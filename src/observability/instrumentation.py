from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from src.observability.client import ObservabilityClient, SpanScope
from src.observability.context import get_current_context
from src.observability.models import SpanKind


@asynccontextmanager
async def observed_operation(
    client: ObservabilityClient | None,
    operation_name: str,
    *,
    kind: SpanKind,
    component_name: str | None = None,
    input_data: Any = None,
    trigger_type: str = "internal",
    metadata: dict[str, Any] | None = None,
) -> AsyncIterator[SpanScope | None]:
    """创建当前 Trace 的子 Span；没有上游 Trace 时自动创建独立 Trace。"""

    if client is None:
        yield None
        return
    await client.startup()
    context = get_current_context()
    if context and context.trace_id and context.run_id:
        scope = SpanScope(
            client=client,
            trace_id=context.trace_id,
            run_id=context.run_id,
            operation_name=operation_name,
            kind=kind,
            component_name=component_name,
            parent_span_id=context.span_id,
            input_data=input_data,
            metadata=metadata or {},
        )
        async with scope as span:
            yield span
        return
    async with client.trace(
        operation_name,
        input_data=input_data,
        metadata=metadata,
    ) as trace:
        async with trace.run(trigger_type=trigger_type) as run:
            async with run.span(
                operation_name,
                kind=kind,
                component_name=component_name,
                input_data=input_data,
                metadata=metadata,
            ) as span:
                yield span
