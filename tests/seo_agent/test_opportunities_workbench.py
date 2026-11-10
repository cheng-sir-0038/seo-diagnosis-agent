from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from src.models.common import Capability
from src.observability.client import ObservabilityClient
from src.observability.storage import ObservabilityDatabase
from src.seo_agent.approvals.service import ApprovalService
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.notifications.service import NotificationService
from src.seo_agent.opportunities.models import OpportunityCandidate, OpportunityScoreFactors
from src.seo_agent.opportunities.service import OpportunityService, ScanRunService
from src.seo_agent.schedules.service import WorkScheduleService
from src.seo_agent.services.system import SeoAgentSystemService
from src.seo_agent.tools.seo_data import SeoDataToolBridge
from src.seo_agent.workbench.service import WorkbenchService


class FakeFacade:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def query(self, **kwargs):
        self.calls.append(kwargs)
        return {"meta": {"source": "dataforseo"}, "data": [{"keyword": "waterjet"}]}


@pytest.fixture
async def context(tmp_path: Path):
    db = SeoAgentDatabase(tmp_path / "seo-agent.db")
    await db.startup()
    system = SeoAgentSystemService(db)
    site = await system.create_site(
        name="Demo", domain="opps.test", default_country="US", default_language="en", timezone="UTC"
    )
    owner = await system.create_actor(actor_type="USER", name="Owner", email="owner@qq.com")
    agent = await system.create_actor(actor_type="SEO_AGENT", name="SEO Agent")
    schedules = WorkScheduleService(db)
    schedule = await schedules.create_schedule(
        site_id=site["id"],
        schedule_type="OPPORTUNITY_SCAN",
        name="Daily",
        frequency="DAILY",
        timezone="UTC",
    )
    notifications = NotificationService(db)
    await notifications.seed_event_types()
    await notifications.create_subscription(
        site_id=site["id"],
        subscriber_actor_id=owner["id"],
        recipient_email="owner@qq.com",
        recipient_role="APPROVER",
        is_default_approver=True,
    )
    approvals = ApprovalService(db)
    yield db, site, owner, agent, schedule, approvals, notifications
    await db.shutdown()


def candidate(score_boost: int = 0) -> OpportunityCandidate:
    return OpportunityCandidate(
        normalized_intent="waterjet maintenance cost",
        opportunity_type="CONTENT_GAP",
        target_country="US",
        target_language="en",
        title="Waterjet maintenance cost guide",
        summary="Competitors cover cost breakdown",
        keywords=["waterjet maintenance cost", "waterjet service price"],
        competitor_pages=[
            "HTTPS://COMPETITOR.COM/Guide/#section",
            "https://competitor.com/Guide/",
        ],
        evidence_snapshot_ids=["snapshot-1"],
        score_factors=OpportunityScoreFactors(
            search_demand=min(100, 80 + score_boost),
            business_intent=90,
            content_gap=85,
            difficulty_fit=75,
            site_fit=80,
            evidence_confidence=90,
        ),
        search_volume=1000 + score_boost * 50,
        keyword_difficulty=35,
        competitor_count=8,
    )


@pytest.mark.asyncio
async def test_scan_run_forbids_overlap_and_records_skip(context) -> None:
    db, _, _, _, schedule, _, _ = context
    service = ScanRunService(db)
    first = await service.start(
        schedule_id=schedule["id"], trigger_type="SCHEDULED", idempotency_key="scan:1"
    )
    skipped = await service.start(
        schedule_id=schedule["id"], trigger_type="SCHEDULED", idempotency_key="scan:2"
    )
    assert first["status"] == "RUNNING"
    assert skipped["status"] == "SKIPPED_ALREADY_RUNNING"
    assert skipped["existing_run_id"] == first["id"]
    manual = await service.start(
        schedule_id=schedule["id"], trigger_type="MANUAL", idempotency_key="scan:3"
    )
    assert manual["id"] == first["id"]


