from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.agent.loop import AgentLoop
from src.agent.models import RunStatus
from src.agent.observability import AgentRunObserver
from src.agent.providers.base import ModelChunk, ToolCall
from src.agent.providers.fake import FakeModelProvider
from src.agent.run_manager import RunManager
from src.core.exceptions import InternalProviderRequestError
from src.models.common import Capability
from src.observability.client import ObservabilityClient
from src.observability.exporters import OpenTelemetryExporter
from src.observability.storage import ObservabilityDatabase
from src.seo_agent.chat.runtime import build_seo_system_prompt
from src.seo_agent.chat.tools import build_seo_agent_registry

SITE = {"id": "site-1", "domain": "example.com", "default_country": "US", "default_language": "en"}
CAPABILITIES = {
    Capability.PAGE_METADATA,
    Capability.PAGE_PERFORMANCE,
    Capability.SEARCH_METRICS,
    Capability.ORGANIC_KEYWORDS,
    Capability.KEYWORD_OVERVIEW,
    Capability.AUTOCOMPLETE,
    Capability.SERP_OVERVIEW,
    Capability.ORGANIC_COMPETITORS,
}


class ExternalData:
    def __init__(self, capabilities: set[Capability] | None = None) -> None:
        self.capabilities = CAPABILITIES if capabilities is None else capabilities
        self.calls: list[dict[str, Any]] = []
        self.fail: set[Capability] = set()
        self.slow: set[Capability] = set()

    def live_capabilities(self) -> set[Capability]:
        return self.capabilities

    async def query_live(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        capability = kwargs["capability"]
        if capability in self.slow:
            await asyncio.sleep(1)
        if capability in self.fail:
            raise InternalProviderRequestError("private upstream body with credential")
        data: Any = {
            Capability.PAGE_METADATA: {
                "status_code": 200,
                "requested_url": kwargs["params"].get("url"),
                "final_url": kwargs["params"].get("url"),
                "title": "Example",
                "title_count": 1,
                "h1s": ["Example"],
                "h1_count": 1,
            },
            Capability.PAGE_PERFORMANCE: {
                "strategy": "mobile",
                "category_scores": {"performance": 80},
            },
            Capability.SEARCH_METRICS: {
                "organic_keywords_count": 10,
                "organic_traffic_estimate": 30,
            },
            Capability.ORGANIC_KEYWORDS: [
                {"keyword": "waterjet", "position": 12, "url": "https://example.com/guide"}
            ],
            Capability.AUTOCOMPLETE: [
                {"value": "waterjet cost", "position": 1},
                {"value": "Waterjet cost", "position": 2},
                {"value": "waterjet repair", "position": 3},
            ],
            Capability.KEYWORD_OVERVIEW: [
                {"keyword": "waterjet", "search_volume": 100, "keyword_difficulty": 30},
                {"keyword": "waterjet cost", "search_volume": None, "keyword_difficulty": None},
            ],
            Capability.SERP_OVERVIEW: [
                {
                    "rank": 1,
                    "title": "Waterjet guide",
                    "url": "https://competitor.example/guide",
                    "type": "organic",
                }
            ],
            Capability.ORGANIC_COMPETITORS: [
                {"domain": "competitor.example", "common_keywords_count": 3}
            ],
        }[capability]
        return {
            "meta": {
                "source": "page_html" if capability == Capability.PAGE_METADATA else "dataforseo",
                "capability": capability.value,
            },
            "data": data,
        }


def test_registry_registers_only_workflows_with_useful_live_dependencies() -> None:
    names = build_seo_agent_registry(ExternalData(), SITE).names()
    assert {
        "seo_site_audit",
        "seo_search_diagnosis",
        "seo_keyword_opportunity",
        "seo_site_diagnosis",
    } <= set(names)
    assert build_seo_agent_registry(ExternalData(set()), SITE).names() == []
    assert build_seo_agent_registry(ExternalData({Capability.SEARCH_METRICS}), SITE).names() == [
        "seo_search_metrics"
    ]


@pytest.mark.asyncio
async def test_search_workflow_preserves_real_snapshot_and_market() -> None:
    facade = ExternalData()
    result = (
        await build_seo_agent_registry(facade, SITE)
        .get("seo_search_diagnosis")
        .execute({"country": "GB", "language": "de", "limit": 3})
    )
    assert result.success
    assert result.data["workflow_status"] == "complete"
    assert set(result.data["evidence"]) == {"metrics", "keywords"}
    assert result.data["evidence"]["keywords"]["raw"]["data"][0]["position"] == 12
    assert all(
        call["params"]["country"] == "GB" and call["params"]["language"] == "de"
        for call in facade.calls
    )
    assert any("历史" in limitation for limitation in result.data["limitations"])


@pytest.mark.asyncio
async def test_keyword_workflow_bounds_and_deduplicates_candidates_without_inventing_metrics() -> (
    None
):
    facade = ExternalData()
    result = (
        await build_seo_agent_registry(facade, SITE)
        .get("seo_keyword_opportunity")
        .execute({"query": "waterjet", "limit": 3})
    )
    assert result.success
    assert result.data["workflow_status"] == "complete"
    candidates = result.data["keyword_candidates"]
    assert [row["keyword"] for row in candidates] == [
        "waterjet",
        "waterjet cost",
        "waterjet repair",
    ]
    assert candidates[0]["search_volume"] == 100
    assert candidates[1]["search_volume"] is None and candidates[2]["keyword_difficulty"] is None
    assert [call["capability"] for call in facade.calls] == [
        Capability.AUTOCOMPLETE,
        Capability.KEYWORD_OVERVIEW,
        Capability.SERP_OVERVIEW,
    ]
    assert facade.calls[1]["params"]["keywords"] == ["waterjet", "waterjet cost", "waterjet repair"]
    assert facade.calls[2]["params"]["keyword"] == "waterjet"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unconfigured", "failed"])
async def test_keyword_workflow_keeps_other_evidence_when_metrics_unavailable(mode: str) -> None:
    facade = ExternalData(
        CAPABILITIES - {Capability.KEYWORD_OVERVIEW} if mode == "unconfigured" else None
    )
    if mode == "failed":
        facade.fail.add(Capability.KEYWORD_OVERVIEW)
    result = (
        await build_seo_agent_registry(facade, SITE)
        .get("seo_keyword_opportunity")
        .execute({"query": "waterjet"})
    )
    assert result.success and result.data["workflow_status"] == "partial"
    assert {"suggestions", "serp"} <= set(result.data["evidence"])
    assert all(row["search_volume"] is None for row in result.data["keyword_candidates"])
    assert "private upstream" not in str(result.data)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("seo_keyword_opportunity", {"query": "   "}),
        ("seo_keyword_opportunity", {"query": "waterjet", "limit": 20}),
        ("seo_keyword_opportunity", {"query": "word " * 11}),
        ("seo_search_diagnosis", {"limit": True}),
        ("seo_site_diagnosis", {"include_performance": "false"}),
        ("seo_site_diagnosis", {"query": "word " * 11}),
        ("seo_site_diagnosis", {"include_competitors": True, "extra": True}),
    ],
)
async def test_workflows_reject_invalid_input_before_querying(name: str, arguments: dict) -> None:
    facade = ExternalData()
    with pytest.raises(ValueError):
        await build_seo_agent_registry(facade, SITE).get(name).execute(arguments)
    assert facade.calls == []


