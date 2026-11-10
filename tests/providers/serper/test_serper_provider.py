from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.exceptions import InternalProviderResponseError
from src.models.common import Capability, DataIntegrity, DataSource
from src.providers.serper.client import SerperClient
from src.providers.serper.config import SerperConfig
from src.providers.serper.provider import SerperProvider


@pytest.mark.asyncio
async def test_provider_maps_organic_results_to_canonical_serp_rows() -> None:
    client = SerperClient(SerperConfig(api_key="test-key"))
    client.search = AsyncMock(
        return_value={
            "searchParameters": {"q": "seo agent", "gl": "gb", "hl": "en"},
            "organic": [
                {
                    "title": "SEO Agent Guide",
                    "link": "https://example.com/guide",
                    "snippet": "A practical guide.",
                    "position": 1,
                },
                {
                    "title": "Missing URL",
                    "snippet": "This invalid row must be ignored.",
                    "position": 2,
                },
                {
                    "title": "SEO Agent Tools",
                    "link": "https://example.org/tools",
                    "snippet": "Tool comparison.",
                    "position": 3,
                },
            ],
        }
    )
    provider = SerperProvider(client)

    rows = await provider.get_serp_overview(
        keyword="seo agent",
        country="GB",
        language="en",
        limit=2,
        page=1,
        autocorrect=False,
    )

    assert [row.rank for row in rows] == [1, 3]
    assert [row.url for row in rows] == [
        "https://example.com/guide",
        "https://example.org/tools",
    ]
    assert all(row.type == "organic" for row in rows)
    assert all(row.source == DataSource.SERPER for row in rows)
    assert all(row.integrity == DataIntegrity.PARTIAL for row in rows)
    assert all(row.is_estimated is False for row in rows)
    client.search.assert_awaited_once_with(
        query="seo agent",
        country="gb",
        language="en",
        limit=2,
        page=1,
        autocorrect=False,
    )


def test_provider_declares_live_serper_capabilities_and_needs_key() -> None:
    unavailable = SerperProvider(SerperClient(SerperConfig(api_key="")))
    live = SerperProvider(SerperClient(SerperConfig(api_key="configured")))

    assert unavailable.runtime_mode == "unavailable"
    assert live.runtime_mode == "live"
    assert live.meta.source == DataSource.SERPER
    assert live.capabilities == {
        Capability.SERP_OVERVIEW,
        Capability.AUTOCOMPLETE,
        Capability.NEWS_SEARCH,
        Capability.IMAGE_SEARCH,
        Capability.VIDEO_SEARCH,
    }


@pytest.mark.asyncio
async def test_provider_rejects_blank_keyword_and_unmappable_nonempty_results() -> None:
    provider = SerperProvider(SerperClient(SerperConfig(api_key="test-key")))

    with pytest.raises(ValueError, match="keyword"):
        await provider.get_serp_overview(keyword="  ", country="us")

    provider.client.search = AsyncMock(return_value={"organic": [{"title": "No link"}]})
    with pytest.raises(InternalProviderResponseError, match="无法映射"):
        await provider.get_serp_overview(keyword="seo", country="us")
