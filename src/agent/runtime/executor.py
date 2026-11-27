from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from src.agent.content_sanitizer import sanitize_assistant_content
from src.agent.harness.protocols import AgentRunPersistence
from src.agent.harness.summary import compact_model_context
from src.agent.models import AgentEvent, AgentRun, HumanInputRequest, HumanInputState, RunStatus
from src.agent.observability import AgentRunObserver
from src.agent.providers.base import ModelProvider, ToolCall
from src.agent.providers.errors import ModelRequestError
from src.agent.tools.base import ToolResult, ToolRuntimeContext, resolve_render_hint
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
_SPECIALIST_DISPATCHED = "agent.dispatched"
_SPECIALIST_COMPLETED = "agent.completed"
_SPECIALIST_FAILED = "agent.failed"
_SPECIALIST_LIFECYCLE = frozenset({
    _SPECIALIST_DISPATCHED, _SPECIALIST_COMPLETED, _SPECIALIST_FAILED, "run.waiting_for_input",
})
_active_specialist: ContextVar[tuple[str, dict[str, Any]] | None] = ContextVar(
    "active_specialist", default=None,
)


def _specialist_payload(run: AgentRun) -> dict[str, Any]:
    scope = _active_specialist.get()
    return scope[1] if scope is not None and scope[0] == run.id else {}


