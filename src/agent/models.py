from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECOVERING = "recovering"
    RECOVERY_BLOCKED = "recovery_blocked"
    WAITING_FOR_INPUT = "waiting_for_input"


class HumanInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        min_length=1, max_length=3
    )
    allow_use_available: bool = False


class HumanInputAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=256)
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    action: Literal["answer", "use_available"] = "answer"


class HumanInputState(HumanInputRequest):
    request_id: str
    tool_name: str
    tool_arguments: dict[str, Any]
    tool_result: dict[str, Any]
    answer: str | None = None
    action: Literal["answer", "use_available"] = "answer"


class AgentMessage(BaseModel):
    role: str
    content: str | None = None
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class AgentEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex}")
    run_id: str
    type: str
    sequence: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class AgentRun:
    id: str
    messages: list[dict[str, Any]]
    status: RunStatus = RunStatus.PENDING
    events: list[AgentEvent] = field(default_factory=list)
    queue: asyncio.Queue[AgentEvent] = field(default_factory=asyncio.Queue)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    error: str | None = None
    sequence: int = 0
    task_id: str | None = None
    thread_id: str | None = None
    site_id: str | None = None
    conversation_id: str | None = None
    iteration: int = 0
    tool_count: int = 0
    pending_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    survival_summary: dict[str, Any] = field(default_factory=dict)
    completion_mode: str | None = None
    partial: bool = False
    pause_requested: bool = False
    human_input: HumanInputState | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> AgentEvent:
        self.sequence += 1
        event = AgentEvent(
            run_id=self.id,
            type=event_type,
            sequence=self.sequence,
            payload=payload or {},
        )
        self.events.append(event)
        self.queue.put_nowait(event)
        return event

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def request_pause(self) -> None:
        self.pause_requested = True
