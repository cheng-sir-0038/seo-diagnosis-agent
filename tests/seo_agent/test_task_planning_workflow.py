from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.core.exceptions import InternalProviderRequestError, InternalProviderResponseError
from src.seo_agent.approvals.service import ApprovalService
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.services.system import SeoAgentSystemService
from src.seo_agent.tasks.models import TaskPrdContent
from src.seo_agent.tasks.service import TaskDeliveryService
from src.seo_agent.workflows.task_delivery import TaskDeliveryWorkflows
from src.seo_agent.workflows.task_planning import TaskPlanningRequest, TaskPlanningWorkflow


class PrdModel:
    model = "prd-fixture"

    def __init__(self):
        self.calls = 0
        self.fail = False
        self.invalid = False
        self.before_return = None
        self.context = None

    def with_structured_output(self, schema, **_kwargs):
        model = self

        class Runnable:
            async def ainvoke(self, messages):
                model.calls += 1
                model.context = json.loads(messages[-1].content)
                await asyncio.sleep(0)
                if model.fail:
                    raise RuntimeError("private model failure")
                if model.invalid:
                    return schema.model_validate({})
                if model.before_return:
                    await model.before_return()
                return schema.model_validate(
                    {
                        "background": "Approved opportunity evidence",
                        "target_intent": "waterjet cost",
                        "target_keywords": ["waterjet cost"],
                        "target_audience": "buyer",
                        "competitor_gap": "Unverified",
                        "content_type": "article",
                        "content_outline": ["cost factors"],
                        "seo_title": "Waterjet Cost",
                        "seo_description": "Cost factors",
                        "acceptance_criteria": ["Cite the supplied evidence"],
                    }
                )

        return Runnable()


@pytest.fixture
async def planning(tmp_path: Path, approve_opportunity):
    db = SeoAgentDatabase(tmp_path / "business.db")
    await db.startup()
    system = SeoAgentSystemService(db)
    site = await system.create_site(
        name="Planning",
        domain="example.com",
        default_country="US",
        default_language="en",
        timezone="UTC",
    )
    actor = await system.create_actor(actor_type="USER", name="Owner")
    owner = await system.create_actor(actor_type="HUMAN_OPERATOR", name="Writer")
    now = "2026-11-11T00:00:00+00:00"
    for index in range(2):
        await db.execute(
            "INSERT INTO opportunities(id,site_id,fingerprint,normalized_intent,opportunity_type,"
            "target_country,target_language,title,current_score,priority_level,status,policy_version,"
            "first_seen_at,last_seen_at,created_at,updated_at) "
            "VALUES (?, ?, ?, 'waterjet cost', 'KEYWORD_OPPORTUNITY', 'US', 'en', 'Cost', "
            "85, 'HIGH', 'WAITING_FOR_DECISION', 'v1', ?, ?, ?, ?)",
            (f"opp-{index}", site["id"], f"fingerprint-{index}", now, now, now, now),
        )
        await approve_opportunity(db, site["id"], f"opp-{index}", actor["id"])
    tasks = TaskDeliveryService(db, artifact_root=tmp_path / "artifacts")
    model = PrdModel()
    delivery = TaskDeliveryWorkflows(db, tasks, model_factory=lambda: model)
    workflow = TaskPlanningWorkflow(db, tasks, delivery)
    request = TaskPlanningRequest(
        site_id=site["id"],
        title="Waterjet cost guide",
        task_type="ARTICLE",
        priority="HIGH",
        opportunity_ids=["opp-0"],
        owner_actor_id=owner["id"],
        created_by_actor_id=actor["id"],
        idempotency_key="planning-1",
    )
    yield db, site, actor, tasks, model, workflow, request
    await db.shutdown()


@pytest.mark.asyncio
async def test_planning_creates_one_draft_task_prd_and_artifacts_and_replays(planning):
    db, _, _, tasks, model, workflow, request = planning
    approvals_before = await db.fetch_all("SELECT * FROM approval_actions ORDER BY id")
    first = await workflow.execute(request)
    assert first["workflow_status"] == "complete"
    task = await tasks.get_task_detail(first["task_id"])
    assert task["status"] == "DRAFT" and len(task["prd_versions"]) == 1
    assert task["current_prd_version_id"] == first["prd_version_id"]
    artifacts = await db.fetch_all("SELECT * FROM task_prd_artifacts")
    assert len(artifacts) == 2 and all(Path(row["file_path"]).is_file() for row in artifacts)
    assert TaskPrdContent.model_validate_json(task["prd_versions"][0]["structured_content_json"])
    assert first == await workflow.execute(request)
    assert model.calls == 1
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 1
    assert await db.fetch_value("SELECT COUNT(*) FROM task_prd_versions") == 1
    assert await db.fetch_all("SELECT * FROM approval_actions ORDER BY id") == approvals_before
    for table in ("task_dispatches", "deliveries", "publish_records"):
        assert await db.fetch_value(f"SELECT COUNT(*) FROM {table}") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["pending", "low", "newer_pending", "foreign", "duplicates"])
