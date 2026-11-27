from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from collections.abc import AsyncIterator
from hmac import compare_digest
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy.exc import SQLAlchemyError

from src.agent.harness.persistence import StoreRunPersistence
from src.agent.models import AgentEvent, AgentRun, HumanInputAnswer, HumanInputState, RunStatus
from src.agent.observability import EXECUTOR_WORKFLOW, AgentRunObserver
from src.agent.providers.base import ModelProvider
from src.agent.providers.errors import ModelConfigurationError
from src.agent.providers.factory import build_text_provider
from src.agent.run_manager import run_manager
from src.agent.runtime.executor import AgentExecutor
from src.agent.tools.registry import ToolRegistry
from src.agent.workspace.bootstrap import workspace_specialists
from src.agent.workspace.registry import (
    SpecialistContextUnavailable, SpecialistUnavailable, ToolContext, WorkspaceResources,
)
from src.agent.workspace.documents import (
    DOCUMENT_PROMPT_VERSION,
    DOCUMENT_SYSTEM_PROMPT,
    MAX_ATTACHMENT_COUNT,
    WorkspaceDocumentStore,
    conversation_scope,
    workspace_owner,
)
from src.agent.workspace.routing import routing_prompt
from src.agent.workspace.team import WorkspaceTeam, conversation_context
from src.api.routes.workspace.directory import ToolDirectory, describe_registered
from src.api.routes.workspace.documents import DocumentId, OptionalWorkspaceKey, document_store
from src.api.routes.workspace.followup import router as followup_router
from src.core.cache import cache_manager
from src.core.config import settings
from src.facade.data_facade import facade
from src.followup_agent.store import FollowupCaseStore, access_hash
from src.followup_agent.specialist import FOLLOWUP_SPECIALIST
from src.followup_agent.workflow import (
    SAFE_INTENT,
    FollowupCommand,
    SaveFollowup,
    explicit_followup,
)
from src.observability.runtime import observability_client, observability_database
from src.seo_agent.runtime import (
    seo_agent_system_service,
    seo_chat_harness_store,
    seo_chat_recovery_service,
    structured_model_automation,
)
from src.seo_agent.specialist import SEO_ID, build_seo_system_prompt
from src.seo_agent.workflows import cloudflare_images

site_chat_router = APIRouter(prefix="/seo-agent/chat", tags=["SEO Agent Chat"])
router = APIRouter(prefix="/chat", tags=["Conversations"])
router.include_router(followup_router)


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class CreateConversationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str | None = Field(default=None, min_length=1, max_length=256)
    messages: list[ConversationMessage] = Field(min_length=1)
    document_ids: list[DocumentId] = Field(default_factory=list, max_length=MAX_ATTACHMENT_COUNT)

    @model_validator(mode="after")
    def last_message_is_user(self) -> CreateConversationRunRequest:
        if self.messages[-1].role != "user" or not self.messages[-1].content.strip():
            raise ValueError("请以用户的业务问题开始本轮对话")
        if self.document_ids and (
            not self.conversation_id or len(set(self.document_ids)) != len(self.document_ids)
        ):
            raise ValueError("附件需要关联当前会话，且每份只可选一次。")
        return self


class CreateSiteRunRequest(BaseModel):
    site_id: str = Field(min_length=1)
    conversation_id: str | None = None
    messages: list[ConversationMessage] = Field(min_length=1)


class ConversationRunResponse(BaseModel):
    run_id: str
    task_id: str | None = None
    thread_id: str | None = None
    status: str
    events_url: str
    cancel_url: str
    followup_key: str | None = None


class WorkspaceTaskRow(BaseModel):
    id: str
    conversation_id: str
    current_run_id: str | None
    title: str
    status: str
    can_resume: bool
    partial: bool
    created_at: str
    updated_at: str


class WorkspaceRunStats(BaseModel):
    elapsed_ms: float | None = None
    total_tokens: int | None = None


def build_conversation_model() -> ModelProvider:
    """Build the startup-selected text provider for workspace and SEO chats."""
    return build_text_provider(settings)


def _run_text_model(site_id: str | None, state: dict) -> ModelProvider | None:
    """Use a provider for supervision and model-backed specialists only."""
    if site_id is None and not state.get("question"):
        definition = workspace_specialists.find(state.get("route"))
        if definition is not None and not definition.requires_text_model:
            return None
    return build_conversation_model()


