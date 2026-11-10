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

SITE = {"id": "site_1", "domain": "example.com", "default_country": "US"}


def test_prompt_requires_evidence_before_claiming_business_impact() -> None:
    prompt = build_seo_system_prompt(SITE, [])
    assert "不得据此断言从未做过 SEO" in prompt
    assert "不能据此断言整站状态或 Google 是否收录" in prompt
    assert "不得说本次重新抓取或排除缓存" in prompt
    assert "seo_site_audit 可用时，必须调用该工具" in prompt
    assert "Canonical 指向其他 URL 可能是正常的重复页面归一" in prompt
    assert "不能仅凭非自引用判定错误或建议修改" in prompt
    assert "目标页面内容和站点希望使用的主地址" in prompt
    assert "不得把待核实的差异列为优先问题或给出改写、重定向方案" in prompt


class ExternalDataFixture:
    def __init__(self, *, performance: bool = True, fail_performance: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.performance = performance
        self.fail_performance = fail_performance

    def live_capabilities(self) -> set[Capability]:
        capabilities = {Capability.PAGE_METADATA}
        if self.performance:
            capabilities.add(Capability.PAGE_PERFORMANCE)
        return capabilities

    async def query_live(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if kwargs["capability"] == Capability.PAGE_METADATA:
            return {
                "meta": {"capability": "page_metadata", "source": "page_html"},
                "data": {
                    "requested_url": kwargs["params"]["url"],
                    "final_url": kwargs["params"]["url"],
                    "status_code": 200,
                    "title": "Example",
                    "title_count": 1,
                    "meta_description": None,
                    "meta_description_count": 0,
                    "h1s": ["Example"],
                    "h1_count": 1,
                    "declared_noindex": False,
                    "warnings": ["页面源 HTML 中没有非空 Meta Description。"],
                },
            }
        if self.fail_performance:
            raise InternalProviderRequestError("sensitive upstream detail")
        return {
            "meta": {"capability": "page_performance", "source": "pagespeed"},
            "data": {"strategy": "mobile", "category_scores": {"performance": 78}},
        }


@pytest.mark.asyncio
async def test_agent_loop_runs_page_and_performance_workflow() -> None:
    facade = ExternalDataFixture()
    registry = build_seo_agent_registry(facade, SITE)
    provider = FakeModelProvider([
        [ModelChunk(tool_calls=[ToolCall(
            id="audit", name="seo_site_audit", arguments={"include_performance": True}
        )], finish_reason="tool_calls")],
        [ModelChunk(text="页面检查完成。"), ModelChunk(finish_reason="stop")],
    ])
    run = RunManager().create_run([{"role": "user", "content": "做页面基础体检并检查性能"}])

    await AgentLoop(provider, registry).run(run)

    assert run.status == RunStatus.COMPLETED
    assert [call["capability"] for call in facade.calls] == [
        Capability.PAGE_METADATA, Capability.PAGE_PERFORMANCE
    ]
    event = next(event for event in run.events if event.type == "tool.completed")
    result = event.payload["result"]["data"]
    assert event.payload["render_hint"] == "cards"
    assert result["workflow_status"] == "complete"
    assert result["schema_version"] == "1.0"
    assert result["title"]["fallback"] == "页面 SEO 基础体检"
    assert result["evidence"]["page"]["raw"]["data"]["title"] == "Example"
    assert result["evidence"]["performance"]["scores"][0]["value"] == 78
    assert any("收录" in item for item in result["limitations"])


@pytest.mark.asyncio
async def test_performance_failure_keeps_page_evidence_and_safe_error() -> None:
    facade = ExternalDataFixture(fail_performance=True)
    tool = build_seo_agent_registry(facade, SITE).get("seo_site_audit")

    result = await tool.execute({"include_performance": True})

    assert result.success
    assert result.data["workflow_status"] == "partial"
    assert "部分检查未完成" in result.data["description"]["fallback"]
    assert result.data["evidence"]["page"]["raw"]["data"]["title"] == "Example"
    assert result.data["evidence"]["performance"]["error_code"] == "UPSTREAM_TIMEOUT"
    assert "sensitive upstream detail" not in str(result.data)


@pytest.mark.asyncio
async def test_cached_page_is_visible_in_workflow_result() -> None:
    class CachedPage(ExternalDataFixture):
        async def query_live(self, **kwargs: Any) -> dict[str, Any]:
            response = await super().query_live(**kwargs)
            response["meta"]["cached"] = True
            return response

    facade = CachedPage(performance=False)
    result = await build_seo_agent_registry(facade, SITE).get("seo_site_audit").execute({})

    assert result.data["provenance"]["cached"] is True
    assert {"label": "读取方式", "value": "缓存数据"} in result.data["items"][0]["metadata"]


@pytest.mark.asyncio
async def test_optional_performance_and_site_boundary() -> None:
    facade = ExternalDataFixture(performance=False)
    tool = build_seo_agent_registry(facade, SITE).get("seo_site_audit")

    result = await tool.execute({})
    assert result.data["workflow_status"] == "complete"
    assert len(facade.calls) == 1

    result = await tool.execute({"include_performance": True})
    assert result.data["workflow_status"] == "partial"
    assert result.data["evidence"]["performance"]["error_code"] == "NOT_CONFIGURED"

    with pytest.raises(ValueError, match="当前站点"):
        await tool.execute({"url": "https://other.example/"})
    assert len(facade.calls) == 2


@pytest.mark.asyncio
async def test_performance_timeout_returns_page_before_agent_tool_timeout(monkeypatch) -> None:
    from src.seo_agent.chat import tools

    class SlowPerformance(ExternalDataFixture):
        async def query_live(self, **kwargs: Any) -> dict[str, Any]:
            if kwargs["capability"] == Capability.PAGE_PERFORMANCE:
                await asyncio.sleep(1)
            return await super().query_live(**kwargs)

    monkeypatch.setattr(tools.settings, "agent_tool_timeout", 0.25)
    facade = SlowPerformance()
    registry = build_seo_agent_registry(facade, SITE)
    provider = FakeModelProvider([
        [ModelChunk(tool_calls=[ToolCall(
            id="audit", name="seo_site_audit", arguments={"include_performance": True}
        )], finish_reason="tool_calls")],
        [ModelChunk(text="性能未获取，已保留页面数据。"), ModelChunk(finish_reason="stop")],
    ])
    run = RunManager().create_run([{"role": "user", "content": "检查页面和性能"}])

    await AgentLoop(provider, registry, tool_timeout=0.25).run(run)

    event = next(event for event in run.events if event.type == "tool.completed")
    assert event.payload["result"]["data"]["workflow_status"] == "partial"
    performance = event.payload["result"]["data"]["evidence"]["performance"]
    assert performance["error_code"] == "TOOL_TIMEOUT"


@pytest.mark.asyncio
@pytest.mark.parametrize("include_performance", [False, True])
async def test_audit_records_only_executed_tools_as_child_spans(
    tmp_path: Path, include_performance: bool
) -> None:
    memory = InMemorySpanExporter()
    database = ObservabilityDatabase(tmp_path / "observability.db")
    exporter = OpenTelemetryExporter(
        service_name="seo-agent", endpoint=None, span_exporter=memory, database=database
    )
    observer = ObservabilityClient(database, project_key="seo-agent", exporters=[exporter])
    facade = ExternalDataFixture()
    registry = build_seo_agent_registry(facade, SITE, observability=observer)
    provider = FakeModelProvider([
        [ModelChunk(tool_calls=[ToolCall(
            id="audit", name="seo_site_audit",
            arguments={
                "url": "https://example.com/private", "include_performance": include_performance
            },
        )], finish_reason="tool_calls")],
        [ModelChunk(text="完成。"), ModelChunk(finish_reason="stop")],
    ])
    run = RunManager().create_run([{"role": "user", "content": "检查页面"}])
    try:
        await AgentLoop(
            provider, registry, observer=AgentRunObserver(observer, operation_name="seo_agent.chat")
        ).run(run)
        rows = await database.fetch_all(
            "SELECT id, parent_span_id, operation_name, status FROM obs_spans"
        )
        by_name = {row["operation_name"]: row for row in rows}
        assert by_name["seo_page_metadata"]["parent_span_id"] == by_name["seo_site_audit"]["id"]
        assert by_name["seo_page_metadata"]["status"] == "COMPLETED"
        assert ("seo_page_performance" in by_name) is include_performance
        if include_performance:
            assert (
                by_name["seo_page_performance"]["parent_span_id"] == by_name["seo_site_audit"]["id"]
            )
        spans = {span.name: span for span in memory.get_finished_spans()}
        assert spans["seo_page_metadata"].attributes["agent.component_name"] == "page_html"
        assert spans["seo_page_metadata"].attributes["langfuse.observation.type"] == "tool"
        if include_performance:
            assert spans["seo_page_performance"].attributes["agent.component_name"] == "pagespeed"
            assert spans["seo_page_performance"].attributes["langfuse.observation.type"] == "tool"
        assert "example.com/private" in str([dict(span.attributes) for span in spans.values()])
    finally:
        await observer.shutdown()


@pytest.mark.asyncio
async def test_performance_failure_marks_child_span_but_keeps_workflow_partial(
    tmp_path: Path,
) -> None:
    database = ObservabilityDatabase(tmp_path / "observability.db")
    observer = ObservabilityClient(database, project_key="seo-agent")
    registry = build_seo_agent_registry(
        ExternalDataFixture(fail_performance=True), SITE, observability=observer
    )
    provider = FakeModelProvider([
        [ModelChunk(tool_calls=[ToolCall(
            id="audit", name="seo_site_audit", arguments={"include_performance": True}
        )], finish_reason="tool_calls")],
        [ModelChunk(text="性能未获取。"), ModelChunk(finish_reason="stop")],
    ])
    run = RunManager().create_run([{"role": "user", "content": "检查页面和性能"}])
    try:
        await AgentLoop(
            provider, registry, observer=AgentRunObserver(observer, operation_name="seo_agent.chat")
        ).run(run)
        row = await database.fetch_one(
            "SELECT status FROM obs_spans WHERE operation_name = 'seo_page_performance'"
        )
        assert row is not None and row["status"] == "FAILED"
        event = next(event for event in run.events if event.type == "tool.completed")
        assert event.payload["result"]["data"]["workflow_status"] == "partial"
    finally:
        await observer.shutdown()
