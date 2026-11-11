from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from src.observability.client import ObservabilityClient
from src.observability.instrumentation import (
    model_usage_fields,
    observed_operation,
    record_model_failure,
)
from src.observability.models import SpanKind
from src.promptops.registry import PromptRegistry
from src.seo_agent.automation.model_result import invoke_structured_model, model_provider_name
from src.seo_agent.automation.prompts import builtin_prompt


class StructuredModel(Protocol):
    def with_structured_output(self, schema: type, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class StructuredBusinessResult:
    output: Any
    prompt_key: str
    prompt_version_id: str | None
    trace_id: str | None


class StructuredModelRunner:
    """共享结构化输出校验、提示词版本和模型调用观测。"""

    def __init__(
        self,
        *,
        model_factory: Callable[[], StructuredModel],
        prompt_registry: PromptRegistry | None = None,
        observability: ObservabilityClient | None = None,
        prompt_environment: str = "production",
    ) -> None:
        self.model_factory, self.prompt_registry = model_factory, prompt_registry
        self.observability, self.prompt_environment = observability, prompt_environment

    async def invoke(
        self,
        *,
        prompt_key: str,
        schema: type,
        context: dict[str, Any],
        operation: str,
        business_object: dict[str, Any],
        model: StructuredModel | None = None,
        method: str | None = None,
    ) -> StructuredBusinessResult:
        prompt = (
            await self.prompt_registry.resolve_version(
                prompt_key, environment=self.prompt_environment
            )
            if self.prompt_registry
            else builtin_prompt(prompt_key)
        )
        system = prompt["system_template"]
        if method == "json_mode":
            system += "\n仅输出符合以下 JSON Schema 的 JSON 对象，不要输出解释或 Markdown：\n"
            system += json.dumps(schema.model_json_schema(), ensure_ascii=False)
        messages = [
            SystemMessage(content=system),
            HumanMessage(
                content=prompt["user_template"].replace(
                    "{{input}}", json.dumps(context, ensure_ascii=False, default=str)
                )
            ),
        ]
        model = model if model is not None else self.model_factory()
        provider = model_provider_name(model)
        name = str(
            getattr(model, "model_name", None)
            or getattr(model, "model", None)
            or model.__class__.__name__
        )
        details = {
            "provider": provider,
            "model": name,
            "operation": operation,
            "prompt_key": prompt_key,
            "prompt_version": prompt["version_label"],
            "prompt_version_id": prompt.get("id"),
            "input_summary": {"business_object": business_object, "messages": messages},
        }
        async with observed_operation(
            self.observability,
            prompt_key,
            kind=SpanKind.MODEL,
            component_name=provider,
            input_data=details["input_summary"],
            trigger_type="automation",
            metadata={"workflow_name": operation, "prompt_version_id": prompt.get("id"),
                      "structured_output_method": method or "function_calling"},
        ) as span:
            started = time.perf_counter()
            invocation = None
            try:
                invocation = await invoke_structured_model(model, schema, messages, method=method)
                if invocation.parsing_error is not None:
                    raise invocation.parsing_error
                validated = schema.model_validate(invocation.parsed)
            except Exception as exc:
                if span is not None:
                    output = (
                        {"response": invocation.raw, "parsed": invocation.parsed}
                        if invocation else {}
                    )
                    span.output_data = output
                    await record_model_failure(
                        span,
                        exc,
                        issue_type="STRUCTURED_OUTPUT_FAILED",
                        component=operation,
                        latency_ms=round((time.perf_counter() - started) * 1000, 3),
                        structured_output_valid=False,
                        output_summary=output,
                        **model_usage_fields(invocation.usage if invocation else {}),
                        **details,
                    )
                raise
            if span is not None:
                span.output_data = validated.model_dump(mode="json", by_alias=True)
                await span.model_call(
                    **details,
                    **model_usage_fields(invocation.usage),
                    latency_ms=round((time.perf_counter() - started) * 1000, 3),
                    finish_reason=invocation.finish_reason,
                    structured_output_valid=True,
                    output_summary=validated.model_dump(mode="json", by_alias=True),
                )
            return StructuredBusinessResult(
                output=validated,
                prompt_key=prompt_key,
                prompt_version_id=prompt.get("id"),
                trace_id=span.trace_id if span else None,
            )
