from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

from src.observability.otel_ids import (
    is_valid_span_id,
    is_valid_trace_id,
    new_span_id_hex,
    new_trace_id_hex,
)
from src.observability.schema import SCHEMA_SQL, SCHEMA_VERSION


class ObservabilityDatabase:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._connection: aiosqlite.Connection | None = None

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
        await connection.execute("PRAGMA busy_timeout = 5000")
        await connection.executescript(SCHEMA_SQL)
        await self._ensure_column(connection, "obs_traces", "otel_trace_id", "TEXT")
        await self._ensure_column(connection, "obs_traces", "otel_root_span_id", "TEXT")
        await self._backfill_otel_ids(connection)
        await self._ensure_column(connection, "obs_model_calls", "prompt_version_id", "TEXT")
        await self._ensure_column(connection, "obs_model_calls", "evaluation_case_id", "TEXT")
        await self._ensure_column(connection, "obs_model_calls", "experiment_variant_id", "TEXT")
        await connection.execute(
            "INSERT OR IGNORE INTO obs_schema_migrations(version, applied_at) "
            "VALUES (?, datetime('now'))",
            (SCHEMA_VERSION,),
        )
        await connection.commit()
        self._connection = connection

    @staticmethod
    async def _backfill_otel_ids(connection: aiosqlite.Connection) -> None:
        async with connection.execute(
            "SELECT id, otel_trace_id, otel_root_span_id FROM obs_traces"
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            trace_id = str(row[1]) if row[1] is not None else None
            span_id = str(row[2]) if row[2] is not None else None
            if is_valid_trace_id(trace_id) and is_valid_span_id(span_id):
                continue
            await connection.execute(
                "UPDATE obs_traces SET otel_trace_id = ?, otel_root_span_id = ? WHERE id = ?",
                (
                    trace_id if is_valid_trace_id(trace_id) else new_trace_id_hex(),
                    span_id if is_valid_span_id(span_id) else new_span_id_hex(),
                    row[0],
                ),
            )

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

    async def executescript(self, script: str) -> None:
        connection = self._require_connection()
        await connection.executescript(script)
        await connection.commit()

    async def shutdown(self) -> None:
        if self._connection is None:
            return
        await self._connection.close()
        self._connection = None

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("ObservabilityDatabase 尚未启动")
        return self._connection

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        connection = self._require_connection()
        await connection.execute(sql, params)
        await connection.commit()

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

    async def table_counts(self) -> dict[str, int]:
        tables = (
            "obs_projects",
            "obs_traces",
            "obs_trace_links",
            "obs_runs",
            "obs_spans",
            "obs_events",
            "obs_model_calls",
            "obs_tool_calls",
            "obs_issues",
            "obs_issue_occurrences",
            "obs_metric_points",
        )
        return {
            table: int(await self.fetch_value(f"SELECT COUNT(*) FROM {table}") or 0)
            for table in tables
        }