def _durable_enabled() -> bool:
    return seo_chat_harness_store.database.started


async def _execute(
    run_id: str,
    provider: ModelProvider | None,
    registry: ToolRegistry | None,
    conversation_id: str | None,
    document_context: str | None = None,
) -> None:
    """Execute a workspace team, standalone SEO chat or document-only run."""
    run = run_manager.get(run_id)
    if run is None:
        return
    reading_documents = "document_owner" in run.dispatch_state
    observer = (
        AgentRunObserver(
            observability_client,
            operation_name=(
                "workspace.documents" if reading_documents
                else "seo_agent.chat" if registry is not None else "workspace.chat"
            ),
            session_id=None if reading_documents else conversation_id,
            agent_name="seo-specialist-agent" if registry is not None else "workspace-supervisor",
            workflow_name=EXECUTOR_WORKFLOW if registry is not None or reading_documents
            else "workspace-graph",
            record_content=not reading_documents,
        )
        if observability_database.started
        else None
    )
    persistence = StoreRunPersistence(seo_chat_harness_store) if _durable_enabled() else None
    executor = AgentExecutor(
        provider,
        registry if registry is not None else ToolRegistry(),
        max_iterations=settings.agent_max_iterations,
        max_tool_calls=settings.agent_max_tool_calls,
        tool_timeout=settings.agent_tool_timeout,
        observer=observer,
        persistence=persistence,
        context_message_limit=settings.agent_context_message_limit,
        context_character_limit=settings.agent_context_character_limit,
    )
    if reading_documents:
        async def answer_documents(current: AgentRun) -> None:
            context = document_context
            if context is None:
                state = current.dispatch_state
                question = next(
                    message["content"] for message in reversed(current.messages)
                    if message["role"] == "user"
                )
                try:
                    context = await WorkspaceDocumentStore(
                        seo_chat_harness_store.database,
                    ).model_context(
                        state["document_owner"], conversation_scope(current.conversation_id or ""),
                        state["document_ids"], question,
                    )
                except KeyError:
                    raise ValueError("本次选中的资料已删除或无法访问。") from None
            await executor.run_turns(current, extra_user_context=context)

        await executor.run(run, workflow=answer_documents)
    elif registry is not None:
        await executor.run(run)
    else:
        team = WorkspaceTeam(
            executor, workspace_specialists, _workspace_resources(),
            seo_chat_harness_store.completed_dialog,
            seo_chat_harness_store.completed_tool_data if _durable_enabled() else None,
        )
        await executor.run(run, workflow=team.execute)


def _workspace_resources() -> WorkspaceResources:
    return WorkspaceResources(
        settings=settings, database=seo_chat_harness_store.database,
        facade=facade, cache=cache_manager, planner=structured_model_automation.model_runner,
        observability=observability_client if observability_database.started else None,
        find_site=seo_agent_system_service.find_site_by_host if _durable_enabled() else None,
    )


async def _site_seo_tools(site: dict[str, Any], messages: list[str]) -> ToolRegistry:
    return await workspace_specialists.get(SEO_ID).tools(ToolContext(
        _workspace_resources(), site=site, user_messages=tuple(messages),
    ))


@router.get("/tools", response_model=ToolDirectory)
async def list_workspace_tools(
    conversation_id: str | None = Query(default=None, min_length=1, max_length=256),
) -> ToolDirectory:
    """列出专员已登记的能力与公开的静态提示词。"""
    previous = {}
    if conversation_id and _durable_enabled():
        previous = await seo_chat_harness_store.latest_dispatch_state(
            conversation_id, include_dialog=False,
        )
    target = previous.get("target_url")
    context = ToolContext(_workspace_resources(), target_url=target)
    rows = []
    for definition in workspace_specialists:
        note = (definition.no_target_note if not target else None) or definition.directory_note
        try:
            tools = await definition.tools(context)
        except SpecialistContextUnavailable as exc:
            tools, note = ToolRegistry(), str(exc)
        rows.append(describe_registered(definition, tools, note))
    return ToolDirectory(specialists=rows)


