from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.facade.data_facade import DataFacade
from src.models.common import Capability
from src.providers.serper import SerperClient, SerperConfig, SerperProvider
from src.seo_agent.chat.tools import build_seo_agent_registry

SITE = {
    "id": "site-1",
    "domain": "example.com",
    "default_country": "GB",
    "default_language": "en-GB",
}


@pytest.mark.asyncio
async def test_news_flows_through_facade_agent_and_cards_presentation() -> None:
    client = SerperClient(SerperConfig(api_key="test-key"))
    client.request = AsyncMock(
        return_value={
            "news": [
                {
                    "title": "SEO Update",
                    "link": "https://news.example/update",
                    "snippet": "What changed",
                    "date": "1 day ago",
                    "source": "SEO Daily",
                    "position": 1,
                }
            ]
        }
    )
    provider = SerperProvider(client)
    facade = DataFacade()
    facade._providers = {"serper": provider}
    facade._capability_map = {Capability.NEWS_SEARCH: [provider]}

    result = await facade.query_live(
        Capability.NEWS_SEARCH,
        params={
            "query": "seo update",
            "country": "gb",
            "language": "en-GB",
            "limit": 10,
        },
        force_refresh=True,
    )
    tool = build_seo_agent_registry(facade, SITE).get("seo_news_search")

    assert result["meta"]["source"] == "serper"
    assert result["data"][0]["title"] == "SEO Update"
    assert tool is not None and tool.render_hint == "cards"
    tool_result = await tool.execute({"query": "seo update"})
    assert tool_result.success is True
    assert tool_result.data["view_type"] == "cards"
    assert tool_result.data["items"][0]["metadata"][0] == {
        "label": "来源",
        "value": "SEO Daily",
    }
