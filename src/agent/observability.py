from __future__ import annotations

import asyncio
from typing import Any

from src.agent.models import AgentRun, RunStatus
from src.agent.providers.base import ModelProvider
from src.observability.client import ObservabilityClient, RunScope, SpanScope, TraceScope
from src.observability.models import Severity, SpanKind


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

    @property
    def trace_id(self) -> str | None:
        return self.trace_scope.trace_id if self.trace_scope else None

    @property
    def run_id(self) -> str | None:
        return self.run_scope.run_id if self.run_scope else None

    async def start(self, run: AgentRun, provider: ModelProvider) -> None:
        await self.client.startup()
        self.trace_scope = self.client.trace(
            self.operation_name,
            session_id=self.session_id,
            input_data={
                "application_run_id": run.id,
                "message_count": len(run.messages),
                "roles": [message.get("role") for message in run.messages],
            },
            metadata={"application_run_id": run.id},
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
        exc: BaseException | None = None
        if run.status == RunStatus.FAILED:
            exc = RuntimeError(run.error or "Agent run failed")
        elif run.status == RunStatus.CANCELLED:
            exc = asyncio.CancelledError()
        await self.run_scope.__aexit__(type(exc) if exc else None, exc, None)
        await self.trace_scope.__aexit__(type(exc) if exc else None, exc, None)
        self.run_scope = None
        self.trace_scope = None

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
