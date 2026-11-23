from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from src.seo_agent.checkpoints import CheckpointManager
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import SchemaMigrationRow, SiteRow, WorkScheduleRow

STAMP = "2026-11-23T11:00:00+00:00"


def site_statement(identifier):
    return insert(SiteRow).values(
        id=identifier,
        domain=f"{identifier}.example",
        name=identifier,
        default_country="US",
        default_language="en",
        timezone="UTC",
        created_at=STAMP,
        updated_at=STAMP,
    )


@pytest.mark.asyncio
async def test_concurrent_schema_boot_and_nested_transaction_rollback(pg_url):
    first, second = SeoAgentDatabase(pg_url), SeoAgentDatabase(pg_url)
    try:
        await asyncio.gather(first.startup(), second.startup())
        assert await first.fetch_value(select(SchemaMigrationRow.version)) == 1
        async with first.transaction() as tx:
            await tx.execute(site_statement("kept"))
            async with tx.transaction() as nested:
                assert nested.in_transaction
                assert await nested.fetch_value(select(SiteRow.name)) == "kept"
            assert await second.fetch_value(select(SiteRow.name)) is None
        assert await second.fetch_value(select(SiteRow.name)) == "kept"

        with pytest.raises(ValueError, match="cancel"):
            async with first.transaction() as tx:
                await tx.execute(site_statement("rolled_back"))
                raise ValueError("cancel")
        assert await second.fetch_value(select(func.count()).select_from(SiteRow)) == 1
    finally:
        await asyncio.gather(first.shutdown(), second.shutdown())


@pytest.mark.asyncio
async def test_only_one_active_opportunity_scan_for_a_site(pg_url):
    db = SeoAgentDatabase(pg_url)
    await db.startup()
    try:
        await db.execute(site_statement("shop"))
        values = {
            "site_id": "shop",
            "schedule_type": "OPPORTUNITY_SCAN",
            "name": "扫描",
            "frequency": "DAILY",
            "timezone": "UTC",
            "created_at": STAMP,
            "updated_at": STAMP,
        }
        schedule = insert(WorkScheduleRow).values(**values)
        await db.execute(schedule.values(id="active"))
        await db.execute(schedule.values(id="paused", enabled=0))
        with pytest.raises(IntegrityError):
            await db.execute(schedule.values(id="other"))
        assert set(row["id"] for row in await db.fetch_all(select(WorkScheduleRow.id))) == {
            "active",
            "paused",
        }
    finally:
        await db.shutdown()


@pytest.mark.asyncio
async def test_two_workers_can_start_langgraph_on_an_empty_database(pg_url):
    managers = [CheckpointManager(pg_url), CheckpointManager(pg_url)]
    try:
        await asyncio.gather(*(manager.startup() for manager in managers))
        assert all(manager.require_saver() is not None for manager in managers)
    finally:
        await asyncio.gather(*(manager.shutdown() for manager in managers))