async def _schedule_restored_run(run: AgentRun) -> None:
    """Restore dependencies without silently sending a saved conversation to another provider."""
    if run.site_id is None and "document_owner" not in run.dispatch_state:
        workspace_specialists.check_saved_route(run.dispatch_state)
    provider = _run_text_model(run.site_id, run.dispatch_state)
    original_provider = run.dispatch_state.get("text_model_provider", run.model_provider)
    if (
        provider is not None and original_provider in {"openai", "deepseek"}
        and original_provider != provider.provider_name
    ):
        raise ModelConfigurationError(
            "当前模型服务与此运行开始时不同。请恢复原服务，或新建对话。"
        )
    if run.site_id is None:
        run.task = asyncio.create_task(
            _execute(run.id, provider, None, run.conversation_id),
            name=f"workspace-recover:{run.id}",
        )
        return
    site = await seo_agent_system_service.get_site(run.site_id)
    registry = await _site_seo_tools(site, [
        str(message.get("content", "")) for message in run.messages if message.get("role") == "user"
    ])
    if not registry.schemas():
        raise RuntimeError("恢复运行没有真实 SEO 数据工具可用")
    run.task = asyncio.create_task(
        _execute(run.id, provider, registry, run.conversation_id),
        name=f"seo-agent-recover:{run.id}",
    )


async def recover_active_conversations() -> dict[str, int]:
    if not settings.agent_chat_recovery_enabled or not _durable_enabled():
        return {"recovered": 0, "blocked": 0}
    return await seo_chat_recovery_service.recover_active(_schedule_restored_run)


@site_chat_router.post(
    "/runs", response_model=ConversationRunResponse, status_code=status.HTTP_201_CREATED,
)
async def create_site_run(body: CreateSiteRunRequest) -> ConversationRunResponse:
    try:
        site = await seo_agent_system_service.get_site(body.site_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if str(site.get("status") or "ACTIVE") != "ACTIVE":
        raise HTTPException(status_code=409, detail="站点当前不可用于 SEO Agent 调查")
    registry = await _site_seo_tools(site, [m.content for m in body.messages if m.role == "user"])
    if not registry.schemas():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "NO_LIVE_SEO_TOOLS",
                "message": "当前没有真实 SEO 数据工具可用，请检查 DataForSEO 凭证并重启服务。",
            },
        )
    return await _start_run(
        body, site_id=body.site_id, registry=registry,
        system_prompt=build_seo_system_prompt(site, registry.schemas()),
    )


@router.post("/runs", response_model=ConversationRunResponse, status_code=201)
async def create_run(
    body: CreateConversationRunRequest, workspace_key: OptionalWorkspaceKey = None,
) -> ConversationRunResponse:
    latest = body.messages[-1].content
    if body.document_ids:
        if explicit_followup(latest):
            raise HTTPException(422, "请从复诊资料区上传报告，通用附件用于普通文档问答。")
        if not workspace_key:
            raise HTTPException(404, "选中资料不存在或无法访问。")
        owner = workspace_owner(workspace_key)
        try:
            context = await document_store().model_context(
                owner, conversation_scope(body.conversation_id or ""), body.document_ids, latest,
            )
        except KeyError:
            raise HTTPException(404, "选中资料不存在或无法访问。") from None
        except SQLAlchemyError:
            raise HTTPException(503, "暂时无法读取选中的资料。") from None
        return await _start_run(
            body, site_id=None, registry=None, system_prompt=DOCUMENT_SYSTEM_PROMPT,
            dispatch_state={"document_owner": owner, "document_ids": body.document_ids},
            document_context=context,
        )
    previous = {}
    if _durable_enabled() and body.conversation_id:
        previous = await seo_chat_harness_store.latest_dispatch_state(
            body.conversation_id, include_dialog=False,
        )
    state = conversation_context(previous, workspace_specialists)
    if explicit_followup(latest):
        state = {
            "route": FOLLOWUP_SPECIALIST.id, "intent": SAFE_INTENT,
            "followup_demo": "合成" in latest and "演示" in latest,
        }
        # The attachments and clinical notes belong in the case, outside model messages.
        body = body.model_copy(update={"messages": [ConversationMessage(
            role="user", content=SAFE_INTENT,
        )]})
    key = secrets.token_urlsafe(32) if _durable_enabled() else None
    if key:
        state["followup_key_hash"] = access_hash(key)
    _, prompt = routing_prompt(workspace_specialists)
    response = await _start_run(
        body, site_id=None, registry=None, system_prompt=prompt["system_template"], dispatch_state=state,
    )
    return response.model_copy(update={"followup_key": key})


