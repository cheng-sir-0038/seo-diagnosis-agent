from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from threading import local

from opentelemetry.sdk.trace.id_generator import IdGenerator, RandomIdGenerator
from opentelemetry.trace import SpanContext, TraceFlags, TraceState

_TRACE_BYTES = 16
_SPAN_BYTES = 8


def new_trace_id_hex() -> str:
    while True:
        value = secrets.token_hex(_TRACE_BYTES)
        if int(value, 16) != 0:
            return value


def new_span_id_hex() -> str:
    while True:
        value = secrets.token_hex(_SPAN_BYTES)
        if int(value, 16) != 0:
            return value


def is_valid_trace_id(value: str | None) -> bool:
    if value is None or len(value) != 32:
        return False
    try:
        return int(value, 16) != 0
    except ValueError:
        return False


def is_valid_span_id(value: str | None) -> bool:
    if value is None or len(value) != 16:
        return False
    try:
        return int(value, 16) != 0
    except ValueError:
        return False


def span_context_from_hex(
    trace_id: str,
    span_id: str,
    *,
    is_remote: bool,
) -> SpanContext:
    if not is_valid_trace_id(trace_id):
        raise ValueError(f"无效 OpenTelemetry TraceId: {trace_id}")
    if not is_valid_span_id(span_id):
        raise ValueError(f"无效 OpenTelemetry SpanId: {span_id}")
    return SpanContext(
        trace_id=int(trace_id, 16),
        span_id=int(span_id, 16),
        is_remote=is_remote,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=TraceState(),
    )


class ExplicitIdGenerator(IdGenerator):
    """为内部 Trace 导出提供已持久化的 OTel ID，其余 Span 使用随机 ID。"""

    def __init__(self) -> None:
        self._fallback = RandomIdGenerator()
        self._local = local()

    @contextmanager
    def use(self, *, trace_id: str, span_id: str) -> Iterator[None]:
        if not is_valid_trace_id(trace_id):
            raise ValueError(f"无效 OpenTelemetry TraceId: {trace_id}")
        if not is_valid_span_id(span_id):
            raise ValueError(f"无效 OpenTelemetry SpanId: {span_id}")
        previous_trace = getattr(self._local, "trace_id", None)
        previous_span = getattr(self._local, "span_id", None)
        self._local.trace_id = int(trace_id, 16)
        self._local.span_id = int(span_id, 16)
        try:
            yield
        finally:
            self._local.trace_id = previous_trace
            self._local.span_id = previous_span

    def generate_trace_id(self) -> int:
        value = getattr(self._local, "trace_id", None)
        if value is not None:
            self._local.trace_id = None
            return int(value)
        return self._fallback.generate_trace_id()

    def generate_span_id(self) -> int:
        value = getattr(self._local, "span_id", None)
        if value is not None:
            self._local.span_id = None
            return int(value)
        return self._fallback.generate_span_id()

    def is_trace_id_random(self) -> bool:
        return True