@pytest.mark.asyncio
async def test_site_report_runs_selected_business_steps_without_duplicate_queries() -> None:
    facade = ExternalData()
    result = (
        await build_seo_agent_registry(facade, SITE)
        .get("seo_site_diagnosis")
        .execute({"query": "waterjet", "include_competitors": True})
    )
    assert result.success and result.data["workflow_status"] == "complete"
    assert set(result.data["evidence"]) == {"page", "search", "opportunities", "competitors"}
    capabilities = [call["capability"] for call in facade.calls]
    assert len(capabilities) == len(set(capabilities))
    assert Capability.PAGE_PERFORMANCE not in capabilities
    assert result.data["coverage"]["indexing"]["status"] == "not_implemented"
    assert result.data["coverage"]["robots_txt"]["status"] == "not_implemented"
    assert result.data["coverage"]["sitemap"]["status"] == "not_implemented"


@pytest.mark.asyncio
async def test_site_report_rejects_cross_site_url_before_paid_queries() -> None:
    facade = ExternalData()
    result = (
        await build_seo_agent_registry(facade, SITE)
        .get("seo_site_diagnosis")
        .execute({"url": "https://other.example/", "query": "waterjet"})
    )
    assert not result.success
    assert facade.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("slow", [Capability.SEARCH_METRICS, Capability.PAGE_PERFORMANCE])
