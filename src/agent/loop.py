from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from src.agent.content_sanitizer import sanitize_assistant_content
from src.agent.harness.protocols import AgentRunPersistence
from src.agent.harness.summary import compact_model_context
from src.agent.models import AgentRun, HumanInputRequest, HumanInputState, RunStatus
from src.agent.observability import AgentRunObserver
from src.agent.providers.base import ModelProvider, ToolCall
from src.agent.tools.base import ToolRuntimeContext, resolve_render_hint
from src.agent.tools.registry import ToolRegistry
from src.observability.context import (
    get_current_context,
    reset_current_context,
    set_current_context,
)
from src.observability.instrumentation import (
    model_usage_fields,
    observed_operation,
    record_model_failure,
)
from src.observability.models import Severity, SpanKind, Status

logger = logging.getLogger(__name__)

_BUDGET_SUMMARY_PROMPT = """你已到达本次调查的模型循环预算上限。
现在必须停止调用任何工具，只能基于已经获得的证据形成最终答案。
请完成用户已要求的部分；证据不足的部分明确写明限制，不得继续补查、不得编造数据。
输出应包含：已确认结论、关键证据、数据不足或失败项、下一步建议。"""
_BUDGET_PREFIX = (
    "本次调查已达到本轮调查预算，以下结论基于已经获得的证据；数据不足的部分已明确标注。"
)
_DUPLICATE_GUARD_PREFIX = "检测到重复工具调用，已停止继续补查；以下结论基于已经获得的证据。"


