from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from src.observability.client import ObservabilityClient
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind
from src.seo_agent.automation.digests import DigestService
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import (
    ActorRow,
    NotificationEventTypeRow,
    NotificationPreferenceRow,
    NotificationSubscriptionRow,
    WorkScheduleRow,
)
from src.seo_agent.opportunities.service import ScanRunService
from src.seo_agent.schedules.provider import LocalScheduleProvider
from src.seo_agent.services.system import SeoAgentSystemService

_WEEKDAYS = {1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri", 6: "sat", 7: "sun"}


class ScheduleAutomationCoordinator:
    def __init__(
        self,
        database: SeoAgentDatabase,
        provider: LocalScheduleProvider,
        scans: ScanRunService,
        system: SeoAgentSystemService,
        *,
        agent_actor_id: str | None = None,
        observability: ObservabilityClient | None = None,
    ) -> None:
        self.database = database
        self.provider = provider
        self.scans = scans
        self.system = system
        self.agent_actor_id = agent_actor_id
        self.observability = observability

    async def sync(self) -> int:
        schedules = await self.database.fetch_all(
            select(WorkScheduleRow.__table__).where(
                WorkScheduleRow.schedule_type == "OPPORTUNITY_SCAN",
                WorkScheduleRow.enabled == 1,
                WorkScheduleRow.archived_at.is_(None),
            )
        )
        valid_ids = {f"opportunity-scan:{schedule['id']}" for schedule in schedules}
        for job in self.provider.scheduler.get_jobs():
            if job.id.startswith("opportunity-scan:") and job.id not in valid_ids:
                self.provider.remove_job(job.id)
        for schedule in schedules:
            hour, minute = self._execution_time(schedule.get("execution_time"))
            day_of_week = None
            if schedule["frequency"] == "WEEKLY":
                day_of_week = _WEEKDAYS.get(int(schedule.get("weekday") or 1), "mon")
            self.provider.add_cron_job(
                job_id=f"opportunity-scan:{schedule['id']}",
                callback=self.run_scan,
                hour=hour,
                minute=minute,
                day_of_week=day_of_week,
                timezone=schedule["timezone"],
                kwargs={"schedule_id": schedule["id"]},
            )
        return len(schedules)

    async def run_scan(
        self, schedule_id: str, *, slot_key: str | None = None,
    ) -> dict[str, Any] | None:
        slot = datetime.fromisoformat(slot_key) if slot_key else datetime.now(UTC)
        if slot.tzinfo is None:
            slot = slot.replace(tzinfo=UTC)
        async with observed_operation(
            self.observability,
            "scheduler.opportunity_scan",
            kind=SpanKind.SCHEDULER,
            component_name="apscheduler",
            input_data={"schedule_id": schedule_id, "slot_key": slot_key},
            trigger_type="scheduled",
        ) as span:
            schedule = await self.database.fetch_one(
                select(WorkScheduleRow.__table__).where(WorkScheduleRow.id == schedule_id)
            )
            if schedule is None:
                return None
            run = await self.scans.start_scheduled_slot(schedule_id, slot)
            if run is None:
                if span is not None:
                    await span.event("scheduler.scan.inactive", {"schedule_id": schedule_id})
                return None
            actor_id = self.agent_actor_id
            if actor_id is None:
                actor = await self.database.fetch_one(
                    select(ActorRow.id)
                    .where(
                        ActorRow.actor_type == "SEO_AGENT",
                        ActorRow.status == "ACTIVE",
                        ActorRow.archived_at.is_(None),
                    )
                    .order_by(ActorRow.created_at)
                    .limit(1)
                )
                actor_id = actor["id"] if actor else None
            title = "启动机会扫描" if run["status"] == "RUNNING" else "跳过机会扫描"
            summary = (
                "扫描运行已创建，等待诊断引擎提交结构化 OpportunityCandidate。"
                if run["status"] == "RUNNING"
                else f"本次计划未启动：{run['status']}"
            )
            await self.system.append_work_record(
                site_id=schedule["site_id"],
                title=title,
                summary=summary,
                record_type="scan_started" if run["status"] == "RUNNING" else "scan_skipped",
                actor_id=actor_id,
                idempotency_key=f"scan-work-record:{run['id']}",
                payload={
                    "scan_run_id": run["id"],
                    "schedule_id": schedule_id,
                    "status": run["status"],
                },
            )
            if span is not None:
                await span.event(
                    "scheduler.scan.created", {"scan_run_id": run["id"], "status": run["status"]}
                )
            return run

    @staticmethod
    def _execution_time(value: str | None) -> tuple[int, int]:
        if not value:
            return 6, 30
        parts = value.split(":", 1)
        return int(parts[0]), int(parts[1])


class DigestScheduleCoordinator:
    def __init__(
        self,
        database: SeoAgentDatabase,
        provider: LocalScheduleProvider,
        digests: DigestService,
        *,
        observability: ObservabilityClient | None = None,
    ) -> None:
        self.database = database
        self.provider = provider
        self.digests = digests
        self.observability = observability

    async def sync(self) -> int:
        preferences = await self.database.fetch_all(
            select(
                NotificationSubscriptionRow.site_id,
                NotificationEventTypeRow.event_key,
                NotificationPreferenceRow.schedule_json,
            )
            .distinct()
            .select_from(NotificationPreferenceRow)
            .join(
                NotificationSubscriptionRow,
                NotificationSubscriptionRow.id == NotificationPreferenceRow.subscription_id,
            )
            .join(
                NotificationEventTypeRow,
                NotificationEventTypeRow.id == NotificationPreferenceRow.event_type_id,
            )
            .where(
                NotificationSubscriptionRow.enabled == 1,
                NotificationSubscriptionRow.archived_at.is_(None),
                NotificationPreferenceRow.enabled == 1,
                NotificationEventTypeRow.event_key.in_(("daily_digest", "weekly_digest")),
            )
        )
        valid_ids: set[str] = set()
        for preference in preferences:
            period = "daily" if preference["event_key"] == "daily_digest" else "weekly"
            job_id = f"digest:{period}:{preference['site_id']}"
            valid_ids.add(job_id)
            schedule = self._schedule(preference.get("schedule_json"), period)
            self.provider.add_cron_job(
                job_id=job_id,
                callback=self.run_digest,
                hour=schedule["hour"],
                minute=schedule["minute"],
                day_of_week=schedule.get("weekday"),
                timezone=schedule["timezone"],
                kwargs={"site_id": preference["site_id"], "period": period},
            )
        for job in self.provider.scheduler.get_jobs():
            if job.id.startswith("digest:") and job.id not in valid_ids:
                self.provider.remove_job(job.id)
        return len(valid_ids)

    async def run_digest(self, site_id: str, period: str) -> dict[str, Any]:
        async with observed_operation(
            self.observability,
            f"scheduler.{period}_digest",
            kind=SpanKind.SCHEDULER,
            component_name="apscheduler",
            input_data={"site_id": site_id, "period": period},
            trigger_type="scheduled",
        ):
            now = datetime.now(UTC)
            period_key = now.strftime("%Y-%m-%d") if period == "daily" else f"{now:%G}-W{now:%V}"
            return await self.digests.create_digest(
                site_id=site_id,
                period=period,
                period_key=period_key,
            )

    @staticmethod
    def _schedule(raw: str | None, period: str) -> dict[str, Any]:
        import json

        try:
            value = json.loads(raw or "{}")
        except json.JSONDecodeError:
            value = {}
        return {
            "hour": int(value.get("hour", 9)),
            "minute": int(value.get("minute", 0)),
            "weekday": value.get("weekday", "mon") if period == "weekly" else None,
            "timezone": str(value.get("timezone", "UTC")),
        }
