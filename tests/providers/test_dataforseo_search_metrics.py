from unittest.mock import AsyncMock

import pytest

from src.providers.dataforseo import DataForSEOProvider


def response_with_metrics(metrics: dict) -> dict:
    return {
        "tasks": [
            {
                "status_code": 20000,
                "result": [
                    {
                        "items": [
                            {
                                "domain": "example.com",
                                "metrics": {"organic": metrics},
                            }
                        ]
                    }
                ],
            }
        ]
    }


@pytest.mark.asyncio
async def test_search_metrics_uses_current_dataforseo_ranking_buckets() -> None:
    provider = DataForSEOProvider()
    provider.client = type("Client", (), {})()
    provider.client.request = AsyncMock(
        return_value=response_with_metrics(
            {
                "count": 100,
                "etv": 52000,
                "pos_1": 10,
                "pos_2_3": 20,
                "pos_4_10": 30,
            }
        )
    )

    result = await provider.get_search_metrics("example.com", country="us")

    assert result.organic_keywords_count == 100
    assert result.top_3_count == 30
    assert result.top_10_count == 60


@pytest.mark.asyncio
async def test_search_metrics_keeps_cumulative_ranking_invariant() -> None:
    provider = DataForSEOProvider()
    provider.client = type("Client", (), {})()
    provider.client.request = AsyncMock(
        return_value=response_with_metrics(
            {
                "count": 214,
                "etv": 52000,
                "pos_1": 43,
                "pos_2_3": 0,
                "pos_4_10": 0,
            }
        )
    )

    result = await provider.get_search_metrics("example.com", country="us")

    assert result.top_3_count == 43
    assert result.top_10_count == 43
    assert result.top_3_count <= result.top_10_count <= result.organic_keywords_count