class AgentLoop:
    def __init__(
        self,
        provider: ModelProvider,
        tools: ToolRegistry,
        *,
        max_iterations: int = 8,
        max_tool_calls: int = 12,
        tool_timeout: float = 15.0,
        observer: AgentRunObserver | None = None,
        persistence: AgentRunPersistence | None = None,
        context_message_limit: int = 24,
        context_character_limit: int = 50_000,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls
        self.tool_timeout = tool_timeout
        self.observer = observer
        self.persistence = persistence
        self.context_message_limit = context_message_limit
        self.context_character_limit = context_character_limit

    async def run(self, run: AgentRun) -> None:
        context_token = set_current_context(get_current_context()) if self.observer else None
        resumed = run.status == RunStatus.RECOVERING
        messages = [dict(message) for message in run.messages]
        iteration = int(run.iteration)
        tool_count = int(run.tool_count)
        run.error = None
        run.finished_at = None
        run.partial = False
        run.completion_mode = None
        try:
            if self.observer is not None:
                await self.observer.start(run, self.provider)
            run.status = RunStatus.RUNNING
            await self._emit(
                run,
                "run.resumed" if resumed else "run.started",
                {"status": run.status.value, "iteration": run.iteration},
            )
            if self.max_iterations <= 0:
                raise RuntimeError("最大模型循环次数必须大于 0")
            if run.human_input and run.human_input.answer is not None:
                await self._emit(run, "input.accepted", {
                    "request_id": run.human_input.request_id,
                    "answer": run.human_input.answer,
                    "action": run.human_input.action,
                })
                async with observed_operation(
                    self.observer.client if self.observer else None,
                    "human_input_received", kind=SpanKind.INTERNAL,
                    input_data={
                        "request_id": run.human_input.request_id, "answer": run.human_input.answer,
                    },
                ) as span:
                    if span:
                        span.output_data = {"application_run_id": run.id, "status": "resuming"}
                if not run.pending_tool_calls:
                    call = ToolCall(
                        id=f"resume_{uuid.uuid4().hex}", name=run.human_input.tool_name,
                        arguments=run.human_input.tool_arguments,
                    )
                    messages.append({"role": "assistant", "content": None, "tool_calls": [{
                        "id": call.id, "type": "function", "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }]})
                    run.pending_tool_calls = [_serialize_tool_call(call)]
                    await self._save_checkpoint(
                        run, messages, iteration=iteration, tool_count=tool_count,
                        stage="input_resume_pending",
                    )
            if run.pending_tool_calls:
                iteration, tool_count = await self._execute_pending_tools(
                    run,
                    messages,
                    iteration=iteration,
                    tool_count=tool_count,
                )
            if await self._wait_for_input(run, messages):
                return

            if iteration >= self.max_iterations:
                iteration = self.max_iterations - 1

            while iteration < self.max_iterations:
                self._check_cancel(run)
                repeated_tool_guard = _has_reused_tool_result(run)
                budget_exhausted = iteration >= self.max_iterations - 1
                force_summary = budget_exhausted or repeated_tool_guard
                completion_mode = "duplicate_tool_guard" if repeated_tool_guard else "budget_limit"
                stage = "summarizing_budget" if force_summary else "calling_model"
                run.iteration = iteration
                run.tool_count = tool_count
                await self._emit(
                    run,
                    "stage.changed",
                    {
                        "stage": stage,
                        "iteration": iteration + 1,
                        "remaining_iterations": max(0, self.max_iterations - iteration - 1),
                    },
                    stage=stage,
                )
                await self._save_checkpoint(
                    run,
                    messages,
                    iteration=iteration,
                    tool_count=tool_count,
                    stage=stage,
                )
                assistant_text: list[str] = []
                pending_calls: list[ToolCall] = []
                usage = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_tokens": 0,
                    "cached_tokens": 0,
                    "total_tokens": 0,
                }
                finish_reason: str | None = None
                first_token_ms: float | None = None
                usage_keys: set[str] = set()
                started = time.perf_counter()
                model_messages = compact_model_context(
                    messages,
                    run.survival_summary,
                    message_limit=self.context_message_limit,
                    character_limit=self.context_character_limit,
                )
                if force_summary:
                    model_messages = _inject_budget_summary_prompt(model_messages)
                tool_choice = "none" if force_summary else "auto"
                tool_schemas = self.tools.schemas()
                model_input = {
                    "messages": model_messages, "tools": tool_schemas, "tool_choice": tool_choice
                }
                model_span = (
                    self.observer.span(
                        f"model.iteration.{iteration + 1}",
                        kind=SpanKind.MODEL,
                        component_name=self.provider.provider_name,
                        input_data={
                            "message_count": len(model_messages),
                            "full_message_count": len(messages),
                            "tool_schema_count": len(tool_schemas),
                            "tool_choice": tool_choice,
                        },
                    )
                    if self.observer
                    else None
                )

                async def consume_model() -> None:
                    nonlocal finish_reason, first_token_ms
                    async for chunk in self.provider.stream(
                        model_messages,
                        tool_schemas,
                        tool_choice=tool_choice,
                    ):
                        self._check_cancel(run)
                        if chunk.text:
                            if first_token_ms is None:
                                first_token_ms = round((time.perf_counter() - started) * 1000, 3)
                            assistant_text.append(chunk.text)
                            if not force_summary:
                                await self._emit(run, "message.delta", {"delta": chunk.text})
                        if chunk.tool_calls:
                            pending_calls.extend(chunk.tool_calls)
                        if chunk.finish_reason:
                            finish_reason = chunk.finish_reason
                        usage_keys.update(chunk.usage)
                        for key, value in chunk.usage.items():
                            usage[key] = max(usage.get(key, 0), int(value or 0))

                if model_span is not None:
                    async with model_span as span:
                        try:
                            await consume_model()
                        except Exception as exc:
                            await record_model_failure(
                                span, exc, component=f"model.{self.provider.provider_name}",
                                provider=self.provider.provider_name,
                                model=self.provider.model_name or "unknown",
                                operation="stream",
                                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                                time_to_first_token_ms=first_token_ms,
                                finish_reason=finish_reason,
                                input_summary=model_input,
                                output_summary={"content": "".join(assistant_text)},
                            )
                            raise
                        await span.model_call(
                            provider=self.provider.provider_name,
                            model=self.provider.model_name or "unknown",
                            operation="stream",
                            **model_usage_fields({key: usage[key] for key in usage_keys}),
                            latency_ms=round((time.perf_counter() - started) * 1000, 3),
                            time_to_first_token_ms=first_token_ms,
                            finish_reason=finish_reason,
                            input_summary=model_input,
                            output_summary={
                                "content": "".join(assistant_text),
                                "tool_calls": [
                                    {
                                        "id": call.id,
                                        "name": call.name,
                                        "arguments": call.arguments,
                                    }
                                    for call in pending_calls
                                ],
                                "text_length": sum(len(part) for part in assistant_text),
                                "tool_call_count": len(pending_calls),
                                "iteration": iteration + 1,
                                "tool_choice": tool_choice,
                                "selected_tools": [
                                    call.name if call.name in self.tools.names() else "unknown_tool"
                                    for call in pending_calls
                                ],
                                "decision": (
                                    "forced_summary" if force_summary else
                                    "call_tools" if pending_calls else "final_answer"
                                ),
                                "decision_reason": completion_mode if force_summary else None,
                            },
                        )
                else:
                    await consume_model()

                for key, value in usage.items():
                    run.token_usage[key] = run.token_usage.get(key, 0) + int(value or 0)

                if pending_calls and not force_summary:
                    assistant_message: dict[str, Any] = {
                        "role": "assistant",
                        "content": "".join(assistant_text) or None,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                                },
                            }
                            for call in pending_calls
                        ],
                    }
                    messages.append(assistant_message)
                    run.pending_tool_calls = [_serialize_tool_call(call) for call in pending_calls]
                    await self._save_checkpoint(
                        run,
                        messages,
                        iteration=iteration,
                        tool_count=tool_count,
                        stage="tools_pending",
                    )
                    iteration, tool_count = await self._execute_pending_tools(
                        run,
                        messages,
                        iteration=iteration,
                        tool_count=tool_count,
                    )
                    if await self._wait_for_input(run, messages):
                        return
                    continue

                raw_final_text = "".join(assistant_text).strip()
                sanitized = sanitize_assistant_content(
                    raw_final_text,
                    fallback=_budget_fallback(run) if force_summary else None,
                    discard_body_on_control_markup=force_summary,
                )
                final_text = sanitized.content
                if force_summary:
                    final_text = _budget_completion_text(
                        final_text,
                        run,
                        completion_mode=completion_mode,
                    )
                    run.partial = True
                    run.completion_mode = completion_mode
                elif not final_text:
                    final_text = "模型未生成可展示的文本，请查看已经获取的数据证据。"
                messages.append({"role": "assistant", "content": final_text})
                run.messages = [dict(message) for message in messages]
                await self._emit(run, "message.completed", {"content": final_text})
                run.status = RunStatus.COMPLETED
                run.finished_at = datetime.now(UTC)
                await self._save_checkpoint(
                    run,
                    messages,
                    iteration=iteration + 1,
                    tool_count=tool_count,
                    stage="completed",
                )
                if self.persistence is not None:
                    await self.persistence.finalize(run)
                await self._emit(
                    run,
                    "run.completed",
                    {
                        "status": run.status.value,
                        "partial": run.partial,
                        "completion_mode": run.completion_mode or "normal",
                    },
                )
                return
            raise RuntimeError("模型循环未能收敛")
        except asyncio.CancelledError:
            if run.pause_requested:
                if run.status != RunStatus.WAITING_FOR_INPUT:
                    run.status = RunStatus.RECOVERING
                await self._save_checkpoint(
                    run,
                    messages,
                    iteration=iteration,
                    tool_count=tool_count,
                    stage=("waiting_for_input"
                           if run.status == RunStatus.WAITING_FOR_INPUT else "paused"),
                )
            elif not run.terminal:
                run.status = RunStatus.CANCELLED
                run.finished_at = datetime.now(UTC)
                await self._save_checkpoint(
                    run,
                    messages,
                    iteration=iteration,
                    tool_count=tool_count,
                    stage="cancelled",
                )
                if self.persistence is not None:
                    await self.persistence.finalize(run)
                await self._emit(run, "run.cancelled", {"status": run.status.value})
            raise
        except _RunCancelledError:
            run.status = RunStatus.CANCELLED
            run.finished_at = datetime.now(UTC)
            await self._save_checkpoint(
                run,
                messages,
                iteration=iteration,
                tool_count=tool_count,
                stage="cancelled",
            )
            if self.persistence is not None:
                await self.persistence.finalize(run)
            await self._emit(run, "run.cancelled", {"status": run.status.value})
        except Exception as exc:
            run.status = RunStatus.FAILED
            run.error = str(exc)
            run.finished_at = datetime.now(UTC)
            try:
                await self._save_checkpoint(
                    run, messages, iteration=iteration, tool_count=tool_count, stage="failed"
                )
                if self.persistence is not None:
                    await self.persistence.finalize(run)
            except Exception as checkpoint_exc:
                logger.warning("Failed to persist failed run: %s", type(checkpoint_exc).__name__)
            try:
                await self._emit(
                    run,
                    "run.failed",
                    {"status": run.status.value, "message": str(exc)},
                    severity=Severity.ERROR,
                )
            except Exception as event_exc:
                logger.warning("Failed to persist failure event: %s", type(event_exc).__name__)
        finally:
            try:
                if self.persistence is not None:
                    await self.persistence.finalize(run)
                if self.observer is not None:
                    try:
                        await self.observer.finish(run)
                    except Exception as observer_exc:
                        logger.warning(
                            "Failed to finish observation: %s", type(observer_exc).__name__
                        )
            finally:
                if context_token is not None:
                    reset_current_context(context_token)

    async def _execute_pending_tools(
        self,
        run: AgentRun,
        messages: list[dict[str, Any]],
        *,
        iteration: int,
        tool_count: int,
    ) -> tuple[int, int]:
        while run.pending_tool_calls:
            self._check_cancel(run)
            call = _deserialize_tool_call(run.pending_tool_calls[0])
            cached = _find_cached_success(messages, call)
            if cached is not None:
                result_payload, reused_from_call_id = cached
                await self._reuse_tool_result(
                    run,
                    call,
                    messages,
                    result_payload=result_payload,
                    reused_from_call_id=reused_from_call_id,
                )
            else:
                tool_count += 1
                if tool_count > self.max_tool_calls:
                    raise RuntimeError("超过最大工具调用次数")
                await self._execute_tool(run, call, messages)
            payload = json.loads(messages[-1]["content"])
            request = (payload.get("metadata") or {}).get("human_input")
            if request is not None:
                validated = HumanInputRequest.model_validate(request)
                run.human_input = HumanInputState(
                    **validated.model_dump(), request_id=f"input_{uuid.uuid4().hex}",
                    tool_name=call.name,
                    tool_arguments=call.arguments, tool_result=payload,
                )
            elif run.human_input and run.human_input.tool_name == call.name:
                run.human_input = None
            run.pending_tool_calls = run.pending_tool_calls[1:]
            run.tool_count = tool_count
            if not run.pending_tool_calls:
                iteration += 1
                run.iteration = iteration
            run.messages = [dict(message) for message in messages]
            await self._save_checkpoint(
                run,
                messages,
                iteration=iteration,
                tool_count=tool_count,
                stage="tool_completed",
            )
        return iteration, tool_count

    async def _wait_for_input(self, run: AgentRun, messages: list[dict[str, Any]]) -> bool:
        pending = run.human_input
        if pending is None or pending.answer is not None:
            return False
        text = "还需要你补充以下信息：\n\n" + "\n".join(
            f"{index + 1}. {question}" for index, question in enumerate(pending.questions)
        )
        if not messages or messages[-1].get("content") != text:
            messages.append({"role": "assistant", "content": text})
        run.status = RunStatus.WAITING_FOR_INPUT
        async with observed_operation(
            self.observer.client if self.observer else None,
            "wait_for_user_input", kind=SpanKind.INTERNAL,
            input_data={"request_id": pending.request_id, "questions": pending.questions},
        ) as span:
            if span:
                span.output_data = {"application_run_id": run.id, "status": run.status.value}
        await self._emit(run, "message.completed", {"content": text})
        await self._emit(run, "run.waiting_for_input", {
            "request_id": pending.request_id, "questions": pending.questions,
            "allow_use_available": pending.allow_use_available,
        }, stage="waiting_for_input")
        await self._save_checkpoint(
            run, messages, iteration=run.iteration, tool_count=run.tool_count,
            stage="waiting_for_input",
        )
        return True

    async def _reuse_tool_result(
        self,
        run: AgentRun,
        call: ToolCall,
        messages: list[dict[str, Any]],
        *,
        result_payload: dict[str, Any],
        reused_from_call_id: str,
    ) -> None:
        try:
            tool = self.tools.get(call.name)
        except KeyError:
            tool = None
        await self._emit(
            run,
            "tool.started",
            {
                "tool_call_id": call.id,
                "name": call.name,
                "arguments": call.arguments,
                "reused": True,
            },
        )
        event_payload: dict[str, Any] = {
            "tool_call_id": call.id,
            "name": call.name,
            "result": result_payload,
            "error_code": None,
            "latency_ms": 0.0,
            "reused": True,
            "reused_from_tool_call_id": reused_from_call_id,
        }
        if tool is not None:
            render_hint = resolve_render_hint(tool, result_payload)
            if render_hint is not None:
                event_payload["render_hint"] = render_hint
        await self._emit(run, "tool.completed", event_payload)
        if self.observer is not None:
            await self.observer.reused_tool(
                call.name if tool is not None else "unknown_tool", call.arguments, result_payload
            )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": json.dumps(result_payload, ensure_ascii=False),
            }
        )

    async def _save_checkpoint(
        self,
        run: AgentRun,
        messages: list[dict[str, Any]],
        *,
        iteration: int,
        tool_count: int,
        stage: str,
    ) -> None:
        run.messages = [dict(message) for message in messages]
        run.iteration = iteration
        run.tool_count = tool_count
        if self.persistence is None:
            return
        run.survival_summary = await self.persistence.save_checkpoint(
            run,
            messages=run.messages,
            iteration=iteration,
            tool_count=tool_count,
            pending_tool_calls=run.pending_tool_calls,
            stage=stage,
            token_usage=run.token_usage,
        )

    async def _execute_tool(
        self,
        run: AgentRun,
        call: ToolCall,
        messages: list[dict[str, Any]],
    ) -> None:
        try:
            tool = self.tools.get(call.name)
        except KeyError:
            tool = None
        await self._emit(
            run,
            "tool.started",
            {"tool_call_id": call.id, "name": call.name, "arguments": call.arguments},
        )
        started = time.perf_counter()
        tool_span = (
            self.observer.span(
                call.name if tool is not None else "unknown_tool",
                kind=SpanKind.TOOL,
                component_name=call.name if tool is not None else "unknown_tool",
                input_data={"arguments": call.arguments},
            )
            if self.observer
            else None
        )

        async def execute() -> tuple[dict[str, Any], Status, str | None, str | None]:
            if tool is None:
                return (
                    {"success": False, "error": f"未知工具: {call.name}"},
                    Status.FAILED,
                    "UNKNOWN_TOOL",
                    f"未知工具: {call.name}",
                )
            try:
                result = await asyncio.wait_for(
                    tool.execute(
                        call.arguments,
                        runtime=ToolRuntimeContext(
                            run_id=run.id,
                            task_id=run.task_id,
                            thread_id=run.thread_id,
                            site_id=run.site_id,
                            conversation_id=run.conversation_id,
                            metadata={"human_input_resume": run.human_input.model_dump(mode="json")}
                            if run.human_input and run.human_input.answer is not None
                            and run.human_input.tool_name == call.name else {},
                        ),
                    ),
                    timeout=self.tool_timeout,
                )
                payload = result.model_dump(mode="json")
                return (
                    payload,
                    Status.COMPLETED if result.success else Status.FAILED,
                    None if result.success else "TOOL_FAILED",
                    result.error,
                )
            except TimeoutError as exc:
                return (
                    {"success": False, "error": "工具执行超时"},
                    Status.FAILED,
                    "TOOL_TIMEOUT",
                    str(exc) or "工具执行超时",
                )
            except Exception as exc:
                return (
                    {"success": False, "error": str(exc)},
                    Status.FAILED,
                    type(exc).__name__,
                    str(exc),
                )

        if tool_span is not None:
            async with tool_span as span:
                result_payload, status, error_code, error_message = await execute()
                latency_ms = round((time.perf_counter() - started) * 1000, 3)
                await span.tool_call(
                    tool_call_id=call.id,
                    tool_name=call.name if tool is not None else "unknown_tool",
                    operation="execute",
                    arguments=call.arguments,
                    result_summary=result_payload,
                    status=status,
                    timeout_seconds=self.tool_timeout,
                    latency_ms=latency_ms,
                    error_code=error_code,
                    error_message=error_message,
                )
                if status == Status.FAILED:
                    await span.capture_exception(
                        RuntimeError(error_message or "工具执行失败"),
                        issue_type=error_code or "TOOL_FAILED",
                        component=call.name,
                        error_code=error_code,
                        context={"tool_call_id": call.id},
                    )
        else:
            result_payload, status, error_code, error_message = await execute()

        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        event_type = "tool.completed" if status == Status.COMPLETED else "tool.failed"
        event_payload: dict[str, Any] = {
            "tool_call_id": call.id,
            "name": call.name,
            "result": result_payload,
            "error_code": error_code,
            "latency_ms": latency_ms,
        }
        if tool is not None and status == Status.COMPLETED:
            render_hint = resolve_render_hint(tool, result_payload)
            if render_hint is not None:
                event_payload["render_hint"] = render_hint
        await self._emit(
            run,
            event_type,
            event_payload,
            severity=Severity.INFO if status == Status.COMPLETED else Severity.ERROR,
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": json.dumps(result_payload, ensure_ascii=False),
            }
        )

    async def _emit(
        self,
        run: AgentRun,
        event_type: str,
        payload: dict[str, Any],
        *,
        severity: Severity = Severity.INFO,
        stage: str | None = None,
    ) -> None:
        event = run.emit(event_type, payload)
        if self.persistence is not None:
            await self.persistence.persist_event(run, event)
        if self.observer is not None:
            await self.observer.event(
                event_type,
                payload,
                severity=severity,
                stage=stage,
            )

    @staticmethod
    def _check_cancel(run: AgentRun) -> None:
        if run.cancel_event.is_set():
            raise _RunCancelledError


