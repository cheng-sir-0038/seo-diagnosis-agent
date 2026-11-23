from __future__ import annotations

import asyncio
import os
from contextlib import AbstractAsyncContextManager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import func, select
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.core.config import settings
from src.core.database import postgres_url, prepare_schema


class CheckpointManager:
    def __init__(self, url: str | URL | None = None, *, schema: str | None = None) -> None:
        self.schema = settings.database_schema if url is None and schema is None else schema
        self.url = postgres_url(url, schema=self.schema)
        self.saver: AsyncPostgresSaver | None = None
        self._context: AbstractAsyncContextManager[AsyncPostgresSaver] | None = None
        self._lifecycle_lock = asyncio.Lock()

    async def startup(self) -> None:
        async with self._lifecycle_lock:
            if self.saver is not None:
                return
            os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
            dsn = self.url.set(drivername="postgresql").render_as_string(hide_password=False)
            context = AsyncPostgresSaver.from_conn_string(dsn)
            saver = await context.__aenter__()
            try:
                await self._setup(saver)
            except BaseException:
                await context.__aexit__(None, None, None)
                raise
            self._context = context
            self.saver = saver

    async def _setup(self, saver: AsyncPostgresSaver) -> None:
        # LangGraph builds indexes concurrently. Wait outside a DB statement to avoid
        # holding a snapshot that would block the worker currently building the index.
        engine = create_async_engine(self.url, poolclass=NullPool)
        try:
            if self.schema:
                async with engine.begin() as connection:
                    await prepare_schema(connection, self.schema)
            async with engine.connect() as connection:
                guard = await connection.execution_options(isolation_level="AUTOCOMMIT")
                lock = select(
                    func.pg_try_advisory_lock(
                        func.hashtext("seo-agent-checkpoints"),
                        func.hashtext(self.schema or func.coalesce(func.current_schema(), "")),
                    )
                )
                while not await guard.scalar(lock):
                    await asyncio.sleep(0.1)
                await saver.setup()
        finally:
            await engine.dispose()

    async def shutdown(self) -> None:
        async with self._lifecycle_lock:
            context = self._context
            self.saver = None
            self._context = None
            if context is not None:
                await context.__aexit__(None, None, None)

    def require_saver(self) -> AsyncPostgresSaver:
        if self.saver is None:
            raise RuntimeError("CheckpointManager 尚未启动")
        return self.saver
