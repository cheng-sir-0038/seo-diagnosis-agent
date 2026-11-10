from __future__ import annotations

# ruff: noqa: E501
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from src.seo_agent.db.database import SeoAgentDatabase
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
        for key, name, mode, threshold, schedule in EVENT_TYPES:
            await self.database.execute(
                "INSERT OR IGNORE INTO notification_event_types(id, event_key, name, "
                "default_delivery_mode, supports_threshold, supports_schedule, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_id("net"), key, name, mode, threshold, schedule, "ACTIVE", now),
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
        subscription_id = _id("subscription")
        now = _now()
        await self.database.execute(
            "INSERT INTO notification_subscriptions(id, site_id, subscriber_actor_id, "
            "recipient_email, recipient_role, channel, enabled, is_default_approver, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'EMAIL', 1, ?, ?, ?)",
            (
                subscription_id,
                site_id,
                subscriber_actor_id,
                recipient_email,
                recipient_role,
                int(is_default_approver),
                now,
                now,
            ),
        )
        await self.seed_event_types()
        event_types = await self.database.fetch_all("SELECT * FROM notification_event_types")
        for event_type in event_types:
            mandatory = bool(
                is_default_approver and event_type["event_key"] == "approval_requested"
            )
            enabled = mandatory or event_type["event_key"] in {
                "approval_requested",
                "high_value_opportunity",
                "task_completed",
            }
            await self.database.execute(
                "INSERT INTO notification_preferences(id, subscription_id, event_type_id, enabled, "
                "mandatory, delivery_mode, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _id("preference"),
                    subscription_id,
                    event_type["id"],
                    int(enabled),
                    int(mandatory),
                    event_type["default_delivery_mode"],
                    now,
                    now,
                ),
            )
        row = await self.database.fetch_one(
            "SELECT * FROM notification_subscriptions WHERE id = ?", (subscription_id,)
        )
        assert row is not None
        return row

    async def list_subscriptions(self, site_id: str) -> list[dict[str, Any]]:
        return await self.database.fetch_all(
            "SELECT * FROM notification_subscriptions WHERE site_id = ? AND archived_at IS NULL "
            "ORDER BY created_at",
            (site_id,),
        )

    async def list_preferences(self, subscription_id: str) -> list[dict[str, Any]]:
        return await self.database.fetch_all(
            "SELECT p.*, t.event_key, t.name, t.description, t.supports_threshold, "
            "t.supports_schedule FROM notification_preferences p "
            "JOIN notification_event_types t ON t.id = p.event_type_id "
            "WHERE p.subscription_id = ? ORDER BY t.event_key",
            (subscription_id,),
        )

    async def get_preference(self, subscription_id: str, event_key: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT p.*, t.event_key FROM notification_preferences p "
            "JOIN notification_event_types t ON t.id = p.event_type_id "
            "WHERE p.subscription_id = ? AND t.event_key = ?",
            (subscription_id, event_key),
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
            "UPDATE notification_preferences SET enabled = ?, delivery_mode = COALESCE(?, delivery_mode), "
            "schedule_json = ?, threshold_json = ?, updated_at = ? WHERE id = ?",
            (
                int(enabled),
                delivery_mode,
                json.dumps(schedule or {}, ensure_ascii=False),
                json.dumps(threshold or {}, ensure_ascii=False),
                now,
                current["id"],
            ),
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
        existing = await self.database.fetch_one(
            "SELECT * FROM notification_events WHERE idempotency_key = ?", (idempotency_key,)
        )
        if existing is not None:
            return existing
        event_type = await self.database.fetch_one(
            "SELECT * FROM notification_event_types WHERE event_key = ?", (event_key,)
        )
        if event_type is None:
            raise KeyError(f"通知事件类型不存在: {event_key}")
        event_id = _id("notification")
        now = _now()
        await self.database.execute(
            "INSERT INTO notification_events(id, site_id, event_type_id, business_object_type, "
            "business_object_id, title, content_json, priority, idempotency_key, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                site_id,
                event_type["id"],
                business_object_type,
                business_object_id,
                title,
                json.dumps(content, ensure_ascii=False),
                priority,
                idempotency_key,
                now,
            ),
        )
        subscriptions = await self.database.fetch_all(
            "SELECT s.*, p.id AS preference_id FROM notification_subscriptions s "
            "JOIN notification_preferences p ON p.subscription_id = s.id "
            "WHERE s.site_id = ? AND s.enabled = 1 AND s.archived_at IS NULL "
            "AND p.event_type_id = ? AND p.enabled = 1",
            (site_id, event_type["id"]),
        )
        for subscription in subscriptions:
            delivery_key = f"{idempotency_key}:{subscription['id']}:email"
            await self.database.execute(
                "INSERT OR IGNORE INTO notification_deliveries(id, notification_event_id, "
                "subscription_id, channel_adapter, provider_adapter, recipient, status, "
                "idempotency_key, scheduled_at, created_at) VALUES (?, ?, ?, 'email', 'smtp', ?, "
                "'PENDING', ?, ?, ?)",
                (
                    _id("delivery"),
                    event_id,
                    subscription["id"],
                    subscription["recipient_email"],
                    delivery_key,
                    now,
                    now,
                ),
            )
        row = await self.database.fetch_one(
            "SELECT * FROM notification_events WHERE id = ?", (event_id,)
        )
        assert row is not None
        return row

    async def send_delivery(self, delivery_id: str) -> EmailSendResult:
        if self.provider is None:
            raise RuntimeError("邮件 Provider 未配置")
        row = await self.database.fetch_one(
            "SELECT d.*, e.title, e.content_json FROM notification_deliveries d "
            "JOIN notification_events e ON e.id = d.notification_event_id WHERE d.id = ?",
            (delivery_id,),
        )
        if row is None:
            raise KeyError(f"通知投递不存在: {delivery_id}")
        count = await self.database.fetch_value(
            "SELECT COUNT(*) FROM notification_attempts WHERE notification_delivery_id = ?",
            (delivery_id,),
        )
        attempt_no = int(count or 0) + 1
        attempt_id = _id("attempt")
        now = _now()
        await self.database.execute(
            "INSERT INTO notification_attempts(id, notification_delivery_id, attempt_no, provider, "
            "status, started_at, created_at) VALUES (?, ?, ?, 'smtp', 'RUNNING', ?, ?)",
            (attempt_id, delivery_id, attempt_no, now, now),
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
        await self.database.execute(
            "UPDATE notification_attempts SET status = ?, error_code = ?, error_message = ?, "
            "completed_at = ? WHERE id = ?",
            (result.status, result.error_code, result.error_message, completed, attempt_id),
        )
        await self.database.execute(
            "UPDATE notification_deliveries SET status = ?, provider_message_id = ?, sent_at = ?, "
            "failed_at = ? WHERE id = ?",
            (
                result.status,
                result.provider_message_id,
                completed if result.success else None,
                None if result.success else completed,
                delivery_id,
            ),
        )
        return result

    async def list_pending_deliveries(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self.database.fetch_all(
            "SELECT * FROM notification_deliveries WHERE status IN ('PENDING', 'FAILED') "
            "ORDER BY scheduled_at LIMIT ?",
            (limit,),
        )
