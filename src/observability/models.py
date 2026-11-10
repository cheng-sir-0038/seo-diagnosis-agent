from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Status(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class SpanKind(StrEnum):
    INTERNAL = "INTERNAL"
    MODEL = "MODEL"
    TOOL = "TOOL"
    LANGGRAPH_NODE = "LANGGRAPH_NODE"
    ADAPTER = "ADAPTER"
    HTTP = "HTTP"
    DATABASE = "DATABASE"
    SCHEDULER = "SCHEDULER"
    APPROVAL = "APPROVAL"
    CUSTOM = "CUSTOM"


class TraceRelation(StrEnum):
    FOLLOW_UP = "FOLLOW_UP"
    CHILD_PROCESS = "CHILD_PROCESS"
    RETRY = "RETRY"
    DERIVED_FROM = "DERIVED_FROM"


class Severity(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class IssueStatus(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    RESOLVED = "RESOLVED"
    REOPENED = "REOPENED"
    IGNORED = "IGNORED"
    ARCHIVED = "ARCHIVED"


class ObservationContext(BaseModel):
    project_id: str
    project_key: str
    trace_id: str | None = None
    run_id: str | None = None
    span_id: str | None = None


class ExportEnvelope(BaseModel):
    schema_version: str = "1.0"
    entity_type: str
    project_key: str
    emitted_at: str
    payload: dict[str, Any] = Field(default_factory=dict)
