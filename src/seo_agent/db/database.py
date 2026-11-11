from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from src.seo_agent.db.schema import SCHEMA_SQL, SCHEMA_VERSION


class SeoAgentDatabase:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._connection: aiosqlite.Connection | None = None
        self._autocommit = True

    @property
    def started(self) -> bool:
        return self._connection is not None

    async def startup(self) -> None:
        if self._connection is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.executescript(SCHEMA_SQL)
        await self._ensure_column(connection, "task_prd_versions", "prompt_version_id", "TEXT")
        await self._ensure_column(connection, "task_prd_versions", "source_trace_id", "TEXT")
        await self._ensure_column(connection, "delivery_reviews", "prompt_version_id", "TEXT")
        await self._ensure_column(connection, "delivery_reviews", "source_trace_id", "TEXT")
        await self._ensure_column(connection, "publish_records", "approval_action_id", "TEXT")
        await self._ensure_column(connection, "publish_records", "source_trace_id", "TEXT")
        await self._ensure_column(connection, "monitoring_plans", "origin_trace_id", "TEXT")
        await self._ensure_column(connection, "monitoring_checks", "trace_id", "TEXT")
        await connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
            "VALUES (?, datetime('now'))",
            (SCHEMA_VERSION,),
        )
        await connection.commit()
        self._connection = connection

    @staticmethod
    async def _ensure_column(
        connection: aiosqlite.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        async with connection.execute(f"PRAGMA table_info({table})") as cursor:
            columns = {str(row[1]) for row in await cursor.fetchall()}
        if column not in columns:
            await connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    async def shutdown(self) -> None:
        if self._connection is None:
            return
        await self._connection.close()
        self._connection = None

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("SeoAgentDatabase 尚未启动")
        return self._connection

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        connection = self._require_connection()
        await connection.execute(sql, params)
        if self._autocommit:
            await connection.commit()

    async def execute_many(self, sql: str, params: list[tuple[Any, ...]]) -> None:
        connection = self._require_connection()
        await connection.executemany(sql, params)
        if self._autocommit:
            await connection.commit()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[SeoAgentDatabase]:
        """在独立连接中原子执行一组现有业务服务调用。"""
        self._require_connection()
        if not self._autocommit:
            raise RuntimeError("不支持嵌套业务事务")
        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        session = SeoAgentDatabase(self.path)
        session._connection, session._autocommit = connection, False
        try:
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute("BEGIN IMMEDIATE")
            yield session
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise
        finally:
            session._connection = None
            await connection.close()

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        connection = self._require_connection()
        async with connection.execute(sql, params) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        connection = self._require_connection()
        async with connection.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_value(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        connection = self._require_connection()
        async with connection.execute(sql, params) as cursor:
            row = await cursor.fetchone()
        return row[0] if row is not None else None
