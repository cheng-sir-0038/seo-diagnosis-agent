from __future__ import annotations

import json
from pathlib import Path
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


def test_legacy_workflow_imports_resolve_to_the_new_implementations():
    from src.agent.workspace import supervisor, team
    from src.seo_agent import specialist
    from src.seo_agent.chat import runtime

    assert supervisor.WorkspaceSupervisor is team.WorkspaceTeam
    assert supervisor.conversation_context is team.conversation_context
    assert runtime.build_seo_system_prompt is specialist.build_seo_system_prompt


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


def test_bundled_skill_details_match_the_approved_directory_text():
    from src.api.routes.workspace.directory import bundled_skill_catalog

    root = Path(__file__).resolve().parents[2]
    catalog = root / "frontend/src/services/bundledSkillCatalog.json"
    assert json.loads(catalog.read_text(encoding="utf-8")) == bundled_skill_catalog(), (
        "Regenerate with python -m src.api.routes.workspace.directory "
        "> frontend/src/services/bundledSkillCatalog.json"
    )


@pytest.mark.asyncio
async def test_tool_directory_lists_the_live_registries_without_running_tools(monkeypatch):
    from src.procurement_agent.models import INTAKE_PROMPT
    from src.travel_agent.intake import TRAVEL_INTAKE_PROMPT
    from src.travel_agent.tools import travel_tools

    monkeypatch.setattr(chat, "_travel_registry", travel_tools)
    pages = Pages()
    monkeypatch.setattr(chat, "facade", pages)
    monkeypatch.setattr(chat, "seo_chat_harness_store", SimpleNamespace(
        database=SimpleNamespace(started=False),
    ))

    def unexpected_prompt(*args, **kwargs):
        pytest.fail("The directory must not build a runtime prompt or call a model")

    monkeypatch.setattr(chat, "build_seo_system_prompt", unexpected_prompt)
    monkeypatch.setattr(chat, "build_conversation_model", unexpected_prompt)
    monkeypatch.setattr(chat.structured_model_automation.model_runner, "invoke", unexpected_prompt)
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
        assert search["skills"] == []
        assert {prompt["id"] for prompt in search["default_prompts"]} == {
            "seo-diagnosis", "seo-course-editor",
        }
        for row in (learning, exam, followup):
            assert "无专员提示词" in row["deterministic_path"]
            assert all("分派指令" in prompt["label"] for prompt in row["default_prompts"])
        assert {p["id"]: p["text"] for p in travel["default_prompts"]}[
            "travel-intake"
        ] == TRAVEL_INTAKE_PROMPT
        assert {p["id"]: p["text"] for p in procurement["default_prompts"]}[
            "procurement-intake"
        ] == INTAKE_PROMPT
        assert len(travel["skills"]) == 1 and len(travel["skills"][0]["tools"]) == 6
        limits = {tool["name"]: tool["availability_note"] for tool in travel["tools"]}
        assert "未配置" in limits["travel_transport"] and "未配置" in limits["travel_hotels"]
        assert "未配置" in limits["travel_commute"] and limits["travel_weather"] is None
        database = SimpleNamespace(started=True)

        async def last_target(conversation_id, *, include_dialog):
            assert include_dialog is False
            if conversation_id == "invalid-target":
                return {"target_url": "https://[malformed-private-target"}
            assert conversation_id == "course-turn"
            return {
                "target_url": "https://example.com/private-target", "source_run_id": "private-run",
                "system_prompt": "private-effective-prompt", "server_dialog": "private-user-input",
                "followup_key_hash": "private-access-hash",
            }

        async def find_site(host):
            assert host == "example.com"
            return {
                "id": "configured-site", "status": "ACTIVE", "name": "private-site-name",
                "default_country": "private-setting", "prompt_override": "private-override",
            }

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
        assert search["skills"]
        assert "private-" not in response.text
        from src.api.routes.workspace.directory import bundled_skill_catalog

        bundled_usage = {
            (entry["agent"], name): usage
            for entry in bundled_skill_catalog()
            for name, usage in entry["usage"].items()
        }
        for row in scoped["specialists"]:
            for tool in row["tools"]:
                assert set(tool) == {"name", "description", "guidance", "availability_note", "skill_usage"}
                assert tool["skill_usage"] == bundled_usage[(row["agent"], tool["name"])]
                assert all(tool["skill_usage"].values())
        registration = bundled_usage[("healthcare", "medical_exam_registration")]
        assert "实践技能考试免考核对" in registration["scenario"]
        assert not any("skills_retake" in value for value in registration.values())
        assert bundled_usage[("followup", "followup_read_materials")] != bundled_usage[
            ("followup", "followup_save_confirmed")
        ]
        for row in scoped["specialists"]:
            bound_tools = set()
            prompt_ids = {prompt["id"] for prompt in row["default_prompts"]}
            for skill in row["skills"]:
                assert set(skill) == {"name", "label", "summary", "tools"}
                assert skill["summary"] and skill["tools"] and skill["name"] in prompt_ids
                bound_tools.update(skill["tools"])
            assert bound_tools == {tool["name"] for tool in row["tools"]}
            for prompt in row["default_prompts"]:
                assert set(prompt) == {"id", "label", "text"} and prompt["text"]
        invalid = await api.get("/api/v1/chat/tools?conversation_id=invalid-target")
        assert invalid.status_code == 200
        assert "private-target" not in invalid.text
        assert invalid.json()["specialists"][1]["default_prompts"] == specialists[1]["default_prompts"]
        assert (await api.get("/api/v1/chat/tools?conversation_id=")).status_code == 422
        assert pages.urls == []


