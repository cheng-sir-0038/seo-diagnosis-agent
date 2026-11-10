from __future__ import annotations

from pathlib import Path

import pytest

from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.notifications.service import NotificationService
from src.seo_agent.schedules.service import WorkScheduleService
from src.seo_agent.services.system import SeoAgentSystemService
from src.seo_agent.tasks.service import TaskDeliveryService


@pytest.mark.asyncio
async def test_product_query_services_return_real_lists_and_details(tmp_path: Path) -> None:
    db = SeoAgentDatabase(tmp_path / "product.db")
    await db.startup()
    try:
        system = SeoAgentSystemService(db)
        site = await system.create_site(
            name="Demo",
            domain="ui.test",
            default_country="US",
            default_language="en",
            timezone="UTC",
        )
        owner = await system.create_actor(actor_type="USER", name="Owner", email="owner@qq.com")
        assert len(await system.list_actors()) == 1

        notifications = NotificationService(db)
        await notifications.seed_event_types()
        subscription = await notifications.create_subscription(
            site_id=site["id"],
            subscriber_actor_id=owner["id"],
            recipient_email="owner@qq.com",
            recipient_role="APPROVER",
            is_default_approver=True,
        )
        listed_subscriptions = await notifications.list_subscriptions(site["id"])
        preferences = await notifications.list_preferences(subscription["id"])
        assert listed_subscriptions[0]["id"] == subscription["id"]
        assert any(item["event_key"] == "approval_requested" for item in preferences)

        schedules = WorkScheduleService(db)
        schedule = await schedules.create_schedule(
            site_id=site["id"],
            schedule_type="OPPORTUNITY_SCAN",
            name="Daily",
            frequency="DAILY",
            timezone="UTC",
        )
        assert await schedules.list_runs(schedule["id"]) == []

        tasks = TaskDeliveryService(db, artifact_root=tmp_path / "artifacts")
        assert await tasks.list_tasks(site["id"]) == []
    finally:
        await db.shutdown()
