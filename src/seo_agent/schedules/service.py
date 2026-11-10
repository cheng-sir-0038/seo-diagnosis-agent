from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from src.seo_agent.db.database import SeoAgentDatabase


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
            "INSERT INTO work_schedules(id, site_id, schedule_type, name, frequency, weekday, "
            "execution_time, timezone, enabled, config_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (
                schedule_id,
                site_id,
                schedule_type,
                name,
                frequency,
                weekday,
                execution_time,
                timezone,
                json.dumps(config or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        row = await self.database.fetch_one(
            "SELECT * FROM work_schedules WHERE id = ?", (schedule_id,)
        )
        assert row is not None
        return row

    async def list_schedules(self, site_id: str) -> list[dict[str, Any]]:
        return await self.database.fetch_all(
            "SELECT * FROM work_schedules WHERE site_id = ? AND archived_at IS NULL "
            "ORDER BY created_at",
            (site_id,),
        )

    async def list_runs(self, schedule_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return await self.database.fetch_all(
            "SELECT * FROM schedule_runs WHERE schedule_id = ? ORDER BY created_at DESC LIMIT ?",
            (schedule_id, max(1, min(limit, 200))),
        )

    async def set_enabled(self, schedule_id: str, enabled: bool) -> dict[str, Any]:
        await self.database.execute(
            "UPDATE work_schedules SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(enabled), _now(), schedule_id),
        )
        row = await self.database.fetch_one(
            "SELECT * FROM work_schedules WHERE id = ?", (schedule_id,)
        )
        if row is None:
            raise KeyError(f"工作计划不存在: {schedule_id}")
        return row
