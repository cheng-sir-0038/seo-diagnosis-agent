from __future__ import annotations

from src.seo_agent.approvals.service import ApprovalService
from src.seo_agent.notifications.service import NotificationService


class ApprovalReminderCoordinator:
    def __init__(
        self,
        approvals: ApprovalService,
        notifications: NotificationService,
    ) -> None:
        self.approvals = approvals
        self.notifications = notifications

    async def run_once(self) -> int:
        actions = await self.approvals.list_due_reminders()
        created = 0
        for action in actions:
            await self.notifications.create_event(
                site_id=action["site_id"],
                event_key="approval_requested",
                business_object_type="approval_action",
                business_object_id=action["id"],
                title="SEO 优化专员有待审批动作",
                content={"summary": f"动作 {action['action_type']} 等待处理"},
                priority="HIGH",
                idempotency_key=f"approval-reminder:{action['id']}:{action['next_remind_at']}",
            )
            await self.approvals.mark_reminded(action["id"])
            created += 1
        return created
