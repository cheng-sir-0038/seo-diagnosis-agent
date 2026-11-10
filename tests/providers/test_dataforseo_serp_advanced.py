from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.models.common import Capability, DataSource, Device
from src.providers.dataforseo.provider import DataForSEOProvider
from src.routing.contracts import ProviderFeature, ProviderOperatingSystem


def serp_response(*items: dict) -> dict:
    return {
        "status_code": 20000,
        "tasks": [
            {
                "status_code": 20000,
                "result": [
                    {
                        "keyword": "seo agent",
                        "item_types": [item.get("type") for item in items],
                        "items": list(items),
                    }
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_advanced_serp_builds_dataforseo_task_body_and_maps_ai_overview() -> None:
    provider = DataForSEOProvider()
    provider.client = type("Client", (), {})()
    provider.client.request = AsyncMock(
        return_value=serp_response(
            {
                "type": "ai_overview",
                "rank_absolute": 1,
                "page": 1,
                "position": "left",
                "asynchronous_ai_overview": True,
                "markdown": "## AI summary\nUseful answer.",
                "items": [
                    {
                        "type": "ai_overview_element",
                        "title": "Summary",
                        "text": "Useful answer.",
                        "references": [
                            {
                                "type": "ai_overview_reference",
                                "url": "https://reference.example/source",
                            }
                        ],
                    }
                ],
            },
            {
                "type": "organic",
                "rank_absolute": 2,
                "page": 1,
                "title": "SEO Agent Guide",
                "url": "https://example.com/guide",
                "description": "Guide description",
            },
        )
    )

    rows = await provider.get_serp_overview(
        keyword="seo agent",
        country="gb",
        language="en-US",
        limit=50,
        device=Device.MOBILE,
        os=ProviderOperatingSystem.IOS,
        location_name="London,England,United Kingdom",
        load_ai_overview=True,
        se_domain="google.co.uk",
        result_types=["ai_overview", "organic"],
    )

    provider.client.request.assert_awaited_once_with(
        "serp_google_organic",
        task_body={
            "keyword": "seo agent",
            "location_name": "London,England,United Kingdom",
            "language_code": "en",
            "depth": 50,
            "device": "mobile",
            "os": "ios",
            "load_async_ai_overview": True,
            "se_domain": "google.co.uk",
        },
    )
    assert [row.type for row in rows] == ["ai_overview", "organic"]
    overview = rows[0]
    assert overview.source == DataSource.DATAFORSEO
    assert overview.url is None
    assert overview.title == "AI Overview"
    assert overview.markdown == "## AI summary\nUseful answer."
    assert overview.description == "Useful answer."
    assert overview.reference_urls == ["https://reference.example/source"]
    assert overview.asynchronous is True
    assert overview.page == 1
    assert overview.position == "left"
    assert rows[1].url == "https://example.com/guide"


@pytest.mark.asyncio
async def test_advanced_serp_uses_coordinate_and_filters_result_types() -> None:
    provider = DataForSEOProvider()
    provider.client = type("Client", (), {})()
    provider.client.request = AsyncMock(
        return_value=serp_response(
            {
                "type": "organic",
                "rank_absolute": 150,
                "title": "Deep result",
                "url": "https://example.com/deep",
            },
            {
                "type": "people_also_ask",
                "rank_absolute": 151,
                "title": "Question",
                "url": "https://example.com/question",
            },
        )
    )

    rows = await provider.get_serp_overview(
        keyword="seo",
        country="us",
        language="fr-FR",
        limit=200,
        device="desktop",
        os="macos",
        location_coordinate="51.5074,-0.1278,200",
        result_types=["organic"],
    )

    task_body = provider.client.request.await_args.kwargs["task_body"]
    assert task_body == {
        "keyword": "seo",
        "location_coordinate": "51.5074,-0.1278,200",
        "language_code": "fr",
        "depth": 200,
        "device": "desktop",
        "os": "macos",
    }
    assert len(rows) == 1
    assert rows[0].rank == 150
    assert rows[0].type == "organic"


def test_dataforseo_profile_promotes_advanced_serp_features_to_implemented() -> None:
    provider = DataForSEOProvider()
    provider.client = type("Client", (), {"_session": object()})()
    profile = provider.routing_profile

    assert profile.max_results_for(Capability.SERP_OVERVIEW) == 200
    assert profile.fixed_language is None
    assert {
        ProviderFeature.LANGUAGE_TARGETING,
        ProviderFeature.DEVICE,
        ProviderFeature.OS,
        ProviderFeature.EXACT_LOCATION,
        ProviderFeature.COORDINATES,
        ProviderFeature.AI_OVERVIEW,
        ProviderFeature.DEEP_RESULTS,
    } <= set(profile.implemented_features)
