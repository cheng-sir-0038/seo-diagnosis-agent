from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.observability.client import ObservabilityClient
from src.observability.models import SpanKind, Status
from src.promptops.evaluation import ExecutionResult
from src.promptops.models import CandidatePrompt
from src.promptops.registry import PromptRegistry
from src.seo_agent.automation.model_result import invoke_structured_model


class DeepSeekCandidateGenerator:
    def __init__(
        self,
        *,
        model_factory: Callable[[], Any],
        prompt_registry: PromptRegistry,
        observability: ObservabilityClient | None = None,
        environment: str = "production",
    ) -> None:
        self.model_factory = model_factory
        self.prompt_registry = prompt_registry
        self.observability = observability
        self.environment = environment

    async def generate(
        self,
        *,
        current_version: dict[str, Any],
        failures: list[dict[str, Any]],
    ) -> CandidatePrompt:
        meta_prompt = await self.prompt_registry.resolve_version(
            "agent.prompt.optimize",
            environment=self.environment,
        )
        context = {
            "current_version": {
                "version_id": current_version["id"],
                "version_label": current_version["version_label"],
                "system_template": current_version["system_template"],
                "user_template": current_version["user_template"],
                "output_schema": current_version["output_schema_data"],
                "config": current_version["config_data"],
            },
            "failures": failures,
        }
        model = self.model_factory()
        messages = [
            SystemMessage(content=meta_prompt["system_template"]),
            HumanMessage(
                content=meta_prompt["user_template"].replace(
                    "{{input}}",
                    json.dumps(context, ensure_ascii=False, default=str),
                )
            ),
        ]
        started = time.perf_counter()
        if self.observability is None:
            invocation = await invoke_structured_model(model, CandidatePrompt, messages)
            if invocation.parsing_error is not None:
                raise invocation.parsing_error
            return CandidatePrompt.model_validate(invocation.parsed)
        async with self.observability.trace(
            "agent.prompt.optimize",
            input_data={"current_version_id": current_version["id"], "failures": failures},
        ) as trace:
            async with trace.run(
                trigger_type="prompt_optimization",
                agent_name="prompt-optimizer",
                workflow_name="generate_challenger",
                model_provider=_provider_name(model),
                model_name=_model_name(model),
            ) as run:
                async with run.span(
                    "generate_challenger",
                    kind=SpanKind.MODEL,
                    component_name=_provider_name(model),
                ) as span:
                    try:
                        invocation = await invoke_structured_model(
                            model,
                            CandidatePrompt,
                            messages,
                        )
                        if invocation.parsing_error is not None:
                            raise invocation.parsing_error
                        result = CandidatePrompt.model_validate(invocation.parsed)
                    except Exception as exc:
                        await span.model_call(
                            provider=_provider_name(model),
                            model=_model_name(model),
                            operation="generate_challenger",
                            prompt_key="agent.prompt.optimize",
                            prompt_version=meta_prompt["version_label"],
                            prompt_version_id=meta_prompt["id"],
                            status=Status.FAILED,
                            structured_output_valid=False,
                            error_code=type(exc).__name__,
                            error_message=str(exc),
                        )
                        await span.capture_exception(
                            exc,
                            issue_type="PROMPT_CANDIDATE_GENERATION_FAILED",
                            component="prompt_optimizer",
                        )
                        raise
                    await span.model_call(
                        provider=_provider_name(model),
                        model=_model_name(model),
                        operation="generate_challenger",
                        prompt_key="agent.prompt.optimize",
                        prompt_version=meta_prompt["version_label"],
                        prompt_version_id=meta_prompt["id"],
                        input_tokens=invocation.usage.get("input_tokens"),
                        output_tokens=invocation.usage.get("output_tokens"),
                        reasoning_tokens=invocation.usage.get("reasoning_tokens"),
                        cached_tokens=invocation.usage.get("cached_tokens"),
                        latency_ms=round((time.perf_counter() - started) * 1000, 3),
                        finish_reason=invocation.finish_reason,
                        structured_output_valid=True,
                        output_summary=result.model_dump(mode="json"),
                    )
                    return result


