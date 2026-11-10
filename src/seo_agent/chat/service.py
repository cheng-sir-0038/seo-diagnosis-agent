from __future__ import annotations

from collections.abc import Awaitable, Callable

from loguru import logger

from src.agent.harness.store import AgentChatHarnessStore
from src.agent.models import AgentRun
from src.agent.run_manager import RunManager


class SeoChatRecoveryService:
    """Loads durable chat runs and delegates scheduling to the API runtime."""

    def __init__(self, store: AgentChatHarnessStore, manager: RunManager) -> None:
        self.store = store
        self.manager = manager

    async def recover_active(
        self,
        schedule: Callable[[AgentRun], Awaitable[None]],
    ) -> dict[str, int]:
        recovered = 0
        blocked = 0
        for run_id in await self.store.list_recoverable_runs():
            existing = self.manager.get(run_id)
            if existing is not None and existing.task is not None and not existing.task.done():
                continue
            try:
                run = await self.store.restore_run(run_id)
                self.manager.register(run)
                await schedule(run)
                recovered += 1
            except Exception as exc:  # recovery must not prevent API startup
                blocked += 1
                await self.store.mark_recovery_blocked(run_id, str(exc))
                logger.exception(f"SEO Chat 恢复失败 | run_id={run_id} | {exc}")
        return {"recovered": recovered, "blocked": blocked}
