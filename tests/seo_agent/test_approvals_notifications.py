from __future__ import annotations

from pathlib import Path

import pytest

from src.observability.client import ObservabilityClient
from src.observability.storage import ObservabilityDatabase
from src.seo_agent.approvals.graph import build_approval_graph
from src.seo_agent.approvals.service import ApprovalService
from src.seo_agent.checkpoints import CheckpointManager
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.notifications.email import EmailMessage, SmtpEmailProvider, SmtpSettings
from src.seo_agent.notifications.service import NotificationService
from src.seo_agent.services.system import SeoAgentSystemService


@pytest.fixture
async def context(tmp_path: Path):
    db = SeoAgentDatabase(tmp_path / "seo-agent.db")
    await db.startup()
    system = SeoAgentSystemService(db)
    site = await system.create_site(
        name="Demo", domain="demo.test", default_country="US", default_language="en", timezone="UTC"
    )
    owner = await system.create_actor(actor_type="USER", name="Owner", email="owner@qq.com")
    yield db, site, owner
    await db.shutdown()


@pytest.mark.asyncio
async def test_approval_is_idempotent_and_keeps_decision_history(context) -> None:
    db, site, owner = context
    service = ApprovalService(db)
    action = await service.create_action(
        site_id=site["id"],
        action_type="ADD_OPPORTUNITY_TO_PRODUCTION",
        risk_level="HIGH",
        target_type="opportunity",
        target_id="opp-1",
        requested_payload={"task_type": "article"},
        approver_actor_id=owner["id"],
        idempotency_key="approval:1",
    )
    repeated = await service.create_action(
        site_id=site["id"],
        action_type="ADD_OPPORTUNITY_TO_PRODUCTION",
        risk_level="HIGH",
        target_type="opportunity",
        target_id="opp-1",
        requested_payload={"task_type": "article"},
        approver_actor_id=owner["id"],
        idempotency_key="approval:1",
    )
    assert repeated["id"] == action["id"]
    decision = await service.decide(
        action_id=action["id"],
        actor_id=owner["id"],
        decision="EDIT_AND_APPROVE",
        edited_payload={"task_type": "faq"},
        reason="先做 FAQ",
    )
    assert decision["decision"] == "EDIT_AND_APPROVE"
    assert (await service.get_action(action["id"]))["status"] == "EDITED_AND_APPROVED"
    assert len(await service.list_decisions(action["id"])) == 1


@pytest.mark.asyncio
async def test_langgraph_approval_interrupt_and_resume(tmp_path: Path) -> None:
    manager = CheckpointManager(tmp_path / "checkpoint.db")
    await manager.startup()
    try:
        graph = build_approval_graph(manager.require_saver())
        config = {"configurable": {"thread_id": "approval-thread"}}
        interrupted = await graph.ainvoke(
            {"approval_action_id": "approval-1", "current_stage": "created", "decision": None},
            config=config,
        )
        assert interrupted["__interrupt__"]
        from langgraph.types import Command

        resumed = await graph.ainvoke(Command(resume={"decision": "approve"}), config=config)
        assert resumed["decision"]["decision"] == "approve"
        assert resumed["current_stage"] == "approval_resolved"
    finally:
        await manager.shutdown()


class FakeSMTP:
    sent: list[EmailMessage] = []

    def send(self, settings: SmtpSettings, message: EmailMessage) -> str:
        self.sent.append(message)
        return "fake-message-id"


class FailingSMTP:
    def send(self, settings: SmtpSettings, message: EmailMessage) -> str:
        raise OSError("smtp unavailable")


@pytest.mark.asyncio
async def test_smtp_provider_uses_injected_transport_without_network() -> None:
    transport = FakeSMTP()
    provider = SmtpEmailProvider(
        SmtpSettings(
            host="smtp.qq.com",
            port=465,
            security="ssl",
            username="sender@qq.com",
            auth_code="code",
            from_address="sender@qq.com",
            from_name="SEO优化专员",
        ),
        transport=transport,
    )
    result = await provider.send(
        EmailMessage(
            message_id="m1",
            to=["owner@qq.com"],
            subject="待审批",
            text_content="请处理",
            html_content=None,
            idempotency_key="mail:1",
        )
    )
    assert result.success is True
    assert result.provider_message_id == "fake-message-id"
    assert transport.sent[0].subject == "待审批"


