from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger


class LocalScheduleProvider:
    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone="UTC")

    @property
    def running(self) -> bool:
        return self.scheduler.running

    async def startup(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()

    async def shutdown(self) -> None:
        current = self.scheduler
        if current.running:
            current.shutdown(wait=False)
            await asyncio.sleep(0)
        self.scheduler = AsyncIOScheduler(timezone="UTC")

    def add_interval_job(
        self,
        *,
        job_id: str,
        callback: Callable[..., Any],
        hours: int,
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.scheduler.add_job(
            callback,
            trigger=IntervalTrigger(hours=hours),
            id=job_id,
            kwargs=kwargs or {},
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

    def add_minute_interval_job(
        self,
        *,
        job_id: str,
        callback: Callable[..., Any],
        minutes: int,
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.scheduler.add_job(
            callback,
            trigger=IntervalTrigger(minutes=minutes),
            id=job_id,
            kwargs=kwargs or {},
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

    def add_cron_job(
        self,
        *,
        job_id: str,
        callback: Callable[..., Any],
        hour: int,
        minute: int,
        day_of_week: str | None = None,
        timezone: str = "UTC",
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.scheduler.add_job(
            callback,
            trigger=CronTrigger(
                hour=hour,
                minute=minute,
                day_of_week=day_of_week,
                timezone=timezone,
            ),
            id=job_id,
            kwargs=kwargs or {},
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

    def remove_job(self, job_id: str) -> None:
        if self.scheduler.get_job(job_id) is not None:
            self.scheduler.remove_job(job_id)
