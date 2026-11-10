from __future__ import annotations

import json
import uuid

# ruff: noqa: E501
from datetime import UTC, datetime
from typing import Any

from src.observability.client import ObservabilityClient
from src.observability.context import get_current_context
from src.seo_agent.approvals.policy import ActionPolicy
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.monitoring.service import MonitoringService
from src.seo_agent.publishing.html_site import (
    HtmlPublishRequest,
    HtmlSitePublishAdapter,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class PublishingService:
    def __init__(
        self,
        database: SeoAgentDatabase,
        adapter: HtmlSitePublishAdapter,
        monitoring: MonitoringService,
        observability: ObservabilityClient | None = None,
        action_policy: ActionPolicy | None = None,
    ) -> None:
        self.database = database
        self.adapter = adapter
        self.monitoring = monitoring
        self.observability = observability
        self.action_policy = action_policy or ActionPolicy()

    async def publish_delivery(
        self,
        *,
        task_id: str,
        delivery_id: str,
        slug: str,
        html_content: str,
        submitted_by_actor_id: str,
        idempotency_key: str,
        target_environment: str = "production",
        approval_action_id: str | None = None,
    ) -> dict[str, Any]:
        arguments = {
            "task_id": task_id,
            "delivery_id": delivery_id,
            "slug": slug,
            "target_environment": target_environment,
            "approval_action_id": approval_action_id,
        }
        if self.observability is None:
            return await self._publish_delivery_core(
                task_id=task_id,
                delivery_id=delivery_id,
                slug=slug,
                html_content=html_content,
                submitted_by_actor_id=submitted_by_actor_id,
                idempotency_key=idempotency_key,
                target_environment=target_environment,
                approval_action_id=approval_action_id,
            )
        parent = get_current_context()
        async with self.observability.trace(
            "seo.publish",
            user_id=submitted_by_actor_id,
            input_data=arguments,
        ) as trace:
            if parent and parent.trace_id and parent.trace_id != trace.trace_id:
                await self.observability.link_traces(
                    source_trace_id=parent.trace_id,
                    target_trace_id=trace.trace_id,
                    relation_type="CHILD_PROCESS",
                    attributes={"task_id": task_id, "delivery_id": delivery_id},
                )
            async with trace.run(
                trigger_type="api",
                workflow_name="publish_delivery",
                metadata=arguments,
            ):
                return await self._publish_delivery_core(
                    task_id=task_id,
                    delivery_id=delivery_id,
                    slug=slug,
                    html_content=html_content,
                    submitted_by_actor_id=submitted_by_actor_id,
                    idempotency_key=idempotency_key,
                    target_environment=target_environment,
                    approval_action_id=approval_action_id,
                    source_trace_id=trace.trace_id,
                )

    async def _publish_delivery_core(
        self,
        *,
        task_id: str,
        delivery_id: str,
        slug: str,
        html_content: str,
        submitted_by_actor_id: str,
        idempotency_key: str,
        target_environment: str = "production",
        source_trace_id: str | None = None,
        approval_action_id: str | None = None,
    ) -> dict[str, Any]:
        existing = await self.database.fetch_one(
            "SELECT * FROM publish_records WHERE idempotency_key = ?", (idempotency_key,)
        )
        if existing is not None:
            return existing
        delivery = await self.database.fetch_one(
            "SELECT * FROM deliveries WHERE id = ? AND task_id = ?", (delivery_id, task_id)
        )
        if delivery is None:
            raise KeyError(f"交付不存在或不属于任务: {delivery_id}")
        if delivery["status"] != "ACCEPTED":
            raise ValueError("只有验收通过的交付才能发布")
        try:
            content = json.loads(delivery["structured_content_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("验收通过的交付内容无效") from exc
        if (
            not isinstance(content, dict)
            or not isinstance(content.get("html"), str)
            or content["html"] != html_content
        ):
            raise ValueError("发布 HTML 必须与验收通过的交付内容一致")
        policy = self.action_policy.evaluate(
            action_type="PUBLISH_CONTENT",
            target_environment=target_environment,
        )
        if policy.requires_approval:
            await self._validate_publish_approval(
                approval_action_id=approval_action_id,
                delivery_id=delivery_id,
            )
        result = await self.adapter.publish(
            HtmlPublishRequest(
                slug=slug,
                html_content=html_content,
                idempotency_key=idempotency_key,
            )
        )
        if not result.success:
            raise RuntimeError(result.error_message or "HTML 发布失败")
        publish_id = _id("publish")
        now = _now()
        await self.database.execute(
            "INSERT INTO publish_records(id, task_id, delivery_id, publisher_adapter, status, "
            "target_environment, candidate_url, published_at, submitted_by_actor_id, "
            "idempotency_key, approval_action_id, source_trace_id, created_at, updated_at) "
            "VALUES (?, ?, ?, 'html_site', 'WAITING_CONFIRMATION', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                publish_id,
                task_id,
                delivery_id,
                target_environment,
                result.published_url,
                now,
                submitted_by_actor_id,
                idempotency_key,
                approval_action_id,
                source_trace_id,
                now,
                now,
            ),
        )
        await self._set_task_status(task_id, "WAITING_FOR_PUBLISH", submitted_by_actor_id)
        row = await self.database.fetch_one(
            "SELECT * FROM publish_records WHERE id = ?", (publish_id,)
        )
        assert row is not None
        return row

    async def _validate_publish_approval(
        self,
        *,
        approval_action_id: str | None,
        delivery_id: str,
    ) -> None:
        if not approval_action_id:
            raise ValueError("生产发布需要已批准的 PUBLISH_CONTENT 动作")
        action = await self.database.fetch_one(
            "SELECT * FROM approval_actions WHERE id = ?",
            (approval_action_id,),
        )
        if action is None:
            raise KeyError(f"发布审批动作不存在: {approval_action_id}")
        if action["action_type"] != "PUBLISH_CONTENT":
            raise ValueError("审批动作类型不是 PUBLISH_CONTENT")
        if action["target_type"] != "delivery" or action["target_id"] != delivery_id:
            raise ValueError("审批动作与当前交付不匹配")
        if action["status"] not in {"APPROVED", "EDITED_AND_APPROVED"}:
            raise ValueError("发布审批动作尚未批准")

    async def confirm_publish(
        self,
        publish_id: str,
        *,
        confirmed_by_actor_id: str,
        published_url: str,
    ) -> dict[str, Any]:
        record = await self.get_publish(publish_id)
        verification = await self.adapter.verify(published_url)
        # 本地或尚未公网可访问的 MVP 可由负责人确认继续；验证结果作为状态记录，
        # 不能被误作 Google 收录证据。
        now = _now()
        await self.database.execute(
            "UPDATE publish_records SET status = 'CONFIRMED', published_url = ?, "
            "confirmed_by_actor_id = ?, confirmed_at = ?, updated_at = ?, error_code = ?, "
            "error_message = ? WHERE id = ?",
            (
                published_url,
                confirmed_by_actor_id,
                now,
                now,
                None if verification.reachable else "URL_NOT_REACHABLE",
                verification.error_message,
                publish_id,
            ),
        )
        await self._set_task_status(record["task_id"], "MONITORING_INDEXING", confirmed_by_actor_id)
        await self.monitoring.create_plan(
            publish_record_id=publish_id,
            published_at=record["published_at"],
            origin_trace_id=record.get("source_trace_id"),
        )
        return await self.get_publish(publish_id)

    async def get_publish(self, publish_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT * FROM publish_records WHERE id = ?", (publish_id,)
        )
        if row is None:
            raise KeyError(f"发布记录不存在: {publish_id}")
        return row

    async def _set_task_status(self, task_id: str, status: str, actor_id: str | None) -> None:
        task = await self.database.fetch_one(
            "SELECT * FROM execution_tasks WHERE id = ?", (task_id,)
        )
        if task is None:
            raise KeyError(f"任务不存在: {task_id}")
        now = _now()
        await self.database.execute(
            "UPDATE execution_tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, task_id),
        )
        await self.database.execute(
            "INSERT INTO task_status_history(id, task_id, from_status, to_status, reason, "
            "changed_by_actor_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _id("taskstatus"),
                task_id,
                task["status"],
                status,
                "发布流程状态更新",
                actor_id,
                now,
            ),
        )
