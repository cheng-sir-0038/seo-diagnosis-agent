from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.models.common import Capability, DataIntegrity, DataSource
from src.providers.serper import SerperClient, SerperConfig, SerperProvider, SerperSearchType


@pytest.mark.asyncio
async def test_provider_maps_deduplicated_autocomplete_suggestions() -> None:
    client = SerperClient(SerperConfig(api_key="test-key"))
    client.request = AsyncMock(
        return_value={
            "suggestions": [
                {"value": "SEO Agent Tools"},
                {"value": "  seo   agent tools  "},
                "SEO Agent Pricing",
                {"value": ""},
                {"unexpected": "ignored"},
            ]
        }
    )
    provider = SerperProvider(client)

    rows = await provider.get_autocomplete(
        query="seo agent",
        country="GB",
        language="en-GB",
    )

    assert provider.capabilities >= {Capability.AUTOCOMPLETE}
    assert [row.position for row in rows] == [1, 2]
    assert [row.value for row in rows] == ["SEO Agent Tools", "SEO Agent Pricing"]
    assert [row.normalized_value for row in rows] == [
        "seo agent tools",
        "seo agent pricing",
    ]
    assert all(row.source == DataSource.SERPER for row in rows)
    assert all(row.integrity == DataIntegrity.COMPLETE for row in rows)
    assert all(row.is_estimated is False for row in rows)
    client.request.assert_awaited_once_with(
        SerperSearchType.AUTOCOMPLETE,
        query="seo agent",
        country="gb",
        language="en",
    )


@pytest.mark.asyncio
async def test_provider_rejects_blank_autocomplete_query() -> None:
    provider = SerperProvider(SerperClient(SerperConfig(api_key="test-key")))

    with pytest.raises(ValueError, match="query"):
        await provider.get_autocomplete(query="  ", country="us", language="en")
