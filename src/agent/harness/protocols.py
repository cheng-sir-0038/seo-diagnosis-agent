from __future__ import annotations

from typing import Any, Protocol

from src.agent.models import AgentEvent, AgentRun


class AgentRunPersistence(Protocol):
    async def persist_event(self, run: AgentRun, event: AgentEvent) -> None: ...

    async def save_checkpoint(
        self,
        run: AgentRun,
        *,
        messages: list[dict[str, Any]],
        iteration: int,
        tool_count: int,
        pending_tool_calls: list[dict[str, Any]],
        stage: str,
        token_usage: dict[str, int],
    ) -> dict[str, Any]: ...

    async def finalize(self, run: AgentRun) -> None: ...
