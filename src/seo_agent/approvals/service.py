from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import TABLES

actions = TABLES["approval_actions"]
approvers = TABLES["approval_action_approvers"]
decisions = TABLES["approval_decisions"]
opportunities = TABLES["opportunities"]
history = TABLES["opportunity_status_history"]

_PENDING = "WAITING_FOR_APPROVAL"
_PRODUCTION = "ADD_OPPORTUNITY_TO_PRODUCTION"
_DECISIONS = {
    "APPROVE": "APPROVED",
    "EDIT_AND_APPROVE": "EDITED_AND_APPROVED",
    "REJECT": "REJECTED",
    "DECISION_CORRECTED": _PENDING,
}


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _after(previous: str | None) -> datetime:
    now = _now()
    if previous is None:
        return now
    last = datetime.fromisoformat(previous)
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return max(now, last + timedelta(microseconds=1))


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
        async with self.database.transaction() as tx:
            previous = None
            if action_type == _PRODUCTION and target_type == "opportunity":
                # Serialise new approvals with decisions and task creation for this opportunity.
                await tx.fetch_value(select(opportunities.c.id).where(
                    opportunities.c.site_id == site_id, opportunities.c.id == target_id,
                ).with_for_update())
                previous = await tx.fetch_value(select(func.max(actions.c.requested_at)).where(
                    actions.c.site_id == site_id, actions.c.target_type == target_type,
                    actions.c.target_id == target_id, actions.c.action_type == action_type,
                ))
            now = _after(previous)
            action_id = _id("approval")
            await tx.execute(pg_insert(actions).values(
                id=action_id, site_id=site_id, action_type=action_type, risk_level=risk_level,
                status=_PENDING, target_type=target_type, target_id=target_id,
                requested_payload_json=json.dumps(requested_payload, ensure_ascii=False),
                requested_by_actor_id=requested_by_actor_id,
                requested_by_run_id=requested_by_run_id,
                idempotency_key=idempotency_key, requested_at=now.isoformat(),
                next_remind_at=(now + timedelta(hours=self.reminder_hours)).isoformat(),
                created_at=now.isoformat(), updated_at=now.isoformat(),
            ).on_conflict_do_nothing(index_elements=[actions.c.idempotency_key]))
            row = await tx.fetch_one(select(actions).where(
                actions.c.idempotency_key == idempotency_key,
            ))
            assert row is not None
            if (row["site_id"], row["target_type"], row["target_id"], row["action_type"]) != (
                site_id, target_type, target_id, action_type,
            ):
                raise ValueError("审批幂等键已用于其他申请")
            if row["id"] == action_id:
                await tx.execute(insert(approvers).values(
                    id=_id("approver"), approval_action_id=action_id, actor_id=approver_actor_id,
                    approval_order=1, is_required=1, status="WAITING", created_at=now.isoformat(),
                ))
            row["requested_payload"] = json.loads(row.pop("requested_payload_json"))
            return row

    async def get_action(self, action_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(select(actions).where(actions.c.id == action_id))
        if row is None:
            raise KeyError(f"审批动作不存在: {action_id}")
        row["requested_payload"] = json.loads(row.pop("requested_payload_json"))
        return row

    async def list_pending(self, site_id: str | None = None) -> list[dict[str, Any]]:
        query = select(actions).where(actions.c.status == _PENDING)
        if site_id:
            query = query.where(actions.c.site_id == site_id)
        return await self.database.fetch_all(query.order_by(actions.c.requested_at, actions.c.id))

    async def list_due_reminders(self, at: datetime | None = None) -> list[dict[str, Any]]:
        return await self.database.fetch_all(select(actions).where(
            actions.c.status == _PENDING, actions.c.next_remind_at <= (at or _now()).isoformat(),
        ).order_by(actions.c.next_remind_at, actions.c.id))

    async def mark_reminded(self, action_id: str, at: datetime | None = None) -> None:
        now = at or _now()
        await self.database.execute(update(actions).where(
            actions.c.id == action_id, actions.c.status == _PENDING,
        ).values(last_reminded_at=now.isoformat(), updated_at=now.isoformat(),
                 next_remind_at=(now + timedelta(hours=self.reminder_hours)).isoformat()))

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
        if normalized not in _DECISIONS:
            raise ValueError(f"不支持的审批决定: {decision}")
        async with self.database.transaction() as tx:
            lookup = select(actions).where(actions.c.id == action_id)
            snapshot = await tx.fetch_one(lookup)
            if snapshot is None:
                raise KeyError(f"审批动作不存在: {action_id}")
            applies_to_opportunity = (
                snapshot["action_type"] == _PRODUCTION and snapshot["target_type"] == "opportunity"
            )
            opportunity = None
            if applies_to_opportunity:
                opportunity = await tx.fetch_one(select(opportunities).where(
                    opportunities.c.id == snapshot["target_id"],
                    opportunities.c.site_id == snapshot["site_id"],
                ).with_for_update())
            action = await tx.fetch_one(lookup.with_for_update())
            if action is None:
                raise KeyError(f"审批动作不存在: {action_id}")
            approver = await tx.fetch_one(select(approvers).where(
                approvers.c.approval_action_id == action_id, approvers.c.actor_id == actor_id,
            ).with_for_update())
            if approver is None:
                raise PermissionError("当前 Actor 不是该动作审批人")

            previous = await tx.fetch_one(select(decisions).where(
                decisions.c.approval_action_approver_id == approver["id"],
            ).order_by(decisions.c.decided_at.desc(), decisions.c.id.desc()).limit(1))
            if applies_to_opportunity:
                if opportunity is None:
                    raise KeyError("审批对应的机会不存在或不属于当前站点")
                expected = {
                    _PENDING: {"WAITING_FOR_DECISION", "OPPORTUNITY_POOL", "OBSERVED"},
                    "APPROVED": {"APPROVED"}, "EDITED_AND_APPROVED": {"APPROVED"},
                    "REJECTED": {"REJECTED"},
                }.get(action["status"], set())
                if opportunity["status"] not in expected:
                    raise ValueError("审批动作与机会状态不一致，请刷新后重试")
                if action["status"] != _PENDING and normalized != "DECISION_CORRECTED":
                    if previous and previous["decision"] == normalized and (
                        json.loads(previous["edited_payload_json"] or "null") == edited_payload
                        and previous["reason"] == reason
                    ):
                        return previous
                    raise ValueError("审批已处理，请先更正决定")

            now_time = _after(previous["decided_at"] if previous else None)
            now = now_time.isoformat()
            decision_id = _id("decision")
            await tx.execute(insert(decisions).values(
                id=decision_id, approval_action_approver_id=approver["id"], decision=normalized,
                original_payload_json=action["requested_payload_json"],
                edited_payload_json=(json.dumps(edited_payload, ensure_ascii=False)
                                     if edited_payload is not None else None),
                reason=reason, decided_by_actor_id=actor_id, decided_at=now, created_at=now,
            ))
            target_status = _DECISIONS[normalized]
            reopened = normalized == "DECISION_CORRECTED"
            approver_status = {
                "DECISION_CORRECTED": "WAITING",
                "APPROVE": "APPROVED", "EDIT_AND_APPROVE": "APPROVED",
            }.get(normalized, normalized)
            await tx.execute(update(approvers).where(approvers.c.id == approver["id"]).values(
                status=approver_status, decided_at=None if reopened else now,
            ))
            await tx.execute(update(actions).where(actions.c.id == action_id).values(
                status=target_status, decided_at=None if reopened else now, updated_at=now,
                next_remind_at=((now_time + timedelta(hours=self.reminder_hours)).isoformat()
                                if reopened else None),
            ))
            if opportunity is not None:
                new_status = {
                    "APPROVE": "APPROVED", "EDIT_AND_APPROVE": "APPROVED",
                    "REJECT": "REJECTED", "DECISION_CORRECTED": "WAITING_FOR_DECISION",
                }[normalized]
                if opportunity["status"] != new_status:
                    await tx.execute(update(opportunities).where(
                        opportunities.c.id == opportunity["id"],
                    ).values(status=new_status, rejected_at=now if normalized == "REJECT" else None,
                             rejection_reason=reason if normalized == "REJECT" else None,
                             updated_at=now))
                    await tx.execute(insert(history).values(
                        id=_id("oppstatus"), opportunity_id=opportunity["id"],
                        from_status=opportunity["status"], to_status=new_status,
                        reason=reason or "审批决定", policy_version=opportunity["policy_version"],
                        changed_by_actor_id=actor_id, created_at=now,
                    ))
            row = await tx.fetch_one(select(decisions).where(decisions.c.id == decision_id))
            assert row is not None
            return row

    async def list_decisions(self, action_id: str) -> list[dict[str, Any]]:
        return await self.database.fetch_all(select(decisions).join(
            approvers, approvers.c.id == decisions.c.approval_action_approver_id,
        ).where(approvers.c.approval_action_id == action_id)
          .order_by(decisions.c.created_at, decisions.c.id))
