from __future__ import annotations

# ruff: noqa: E501
from typing import Any

from src.seo_agent.db.database import SeoAgentDatabase


class WorkbenchService:
    def __init__(self, database: SeoAgentDatabase) -> None:
        self.database = database

    async def summary(self, site_id: str) -> dict[str, Any]:
        pending_approvals = await self.database.fetch_value(
            "SELECT COUNT(*) FROM approval_actions WHERE site_id = ? AND status = "
            "'WAITING_FOR_APPROVAL'",
            (site_id,),
        )
        high = await self.database.fetch_value(
            "SELECT COUNT(*) FROM opportunities WHERE site_id = ? AND priority_level = 'HIGH' "
            "AND archived_at IS NULL",
            (site_id,),
        )
        medium = await self.database.fetch_value(
            "SELECT COUNT(*) FROM opportunities WHERE site_id = ? AND priority_level = 'MEDIUM' "
            "AND archived_at IS NULL",
            (site_id,),
        )
        active_tasks = await self.database.fetch_value(
            "SELECT COUNT(*) FROM execution_tasks WHERE site_id = ? AND status NOT IN "
            "('COMPLETED', 'CANCELLED') AND archived_at IS NULL",
            (site_id,),
        )
        active_scans = await self.database.fetch_value(
            "SELECT COUNT(*) FROM schedule_runs r JOIN work_schedules s ON s.id = r.schedule_id "
            "WHERE s.site_id = ? AND r.status = 'RUNNING'",
            (site_id,),
        )
        monitoring = await self.database.fetch_value(
            "SELECT COUNT(*) FROM monitoring_plans p JOIN publish_records pr ON pr.id = p.publish_record_id "
            "JOIN execution_tasks t ON t.id = pr.task_id WHERE t.site_id = ? AND p.status NOT IN "
            "('COMPLETED', 'INDEXING_INCONCLUSIVE')",
            (site_id,),
        )
        recent_records = await self.database.fetch_all(
            "SELECT * FROM work_records WHERE site_id = ? ORDER BY occurred_at DESC LIMIT 20",
            (site_id,),
        )
        return {
            "pending_approvals": int(pending_approvals or 0),
            "high_value_opportunities": int(high or 0),
            "medium_opportunities": int(medium or 0),
            "active_tasks": int(active_tasks or 0),
            "active_scan_runs": int(active_scans or 0),
            "active_monitoring_plans": int(monitoring or 0),
            "recent_work_records": recent_records,
        }