def _has_reused_tool_result(run: AgentRun) -> bool:
    return any(
        event.type == "tool.completed" and event.payload.get("reused") is True
        for event in run.events
    )


def _canonical_tool_call_key(name: str, arguments: dict[str, Any]) -> str:
    return (
        f"{name}:{json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
    )


def _find_cached_success(
    messages: list[dict[str, Any]],
    call: ToolCall,
) -> tuple[dict[str, Any], str] | None:
    call_keys: dict[str, str] = {}
    cache: dict[str, tuple[dict[str, Any], str]] = {}
    for message in messages:
        if message.get("role") == "assistant":
            for item in message.get("tool_calls") or []:
                if not isinstance(item, dict):
                    continue
                function = item.get("function") or {}
                if not isinstance(function, dict):
                    continue
                raw_arguments = function.get("arguments") or "{}"
                try:
                    arguments = (
                        json.loads(raw_arguments)
                        if isinstance(raw_arguments, str)
                        else dict(raw_arguments)
                    )
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                if not isinstance(arguments, dict):
                    continue
                tool_call_id = str(item.get("id") or "")
                name = str(function.get("name") or "")
                if tool_call_id and name:
                    call_keys[tool_call_id] = _canonical_tool_call_key(name, arguments)
        elif message.get("role") == "tool":
            tool_call_id = str(message.get("tool_call_id") or "")
            key = call_keys.get(tool_call_id)
            if not key:
                continue
            content = message.get("content")
            try:
                payload = json.loads(content) if isinstance(content, str) else dict(content or {})
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if (
                isinstance(payload, dict) and payload.get("success") is True
                and not (payload.get("metadata") or {}).get("human_input")
            ):
                cache[key] = (payload, tool_call_id)
    return cache.get(_canonical_tool_call_key(call.name, call.arguments))