async def _start_run(
    body: CreateConversationRunRequest | CreateSiteRunRequest, *, site_id: str | None,
    registry: ToolRegistry | None, system_prompt: str, dispatch_state: dict | None = None,
    document_context: str | None = None,
) -> ConversationRunResponse:
    dispatch_state = dict(dispatch_state or {})
    try:
        provider = _run_text_model(site_id, dispatch_state)
    except ModelConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    if provider is not None:
        dispatch_state["text_model_provider"] = provider.provider_name
    user_turn = body.messages[-1:] if site_id is None else body.messages
    messages = [
        {"role": "system", "content": system_prompt},
        *[message.model_dump(mode="json") for message in user_turn],
    ]
    created = None
    if _durable_enabled():
        intent = "资料问答" if document_context else next(
            (m.content for m in reversed(body.messages) if m.role == "user"),
            body.messages[-1].content,
        )
        created = await seo_chat_harness_store.create_task_run(
            site_id=site_id, conversation_id=body.conversation_id, title=intent[:120],
            user_intent=intent, messages=messages, model_provider=None, model_name=None,
            dispatch_state=dispatch_state,
            prompt_version=(
                DOCUMENT_PROMPT_VERSION if document_context
                else "seo-chat-v1" if site_id else "workspace-v1"
            ),
            prompt_hash=hashlib.sha256(system_prompt.encode()).hexdigest(),
            available_tools=registry.names() if registry is not None else [],
        )
    run = run_manager.create_run(
        messages, run_id=created.run_id if created else None,
        task_id=created.task_id if created else None,
        thread_id=created.thread_id if created else None, site_id=site_id,
        conversation_id=body.conversation_id,
    )
    run.dispatch_state = dispatch_state
    run.task = asyncio.create_task(
        _execute(run.id, provider, registry, body.conversation_id, document_context),
        name=f"chat:{run.id}",
    )
    return ConversationRunResponse(
        run_id=run.id, task_id=run.task_id, thread_id=run.thread_id, status=run.status.value,
        **_run_urls(run),
    )


def _run_urls(run: AgentRun) -> dict[str, str]:
    prefix = "/api/v1/seo-agent/chat" if run.site_id else "/api/v1/chat"
    return {
        "events_url": f"{prefix}/runs/{run.id}/events",
        "cancel_url": f"{prefix}/runs/{run.id}/cancel",
    }


@site_chat_router.get("/images/{image_id}", response_class=FileResponse)
def get_share_image(image_id: UUID) -> FileResponse:
    stored = cloudflare_images.cloudflare_share_images.get_file(image_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="分享图不存在")
    path, media_type = stored
    return FileResponse(
        path, media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"},
    )


def _encode(event: AgentEvent) -> str:
    payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    event_id = "" if event.type == "message.delta" else f"id: {event.sequence}\n"
    return f"{event_id}event: {event.type}\ndata: {payload}\n\n"


async def _load_runtime_run(run_id: str) -> AgentRun | None:
    run = run_manager.get(run_id)
    if run is not None:
        return run
    if not _durable_enabled():
        return None
    try:
        run = await seo_chat_harness_store.restore_run(run_id, mark_recovering=False)
    except KeyError:
        return None
    return run_manager.register(run)


def _require_document_access(state: dict, key: str | None) -> None:
    owner = state.get("document_owner")
    if owner and (not key or not compare_digest(owner, workspace_owner(key))):
        raise HTTPException(404, "run 不存在")


async def _event_stream(
    run_id: str,
    *,
    after_sequence: int = 0,
) -> AsyncIterator[str]:
    run = await _load_runtime_run(run_id)
    if run is None:
        return
    cursor = max(0, after_sequence)
    live_start_sequence = run.sequence

    async for event in run.follow_events():
        if event is None:
            yield "event: heartbeat\ndata: {}\n\n"
        elif event.sequence > cursor and not (
            cursor > 0 and event.type == "message.delta" and event.sequence <= live_start_sequence
        ):
            yield _encode(event)


