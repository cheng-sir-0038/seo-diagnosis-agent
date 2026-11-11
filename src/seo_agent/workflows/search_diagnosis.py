from __future__ import annotations

from typing import Any

from src.agent.tools.base import AgentTool, ToolProtocol, ToolResult, ToolRuntimeContext
from src.observability.client import ObservabilityClient
from src.seo_agent.workflows.execution import (
    ResearchArguments,
    WorkflowSteps,
    evidence_rows,
    research_params,
)


class SearchDiagnosisWorkflow(AgentTool):
    name = "seo_search_diagnosis"
    description = (
        "诊断当前站点的自然搜索现状：组合站点指标与有限数量的排名关键词，"
        "保留来源和对应页面。不提供第一方流量、完整历史或下降原因证明。"
    )
    parameters_schema = ResearchArguments.model_json_schema()
    render_hint = "cards"

    def __init__(
        self,
        tools: dict[str, ToolProtocol],
        timeout: float,
        *,
        observability: ObservabilityClient | None = None,
    ) -> None:
        self.tools, self.timeout, self.observability = tools, timeout, observability

    async def execute(
        self,
        arguments: dict[str, Any],
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        options = ResearchArguments.model_validate(arguments)
        steps = WorkflowSteps(self.tools, self.timeout, self.observability, runtime)
        params = research_params(options)
        metrics = await steps.run("metrics", "seo_search_metrics", params)
        keywords = await steps.run("keywords", "seo_organic_keywords", params)
        items = []
        if metrics:
            data = metrics.get("raw", {}).get("data", {})
            items.append(
                {
                    "key": "metrics",
                    "title": "自然搜索现状",
                    "summary": "第三方数据源估算，不等于实际访问量。",
                    "metadata": [
                        {"label": label, "value": str(data[key])}
                        for key, label in (
                            ("organic_keywords_count", "关键词数量"),
                            ("organic_traffic_estimate", "流量估算"),
                        )
                        if isinstance(data, dict) and data.get(key) is not None
                    ],
                }
            )
        for index, row in enumerate(evidence_rows(keywords)[: options.limit]):
            items.append(
                {
                    "key": f"keyword-{index}",
                    "title": str(row.get("keyword") or "未提供关键词"),
                    "summary": "对应页面：" + str(row.get("url") or "数据源未提供"),
                    "metadata": [{"label": "排名", "value": str(row.get("position") or "未知")}],
                }
            )
        return steps.finish(
            "search_diagnosis",
            "自然搜索现状诊断",
            items,
            [
                "指标和排名来自第三方 SEO 数据，不是 GSC/GA4 的第一方统计。",
                "只覆盖本次有限样本；没有完整历史数据，不能据此认定流量下降或其原因。",
            ],
        )
