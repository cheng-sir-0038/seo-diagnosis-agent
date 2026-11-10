from __future__ import annotations

from src.observability.client import ObservabilityClient
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind
from src.seo_agent.automation.outbox import NotificationOutboxWorker
from src.seo_agent.automation.schedules import (
    DigestScheduleCoordinator,
    ScheduleAutomationCoordinator,
)
from src.seo_agent.schedules.provider import LocalScheduleProvider


class AutomationLifecycle:
    def __init__(
        self,
        provider: LocalScheduleProvider,
        outbox: NotificationOutboxWorker,
        scan_schedules: ScheduleAutomationCoordinator,
        digest_schedules: DigestScheduleCoordinator,
        *,
        outbox_interval_minutes: int = 1,
        sync_interval_minutes: int = 5,
        observability: ObservabilityClient | None = None,
    ) -> None:
        self.provider = provider
        self.outbox = outbox
        self.scan_schedules = scan_schedules
        self.digest_schedules = digest_schedules
        self.outbox_interval_minutes = outbox_interval_minutes
        self.sync_interval_minutes = sync_interval_minutes
        self.observability = observability

    async def startup(self) -> None:
        await self.sync()
        self.provider.add_minute_interval_job(
            job_id="notification-outbox",
            callback=self.run_outbox,
            minutes=max(1, self.outbox_interval_minutes),
        )
        self.provider.add_minute_interval_job(
            job_id="automation-schedule-sync",
            callback=self.sync,
            minutes=max(1, self.sync_interval_minutes),
        )

    async def shutdown(self) -> None:
        await self.provider.shutdown()

    async def run_outbox(self):
        async with observed_operation(
            self.observability,
            "scheduler.notification_outbox",
            kind=SpanKind.SCHEDULER,
            component_name="apscheduler",
            trigger_type="scheduled",
        ):
            return await self.outbox.run_once()

    async def sync(self) -> dict[str, int]:
        async with observed_operation(
            self.observability,
            "scheduler.automation_sync",
            kind=SpanKind.SCHEDULER,
            component_name="apscheduler",
            trigger_type="scheduled",
        ):
            return {
                "scan_jobs": await self.scan_schedules.sync(),
                "digest_jobs": await self.digest_schedules.sync(),
            }
