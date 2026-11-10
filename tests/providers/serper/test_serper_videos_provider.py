from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from src.models.common import Capability
from src.providers.serper import SerperClient, SerperConfig, SerperProvider, SerperSearchType


@pytest.mark.asyncio
async def test_video_mapping_duration_and_relative_date():
    client = SerperClient(SerperConfig(api_key="key"))
    client.request = AsyncMock(
        return_value={
            "videos": [
                {
                    "title": "SEO Video",
                    "link": "https://video.example/watch",
                    "snippet": "Summary",
                    "imageUrl": "https://img/thumb.jpg",
                    "duration": "12:34",
                    "source": "YouTube",
                    "channel": "SEO Channel",
                    "date": "3 days ago",
                    "position": 1,
                },
                {
                    "title": "Dated",
                    "link": "https://video.example/dated",
                    "duration": "1:02:03",
                    "date": "2026-07-20T10:00:00Z",
                    "position": 2,
                },
            ]
        }
    )
    p = SerperProvider(client)
    rows = await p.get_video_search(query="seo", country="gb", language="en-GB", limit=10, page=1)
    assert Capability.VIDEO_SEARCH in p.capabilities
    assert rows[0].duration_seconds == 754 and rows[0].published_at is None
    assert rows[1].duration_seconds == 3723 and rows[1].published_at == datetime(
        2026, 7, 20, 10, 0, tzinfo=UTC
    )
    client.request.assert_awaited_once_with(
        SerperSearchType.VIDEOS,
        query="seo",
        country="gb",
        language="en",
        limit=10,
        page=1,
        autocorrect=True,
    )