def _inject_budget_summary_prompt(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prompt = {"role": "system", "content": _BUDGET_SUMMARY_PROMPT}
    if messages and messages[0].get("role") == "system":
        return [messages[0], prompt, *messages[1:]]
    return [prompt, *messages]


def _budget_completion_text(
    text: str,
    run: AgentRun,
    *,
    completion_mode: str,
) -> str:
    body = text or _budget_fallback(run)
    prefix = (
        _DUPLICATE_GUARD_PREFIX if completion_mode == "duplicate_tool_guard" else _BUDGET_PREFIX
    )
    if prefix in body:
        return body
    return f"{prefix}\n\n{body}"


def _budget_fallback(run: AgentRun) -> str:
    facts = [str(item) for item in run.survival_summary.get("confirmed_facts", []) if item]
    limitations = [str(item) for item in run.survival_summary.get("limitations", []) if item]
    sections: list[str] = []
    if facts:
        sections.append("已确认的证据：\n- " + "\n- ".join(facts[:10]))
    if limitations:
        sections.append("数据限制：\n- " + "\n- ".join(limitations[:10]))
    if not sections:
        sections.append("当前已保存工具证据，但模型未生成完整总结。请查看对话中的结构化数据结果。")
    return "\n\n".join(sections)


def _serialize_tool_call(call: ToolCall) -> dict[str, Any]:
    return {"id": call.id, "name": call.name, "arguments": dict(call.arguments)}


def _deserialize_tool_call(payload: dict[str, Any]) -> ToolCall:
    return ToolCall(
        id=str(payload.get("id") or ""),
        name=str(payload.get("name") or ""),
        arguments=dict(payload.get("arguments") or {}),
    )


class _RunCancelledError(Exception):
    pass
