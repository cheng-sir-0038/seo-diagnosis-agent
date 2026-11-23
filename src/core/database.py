"""Async PostgreSQL access shared by the application's ORM stores."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import copy
from typing import Any, Self

from sqlalchemy import MetaData, func, inspect, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.schema import CreateSchema
from sqlalchemy.sql import Executable

from src.core.config import settings


def postgres_url(value: str | URL | None = None, *, schema: str | None = None) -> URL:
    url = make_url(settings.database_url if value is None else value)
    if url.get_backend_name() != "postgresql" or not url.database:
        raise ValueError("DATABASE_URL 必须指向一个 PostgreSQL 数据库")
    if value is None and schema is None:
        schema = settings.database_schema
    if schema:
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema, re.ASCII):
            raise ValueError("数据库 schema 须使用小写字母、数字和下划线")
        options = f"{url.query.get('options', '')} -csearch_path={schema}".strip()
        url = url.update_query_dict({"options": options})
    return url.set(drivername="postgresql+psycopg")


async def prepare_schema(connection: AsyncConnection, schema: str | None) -> None:
    """Serialize startup within the target schema until the caller's transaction commits."""
    schema_key = schema or func.coalesce(func.current_schema(), "")
    await connection.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtext("seo-agent-schema"),
                func.hashtext(schema_key),
            )
        )
    )
    if schema and not await connection.run_sync(lambda conn: inspect(conn).has_schema(schema)):
        await connection.execute(CreateSchema(schema))


class PostgresDatabase:
    def __init__(
        self,
        metadata: MetaData,
        url: str | URL | None = None,
        *,
        schema: str | None = None,
    ) -> None:
        self.schema = settings.database_schema if url is None and schema is None else schema
        self.url = postgres_url(url, schema=self.schema)
        self.metadata = metadata
        self._engine: AsyncEngine | None = None
        self._bound_session: AsyncSession | None = None
        self._startup_lock = asyncio.Lock()

    @property
    def started(self) -> bool:
        return self._engine is not None

    @property
    def in_transaction(self) -> bool:
        return self._bound_session is not None

    async def startup(self) -> None:
        async with self._startup_lock:
            if self._engine is not None:
                return
            engine = create_async_engine(self.url, pool_pre_ping=True)
            try:
                async with engine.begin() as connection:
                    await prepare_schema(connection, self.schema)
                    await connection.run_sync(self.metadata.create_all)
            except BaseException:
                await engine.dispose()
                raise
            self._engine = engine

    async def shutdown(self) -> None:
        async with self._startup_lock:
            if self._engine is not None:
                await self._engine.dispose()
                self._engine = None

    @asynccontextmanager
    async def orm_session(self, *, write: bool = False) -> AsyncIterator[AsyncSession]:
        if self._bound_session is not None:
            yield self._bound_session
            return
        if self._engine is None:
            raise RuntimeError("PostgreSQL 数据库尚未启动")
        async with AsyncSession(self._engine, expire_on_commit=False) as session:
            if write:
                async with session.begin():
                    yield session
            else:
                yield session

    @asynccontextmanager
    async def transaction(self, *, join_existing: bool = True) -> AsyncIterator[Self]:
        if self.in_transaction:
            if not join_existing:
                raise RuntimeError("当前业务操作已在事务中")
            yield self
            return
        async with self.orm_session(write=True) as session:
            bound = copy(self)
            bound._bound_session = session
            try:
                yield bound
            finally:
                bound._bound_session = None
                bound._engine = None

    async def execute(self, statement: Executable) -> None:
        async with self.orm_session(write=True) as session:
            await session.execute(statement)

    async def fetch_one(self, statement: Executable) -> dict[str, Any] | None:
        async with self.orm_session(write=statement.is_dml) as session:
            row = (await session.execute(statement)).mappings().first()
            return dict(row) if row is not None else None

    async def fetch_all(self, statement: Executable) -> list[dict[str, Any]]:
        async with self.orm_session(write=statement.is_dml) as session:
            return [dict(row) for row in (await session.execute(statement)).mappings()]

    async def fetch_value(self, statement: Executable) -> Any:
        async with self.orm_session(write=statement.is_dml) as session:
            return (await session.execute(statement)).scalar()
