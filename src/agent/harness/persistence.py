from __future__ import annotations

from typing import Any

from src.agent.harness.store import AgentChatHarnessStore
from src.agent.models import AgentEvent, AgentRun


class StoreRunPersistence:
    def __init__(self, store: AgentChatHarnessStore) -> None:
        self.store = store

    async def persist_event(self, run: AgentRun, event: AgentEvent) -> None:
        await self.store.persist_event(run, event)

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
    ) -> dict[str, Any]:
        return await self.store.save_checkpoint(
            run,
            messages=messages,
            iteration=iteration,
            tool_count=tool_count,
            pending_tool_calls=pending_tool_calls,
            stage=stage,
            token_usage=token_usage,
        )

    async def finalize(self, run: AgentRun) -> None:
        await self.store.finalize_run(run)
