from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from src.observability.client import ObservabilityClient
from src.observability.models import SpanKind, Status
from src.promptops.registry import PromptRegistry
from src.seo_agent.automation.model_result import invoke_structured_model
from src.seo_agent.automation.models import DeliveryReviewOutput
from src.seo_agent.automation.prompts import builtin_prompt
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.services.system import SeoAgentSystemService
from src.seo_agent.tasks.models import TaskPrdContent
from src.seo_agent.tasks.service import TaskDeliveryService


@dataclass(frozen=True)
class _StructuredBusinessResult:
    output: Any
    prompt_key: str
    prompt_version_id: str | None
    trace_id: str | None


class StructuredModel(Protocol):
    def with_structured_output(self, schema: type, **kwargs: Any) -> Any: ...


class StructuredModelAutomation:
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

    async def generate_prd(
        self,
        *,
        task_id: str,
        created_by_actor_id: str,
        generated_by_run_id: str | None = None,
    ) -> dict[str, Any]:
        task = await self.tasks.get_task_detail(task_id)
        opportunities = task.get("opportunities") or []
        if not opportunities:
            raise ValueError("任务没有关联机会，无法生成 PRD")
        context = {
            "task": {
                "id": task["id"],
                "title": task["title"],
                "task_type": task["task_type"],
                "priority": task["priority"],
                "deadline_at": task.get("deadline_at"),
            },
            "opportunities": [
                {
                    "id": opportunity["id"],
                    "title": opportunity["title"],
                    "normalized_intent": opportunity["normalized_intent"],
                    "summary": opportunity.get("current_summary"),
                    "score": opportunity.get("current_score"),
                    "priority": opportunity.get("priority_level"),
                }
                for opportunity in opportunities
            ],
        }
        invocation = await self._invoke_structured(
            prompt_key="seo.prd.generate",
            schema=TaskPrdContent,
            context=context,
            operation="generate_prd",
            business_object={"task_id": task_id, "site_id": task["site_id"]},
        )
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
            summary=f"{delivery['title']} 验收结果：{output.status}，{output.score} 分",
            record_type="delivery_reviewed",
            actor_id=reviewer_actor_id,
            idempotency_key=f"delivery-reviewed:{review['id']}",
            payload={"task_id": task["id"], "delivery_id": delivery_id, "review_id": review["id"]},
        )
        return review

    async def _resolve_prompt(self, prompt_key: str) -> dict[str, Any]:
        if self.prompt_registry is None:
            return builtin_prompt(prompt_key)
        return await self.prompt_registry.resolve_version(
            prompt_key,
            environment=self.prompt_environment,
        )

    async def _invoke_structured(
        self,
        *,
        prompt_key: str,
        schema: type,
        context: dict[str, Any],
        operation: str,
        business_object: dict[str, Any],
    ) -> _StructuredBusinessResult:
        prompt = await self._resolve_prompt(prompt_key)
        input_json = json.dumps(context, ensure_ascii=False, default=str)
        user_content = prompt["user_template"].replace("{{input}}", input_json)
        model = self.model_factory()
        messages = [
            SystemMessage(content=prompt["system_template"]),
            HumanMessage(content=user_content),
        ]

        async def invoke():
            started = time.perf_counter()
            result = await invoke_structured_model(model, schema, messages)
            if result.parsing_error is not None:
                raise result.parsing_error
            return result, round((time.perf_counter() - started) * 1000, 3)

        if self.observability is None:
            invocation, _ = await invoke()
            return _StructuredBusinessResult(
                output=schema.model_validate(invocation.parsed),
                prompt_key=prompt_key,
                prompt_version_id=prompt.get("id"),
                trace_id=None,
            )

        await self.observability.startup()
        async with self.observability.trace(
            prompt_key,
            input_data={"business_object": business_object, "context": context},
        ) as trace:
            async with trace.run(
                trigger_type="automation",
                agent_name="seo-specialist",
                workflow_name=operation,
                model_provider=self._model_provider_name(model),
                model_name=self._model_name(model),
            ) as run:
                async with run.span(
                    operation,
                    kind=SpanKind.MODEL,
                    component_name=self._model_provider_name(model),
                ) as span:
                    try:
                        invocation, latency_ms = await invoke()
                        validated = schema.model_validate(invocation.parsed)
                    except Exception as exc:
                        await span.model_call(
                            provider=self._model_provider_name(model),
                            model=self._model_name(model),
                            operation=operation,
                            prompt_key=prompt_key,
                            prompt_version=prompt["version_label"],
                            prompt_version_id=prompt.get("id"),
                            status=Status.FAILED,
                            error_code=type(exc).__name__,
                            error_message=str(exc),
                            structured_output_valid=False,
                            input_summary={"business_object": business_object},
                        )
                        await span.capture_exception(
                            exc,
                            issue_type="STRUCTURED_OUTPUT_FAILED",
                            component=operation,
                            context={"prompt_version_id": prompt.get("id")},
                        )
                        raise
                    await span.model_call(
                        provider=self._model_provider_name(model),
                        model=self._model_name(model),
                        operation=operation,
                        prompt_key=prompt_key,
                        prompt_version=prompt["version_label"],
                        prompt_version_id=prompt.get("id"),
                        input_tokens=invocation.usage.get("input_tokens"),
                        output_tokens=invocation.usage.get("output_tokens"),
                        reasoning_tokens=invocation.usage.get("reasoning_tokens"),
                        cached_tokens=invocation.usage.get("cached_tokens"),
                        latency_ms=latency_ms,
                        finish_reason=invocation.finish_reason,
                        structured_output_valid=True,
                        input_summary={"business_object": business_object},
                        output_summary=validated.model_dump(mode="json"),
                    )
                    return _StructuredBusinessResult(
                        output=validated,
                        prompt_key=prompt_key,
                        prompt_version_id=prompt.get("id"),
                        trace_id=trace.trace_id,
                    )

    @staticmethod
    def _model_provider_name(model: Any) -> str:
        return str(getattr(model, "provider_name", model.__class__.__name__)).lower()

    @staticmethod
    def _model_name(model: Any) -> str:
        return str(
            getattr(model, "model_name", None)
            or getattr(model, "model", None)
            or model.__class__.__name__
        )
