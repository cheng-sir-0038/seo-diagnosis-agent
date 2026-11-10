from unittest.mock import AsyncMock

import pytest

from src.models.common import Capability, DataSource
from src.providers.serper import SerperClient, SerperConfig, SerperProvider, SerperSearchType


@pytest.mark.asyncio
async def test_provider_maps_image_search_fields_without_google_internal_url():
    client = SerperClient(SerperConfig(api_key="key"))
    client.request = AsyncMock(
        return_value={
            "images": [
                {
                    "title": "SEO dashboard",
                    "imageUrl": "https://img.example/full.png",
                    "imageWidth": 1200,
                    "imageHeight": 800,
                    "thumbnailUrl": "https://img.example/thumb.png",
                    "thumbnailWidth": 300,
                    "thumbnailHeight": 200,
                    "source": "Example",
                    "domain": "example.com",
                    "link": "https://example.com/page",
                    "googleUrl": "https://google.com/internal",
                    "position": 1,
                }
            ]
        }
    )
    provider = SerperProvider(client)
    rows = await provider.get_image_search(
        query="seo dashboard", country="gb", language="en-GB", limit=10, page=1
    )
    assert Capability.IMAGE_SEARCH in provider.capabilities
    row = rows[0]
    assert row.source == DataSource.SERPER and row.page_url == "https://example.com/page"
    assert row.image_url == "https://img.example/full.png" and row.image_width == 1200
    assert not hasattr(row, "google_url")
    client.request.assert_awaited_once_with(
        SerperSearchType.IMAGES,
        query="seo dashboard",
        country="gb",
        language="en",
        limit=10,
        page=1,
        autocorrect=True,
    )
