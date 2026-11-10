from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from src.seo_agent.db.database import SeoAgentDatabase
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
            "SELECT * FROM work_records WHERE site_id = ? AND occurred_at >= ? "
            "ORDER BY occurred_at DESC LIMIT 200",
            (site_id, cutoff.isoformat()),
        )
        counts = {
            "opportunities": int(
                await self.database.fetch_value(
                    "SELECT COUNT(*) FROM opportunities WHERE site_id = ? AND last_seen_at >= ?",
                    (site_id, cutoff.isoformat()),
                )
                or 0
            ),
            "pending_approvals": int(
                await self.database.fetch_value(
                    "SELECT COUNT(*) FROM approval_actions WHERE site_id = ? AND status = "
                    "'WAITING_FOR_APPROVAL'",
                    (site_id,),
                )
                or 0
            ),
            "active_tasks": int(
                await self.database.fetch_value(
                    "SELECT COUNT(*) FROM execution_tasks WHERE site_id = ? AND status NOT IN "
                    "('COMPLETED', 'CANCELLED') AND archived_at IS NULL",
                    (site_id,),
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
