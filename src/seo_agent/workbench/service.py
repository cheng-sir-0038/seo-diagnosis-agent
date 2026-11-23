from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import (
    ApprovalActionRow,
    ExecutionTaskRow,
    MonitoringPlanRow,
    OpportunityRow,
    PublishRecordRow,
    ScheduleRunRow,
    WorkRecordRow,
    WorkScheduleRow,
)


class WorkbenchService:
    def __init__(self, database: SeoAgentDatabase) -> None:
        self.database = database

    async def summary(self, site_id: str) -> dict[str, Any]:
        pending = select(func.count()).select_from(ApprovalActionRow).where(
            ApprovalActionRow.site_id == site_id,
            ApprovalActionRow.status == "WAITING_FOR_APPROVAL",
        )
        active_tasks = select(func.count()).select_from(ExecutionTaskRow).where(
            ExecutionTaskRow.site_id == site_id,
            ExecutionTaskRow.status.not_in(("COMPLETED", "CANCELLED")),
            ExecutionTaskRow.archived_at.is_(None),
        )
        active_scans = (
            select(func.count()).select_from(ScheduleRunRow)
            .join(WorkScheduleRow, WorkScheduleRow.id == ScheduleRunRow.schedule_id)
            .where(WorkScheduleRow.site_id == site_id, ScheduleRunRow.status == "RUNNING")
        )
        monitoring = (
            select(func.count()).select_from(MonitoringPlanRow)
            .join(PublishRecordRow, PublishRecordRow.id == MonitoringPlanRow.publish_record_id)
            .join(ExecutionTaskRow, ExecutionTaskRow.id == PublishRecordRow.task_id)
            .where(
                ExecutionTaskRow.site_id == site_id,
                MonitoringPlanRow.status.not_in(("COMPLETED", "INDEXING_INCONCLUSIVE")),
            )
        )
        counters = await self.database.fetch_one(
            select(
                pending.scalar_subquery().label("pending_approvals"),
                func.count().filter(OpportunityRow.priority_level == "HIGH")
                .label("high_value_opportunities"),
                func.count().filter(OpportunityRow.priority_level == "MEDIUM")
                .label("medium_opportunities"),
                active_tasks.scalar_subquery().label("active_tasks"),
                active_scans.scalar_subquery().label("active_scan_runs"),
                monitoring.scalar_subquery().label("active_monitoring_plans"),
            ).select_from(OpportunityRow).where(
                OpportunityRow.site_id == site_id, OpportunityRow.archived_at.is_(None),
            )
        )
        assert counters is not None
        recent = await self.database.fetch_all(
            select(WorkRecordRow.__table__).where(WorkRecordRow.site_id == site_id)
            .order_by(WorkRecordRow.occurred_at.desc()).limit(20)
        )
        return {**counters, "recent_work_records": recent}