@pytest.mark.asyncio
async def test_smtp_provider_persists_success_and_failure_observations(
    tmp_path: Path,
) -> None:
    database = ObservabilityDatabase(tmp_path / "smtp-observability.db")
    await database.startup()
    client = ObservabilityClient(database, project_key="seo-agent")
    settings = SmtpSettings(
        host="smtp.qq.com",
        port=465,
        security="ssl",
        username="sender@qq.com",
        auth_code="code",
        from_address="sender@qq.com",
        from_name="SEO优化专员",
    )
    message = EmailMessage(
        message_id="m-observed",
        to=["owner@qq.com"],
        subject="待审批",
        text_content="请处理",
        idempotency_key="mail:observed",
    )
    try:
        success = await SmtpEmailProvider(
            settings,
            transport=FakeSMTP(),
            observability=client,
        ).send(message)
        failure = await SmtpEmailProvider(
            settings,
            transport=FailingSMTP(),
            observability=client,
        ).send(message.model_copy(update={"idempotency_key": "mail:failed"}))
        calls = await database.fetch_all("SELECT * FROM obs_tool_calls ORDER BY started_at")
        issue = await database.fetch_one("SELECT * FROM obs_issues")
        assert success.success is True
        assert failure.success is False
        assert [call["status"] for call in calls] == ["COMPLETED", "FAILED"]
        assert all(call["tool_name"] == "smtp.send_email" for call in calls)
        assert issue is not None and issue["issue_type"] == "SMTP_ERROR"
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_default_approver_subscription_forces_approval_email(context) -> None:
    db, site, owner = context
    service = NotificationService(db)
    await service.seed_event_types()
    subscription = await service.create_subscription(
        site_id=site["id"],
        subscriber_actor_id=owner["id"],
        recipient_email="owner@qq.com",
        recipient_role="APPROVER",
        is_default_approver=True,
    )
    preference = await service.get_preference(subscription["id"], "approval_requested")
    assert preference["mandatory"] == 1
    assert preference["enabled"] == 1
    with pytest.raises(ValueError, match="强制开启"):
        await service.set_preference(subscription["id"], "approval_requested", enabled=False)


@pytest.mark.asyncio
async def test_notification_event_creates_delivery_and_attempt_history(context) -> None:
    db, site, owner = context
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
    service = NotificationService(db, provider)
    await service.seed_event_types()
    await service.create_subscription(
        site_id=site["id"],
        subscriber_actor_id=owner["id"],
        recipient_email="owner@qq.com",
        recipient_role="APPROVER",
        is_default_approver=True,
    )
    event = await service.create_event(
        site_id=site["id"],
        event_key="approval_requested",
        business_object_type="approval_action",
        business_object_id="approval-1",
        title="待审批",
        content={"summary": "请处理"},
        priority="HIGH",
        idempotency_key="event:1",
    )
    deliveries = await service.list_pending_deliveries()
    assert len(deliveries) == 1
    result = await service.send_delivery(deliveries[0]["id"])
    assert result.success is True
    attempt_count = await db.fetch_value(
        "SELECT COUNT(*) FROM notification_attempts WHERE notification_delivery_id = ?",
        (deliveries[0]["id"],),
    )
    assert attempt_count == 1
    repeated = await service.create_event(
        site_id=site["id"],
        event_key="approval_requested",
        business_object_type="approval_action",
        business_object_id="approval-1",
        title="待审批",
        content={"summary": "请处理"},
        priority="HIGH",
        idempotency_key="event:1",
    )
    assert repeated["id"] == event["id"]


@pytest.mark.asyncio
async def test_reminder_coordinator_creates_one_event_per_due_window(context) -> None:
    from src.seo_agent.notifications.reminders import ApprovalReminderCoordinator

    db, site, owner = context
    approvals = ApprovalService(db, reminder_hours=0)
    notifications = NotificationService(db)
    await notifications.seed_event_types()
    await notifications.create_subscription(
        site_id=site["id"],
        subscriber_actor_id=owner["id"],
        recipient_email="owner@qq.com",
        recipient_role="APPROVER",
        is_default_approver=True,
    )
    await approvals.create_action(
        site_id=site["id"],
        action_type="PUBLISH",
        risk_level="HIGH",
        target_type="task",
        target_id="task-1",
        requested_payload={},
        approver_actor_id=owner["id"],
        idempotency_key="approval:due",
    )
    coordinator = ApprovalReminderCoordinator(approvals, notifications)
    assert await coordinator.run_once() == 1
    assert await db.fetch_value("SELECT COUNT(*) FROM notification_events") == 1


@pytest.mark.asyncio
async def test_mvp_allows_only_one_active_opportunity_schedule(context) -> None:
    from src.seo_agent.schedules.service import WorkScheduleService

    db, site, _ = context
    service = WorkScheduleService(db)
    await service.create_schedule(
        site_id=site["id"],
        schedule_type="OPPORTUNITY_SCAN",
        name="Daily",
        frequency="DAILY",
        timezone="UTC",
    )
    with pytest.raises(Exception, match="UNIQUE constraint failed"):
        await service.create_schedule(
            site_id=site["id"],
            schedule_type="OPPORTUNITY_SCAN",
            name="Weekly",
            frequency="WEEKLY",
            timezone="UTC",
        )


@pytest.mark.asyncio
async def test_local_scheduler_registers_and_removes_jobs() -> None:
    from src.seo_agent.schedules.provider import LocalScheduleProvider

    provider = LocalScheduleProvider()
    await provider.startup()
    try:

        async def callback() -> None:
            return None

        provider.add_interval_job(job_id="test-job", callback=callback, hours=24)
        assert provider.scheduler.get_job("test-job") is not None
        provider.remove_job("test-job")
        assert provider.scheduler.get_job("test-job") is None
    finally:
        await provider.shutdown()
