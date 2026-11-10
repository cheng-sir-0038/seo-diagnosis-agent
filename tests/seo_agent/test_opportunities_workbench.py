from __future__ import annotations

from pathlib import Path

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
    await service.reject_opportunity(first["id"], reason="优先级不足", actor_id=owner["id"])
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
