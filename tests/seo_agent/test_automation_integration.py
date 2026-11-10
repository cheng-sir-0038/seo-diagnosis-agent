from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.observability.client import ObservabilityClient
from src.observability.storage import ObservabilityDatabase
from src.seo_agent.automation.digests import DigestService
from src.seo_agent.automation.models import DeliveryReviewOutput
from src.seo_agent.automation.outbox import NotificationOutboxWorker
from src.seo_agent.automation.schedules import ScheduleAutomationCoordinator
from src.seo_agent.automation.structured import StructuredModelAutomation
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.notifications.email import EmailMessage, SmtpEmailProvider, SmtpSettings
from src.seo_agent.notifications.service import NotificationService
from src.seo_agent.opportunities.service import ScanRunService
from src.seo_agent.schedules.provider import LocalScheduleProvider
from src.seo_agent.schedules.service import WorkScheduleService
from src.seo_agent.services.system import SeoAgentSystemService
from src.seo_agent.tasks.models import TaskPrdContent
from src.seo_agent.tasks.service import TaskDeliveryService


class FakeRunnable:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.inputs: list[Any] = []

    async def ainvoke(self, value: Any) -> Any:
        self.inputs.append(value)
        return self.value


class FakeStructuredModel:
    def __init__(self, responses: dict[type, Any]) -> None:
        self.responses = responses

    def with_structured_output(self, schema: type, **_: Any) -> FakeRunnable:
        return FakeRunnable(self.responses[schema])


class FakeSMTP:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    def send(self, settings: SmtpSettings, message: EmailMessage) -> str:
        self.messages.append(message)
        return f"smtp-{len(self.messages)}"


@pytest.fixture
async def context(tmp_path: Path):
    db = SeoAgentDatabase(tmp_path / "automation.db")
    await db.startup()
    system = SeoAgentSystemService(db)
    site = await system.create_site(
        name="Demo",
        domain="automation.test",
        default_country="US",
        default_language="en",
        timezone="UTC",
    )
    owner = await system.create_actor(actor_type="USER", name="Owner", email="owner@qq.com")
    executor = await system.create_actor(
        actor_type="HUMAN_OPERATOR", name="Writer", email="writer@qq.com"
    )
    agent = await system.create_actor(actor_type="SEO_AGENT", name="SEO Agent")
    now = "2026-07-23T00:00:00+00:00"
    await db.execute(
        "INSERT INTO opportunities(id, site_id, fingerprint, normalized_intent, opportunity_type, "
        "target_country, target_language, title, current_score, priority_level, status, "
        "current_summary, "
        "policy_version, first_seen_at, last_seen_at, created_at, updated_at) "
        "VALUES ('opp-1', ?, 'fp', 'waterjet maintenance cost', 'CONTENT_GAP', 'US', 'en', "
        "'Maintenance Cost', 88, 'HIGH', 'APPROVED', 'Competitors cover pricing', "
        "'v1', ?, ?, ?, ?)",
        (site["id"], now, now, now, now),
    )
    tasks = TaskDeliveryService(db, artifact_root=tmp_path / "artifacts")
    task = await tasks.create_task(
        site_id=site["id"],
        title="Maintenance guide",
        task_type="ARTICLE",
        priority="HIGH",
        opportunity_ids=["opp-1"],
        owner_actor_id=owner["id"],
    )
    yield db, system, site, owner, executor, agent, tasks, task, tmp_path
    await db.shutdown()


def prd_result() -> TaskPrdContent:
    return TaskPrdContent(
        background="Competitors cover pricing and this site does not.",
        target_intent="waterjet maintenance cost",
        target_keywords=["waterjet maintenance cost"],
        target_audience="maintenance buyer",
        competitor_gap="missing cost breakdown",
        content_type="article",
        content_outline=["cost factors", "service schedule", "FAQ"],
        required_questions=["How much does maintenance cost?"],
        seo_title="Waterjet Maintenance Cost Guide",
        seo_description="Understand waterjet maintenance cost.",
        internal_links=["/services"],
        prohibited_items=["invented prices"],
        delivery_requirements=["Markdown"],
        acceptance_criteria=["covers cost factors"],
        monitoring_goals=[{"type": "TOP20", "keyword": "waterjet maintenance cost"}],
    )


@pytest.mark.asyncio
async def test_structured_model_generates_prd_and_review(context) -> None:
    db, _, _, owner, executor, _, tasks, task, tmp_path = context
    review_result = DeliveryReviewOutput(
        status="ACCEPTED",
        score=92,
        passed_items=[{"key": "intent"}],
        failed_items=[],
        missing_items=[],
        revision_instructions=[],
    )
    model = FakeStructuredModel({TaskPrdContent: prd_result(), DeliveryReviewOutput: review_result})
    automation = StructuredModelAutomation(db, tasks, model_factory=lambda: model)
    prd = await automation.generate_prd(task_id=task["id"], created_by_actor_id=owner["id"])
    assert prd["version_no"] == 1
    assert (
        await db.fetch_value(
            "SELECT COUNT(*) FROM task_prd_artifacts WHERE prd_version_id = ?", (prd["id"],)
        )
        == 2
    )
    delivery = await tasks.submit_delivery(
        task_id=task["id"],
        prd_version_id=prd["id"],
        title="Draft",
        summary="ready",
        structured_content={"body": "cost factors"},
        submitted_by_actor_id=executor["id"],
    )
    review = await automation.review_delivery(
        delivery_id=delivery["id"], reviewer_actor_id=owner["id"]
    )
    assert review["status"] == "ACCEPTED"
    assert review["score"] == 92


