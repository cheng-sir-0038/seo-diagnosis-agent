from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from src.observability.client import ObservabilityClient
from src.promptops.registry import PromptRegistry
from src.seo_agent.automation.models import DeliveryReviewOutput
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.services.system import SeoAgentSystemService
from src.seo_agent.tasks.models import TaskPrdContent
from src.seo_agent.tasks.service import TaskDeliveryService
from src.seo_agent.workflows.structured_model import (
    StructuredBusinessResult,
    StructuredModel,
    StructuredModelRunner,
)


class TaskDeliveryWorkflows:
    """任务 PRD 生成与交付审核：复用业务服务校验、模型调用和持久化。"""
    def __init__(
        self,
        database: SeoAgentDatabase,
        tasks: TaskDeliveryService,
        *,
        model_factory: Callable[[], StructuredModel],
        prompt_registry: PromptRegistry | None = None,
        observability: ObservabilityClient | None = None,
        prompt_environment: str = "production",
    ) -> None:
        self.database = database
        self.tasks = tasks
        self.model_factory = model_factory
        self.prompt_registry = prompt_registry
        self.observability = observability
        self.prompt_environment = prompt_environment
        self.system = SeoAgentSystemService(database)
        self.model_runner = StructuredModelRunner(
            model_factory=model_factory, prompt_registry=prompt_registry,
            observability=observability, prompt_environment=prompt_environment,
        )

    async def generate_prd(
        self,
        *,
        task_id: str,
        created_by_actor_id: str,
        generated_by_run_id: str | None = None,
    ) -> dict[str, Any]:
        task = await self.tasks.get_task_detail(task_id)
        invocation = await self.prepare_prd(task)
        content = invocation.output
        current_count = len(task.get("prd_versions") or [])
        prd = await self.tasks.create_prd_version(
            task_id=task_id,
            title=f"{task['title']} PRD V{current_count + 1}",
            content=content,
            created_by_actor_id=created_by_actor_id,
            source_version_id=(task.get("current_prd_version_id") if current_count else None),
            change_summary="PromptOps 结构化生成",
            generated_by_run_id=generated_by_run_id,
            prompt_version_id=invocation.prompt_version_id,
            source_trace_id=invocation.trace_id,
        )
        await self.tasks.render_prd_artifacts(prd["id"])
        await self.system.append_work_record(
            site_id=task["site_id"],
            title="生成任务 PRD",
            summary=f"{task['title']} 已生成 PRD V{prd['version_no']}",
            record_type="prd_generated",
            actor_id=created_by_actor_id,
            idempotency_key=f"prd-generated:{prd['id']}",
            payload={"task_id": task_id, "prd_version_id": prd["id"]},
        )
        return prd

    async def prepare_prd(self, task: dict[str, Any]) -> StructuredBusinessResult:
        """生成并校验 PRD 内容；不创建任务或写入业务记录。"""
        opportunities = task.get("opportunities") or []
        if not opportunities:
            raise ValueError("任务没有关联机会，无法生成 PRD")
        return await self._invoke_structured(
            prompt_key="seo.prd.generate",
            schema=TaskPrdContent,
            context={
                "task": {key: task.get(key) for key in (
                    "id", "title", "task_type", "priority", "deadline_at"
                )},
                "opportunities": [
                    {
                        "id": opportunity["id"], "title": opportunity["title"],
                        "normalized_intent": opportunity["normalized_intent"],
                        "summary": opportunity.get("current_summary"),
                        "score": opportunity.get("current_score"),
                        "priority": opportunity.get("priority_level"),
                    }
                    for opportunity in opportunities
                ],
            },
            operation="generate_prd",
            business_object={"task_id": task.get("id"), "site_id": task["site_id"]},
        )

    async def review_delivery(
        self,
        *,
        delivery_id: str,
        reviewer_actor_id: str,
        reviewed_by_run_id: str | None = None,
    ) -> dict[str, Any]:
        delivery = await self.database.fetch_one(
            "SELECT * FROM deliveries WHERE id = ?", (delivery_id,)
        )
        if delivery is None:
            raise KeyError(f"交付不存在: {delivery_id}")
        prd = await self.tasks.get_prd_version(delivery["prd_version_id"])
        task = await self.tasks.get_task(delivery["task_id"])
        context = {
            "task": {"id": task["id"], "title": task["title"], "task_type": task["task_type"]},
            "prd": json.loads(prd["structured_content_json"]),
            "delivery": {
                "id": delivery["id"],
                "title": delivery["title"],
                "summary": delivery.get("summary"),
                "structured_content": json.loads(delivery["structured_content_json"]),
            },
        }
        invocation = await self._invoke_structured(
            prompt_key="seo.delivery.review",
            schema=DeliveryReviewOutput,
            context=context,
            operation="review_delivery",
            business_object={
                "task_id": task["id"],
                "delivery_id": delivery_id,
                "site_id": task["site_id"],
            },
        )
        output = invocation.output
        review = await self.tasks.review_delivery(
            delivery_id=delivery_id,
            prd_version_id=delivery["prd_version_id"],
            reviewer_actor_id=reviewer_actor_id,
            status=output.status,
            score=output.score,
            passed_items=output.passed_items,
            failed_items=output.failed_items,
            missing_items=output.missing_items,
            revision_instructions=output.revision_instructions,
            reviewed_by_run_id=reviewed_by_run_id,
            prompt_version_id=invocation.prompt_version_id,
            source_trace_id=invocation.trace_id,
        )
        await self.system.append_work_record(
            site_id=task["site_id"],
            title="完成交付验收",
            summary=f"{delivery['title']} 验收结果：{review['status']}，{review['score']} 分",
            record_type="delivery_reviewed",
            actor_id=reviewer_actor_id,
            idempotency_key=f"delivery-reviewed:{review['id']}",
            payload={"task_id": task["id"], "delivery_id": delivery_id, "review_id": review["id"]},
        )
        return review

    async def _invoke_structured(
        self, *, prompt_key: str, schema: type, context: dict[str, Any],
        operation: str, business_object: dict[str, Any],
    ) -> StructuredBusinessResult:
        return await self.model_runner.invoke(
            prompt_key=prompt_key, schema=schema, context=context,
            operation=operation, business_object=business_object,
        )
