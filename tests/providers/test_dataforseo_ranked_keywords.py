from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.exceptions import InternalProviderResponseError
from src.models.common import DataIntegrity
from src.providers.dataforseo.provider import DataForSEOProvider


def nested_response(*items: dict) -> dict:
    return {
        "status_code": 20000,
        "tasks": [
            {
                "status_code": 20000,
                "result": [{"items": list(items), "total_count": len(items)}],
            }
        ],
    }


@pytest.mark.asyncio
async def test_ranked_keywords_maps_current_live_nested_contract() -> None:
    provider = DataForSEOProvider()
    provider.client = type("Client", (), {})()
    provider.client.request = AsyncMock(
        return_value=nested_response(
            {
                "se_type": "google",
                "keyword_data": {
                    "keyword": "seo agent",
                    "keyword_info": {"search_volume": 140, "cpc": 2.5},
                    "keyword_properties": {"keyword_difficulty": 25},
                    "search_intent_info": {"main_intent": "commercial"},
                    "serp_info": {"serp_item_types": ["organic", "people_also_ask"]},
                },
                "ranked_serp_element": {
                    "keyword_difficulty": 27,
                    "serp_item_types": ["organic", "people_also_ask"],
                    "serp_item": {
                        "rank_group": 6,
                        "rank_absolute": 7,
                        "url": "https://example.com/seo-agent",
                        "etv": 12.6,
                        "rank_changes": {"previous_rank_absolute": 11},
                    },
                },
            }
        )
    )

    rows = await provider.get_organic_keywords("example.com", country="us", limit=1)

    assert len(rows) == 1
    row = rows[0]
    assert row.keyword == "seo agent"
    assert row.position == 7
    assert row.previous_position == 11
    assert row.position_change == 4
    assert row.search_volume == 140
    assert row.keyword_difficulty == 25
    assert row.cpc == 2.5
    assert row.url == "https://example.com/seo-agent"
    assert row.traffic_estimate == 13
    assert row.intent == "commercial"
    assert row.serp_features == ["organic", "people_also_ask"]


@pytest.mark.asyncio
async def test_ranked_keywords_keeps_legacy_pseudo_contract_compatible() -> None:
    provider = DataForSEOProvider()
    provider.client = type("Client", (), {})()
    provider.client.request = AsyncMock(
        return_value=nested_response(
            {
                "keyword": "legacy keyword",
                "search_volume": 90,
                "cpc": 1.2,
                "keyword_difficulty": 18,
                "estimated_traffic": 7.7,
                "search_intent": "informational",
                "serp_features": ["video"],
                "ranked_domain": {
                    "rank_absolute": 9,
                    "url": "https://example.com/legacy",
                    "rank_changes": {"previous_rank_absolute": 12},
                },
            }
        )
    )

    rows = await provider.get_organic_keywords("example.com", limit=1)

    assert rows[0].keyword == "legacy keyword"
    assert rows[0].position == 9
    assert rows[0].previous_position == 12
    assert rows[0].position_change == 3
    assert rows[0].url == "https://example.com/legacy"


@pytest.mark.asyncio
async def test_ranked_keywords_fails_when_items_exist_but_critical_fields_do_not_parse() -> None:
    provider = DataForSEOProvider()
    provider.client = type("Client", (), {})()
    provider.client.request = AsyncMock(
        return_value=nested_response(
            {"keyword_data": {}, "ranked_serp_element": {"serp_item": {}}},
            {"unexpected": "shape"},
        )
    )

    with pytest.raises(InternalProviderResponseError, match="无法解析关键词明细"):
        await provider.get_organic_keywords("example.com", limit=2)


@pytest.mark.asyncio
async def test_ranked_keywords_marks_partial_when_invalid_rows_are_dropped() -> None:
    provider = DataForSEOProvider()
    provider.client = type("Client", (), {})()
    provider.client.request = AsyncMock(
        return_value=nested_response(
            {
                "keyword_data": {"keyword": "valid", "keyword_info": {"search_volume": 10}},
                "ranked_serp_element": {
                    "serp_item": {"rank_absolute": 3, "url": "https://example.com/valid"}
                },
            },
            {"keyword_data": {}, "ranked_serp_element": {}},
        )
    )

    rows = await provider.get_organic_keywords("example.com", limit=2)

    assert len(rows) == 1
    assert rows[0].integrity == DataIntegrity.PARTIAL