@pytest.mark.asyncio
async def test_outbox_worker_sends_current_provider_and_records_attempt(context) -> None:
    db, _, site, owner, _, _, _, _, _ = context
    transport = FakeSMTP()
    provider = SmtpEmailProvider(
        SmtpSettings(
            host="smtp.qq.com",
            port=465,
            security="ssl",
            username="sender@qq.com",
            auth_code="code",
            from_address="sender@qq.com",
        ),
        transport=transport,
    )
    notifications = NotificationService(db, provider)
    await notifications.seed_event_types()
    await notifications.create_subscription(
        site_id=site["id"],
        subscriber_actor_id=owner["id"],
        recipient_email="owner@qq.com",
        recipient_role="APPROVER",
        is_default_approver=True,
    )
    await notifications.create_event(
        site_id=site["id"],
        event_key="approval_requested",
        business_object_type="approval_action",
        business_object_id="approval-1",
        title="待审批",
        content={"summary": "请处理"},
        priority="HIGH",
        idempotency_key="outbox:event",
    )
    result = await NotificationOutboxWorker(db, notifications, provider, max_attempts=3).run_once()
    assert result.sent == 1 and result.failed == 0
    assert len(transport.messages) == 1
    assert await db.fetch_value("SELECT COUNT(*) FROM notification_attempts") == 1


@pytest.mark.asyncio
async def test_digest_uses_business_records_and_creates_one_event(context) -> None:
    db, system, site, owner, _, agent, _, _, _ = context
    notifications = NotificationService(db)
    await notifications.seed_event_types()
    subscription = await notifications.create_subscription(
        site_id=site["id"],
        subscriber_actor_id=owner["id"],
        recipient_email="owner@qq.com",
        recipient_role="APPROVER",
        is_default_approver=True,
    )
    await notifications.set_preference(subscription["id"], "daily_digest", enabled=True)
    await system.append_work_record(
        site_id=site["id"],
        title="发现机会",
        summary="评分 88",
        record_type="opportunity_discovered",
        actor_id=agent["id"],
        idempotency_key="record:digest",
    )
    digest = await DigestService(db, notifications).create_digest(
        site_id=site["id"], period="daily", period_key="2026-07-23"
    )
    repeated = await DigestService(db, notifications).create_digest(
        site_id=site["id"], period="daily", period_key="2026-07-23"
    )
    assert repeated["id"] == digest["id"]
    assert await db.fetch_value("SELECT COUNT(*) FROM notification_deliveries") == 1


@pytest.mark.asyncio
async def test_schedule_coordinator_syncs_and_triggers_real_scan_run(context) -> None:
    db, system, site, _, _, agent, _, _, tmp_path = context
    schedules = WorkScheduleService(db)
    schedule = await schedules.create_schedule(
        site_id=site["id"],
        schedule_type="OPPORTUNITY_SCAN",
        name="Daily",
        frequency="DAILY",
        timezone="UTC",
        execution_time="06:30",
    )
    provider = LocalScheduleProvider()
    observability_db = ObservabilityDatabase(tmp_path / "scheduler-observability.db")
    await observability_db.startup()
    observability = ObservabilityClient(observability_db, project_key="seo-agent")
    await provider.startup()
    try:
        coordinator = ScheduleAutomationCoordinator(
            db,
            provider,
            ScanRunService(db),
            system,
            agent_actor_id=agent["id"],
            observability=observability,
        )
        synced = await coordinator.sync()
        assert synced == 1
        assert provider.scheduler.get_job(f"opportunity-scan:{schedule['id']}") is not None
        run = await coordinator.run_scan(schedule["id"], slot_key="2026-07-23T06:30")
        assert run["status"] == "RUNNING"
        assert (
            await db.fetch_value(
                "SELECT COUNT(*) FROM work_records WHERE record_type = 'scan_started'"
            )
            == 1
        )
        span = await observability_db.fetch_one("SELECT * FROM obs_spans WHERE kind = 'SCHEDULER'")
        assert span is not None
        assert span["operation_name"] == "scheduler.opportunity_scan"
    finally:
        await provider.shutdown()
        await observability_db.shutdown()


@pytest.mark.asyncio
async def test_local_schedule_provider_can_restart_after_shutdown() -> None:
    provider = LocalScheduleProvider()
    await provider.startup()
    first_scheduler = provider.scheduler
    await provider.shutdown()

    await provider.startup()
    try:
        assert provider.running is True
        assert provider.scheduler is not first_scheduler
    finally:
        await provider.shutdown()
