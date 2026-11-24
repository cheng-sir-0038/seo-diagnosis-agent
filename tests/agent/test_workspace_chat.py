from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.agent.harness.store import AgentChatHarnessStore
from src.agent.models import RunStatus
from src.agent.providers.fake import FakeModelProvider
from src.agent.run_manager import RunManager
from src.api.routes.workspace import conversations as chat
from src.models.common import Capability
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import SiteRow


class RouteModel:
    def __init__(self, route: str, needs_page: bool = True):
        self.route = route
        self.needs_page = needs_page
        self.calls = 0

    async def invoke(self, *, schema, **kwargs):
        self.calls += 1
        candidates = kwargs["context"]["candidate_pages"]
        return SimpleNamespace(usage={}, output=schema(
            route=self.route, needs_page=self.needs_page, target_index=1 if candidates else None,
        ))


class Pages:
    def __init__(self):
        self.urls = []

    def live_capabilities(self):
        return {Capability.PAGE_METADATA}

    async def query_live(self, *, params, **kwargs):
        self.urls.append(params["url"])
        return {
            "meta": {"source": "page_html"},
            "data": {
                "source": "page_html", "title": "Community classes", "h1s": ["Community classes"],
                "status_code": 200, "requested_url": params["url"], "final_url": params["url"],
            },
        }


def test_app_exposes_a_standalone_chat_contract():
    from src.server import app

    spec = app.openapi()
    create = spec["paths"]["/api/v1/chat/runs"]["post"]
    ref = create["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    assert set(spec["components"]["schemas"][ref]["properties"]) == {
        "conversation_id", "messages", "document_ids",
    }
    assert "/api/v1/seo-agent/chat/runs/{run_id}/events" in spec["paths"]


@pytest.mark.asyncio
async def test_workspace_tasks_only_list_requested_site_free_conversations(pg_url, monkeypatch):
    database = SeoAgentDatabase(pg_url)
    await database.startup()
    store = AgentChatHarnessStore(database)
    monkeypatch.setattr(chat, "seo_chat_harness_store", store)
    app = FastAPI()
    app.include_router(chat.router, prefix="/api/v1")
    try:
        async with database.orm_session(write=True) as db:
            db.add(SiteRow(
                id="site-a", name="Site A", domain="example.com", default_country="US",
                default_language="en", timezone="UTC",
                created_at="2026-11-24T00:00:00Z", updated_at="2026-11-24T00:00:00Z",
            ))
        created = []
        for conversation_id, site_id in (("known", None), ("other", None), ("known", "site-a")):
            created.append(await store.create_task_run(
                site_id=site_id, conversation_id=conversation_id, title="Test task",
                user_intent="Test task", messages=[{"role": "user", "content": "Test task"}],
                model_provider=None, model_name=None,
            ))
        run = await store.restore_run(created[0].run_id, mark_recovering=False)
        run.status = RunStatus.WAITING_FOR_INPUT
        await store.finalize_run(run)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
            response = await api.get("/api/v1/chat/tasks", params={"conversation_id": "known"})
            assert response.status_code == 200
            [row] = response.json()
            assert row["id"] == created[0].task_id
            assert row["current_run_id"] == created[0].run_id
            assert row["status"] == "WAITING_FOR_INPUT"
            assert set(row) == {
                "id", "conversation_id", "current_run_id", "title", "status",
                "created_at", "updated_at", "can_resume", "partial",
            }
            assert not row["can_resume"] and not row["partial"]
            run.status = RunStatus.FAILED
            await store.finalize_run(run)
            [failed] = (await api.get("/api/v1/chat/tasks?conversation_id=known")).json()
            assert failed["status"] == "FAILED" and failed["can_resume"]
            await store.mark_recovery_blocked(run.id, "恢复条件不足")
            [blocked] = (await api.get("/api/v1/chat/tasks?conversation_id=known")).json()
            assert blocked["status"] == "RECOVERY_BLOCKED" and blocked["can_resume"]
            run.status = RunStatus.COMPLETED
            await store.finalize_run(run)
            await store.persist_event(run, run.emit("run.completed", {"partial": True}))
            [partial] = (await api.get("/api/v1/chat/tasks?conversation_id=known")).json()
            assert partial["partial"] and not partial["can_resume"]
            assert (await api.get("/api/v1/chat/tasks")).status_code == 422
            assert (await api.get("/api/v1/chat/tasks?conversation_id=")).status_code == 422
            assert (await api.get("/api/v1/chat/tasks?conversation_id=unknown")).json() == []
            await database.shutdown()
            assert (await api.get("/api/v1/chat/tasks?conversation_id=known")).status_code == 503
    finally:
        if database.started:
            await database.shutdown()


