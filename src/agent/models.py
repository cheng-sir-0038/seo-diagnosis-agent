from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
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


STREAM_END_EVENTS = {"run.completed", "run.failed", "run.cancelled", "run.waiting_for_input"}


class HumanInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        min_length=1, max_length=3
    )
    allow_use_available: bool = False
    message: str | None = Field(default=None, min_length=1, max_length=12000)


class HumanInputAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=256)
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    action: Literal["answer", "use_available"] = "answer"
    followup_revision: int | None = Field(
        default=None, ge=0, description="确认复诊清单时提供已核对的草稿版本。",
    )


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
    _events_changed: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
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
    dispatch_state: dict[str, Any] = field(default_factory=dict)
    model_provider: str | None = None
    model_name: str | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> AgentEvent:
        event = AgentEvent(
            run_id=self.id,
            type=event_type,
            sequence=self.sequence + 1,
            payload=payload or {},
        )
        self.publish_event(event)
        return event

    def publish_event(self, event: AgentEvent) -> None:
        if event.run_id != self.id or event.sequence != self.sequence + 1:
            raise ValueError("事件必须属于当前运行并按顺序发布")
        self.sequence = event.sequence
        self.events.append(event)
        self._events_changed.set()
        self._events_changed = asyncio.Event()

    async def follow_events(self) -> AsyncIterator[AgentEvent | None]:
        index = 0
        while True:
            if index < len(self.events):
                event = self.events[index]
                index += 1
                yield event
                continue
            stopped = self.terminal or self.status in {
                RunStatus.WAITING_FOR_INPUT, RunStatus.RECOVERY_BLOCKED,
            }
            # A running task can set its status before emitting the final event.
            if stopped and (
                self.task is None
                or self.task.done()
                or (self.events and self.events[-1].type in STREAM_END_EVENTS)
            ):
                return
            try:
                await asyncio.wait_for(self._events_changed.wait(), timeout=15)
            except TimeoutError:
                yield None

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def request_pause(self) -> None:
        self.pause_requested = True
