from __future__ import annotations

from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.client.errors import A2AClientTimeoutError
from a2a.helpers import new_task, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Message,
    Part,
    Role,
    SendMessageRequest,
    TaskState,
)
from a2a.utils.errors import A2AError
from google.protobuf.json_format import MessageToJson, ParseDict
from google.protobuf.struct_pb2 import Value
from pydantic import ValidationError
from starlette.applications import Starlette

from src.core.config import settings
from src.core.exceptions import ErrorCode, InternalAdapterError
from src.models.geo import GeoCitationReport, GeoCitationRequest
from src.observability.client import ObservabilityClient
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind
from src.seo_agent.geo_search import DeepSeekGeoSearch, GeoSearchAdapter, OpenAIGeoSearch

SKILL_ID = "geo-citation-verification"
ARTIFACT_NAME = "geo-citation-report"
LOCAL_URL = "http://127.0.0.1:8011"


def _data_part(data: dict[str, Any]) -> Part:
    return Part(data=ParseDict(data, Value()), media_type="application/json")


class CitationAgentExecutor(AgentExecutor):
    def __init__(
        self,
        adapters: dict[str, GeoSearchAdapter],
        observability: ObservabilityClient | None,
    ) -> None:
        self.adapters, self.observability = adapters, observability

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = TaskUpdater(event_queue, context.task_id, context.context_id)
        message = context.message
        if context.current_task is None:
            await event_queue.enqueue_event(
                new_task(
                    context.task_id,
                    context.context_id,
                    TaskState.TASK_STATE_SUBMITTED,
                    history=[message] if message is not None else [],
                )
            )
        try:
            if (
                message is None
                or message.role != Role.ROLE_USER
                or len(message.parts) != 1
                or not message.parts[0].HasField("data")
            ):
                raise ValueError("Expected a citation request data part")
            request = GeoCitationRequest.model_validate_json(MessageToJson(message.parts[0].data))
        except ValueError:
            await task.reject(new_text_message("INVALID_PARAMS", role=Role.ROLE_AGENT))
            return
        error_code = None
        data = None
        async with observed_operation(
            self.observability,
            "geo_citation_verifier",
            kind=SpanKind.AGENT,
            component_name="geo_citation_agent",
            input_data=request.model_dump(),
            metadata={"a2a_task_id": context.task_id, "a2a_message_id": message.message_id},
        ) as span:
            await task.start_work()
            adapter = self.adapters.get(request.provider)
            try:
                if adapter is None:
                    raise GeoPeerError("NOT_CONFIGURED")
                sample = await adapter.search(request.question)
                data = GeoCitationReport.build(request, sample).model_dump(mode="json")
                if span:
                    span.output_data = data
            except (InternalAdapterError, GeoPeerError) as exc:
                error_code = (
                    exc.error_code if isinstance(exc, GeoPeerError) else exc.error_code.value
                )
                if span:
                    await span.capture_exception(exc, error_code=error_code)

        if data is None:
            await task.failed(
                new_text_message(
                    error_code or "INTERNAL_ERROR",
                    role=Role.ROLE_AGENT,
                )
            )
        else:
            await task.add_artifact([_data_part(data)], name=ARTIFACT_NAME)
            await task.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


