from __future__ import annotations

# ruff: noqa: E501
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

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
        if not self.database.started:
            raise RuntimeError("SeoAgentDatabase 尚未启动")

        # A separate connection keeps these writes atomic even when other requests use the shared one.
        async with aiosqlite.connect(self.database.path) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute("BEGIN IMMEDIATE")
            async with connection.execute(
                "SELECT * FROM approval_actions WHERE id = ?", (action_id,)
            ) as cursor:
                action = await cursor.fetchone()
            if action is None:
                raise KeyError(f"审批动作不存在: {action_id}")
            async with connection.execute(
                "SELECT * FROM approval_action_approvers "
                "WHERE approval_action_id = ? AND actor_id = ?", (action_id, actor_id)
            ) as cursor:
                approver = await cursor.fetchone()
            if approver is None:
                raise PermissionError("当前 Actor 不是该动作审批人")

            opportunity = None
            if (
                action["action_type"] == "ADD_OPPORTUNITY_TO_PRODUCTION"
                and action["target_type"] == "opportunity"
            ):
                async with connection.execute(
                    "SELECT * FROM opportunities WHERE id = ? AND site_id = ?",
                    (action["target_id"], action["site_id"]),
                ) as cursor:
                    opportunity = await cursor.fetchone()
                if opportunity is None:
                    raise KeyError("审批对应的机会不存在或不属于当前站点")
                expected = {
                    "WAITING_FOR_APPROVAL": "WAITING_FOR_DECISION",
                    "APPROVED": "APPROVED",
                    "EDITED_AND_APPROVED": "APPROVED",
                    "REJECTED": "REJECTED",
                }.get(action["status"])
                if opportunity["status"] != expected:
                    raise ValueError("审批动作与机会状态不一致，请刷新后重试")
                if action["status"] != "WAITING_FOR_APPROVAL" and normalized != "DECISION_CORRECTED":
                    async with connection.execute(
                        "SELECT * FROM approval_decisions WHERE approval_action_approver_id = ? "
                        "ORDER BY rowid DESC LIMIT 1", (approver["id"],)
                    ) as cursor:
                        previous = await cursor.fetchone()
                    previous_edit = (
                        json.loads(previous["edited_payload_json"])
                        if previous and previous["edited_payload_json"] is not None else None
                    )
                    if (
                        previous and previous["decision"] == normalized
                        and previous_edit == edited_payload and previous["reason"] == reason
                    ):
                        return dict(previous)
                    raise ValueError("审批已处理，请先更正决定")

            now_time = _now()
            now = now_time.isoformat()
            decision_id = _id("decision")
            await connection.execute(
                "INSERT INTO approval_decisions(id, approval_action_approver_id, decision, "
                "original_payload_json, edited_payload_json, reason, decided_by_actor_id, "
                "decided_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    decision_id, approver["id"], normalized, action["requested_payload_json"],
                    json.dumps(edited_payload, ensure_ascii=False)
                    if edited_payload is not None else None,
                    reason, actor_id, now, now,
                ),
            )
            target_status = status_by_decision[normalized]
            approver_status = (
                "WAITING" if normalized == "DECISION_CORRECTED"
                else "APPROVED" if normalized in {"APPROVE", "EDIT_AND_APPROVE"}
                else normalized
            )
            decided_at = None if normalized == "DECISION_CORRECTED" else now
            next_remind_at = (
                (now_time + timedelta(hours=self.reminder_hours)).isoformat()
                if normalized == "DECISION_CORRECTED" else None
            )
            await connection.execute(
                "UPDATE approval_action_approvers SET status = ?, decided_at = ? WHERE id = ?",
                (approver_status, decided_at, approver["id"]),
            )
            await connection.execute(
                "UPDATE approval_actions SET status = ?, decided_at = ?, next_remind_at = ?, "
                "updated_at = ? WHERE id = ?",
                (target_status, decided_at, next_remind_at, now, action_id),
            )
            if opportunity is not None:
                opportunity_status = {
                    "APPROVE": "APPROVED", "EDIT_AND_APPROVE": "APPROVED",
                    "REJECT": "REJECTED", "DECISION_CORRECTED": "WAITING_FOR_DECISION",
                }[normalized]
                if opportunity["status"] != opportunity_status:
                    await connection.execute(
                        "UPDATE opportunities SET status = ?, rejected_at = ?, "
                        "rejection_reason = ?, updated_at = ? WHERE id = ?",
                        (opportunity_status, now if normalized == "REJECT" else None,
                         reason if normalized == "REJECT" else None, now, opportunity["id"]),
                    )
                    await connection.execute(
                        "INSERT INTO opportunity_status_history(id, opportunity_id, from_status, "
                        "to_status, reason, policy_version, changed_by_actor_id, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (_id("oppstatus"), opportunity["id"], opportunity["status"],
                         opportunity_status, reason or "审批决定", opportunity["policy_version"],
                         actor_id, now),
                    )
            await connection.commit()
            async with connection.execute(
                "SELECT * FROM approval_decisions WHERE id = ?", (decision_id,)
            ) as cursor:
                row = await cursor.fetchone()
            assert row is not None
            return dict(row)

    async def list_decisions(self, action_id: str) -> list[dict[str, Any]]:
        return await self.database.fetch_all(
            "SELECT d.* FROM approval_decisions d "
            "JOIN approval_action_approvers a ON a.id = d.approval_action_approver_id "
            "WHERE a.approval_action_id = ? ORDER BY d.created_at",
            (action_id,),
        )
