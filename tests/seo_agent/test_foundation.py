from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import insert, inspect, update

from src.seo_agent.adapters.base import AdapterHealth, AdapterStatus, BaseAdapter
from src.seo_agent.adapters.registry import AdapterDefinition, AdapterRegistry
from src.seo_agent.checkpoints import CheckpointManager
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import SiteRow
from src.seo_agent.graphs.foundation import build_foundation_graph
from src.seo_agent.notifications.service import NotificationService
from src.seo_agent.services.system import (
    InvalidSiteDomainError,
    SeoAgentSystemService,
    normalize_site_domain,
)


class ReadyAdapter(BaseAdapter):
    key = "ready"
    category = "test"

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(status=AdapterStatus.READY)

    def capabilities(self) -> set[str]:
        return {"ping"}


@pytest.mark.asyncio
async def test_database_creates_complete_foundation_schema(pg_url: str) -> None:
    database = SeoAgentDatabase(pg_url)
    await database.startup()
    try:
        async with database.orm_session() as session:
            connection = await session.connection()
            tables, refs = await connection.run_sync(
                lambda conn: (
                    set(inspect(conn).get_table_names()),
                    inspect(conn).get_foreign_keys("agent_runtime_tasks"),
                )
            )
        assert {
            "sites",
            "actors",
            "adapter_definitions",
            "site_adapter_bindings",
            "work_schedules",
            "schedule_runs",
            "workflow_threads",
            "agent_runs",
            "agent_runtime_tasks",
            "agent_run_events",
            "agent_run_checkpoints",
            "agent_survival_summaries",
            "work_records",
            "opportunities",
            "task_opportunities",
            "approval_actions",
            "task_prd_versions",
            "deliveries",
            "publish_records",
            "monitoring_plans",
            "notification_subscriptions",
        } <= tables
        assert {"sites", "workflow_threads"} <= {ref["referred_table"] for ref in refs}
    finally:
        await database.shutdown()


def test_adapter_registry_resolves_real_and_placeholder_definitions() -> None:
    registry = AdapterRegistry()
    registry.register_definition(
        AdapterDefinition(
            category="test",
            key="ready",
            display_name="Ready",
            implemented=True,
        )
    )
    registry.register(ReadyAdapter())
    assert registry.resolve("test", "ready").capabilities() == {"ping"}

    registry.register_definition(
        AdapterDefinition(
            category="test",
            key="future",
            display_name="Future",
            implemented=False,
        )
    )
    with pytest.raises(NotImplementedError, match="NOT_IMPLEMENTED"):
        registry.resolve("test", "future")


@pytest.mark.asyncio
async def test_system_service_persists_site_thread_run_and_work_record(pg_url: str) -> None:
    database = SeoAgentDatabase(pg_url)
    await database.startup()
    service = SeoAgentSystemService(database)
    try:
        site = await service.create_site(
            name="Demo",
            domain="example.com",
            default_country="US",
            default_language="en",
            timezone="Asia/Shanghai",
        )
        actor = await service.create_actor(
            actor_type="USER",
            name="Owner",
            email="owner@example.com",
        )
        thread = await service.create_thread(
            site_id=site["id"],
            workflow_type="OPPORTUNITY",
            business_object_type="site",
            business_object_id=site["id"],
        )
        run = await service.start_run(thread_id=thread["thread_id"], trigger_type="MANUAL")

        async def log_run():
            return await service.append_work_record(
                site_id=site["id"],
                title="M1 started",
                summary="Foundation run",
                record_type="foundation_started",
                actor_id=actor["id"],
                agent_run_id=run["id"],
                idempotency_key="foundation:1",
            )

        record, retried = await asyncio.gather(log_run(), log_run())
        assert record["id"] == retried["id"]
        assert (await service.get_site(site["id"]))["domain"] == "example.com"
        assert (await service.find_site_by_host("WWW.EXAMPLE.COM."))["id"] == site["id"]
        assert await service.find_site_by_host("elsewhere.example") is None
        assert (await service.get_thread(thread["thread_id"]))["id"] == thread["id"]
        assert record["id"]
        assert len(await service.list_work_records(site["id"])) == 1
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_langgraph_postgres_checkpoint_persists_thread_state(pg_url: str) -> None:
    manager = CheckpointManager(pg_url)
    await manager.startup()
    try:
        graph = build_foundation_graph(manager.saver)
        config = {"configurable": {"thread_id": "thread-1"}}
        result = await graph.ainvoke(
            {
                "thread_id": "thread-1",
                "run_id": "run-1",
                "site_id": "site-1",
                "current_stage": "created",
                "events": [],
            },
            config=config,
        )
        assert result["current_stage"] == "foundation_ready"
        snapshot = await graph.aget_state(config)
        assert snapshot.values["run_id"] == "run-1"
    finally:
        await manager.shutdown()


