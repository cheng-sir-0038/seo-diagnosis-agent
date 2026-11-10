from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from src.agent.models import AgentRun, RunStatus


class RunManager:
    def __init__(self, retention_seconds: int = 300) -> None:
        self._runs: dict[str, AgentRun] = {}
        self.retention_seconds = retention_seconds

    def create_run(
        self,
        messages: list[dict],
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        thread_id: str | None = None,
        site_id: str | None = None,
        conversation_id: str | None = None,
    ) -> AgentRun:
        self.cleanup_finished()
        run = AgentRun(
            id=run_id or f"run_{uuid.uuid4().hex}",
            messages=[dict(message) for message in messages],
            task_id=task_id,
            thread_id=thread_id,
            site_id=site_id,
            conversation_id=conversation_id,
        )
        self._runs[run.id] = run
        return run

    def register(self, run: AgentRun) -> AgentRun:
        self._runs[run.id] = run
        return run

    def get(self, run_id: str) -> AgentRun | None:
        return self._runs.get(run_id)

    def cancel(self, run_id: str) -> AgentRun | None:
        run = self.get(run_id)
        if run is not None and not run.terminal:
            run.request_cancel()
            if run.task and not run.task.done():
                run.task.cancel()
            else:
                run.status = RunStatus.CANCELLED
                run.finished_at = datetime.now(UTC)
                run.emit("run.cancelled", {"status": run.status.value})
        return run

    def finish(self, run: AgentRun, status: RunStatus, error: str | None = None) -> None:
        run.status = status
        run.error = error
        run.finished_at = datetime.now(UTC)

    def cleanup_finished(self, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        removable = [
            run_id
            for run_id, run in self._runs.items()
            if run.terminal
            and run.finished_at is not None
            and (current - run.finished_at).total_seconds() >= self.retention_seconds
        ]
        for run_id in removable:
            self._runs.pop(run_id, None)
        return len(removable)

    async def shutdown(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        for run in self._runs.values():
            if run.task and not run.task.done():
                run.request_pause()
                run.task.cancel()
                tasks.append(run.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


run_manager = RunManager()
