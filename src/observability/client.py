from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import cast, func, insert, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

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
from src.observability.sanitizer import observation_json, sanitize_payload, sanitized_json
from src.observability.schema import (
    EventRow,
    IssueOccurrenceRow,
    IssueRow,
    IssueTraceLinkRow,
    MetricPointRow,
    ModelCallRow,
    ProjectRow,
    RunRow,
    SpanRow,
    ToolCallRow,
    TraceLinkRow,
    TraceRow,
)
from src.observability.storage import ObservabilityDatabase

logger = logging.getLogger(__name__)


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
    if status == Status.INTERRUPTED:
        return f"{prefix}.interrupted"
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
        now = _now()
        async with self.database.orm_session(write=True) as session:
            await session.execute(
                pg_insert(ProjectRow).values(
                    id=_id("project"), project_key=self.project_key, name=self.project_name,
                    service_name=self.service_name, created_at=now, updated_at=now,
                ).on_conflict_do_nothing(index_elements=[ProjectRow.project_key])
            )
            project = await session.execute(
                select(ProjectRow.id).where(ProjectRow.project_key == self.project_key)
            )
            project_id = project.scalar_one()
        self._project_id = project_id
        return project_id

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
        async with self.database.orm_session(write=True) as session:
            # Serialize writers for this trace, including writers in other processes.
            locked = await session.scalar(
                select(TraceRow.id).where(TraceRow.id == trace_id).with_for_update(key_share=True)
            )
            if locked is None:
                raise KeyError(f"Trace 不存在: {trace_id}")
            last_sequence = await session.scalar(
                select(func.max(EventRow.sequence)).where(EventRow.trace_id == trace_id)
            )
            result = await session.execute(
                insert(EventRow).values(
                    id=_id("event"), trace_id=trace_id, run_id=run_id, span_id=span_id,
                    sequence=(last_sequence or 0) + 1, event_type=event_type,
                    severity=severity.value, stage=stage,
                    payload_json=sanitized_json(payload or {}),
                    occurred_at=_now(),
                ).returning(*EventRow.__table__.c)
            )
            row = dict(result.mappings().one())
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
        row = await self.database.insert_row(
            MetricPointRow, id=_id("metric"), project_id=project_id, trace_id=trace_id,
            run_id=run_id, span_id=span_id, metric_name=metric_name, value=float(value),
            unit=unit, dimensions_json=sanitized_json(dimensions or {}), recorded_at=_now(),
        )
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
        total_tokens: int | None = None,
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
        completed_at = datetime.now(UTC)
        started_at = completed_at - timedelta(milliseconds=latency_ms or 0)
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
        row = await self.database.insert_row(
            ModelCallRow, id=call_id, trace_id=trace_id, run_id=run_id, span_id=span_id,
            provider=provider, model=model, operation=operation, prompt_key=prompt_key,
            prompt_version=prompt_version, prompt_version_id=prompt_version_id,
            evaluation_case_id=evaluation_case_id, experiment_variant_id=experiment_variant_id,
            input_tokens=input_tokens, output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cached_tokens=cached_tokens, total_tokens=total_tokens, estimated_cost=estimated_cost,
            latency_ms=latency_ms, time_to_first_token_ms=time_to_first_token_ms,
            finish_reason=finish_reason, structured_output_valid=(
                None if structured_output_valid is None else int(structured_output_valid)
            ),
            retry_count=retry_count, status=status.value, error_code=error_code,
            error_message=error_message, input_summary_json=observation_json(input_summary or {}),
            output_summary_json=observation_json(output_summary or {}),
            started_at=started_at.isoformat(), completed_at=completed_at.isoformat(),
        )
        if total_tokens is not None:
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
        completed_at = datetime.now(UTC)
        started_at = completed_at - timedelta(milliseconds=latency_ms or 0)
        row = await self.database.insert_row(
            ToolCallRow, id=row_id, trace_id=trace_id, run_id=run_id, span_id=span_id,
            tool_call_id=tool_call_id, tool_name=tool_name, adapter_key=adapter_key,
            operation=operation, arguments_json=observation_json(arguments or {}),
            result_summary_json=observation_json(result_summary or {}), status=status.value,
            retry_count=retry_count, timeout_seconds=timeout_seconds, latency_ms=latency_ms,
            external_request_id=external_request_id, error_code=error_code,
            error_message=error_message, started_at=started_at.isoformat(),
            completed_at=completed_at.isoformat(),
        )
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
        context_json = sanitized_json(context or {})
        async with self.database.orm_session(write=True) as session:
            await session.execute(
                pg_insert(IssueRow).values(
                    id=_id("issue"), project_id=project_id, fingerprint=fingerprint,
                    title=title or f"{component or issue_type}: {message[:120]}",
                    issue_type=issue_type, severity=severity.value, component=component,
                    status=IssueStatus.OPEN.value, occurrence_count=0, affected_trace_count=0,
                    first_seen_at=now, last_seen_at=now, latest_context_json=context_json,
                ).on_conflict_do_nothing(index_elements=[IssueRow.project_id, IssueRow.fingerprint])
            )
            query = select(IssueRow).where(
                IssueRow.project_id == project_id, IssueRow.fingerprint == fingerprint
            ).with_for_update(key_share=True)
            issue = (await session.scalars(query)).one()
            issue_id = issue.id
            linked = await session.scalar(
                pg_insert(IssueTraceLinkRow).values(
                    issue_id=issue_id, trace_id=trace_id, created_at=now,
                ).on_conflict_do_nothing().returning(IssueTraceLinkRow.trace_id)
            )
            issue.affected_trace_count += int(linked is not None)
            issue.occurrence_count += 1
            issue.last_seen_at = now
            issue.latest_context_json = context_json
            if issue.status == IssueStatus.RESOLVED.value:
                issue.status = IssueStatus.REOPENED.value
                issue.reopened_at = now
                issue.resolved_at = None
            session.add(IssueOccurrenceRow(
                id=_id("occurrence"), issue_id=issue_id, trace_id=trace_id, run_id=run_id,
                span_id=span_id, fingerprint=fingerprint, issue_type=issue_type,
                severity=severity.value, component=component, error_code=error_code,
                message=message[:2000], context_json=context_json, occurred_at=now,
            ))
            result = {column.key: getattr(issue, column.key) for column in IssueRow.__table__.c}
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
        values = {"status": status.value}
        if status == IssueStatus.RESOLVED:
            values["resolved_at"] = _now()
        async with self.database.orm_session(write=True) as session:
            result = await session.execute(
                update(IssueRow).where(IssueRow.id == issue_id).values(**values)
                .returning(*IssueRow.__table__.c)
            )
            row = result.mappings().one_or_none()
            if row is None:
                raise KeyError(f"观测问题不存在: {issue_id}")
            return dict(row)

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
        async with self.database.orm_session(write=True) as session:
            source = await session.get(TraceRow, source_trace_id)
            target = await session.get(
                TraceRow, target_trace_id, with_for_update={"key_share": True}
            )
            if source is None:
                raise KeyError(f"来源 Trace 不存在: {source_trace_id}")
            if target is None:
                raise KeyError(f"目标 Trace 不存在: {target_trace_id}")
            if source.project_id != target.project_id:
                raise ValueError("不同 Project 的 Trace 不能直接链接")
            if target.status != Status.RUNNING.value:
                raise ValueError("目标 Trace 已结束，无法创建标准 OpenTelemetry Span Link")
            normalized_relation = relation_type.strip().upper()
            query = select(TraceLinkRow.__table__).where(
                TraceLinkRow.source_trace_id == source_trace_id,
                TraceLinkRow.target_trace_id == target_trace_id,
                TraceLinkRow.relation_type == normalized_relation,
            )
            existing = (await session.execute(query)).mappings().one_or_none()
            if existing is not None:
                return dict(existing)
            created = await session.execute(
                insert(TraceLinkRow).values(
                    id=_id("trace_link"), source_trace_id=source_trace_id,
                    target_trace_id=target_trace_id, relation_type=normalized_relation,
                    attributes_json=sanitized_json(attributes or {}), created_at=_now(),
                ).returning(*TraceLinkRow.__table__.c)
            )
            row = dict(created.mappings().one())
        await self._export("trace_link", row)
        return row

    async def get_trace(self, trace_id: str) -> dict[str, Any]:
        trace = await self.database.fetch_one(
            select(TraceRow.__table__).where(TraceRow.id == trace_id)
        )
        if trace is None:
            raise KeyError(f"Trace 不存在: {trace_id}")
        trace["runs"] = await self.database.fetch_all(
            select(RunRow.__table__).where(RunRow.trace_id == trace_id).order_by(RunRow.started_at)
        )
        spans = await self.database.fetch_all(
            select(SpanRow.__table__).where(SpanRow.trace_id == trace_id)
            .order_by(SpanRow.started_at)
        )
        by_run = {run["id"]: run for run in trace["runs"]}
        for run in trace["runs"]:
            run["spans"] = []
        for span in spans:
            if span["run_id"] in by_run:
                by_run[span["run_id"]]["spans"].append(span)
        for key, model, ordering in (
            ("events", EventRow, EventRow.sequence),
            ("model_calls", ModelCallRow, ModelCallRow.started_at),
            ("tool_calls", ToolCallRow, ToolCallRow.started_at),
        ):
            trace[key] = await self.database.fetch_all(
                select(model.__table__).where(model.trace_id == trace_id).order_by(ordering)
            )
        trace["issues"] = await self.database.fetch_all(
            select(IssueRow.__table__)
            .join(IssueTraceLinkRow, IssueTraceLinkRow.issue_id == IssueRow.id)
            .where(IssueTraceLinkRow.trace_id == trace_id).order_by(IssueRow.last_seen_at.desc())
        )
        for key, column in (
            ("outgoing_links", TraceLinkRow.source_trace_id),
            ("incoming_links", TraceLinkRow.target_trace_id)
        ):
            trace[key] = await self.database.fetch_all(
                select(TraceLinkRow.__table__).where(column == trace_id)
                .order_by(TraceLinkRow.created_at)
            )
        return trace

    async def list_traces(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        project_id = await self._ensure_project()
        query = select(TraceRow.__table__).where(TraceRow.project_id == project_id)
        if status:
            query = query.where(TraceRow.status == status)
        return await self.database.fetch_all(query.order_by(TraceRow.started_at.desc()).limit(
            max(1, min(limit, 500))
        ))

    async def application_run_stats(self, run_id: str) -> dict[str, Any]:
        filters = (
            TraceRow.project_id == await self._ensure_project(),
            cast(TraceRow.metadata_json, JSONB)["application_run_id"].astext == run_id,
        )
        elapsed = select(func.sum(TraceRow.duration_ms)).where(*filters).scalar_subquery()
        tokens = (
            select(func.sum(ModelCallRow.total_tokens))
            .join(TraceRow, TraceRow.id == ModelCallRow.trace_id)
            .where(*filters)
            .scalar_subquery()
        )
        rows = await self.database.fetch_all(select(
            elapsed.label("elapsed_ms"),
            tokens.label("total_tokens"),
        ))
        return rows[0]

    async def list_issues(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        project_id = await self._ensure_project()
        query = select(IssueRow.__table__).where(IssueRow.project_id == project_id)
        if status:
            query = query.where(IssueRow.status == status)
        return await self.database.fetch_all(query.order_by(IssueRow.last_seen_at.desc()).limit(
            max(1, min(limit, 500))
        ))

    async def metric_summary(
        self, *, metric_name: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        metric = MetricPointRow
        latest = func.max(metric.recorded_at).label("last_recorded_at")
        query = select(
            metric.metric_name, metric.unit, func.count().label("point_count"),
            func.avg(metric.value).label("average_value"),
            func.min(metric.value).label("minimum_value"),
            func.max(metric.value).label("maximum_value"), latest,
        ).where(metric.project_id == await self._ensure_project())
        if metric_name:
            query = query.where(metric.metric_name == metric_name)
        return await self.database.fetch_all(
            query.group_by(metric.metric_name, metric.unit).order_by(latest.desc())
            .limit(max(1, min(limit, 500)))
        )

    async def _export(self, entity_type: str, payload: dict[str, Any]) -> None:
        if not self.exporters:
            return
        json_fields = {
            key: value for key, value in payload.items()
            if key.endswith("_json") and isinstance(value, str)
        }
        safe_payload = sanitize_payload({
            key: value for key, value in payload.items() if key not in json_fields
        })
        for key, value in json_fields.items():
            try:
                safe_payload[key] = observation_json(json.loads(value))
            except (TypeError, ValueError):
                safe_payload[key] = "{}"
        envelope = ExportEnvelope(
            entity_type=entity_type,
            project_key=self.project_key,
            emitted_at=_now(),
            payload=safe_payload,
        )
        for exporter in self.exporters:
            try:
                await exporter.export(envelope)
            except Exception as exc:
                logger.warning("Observability export failed: %s", type(exc).__name__)


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
    status_override: Status | None = field(default=None, init=False)
    output_data: Any = field(default=None, init=False)
    _started_perf: float = field(default=0.0, init=False)
    _token: Any = field(default=None, init=False)

    async def __aenter__(self) -> TraceScope:
        project_id = await self.client._ensure_project()
        now = _now()
        self._started_perf = time.perf_counter()
        await self.client.database.execute(insert(TraceRow).values(
            id=self.trace_id, project_id=project_id, operation_name=self.operation_name,
            session_id=self.session_id, user_id=self.user_id, status=Status.RUNNING.value,
            input_json=observation_json(self.input_data or {}), otel_trace_id=self.otel_trace_id,
            otel_root_span_id=self.otel_root_span_id, metadata_json=sanitized_json(self.metadata),
            started_at=now,
        ))
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
        status = self.status_override or _status_for_exception(exc)
        duration = _duration_ms(self._started_perf)
        now = _now()
        row = await self.client.database.finish_row(
            TraceRow, self.trace_id, status=status.value,
            error_code=type(exc).__name__ if exc else None,
            error_message=str(exc)[:2000] if exc else None, completed_at=now, duration_ms=duration,
            metadata_json=sanitized_json(self.metadata),
            output_json=observation_json(self.output_data or {}),
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
        row["incoming_links"] = await self.client.database.fetch_all(
            select(
                TraceLinkRow.__table__,
                TraceRow.otel_trace_id.label("source_otel_trace_id"),
                TraceRow.otel_root_span_id.label("source_otel_root_span_id"),
            ).join(TraceRow, TraceRow.id == TraceLinkRow.source_trace_id)
            .where(TraceLinkRow.target_trace_id == self.trace_id).order_by(TraceLinkRow.created_at)
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
    status_override: Status | None = field(default=None, init=False)
    _started_perf: float = field(default=0.0, init=False)
    _token: Any = field(default=None, init=False)

    async def __aenter__(self) -> RunScope:
        project_id = await self.client._ensure_project()
        self._started_perf = time.perf_counter()
        await self.client.database.execute(insert(RunRow).values(
            id=self.run_id, trace_id=self.trace_id, thread_id=self.thread_id,
            trigger_type=self.trigger_type, attempt_no=self.attempt_no, agent_name=self.agent_name,
            workflow_name=self.workflow_name, model_provider=self.model_provider,
            model_name=self.model_name, status=Status.RUNNING.value, started_at=_now(),
            metadata_json=sanitized_json(self.metadata),
        ))
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
        status = self.status_override or _status_for_exception(exc)
        duration = _duration_ms(self._started_perf)
        row = await self.client.database.finish_row(
            RunRow, self.run_id, status=status.value,
            error_code=type(exc).__name__ if exc else None,
            error_message=str(exc)[:2000] if exc else None,
            completed_at=_now(), duration_ms=duration,
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
    output_data: Any = field(default=None, init=False)
    _started_perf: float = field(default=0.0, init=False)
    _token: Any = field(default=None, init=False)
    _captured_error: tuple[str, str] | None = field(default=None, init=False)

    async def __aenter__(self) -> SpanScope:
        project_id = await self.client._ensure_project()
        self._started_perf = time.perf_counter()
        await self.client.database.execute(insert(SpanRow).values(
            id=self.span_id, trace_id=self.trace_id, run_id=self.run_id,
            parent_span_id=self.parent_span_id,
            kind=self.kind.value, component_name=self.component_name,
            operation_name=self.operation_name,
            status=Status.RUNNING.value, input_json=observation_json(self.input_data or {}),
            metadata_json=sanitized_json(self.metadata), started_at=_now(),
        ))
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
        status = (
            _status_for_exception(exc) if exc is not None else
            Status.FAILED if self._captured_error is not None else Status.COMPLETED
        )
        duration = _duration_ms(self._started_perf)
        error_code = (
            type(exc).__name__
            if exc is not None
            else (self._captured_error[0] if self._captured_error else None)
        )
        error_message = str(effective_error)[:2000] if effective_error is not None else None
        row = await self.client.database.finish_row(
            SpanRow, self.span_id, status=status.value, error_code=error_code,
            error_message=error_message, completed_at=_now(), duration_ms=duration,
            output_json=observation_json(self.output_data or {}),
        )
        await self.metric("span.duration_ms", duration, unit="ms")
        await self.event(
            _event_for_status("span", status),
            {"duration_ms": duration, "status": status.value},
            severity=Severity.INFO if status != Status.FAILED else Severity.ERROR,
        )
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
