from __future__ import annotations

# ruff: noqa: E501
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from src.seo_agent.db.database import SeoAgentDatabase


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class ApprovalService:
    def __init__(self, database: SeoAgentDatabase, *, reminder_hours: int = 24) -> None:
        self.database = database
        self.reminder_hours = reminder_hours

    async def create_action(
        self,
        *,
        site_id: str,
        action_type: str,
        risk_level: str,
        target_type: str,
        target_id: str,
        requested_payload: dict[str, Any],
        approver_actor_id: str,
        idempotency_key: str,
        requested_by_actor_id: str | None = None,
        requested_by_run_id: str | None = None,
    ) -> dict[str, Any]:
        existing = await self.database.fetch_one(
            "SELECT * FROM approval_actions WHERE idempotency_key = ?", (idempotency_key,)
        )
        if existing is not None:
            return existing
        now = _now()
        action_id = _id("approval")
        approver_id = _id("approver")
        await self.database.execute(
            "INSERT INTO approval_actions(id, site_id, action_type, risk_level, status, target_type, "
            "target_id, requested_payload_json, requested_by_actor_id, requested_by_run_id, "
            "idempotency_key, requested_at, next_remind_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'WAITING_FOR_APPROVAL', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action_id,
                site_id,
                action_type,
                risk_level,
                target_type,
                target_id,
                json.dumps(requested_payload, ensure_ascii=False),
                requested_by_actor_id,
                requested_by_run_id,
                idempotency_key,
                now.isoformat(),
                (now + timedelta(hours=self.reminder_hours)).isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        await self.database.execute(
            "INSERT INTO approval_action_approvers(id, approval_action_id, actor_id, approval_order, "
            "is_required, status, created_at) VALUES (?, ?, ?, 1, 1, 'WAITING', ?)",
            (approver_id, action_id, approver_actor_id, now.isoformat()),
        )
        return await self.get_action(action_id)

    async def get_action(self, action_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT * FROM approval_actions WHERE id = ?", (action_id,)
        )
        if row is None:
            raise KeyError(f"审批动作不存在: {action_id}")
        row["requested_payload"] = json.loads(row.pop("requested_payload_json"))
        return row

    async def list_pending(self, site_id: str | None = None) -> list[dict[str, Any]]:
        if site_id:
            return await self.database.fetch_all(
                "SELECT * FROM approval_actions WHERE site_id = ? AND status = "
                "'WAITING_FOR_APPROVAL' ORDER BY requested_at",
                (site_id,),
            )
        return await self.database.fetch_all(
            "SELECT * FROM approval_actions WHERE status = 'WAITING_FOR_APPROVAL' "
            "ORDER BY requested_at"
        )

    async def list_due_reminders(self, at: datetime | None = None) -> list[dict[str, Any]]:
        due = (at or _now()).isoformat()
        return await self.database.fetch_all(
            "SELECT * FROM approval_actions WHERE status = 'WAITING_FOR_APPROVAL' "
            "AND next_remind_at IS NOT NULL AND next_remind_at <= ? ORDER BY next_remind_at",
            (due,),
        )

    async def mark_reminded(self, action_id: str, at: datetime | None = None) -> None:
        now = at or _now()
        await self.database.execute(
            "UPDATE approval_actions SET last_reminded_at = ?, next_remind_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'WAITING_FOR_APPROVAL'",
            (
                now.isoformat(),
                (now + timedelta(hours=self.reminder_hours)).isoformat(),
                now.isoformat(),
                action_id,
            ),
        )

    async def decide(
        self,
        *,
        action_id: str,
        actor_id: str,
        decision: str,
        edited_payload: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        normalized = decision.upper()
        status_by_decision = {
            "APPROVE": "APPROVED",
            "EDIT_AND_APPROVE": "EDITED_AND_APPROVED",
            "REJECT": "REJECTED",
            "DECISION_CORRECTED": "WAITING_FOR_APPROVAL",
        }
        if normalized not in status_by_decision:
            raise ValueError(f"不支持的审批决定: {decision}")
        action = await self.get_action(action_id)
        approver = await self.database.fetch_one(
            "SELECT * FROM approval_action_approvers WHERE approval_action_id = ? AND actor_id = ?",
            (action_id, actor_id),
        )
        if approver is None:
            raise PermissionError("当前 Actor 不是该动作审批人")
        now = _now().isoformat()
        decision_id = _id("decision")
        await self.database.execute(
            "INSERT INTO approval_decisions(id, approval_action_approver_id, decision, "
            "original_payload_json, edited_payload_json, reason, decided_by_actor_id, "
            "decided_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id,
                approver["id"],
                normalized,
                json.dumps(action["requested_payload"], ensure_ascii=False),
                json.dumps(edited_payload, ensure_ascii=False)
                if edited_payload is not None
                else None,
                reason,
                actor_id,
                now,
                now,
            ),
        )
        target_status = status_by_decision[normalized]
        approver_status = (
            "APPROVED" if normalized in {"APPROVE", "EDIT_AND_APPROVE"} else normalized
        )
        await self.database.execute(
            "UPDATE approval_action_approvers SET status = ?, decided_at = ? WHERE id = ?",
            (approver_status, now, approver["id"]),
        )
        await self.database.execute(
            "UPDATE approval_actions SET status = ?, decided_at = ?, next_remind_at = NULL, "
            "updated_at = ? WHERE id = ?",
            (target_status, now, now, action_id),
        )
        row = await self.database.fetch_one(
            "SELECT * FROM approval_decisions WHERE id = ?", (decision_id,)
        )
        assert row is not None
        return row

    async def list_decisions(self, action_id: str) -> list[dict[str, Any]]:
        return await self.database.fetch_all(
            "SELECT d.* FROM approval_decisions d "
            "JOIN approval_action_approvers a ON a.id = d.approval_action_approver_id "
            "WHERE a.approval_action_id = ? ORDER BY d.created_at",
            (action_id,),
        )
