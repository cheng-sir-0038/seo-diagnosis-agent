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
async def test_autocomplete_flows_through_live_facade_and_agent_tool() -> None:
    client = SerperClient(SerperConfig(api_key="test-key"))
    client.request = AsyncMock(
        return_value={"suggestions": [{"value": "seo agent tools"}]}
    )
    provider = SerperProvider(client)
    facade = DataFacade()
    facade._providers = {"serper": provider}
    facade._capability_map = {Capability.AUTOCOMPLETE: [provider]}

    result = await facade.query_live(
        Capability.AUTOCOMPLETE,
        params={"query": "seo agent", "country": "gb", "language": "en-GB"},
        force_refresh=True,
    )
    registry = build_seo_agent_registry(facade, SITE)
    tool = registry.get("seo_autocomplete")

    assert result["meta"]["source"] == "serper"
    assert result["meta"]["capability"] == "autocomplete"
    assert result["data"][0]["value"] == "seo agent tools"
    assert tool is not None
    assert set(tool.parameters_schema["properties"]) == {
        "query",
        "country",
        "language",
        "force_refresh",
    }

    tool_result = await tool.execute({"query": "seo agent"})
    assert tool_result.success is True