class DeepSeekPromptExecutor:
    def __init__(
        self,
        *,
        model_factory: Callable[[], Any],
        observability: ObservabilityClient | None = None,
    ) -> None:
        self.model_factory = model_factory
        self.observability = observability

    async def execute(self, version: dict[str, Any], case: dict[str, Any]) -> ExecutionResult:
        model = self.model_factory()
        schema = version.get("output_schema_data") or {"type": "object"}
        input_json = json.dumps(case["input_json_data"], ensure_ascii=False, default=str)
        messages = [
            SystemMessage(content=version["system_template"]),
            HumanMessage(content=version["user_template"].replace("{{input}}", input_json)),
        ]
        started = time.perf_counter()
        try:
            if self.observability is None:
                invocation = await invoke_structured_model(model, schema, messages)
                if invocation.parsing_error is not None:
                    raise invocation.parsing_error
                output = invocation.parsed
            else:
                async with self.observability.trace(
                    "prompt.evaluation.case",
                    input_data={
                        "prompt_version_id": version["id"],
                        "evaluation_case_id": case["id"],
                    },
                ) as trace:
                    async with trace.run(
                        trigger_type="evaluation",
                        agent_name="prompt-evaluator",
                        workflow_name="dataset_replay",
                        model_provider=_provider_name(model),
                        model_name=_model_name(model),
                    ) as run:
                        async with run.span(
                            "evaluate_case",
                            kind=SpanKind.MODEL,
                            component_name=_provider_name(model),
                        ) as span:
                            try:
                                invocation = await invoke_structured_model(
                                    model,
                                    schema,
                                    messages,
                                )
                                if invocation.parsing_error is not None:
                                    raise invocation.parsing_error
                                output = invocation.parsed
                            except Exception as exc:
                                await span.model_call(
                                    provider=_provider_name(model),
                                    model=_model_name(model),
                                    operation="evaluate_case",
                                    prompt_version=version["version_label"],
                                    prompt_version_id=version["id"],
                                    evaluation_case_id=case["id"],
                                    status=Status.FAILED,
                                    structured_output_valid=False,
                                    latency_ms=round(
                                        (time.perf_counter() - started) * 1000,
                                        3,
                                    ),
                                    error_code=type(exc).__name__,
                                    error_message=str(exc),
                                )
                                await span.capture_exception(
                                    exc,
                                    issue_type="PROMPT_EVALUATION_MODEL_FAILED",
                                    component="prompt_evaluator",
                                    context={
                                        "prompt_version_id": version["id"],
                                        "evaluation_case_id": case["id"],
                                    },
                                )
                                raise
                            await span.model_call(
                                provider=_provider_name(model),
                                model=_model_name(model),
                                operation="evaluate_case",
                                prompt_version=version["version_label"],
                                prompt_version_id=version["id"],
                                evaluation_case_id=case["id"],
                                input_tokens=invocation.usage.get("input_tokens"),
                                output_tokens=invocation.usage.get("output_tokens"),
                                reasoning_tokens=invocation.usage.get("reasoning_tokens"),
                                cached_tokens=invocation.usage.get("cached_tokens"),
                                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                                finish_reason=invocation.finish_reason,
                                structured_output_valid=True,
                                output_summary=output,
                            )
            usage = invocation.usage if "invocation" in locals() else {}
            return ExecutionResult(
                output=output,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        except Exception as exc:
            return ExecutionResult(
                output={},
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                error_message=str(exc),
            )


def _provider_name(model: Any) -> str:
    return str(getattr(model, "provider_name", model.__class__.__name__)).lower()


def _model_name(model: Any) -> str:
    return str(
        getattr(model, "model_name", None)
        or getattr(model, "model", None)
        or model.__class__.__name__
    )
