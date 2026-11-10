from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import Link, Status, StatusCode

from src.observability.models import ExportEnvelope
from src.observability.otel_ids import ExplicitIdGenerator, span_context_from_hex


class ObservationExporter(Protocol):
    async def export(self, envelope: ExportEnvelope) -> None: ...

    async def shutdown(self) -> None: ...


class JsonlExporter:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = asyncio.Lock()

    async def export(self, envelope: ExportEnvelope) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = envelope.model_dump_json() + "\n"
        async with self._lock:
            await asyncio.to_thread(self._append, line)

    def _append(self, line: str) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(line)

    async def shutdown(self) -> None:
        return None


class OpenTelemetryExporter:
    """把内部 Trace 镜像为标准 OpenTelemetry Span。

    SQLite 是内部真源；OTLP 仅是可选外部出口。内部 `obs_trace_links`
    会在目标根 Span 创建时转换为 OpenTelemetry `Link`，而不是额外伪 Span。
    """

    def __init__(
        self,
        *,
        service_name: str,
        endpoint: str | None,
        span_exporter: SpanExporter | None = None,
    ) -> None:
        self.enabled = bool(endpoint or span_exporter)
        self._provider: TracerProvider | None = None
        self._tracer = None
        self._id_generator = ExplicitIdGenerator()
        if not self.enabled:
            return
        sink = span_exporter or OTLPSpanExporter(endpoint=endpoint)
        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name}),
            id_generator=self._id_generator,
        )
        if span_exporter is not None:
            provider.add_span_processor(SimpleSpanProcessor(sink))
        else:
            provider.add_span_processor(BatchSpanProcessor(sink))
        self._provider = provider
        self._tracer = provider.get_tracer("agent-observability-core")

    async def export(self, envelope: ExportEnvelope) -> None:
        if not self.enabled or self._tracer is None:
            return
        if envelope.entity_type == "trace_link":
            return
        await asyncio.to_thread(self._export_sync, envelope)

    def _export_sync(self, envelope: ExportEnvelope) -> None:
        if envelope.entity_type == "trace":
            self._export_trace(envelope)
            return
        self._export_generic_entity(envelope)

    def _export_trace(self, envelope: ExportEnvelope) -> None:
        assert self._tracer is not None
        payload = envelope.payload
        trace_id = str(payload["otel_trace_id"])
        root_span_id = str(payload["otel_root_span_id"])
        links = [self._build_link(item) for item in payload.get("incoming_links", [])]
        attributes = self._base_attributes(envelope)
        attributes["agent.internal_trace_id"] = str(payload["id"])
        attributes["agent.trace.status"] = str(payload.get("status") or "UNKNOWN")
        if payload.get("session_id") is not None:
            attributes["agent.session_id"] = str(payload["session_id"])
        if payload.get("user_id") is not None:
            attributes["agent.user_id"] = str(payload["user_id"])

        with self._id_generator.use(trace_id=trace_id, span_id=root_span_id):
            span = self._tracer.start_span(
                str(payload.get("operation_name") or "agent.trace"),
                context=Context(),
                attributes=attributes,
                links=links,
                start_time=self._timestamp_ns(payload.get("started_at")),
            )
        self._apply_status(span, payload)
        span.end(end_time=self._timestamp_ns(payload.get("completed_at")))

    def _export_generic_entity(self, envelope: ExportEnvelope) -> None:
        assert self._tracer is not None
        name = str(envelope.payload.get("operation_name") or envelope.entity_type)
        with self._tracer.start_as_current_span(name) as span:
            for key, value in self._base_attributes(envelope).items():
                span.set_attribute(key, value)
            for key in ("id", "trace_id", "run_id", "span_id", "status", "kind"):
                value = envelope.payload.get(key)
                if value is not None:
                    span.set_attribute(f"agent.{key}", str(value))

    @staticmethod
    def _base_attributes(envelope: ExportEnvelope) -> dict[str, str]:
        return {
            "agent.observability.entity_type": envelope.entity_type,
            "agent.project_key": envelope.project_key,
        }

    @classmethod
    def _build_link(cls, item: dict[str, Any]) -> Link:
        context = span_context_from_hex(
            str(item["source_otel_trace_id"]),
            str(item["source_otel_root_span_id"]),
            is_remote=True,
        )
        attributes: dict[str, Any] = {
            "agent.link.relation_type": str(item["relation_type"]),
            "agent.link.source_trace_id": str(item["source_trace_id"]),
        }
        raw = item.get("attributes_json") or "{}"
        try:
            business_attributes = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            business_attributes = {"raw": str(raw)}
        for key, value in business_attributes.items():
            attributes[f"agent.link.{cls._attribute_key(str(key))}"] = cls._attribute_value(value)
        return Link(context, attributes=attributes)

    @staticmethod
    def _attribute_key(value: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_")
        return normalized or "value"

    @staticmethod
    def _attribute_value(value: Any) -> Any:
        if isinstance(value, (str, bool, int, float)):
            return value
        if isinstance(value, list) and value:
            item_type = type(value[0])
            if item_type in {str, bool, int, float} and all(
                type(item) is item_type for item in value
            ):
                return value
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _timestamp_ns(value: Any) -> int | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1_000_000_000)

    @staticmethod
    def _apply_status(span: Any, payload: dict[str, Any]) -> None:
        status = str(payload.get("status") or "")
        if status == "FAILED":
            span.set_status(Status(StatusCode.ERROR, str(payload.get("error_message") or "failed")))
        elif status == "COMPLETED":
            span.set_status(Status(StatusCode.OK))

    async def force_flush(self) -> bool:
        if self._provider is None:
            return True
        return bool(await asyncio.to_thread(self._provider.force_flush))

    async def shutdown(self) -> None:
        if self._provider is not None:
            await asyncio.to_thread(self._provider.force_flush)
            await asyncio.to_thread(self._provider.shutdown)
