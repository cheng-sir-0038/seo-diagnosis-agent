from __future__ import annotations

import asyncio

import pytest

from src.agent.loop import AgentLoop
from src.agent.models import RunStatus
from src.agent.providers.base import ModelChunk, ToolCall
from src.agent.providers.fake import FakeModelProvider
from src.agent.run_manager import RunManager
from src.core.exceptions import InternalProviderRequestError
from src.models.common import Capability
from src.seo_agent.chat.tools import build_seo_agent_registry

SITE = {"id": "site-1", "domain": "example.com", "default_country": "US", "default_language": "en"}


@pytest.mark.asyncio
async def test_agent_loop_can_execute_ranking_page_workflow_and_render_cards():
    facade = RankingData()
    registry = build_seo_agent_registry(facade, SITE)
    model = FakeModelProvider(
        [
            [
                ModelChunk(
                    tool_calls=[
                        ToolCall(
                            id="rank-pages",
                            name="seo_ranking_page_optimization",
                            arguments={"max_pages": 1},
                        )
                    ],
                    finish_reason="tool_calls",
                )
            ],
            [
                ModelChunk(text="已取得页面和搜索证据，待确认页面用途。"),
                ModelChunk(finish_reason="stop"),
            ],
        ]
    )
    run = RunManager().create_run([{"role": "user", "content": "看看已有排名页面怎么优化"}])
    await AgentLoop(model, registry).run(run)
    assert run.status == RunStatus.COMPLETED
    event = next(event for event in run.events if event.type == "tool.completed")
    assert event.payload["render_hint"] == "cards"
    assert event.payload["result"]["data"]["optimization_candidates"][0]["page_verified"] is True


class RankingData:
    def __init__(self):
        self.calls = []
        self.fail_urls = set()
        self.slow = False
        self.rows = [
            {"keyword": "malformed", "position": 6, "url": "https://[bad-ipv6/"},
            {"keyword": "foreign", "position": 5, "url": "https://other.example/a"},
            {"keyword": "too early", "position": 1, "url": "https://example.com/top"},
            {"keyword": "waterjet cost", "position": 12, "url": "https://example.com/cost#intro"},
            {"keyword": "waterjet price", "position": 15, "url": "https://example.com/cost"},
            {"keyword": "waterjet repair", "position": 9, "url": "https://example.com/repair"},
            {"keyword": "third page", "position": 7, "url": "https://example.com/third"},
        ]

    def live_capabilities(self):
        return {Capability.ORGANIC_KEYWORDS, Capability.PAGE_METADATA, Capability.SERP_OVERVIEW}

    async def query_live(self, **kwargs):
        self.calls.append(kwargs)
        capability, params = kwargs["capability"], kwargs["params"]
        if capability == Capability.ORGANIC_KEYWORDS:
            data = self.rows
        elif capability == Capability.PAGE_METADATA:
            if self.slow:
                await asyncio.sleep(1)
            if params["url"] in self.fail_urls:
                raise InternalProviderRequestError("private upstream detail")
            data = {
                "status_code": 200,
                "title": "Waterjet guide",
                "title_count": 1,
                "h1s": ["Waterjet"],
                "h1_count": 1,
                "meta_description": None,
                "final_url": params["url"],
            }
        else:
            data = [
                {
                    "rank": 1,
                    "title": "Cost guide",
                    "url": "https://competitor.example/guide",
                    "type": "organic",
                }
            ]
        return {
            "meta": {
                "source": "page_html" if capability == Capability.PAGE_METADATA else "dataforseo",
                "capability": capability.value,
            },
            "data": data,
        }


@pytest.mark.asyncio
async def test_ranking_workflow_selects_bounded_same_site_pages_and_preserves_evidence():
    facade = RankingData()
    result = (
        await build_seo_agent_registry(facade, SITE)
        .get("seo_ranking_page_optimization")
        .execute({})
    )
    assert result.success and result.data["workflow_status"] == "complete"
    pages = result.data["optimization_candidates"]
    assert [page["url"] for page in pages] == [
        "https://example.com/cost",
        "https://example.com/repair",
    ]
    assert pages[0]["keywords"] == ["waterjet cost", "waterjet price"]
    assert pages[0]["title"] == "Waterjet guide"
    assert pages[0]["meta_description"] is None
    assert len(facade.calls) == 5
    assert [
        call["params"]["keyword"]
        for call in facade.calls
        if call["capability"] == Capability.SERP_OVERVIEW
    ] == ["waterjet cost", "waterjet repair"]


@pytest.mark.asyncio
async def test_failed_page_is_retained_as_unverified_and_other_pages_continue():
    facade = RankingData()
    facade.fail_urls.add("https://example.com/cost")
    result = (
        await build_seo_agent_registry(facade, SITE)
        .get("seo_ranking_page_optimization")
        .execute({})
    )
    assert result.success and result.data["workflow_status"] == "partial"
    assert result.data["optimization_candidates"][0]["page_verified"] is False
    assert result.data["optimization_candidates"][1]["page_verified"] is True
    assert "private upstream" not in str(result.data)
    assert (
        len([call for call in facade.calls if call["capability"] == Capability.SERP_OVERVIEW]) == 1
    )


@pytest.mark.asyncio
async def test_optional_performance_and_empty_matching_sample():
    facade = RankingData()
    tool = build_seo_agent_registry(facade, SITE).get("seo_ranking_page_optimization")
    result = await tool.execute({"include_performance": True, "max_pages": 1})
    assert result.success and result.data["workflow_status"] == "partial"
    assert result.data["coverage"]["performance_0"]["error_code"] == "NOT_CONFIGURED"
    facade.rows = []
    result = await tool.execute({})
    assert result.success and result.data["optimization_candidates"] == []
    assert len(result.data["evidence"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"min_position": 20, "max_position": 4},
        {"max_pages": 4},
        {"include_performance": "false"},
        {"limit": True},
        {"extra": 1},
    ],
)
async def test_invalid_ranking_arguments_do_not_make_requests(arguments):
    facade = RankingData()
    with pytest.raises(ValueError):
        await (
            build_seo_agent_registry(facade, SITE)
            .get("seo_ranking_page_optimization")
            .execute(arguments)
        )
    assert not facade.calls


@pytest.mark.asyncio
async def test_ranking_timeout_keeps_original_rankings(monkeypatch):
    from src.seo_agent.chat import tools

    monkeypatch.setattr(tools.settings, "agent_tool_timeout", 0.15)
    facade = RankingData()
    facade.slow = True
    result = (
        await build_seo_agent_registry(facade, SITE)
        .get("seo_ranking_page_optimization")
        .execute({})
    )
    assert result.success and result.data["workflow_status"] == "partial"
    assert result.data["evidence"]["rankings"]["raw"]["data"] == facade.rows
