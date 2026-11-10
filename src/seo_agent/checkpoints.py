from __future__ import annotations

import os
from contextlib import AbstractAsyncContextManager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


class CheckpointManager:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.saver: AsyncSqliteSaver | None = None
        self._context: AbstractAsyncContextManager[AsyncSqliteSaver] | None = None

    async def startup(self) -> None:
        if self.saver is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
        context = AsyncSqliteSaver.from_conn_string(str(self.path))
        saver = await context.__aenter__()
        await saver.setup()
        self._context = context
        self.saver = saver

    async def shutdown(self) -> None:
        context = self._context
        self.saver = None
        self._context = None
        if context is not None:
            await context.__aexit__(None, None, None)

    def require_saver(self) -> AsyncSqliteSaver:
        if self.saver is None:
            raise RuntimeError("CheckpointManager 尚未启动")
        return self.saver