@pytest.mark.asyncio
async def test_seo_directory_matches_the_runtime_skills_for_each_available_tool(monkeypatch):
    from src.seo_agent.chat.tools import SEO_TOOL_DEFINITIONS, build_seo_agent_registry
    from src.seo_agent.skills import SEO_SKILLS
    from src.seo_agent.workflows.geo import GeoCitationWorkflow

    capabilities = {item.capability for item in SEO_TOOL_DEFINITIONS}
    facade = SimpleNamespace(live_capabilities=lambda: capabilities, semrush_available=True)
    monkeypatch.setattr(chat, "facade", facade)
    monkeypatch.setattr(chat, "seo_chat_harness_store", SimpleNamespace(database=SimpleNamespace(started=False)))
    site = {"domain": "example.com"}
    registry = build_seo_agent_registry(
        facade, site, snapshots=chat.cache_manager,
        model_runner=chat.structured_model_automation.model_runner,
        geo_citation_client=SimpleNamespace(),
    )
    assert GeoCitationWorkflow.name in registry.names()
    assert set(registry.names()) == SEO_SKILLS.keys()
    app = FastAPI()
    app.include_router(chat.router, prefix="/api/v1")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        response = await api.get("/api/v1/chat/tools", params={"conversation_id": "empty"})
    assert response.status_code == 200
    seo = next(row for row in response.json()["specialists"] if row["agent"] == "seo")
    tool_names = [tool["name"] for tool in seo["tools"]]
    assert set(tool_names) == {
        "seo_keyword_overview", "seo_autocomplete", "seo_news_search", "seo_image_search",
        "seo_serp_overview", "seo_keyword_opportunity",
    }
    assert [skill["tools"] for skill in seo["skills"]] == [[name] for name in tool_names]
    prompt_by_id = {prompt["id"]: prompt["text"] for prompt in seo["default_prompts"]}
    fragments = [prompt_by_id[skill["name"]] for skill in seo["skills"]]
    assert len(set(fragments)) == len(tool_names)
    keyword, autocomplete = (SEO_SKILLS[name] for name in ("seo_keyword_overview", "seo_autocomplete"))
    assert "1–50 个关键词" in seo["skills"][tool_names.index("seo_keyword_overview")]["summary"]
    assert prompt_by_id[keyword.name] == keyword.prompt_fragment
    assert all(tool["skill_usage"]["rules"] == SEO_SKILLS[tool["name"]].prompt_fragment for tool in seo["tools"])
    assert prompt_by_id[autocomplete.name] == autocomplete.prompt_fragment
    runtime_prompt = chat.build_seo_system_prompt(site, [{"function": {"name": keyword.tools[0]}}])
    assert keyword.prompt_fragment in runtime_prompt
    assert autocomplete.prompt_fragment not in runtime_prompt


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