async def test_invalid_opportunities_fail_before_model_or_business_writes(planning, problem):
    db, _, actor, _, model, workflow, request = planning
    if problem in {"pending", "low"}:
        await db.execute(
            "UPDATE opportunities SET status=?, priority_level=? WHERE id='opp-0'",
            ("OBSERVED" if problem == "low" else "WAITING_FOR_DECISION", "LOW"),
        )
    elif problem == "newer_pending":
        await ApprovalService(db).create_action(
            site_id=request.site_id,
            action_type="ADD_OPPORTUNITY_TO_PRODUCTION",
            risk_level="HIGH",
            target_type="opportunity",
            target_id="opp-0",
            requested_payload={},
            approver_actor_id=actor["id"],
            idempotency_key="later-approval",
        )
    elif problem == "foreign":
        other_site = await SeoAgentSystemService(db).create_site(
            name="Other",
            domain="other.example",
            default_country="US",
            default_language="en",
            timezone="UTC",
        )
        await db.execute("UPDATE opportunities SET site_id=? WHERE id='opp-0'", (other_site["id"],))
    else:
        request = request.model_copy(update={"opportunity_ids": ["opp-0", "opp-0"]})
    with pytest.raises((ValueError, KeyError)):
        await workflow.execute(request)
    assert model.calls == 0
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0


@pytest.mark.asyncio
async def test_model_failure_leaves_no_task_and_same_request_can_retry(planning):
    db, _, _, _, model, workflow, request = planning
    model.fail = True
    with pytest.raises(InternalProviderRequestError):
        await workflow.execute(request)
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0
    assert await db.fetch_value("SELECT COUNT(*) FROM work_records") == 0
    model.fail = False
    assert (await workflow.execute(request))["task_id"]
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 1


@pytest.mark.asyncio
async def test_invalid_model_schema_does_not_persist_task_or_prd(planning):
    db, _, _, _, model, workflow, request = planning
    model.invalid = True
    with pytest.raises(InternalProviderResponseError):
        await workflow.execute(request)
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0
    assert await db.fetch_value("SELECT COUNT(*) FROM task_prd_versions") == 0


@pytest.mark.asyncio
async def test_approval_is_rechecked_after_model_before_atomic_write(planning):
    db, _, _, _, model, workflow, request = planning

    async def change_approval():
        action = await db.fetch_one("SELECT id FROM approval_actions WHERE target_id='opp-0'")
        await ApprovalService(db).decide(
            action_id=action["id"], actor_id=request.created_by_actor_id,
            decision="DECISION_CORRECTED",
        )

    model.before_return = change_approval
    with pytest.raises(ValueError, match="审批"):
        await workflow.execute(request)
    assert model.calls == 1
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0
    assert await db.fetch_value("SELECT COUNT(*) FROM task_prd_versions") == 0


@pytest.mark.asyncio
async def test_planning_serializes_duplicate_calls_and_rejects_changed_request(planning):
    db, _, _, _, model, workflow, request = planning
    first, second = await asyncio.gather(workflow.execute(request), workflow.execute(request))
    assert first == second and model.calls == 1
    with pytest.raises(ValueError, match="幂等"):
        await workflow.execute(request.model_copy(update={"title": "Different request"}))
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 1


@pytest.mark.asyncio
async def test_parallel_workflow_instances_share_one_durable_result(planning):
    db, _, _, tasks, model, workflow, request = planning
    other = TaskPlanningWorkflow(db, tasks, workflow.delivery)
    first, second = await asyncio.gather(workflow.execute(request), other.execute(request))
    assert first == second
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 1
    assert await db.fetch_value("SELECT COUNT(*) FROM task_prd_versions") == 1
    assert await db.fetch_value("SELECT COUNT(*) FROM work_records") == 1
    assert model.calls in {1, 2}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["database", "cancelled", "renderer"])
