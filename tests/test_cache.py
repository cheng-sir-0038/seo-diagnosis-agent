import pytest
from sqlalchemy import select

from src.core.cache import CacheManager
from src.core.evidence_models import ApiUsage


@pytest.mark.asyncio
async def test_cache_set_get_and_ttl():
    cache = CacheManager()

    await cache.set("key", {"value": 1}, ttl=60)

    assert await cache.get("key") == {"value": 1}

    await cache.invalidate("key")

    assert await cache.get("key") is None
    await cache.set("expired", "value", ttl=0)
    assert await cache.get("expired") is None
    await cache.shutdown()


@pytest.mark.asyncio
async def test_snapshot_survives_cache_manager_restart(pg_url):
    response = {
        "meta": {
            "capability": "search_metrics",
            "source": "dataforseo",
            "params_hash": "abc123",
            "fetched_at": "2026-07-22T12:00:00+00:00",
            "integrity": "estimated",
            "is_estimated": True,
            "units_consumed": 2,
        },
        "data": {"organic_traffic_estimate": 12},
    }

    first = CacheManager(pg_url)
    await first.startup()
    snapshot_id = await first.save_snapshot(
        task_id="task-1",
        capability="search_metrics",
        data=response,
        provider="dataforseo",
    )
    await first.log_api_usage("dataforseo", "competitors_domain", 2, True)
    await first.shutdown()

    second = CacheManager(pg_url)
    await second.startup()

    assert await second.get_snapshot(snapshot_id) == response
    assert await second.get_snapshot("missing") is None
    async with second.database.orm_session() as session:
        log = (await session.scalars(select(ApiUsage))).one()
        assert (log.provider, log.endpoint, log.units_consumed, log.success) == (
            "dataforseo", "competitors_domain", 2, True,
        )
    await second.shutdown()
