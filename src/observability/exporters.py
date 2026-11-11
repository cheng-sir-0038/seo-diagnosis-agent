from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
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
from opentelemetry.trace import Link, NonRecordingSpan, Status, StatusCode, set_span_in_context

from src.observability.models import ExportEnvelope
from src.observability.otel_ids import ExplicitIdGenerator, span_context_from_hex
from src.observability.storage import ObservabilityDatabase


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
        database: ObservabilityDatabase | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.enabled = bool(endpoint or span_exporter)
        self.database = database
        self._provider: TracerProvider | None = None
        self._tracer = None
        self._id_generator = ExplicitIdGenerator()
        if not self.enabled:
            return
        sink = span_exporter or OTLPSpanExporter(endpoint=endpoint, headers=headers)
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
        if envelope.entity_type not in {"trace", "span", "model_call", "tool_call"}:
            return
        if envelope.entity_type != "trace":
            if self.database is None:
                return
            trace = await self.database.fetch_one(
                "SELECT otel_trace_id, otel_root_span_id FROM obs_traces WHERE id = ?",
                (envelope.payload.get("trace_id"),),
            )
            if trace is None:
                return
            envelope = envelope.model_copy(update={"payload": {**envelope.payload, **trace}})
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
        attributes["langfuse.observation.type"] = "span"
        attributes.update(self._trace_metadata(payload))
        attributes.update(self._io_attributes(payload, "input_json", "output_json"))

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
        payload = envelope.payload
        trace_id = str(payload["otel_trace_id"])
        parent_id = (
            payload.get("parent_span_id")
            if envelope.entity_type == "span" else payload.get("span_id")
        )
        parent_span_id = (
            self._span_id(str(parent_id)) if parent_id else str(payload["otel_root_span_id"])
        )
        parent = span_context_from_hex(trace_id, parent_span_id, is_remote=False)
        context = set_span_in_context(NonRecordingSpan(parent), Context())
        attributes = self._base_attributes(envelope)
        for key in ("id", "trace_id", "run_id", "status", "kind", "error_code", "component_name"):
            if payload.get(key) is not None:
                attributes[f"agent.{key}"] = str(payload[key])
        name = str(
            payload.get("tool_name") or payload.get("operation_name")
            or payload.get("operation") or envelope.entity_type
        )
        if envelope.entity_type == "model_call":
            attributes.update(self._model_attributes(payload))
            attributes.update(
                self._io_attributes(payload, "input_summary_json", "output_summary_json")
            )
        else:
            metadata = self._json_object(payload.get("metadata_json"))
            attributes["langfuse.observation.type"] = (
                "tool"
                if envelope.entity_type == "tool_call" or metadata.get("tool_execution") is True
                else "span"
            )
            if envelope.entity_type == "tool_call":
                attributes.update(self._tool_metadata(payload))
                attributes.update(
                    self._io_attributes(payload, "arguments_json", "result_summary_json")
                )
            else:
                attributes.update(self._io_attributes(payload, "input_json", "output_json"))
            if metadata.get("reused") is True:
                attributes["langfuse.observation.metadata.reused"] = True
        span_id = self._span_id(str(payload["id"]))
        with self._id_generator.use(trace_id=trace_id, span_id=span_id):
            span = self._tracer.start_span(
                name, context=context, attributes=attributes,
                start_time=self._timestamp_ns(payload.get("started_at")),
            )
        self._apply_status(span, payload)
        span.end(end_time=self._timestamp_ns(payload.get("completed_at")))

    @staticmethod
    def _span_id(value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        return digest if int(digest, 16) else "0000000000000001"

    @classmethod
    def _model_attributes(cls, payload: dict[str, Any]) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            "langfuse.observation.type": "generation",
            "gen_ai.request.model": str(payload["model"]),
            "gen_ai.provider.name": str(payload["provider"]),
        }
        usage: dict[str, int] = {}
        input_tokens = payload.get("input_tokens")
        cached = payload.get("cached_tokens")
        if input_tokens is not None:
            if cached is not None and 0 <= cached <= input_tokens:
                usage["input"] = input_tokens - cached
                usage["cache_read_input_tokens"] = cached
            else:
                usage["input"] = input_tokens
        if payload.get("output_tokens") is not None:
            usage["output"] = payload["output_tokens"]
        if payload.get("total_tokens") is not None:
            usage["total"] = payload["total_tokens"]
        if usage:
            attributes["langfuse.observation.usage_details"] = json.dumps(usage)
        for key in (
            "reasoning_tokens", "prompt_key", "prompt_version", "finish_reason",
            "time_to_first_token_ms",
        ):
            if payload.get(key) is not None:
                attributes[f"langfuse.observation.metadata.{key}"] = str(payload[key])
        first_token_ms = payload.get("time_to_first_token_ms")
        if isinstance(first_token_ms, (int, float)) and 0 <= first_token_ms <= 86_400_000:
            try:
                started = datetime.fromisoformat(str(payload["started_at"]).replace("Z", "+00:00"))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
                attributes["langfuse.observation.completion_start_time"] = (
                    started + timedelta(milliseconds=first_token_ms)
                ).isoformat()
            except (KeyError, TypeError, ValueError, OverflowError):
                pass
        if payload.get("operation") == "stream":
            summary = cls._json_object(payload.get("output_summary_json"))
            for key, allowed in (
                ("decision", {"call_tools", "forced_summary", "final_answer"}),
                ("decision_reason", {"budget_limit", "duplicate_tool_guard"}),
                ("tool_choice", {"auto", "none"}),
            ):
                value = summary.get(key)
                if isinstance(value, str) and value in allowed:
                    attributes[f"langfuse.observation.metadata.{key}"] = value
            iteration = summary.get("iteration")
            if type(iteration) is int and 0 <= iteration <= 100_000:
                attributes["langfuse.observation.metadata.iteration"] = iteration
            selected = summary.get("selected_tools")
            if isinstance(selected, list) and all(
                isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", item)
                for item in selected
            ):
                attributes["langfuse.observation.metadata.selected_tools"] = selected[:100]
        return attributes

    @classmethod
    def _trace_metadata(cls, payload: dict[str, Any]) -> dict[str, Any]:
        metadata = cls._json_object(payload.get("metadata_json"))
        attributes: dict[str, Any] = {}
        for key in ("agent_name", "workflow_name"):
            value = metadata.get(key)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value):
                attributes[f"langfuse.observation.metadata.{key}"] = value
        for key, allowed in (
            ("application_status", {
                "pending", "running", "completed", "failed", "cancelled", "recovering",
                "recovery_blocked",
            }),
            ("completion_mode", {
                "normal", "budget_limit", "duplicate_tool_guard", "failed", "cancelled",
                "recovering", "recovery_blocked",
            }),
        ):
            value = metadata.get(key)
            if isinstance(value, str) and value in allowed:
                attributes[f"langfuse.observation.metadata.{key}"] = value
        for key in ("iterations", "tool_calls", "reused_tool_calls"):
            value = metadata.get(key)
            if type(value) is int and 0 <= value <= 100_000:
                attributes[f"langfuse.observation.metadata.{key}"] = value
        for key in ("partial", "resumed"):
            if type(metadata.get(key)) is bool:
                attributes[f"langfuse.observation.metadata.{key}"] = metadata[key]
        return attributes

    @classmethod
    def _tool_metadata(cls, payload: dict[str, Any]) -> dict[str, Any]:
        arguments = cls._json_object(payload.get("arguments_json"))
        result = cls._json_object(payload.get("result_summary_json"))
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        attributes: dict[str, Any] = {}
        for key in ("force_refresh", "include_performance"):
            if type(arguments.get(key)) is bool:
                attributes[f"langfuse.observation.metadata.{key}"] = arguments[key]
        strategy = arguments.get("strategy")
        if isinstance(strategy, str) and strategy in {"mobile", "desktop"}:
            attributes["langfuse.observation.metadata.strategy"] = strategy
        operation = payload.get("operation")
        if isinstance(operation, str) and operation in {"execute", "query", "reuse"}:
            attributes["langfuse.observation.metadata.operation"] = operation
        if type(result.get("success")) is bool:
            attributes["langfuse.observation.metadata.result_success"] = result["success"]
        workflow_status = data.get("workflow_status")
        if isinstance(workflow_status, str) and workflow_status in {"complete", "partial"}:
            attributes["langfuse.observation.metadata.workflow_status"] = workflow_status
        provenance = data.get("provenance")
        if isinstance(provenance, dict) and type(provenance.get("cached")) is bool:
            attributes["langfuse.observation.metadata.cached"] = provenance["cached"]
        return attributes

    @staticmethod
    def _json_object(raw: Any) -> dict[str, Any]:
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _io_attributes(payload: dict[str, Any], input_key: str, output_key: str) -> dict[str, str]:
        attributes = {}
        for key, destination in (
            (input_key, "langfuse.observation.input"),
            (output_key, "langfuse.observation.output"),
        ):
            value = payload.get(key)
            if isinstance(value, str) and value not in {"", "{}", "null"}:
                attributes[destination] = value
        return attributes

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
        for key in (
            "monitoring_plan_id", "monitoring_check_id", "check_type", "task_id", "delivery_id"
        ):
            if key in business_attributes:
                attributes[f"agent.link.{key}"] = cls._attribute_value(business_attributes[key])
        return Link(context, attributes=attributes)

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
            span.set_status(Status(StatusCode.ERROR, str(payload.get("error_code") or "failed")))
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