async def test_persistence_failure_rolls_back_and_retry_has_no_duplicate_task(
    planning, monkeypatch, failure
):
    db, _, _, tasks, _, workflow, request = planning
    if failure == "renderer":
        original = tasks.pdf_renderer.render

        def broken_renderer(*args):
            raise OSError("renderer unavailable")

        monkeypatch.setattr(tasks.pdf_renderer, "render", broken_renderer)
    else:
        original = SeoAgentSystemService.append_work_record

        async def broken_record(*args, **kwargs):
            if failure == "cancelled":
                raise asyncio.CancelledError
            raise RuntimeError("write failed after artifact rendering")

        monkeypatch.setattr(SeoAgentSystemService, "append_work_record", broken_record)
    with pytest.raises((RuntimeError, OSError, asyncio.CancelledError)):
        await workflow.execute(request)
    for table in ("execution_tasks", "task_prd_versions", "task_prd_artifacts", "work_records"):
        assert await db.fetch_value(f"SELECT COUNT(*) FROM {table}") == 0
    assert not list(tasks.artifact_root.glob("task_*"))
    if failure == "renderer":
        monkeypatch.setattr(tasks.pdf_renderer, "render", original)
    else:
        monkeypatch.setattr(SeoAgentSystemService, "append_work_record", original)
    result = await workflow.execute(request)
    assert result["task_id"] and await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["site", "owner", "creator", "missing_actor"])
async def test_inactive_scope_rejected_before_model(planning, scope):
    db, _, actor, _, model, workflow, request = planning
    if scope == "site":
        await db.execute("UPDATE sites SET status='INACTIVE' WHERE id=?", (request.site_id,))
    elif scope == "missing_actor":
        request = request.model_copy(update={"created_by_actor_id": "unknown"})
    else:
        actor_id = request.owner_actor_id if scope == "owner" else request.created_by_actor_id
        await db.execute("UPDATE actors SET status='INACTIVE' WHERE id=?", (actor_id,))
    with pytest.raises((ValueError, KeyError)):
        await workflow.execute(request)
    assert model.calls == 0
    assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0


@pytest.mark.asyncio
async def test_planning_api_uses_real_services_and_sanitizes_model_errors(planning, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.api.routes import seo_agent_tasks

    db, _, _, _, model, workflow, request = planning
    monkeypatch.setattr(seo_agent_tasks, "task_planning_workflow", workflow)
    app = FastAPI()
    app.include_router(seo_agent_tasks.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        invalid = await client.post(
            "/seo-agent/tasks/plan", json={**request.model_dump(), "auto_approve": True}
        )
        assert invalid.status_code == 422
        model.fail = True
        unavailable = await client.post("/seo-agent/tasks/plan", json=request.model_dump())
        assert unavailable.status_code == 503 and "private model failure" not in unavailable.text
        assert await db.fetch_value("SELECT COUNT(*) FROM execution_tasks") == 0
        model.fail = False
        result = await client.post("/seo-agent/tasks/plan", json=request.model_dump())
        assert result.status_code == 200 and result.json()["task_id"]
        missing = await client.post(
            "/seo-agent/tasks/plan",
            json={
                **request.model_dump(),
                "idempotency_key": "missing",
                "opportunity_ids": ["unknown"],
            },
        )
        assert missing.status_code == 404
        conflict = await client.post(
            "/seo-agent/tasks/plan", json={**request.model_dump(), "title": "Other"}
        )
        assert conflict.status_code == 400


@pytest.mark.asyncio
async def test_planning_observations_correlate_approval_model_and_atomic_write(planning, tmp_path):
    from src.observability.client import ObservabilityClient
    from src.observability.storage import ObservabilityDatabase

    db, _, _, tasks, model, _, request = planning
    observer = ObservabilityClient(
        ObservabilityDatabase(tmp_path / "observations.db"), project_key="test"
    )
    delivery = TaskDeliveryWorkflows(db, tasks, model_factory=lambda: model, observability=observer)
    workflow = TaskPlanningWorkflow(db, tasks, delivery)
    try:
        result = await workflow.execute(request)
        spans = await observer.database.fetch_all("SELECT * FROM obs_spans")
        assert {row["trace_id"] for row in spans} == {result["trace_id"]}
        assert {row["operation_name"] for row in spans} >= {
            "seo.task_planning",
            "validate_approved_opportunities",
            "seo.prd.generate",
            "persist_task_and_prd",
        }
        model_call = await observer.database.fetch_one("SELECT * FROM obs_model_calls")
        assert model_call["prompt_key"] == "seo.prd.generate"
        assert "waterjet cost" in model_call["input_summary_json"]
        assert "acceptance_criteria" in model_call["output_summary_json"]
        await workflow.execute(request)
        assert await observer.database.fetch_value("SELECT COUNT(*) FROM obs_model_calls") == 1
    finally:
        await observer.shutdown()
