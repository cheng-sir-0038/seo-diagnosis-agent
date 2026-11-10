from __future__ import annotations

from pathlib import Path

import pytest

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
async def test_database_creates_complete_foundation_schema(tmp_path: Path) -> None:
    database = SeoAgentDatabase(tmp_path / "seo-agent.db")
    await database.startup()
    try:
        rows = await database.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        tables = {row["name"] for row in rows}
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
        foreign_keys = await database.fetch_value("PRAGMA foreign_keys")
        assert foreign_keys == 1
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
async def test_system_service_persists_site_thread_run_and_work_record(tmp_path: Path) -> None:
    database = SeoAgentDatabase(tmp_path / "seo-agent.db")
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
        record = await service.append_work_record(
            site_id=site["id"],
            title="M1 started",
            summary="Foundation run",
            record_type="foundation_started",
            actor_id=actor["id"],
            agent_run_id=run["id"],
            idempotency_key="foundation:1",
        )
        assert (await service.get_site(site["id"]))["domain"] == "example.com"
        assert (await service.get_thread(thread["thread_id"]))["id"] == thread["id"]
        assert record["id"]
        assert len(await service.list_work_records(site["id"])) == 1
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_langgraph_sqlite_checkpoint_persists_thread_state(tmp_path: Path) -> None:
    manager = CheckpointManager(tmp_path / "checkpoints.db")
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
async def test_seo_agent_system_api_uses_real_sqlite_store(tmp_path: Path, monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from src.api.routes import seo_agent_system as route
    from src.server import app

    database = SeoAgentDatabase(tmp_path / "api.db")
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
            listed = await client.get("/api/v1/seo-agent/system/sites")
        assert created.status_code == 201
        assert listed.status_code == 200
        assert listed.json()[0]["domain"] == "api.example.com"
    finally:
        await database.shutdown()