async def test_nested_timeout_keeps_page_evidence_in_real_agent_loop(monkeypatch, slow) -> None:
    from src.seo_agent.chat import tools

    monkeypatch.setattr(tools.settings, "agent_tool_timeout", 0.3)
    facade = ExternalData()
    facade.slow.add(slow)
    registry = build_seo_agent_registry(facade, SITE)
    model = FakeModelProvider(
        [
            [
                ModelChunk(
                    tool_calls=[
                        ToolCall(
                            id="diagnosis",
                            name="seo_site_diagnosis",
                            arguments={"include_performance": slow == Capability.PAGE_PERFORMANCE},
                        )
                    ],
                    finish_reason="tool_calls",
                )
            ],
            [ModelChunk(text="搜索数据未完成，已保留页面证据。"), ModelChunk(finish_reason="stop")],
        ]
    )
    run = RunManager().create_run([{"role": "user", "content": "诊断站点"}])
    await AgentLoop(model, registry, tool_timeout=0.3).run(run)
    assert run.status == RunStatus.COMPLETED
    event = next(event for event in run.events if event.type == "tool.completed")
    report = event.payload["result"]["data"]
    assert report["workflow_status"] == "partial"
    assert report["evidence"]["page"]["evidence"]["page"]["raw"]["data"]["title"] == "Example"


@pytest.mark.asyncio
async def test_report_records_real_nested_tools_and_model_choice(tmp_path: Path) -> None:
    memory = InMemorySpanExporter()
    database = ObservabilityDatabase(tmp_path / "observability.db")
    exporter = OpenTelemetryExporter(
        service_name="seo-agent", endpoint=None, span_exporter=memory, database=database
    )
    observer = ObservabilityClient(database, project_key="seo-agent", exporters=[exporter])
    facade = ExternalData()
    registry = build_seo_agent_registry(facade, SITE, observability=observer)
    model = FakeModelProvider(
        [
            [
                ModelChunk(
                    tool_calls=[ToolCall(id="report", name="seo_site_diagnosis", arguments={})],
                    finish_reason="tool_calls",
                )
            ],
            [ModelChunk(text="诊断完成。"), ModelChunk(finish_reason="stop")],
        ]
    )
    run = RunManager().create_run(
        [
            {"role": "system", "content": build_seo_system_prompt(SITE, registry.schemas())},
            {"role": "user", "content": "诊断本站"},
        ]
    )
    try:
        await AgentLoop(
            model, registry, observer=AgentRunObserver(observer, operation_name="seo_agent.chat")
        ).run(run)
        spans = await database.fetch_all("SELECT * FROM obs_spans")
        names = {row["operation_name"] for row in spans}
        assert {
            "seo_site_diagnosis",
            "seo_site_audit",
            "seo_page_metadata",
            "seo_search_diagnosis",
            "seo_search_metrics",
            "seo_organic_keywords",
        } <= names
        assert "seo_keyword_opportunity" not in names and "seo_page_performance" not in names
        by_name = {row["operation_name"]: row for row in spans}
        for child, parent in (
            ("seo_site_audit", "seo_site_diagnosis"),
            ("seo_page_metadata", "seo_site_audit"),
            ("seo_search_diagnosis", "seo_site_diagnosis"),
            ("seo_search_metrics", "seo_search_diagnosis"),
            ("seo_organic_keywords", "seo_search_diagnosis"),
        ):
            assert by_name[child]["parent_span_id"] == by_name[parent]["id"]
            assert by_name[child]["trace_id"] == by_name[parent]["trace_id"]
        calls = await database.fetch_all("SELECT * FROM obs_model_calls")
        assert "seo_site_diagnosis" in str(calls) and "诊断本站" in str(calls)
        assert "可用 SEO 数据工具" in str(calls)
    finally:
        await observer.shutdown()