@pytest.mark.asyncio
async def test_seo_data_tool_bridge_calls_facade_with_standard_capability() -> None:
    facade = FakeFacade()
    bridge = SeoDataToolBridge(facade)
    result = await bridge.query(
        capability=Capability.ORGANIC_KEYWORDS,
        site_id="example.com",
        params={"country": "us"},
        task_id="run-1",
    )
    assert result["meta"]["source"] == "dataforseo"
    assert facade.calls[0]["capability"] == Capability.ORGANIC_KEYWORDS
    tools = bridge.build_tools()
    assert {tool.name for tool in tools} >= {"seo_organic_keywords", "seo_serp_overview"}


@pytest.mark.asyncio
async def test_seo_data_tool_bridge_persists_adapter_observation(tmp_path: Path) -> None:
    database = ObservabilityDatabase(tmp_path / "seo-data-observability.db")
    await database.startup()
    client = ObservabilityClient(database, project_key="seo-agent")
    facade = FakeFacade()
    bridge = SeoDataToolBridge(facade, observability=client)
    try:
        result = await bridge.query(
            capability=Capability.ORGANIC_KEYWORDS,
            site_id="example.com",
            params={"country": "us"},
            task_id="run-observed",
        )
        call = await database.fetch_one("SELECT * FROM obs_tool_calls")
        span = await database.fetch_one("SELECT * FROM obs_spans WHERE kind = 'ADAPTER'")
        assert result["meta"]["source"] == "dataforseo"
        assert call is not None
        assert call["tool_name"] == "seo_data.organic_keywords"
        assert call["adapter_key"] == "dataforseo"
        assert span is not None
        assert span["operation_name"] == "seo_data.organic_keywords"
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_ingest_candidate_api_preserves_validated_candidate(context, monkeypatch) -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.api.routes import seo_agent_opportunities

    db, site, owner, agent, schedule, approvals, notifications = context
    scan = await ScanRunService(db).start(
        schedule_id=schedule["id"], trigger_type="MANUAL", idempotency_key="scan:api-ingest"
    )
    service = OpportunityService(db, approvals=approvals, notifications=notifications)
    monkeypatch.setattr(seo_agent_opportunities, "opportunity_service", service)
    app = FastAPI()
    app.include_router(seo_agent_opportunities.router)
    payload = {
        "site_id": site["id"], "scan_run_id": scan["id"], "candidate": candidate().model_dump(),
        "changed_by_actor_id": agent["id"], "default_approver_actor_id": owner["id"],
        "idempotency_key": "candidate:api-ingest",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/seo-agent/opportunities/ingest", json=payload)
        assert first.status_code == 201
        repeated = await client.post("/seo-agent/opportunities/ingest", json=payload)
        assert repeated.status_code == 201
        assert repeated.json()["id"] == first.json()["id"]
        invalid = await client.post(
            "/seo-agent/opportunities/ingest", json={**payload, "candidate": {"title": "bad"}}
        )
        assert invalid.status_code == 422
    assert await db.fetch_value("SELECT COUNT(*) FROM opportunities") == 1


@pytest.mark.asyncio
async def test_opportunity_upsert_adds_observation_keywords_approval_and_notification(
    context,
) -> None:
    db, site, owner, agent, schedule, approvals, notifications = context
    scan = await ScanRunService(db).start(
        schedule_id=schedule["id"], trigger_type="SCHEDULED", idempotency_key="scan:opportunity"
    )
    service = OpportunityService(db, approvals=approvals, notifications=notifications)
    opportunity = await service.upsert_candidate(
        site_id=site["id"],
        scan_run_id=scan["id"],
        candidate=candidate(),
        changed_by_actor_id=agent["id"],
        idempotency_key="candidate:1",
        default_approver_actor_id=owner["id"],
    )
    repeated = await service.upsert_candidate(
        site_id=site["id"],
        scan_run_id=scan["id"],
        candidate=candidate(),
        changed_by_actor_id=agent["id"],
        idempotency_key="candidate:1",
        default_approver_actor_id=owner["id"],
    )
    assert repeated["id"] == opportunity["id"]
    assert opportunity["priority_level"] == "HIGH"
    assert opportunity["status"] == "WAITING_FOR_DECISION"
    assert (
        await db.fetch_value(
            "SELECT COUNT(*) FROM opportunity_observations WHERE opportunity_id = ?",
            (opportunity["id"],),
        )
        == 1
    )
    assert (
        await db.fetch_value(
            "SELECT COUNT(*) FROM opportunity_keywords WHERE opportunity_id = ?",
            (opportunity["id"],),
        )
        == 2
    )
    assert (
        await db.fetch_value(
            "SELECT COUNT(*) FROM opportunity_competitor_pages WHERE opportunity_id = ?",
            (opportunity["id"],),
        )
        == 1
    )
    detail = await service.get_detail(opportunity["id"])
    assert detail["competitor_pages"][0]["url"] == "https://competitor.com/Guide"
    assert (
        await db.fetch_value(
            "SELECT COUNT(*) FROM approval_actions WHERE target_id = ?", (opportunity["id"],)
        )
        == 1
    )
    assert (
        await db.fetch_value(
            "SELECT COUNT(*) FROM notification_events WHERE business_object_id = ?",
            (opportunity["id"],),
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "expected_status"),
    [("APPROVE", "APPROVED"), ("EDIT_AND_APPROVE", "APPROVED"), ("REJECT", "REJECTED")],
)
async def test_opportunity_decision_does_not_request_approval_again(
    context, decision: str, expected_status: str
) -> None:
    db, site, owner, agent, schedule, approvals, notifications = context
    scans = ScanRunService(db)
    service = OpportunityService(db, approvals=approvals, notifications=notifications)
    first = await scans.start(
        schedule_id=schedule["id"], trigger_type="MANUAL", idempotency_key="scan:approved:1"
    )
    opportunity = await service.upsert_candidate(
        site_id=site["id"], scan_run_id=first["id"], candidate=candidate(),
        changed_by_actor_id=agent["id"], idempotency_key="candidate:approved:1",
        default_approver_actor_id=owner["id"],
    )
    action = (await approvals.list_pending(site["id"]))[0]
    await approvals.decide(
        action_id=action["id"], actor_id=owner["id"], decision=decision, reason="test decision"
    )

    await scans.complete(first["id"])
    second = await scans.start(
        schedule_id=schedule["id"], trigger_type="MANUAL", idempotency_key="scan:approved:2"
    )
    await service.upsert_candidate(
        site_id=site["id"], scan_run_id=second["id"], candidate=candidate(),
        changed_by_actor_id=agent["id"], idempotency_key="candidate:approved:2",
        default_approver_actor_id=owner["id"],
    )
    assert len(await approvals.list_pending(site["id"])) == 0
    updated = await service.get(opportunity["id"])
    assert updated["status"] == expected_status
    if decision == "REJECT":
        assert updated["rejection_reason"] == "test decision"
    history = await db.fetch_all(
        "SELECT from_status, to_status, changed_by_actor_id FROM opportunity_status_history "
        "WHERE opportunity_id = ? ORDER BY created_at", (opportunity["id"],)
    )
    assert history[-1] == {
        "from_status": "WAITING_FOR_DECISION", "to_status": expected_status,
        "changed_by_actor_id": owner["id"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("factor", "expected_initial"), [(30, "OBSERVED"), (65, "OPPORTUNITY_POOL")]
)
@pytest.mark.parametrize(
    ("decision", "expected_final"), [("APPROVE", "APPROVED"), ("REJECT", "REJECTED")]
)
async def test_manual_approval_for_non_high_opportunity(
    context, factor: int, expected_initial: str, decision: str, expected_final: str
) -> None:
    from src.seo_agent.tasks.service import TaskDeliveryService

    db, site, owner, agent, schedule, approvals, notifications = context
    scan = await ScanRunService(db).start(
        schedule_id=schedule["id"], trigger_type="MANUAL", idempotency_key="scan:manual"
    )
    proposal = candidate()
    proposal.score_factors = OpportunityScoreFactors(
        search_demand=factor, business_intent=factor, content_gap=factor,
        difficulty_fit=factor, site_fit=factor, evidence_confidence=factor,
    )
    opportunities = OpportunityService(db, approvals=approvals, notifications=notifications)
    opportunity = await opportunities.upsert_candidate(
        site_id=site["id"], scan_run_id=scan["id"], candidate=proposal,
        changed_by_actor_id=agent["id"], idempotency_key="candidate:manual",
        default_approver_actor_id=owner["id"],
    )
    assert opportunity["status"] == expected_initial
    assert await approvals.list_pending(site["id"]) == []
    action = await approvals.create_action(
        site_id=site["id"], action_type="ADD_OPPORTUNITY_TO_PRODUCTION", risk_level="HIGH",
        target_type="opportunity", target_id=opportunity["id"], requested_payload={},
        approver_actor_id=owner["id"], idempotency_key="approval:manual",
    )
    with pytest.raises(PermissionError):
        await approvals.decide(action_id=action["id"], actor_id=agent["id"], decision=decision)
    await approvals.decide(action_id=action["id"], actor_id=owner["id"], decision=decision)
    assert (await opportunities.get(opportunity["id"]))["status"] == expected_final
    if decision == "APPROVE":
        task = await TaskDeliveryService(db).create_task(
            site_id=site["id"], title="Guide", task_type="ARTICLE", priority="LOW",
            opportunity_ids=[opportunity["id"]], owner_actor_id=owner["id"],
        )
        assert task["status"] == "DRAFT"


@pytest.mark.asyncio
async def test_opportunity_decision_retry_and_correction(context) -> None:
    db, site, owner, agent, schedule, approvals, notifications = context
    scan = await ScanRunService(db).start(
        schedule_id=schedule["id"], trigger_type="MANUAL", idempotency_key="scan:correction"
    )
    service = OpportunityService(db, approvals=approvals, notifications=notifications)
    opportunity = await service.upsert_candidate(
        site_id=site["id"], scan_run_id=scan["id"], candidate=candidate(),
        changed_by_actor_id=agent["id"], idempotency_key="candidate:correction",
        default_approver_actor_id=owner["id"],
    )
    action = (await approvals.list_pending(site["id"]))[0]
    first, retry = await asyncio.gather(*(
        approvals.decide(action_id=action["id"], actor_id=owner["id"], decision="APPROVE")
        for _ in range(2)
    ))
    assert retry["id"] == first["id"]
    assert len(await approvals.list_decisions(action["id"])) == 1
    with pytest.raises(ValueError, match="审批已处理"):
        await approvals.decide(action_id=action["id"], actor_id=owner["id"], decision="REJECT")

    await approvals.decide(
        action_id=action["id"], actor_id=owner["id"], decision="DECISION_CORRECTED"
    )
    reopened = await approvals.get_action(action["id"])
    assert reopened["status"] == "WAITING_FOR_APPROVAL"
    assert reopened["next_remind_at"] is not None
    assert (await service.get(opportunity["id"]))["status"] == "WAITING_FOR_DECISION"
    await approvals.decide(action_id=action["id"], actor_id=owner["id"], decision="REJECT")
    assert (await service.get(opportunity["id"]))["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_opportunity_decision_rolls_back_if_status_update_fails(context) -> None:
    db, site, owner, agent, schedule, approvals, notifications = context
    scan = await ScanRunService(db).start(
        schedule_id=schedule["id"], trigger_type="MANUAL", idempotency_key="scan:rollback"
    )
    service = OpportunityService(db, approvals=approvals, notifications=notifications)
    opportunity = await service.upsert_candidate(
        site_id=site["id"], scan_run_id=scan["id"], candidate=candidate(),
        changed_by_actor_id=agent["id"], idempotency_key="candidate:rollback",
        default_approver_actor_id=owner["id"],
    )
    action = (await approvals.list_pending(site["id"]))[0]
    await db.execute(
        "CREATE TRIGGER reject_test_update BEFORE UPDATE OF status ON opportunities "
        "WHEN NEW.status = 'APPROVED' BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
    )
    with pytest.raises(aiosqlite.IntegrityError, match="forced failure"):
        await approvals.decide(action_id=action["id"], actor_id=owner["id"], decision="APPROVE")
    assert (await approvals.get_action(action["id"]))["status"] == "WAITING_FOR_APPROVAL"
    assert (await service.get(opportunity["id"]))["status"] == "WAITING_FOR_DECISION"
    assert await approvals.list_decisions(action["id"]) == []


@pytest.mark.asyncio
async def test_opportunity_decision_api_checks_actor_and_conflicts(context, monkeypatch) -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.api.routes import seo_agent_approvals, seo_agent_opportunities

    db, site, owner, agent, schedule, approvals, notifications = context
    scan = await ScanRunService(db).start(
        schedule_id=schedule["id"], trigger_type="MANUAL", idempotency_key="scan:api"
    )
    service = OpportunityService(db, approvals=approvals, notifications=notifications)
    opportunity = await service.upsert_candidate(
        site_id=site["id"], scan_run_id=scan["id"], candidate=candidate(),
        changed_by_actor_id=agent["id"], idempotency_key="candidate:api",
        default_approver_actor_id=owner["id"],
    )
    action = (await approvals.list_pending(site["id"]))[0]
    monkeypatch.setattr(seo_agent_approvals, "approval_service", approvals)
    monkeypatch.setattr(seo_agent_opportunities, "opportunity_service", service)
    app = FastAPI()
    app.include_router(seo_agent_approvals.router)
    app.include_router(seo_agent_opportunities.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        approval_url = f"/seo-agent/approvals/{action['id']}/decisions"
        reject_url = f"/seo-agent/opportunities/{opportunity['id']}/reject"
        response = await client.post(reject_url, json={"actor_id": agent["id"], "reason": "no"})
        assert response.status_code == 403
        response = await client.post(
            approval_url, json={"actor_id": agent["id"], "decision": "APPROVE"}
        )
        assert response.status_code == 403
        response = await client.post(
            approval_url, json={"actor_id": owner["id"], "decision": "APPROVE"}
        )
        assert response.status_code == 201
        response = await client.post(
            approval_url, json={"actor_id": owner["id"], "decision": "REJECT"}
        )
        assert response.status_code == 400
    assert (await service.get(opportunity["id"]))["status"] == "APPROVED"


@pytest.mark.asyncio
async def test_opportunity_to_local_publish_and_monitoring(context, tmp_path, monkeypatch) -> None:
    from src.seo_agent.monitoring.service import MonitoringService
    from src.seo_agent.publishing.html_site import (
        HtmlSitePublishAdapter,
        HtmlSiteSettings,
        PublishVerification,
    )
    from src.seo_agent.publishing.service import PublishingService
    from src.seo_agent.tasks.models import TaskPrdContent
    from src.seo_agent.tasks.service import TaskDeliveryService

    db, site, owner, agent, schedule, approvals, notifications = context
    scan = await ScanRunService(db).start(
        schedule_id=schedule["id"], trigger_type="MANUAL", idempotency_key="scan:local-flow"
    )
    service = OpportunityService(db, approvals=approvals, notifications=notifications)
    opportunity = await service.upsert_candidate(
        site_id=site["id"], scan_run_id=scan["id"], candidate=candidate(),
        changed_by_actor_id=agent["id"], idempotency_key="candidate:local-flow",
        default_approver_actor_id=owner["id"],
    )
    action = (await approvals.list_pending(site["id"]))[0]
    await approvals.decide(action_id=action["id"], actor_id=owner["id"], decision="APPROVE")
    assert (await service.get(opportunity["id"]))["status"] == "APPROVED"

    tasks = TaskDeliveryService(db, artifact_root=tmp_path / "artifacts")
    task = await tasks.create_task(
        site_id=site["id"], title="Guide", task_type="ARTICLE", priority="HIGH",
        opportunity_ids=[opportunity["id"]], owner_actor_id=owner["id"],
    )
    prd = await tasks.create_prd_version(
        task_id=task["id"], title="PRD", created_by_actor_id=owner["id"],
        content=TaskPrdContent(
            background="Background", target_intent="example", target_keywords=["example"],
            target_audience="Reader", competitor_gap="Gap", content_type="article",
            content_outline=["Guide"], seo_title="Guide", seo_description="Guide",
            acceptance_criteria=["Complete"],
        ),
    )
    await tasks.dispatch_task(
        task_id=task["id"], prd_version_id=prd["id"], recipient_actor_id=owner["id"],
        idempotency_key="dispatch:local-flow",
    )
    delivery = await tasks.submit_delivery(
        task_id=task["id"], prd_version_id=prd["id"], title="Guide", summary="Ready",
        structured_content={"html": "<h1>Guide</h1>"}, submitted_by_actor_id=owner["id"],
    )
    await tasks.review_delivery(
        delivery_id=delivery["id"], prd_version_id=prd["id"], reviewer_actor_id=owner["id"],
        status="ACCEPTED", score=95,
    )
    adapter = HtmlSitePublishAdapter(
        HtmlSiteSettings(root_path=tmp_path / "site", base_url="https://example.com")
    )

    async def local_verify(_url: str) -> PublishVerification:
        return PublishVerification(reachable=False, error_message="Local preview only")

    monkeypatch.setattr(adapter, "verify", local_verify)
    monitoring = MonitoringService(db)
    publishing = PublishingService(db, adapter, monitoring)
    record = await publishing.publish_delivery(
        task_id=task["id"], delivery_id=delivery["id"], slug="guide",
        html_content="<h1>Guide</h1>", submitted_by_actor_id=owner["id"],
        idempotency_key="publish:local-flow", target_environment="preview",
    )
    assert (tmp_path / "site/guide.html").read_text() == "<h1>Guide</h1>"
    confirmed = await publishing.confirm_publish(
        record["id"], confirmed_by_actor_id=owner["id"], published_url=record["candidate_url"]
    )
    assert confirmed["error_code"] == "URL_NOT_REACHABLE"
    assert await monitoring.get_plan_by_publish(record["id"]) is not None


@pytest.mark.asyncio
async def test_rejected_opportunity_reopens_only_on_major_change(context) -> None:
    db, site, owner, agent, schedule, approvals, notifications = context
    scans = ScanRunService(db)
    first_scan = await scans.start(
        schedule_id=schedule["id"], trigger_type="SCHEDULED", idempotency_key="scan:r1"
    )
    service = OpportunityService(db, approvals=approvals, notifications=notifications)
    first = await service.upsert_candidate(
        site_id=site["id"],
        scan_run_id=first_scan["id"],
        candidate=candidate(),
        changed_by_actor_id=agent["id"],
        idempotency_key="candidate:r1",
        default_approver_actor_id=owner["id"],
    )
    with pytest.raises(PermissionError):
        await service.reject_opportunity(first["id"], reason="优先级不足", actor_id=agent["id"])
    await service.reject_opportunity(first["id"], reason="优先级不足", actor_id=owner["id"])
    assert await approvals.list_pending(site["id"]) == []
    await scans.complete(first_scan["id"])
    second_scan = await scans.start(
        schedule_id=schedule["id"], trigger_type="SCHEDULED", idempotency_key="scan:r2"
    )
    unchanged = await service.upsert_candidate(
        site_id=site["id"],
        scan_run_id=second_scan["id"],
        candidate=candidate(),
        changed_by_actor_id=agent["id"],
        idempotency_key="candidate:r2",
        default_approver_actor_id=owner["id"],
    )
    assert unchanged["status"] == "REJECTED"
    await scans.complete(second_scan["id"])
    third_scan = await scans.start(
        schedule_id=schedule["id"], trigger_type="SCHEDULED", idempotency_key="scan:r3"
    )
    changed = await service.upsert_candidate(
        site_id=site["id"],
        scan_run_id=third_scan["id"],
        candidate=candidate(score_boost=30),
        changed_by_actor_id=agent["id"],
        idempotency_key="candidate:r3",
        default_approver_actor_id=owner["id"],
    )
    assert changed["status"] == "WAITING_FOR_DECISION"
    assert len(await approvals.list_pending(site["id"])) == 1
    assert changed["reopened_at"] is not None


@pytest.mark.asyncio
async def test_workbench_summary_reads_business_tables(context) -> None:
    db, site, owner, agent, schedule, approvals, notifications = context
    scan = await ScanRunService(db).start(
        schedule_id=schedule["id"], trigger_type="SCHEDULED", idempotency_key="scan:dash"
    )
    await OpportunityService(db, approvals=approvals, notifications=notifications).upsert_candidate(
        site_id=site["id"],
        scan_run_id=scan["id"],
        candidate=candidate(),
        changed_by_actor_id=agent["id"],
        idempotency_key="candidate:dash",
        default_approver_actor_id=owner["id"],
    )
    summary = await WorkbenchService(db).summary(site["id"])
    assert summary["pending_approvals"] == 1
    assert summary["high_value_opportunities"] == 1
    assert summary["active_scan_runs"] == 1
