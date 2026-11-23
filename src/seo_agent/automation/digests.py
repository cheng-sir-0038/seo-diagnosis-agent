from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import (
    ApprovalActionRow,
    ExecutionTaskRow,
    OpportunityRow,
    WorkRecordRow,
)
from src.seo_agent.notifications.service import NotificationService


class DigestService:
    def __init__(self, database: SeoAgentDatabase, notifications: NotificationService) -> None:
        self.database = database
        self.notifications = notifications

    async def create_digest(
        self,
        *,
        site_id: str,
        period: str,
        period_key: str,
    ) -> dict[str, Any]:
        normalized = period.lower()
        if normalized not in {"daily", "weekly"}:
            raise ValueError("只支持 daily 或 weekly 摘要")
        cutoff = datetime.now(UTC) - timedelta(days=1 if normalized == "daily" else 7)
        records = await self.database.fetch_all(
            select(WorkRecordRow.__table__)
            .where(
                WorkRecordRow.site_id == site_id, WorkRecordRow.occurred_at >= cutoff.isoformat()
            )
            .order_by(WorkRecordRow.occurred_at.desc())
            .limit(200)
        )
        counts = {
            "opportunities": int(
                await self.database.fetch_value(
                    select(func.count())
                    .select_from(OpportunityRow)
                    .where(
                        OpportunityRow.site_id == site_id,
                        OpportunityRow.last_seen_at >= cutoff.isoformat(),
                    )
                )
                or 0
            ),
            "pending_approvals": int(
                await self.database.fetch_value(
                    select(func.count())
                    .select_from(ApprovalActionRow)
                    .where(
                        ApprovalActionRow.site_id == site_id,
                        ApprovalActionRow.status == "WAITING_FOR_APPROVAL",
                    )
                )
                or 0
            ),
            "active_tasks": int(
                await self.database.fetch_value(
                    select(func.count())
                    .select_from(ExecutionTaskRow)
                    .where(
                        ExecutionTaskRow.site_id == site_id,
                        ExecutionTaskRow.status.not_in(("COMPLETED", "CANCELLED")),
                        ExecutionTaskRow.archived_at.is_(None),
                    )
                )
                or 0
            ),
        }
        label = "日报" if normalized == "daily" else "周报"
        lines = [
            f"SEO 优化专员{label}",
            f"新近机会：{counts['opportunities']}",
            f"待审批：{counts['pending_approvals']}",
            f"进行中任务：{counts['active_tasks']}",
        ]
        if records:
            lines.append("最近工作：")
            lines.extend(f"- {record['title']}：{record['summary']}" for record in records[:10])
        else:
            lines.append("最近没有新的工作记录。")
        return await self.notifications.create_event(
            site_id=site_id,
            event_key=f"{normalized}_digest",
            business_object_type="site",
            business_object_id=site_id,
            title=f"SEO 优化专员{label} · {period_key}",
            content={"text": "\n".join(lines), "summary": "；".join(lines[1:4]), "counts": counts},
            priority="NORMAL",
            idempotency_key=f"digest:{site_id}:{normalized}:{period_key}",
        )
