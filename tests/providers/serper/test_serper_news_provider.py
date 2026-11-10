from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from src.models.common import Capability, DataSource
from src.providers.serper import SerperClient, SerperConfig, SerperProvider, SerperSearchType


@pytest.mark.asyncio
async def test_provider_maps_news_and_preserves_relative_date_text() -> None:
    client = SerperClient(SerperConfig(api_key="test-key"))
    client.request = AsyncMock(
        return_value={
            "news": [
                {
                    "title": "Current SEO News",
                    "link": "https://news.example/current",
                    "snippet": "Current summary",
                    "date": "2 hours ago",
                    "source": "SEO Daily",
                    "imageUrl": "https://img.example/current.jpg",
                    "position": 1,
                },
                {
                    "title": "Dated SEO News",
                    "link": "https://news.example/dated",
                    "snippet": "Dated summary",
                    "date": "2026-07-20T10:30:00Z",
                    "source": "Search Journal",
                    "position": 2,
                },
                {"title": "No link", "date": "2026-07-20", "position": 3},
            ]
        }
    )
    provider = SerperProvider(client)

    rows = await provider.get_news_search(
        query="google seo",
        country="GB",
        language="en-GB",
        limit=10,
        page=1,
    )

    assert Capability.NEWS_SEARCH in provider.capabilities
    assert len(rows) == 2
    assert rows[0].published_text == "2 hours ago"
    assert rows[0].published_at is None
    assert rows[0].source_name == "SEO Daily"
    assert rows[0].image_url == "https://img.example/current.jpg"
    assert rows[1].published_at == datetime(2026, 7, 20, 10, 30, tzinfo=UTC)
    assert all(row.source == DataSource.SERPER for row in rows)
    client.request.assert_awaited_once_with(
        SerperSearchType.NEWS,
        query="google seo",
        country="gb",
        language="en",
        limit=10,
        page=1,
        autocorrect=True,
    )