def citation_app(
    base_url: str,
    adapters: dict[str, GeoSearchAdapter],
    observability: ObservabilityClient | None = None,
) -> Starlette:
    card = AgentCard(
        name="GEO Citation Verifier",
        description="Samples AI search answers and verifies citations against a target domain.",
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(
                url=f"{base_url}/a2a", protocol_binding="JSONRPC", protocol_version="1.0"
            )
        ],
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id=SKILL_ID,
                name="Verify AI search citations",
                description="Returns an observed answer, citation URLs, and search metadata.",
                tags=["geo", "citation"],
                input_modes=["application/json"],
                output_modes=["application/json"],
            )
        ],
    )
    # shortcut: tasks stay in memory for the local peer, use a durable store for a shared service.
    handler = DefaultRequestHandler(
        agent_executor=CitationAgentExecutor(adapters, observability),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    @asynccontextmanager
    async def lifespan(app: Starlette):
        try:
            yield
        finally:
            await handler.aclose()

    return Starlette(
        routes=[
            *create_agent_card_routes(card),
            *create_jsonrpc_routes(handler, rpc_url="/a2a"),
        ],
        lifespan=lifespan,
    )


class GeoPeerError(Exception):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class GeoCitationClient:
    def __init__(
        self,
        base_url: str,
        timeout: float,
        *,
        adapters: dict[str, GeoSearchAdapter] | None = None,
        observability: ObservabilityClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.adapters, self.observability = adapters, observability

    async def verify(self, request: GeoCitationRequest) -> tuple[GeoCitationReport, str]:
        message = Message(
            message_id=uuid4().hex,
            role=Role.ROLE_USER,
            parts=[_data_part(request.model_dump(mode="json"))],
        )
        async with observed_operation(
            self.observability,
            "geo_a2a_handoff",
            kind=SpanKind.HTTP,
            component_name="geo_citation_agent",
            input_data=request.model_dump(),
            metadata={
                "a2a_message_id": message.message_id,
                "protocol": "1.0/JSONRPC",
                "transport": "asgi" if self.adapters is not None else "http",
            },
        ) as span:
            try:
                report, task_id = await self._send(request, message)
            except GeoPeerError as exc:
                if span:
                    await span.capture_exception(exc, error_code=exc.error_code)
                raise
            if span:
                span.output_data = {
                    "a2a_task_id": task_id,
                    "citation_status": report.citation_status,
                }
            return report, task_id

    async def _send(
        self,
        request: GeoCitationRequest,
        message: Message,
    ) -> tuple[GeoCitationReport, str]:
        origin = urlsplit(self.base_url)
        if (
            origin.scheme not in {"http", "https"}
            or not origin.netloc
            or origin.username
            or origin.password
            or origin.path
            or origin.query
            or origin.fragment
        ):
            raise GeoPeerError("GEO_A2A_INVALID_URL")
        try:
            async with AsyncExitStack() as stack:
                transport = None
                if self.adapters is not None:
                    app = citation_app(self.base_url, self.adapters, self.observability)
                    await stack.enter_async_context(app.router.lifespan_context(app))
                    transport = httpx.ASGITransport(app=app)
                http_client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        transport=transport,
                        timeout=self.timeout,
                        follow_redirects=False,
                        trust_env=False,
                    )
                )
                card = await A2ACardResolver(http_client, self.base_url).get_agent_card()
                if (
                    not any(skill.id == SKILL_ID for skill in card.skills)
                    or not card.supported_interfaces
                ):
                    raise GeoPeerError("GEO_A2A_INVALID_CARD")
                for interface in card.supported_interfaces:
                    url = urlsplit(interface.url)
                    if (url.scheme, url.netloc) != (origin.scheme, origin.netloc):
                        raise GeoPeerError("GEO_A2A_INVALID_CARD")
                client = await stack.enter_async_context(
                    await create_client(
                        card,
                        ClientConfig(httpx_client=http_client, streaming=False),
                    )
                )
                events = await stack.enter_async_context(
                    aclosing(client.send_message(SendMessageRequest(message=message)))
                )
                event = await anext(events, None)
                if event is None or not event.HasField("task"):
                    raise GeoPeerError("GEO_A2A_INVALID_ARTIFACT")
                task = event.task
                if task.status.state != TaskState.TASK_STATE_COMPLETED:
                    code = next((part.text for part in task.status.message.parts), "")
                    allowed = {error.value for error in ErrorCode} | {"NOT_CONFIGURED"}
                    raise GeoPeerError(code if code in allowed else "GEO_A2A_TASK_FAILED")
                for artifact in task.artifacts:
                    if artifact.name != ARTIFACT_NAME or len(artifact.parts) != 1:
                        continue
                    part = artifact.parts[0]
                    if part.HasField("data"):
                        report = GeoCitationReport.model_validate_json(MessageToJson(part.data))
                        if (
                            report.sample.provider == request.provider
                            and report == GeoCitationReport.build(request, report.sample)
                        ):
                            return report, task.id
        except (A2AClientTimeoutError, httpx.TimeoutException):
            raise GeoPeerError("UPSTREAM_TIMEOUT") from None
        except (A2AError, httpx.HTTPError, OSError):
            raise GeoPeerError("GEO_A2A_UNAVAILABLE") from None
        except (ValidationError, ValueError):
            raise GeoPeerError("GEO_A2A_INVALID_ARTIFACT") from None
        raise GeoPeerError("GEO_A2A_INVALID_ARTIFACT")


def _adapters(observability: ObservabilityClient | None) -> dict[str, GeoSearchAdapter]:
    adapters: dict[str, GeoSearchAdapter] = {}
    if settings.deepseek_api_key:
        adapters["deepseek"] = DeepSeekGeoSearch(
            settings.deepseek_api_key,
            settings.deepseek_base_url,
            "deepseek-flash",
            settings.deepseek_request_timeout,
            observability=observability,
        )
    if settings.openai_api_key.get_secret_value():
        adapters["openai"] = OpenAIGeoSearch(
            settings.openai_api_key.get_secret_value(),
            "https://api.openai.com/v1",
            settings.geo_openai_model,
            settings.deepseek_request_timeout,
            observability=observability,
        )
    return adapters


def configured_citation_client(
    observability: ObservabilityClient | None,
) -> GeoCitationClient | None:
    if settings.geo_a2a_url:
        return GeoCitationClient(
            settings.geo_a2a_url,
            settings.deepseek_request_timeout,
            observability=observability,
        )
    adapters = _adapters(observability)
    if not adapters:
        return None
    return GeoCitationClient(
        LOCAL_URL,
        settings.deepseek_request_timeout,
        adapters=adapters,
        observability=observability,
    )


def create_app() -> Starlette:
    from src.observability.runtime import observability_client

    app = citation_app(
        (settings.geo_a2a_url or LOCAL_URL).rstrip("/"),
        _adapters(observability_client),
        observability_client,
    )
    tasks_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: Starlette):
        await observability_client.startup()
        try:
            async with tasks_lifespan(app):
                yield
        finally:
            await observability_client.shutdown()

    app.router.lifespan_context = lifespan
    return app
