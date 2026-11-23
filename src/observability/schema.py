from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """PostgreSQL metadata shared by observations and prompt management."""


class ProjectRow(Base):
    __tablename__ = "obs_projects"
    __table_args__ = (
        UniqueConstraint("project_key"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_key: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    service_name: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text, server_default="{}")
    created_at: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(Text)


class TraceRow(Base):
    __tablename__ = "obs_traces"
    __table_args__ = (
        Index("idx_obs_traces_project_time", "project_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("obs_projects.id", ondelete="RESTRICT"),
    )
    operation_name: Mapped[str] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    input_json: Mapped[str] = mapped_column(Text, server_default="{}")
    output_json: Mapped[str] = mapped_column(Text, server_default="{}")
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[str] = mapped_column(Text)
    completed_at: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    otel_trace_id: Mapped[str] = mapped_column(Text)
    otel_root_span_id: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text, server_default="{}")


class TraceLinkRow(Base):
    __tablename__ = "obs_trace_links"
    __table_args__ = (
        Index("idx_obs_trace_links_target", "target_trace_id", "created_at"),
        Index("idx_obs_trace_links_source", "source_trace_id", "created_at"),
        UniqueConstraint("source_trace_id", "target_trace_id", "relation_type"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_trace_id: Mapped[str] = mapped_column(
        Text, ForeignKey("obs_traces.id", ondelete="RESTRICT"),
    )
    target_trace_id: Mapped[str] = mapped_column(
        Text, ForeignKey("obs_traces.id", ondelete="RESTRICT"),
    )
    relation_type: Mapped[str] = mapped_column(Text)
    attributes_json: Mapped[str] = mapped_column(Text, server_default="{}")
    created_at: Mapped[str] = mapped_column(Text)


class RunRow(Base):
    __tablename__ = "obs_runs"
    __table_args__ = (
        Index("idx_obs_runs_trace", "trace_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    trace_id: Mapped[str] = mapped_column(Text, ForeignKey("obs_traces.id", ondelete="RESTRICT"))
    thread_id: Mapped[str | None] = mapped_column(Text)
    trigger_type: Mapped[str] = mapped_column(Text)
    attempt_no: Mapped[int] = mapped_column(Integer, server_default="1")
    agent_name: Mapped[str | None] = mapped_column(Text)
    workflow_name: Mapped[str | None] = mapped_column(Text)
    model_provider: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    stop_reason: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[str] = mapped_column(Text)
    completed_at: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[str] = mapped_column(Text, server_default="{}")


class SpanRow(Base):
    __tablename__ = "obs_spans"
    __table_args__ = (
        Index("idx_obs_spans_trace", "trace_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    trace_id: Mapped[str] = mapped_column(Text, ForeignKey("obs_traces.id", ondelete="RESTRICT"))
    run_id: Mapped[str | None] = mapped_column(Text, ForeignKey("obs_runs.id", ondelete="RESTRICT"))
    parent_span_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("obs_spans.id", ondelete="RESTRICT"),
    )
    kind: Mapped[str] = mapped_column(Text)
    component_name: Mapped[str | None] = mapped_column(Text)
    operation_name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    input_json: Mapped[str] = mapped_column(Text, server_default="{}")
    output_json: Mapped[str] = mapped_column(Text, server_default="{}")
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[str] = mapped_column(Text)
    completed_at: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[str] = mapped_column(Text, server_default="{}")


class EventRow(Base):
    __tablename__ = "obs_events"
    __table_args__ = (
        Index("idx_obs_events_trace", "trace_id", "sequence"),
        UniqueConstraint("trace_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    trace_id: Mapped[str] = mapped_column(Text, ForeignKey("obs_traces.id", ondelete="RESTRICT"))
    run_id: Mapped[str | None] = mapped_column(Text, ForeignKey("obs_runs.id", ondelete="RESTRICT"))
    span_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("obs_spans.id", ondelete="RESTRICT"),
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    stage: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, server_default="{}")
    occurred_at: Mapped[str] = mapped_column(Text)


class ModelCallRow(Base):
    __tablename__ = "obs_model_calls"
    __table_args__ = (
        Index("idx_obs_model_calls_trace", "trace_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    trace_id: Mapped[str] = mapped_column(Text, ForeignKey("obs_traces.id", ondelete="RESTRICT"))
    run_id: Mapped[str | None] = mapped_column(Text, ForeignKey("obs_runs.id", ondelete="RESTRICT"))
    span_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("obs_spans.id", ondelete="RESTRICT"),
    )
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    operation: Mapped[str] = mapped_column(Text)
    prompt_key: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    prompt_version_id: Mapped[str | None] = mapped_column(Text)
    evaluation_case_id: Mapped[str | None] = mapped_column(Text)
    experiment_variant_id: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    time_to_first_token_ms: Mapped[float | None] = mapped_column(Float)
    finish_reason: Mapped[str | None] = mapped_column(Text)
    structured_output_valid: Mapped[int | None] = mapped_column(Integer)
    retry_count: Mapped[int] = mapped_column(Integer, server_default="0")
    status: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    input_summary_json: Mapped[str] = mapped_column(Text, server_default="{}")
    output_summary_json: Mapped[str] = mapped_column(Text, server_default="{}")
    started_at: Mapped[str] = mapped_column(Text)
    completed_at: Mapped[str] = mapped_column(Text)


class ToolCallRow(Base):
    __tablename__ = "obs_tool_calls"
    __table_args__ = (
        Index("idx_obs_tool_calls_trace", "trace_id", "started_at"),
        UniqueConstraint("trace_id", "tool_call_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    trace_id: Mapped[str] = mapped_column(Text, ForeignKey("obs_traces.id", ondelete="RESTRICT"))
    run_id: Mapped[str | None] = mapped_column(Text, ForeignKey("obs_runs.id", ondelete="RESTRICT"))
    span_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("obs_spans.id", ondelete="RESTRICT"),
    )
    tool_call_id: Mapped[str] = mapped_column(Text)
    tool_name: Mapped[str] = mapped_column(Text)
    adapter_key: Mapped[str | None] = mapped_column(Text)
    operation: Mapped[str] = mapped_column(Text)
    arguments_json: Mapped[str] = mapped_column(Text, server_default="{}")
    result_summary_json: Mapped[str] = mapped_column(Text, server_default="{}")
    status: Mapped[str] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, server_default="0")
    timeout_seconds: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    external_request_id: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[str] = mapped_column(Text)
    completed_at: Mapped[str] = mapped_column(Text)


class IssueRow(Base):
    __tablename__ = "obs_issues"
    __table_args__ = (
        Index("idx_obs_issues_project_status", "project_id", "status", "last_seen_at"),
        UniqueConstraint("project_id", "fingerprint"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("obs_projects.id", ondelete="RESTRICT"),
    )
    fingerprint: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    issue_type: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    component: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    occurrence_count: Mapped[int] = mapped_column(Integer, server_default="1")
    affected_trace_count: Mapped[int] = mapped_column(Integer, server_default="1")
    first_seen_at: Mapped[str] = mapped_column(Text)
    last_seen_at: Mapped[str] = mapped_column(Text)
    resolved_at: Mapped[str | None] = mapped_column(Text)
    reopened_at: Mapped[str | None] = mapped_column(Text)
    latest_context_json: Mapped[str] = mapped_column(Text, server_default="{}")


class IssueOccurrenceRow(Base):
    __tablename__ = "obs_issue_occurrences"
    __table_args__ = (
        Index("idx_obs_occurrences_issue", "issue_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    issue_id: Mapped[str] = mapped_column(Text, ForeignKey("obs_issues.id", ondelete="RESTRICT"))
    trace_id: Mapped[str] = mapped_column(Text, ForeignKey("obs_traces.id", ondelete="RESTRICT"))
    run_id: Mapped[str | None] = mapped_column(Text, ForeignKey("obs_runs.id", ondelete="RESTRICT"))
    span_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("obs_spans.id", ondelete="RESTRICT"),
    )
    fingerprint: Mapped[str] = mapped_column(Text)
    issue_type: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    component: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    context_json: Mapped[str] = mapped_column(Text, server_default="{}")
    occurred_at: Mapped[str] = mapped_column(Text)


class IssueTraceLinkRow(Base):
    __tablename__ = "obs_issue_trace_links"

    issue_id: Mapped[str] = mapped_column(
        Text, ForeignKey("obs_issues.id", ondelete="RESTRICT"), primary_key=True,
    )
    trace_id: Mapped[str] = mapped_column(
        Text, ForeignKey("obs_traces.id", ondelete="RESTRICT"), primary_key=True,
    )
    created_at: Mapped[str] = mapped_column(Text)


class MetricPointRow(Base):
    __tablename__ = "obs_metric_points"
    __table_args__ = (
        Index("idx_obs_metrics_name_time", "metric_name", "recorded_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("obs_projects.id", ondelete="RESTRICT"),
    )
    trace_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("obs_traces.id", ondelete="RESTRICT"),
    )
    run_id: Mapped[str | None] = mapped_column(Text, ForeignKey("obs_runs.id", ondelete="RESTRICT"))
    span_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("obs_spans.id", ondelete="RESTRICT"),
    )
    metric_name: Mapped[str] = mapped_column(Text)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(Text)
    dimensions_json: Mapped[str] = mapped_column(Text, server_default="{}")
    recorded_at: Mapped[str] = mapped_column(Text)
