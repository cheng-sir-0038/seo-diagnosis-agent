from __future__ import annotations

from dataclasses import dataclass

from src.seo_agent.adapters.base import AdapterStatus
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.notifications.email import SmtpEmailProvider
from src.seo_agent.notifications.service import NotificationService


@dataclass(frozen=True)
class OutboxRunResult:
    sent: int = 0
    failed: int = 0
    exhausted: int = 0
    skipped_not_configured: int = 0


class NotificationOutboxWorker:
    def __init__(
        self,
        database: SeoAgentDatabase,
        notifications: NotificationService,
        provider: SmtpEmailProvider,
        *,
        max_attempts: int = 3,
    ) -> None:
        self.database = database
        self.notifications = notifications
        self.provider = provider
        self.max_attempts = max_attempts

    async def run_once(self, limit: int = 100) -> OutboxRunResult:
        deliveries = await self.notifications.list_pending_deliveries(limit)
        health = await self.provider.health_check()
        if health.status is not AdapterStatus.READY:
            return OutboxRunResult(skipped_not_configured=len(deliveries))
        sent = failed = exhausted = 0
        for delivery in deliveries:
            attempt_count = int(
                await self.database.fetch_value(
                    "SELECT COUNT(*) FROM notification_attempts WHERE notification_delivery_id = ?",
                    (delivery["id"],),
                )
                or 0
            )
            if attempt_count >= self.max_attempts:
                exhausted += 1
                continue
            result = await self.notifications.send_delivery(delivery["id"])
            if result.success:
                sent += 1
            else:
                failed += 1
        return OutboxRunResult(sent=sent, failed=failed, exhausted=exhausted)
