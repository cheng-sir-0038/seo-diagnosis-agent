from unittest.mock import AsyncMock

import pytest

from src.facade.data_facade import DataFacade
from src.models.common import Capability
from src.providers.serper import SerperClient, SerperConfig, SerperProvider
from src.seo_agent.chat.tools import build_seo_agent_registry

SITE = {"id": "s", "domain": "example.com", "default_country": "GB", "default_language": "en-GB"}


@pytest.mark.asyncio
async def test_image_search_uses_image_grid_cards():
    client = SerperClient(SerperConfig(api_key="key"))
    client.request = AsyncMock(
        return_value={
            "images": [
                {
                    "title": "Image",
                    "imageUrl": "https://img/full.jpg",
                    "link": "https://example.com/page",
                    "domain": "example.com",
                    "position": 1,
                }
            ]
        }
    )
    provider = SerperProvider(client)
    facade = DataFacade()
    facade._providers = {"serper": provider}
    facade._capability_map = {Capability.IMAGE_SEARCH: [provider]}
    tool = build_seo_agent_registry(facade, SITE).get("seo_image_search")
    assert tool is not None and tool.render_hint == "cards"
    result = await tool.execute({"query": "seo image"})
    assert result.data["variant"] == "image-grid"
    assert result.data["items"][0]["image_url"] == "https://img/full.jpg"
