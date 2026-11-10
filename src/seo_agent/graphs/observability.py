from __future__ import annotations

from typing import Any

from src.observability.client import ObservabilityClient, SpanScope
from src.observability.models import Severity, SpanKind


class LangGraphObserver:
    def __init__(
        self,
        client: ObservabilityClient,
        *,
        trace_id: str,
        run_id: str,
    ) -> None:
        self.client = client
        self.trace_id = trace_id
        self.run_id = run_id

    def node_span(self, node_name: str, *, input_data: Any = None) -> SpanScope:
        return SpanScope(
            client=self.client,
            trace_id=self.trace_id,
            run_id=self.run_id,
            operation_name=node_name,
            kind=SpanKind.LANGGRAPH_NODE,
            component_name="langgraph",
            parent_span_id=None,
            input_data=input_data,
            metadata={"node_name": node_name},
        )

    async def event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        severity: Severity = Severity.INFO,
        stage: str | None = None,
        span_id: str | None = None,
    ) -> None:
        await self.client.record_event(
            trace_id=self.trace_id,
            run_id=self.run_id,
            span_id=span_id,
            event_type=event_type,
            payload=payload or {},
            severity=severity,
            stage=stage,
        )