def test_langchain_deepseek_factory_is_configured_without_network() -> None:
    from src.seo_agent.models.deepseek import LangChainDeepSeekFactory

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        LangChainDeepSeekFactory(
            api_key="",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
        ).create()

    model = LangChainDeepSeekFactory(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
    ).create()
    assert model.model_name == "deepseek-v4-flash"
    assert model.streaming is True


@pytest.mark.asyncio
async def test_structured_deepseek_request_uses_supported_thinking_mode(monkeypatch) -> None:
    import json

    from httpx import AsyncClient, MockTransport, Response
    from langchain_core.messages import HumanMessage
    from langchain_deepseek import ChatDeepSeek
    from pydantic import BaseModel

    from src.seo_agent.automation.model_result import invoke_structured_model
    from src.seo_agent.models import deepseek

    class Result(BaseModel):
        answer: str

    requests = []

    async def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if body.get("thinking") != {"type": "disabled"}:
            return Response(400, json={"error": {"message": "unsupported tool_choice"}})
        return Response(200, json={
            "id": "test", "object": "chat.completion", "model": "deepseek-v4-flash",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": "call-1", "type": "function",
                    "function": {"name": "Result", "arguments": '{"answer":"ok"}'},
                }],
            }}],
        })

    async with AsyncClient(transport=MockTransport(handle)) as client:
        monkeypatch.setattr(
            deepseek, "ChatDeepSeek",
            lambda **kwargs: ChatDeepSeek(**kwargs, http_async_client=client),
        )
        model = deepseek.LangChainDeepSeekFactory(
            api_key="test-key", base_url="https://api.deepseek.com", model="deepseek-v4-flash"
        ).create()
        model.streaming = False
        result = await invoke_structured_model(model, Result, [HumanMessage(content="test")])
    assert result.parsed.answer == "ok"
    assert requests[0]["thinking"] == {"type": "disabled"}
    assert requests[0]["tool_choice"]["function"]["name"] == "Result"


@pytest.mark.asyncio
async def test_seo_agent_system_api_uses_postgres(pg_url: str, monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from src.api.routes import seo_agent_system as route
    from src.server import app

    database = SeoAgentDatabase(pg_url)
    await database.startup()
    service = SeoAgentSystemService(database)
    monkeypatch.setattr(route, "seo_agent_database", database)
    monkeypatch.setattr(route, "seo_agent_system_service", service)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/seo-agent/system/sites",
                json={
                    "name": "API Demo",
                    "domain": "api.example.com",
                    "default_country": "US",
                    "default_language": "en",
                    "timezone": "UTC",
                },
            )
            duplicate = await client.post(
                "/api/v1/seo-agent/system/sites",
                json={"name": "Another name", "domain": "API.EXAMPLE.COM"},
            )
            listed = await client.get("/api/v1/seo-agent/system/sites")
        assert created.status_code == 201
        assert duplicate.status_code == 409
        assert listed.status_code == 200
        assert listed.json()[0]["domain"] == "api.example.com"
    finally:
        await database.shutdown()


