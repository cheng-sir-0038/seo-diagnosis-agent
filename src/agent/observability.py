from __future__ import annotations

import asyncio
from typing import Any

from src.agent.models import AgentRun, RunStatus
from src.agent.providers.base import ModelProvider
from src.agent.tools.base import ToolProtocol, ToolResult, ToolRuntimeContext
from src.observability.client import ObservabilityClient, RunScope, SpanScope, TraceScope
from src.observability.instrumentation import observed_operation
from src.observability.models import Severity, SpanKind, Status


class AgentRunObserver:
    """把通用 Agent Loop 事件同步到 Agent Observability Core。"""

    def __init__(
        self,
        client: ObservabilityClient,
        *,
        operation_name: str,
        session_id: str | None = None,
        agent_name: str | None = None,
        workflow_name: str = "agent-loop",
    ) -> None:
        self.client = client
        self.operation_name = operation_name
        self.session_id = session_id
        self.agent_name = agent_name
        self.workflow_name = workflow_name
        self.trace_scope: TraceScope | None = None
        self.run_scope: RunScope | None = None
        self._initial_sequence = 0

    @property
    def trace_id(self) -> str | None:
        return self.trace_scope.trace_id if self.trace_scope else None

    @property
    def run_id(self) -> str | None:
        return self.run_scope.run_id if self.run_scope else None

    async def start(self, run: AgentRun, provider: ModelProvider) -> None:
        await self.client.startup()
        self._initial_sequence = run.sequence
        self.trace_scope = self.client.trace(
            self.operation_name,
            session_id=self.session_id,
            input_data={
                "application_run_id": run.id,
                "messages": run.messages,
            },
            metadata={
                "application_run_id": run.id,
                "agent_name": self.agent_name,
                "workflow_name": self.workflow_name,
                "resumed": run.status == RunStatus.RECOVERING,
            },
        )
        await self.trace_scope.__aenter__()
        self.run_scope = self.trace_scope.run(
            trigger_type="api",
            agent_name=self.agent_name,
            workflow_name=self.workflow_name,
            model_provider=provider.provider_name,
            model_name=provider.model_name,
            metadata={"application_run_id": run.id},
        )
        await self.run_scope.__aenter__()

    async def finish(self, run: AgentRun) -> None:
        if self.run_scope is None or self.trace_scope is None:
            return
        events = [event for event in run.events if event.sequence > self._initial_sequence]
        final = next(
            (event for event in reversed(events) if event.type == "message.completed"), None
        )
        if final is not None:
            self.trace_scope.output_data = {"content": final.payload.get("content")}
        self.trace_scope.metadata.update({
            "application_status": run.status.value,
            "completion_mode": run.completion_mode or (
                "normal" if run.status == RunStatus.COMPLETED else run.status.value
            ),
            "iterations": sum(
                1 for event in events
                if event.type == "stage.changed"
                and event.payload.get("stage") in {"calling_model", "summarizing_budget"}
            ),
            "tool_calls": sum(
                1 for event in events
                if event.type == "tool.started" and event.payload.get("reused") is not True
            ),
            "reused_tool_calls": sum(
                1 for event in events
                if event.type == "tool.completed" and event.payload.get("reused") is True
            ),
            "partial": run.partial,
        })
        exc: BaseException | None = None
        if run.status == RunStatus.FAILED:
            exc = RuntimeError(run.error or "Agent run failed")
        elif run.status == RunStatus.CANCELLED:
            exc = asyncio.CancelledError()
        elif run.status == RunStatus.RECOVERING:
            self.trace_scope.status_override = Status.INTERRUPTED
            self.run_scope.status_override = Status.INTERRUPTED
        await self.run_scope.__aexit__(type(exc) if exc else None, exc, None)
        await self.trace_scope.__aexit__(type(exc) if exc else None, exc, None)
        self.run_scope = None
        self.trace_scope = None

    async def reused_tool(
        self, tool_name: str, arguments: dict[str, Any], result: dict[str, Any]
    ) -> None:
        if self.run_scope is None:
            return
        async with self.run_scope.span(
            f"{tool_name}.reused", kind=SpanKind.TOOL, component_name=tool_name,
            input_data=arguments, metadata={"reused": True},
        ) as span:
            span.output_data = result

    async def event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        severity: Severity = Severity.INFO,
        stage: str | None = None,
    ) -> None:
        if self.trace_scope is None or self.run_scope is None:
            return
        if event_type in {"run.started", "run.completed", "run.failed", "run.cancelled"}:
            return
        await self.client.record_event(
            trace_id=self.trace_scope.trace_id,
            run_id=self.run_scope.run_id,
            event_type=event_type,
            payload=payload or {},
            severity=severity,
            stage=stage,
        )

    def span(
        self,
        operation_name: str,
        *,
        kind: SpanKind,
        component_name: str | None = None,
        input_data: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> SpanScope | None:
        if self.run_scope is None:
            return None
        return self.run_scope.span(
            operation_name,
            kind=kind,
            component_name=component_name,
            input_data=input_data,
            metadata=metadata,
        )


async def run_observed_tool(
    client: ObservabilityClient | None,
    tool: ToolProtocol,
    arguments: dict[str, Any],
    *,
    runtime: ToolRuntimeContext | None = None,
    component_name: str | None = None,
    timeout: float | None = None,
) -> ToolResult:
    """运行嵌套工具并统一记录实际调用、来源、耗时和失败状态。"""
    async with observed_operation(
        client, tool.name, kind=SpanKind.TOOL, component_name=component_name,
        input_data=arguments, metadata={"tool_execution": True},
    ) as span:
        call = tool.execute(arguments, runtime=runtime)
        result = (
            await asyncio.wait_for(call, timeout=timeout) if timeout is not None else await call
        )
        if span is not None:
            span.output_data = result.model_dump(mode="json")
        if not result.success and span is not None:
            await span.capture_exception(RuntimeError("工具返回失败"), error_code="TOOL_FAILED")
        return result
