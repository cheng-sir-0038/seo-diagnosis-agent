from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.observability.context import reset_current_context, set_current_context
from src.observability.exporters import ObservationExporter
from src.observability.models import (
    ExportEnvelope,
    IssueStatus,
    ObservationContext,
    Severity,
    SpanKind,
    Status,
)
from src.observability.otel_ids import new_span_id_hex, new_trace_id_hex
from src.observability.sanitizer import sanitize_payload, sanitized_json
from src.observability.storage import ObservabilityDatabase


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _duration_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _status_for_exception(exc: BaseException | None) -> Status:
    if exc is None:
        return Status.COMPLETED
    if isinstance(exc, asyncio.CancelledError):
        return Status.CANCELLED
    return Status.FAILED


def _event_for_status(prefix: str, status: Status) -> str:
    if status == Status.COMPLETED:
        return f"{prefix}.completed"
    if status == Status.CANCELLED:
        return f"{prefix}.cancelled"
    return f"{prefix}.failed"


def _normalize_message(message: str) -> str:
    normalized = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", message.lower())
    normalized = re.sub(r"\b\d+\b", "<n>", normalized)
    return " ".join(normalized.split())[:500]


class ObservabilityClient:
    def __init__(
        self,
        database: ObservabilityDatabase,
        *,
        project_key: str,
        project_name: str | None = None,
        service_name: str | None = None,
        exporters: list[ObservationExporter] | None = None,
    ) -> None:
        self.database = database
        self.project_key = project_key
        self.project_name = project_name or project_key
        self.service_name = service_name
        self.exporters = exporters or []
        self._project_id: str | None = None
        self._event_lock = asyncio.Lock()
        self._issue_lock = asyncio.Lock()

    async def startup(self) -> None:
        await self.database.startup()
        await self._ensure_project()

    async def shutdown(self) -> None:
        for exporter in self.exporters:
            await exporter.shutdown()
        await self.database.shutdown()

    def trace(
        self,
        operation_name: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        input_data: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> TraceScope:
        return TraceScope(
            client=self,
            operation_name=operation_name,
            session_id=session_id,
            user_id=user_id,
            input_data=input_data,
            metadata=metadata or {},
        )

    async def _ensure_project(self) -> str:
        if self._project_id is not None:
            return self._project_id
        row = await self.database.fetch_one(
            "SELECT * FROM obs_projects WHERE project_key = ?", (self.project_key,)
        )
        if row is None:
            project_id = _id("project")
            now = _now()
            await self.database.execute(
                "INSERT INTO obs_projects(id, project_key, name, service_name, metadata_json, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, '{}', ?, ?)",
                (
                    project_id,
                    self.project_key,
                    self.project_name,
                    self.service_name,
                    now,
                    now,
                ),
            )
            self._project_id = project_id
        else:
            self._project_id = str(row["id"])
        return self._project_id

    async def record_event(
        self,
        *,
        trace_id: str,
        event_type: str,
        payload: Any = None,
        run_id: str | None = None,
        span_id: str | None = None,
        severity: Severity = Severity.INFO,
        stage: str | None = None,
    ) -> dict[str, Any]:
        async with self._event_lock:
            current = await self.database.fetch_value(
                "SELECT COALESCE(MAX(sequence), 0) FROM obs_events WHERE trace_id = ?",
                (trace_id,),
            )
            sequence = int(current or 0) + 1
            event_id = _id("event")
            occurred_at = _now()
            await self.database.execute(
                "INSERT INTO obs_events(id, trace_id, run_id, span_id, sequence, event_type, "
                "severity, stage, payload_json, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    trace_id,
                    run_id,
                    span_id,
                    sequence,
                    event_type,
                    severity.value,
                    stage,
                    sanitized_json(payload or {}),
                    occurred_at,
                ),
            )
        row = await self.database.fetch_one("SELECT * FROM obs_events WHERE id = ?", (event_id,))
        assert row is not None
        await self._export("event", row)
        return row

    async def record_metric(
        self,
        *,
        metric_name: str,
        value: float,
        unit: str | None = None,
        dimensions: dict[str, Any] | None = None,
        trace_id: str | None = None,
        run_id: str | None = None,
        span_id: str | None = None,
    ) -> dict[str, Any]:
        project_id = await self._ensure_project()
        metric_id = _id("metric")
        await self.database.execute(
            "INSERT INTO obs_metric_points(id, project_id, trace_id, run_id, span_id, metric_name, "
            "value, unit, dimensions_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                metric_id,
                project_id,
                trace_id,
                run_id,
                span_id,
                metric_name,
                float(value),
                unit,
                sanitized_json(dimensions or {}),
                _now(),
            ),
        )
        row = await self.database.fetch_one(
            "SELECT * FROM obs_metric_points WHERE id = ?", (metric_id,)
        )
        assert row is not None
        await self._export("metric", row)
        return row

    async def record_model_call(
        self,
        *,
        trace_id: str,
        provider: str,
        model: str,
        operation: str,
        run_id: str | None = None,
        span_id: str | None = None,
        prompt_key: str | None = None,
        prompt_version: str | None = None,
        prompt_version_id: str | None = None,
        evaluation_case_id: str | None = None,
        experiment_variant_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        cached_tokens: int | None = None,
        estimated_cost: float | None = None,
        latency_ms: float | None = None,
        time_to_first_token_ms: float | None = None,
        finish_reason: str | None = None,
        structured_output_valid: bool | None = None,
        retry_count: int = 0,
        status: Status = Status.COMPLETED,
        error_code: str | None = None,
        error_message: str | None = None,
        input_summary: Any = None,
        output_summary: Any = None,
    ) -> dict[str, Any]:
        call_id = _id("modelcall")
        now = _now()
        total_tokens = sum(value or 0 for value in (input_tokens, output_tokens, reasoning_tokens))
        await self.database.execute(
            "INSERT INTO obs_model_calls(id, trace_id, run_id, span_id, provider, model, "
            "operation, prompt_key, prompt_version, prompt_version_id, evaluation_case_id, "
            "experiment_variant_id, input_tokens, output_tokens, reasoning_tokens, cached_tokens, "
            "total_tokens, estimated_cost, latency_ms, time_to_first_token_ms, "
            "finish_reason, structured_output_valid, retry_count, status, error_code, "
            "error_message, input_summary_json, output_summary_json, started_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                call_id,
                trace_id,
                run_id,
                span_id,
                provider,
                model,
                operation,
                prompt_key,
                prompt_version,
                prompt_version_id,
                evaluation_case_id,
                experiment_variant_id,
                input_tokens,
                output_tokens,
                reasoning_tokens,
                cached_tokens,
                total_tokens,
                estimated_cost,
                latency_ms,
                time_to_first_token_ms,
                finish_reason,
                None if structured_output_valid is None else int(structured_output_valid),
                retry_count,
                status.value,
                error_code,
                error_message,
                sanitized_json(input_summary or {}),
                sanitized_json(output_summary or {}),
                now,
                now,
            ),
        )
        row = await self.database.fetch_one(
            "SELECT * FROM obs_model_calls WHERE id = ?", (call_id,)
        )
        assert row is not None
        await self.record_metric(
            metric_name="model.tokens.total",
            value=float(total_tokens),
            unit="tokens",
            trace_id=trace_id,
            run_id=run_id,
            span_id=span_id,
            dimensions={"provider": provider, "model": model, "operation": operation},
        )
        if latency_ms is not None:
            await self.record_metric(
                metric_name="model.latency_ms",
                value=latency_ms,
                unit="ms",
                trace_id=trace_id,
                run_id=run_id,
                span_id=span_id,
                dimensions={"provider": provider, "model": model, "operation": operation},
            )
        await self._export("model_call", row)
        return row

    async def record_tool_call(
        self,
        *,
        trace_id: str,
        tool_call_id: str,
        tool_name: str,
        operation: str,
        run_id: str | None = None,
        span_id: str | None = None,
        adapter_key: str | None = None,
        arguments: Any = None,
        result_summary: Any = None,
        status: Status = Status.COMPLETED,
        retry_count: int = 0,
        timeout_seconds: float | None = None,
        latency_ms: float | None = None,
        external_request_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        row_id = _id("toolcall")
        now = _now()
        await self.database.execute(
            "INSERT INTO obs_tool_calls(id, trace_id, run_id, span_id, tool_call_id, tool_name, "
            "adapter_key, operation, arguments_json, result_summary_json, status, retry_count, "
            "timeout_seconds, latency_ms, external_request_id, error_code, error_message, "
            "started_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?)",
            (
                row_id,
                trace_id,
                run_id,
                span_id,
                tool_call_id,
                tool_name,
                adapter_key,
                operation,
                sanitized_json(arguments or {}),
                sanitized_json(result_summary or {}),
                status.value,
                retry_count,
                timeout_seconds,
                latency_ms,
                external_request_id,
                error_code,
                error_message,
                now,
                now,
            ),
        )
        row = await self.database.fetch_one("SELECT * FROM obs_tool_calls WHERE id = ?", (row_id,))
        assert row is not None
        await self.record_metric(
            metric_name="tool.call.count",
            value=1,
            unit="calls",
            trace_id=trace_id,
            run_id=run_id,
            span_id=span_id,
            dimensions={
                "tool_name": tool_name,
                "adapter_key": adapter_key,
                "status": status.value,
            },
        )
        if latency_ms is not None:
            await self.record_metric(
                metric_name="tool.latency_ms",
                value=latency_ms,
                unit="ms",
                trace_id=trace_id,
                run_id=run_id,
                span_id=span_id,
                dimensions={"tool_name": tool_name, "adapter_key": adapter_key},
            )
        await self._export("tool_call", row)
        return row

    async def capture_issue(
        self,
        *,
        trace_id: str,
        message: str,
        issue_type: str,
        component: str | None = None,
        error_code: str | None = None,
        severity: Severity = Severity.ERROR,
        run_id: str | None = None,
        span_id: str | None = None,
        context: Any = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        project_id = await self._ensure_project()
        fingerprint_source = {
            "issue_type": issue_type,
            "component": component,
            "error_code": error_code,
            "message": _normalize_message(message),
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_source, sort_keys=True).encode("utf-8")
        ).hexdigest()
        now = _now()
        async with self._issue_lock:
            issue = await self.database.fetch_one(
                "SELECT * FROM obs_issues WHERE project_id = ? AND fingerprint = ?",
                (project_id, fingerprint),
            )
            if issue is None:
                issue_id = _id("issue")
                await self.database.execute(
                    "INSERT INTO obs_issues(id, project_id, fingerprint, title, issue_type, "
                    "severity, component, status, occurrence_count, affected_trace_count, "
                    "first_seen_at, last_seen_at, latest_context_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', 1, 1, ?, ?, ?)",
                    (
                        issue_id,
                        project_id,
                        fingerprint,
                        title or f"{component or issue_type}: {message[:120]}",
                        issue_type,
                        severity.value,
                        component,
                        now,
                        now,
                        sanitized_json(context or {}),
                    ),
                )
                await self.database.execute(
                    "INSERT INTO obs_issue_trace_links(issue_id, trace_id, created_at) "
                    "VALUES (?, ?, ?)",
                    (issue_id, trace_id, now),
                )
            else:
                issue_id = str(issue["id"])
                linked = await self.database.fetch_value(
                    "SELECT COUNT(*) FROM obs_issue_trace_links "
                    "WHERE issue_id = ? AND trace_id = ?",
                    (issue_id, trace_id),
                )
                next_status = (
                    IssueStatus.REOPENED.value
                    if issue["status"] == IssueStatus.RESOLVED.value
                    else issue["status"]
                )
                await self.database.execute(
                    "UPDATE obs_issues SET status = ?, occurrence_count = occurrence_count + 1, "
                    "affected_trace_count = affected_trace_count + ?, last_seen_at = ?, "
                    "reopened_at = CASE WHEN ? = 'REOPENED' THEN ? ELSE reopened_at END, "
                    "resolved_at = CASE WHEN ? = 'REOPENED' THEN NULL ELSE resolved_at END, "
                    "latest_context_json = ? WHERE id = ?",
                    (
                        next_status,
                        0 if linked else 1,
                        now,
                        next_status,
                        now,
                        next_status,
                        sanitized_json(context or {}),
                        issue_id,
                    ),
                )
                if not linked:
                    await self.database.execute(
                        "INSERT INTO obs_issue_trace_links(issue_id, trace_id, created_at) "
                        "VALUES (?, ?, ?)",
                        (issue_id, trace_id, now),
                    )
            occurrence_id = _id("occurrence")
            await self.database.execute(
                "INSERT INTO obs_issue_occurrences(id, issue_id, trace_id, run_id, span_id, "
                "fingerprint, issue_type, severity, component, error_code, message, context_json, "
                "occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    occurrence_id,
                    issue_id,
                    trace_id,
                    run_id,
                    span_id,
                    fingerprint,
                    issue_type,
                    severity.value,
                    component,
                    error_code,
                    message[:2000],
                    sanitized_json(context or {}),
                    now,
                ),
            )
        result = await self.database.fetch_one("SELECT * FROM obs_issues WHERE id = ?", (issue_id,))
        assert result is not None
        await self.record_event(
            trace_id=trace_id,
            run_id=run_id,
            span_id=span_id,
            event_type="issue.captured",
            severity=severity,
            payload={"issue_id": issue_id, "fingerprint": fingerprint, "error_code": error_code},
        )
        await self.record_metric(
            metric_name="issue.occurrence.count",
            value=1,
            unit="occurrences",
            trace_id=trace_id,
            run_id=run_id,
            span_id=span_id,
            dimensions={"issue_type": issue_type, "component": component, "error_code": error_code},
        )
        await self._export("issue", result)
        return result

    async def set_issue_status(self, issue_id: str, status: IssueStatus) -> dict[str, Any]:
        now = _now()
        await self.database.execute(
            "UPDATE obs_issues SET status = ?, resolved_at = CASE WHEN ? = 'RESOLVED' "
            "THEN ? ELSE resolved_at END WHERE id = ?",
            (status.value, status.value, now, issue_id),
        )
        row = await self.database.fetch_one("SELECT * FROM obs_issues WHERE id = ?", (issue_id,))
        if row is None:
            raise KeyError(f"观测问题不存在: {issue_id}")
        return row

    async def link_traces(
        self,
        *,
        source_trace_id: str,
        target_trace_id: str,
        relation_type: str,
        attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if source_trace_id == target_trace_id:
            raise ValueError("Trace 不能链接到自身")
        source = await self.database.fetch_one(
            "SELECT * FROM obs_traces WHERE id = ?",
            (source_trace_id,),
        )
        target = await self.database.fetch_one(
            "SELECT * FROM obs_traces WHERE id = ?",
            (target_trace_id,),
        )
        if source is None:
            raise KeyError(f"来源 Trace 不存在: {source_trace_id}")
        if target is None:
            raise KeyError(f"目标 Trace 不存在: {target_trace_id}")
        if source["project_id"] != target["project_id"]:
            raise ValueError("不同 Project 的 Trace 不能直接链接")
        if target["status"] != "RUNNING":
            raise ValueError("目标 Trace 已结束，无法创建标准 OpenTelemetry Span Link")
        normalized_relation = relation_type.strip().upper()
        existing = await self.database.fetch_one(
            "SELECT * FROM obs_trace_links WHERE source_trace_id = ? "
            "AND target_trace_id = ? AND relation_type = ?",
            (source_trace_id, target_trace_id, normalized_relation),
        )
        if existing is not None:
            return existing
        link_id = _id("trace_link")
        await self.database.execute(
            "INSERT INTO obs_trace_links(id, source_trace_id, target_trace_id, "
            "relation_type, attributes_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                link_id,
                source_trace_id,
                target_trace_id,
                normalized_relation,
                sanitized_json(attributes or {}),
                _now(),
            ),
        )
        row = await self.database.fetch_one(
            "SELECT * FROM obs_trace_links WHERE id = ?",
            (link_id,),
        )
        assert row is not None
        await self._export("trace_link", row)
        return row

    async def get_trace(self, trace_id: str) -> dict[str, Any]:
        trace = await self.database.fetch_one("SELECT * FROM obs_traces WHERE id = ?", (trace_id,))
        if trace is None:
            raise KeyError(f"Trace 不存在: {trace_id}")
        trace["runs"] = await self.database.fetch_all(
            "SELECT * FROM obs_runs WHERE trace_id = ? ORDER BY started_at", (trace_id,)
        )
        for run in trace["runs"]:
            run["spans"] = await self.database.fetch_all(
                "SELECT * FROM obs_spans WHERE run_id = ? ORDER BY started_at", (run["id"],)
            )
        trace["events"] = await self.database.fetch_all(
            "SELECT * FROM obs_events WHERE trace_id = ? ORDER BY sequence", (trace_id,)
        )
        trace["model_calls"] = await self.database.fetch_all(
            "SELECT * FROM obs_model_calls WHERE trace_id = ? ORDER BY started_at", (trace_id,)
        )
        trace["tool_calls"] = await self.database.fetch_all(
            "SELECT * FROM obs_tool_calls WHERE trace_id = ? ORDER BY started_at", (trace_id,)
        )
        trace["issues"] = await self.database.fetch_all(
            "SELECT i.* FROM obs_issues i JOIN obs_issue_trace_links l ON l.issue_id = i.id "
            "WHERE l.trace_id = ? ORDER BY i.last_seen_at DESC",
            (trace_id,),
        )
        trace["outgoing_links"] = await self.database.fetch_all(
            "SELECT * FROM obs_trace_links WHERE source_trace_id = ? ORDER BY created_at",
            (trace_id,),
        )
        trace["incoming_links"] = await self.database.fetch_all(
            "SELECT * FROM obs_trace_links WHERE target_trace_id = ? ORDER BY created_at",
            (trace_id,),
        )
        return trace

    async def list_traces(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        project_id = await self._ensure_project()
        if status:
            return await self.database.fetch_all(
                "SELECT * FROM obs_traces WHERE project_id = ? AND status = ? "
                "ORDER BY started_at DESC LIMIT ?",
                (project_id, status, max(1, min(limit, 500))),
            )
        return await self.database.fetch_all(
            "SELECT * FROM obs_traces WHERE project_id = ? ORDER BY started_at DESC LIMIT ?",
            (project_id, max(1, min(limit, 500))),
        )

    async def list_issues(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        project_id = await self._ensure_project()
        if status:
            return await self.database.fetch_all(
                "SELECT * FROM obs_issues WHERE project_id = ? AND status = ? "
                "ORDER BY last_seen_at DESC LIMIT ?",
                (project_id, status, max(1, min(limit, 500))),
            )
        return await self.database.fetch_all(
            "SELECT * FROM obs_issues WHERE project_id = ? ORDER BY last_seen_at DESC LIMIT ?",
            (project_id, max(1, min(limit, 500))),
        )

    async def metric_summary(
        self, *, metric_name: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        project_id = await self._ensure_project()
        params: tuple[Any, ...]
        where = "WHERE project_id = ?"
        params = (project_id,)
        if metric_name:
            where += " AND metric_name = ?"
            params += (metric_name,)
        params += (max(1, min(limit, 500)),)
        return await self.database.fetch_all(
            "SELECT metric_name, unit, COUNT(*) AS point_count, AVG(value) AS average_value, "
            "MIN(value) AS minimum_value, MAX(value) AS maximum_value, "
            "MAX(recorded_at) AS last_recorded_at FROM obs_metric_points "
            f"{where} GROUP BY metric_name, unit ORDER BY last_recorded_at DESC LIMIT ?",
            params,
        )

    async def _export(self, entity_type: str, payload: dict[str, Any]) -> None:
        if not self.exporters:
            return
        envelope = ExportEnvelope(
            entity_type=entity_type,
            project_key=self.project_key,
            emitted_at=_now(),
            payload=sanitize_payload(payload),
        )
        for exporter in self.exporters:
            await exporter.export(envelope)


@dataclass
class TraceScope:
    client: ObservabilityClient
    operation_name: str
    session_id: str | None
    user_id: str | None
    input_data: Any
    metadata: dict[str, Any]
    trace_id: str = field(default_factory=lambda: _id("trace"))
    otel_trace_id: str = field(default_factory=new_trace_id_hex)
    otel_root_span_id: str = field(default_factory=new_span_id_hex)
    _started_perf: float = field(default=0.0, init=False)
    _token: Any = field(default=None, init=False)

    async def __aenter__(self) -> TraceScope:
        project_id = await self.client._ensure_project()
        now = _now()
        self._started_perf = time.perf_counter()
        await self.client.database.execute(
            "INSERT INTO obs_traces(id, project_id, operation_name, session_id, user_id, status, "
            "input_json, otel_trace_id, otel_root_span_id, metadata_json, started_at) "
            "VALUES (?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?, ?, ?)",
            (
                self.trace_id,
                project_id,
                self.operation_name,
                self.session_id,
                self.user_id,
                sanitized_json(self.input_data or {}),
                self.otel_trace_id,
                self.otel_root_span_id,
                sanitized_json(self.metadata),
                now,
            ),
        )
        self._token = set_current_context(
            ObservationContext(
                project_id=project_id,
                project_key=self.client.project_key,
                trace_id=self.trace_id,
            )
        )
        await self.client.record_event(trace_id=self.trace_id, event_type="trace.started")
        return self

    async def __aexit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        status = _status_for_exception(exc)
        duration = _duration_ms(self._started_perf)
        now = _now()
        await self.client.database.execute(
            "UPDATE obs_traces SET status = ?, error_code = ?, error_message = ?, "
            "completed_at = ?, duration_ms = ? WHERE id = ?",
            (
                status.value,
                type(exc).__name__ if exc else None,
                str(exc)[:2000] if exc else None,
                now,
                duration,
                self.trace_id,
            ),
        )
        if exc is not None and not isinstance(exc, asyncio.CancelledError):
            await self.client.capture_issue(
                trace_id=self.trace_id,
                message=str(exc),
                issue_type=type(exc).__name__,
                component=self.operation_name,
                error_code=type(exc).__name__,
            )
        await self.client.record_metric(
            metric_name="trace.duration_ms",
            value=duration,
            unit="ms",
            trace_id=self.trace_id,
            dimensions={"operation_name": self.operation_name, "status": status.value},
        )
        await self.client.record_event(
            trace_id=self.trace_id,
            event_type=_event_for_status("trace", status),
            severity=Severity.INFO if status != Status.FAILED else Severity.ERROR,
            payload={"duration_ms": duration, "status": status.value},
        )
        row = await self.client.database.fetch_one(
            "SELECT * FROM obs_traces WHERE id = ?", (self.trace_id,)
        )
        assert row is not None
        row["incoming_links"] = await self.client.database.fetch_all(
            "SELECT l.*, source.otel_trace_id AS source_otel_trace_id, "
            "source.otel_root_span_id AS source_otel_root_span_id "
            "FROM obs_trace_links l "
            "JOIN obs_traces source ON source.id = l.source_trace_id "
            "WHERE l.target_trace_id = ? ORDER BY l.created_at",
            (self.trace_id,),
        )
        await self.client._export("trace", row)
        if self._token is not None:
            reset_current_context(self._token)
        return False

    def run(
        self,
        *,
        trigger_type: str,
        thread_id: str | None = None,
        attempt_no: int = 1,
        agent_name: str | None = None,
        workflow_name: str | None = None,
        model_provider: str | None = None,
        model_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunScope:
        return RunScope(
            client=self.client,
            trace_id=self.trace_id,
            trigger_type=trigger_type,
            thread_id=thread_id,
            attempt_no=attempt_no,
            agent_name=agent_name,
            workflow_name=workflow_name,
            model_provider=model_provider,
            model_name=model_name,
            metadata=metadata or {},
        )


@dataclass
class RunScope:
    client: ObservabilityClient
    trace_id: str
    trigger_type: str
    thread_id: str | None
    attempt_no: int
    agent_name: str | None
    workflow_name: str | None
    model_provider: str | None
    model_name: str | None
    metadata: dict[str, Any]
    run_id: str = field(default_factory=lambda: _id("run"))
    _started_perf: float = field(default=0.0, init=False)
    _token: Any = field(default=None, init=False)

    async def __aenter__(self) -> RunScope:
        project_id = await self.client._ensure_project()
        self._started_perf = time.perf_counter()
        await self.client.database.execute(
            "INSERT INTO obs_runs(id, trace_id, thread_id, trigger_type, attempt_no, agent_name, "
            "workflow_name, model_provider, model_name, status, started_at, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?)",
            (
                self.run_id,
                self.trace_id,
                self.thread_id,
                self.trigger_type,
                self.attempt_no,
                self.agent_name,
                self.workflow_name,
                self.model_provider,
                self.model_name,
                _now(),
                sanitized_json(self.metadata),
            ),
        )
        self._token = set_current_context(
            ObservationContext(
                project_id=project_id,
                project_key=self.client.project_key,
                trace_id=self.trace_id,
                run_id=self.run_id,
            )
        )
        await self.client.record_event(
            trace_id=self.trace_id, run_id=self.run_id, event_type="run.started"
        )
        return self

    async def __aexit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        status = _status_for_exception(exc)
        duration = _duration_ms(self._started_perf)
        await self.client.database.execute(
            "UPDATE obs_runs SET status = ?, error_code = ?, error_message = ?, completed_at = ?, "
            "duration_ms = ? WHERE id = ?",
            (
                status.value,
                type(exc).__name__ if exc else None,
                str(exc)[:2000] if exc else None,
                _now(),
                duration,
                self.run_id,
            ),
        )
        await self.client.record_metric(
            metric_name="run.duration_ms",
            value=duration,
            unit="ms",
            trace_id=self.trace_id,
            run_id=self.run_id,
            dimensions={"trigger_type": self.trigger_type, "status": status.value},
        )
        await self.client.record_event(
            trace_id=self.trace_id,
            run_id=self.run_id,
            event_type=_event_for_status("run", status),
            severity=Severity.INFO if status != Status.FAILED else Severity.ERROR,
            payload={"duration_ms": duration, "status": status.value},
        )
        row = await self.client.database.fetch_one(
            "SELECT * FROM obs_runs WHERE id = ?", (self.run_id,)
        )
        assert row is not None
        await self.client._export("run", row)
        if self._token is not None:
            reset_current_context(self._token)
        return False

    def span(
        self,
        operation_name: str,
        *,
        kind: SpanKind = SpanKind.INTERNAL,
        component_name: str | None = None,
        parent_span_id: str | None = None,
        input_data: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> SpanScope:
        return SpanScope(
            client=self.client,
            trace_id=self.trace_id,
            run_id=self.run_id,
            operation_name=operation_name,
            kind=kind,
            component_name=component_name,
            parent_span_id=parent_span_id,
            input_data=input_data,
            metadata=metadata or {},
        )


@dataclass
class SpanScope:
    client: ObservabilityClient
    trace_id: str
    run_id: str
    operation_name: str
    kind: SpanKind
    component_name: str | None
    parent_span_id: str | None
    input_data: Any
    metadata: dict[str, Any]
    span_id: str = field(default_factory=lambda: _id("span"))
    _started_perf: float = field(default=0.0, init=False)
    _token: Any = field(default=None, init=False)
    _captured_error: tuple[str, str] | None = field(default=None, init=False)

    async def __aenter__(self) -> SpanScope:
        project_id = await self.client._ensure_project()
        self._started_perf = time.perf_counter()
        await self.client.database.execute(
            "INSERT INTO obs_spans(id, trace_id, run_id, parent_span_id, kind, component_name, "
            "operation_name, status, input_json, metadata_json, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?)",
            (
                self.span_id,
                self.trace_id,
                self.run_id,
                self.parent_span_id,
                self.kind.value,
                self.component_name,
                self.operation_name,
                sanitized_json(self.input_data or {}),
                sanitized_json(self.metadata),
                _now(),
            ),
        )
        self._token = set_current_context(
            ObservationContext(
                project_id=project_id,
                project_key=self.client.project_key,
                trace_id=self.trace_id,
                run_id=self.run_id,
                span_id=self.span_id,
            )
        )
        await self.event("span.started", {"kind": self.kind.value})
        return self

    async def __aexit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        effective_error = exc or (
            RuntimeError(self._captured_error[1]) if self._captured_error is not None else None
        )
        status = Status.COMPLETED if effective_error is None else Status.FAILED
        duration = _duration_ms(self._started_perf)
        error_code = (
            type(exc).__name__
            if exc is not None
            else (self._captured_error[0] if self._captured_error else None)
        )
        error_message = str(effective_error)[:2000] if effective_error is not None else None
        await self.client.database.execute(
            "UPDATE obs_spans SET status = ?, error_code = ?, error_message = ?, completed_at = ?, "
            "duration_ms = ? WHERE id = ?",
            (status.value, error_code, error_message, _now(), duration, self.span_id),
        )
        await self.metric("span.duration_ms", duration, unit="ms")
        await self.event(
            "span.completed" if status == Status.COMPLETED else "span.failed",
            {"duration_ms": duration, "status": status.value},
            severity=Severity.INFO if status == Status.COMPLETED else Severity.ERROR,
        )
        row = await self.client.database.fetch_one(
            "SELECT * FROM obs_spans WHERE id = ?", (self.span_id,)
        )
        assert row is not None
        await self.client._export("span", row)
        if self._token is not None:
            reset_current_context(self._token)
        return False

    async def event(
        self,
        event_type: str,
        payload: Any = None,
        *,
        severity: Severity = Severity.INFO,
        stage: str | None = None,
    ) -> dict[str, Any]:
        return await self.client.record_event(
            trace_id=self.trace_id,
            run_id=self.run_id,
            span_id=self.span_id,
            event_type=event_type,
            payload=payload,
            severity=severity,
            stage=stage,
        )

    async def metric(
        self,
        metric_name: str,
        value: float,
        *,
        unit: str | None = None,
        dimensions: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.client.record_metric(
            metric_name=metric_name,
            value=value,
            unit=unit,
            dimensions=dimensions,
            trace_id=self.trace_id,
            run_id=self.run_id,
            span_id=self.span_id,
        )

    async def model_call(self, **kwargs: Any) -> dict[str, Any]:
        return await self.client.record_model_call(
            trace_id=self.trace_id,
            run_id=self.run_id,
            span_id=self.span_id,
            **kwargs,
        )

    async def tool_call(self, **kwargs: Any) -> dict[str, Any]:
        return await self.client.record_tool_call(
            trace_id=self.trace_id,
            run_id=self.run_id,
            span_id=self.span_id,
            **kwargs,
        )

    async def capture_exception(
        self,
        exc: BaseException,
        *,
        issue_type: str | None = None,
        component: str | None = None,
        error_code: str | None = None,
        severity: Severity = Severity.ERROR,
        context: Any = None,
    ) -> dict[str, Any]:
        code = error_code or type(exc).__name__
        self._captured_error = (code, str(exc))
        return await self.client.capture_issue(
            trace_id=self.trace_id,
            run_id=self.run_id,
            span_id=self.span_id,
            message=str(exc),
            issue_type=issue_type or type(exc).__name__,
            component=component or self.component_name or self.operation_name,
            error_code=code,
            severity=severity,
            context=context,
        )
