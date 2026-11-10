from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.tools import StructuredTool

from src.agent.tools import ToolProtocol, ToolRuntimeContext
from src.models.common import Capability
from src.seo_agent.diagnosis.contracts import DiagnosisEngine
from src.seo_agent.tools.seo_data import SeoDataToolBridge


class FakeFacade:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def query(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"meta": {"source": "dataforseo"}, "data": {"ok": True}}


def test_diagnosis_engine_protocol_remains_unchanged() -> None:
    assert "diagnose" in DiagnosisEngine.__dict__


def test_seo_data_bridge_has_eight_canonical_protocol_tools() -> None:
    tools = SeoDataToolBridge(FakeFacade()).build_protocol_tools()
    assert [tool.name for tool in tools] == [
        "seo_organic_keywords",
        "seo_serp_overview",
        "seo_autocomplete",
        "seo_news_search",
        "seo_image_search",
        "seo_video_search",
        "seo_top_pages",
        "seo_organic_competitors",
    ]
    assert all(isinstance(tool, ToolProtocol) for tool in tools)
    assert all(tool.parameters_schema["type"] == "object" for tool in tools)


@pytest.mark.asyncio
async def test_seo_protocol_tool_and_langchain_adapter_are_equivalent() -> None:
    facade = FakeFacade()
    bridge = SeoDataToolBridge(facade)
    canonical = bridge.build_protocol_tools()[0]
    runtime = ToolRuntimeContext(task_id="runtime-task")

    direct = await canonical.execute(
        {"site_id": "example.com", "country": "us", "limit": 3},
        runtime=runtime,
    )
    langchain = next(tool for tool in bridge.build_tools() if tool.name == canonical.name)
    adapted = await langchain.ainvoke({"site_id": "example.com", "country": "us", "limit": 3})

    assert isinstance(langchain, StructuredTool)
    assert direct.success is True
    assert adapted == direct.data
    assert facade.calls[0]["capability"] == Capability.ORGANIC_KEYWORDS
    assert facade.calls[0]["task_id"] == "runtime-task"
    assert facade.calls[1]["task_id"] is None


def test_seo_data_module_no_longer_constructs_structured_tools_directly() -> None:
    source = Path("src/seo_agent/tools/seo_data.py").read_text(encoding="utf-8")
    assert "StructuredTool.from_function" not in source


@pytest.mark.asyncio
async def test_serp_protocol_tool_forwards_provider_routing_fields() -> None:
    facade = FakeFacade()
    tool = SeoDataToolBridge(facade).build_protocol_tools()[1]

    result = await tool.execute(
        {
            "site_id": "example.com",
            "keyword": "seo agent",
            "purpose": "rank_monitoring",
            "preferred_provider": "dataforseo",
            "allow_fallback": False,
        }
    )

    assert result.success is True
    assert facade.calls[0]["params"]["purpose"] == "rank_monitoring"
    assert facade.calls[0]["params"]["preferred_provider"] == "dataforseo"
    assert facade.calls[0]["params"]["allow_fallback"] is False


@pytest.mark.asyncio
async def test_serp_protocol_tool_allows_advanced_depth_and_parameters() -> None:
    facade = FakeFacade()
    tool = SeoDataToolBridge(facade).build_protocol_tools()[1]

    result = await tool.execute(
        {
            "site_id": "example.com",
            "keyword": "seo agent",
            "limit": 200,
            "device": "mobile",
            "os": "ios",
            "location_coordinate": "51.5074,-0.1278,200",
            "load_ai_overview": True,
            "result_types": ["organic", "ai_overview"],
        }
    )

    assert result.success is True
    params = facade.calls[0]["params"]
    assert params["limit"] == 200
    assert params["device"] == "mobile"
    assert params["load_ai_overview"] is True


@pytest.mark.asyncio
async def test_autocomplete_protocol_tool_forwards_query_without_fake_metrics() -> None:
    facade = FakeFacade()
    tool = next(
        item
        for item in SeoDataToolBridge(facade).build_protocol_tools()
        if item.name == "seo_autocomplete"
    )

    result = await tool.execute(
        {
            "site_id": "example.com",
            "query": "seo agent",
            "country": "gb",
            "language": "en-GB",
        }
    )

    assert result.success is True
    assert facade.calls[0]["capability"] == Capability.AUTOCOMPLETE
    assert facade.calls[0]["params"] == {
        "query": "seo agent",
        "country": "gb",
        "language": "en-GB",
    }
