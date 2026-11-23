from __future__ import annotations

from typing import Any

from sqlalchemy import func, insert, select, update

from src.core.database import PostgresDatabase
from src.observability.schema import Base


class ObservabilityDatabase(PostgresDatabase):
    def __init__(self, url: str | None = None, *, schema: str | None = None) -> None:
        # Both modules register their mappings before metadata is created.
        import src.promptops.schema  # noqa: F401

        super().__init__(Base.metadata, url, schema=schema)

    async def insert_row(self, entity: type[Base], **values: Any) -> dict[str, Any]:
        statement = insert(entity).values(**values).returning(*entity.__table__.c)
        async with self.orm_session(write=True) as session:
            return dict((await session.execute(statement)).mappings().one())

    async def finish_row(self, entity: type[Base], row_id: str, **values: Any) -> dict[str, Any]:
        table = entity.__table__
        statement = update(entity).where(table.c.id == row_id).values(**values).returning(*table.c)
        async with self.orm_session(write=True) as session:
            return dict((await session.execute(statement)).mappings().one())

    async def table_counts(self) -> dict[str, int]:
        counts = {}
        async with self.orm_session() as session:
            for table in Base.metadata.sorted_tables:
                if table.name.startswith("obs_"):
                    counts[table.name] = await session.scalar(
                        select(func.count()).select_from(table)
                    )
        return counts