@pytest.mark.parametrize(("value", "domain"), [
    ("  HTTPS://WWW.Example.COM.:443/products?q=1#shipping  ", "www.example.com"),
    ("bücher.example", "xn--bcher-kva.example"),
    ("https://[2001:db8::1]/", "2001:db8::1"),
])
def test_site_normalization_accepts_hostnames_and_http_urls(value: str, domain: str) -> None:
    assert normalize_site_domain(value) == domain


@pytest.mark.parametrize("value", ["", "https://", "ftp://example.com", "bad host.org",
    "example.com:bad", "https://user:pass@example.com", "bad..example", "https://?host=x.com",
])
def test_site_normalization_rejects_invalid_registration_values(value: str) -> None:
    with pytest.raises(InvalidSiteDomainError):
        normalize_site_domain(value)


async def test_registration_rolls_back_and_can_retry_without_partial_site(pg_url, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.api.routes import seo_agent_system as route

    database = SeoAgentDatabase(pg_url)
    await database.startup()
    system = SeoAgentSystemService(database)
    monkeypatch.setattr(route, "seo_agent_system_service", system)
    app = FastAPI()
    app.include_router(route.router)
    endpoint = "/seo-agent/system/site-registrations"
    registration = {
        "name": "Example", "domain": " HTTPS://WWW.Example.ORG./offers?q=1 ",
        "default_country": "GB", "default_language": "en-GB", "timezone": "Europe/London",
        "owner_name": "Site Owner", "owner_email": "owner@example.org",
        "executor_name": "Editor", "executor_email": "editor@example.org",
    }
    create_subscription = NotificationService.create_subscription

    async def fail_after_writing_subscription(self, **kwargs):
        await create_subscription(self, **kwargs)
        raise RuntimeError("simulated subscription failure")

    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            invalid = await client.post(endpoint, json={**registration, "domain": "https://?"})
            assert invalid.status_code == 422
            with monkeypatch.context() as temporary:
                temporary.setattr(
                    NotificationService, "create_subscription", fail_after_writing_subscription,
                )
                assert (await client.post(endpoint, json=registration)).status_code == 500
            assert await system.list_sites() == []
            assert await system.list_actors() == []
            response = await client.post(endpoint, json=registration)
            assert response.status_code == 201
            rejected = await client.post(
                endpoint, json={**registration, "domain": "www.example.org"},
            )
            assert rejected.status_code == 409
        payload = response.json()
        site = payload["site"]
        assert site["domain"] == "www.example.org" and site["default_country"] == "GB"
        assert {row["id"]: row["actor_type"] for row in await system.list_actors()} == {
            payload["owner_actor_id"]: "USER", payload["agent_actor_id"]: "SEO_AGENT",
            payload["executor_actor_id"]: "HUMAN_OPERATOR",
        }
        [subscription] = await NotificationService(database).list_subscriptions(site["id"])
        assert subscription["subscriber_actor_id"] == payload["owner_actor_id"]
        assert subscription["is_default_approver"] == 1
    finally:
        await database.shutdown()


async def test_lookup_matches_legacy_urls_without_merging_subdomains(pg_url):
    database = SeoAgentDatabase(pg_url)
    await database.startup()
    service = SeoAgentSystemService(database)
    fields = {"default_country": "GB", "default_language": "en-GB", "timezone": "UTC"}
    try:
        await database.execute(insert(SiteRow).values(
            id="legacy", name="Legacy", domain="https://WWW.example.org./old?ref=abc",
            created_at="2026-01-01", updated_at="2026-01-01", **fields,
        ))
        assert (await service.find_site_by_host("example.org"))["id"] == "legacy"
        canonical = await service.create_site(name="Apex", domain="example.org", **fields)
        assert (await service.find_site_by_host("example.org"))["id"] == canonical["id"]
        assert (await service.find_site_by_host("www.example.org"))["id"] == "legacy"
        assert await service.find_site_by_host("shop.example.org") is None
        await database.execute(update(SiteRow).where(SiteRow.id == "legacy").values(
            status="ARCHIVED", archived_at="2026-01-03",
        ))
        assert (await service.find_site_by_host("www.example.org"))["archived_at"] == "2026-01-03"
    finally:
        await database.shutdown()
