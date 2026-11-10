from unittest.mock import AsyncMock

import pytest

from src.facade.data_facade import DataFacade
from src.models.common import Capability
from src.providers.serper import SerperClient, SerperConfig, SerperProvider
from src.seo_agent.chat.tools import build_seo_agent_registry

SITE = {"domain": "example.com", "default_country": "GB", "default_language": "en-GB"}


@pytest.mark.asyncio
async def test_video_tool_cards():
    c = SerperClient(SerperConfig(api_key="k"))
    c.request = AsyncMock(
        return_value={
            "videos": [
                {
                    "title": "Video",
                    "link": "https://v/x",
                    "imageUrl": "https://i/x.jpg",
                    "source": "YouTube",
                    "channel": "Channel",
                    "duration": "2:00",
                    "position": 1,
                }
            ]
        }
    )
    p = SerperProvider(c)
    f = DataFacade()
    f._providers = {"serper": p}
    f._capability_map = {Capability.VIDEO_SEARCH: [p]}
    tool = build_seo_agent_registry(f, SITE).get("seo_video_search")
    assert tool and tool.render_hint == "cards"
    result = await tool.execute({"query": "seo"})
    assert result.data["variant"] == "video"
    assert result.data["items"][0]["metadata"][1]["value"] == "Channel"
