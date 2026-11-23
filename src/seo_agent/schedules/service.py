from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select, update

from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import (
    ScheduleRunRow,
    WorkScheduleRow,
)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class WorkScheduleService:
    def __init__(self, database: SeoAgentDatabase) -> None:
        self.database = database

    async def create_schedule(
        self,
        *,
        site_id: str,
        schedule_type: str,
        name: str,
        frequency: str,
        timezone: str,
        execution_time: str | None = None,
        weekday: int | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        schedule_id = _id("schedule")
        now = _now()
        await self.database.execute(
            insert(WorkScheduleRow).values(
                id=schedule_id,
                site_id=site_id,
                schedule_type=schedule_type,
                name=name,
                frequency=frequency,
                weekday=weekday,
                execution_time=execution_time,
                timezone=timezone,
                enabled=1,
                config_json=json.dumps(config or {}, ensure_ascii=False),
                created_at=now,
                updated_at=now,
            )
        )
        row = await self.database.fetch_one(
            select(WorkScheduleRow.__table__).where(WorkScheduleRow.id == schedule_id)
        )
        assert row is not None
        return row

    async def list_schedules(self, site_id: str) -> list[dict[str, Any]]:
        return await self.database.fetch_all(
            select(WorkScheduleRow.__table__)
            .where(WorkScheduleRow.site_id == site_id, WorkScheduleRow.archived_at.is_(None))
            .order_by(WorkScheduleRow.created_at)
        )

    async def list_runs(self, schedule_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return await self.database.fetch_all(
            select(ScheduleRunRow.__table__)
            .where(ScheduleRunRow.schedule_id == schedule_id)
            .order_by(ScheduleRunRow.created_at.desc())
            .limit(max(1, min(limit, 200)))
        )

    async def set_enabled(self, schedule_id: str, enabled: bool) -> dict[str, Any]:
        await self.database.execute(
            update(WorkScheduleRow)
            .where(WorkScheduleRow.id == schedule_id)
            .values(
                enabled=int(enabled),
                updated_at=_now(),
            )
        )
        row = await self.database.fetch_one(
            select(WorkScheduleRow.__table__).where(WorkScheduleRow.id == schedule_id)
        )
        if row is None:
            raise KeyError(f"工作计划不存在: {schedule_id}")
        return row