@router.get("/tasks", response_model=list[WorkspaceTaskRow])
async def list_workspace_tasks(
    conversation_id: Annotated[
        list[Annotated[str, StringConstraints(min_length=1, max_length=256)]],
        Query(min_length=1, max_length=50),
    ],
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict[str, Any]]:
    if not _durable_enabled():
        raise HTTPException(status_code=503, detail="Agent 运行记录暂不可用")
    return await seo_chat_harness_store.list_tasks(
        workspace_conversation_ids=conversation_id, limit=limit,
    )


@site_chat_router.get("/tasks")
async def list_tasks(
    site_id: str | None = None,
    status_group: Literal["pending", "running", "ended"] | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    if not _durable_enabled():
        return []
    return await seo_chat_harness_store.list_tasks(
        site_id=site_id,
        status_group=status_group,
        limit=limit,
        exclude_prompt_version=DOCUMENT_PROMPT_VERSION,
    )


@site_chat_router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    if not _durable_enabled():
        raise HTTPException(status_code=503, detail="Agent 运行 Harness 未启动")
    try:
        task = await seo_chat_harness_store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if task.get("prompt_version") == DOCUMENT_PROMPT_VERSION:
        raise HTTPException(404, "任务不存在")
    return task


@router.get("/runs/{run_id}")
@site_chat_router.get("/runs/{run_id}")
async def get_run(run_id: str, workspace_key: OptionalWorkspaceKey = None) -> dict[str, Any]:
    if _durable_enabled():
        try:
            detail = await seo_chat_harness_store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _require_document_access(
            (detail.get("checkpoint") or {}).get("dispatch_state", {}), workspace_key,
        )
        return detail
    run = run_manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    _require_document_access(run.dispatch_state, workspace_key)
    return {
        "id": run.id,
        "status": run.status.value,
        "current_stage": run.events[-1].type if run.events else "created",
        "model_provider": run.model_provider,
        "model_name": run.model_name,
        "events": [event.model_dump(mode="json") for event in run.events],
    }


@router.get("/runs/{run_id}/stats", response_model=WorkspaceRunStats)
async def get_run_stats(
    run_id: str, workspace_key: OptionalWorkspaceKey = None,
) -> WorkspaceRunStats:
    run = await _load_runtime_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    _require_document_access(run.dispatch_state, workspace_key)
    observed = (
        await observability_client.application_run_stats(run_id)
        if observability_database.started else {}
    )
    elapsed = observed.get("elapsed_ms")
    if elapsed is None and run.finished_at is not None:
        elapsed = (run.finished_at - run.created_at).total_seconds() * 1000
    tokens = observed.get("total_tokens")
    return WorkspaceRunStats(
        elapsed_ms=elapsed,
        total_tokens=tokens if tokens is not None else run.token_usage.get("total_tokens") or None,
    )


@router.get("/runs/{run_id}/events")
@site_chat_router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    after_sequence: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    workspace_key: OptionalWorkspaceKey = None,
) -> StreamingResponse:
    run = await _load_runtime_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    _require_document_access(run.dispatch_state, workspace_key)
    header_cursor = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
    cursor = max(after_sequence, header_cursor)
    return StreamingResponse(
        _event_stream(run_id, after_sequence=cursor),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/resume")
@site_chat_router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, workspace_key: OptionalWorkspaceKey = None) -> dict[str, str]:
    if not _durable_enabled():
        raise HTTPException(status_code=503, detail="Agent 运行 Harness 未启动")
    existing = await _load_runtime_run(run_id)
    if existing:
        _require_document_access(existing.dispatch_state, workspace_key)
    if existing is not None and existing.task is not None and not existing.task.done():
        raise HTTPException(status_code=409, detail="run 正在执行")
    try:
        run = await seo_chat_harness_store.claim_retry(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    run_manager.register(run)
    try:
        await _schedule_restored_run(run)
    except SpecialistUnavailable as exc:
        await seo_chat_harness_store.mark_recovery_blocked(run_id, str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ModelConfigurationError as exc:
        await seo_chat_harness_store.mark_recovery_blocked(run_id, str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except Exception as exc:
        await seo_chat_harness_store.mark_recovery_blocked(run_id, str(exc))
        raise HTTPException(status_code=503, detail="run 恢复条件不足") from exc
    return {"run_id": run.id, "status": run.status.value}


@router.post("/runs/{run_id}/input")
@site_chat_router.post("/runs/{run_id}/input")
async def submit_run_input(
    run_id: str, body: HumanInputAnswer,
    followup_key: str | None = Header(default=None, alias="X-Followup-Key", max_length=128),
    workspace_key: OptionalWorkspaceKey = None,
) -> dict[str, Any]:
    if not _durable_enabled():
        raise HTTPException(status_code=503, detail="Agent 运行 Harness 未启动")
    existing = await _load_runtime_run(run_id)
    if existing:
        _require_document_access(existing.dispatch_state, workspace_key)
    try:
        followup = await FollowupCaseStore(seo_chat_harness_store.database).check_input_access(
            run_id, followup_key,
        )
    except KeyError:
        raise HTTPException(404, "复诊任务不存在。") from None
    except SQLAlchemyError:
        raise HTTPException(503, "暂时无法确认资料访问权限。") from None
    commands = {item.value for item in FollowupCommand}
    if followup and (body.action != "answer" or body.answer not in commands):
        raise HTTPException(422, "请通过本次复诊资料区提交操作。")
    confirming_followup = followup and body.answer == FollowupCommand.CONFIRM
    if body.followup_revision is not None and not confirming_followup:
        raise HTTPException(422, "仅确认保存复诊清单时需要提供草稿版本。")

    def validate_confirmation(pending: HumanInputState) -> None:
        if body.followup_revision is None:
            raise HTTPException(422, "请确认当前复诊清单的草稿版本。")
        if (
            pending.tool_name != SaveFollowup.name
            or pending.tool_arguments.get("revision") != body.followup_revision
        ):
            raise ValueError("这份复诊清单的确认请求已变化，请刷新后核对当前版本。")

    if (
        existing is not None and existing.task is not None and not existing.task.done()
        and existing.status == RunStatus.WAITING_FOR_INPUT
    ):
        try:
            await asyncio.wait_for(asyncio.shield(existing.task), timeout=5)
        except TimeoutError as exc:
            raise HTTPException(status_code=409, detail="正在保存进度，请稍后再提交") from exc
    try:
        run, replayed, sequence = await seo_chat_harness_store.submit_input(
            run_id, body, validate_pending=validate_confirmation if confirming_followup else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not replayed:
        run_manager.register(run)
        try:
            await _schedule_restored_run(run)
        except SpecialistUnavailable as exc:
            await seo_chat_harness_store.mark_recovery_blocked(run_id, str(exc))
            raise HTTPException(status_code=409, detail=f"补充已保存。{exc}") from None
        except ModelConfigurationError as exc:
            await seo_chat_harness_store.mark_recovery_blocked(run_id, str(exc))
            raise HTTPException(status_code=503, detail=f"补充已保存。{exc}") from None
        except Exception as exc:
            await seo_chat_harness_store.mark_recovery_blocked(run_id, str(exc))
            raise HTTPException(status_code=503, detail="补充已保存，运行暂时无法恢复") from exc
    return {
        "run_id": run.id, "task_id": run.task_id, "thread_id": run.thread_id,
        "status": run.status.value, "after_sequence": sequence, "replayed": replayed,
        **_run_urls(run),
    }


@router.post("/runs/{run_id}/cancel")
@site_chat_router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, workspace_key: OptionalWorkspaceKey = None) -> dict[str, str]:
    existing = await _load_runtime_run(run_id)
    if existing:
        _require_document_access(existing.dispatch_state, workspace_key)
    run = run_manager.cancel(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    if _durable_enabled() and (run.task is None or run.task.done()):
        if run.events:
            await seo_chat_harness_store.persist_event(run, run.events[-1])
        await seo_chat_harness_store.save_checkpoint(
            run,
            messages=run.messages,
            iteration=run.iteration,
            tool_count=run.tool_count,
            pending_tool_calls=run.pending_tool_calls,
            stage="cancelled",
            token_usage=run.token_usage,
        )
        await seo_chat_harness_store.finalize_run(run)
    return {"run_id": run.id, "status": run.status.value}