@pytest.mark.asyncio
async def test_tool_directory_lists_the_live_registries_without_running_tools(monkeypatch):
    pages = Pages()
    monkeypatch.setattr(chat, "facade", pages)
    app = FastAPI()
    app.include_router(chat.router, prefix="/api/v1")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        default = await api.get("/api/v1/chat/tools")
        assert default.status_code == 200
        specialists = default.json()["specialists"]
        medical, search, learning, exam, followup, travel, procurement = specialists
        assert medical["agent"] == "healthcare"
        assert {s["name"] for s in medical["skills"]} >= {"medicare-catalogue", "pubmed-literature"}
        medical_names = {t["name"] for t in medical["tools"]}
        assert medical_names == set(chat.build_healthcare_registry().names())
        assert search["agent"] == "seo" and search["tools"] == [] and search["note"]
        assert learning["agent"] == "learning"
        assert {t["name"] for t in learning["tools"]} == {
            "tal_math_practice", "math_resource_lookup",
        }
        assert {s["name"] for s in learning["skills"]} == {"math-practice", "math-resources"}
        assert exam["agent"] == "exam_prep"
        assert exam["tools"][0]["name"] == "ntce_written_exam_lookup"
        assert followup["agent"] == "followup"
        assert {tool["name"] for tool in followup["tools"]} == {
            "followup_read_materials", "followup_prepare_materials", "followup_save_confirmed",
        }
        assert travel["agent"] == "travel"
        assert travel["skills"][0]["name"] == "business-trip-planning"
        assert {tool["name"] for tool in travel["tools"]} == {
            "travel_transport", "travel_hotels", "travel_commute", "travel_weather",
            "travel_compare_plans", "travel_save_plan",
        }
        assert procurement["agent"] == "shopping_procurement"
        assert procurement["skills"][0]["name"] == "shopping-and-procurement"
        assert {tool["name"] for tool in procurement["tools"]} == {
            "shopping_catalog", "shopping_compare_quotes", "shopping_build_list",
            "shopping_reconcile",
        }
        database = SimpleNamespace(started=True)

        async def last_target(conversation_id, *, include_dialog):
            assert conversation_id == "course-turn" and include_dialog is False
            return {"target_url": "https://example.com/clinical-course.html"}

        async def find_site(host):
            return None

        monkeypatch.setattr(chat, "seo_chat_harness_store", SimpleNamespace(
            database=database, latest_dispatch_state=last_target,
        ))
        monkeypatch.setattr(chat.seo_agent_system_service, "find_site_by_host", find_site)
        response = await api.get("/api/v1/chat/tools", params={"conversation_id": "course-turn"})
        scoped = response.json()
        medical, search, *_ = scoped["specialists"]
        assert "postgraduate_admissions_search" in {t["name"] for t in medical["tools"]}
        assert "graduate-admissions" in {s["name"] for s in medical["skills"]}
        assert "seo_page_metadata" in {t["name"] for t in search["tools"]}
        for row in scoped["specialists"]:
            assert all(set(t) == {"name", "description"} for t in row["tools"])
        assert (await api.get("/api/v1/chat/tools?conversation_id=")).status_code == 422
        assert pages.urls == []


@pytest.mark.asyncio
async def test_new_page_chat_dispatches_and_streams_without_a_site(monkeypatch):
    from src.agent.providers.base import ModelChunk, ToolCall

    route_model, pages = RouteModel("seo"), Pages()
    provider = FakeModelProvider([
        [ModelChunk(tool_calls=[ToolCall("check", "seo_page_metadata", {})])],
        [ModelChunk(text="已核对页面标题。")],
    ])
    monkeypatch.setattr(chat, "run_manager", RunManager())
    monkeypatch.setattr(chat, "facade", pages)
    monkeypatch.setattr(chat, "build_conversation_model", lambda: provider)
    monkeypatch.setattr(chat.structured_model_automation, "model_runner", route_model)
    app = FastAPI()
    app.include_router(chat.router, prefix="/api/v1")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        response = await api.post("/api/v1/chat/runs", json={
            "messages": [{"role": "user", "content": "帮我看 https://example.com/classes 的标题"}],
        })
        assert response.status_code == 201, response.text
        stream = await api.get(response.json()["events_url"])
        assert "event: agent.dispatched" in stream.text
        assert '"agent": "seo"' in stream.text
        assert "event: run.completed" in stream.text
        assert pages.urls == ["https://example.com/classes"]
        assert route_model.calls == 1


@pytest.mark.asyncio
async def test_second_query_to_same_tool_does_not_clear_the_first_question():
    from src.agent.models import RunStatus
    from src.agent.providers.base import ModelChunk, ToolCall
    from src.agent.runtime.executor import AgentExecutor
    from src.healthcare_agent import build_healthcare_registry

    tool = "medical_exam_registration"
    conditions = {"year": 2026, "region": "湖北", "exam": "医师资格考试"}
    provider = FakeModelProvider([[ModelChunk(tool_calls=[
        ToolCall("exemption", tool, {
            **conditions, "topic": "skills_retake", "skills_year": 2025,
            "skills_passed": True, "comprehensive_passed": False,
        }),
        ToolCall("registration", tool, {**conditions, "topic": "registration"}),
    ])]])
    run = RunManager().create_run([{"role": "user", "content": "技能要重考吗，还要报名吗？"}])
    await AgentExecutor(provider, build_healthcare_registry()).run(run)
    assert run.status == RunStatus.WAITING_FOR_INPUT
    assert run.human_input.tool_arguments["topic"] == "skills_retake"
    assert any("基地" in q for q in run.human_input.questions)
    assert provider.calls == 1
