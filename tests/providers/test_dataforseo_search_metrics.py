from unittest.mock import AsyncMock

import pytest

from src.core.exceptions import InternalProviderResponseError
from src.providers.dataforseo import DataForSEOProvider
from src.providers.dataforseo.config import ENDPOINTS


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
async def test_search_metrics_reads_domain_overview_target_and_nested_metrics() -> None:
    provider = DataForSEOProvider()
    provider.client = type("Client", (), {})()
    provider.client.request = AsyncMock(
        return_value={
            "tasks": [
                {
                    "result": [
                        {
                            "target": "example.com",
                            "language_code": "zh",
                            "items": [
                                {
                                    "metrics": {
                                        "organic": {
                                            "count": 12,
                                            "etv": 42.3,
                                            "pos_1": 1,
                                            "pos_2_3": 2,
                                            "pos_4_10": 3,
                                        }
                                    }
                                }
                            ],
                        }
                    ]
                }
            ]
        }
    )
    result = await provider.get_search_metrics("www.example.com", country="cn", language="zh")
    assert provider.client.request.call_args.args == ("domain_rank_overview",)
    assert provider.client.request.call_args.kwargs["task_body"]["language_code"] == "zh"
    assert ENDPOINTS["domain_rank_overview"].path.endswith("/google/domain_rank_overview/live")
    assert result.organic_keywords_count == 12
    assert result.organic_traffic_estimate == 42
    assert result.top_3_count == 3 and result.top_10_count == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["competitor.example", "example.co", None])
@pytest.mark.parametrize("nested", [False, True])
async def test_search_metrics_never_substitutes_another_domain(
    domain: str | None, nested: bool
) -> None:
    provider = DataForSEOProvider()
    provider.client = type("Client", (), {})()
    response = response_with_metrics({"count": 999})
    response["tasks"][0]["result"][0]["items"][0]["domain"] = domain
    if nested:
        response["tasks"][0]["result"][0]["target"] = domain
    provider.client.request = AsyncMock(return_value=response)
    with pytest.raises(InternalProviderResponseError):
        await provider.get_search_metrics("example.com", language="zh")


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
