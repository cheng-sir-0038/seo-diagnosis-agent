from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import inspect

from src.seo_agent.adapters.base import AdapterHealth, AdapterStatus, BaseAdapter
from src.seo_agent.adapters.registry import AdapterDefinition, AdapterRegistry
from src.seo_agent.checkpoints import CheckpointManager
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.graphs.foundation import build_foundation_graph
from src.seo_agent.services.system import SeoAgentSystemService


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
