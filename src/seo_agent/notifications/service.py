from __future__ import annotations

# ruff: noqa: E501
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import (
    NotificationAttemptRow,
    NotificationDeliveryRow,
    NotificationEventRow,
    NotificationEventTypeRow,
    NotificationPreferenceRow,
    NotificationSubscriptionRow,
)
from src.seo_agent.notifications.email import EmailMessage, EmailSendResult, SmtpEmailProvider


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


EVENT_TYPES = (
    ("approval_requested", "待审批动作", "IMMEDIATE", 0, 0),
    ("high_value_opportunity", "高价值机会", "IMMEDIATE", 0, 0),
    ("task_progressed", "任务进展", "IMMEDIATE", 0, 0),
    ("task_completed", "任务完成", "IMMEDIATE", 0, 0),
    ("ranking_changed", "重大排名变化", "IMMEDIATE", 1, 0),
    ("daily_digest", "日报", "SCHEDULED", 0, 1),
    ("weekly_digest", "周报", "SCHEDULED", 0, 1),
    ("monthly_digest", "月报", "SCHEDULED", 0, 1),
)


class NotificationService:
    def __init__(
        self, database: SeoAgentDatabase, provider: SmtpEmailProvider | None = None
    ) -> None:
        self.database = database
        self.provider = provider

    async def seed_event_types(self) -> None:
        now = _now()
        values = [
            {
                "id": _id("net"),
                "event_key": key,
                "name": name,
                "default_delivery_mode": mode,
                "supports_threshold": threshold,
                "supports_schedule": schedule,
                "status": "ACTIVE",
                "created_at": now,
            }
            for key, name, mode, threshold, schedule in EVENT_TYPES
        ]
        await self.database.execute(
            pg_insert(NotificationEventTypeRow)
            .values(values)
            .on_conflict_do_nothing(index_elements=[NotificationEventTypeRow.event_key])
        )

    async def create_subscription(
        self,
        *,
        site_id: str,
        subscriber_actor_id: str | None,
        recipient_email: str,
        recipient_role: str,
        is_default_approver: bool = False,
    ) -> dict[str, Any]:
        await self.seed_event_types()
        async with self.database.transaction() as db:
            subscription_id = _id("subscription")
            now = _now()
            await db.execute(
                insert(NotificationSubscriptionRow).values(
                    id=subscription_id,
                    site_id=site_id,
                    subscriber_actor_id=subscriber_actor_id,
                    recipient_email=recipient_email,
                    recipient_role=recipient_role,
                    channel="EMAIL",
                    enabled=1,
                    is_default_approver=int(is_default_approver),
                    created_at=now,
                    updated_at=now,
                )
            )
            event_types = await db.fetch_all(select(NotificationEventTypeRow.__table__))
            for event_type in event_types:
                mandatory = bool(
                    is_default_approver and event_type["event_key"] == "approval_requested"
                )
                enabled = mandatory or event_type["event_key"] in {
                    "approval_requested",
                    "high_value_opportunity",
                    "task_completed",
                }
                await db.execute(
                    insert(NotificationPreferenceRow).values(
                        id=_id("preference"),
                        subscription_id=subscription_id,
                        event_type_id=event_type["id"],
                        enabled=int(enabled),
                        mandatory=int(mandatory),
                        delivery_mode=event_type["default_delivery_mode"],
                        created_at=now,
                        updated_at=now,
                    )
                )
            row = await db.fetch_one(
                select(NotificationSubscriptionRow.__table__).where(
                    NotificationSubscriptionRow.id == subscription_id
                )
            )
            assert row is not None
            return row

    async def list_subscriptions(self, site_id: str) -> list[dict[str, Any]]:
        return await self.database.fetch_all(
            select(NotificationSubscriptionRow.__table__)
            .where(
                NotificationSubscriptionRow.site_id == site_id,
                NotificationSubscriptionRow.archived_at.is_(None),
            )
            .order_by(NotificationSubscriptionRow.created_at)
        )

    async def list_preferences(self, subscription_id: str) -> list[dict[str, Any]]:
        return await self.database.fetch_all(
            select(
                NotificationPreferenceRow.__table__,
                NotificationEventTypeRow.event_key,
                NotificationEventTypeRow.name,
                NotificationEventTypeRow.description,
                NotificationEventTypeRow.supports_threshold,
                NotificationEventTypeRow.supports_schedule,
            )
            .join(
                NotificationEventTypeRow,
                NotificationEventTypeRow.id == NotificationPreferenceRow.event_type_id,
            )
            .where(NotificationPreferenceRow.subscription_id == subscription_id)
            .order_by(NotificationEventTypeRow.event_key)
        )

    async def get_preference(self, subscription_id: str, event_key: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            select(NotificationPreferenceRow.__table__, NotificationEventTypeRow.event_key)
            .join(
                NotificationEventTypeRow,
                NotificationEventTypeRow.id == NotificationPreferenceRow.event_type_id,
            )
            .where(
                NotificationPreferenceRow.subscription_id == subscription_id,
                NotificationEventTypeRow.event_key == event_key,
            )
        )
        if row is None:
            raise KeyError(f"订阅偏好不存在: {subscription_id}/{event_key}")
        return row

    async def set_preference(
        self,
        subscription_id: str,
        event_key: str,
        *,
        enabled: bool,
        delivery_mode: str | None = None,
        schedule: dict[str, Any] | None = None,
        threshold: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = await self.get_preference(subscription_id, event_key)
        if current["mandatory"] and not enabled:
            raise ValueError("默认审批人的待审批通知必须强制开启")
        now = _now()
        await self.database.execute(
            update(NotificationPreferenceRow)
            .where(NotificationPreferenceRow.id == current["id"])
            .values(
                enabled=int(enabled),
                delivery_mode=func.coalesce(delivery_mode, NotificationPreferenceRow.delivery_mode),
                schedule_json=json.dumps(schedule or {}, ensure_ascii=False),
                threshold_json=json.dumps(threshold or {}, ensure_ascii=False),
                updated_at=now,
            )
        )
        return await self.get_preference(subscription_id, event_key)

    async def create_event(
        self,
        *,
        site_id: str,
        event_key: str,
        business_object_type: str,
        business_object_id: str,
        title: str,
        content: dict[str, Any],
        priority: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        async with self.database.transaction() as db:
            event_type = await db.fetch_one(
                select(NotificationEventTypeRow.__table__).where(
                    NotificationEventTypeRow.event_key == event_key
                )
            )
            if event_type is None:
                raise KeyError(f"通知事件类型不存在: {event_key}")
            event_id = _id("notification")
            now = _now()
            recorded = await db.fetch_one(
                pg_insert(NotificationEventRow)
                .values(
                    id=event_id,
                    site_id=site_id,
                    event_type_id=event_type["id"],
                    business_object_type=business_object_type,
                    business_object_id=business_object_id,
                    title=title,
                    content_json=json.dumps(content, ensure_ascii=False),
                    priority=priority,
                    idempotency_key=idempotency_key,
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=[NotificationEventRow.idempotency_key])
                .returning(*NotificationEventRow.__table__.c)
            )
            if recorded is None:
                existing = await db.fetch_one(
                    select(NotificationEventRow.__table__).where(
                        NotificationEventRow.idempotency_key == idempotency_key
                    )
                )
                if existing is None or (
                    existing["site_id"],
                    existing["business_object_type"],
                    existing["business_object_id"],
                    existing["event_type_id"],
                ) != (site_id, business_object_type, business_object_id, event_type["id"]):
                    raise ValueError("通知幂等键已用于其他业务事件")
                return existing
            subscriptions = await db.fetch_all(
                select(
                    NotificationSubscriptionRow.__table__,
                    NotificationPreferenceRow.id.label("preference_id"),
                )
                .join(
                    NotificationPreferenceRow,
                    NotificationPreferenceRow.subscription_id == NotificationSubscriptionRow.id,
                )
                .where(
                    NotificationSubscriptionRow.site_id == site_id,
                    NotificationSubscriptionRow.enabled == 1,
                    NotificationSubscriptionRow.archived_at.is_(None),
                    NotificationPreferenceRow.event_type_id == event_type["id"],
                    NotificationPreferenceRow.enabled == 1,
                )
            )
            for subscription in subscriptions:
                delivery_key = f"{idempotency_key}:{subscription['id']}:email"
                await db.execute(
                    pg_insert(NotificationDeliveryRow)
                    .values(
                        id=_id("delivery"),
                        notification_event_id=event_id,
                        subscription_id=subscription["id"],
                        channel_adapter="email",
                        provider_adapter="smtp",
                        recipient=subscription["recipient_email"],
                        status="PENDING",
                        idempotency_key=delivery_key,
                        scheduled_at=now,
                        created_at=now,
                    )
                    .on_conflict_do_nothing()
                )
            return recorded

    async def send_delivery(
        self, delivery_id: str, *, max_attempts: int | None = None
    ) -> EmailSendResult:
        async with self.database.transaction() as db:
            if self.provider is None:
                raise RuntimeError("邮件 Provider 未配置")
            row = await db.fetch_one(
                select(
                    NotificationDeliveryRow.__table__,
                    NotificationEventRow.title,
                    NotificationEventRow.content_json,
                )
                .join(
                    NotificationEventRow,
                    NotificationEventRow.id == NotificationDeliveryRow.notification_event_id,
                )
                .where(NotificationDeliveryRow.id == delivery_id)
                .with_for_update(of=NotificationDeliveryRow)
            )
            if row is None:
                raise KeyError(f"通知投递不存在: {delivery_id}")
            if row["status"] == "SENT":
                return EmailSendResult(
                    success=True,
                    provider="smtp",
                    status="SENT",
                    provider_message_id=row["provider_message_id"],
                )
            count = await db.fetch_value(
                select(func.count())
                .select_from(NotificationAttemptRow)
                .where(NotificationAttemptRow.notification_delivery_id == delivery_id)
            )
            attempt_no = int(count or 0) + 1
            if max_attempts is not None and attempt_no > max_attempts:
                return EmailSendResult(success=False, provider="smtp", status="EXHAUSTED")
            attempt_id = _id("attempt")
            now = _now()
            await db.execute(
                insert(NotificationAttemptRow).values(
                    id=attempt_id,
                    notification_delivery_id=delivery_id,
                    attempt_no=attempt_no,
                    provider="smtp",
                    status="RUNNING",
                    started_at=now,
                    created_at=now,
                )
            )
            content = json.loads(row["content_json"])
            message = EmailMessage(
                message_id=row["notification_event_id"],
                to=[row["recipient"]],
                subject=row["title"],
                text_content=str(content.get("text") or content.get("summary") or row["title"]),
                html_content=content.get("html"),
                idempotency_key=row["idempotency_key"],
            )
            result = await self.provider.send(message)
            completed = _now()
            await db.execute(
                update(NotificationAttemptRow)
                .where(NotificationAttemptRow.id == attempt_id)
                .values(
                    status=result.status,
                    error_code=result.error_code,
                    error_message=result.error_message,
                    completed_at=completed,
                )
            )
            await db.execute(
                update(NotificationDeliveryRow)
                .where(NotificationDeliveryRow.id == delivery_id)
                .values(
                    status=result.status,
                    provider_message_id=result.provider_message_id,
                    sent_at=completed if result.success else None,
                    failed_at=None if result.success else completed,
                )
            )
            return result

    async def list_pending_deliveries(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self.database.fetch_all(
            select(NotificationDeliveryRow.__table__)
            .where(NotificationDeliveryRow.status.in_(("PENDING", "FAILED")))
            .order_by(NotificationDeliveryRow.scheduled_at)
            .limit(limit)
        )