class AgentExecutor:
    def __init__(
        self,
        provider: ModelProvider | None,
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

    async def run(
        self, run: AgentRun, *, workflow: Callable[[AgentRun], Awaitable[None]] | None = None
    ) -> None:
        context_token = set_current_context(get_current_context()) if self.observer else None
        resumed = run.status == RunStatus.RECOVERING
        run.error = None
        run.finished_at = None
        run.partial = False
        run.completion_mode = None
        try:
            if self.observer is not None:
                await self.observer.start(run, self.provider)
            run.status = RunStatus.RUNNING
            await self.emit(
                run,
                "run.resumed" if resumed else "run.started",
                {"status": run.status.value, "iteration": run.iteration},
            )
            if self.max_iterations <= 0:
                raise RuntimeError("最大模型循环次数必须大于 0")
            await (workflow or self.run_turns)(run)
        except asyncio.CancelledError:
            if run.pause_requested:
                if run.status != RunStatus.WAITING_FOR_INPUT:
                    run.status = RunStatus.RECOVERING
                stage = (
                    "waiting_for_input" if run.status == RunStatus.WAITING_FOR_INPUT else "paused"
                )
                await self.checkpoint(run, stage=stage)
            elif not run.terminal:
                run.status = RunStatus.CANCELLED
                run.finished_at = datetime.now(UTC)
                await self.checkpoint(run, stage="cancelled")
                if self.persistence is not None:
                    await self.persistence.finalize(run)
                await self.emit(run, "run.cancelled", {"status": run.status.value})
            raise
        except _RunCancelledError:
            run.status = RunStatus.CANCELLED
            run.finished_at = datetime.now(UTC)
            await self.checkpoint(run, stage="cancelled")
            if self.persistence is not None:
                await self.persistence.finalize(run)
            await self.emit(run, "run.cancelled", {"status": run.status.value})
        except Exception as exc:
            run.status = RunStatus.FAILED
            run.error = str(exc)
            run.finished_at = datetime.now(UTC)
            try:
                await self.checkpoint(run, stage="failed")
                if self.persistence is not None:
                    await self.persistence.finalize(run)
            except Exception as checkpoint_exc:
                logger.warning("Failed to persist failed run: %s", type(checkpoint_exc).__name__)
            try:
                await self.emit(
                    run,
                    "run.failed",
                    {"status": run.status.value, "message": str(exc)},
                    severity=Severity.ERROR,
                )
            except Exception as event_exc:
                logger.warning("Failed to persist failure event: %s", type(event_exc).__name__)
        finally:
            try:
                if self.persistence is not None and run.status != RunStatus.WAITING_FOR_INPUT:
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

    async def checkpoint(self, run: AgentRun, *, stage: str) -> None:
        await self._save_checkpoint(
            run, run.messages, iteration=run.iteration, tool_count=run.tool_count, stage=stage,
        )

    @asynccontextmanager
    async def specialist(
        self, run: AgentRun, agent: str, *, sender: str, profile: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        """Attribute receipts to one specialist and reuse its unfinished ID on resume."""
        previous = next(
            (event for event in reversed(run.events)
             if event.type in _SPECIALIST_LIFECYCLE and event.payload.get("agent") == agent
             and event.payload.get("agent_run_id")),
            None,
        )
        agent_run_id = (
            previous.payload["agent_run_id"]
            if previous is not None and previous.type != _SPECIALIST_COMPLETED
            else f"agent_{uuid.uuid4().hex}"
        )
        payload: dict[str, Any] = {"agent": agent, "agent_run_id": agent_run_id}
        if profile:
            payload["agent_profile"] = profile
        token = _active_specialist.set((run.id, payload))
        try:
            if previous is None or previous.type != _SPECIALIST_DISPATCHED:
                await self.emit(run, _SPECIALIST_DISPATCHED, {**payload, "from": sender})
            yield agent_run_id
        except (asyncio.CancelledError, _RunCancelledError):
            raise
        except Exception:
            try:
                await self.emit(run, _SPECIALIST_FAILED, payload, severity=Severity.ERROR)
            except Exception as event_exc:
                logger.warning("Failed to persist specialist failure: %s", type(event_exc).__name__)
            raise
        finally:
            _active_specialist.reset(token)

    async def complete_specialist(self, run: AgentRun) -> None:
        if payload := _specialist_payload(run):
            await self.emit(run, _SPECIALIST_COMPLETED, payload)

    async def required_tool(self, run: AgentRun, call: ToolCall) -> ToolResult:
        """执行工作流指定的取证步骤，恢复时沿用同一调用的成功回执。"""
        for event in reversed(run.events):
            if event.type == "tool.completed" and event.payload.get("tool_call_id") == call.id:
                return ToolResult.model_validate(event.payload["result"])
        self._check_cancel(run)
        if run.tool_count >= self.max_tool_calls:
            raise RuntimeError("超过最大工具调用次数")
        run.tool_count += 1
        replies: list[dict[str, Any]] = []
        await self._execute_tool(run, call, replies)
        return ToolResult.model_validate_json(replies[-1]["content"])

    async def run_tool_workflow(
        self, run: AgentRun, call: ToolCall, *, evidence_answer: Callable[[AgentRun], str | None],
    ) -> None:
        """Start a planned tool step and use the normal checkpoints and input receipts."""
        executed = any(
            event.type in {"tool.completed", "tool.failed"}
            and event.payload.get("tool_call_id") == call.id
            for event in run.events
        )
        if run.human_input is None and not run.pending_tool_calls and not executed:
            run.messages.append(_assistant_tool_call(call))
            run.pending_tool_calls = [_serialize_tool_call(call)]
            await self.checkpoint(run, stage="tools_pending")
        await self.run_turns(run, evidence_answer=evidence_answer)

    async def run_turns(
        self, run: AgentRun, *,
        evidence_answer: Callable[[AgentRun], str | None] | None = None,
        extra_user_context: str | None = None,
    ) -> None:
        messages = [dict(message) for message in run.messages]
        iteration = int(run.iteration)
        tool_count = int(run.tool_count)
        if run.human_input and run.human_input.answer is not None:
            await self.emit(run, "input.accepted", {
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
                messages.append(_assistant_tool_call(call))
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
            if not evidence_answer and not extra_user_context:
                continuation = self._planned_continuation(run, tool_count < self.max_tool_calls)
                if isinstance(continuation, str):
                    await self._complete(run, messages, continuation, iteration, tool_count)
                    return
                if isinstance(continuation, ToolCall):
                    messages.append(_assistant_tool_call(continuation))
                    run.pending_tool_calls = [
                        {**_serialize_tool_call(continuation), "planned": True},
                    ]
                    await self._save_checkpoint(
                        run, messages, iteration=iteration, tool_count=tool_count,
                        stage="tools_pending",
                    )
                    iteration, tool_count = await self._execute_pending_tools(
                        run, messages, iteration=iteration, tool_count=tool_count,
                    )
                    if await self._wait_for_input(run, messages):
                        return
                    iteration = min(iteration, self.max_iterations - 1)
                    continue
            if evidence_answer and (answer := evidence_answer(run)) is not None:
                await self._complete(run, messages, answer, iteration, tool_count)
                return
            provider = self.provider
            if provider is None:
                raise RuntimeError("此本地工作流未配置文本模型。")
            repeated_tool_guard = _has_reused_tool_result(run)
            budget_exhausted = iteration >= self.max_iterations - 1
            force_summary = budget_exhausted or repeated_tool_guard
            completion_mode = "duplicate_tool_guard" if repeated_tool_guard else "budget_limit"
            stage = "summarizing_budget" if force_summary else "calling_model"
            run.iteration = iteration
            run.tool_count = tool_count
            await self.emit(
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
            tool_choice = (
                "none" if force_summary or extra_user_context
                else "required" if evidence_answer else "auto"
            )
            tool_schemas = self.tools.schemas()
            capture_content = self.observer is None or self.observer.record_content
            model_input = {
                "messages": model_messages, "tools": tool_schemas, "tool_choice": tool_choice
            } if capture_content else {"message_count": len(model_messages)}
            if extra_user_context:
                user_index = next(
                    index for index in range(len(model_messages) - 1, -1, -1)
                    if model_messages[index]["role"] == "user"
                )
                question = model_messages[user_index]["content"]
                model_messages[user_index] = {
                    **model_messages[user_index], "content": f"{question}\n\n{extra_user_context}",
                }
            model_span = (
                self.observer.span(
                    f"model.iteration.{iteration + 1}",
                    kind=SpanKind.MODEL,
                    component_name=provider.provider_name,
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
                run.model_provider = provider.provider_name
                run.model_name = provider.model_name
                async for chunk in provider.stream(
                    model_messages,
                    tool_schemas,
                    tool_choice=tool_choice,
                ):
                    self._check_cancel(run)
                    if chunk.model:
                        run.model_name = chunk.model
                    if chunk.text:
                        if first_token_ms is None:
                            first_token_ms = round((time.perf_counter() - started) * 1000, 3)
                        assistant_text.append(chunk.text)
                        if not force_summary and evidence_answer is None:
                            await self.emit(run, "message.delta", {"delta": chunk.text})
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
                        error = exc if capture_content or isinstance(exc, ModelRequestError) else (
                            RuntimeError("资料问答模型请求失败")
                        )
                        await record_model_failure(
                            span, error,
                            component=f"model.{provider.provider_name}",
                            provider=provider.provider_name,
                            model=run.model_name or "unknown",
                            operation="stream",
                            latency_ms=round((time.perf_counter() - started) * 1000, 3),
                            time_to_first_token_ms=first_token_ms,
                            finish_reason=finish_reason,
                            input_summary=model_input,
                            output_summary=(
                                {"content": "".join(assistant_text)} if capture_content else {}
                            ),
                        )
                        if not capture_content:
                            raise error from None
                        raise
                    await span.model_call(
                        provider=provider.provider_name,
                        model=run.model_name or "unknown",
                        operation="stream",
                        **model_usage_fields({key: usage[key] for key in usage_keys}),
                        latency_ms=round((time.perf_counter() - started) * 1000, 3),
                        time_to_first_token_ms=first_token_ms,
                        finish_reason=finish_reason,
                        input_summary=model_input,
                        output_summary={
                            **({
                                "content": "".join(assistant_text),
                                "tool_calls": [
                                    {
                                        "id": call.id,
                                        "name": call.name,
                                        "arguments": call.arguments,
                                    }
                                    for call in pending_calls
                                ],
                            } if capture_content else {}),
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
            if evidence_answer:
                final_text = "本轮没有取得可复核的数据回执。请核对问题条件，或稍后重新查询。"
            elif force_summary:
                final_text = _budget_completion_text(
                    final_text,
                    run,
                    completion_mode=completion_mode,
                )
                run.partial = True
                run.completion_mode = completion_mode
            elif not final_text:
                final_text = "模型未生成可展示的文本，请查看已经获取的数据证据。"
            await self._complete(run, messages, final_text, iteration + 1, tool_count)
            return
        raise RuntimeError("模型循环未能收敛")

    async def _complete(
        self, run: AgentRun, messages: list[dict[str, Any]], text: str,
        iteration: int, tool_count: int,
    ) -> None:
        messages.append({"role": "assistant", "content": text})
        run.messages = [dict(message) for message in messages]
        await self.emit(run, "message.completed", {"content": text})
        run.status = RunStatus.COMPLETED
        run.finished_at = datetime.now(UTC)
        await self._save_checkpoint(
            run, messages, iteration=iteration, tool_count=tool_count, stage="completed",
        )
        if self.persistence is not None:
            await self.persistence.finalize(run)
        await self.complete_specialist(run)
        await self.emit(run, "run.completed", {
            "status": run.status.value,
            "partial": run.partial,
            "completion_mode": run.completion_mode or "normal",
        })

    def _planned_continuation(self, run: AgentRun, can_call: bool) -> ToolCall | str | None:
        """Let an opt-in workflow schedule its next step or deliver a receipt-based answer."""
        for tool in self.tools.values():
            continuation = getattr(tool, "planned_continuation", None)
            if continuation and (step := continuation(run, can_call, self.tools.names())) is not None:
                return step
        return None

    async def _recover_planned_tool(self, run: AgentRun, call: ToolCall) -> dict[str, Any] | None:
        for event in reversed(run.events):
            if event.payload.get("tool_call_id") != call.id:
                continue
            if event.type in {"tool.completed", "tool.failed"}:
                # The receipt can be persisted before its message checkpoint.
                return event.payload["result"]
            if event.type == "tool.started":
                # An interrupted provider call has unknown outcome. Do not replay it automatically.
                result = ToolResult(
                    success=False, error="工具调用已中断，结果未确认。", error_code="INTERRUPTED",
                ).model_dump(mode="json")
                await self.emit(run, "tool.failed", {
                    "tool_call_id": call.id, "name": call.name, "result": result,
                    "error_code": "INTERRUPTED", "latency_ms": None,
                })
                return result
        return None

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
            pending_call = run.pending_tool_calls[0]
            call = _deserialize_tool_call(pending_call)
            planned = bool(pending_call.get("planned"))
            receipt = await self._recover_planned_tool(run, call) if planned else None
            cached = None if planned else _find_cached_success(messages, call)
            if receipt is not None:
                tool_count += 1
                messages.append({
                    "role": "tool", "tool_call_id": call.id, "name": call.name,
                    "content": json.dumps(receipt, ensure_ascii=False),
                })
            elif cached is not None:
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
                await self._execute_tool(run, call, messages, planned=planned)
            payload = json.loads(messages[-1]["content"])
            request = (payload.get("metadata") or {}).get("human_input")
            waiting = run.human_input
            if request is not None and (waiting is None or waiting.answer is not None):
                validated = HumanInputRequest.model_validate(request)
                run.human_input = HumanInputState(
                    **validated.model_dump(), request_id=f"input_{uuid.uuid4().hex}",
                    tool_name=call.name,
                    tool_arguments=call.arguments, tool_result=payload,
                )
            elif (
                waiting and waiting.answer is not None and waiting.tool_name == call.name
                and waiting.tool_arguments == call.arguments
            ):
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
        text = pending.message or "还需要你补充以下信息：\n\n" + "\n".join(
            f"{index + 1}. {question}" for index, question in enumerate(pending.questions)
        )
        if not messages or messages[-1].get("content") != text:
            messages.append({"role": "assistant", "content": text})
        waiting = {
            "request_id": pending.request_id, "questions": pending.questions,
            "allow_use_available": pending.allow_use_available,
            **_specialist_payload(run),
        }
        events = (
            AgentEvent(run_id=run.id, sequence=run.sequence + 1, type="message.completed",
                       payload={"content": text}),
            AgentEvent(run_id=run.id, sequence=run.sequence + 2, type="run.waiting_for_input",
                       payload=waiting),
        )
        async with observed_operation(
            self.observer.client if self.observer else None,
            "wait_for_user_input", kind=SpanKind.INTERNAL,
            input_data={"request_id": pending.request_id, "questions": pending.questions},
        ) as span:
            if self.persistence is not None:
                run.survival_summary = await self.persistence.save_waiting(
                    run, messages=messages, events=events,
                )
            if span:
                span.output_data = {
                    "application_run_id": run.id, "status": RunStatus.WAITING_FOR_INPUT.value,
                }
        run.messages = messages
        run.status = RunStatus.WAITING_FOR_INPUT
        for event in events:
            run.publish_event(event)
        if self.observer:
            await self.observer.event("message.completed", {"content": text})
            await self.observer.event("run.waiting_for_input", waiting, stage="waiting_for_input")
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
        await self.emit(
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
        await self.emit(run, "tool.completed", event_payload)
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
        *,
        planned: bool = False,
    ) -> None:
        try:
            tool = self.tools.get(call.name)
        except KeyError:
            tool = None
        await self.emit(
            run,
            "tool.started",
            {"tool_call_id": call.id, "name": call.name, "arguments": call.arguments},
        )
        started = time.perf_counter()
        timeout = getattr(tool, "timeout_seconds", None) or self.tool_timeout
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
                    ToolResult(success=False, error=f"未知工具: {call.name}",
                               error_code="UNKNOWN_TOOL").model_dump(mode="json"),
                    Status.FAILED,
                    "UNKNOWN_TOOL",
                    f"未知工具: {call.name}",
                )
            try:
                metadata = {}
                if intent := run.dispatch_state.get("intent"):
                    metadata["user_intent"] = intent
                pending = run.human_input
                if (
                    pending and pending.answer is not None and pending.tool_name == call.name
                    and pending.tool_arguments == call.arguments
                ):
                    metadata["human_input_resume"] = pending.model_dump(mode="json")
                if prepare_turn := getattr(tool, "turn_runtime_metadata", None):
                    metadata.update(prepare_turn(run))
                if planned and (prepare := getattr(tool, "planned_runtime_metadata", None)):
                    metadata.update(prepare(run))
                result = await asyncio.wait_for(
                    tool.execute(
                        call.arguments,
                        runtime=ToolRuntimeContext(
                            run_id=run.id,
                            task_id=run.task_id,
                            thread_id=run.thread_id,
                            site_id=run.site_id,
                            conversation_id=run.conversation_id,
                            metadata=metadata,
                        ),
                    ),
                    timeout=timeout,
                )
                payload = result.model_dump(mode="json")
                if not result.success:
                    payload["error_code"] = result.error_code or "TOOL_FAILED"
                return (
                    payload,
                    Status.COMPLETED if result.success else Status.FAILED,
                    None if result.success else payload["error_code"],
                    result.error,
                )
            except Exception as exc:
                failure = ToolResult.from_exception(exc)
                return (
                    failure.model_dump(mode="json"),
                    Status.FAILED,
                    failure.error_code,
                    str(exc) or failure.error,
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
                    timeout_seconds=timeout,
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
        await self.emit(
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

    async def emit(
        self,
        run: AgentRun,
        event_type: str,
        payload: dict[str, Any],
        *,
        severity: Severity = Severity.INFO,
        stage: str | None = None,
    ) -> None:
        if event_type.startswith(("tool.", "skill.")):
            payload = {**payload, **_specialist_payload(run)}
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


def _assistant_tool_call(call: ToolCall) -> dict[str, Any]:
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": call.id, "type": "function", "function": {
            "name": call.name,
            "arguments": json.dumps(call.arguments, ensure_ascii=False),
        },
    }]}


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
