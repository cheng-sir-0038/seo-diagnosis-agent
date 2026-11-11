from __future__ import annotations

from typing import Any

from pydantic import Field

from src.agent.tools.base import AgentTool, ToolProtocol, ToolResult, ToolRuntimeContext
from src.observability.client import ObservabilityClient
from src.seo_agent.workflows.execution import (
    ResearchArguments,
    WorkflowSteps,
    evidence_rows,
    research_params,
)


class SiteDiagnosisArguments(ResearchArguments):
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    query: str | None = Field(default=None, min_length=1, max_length=80)
    include_performance: bool = False
    include_competitors: bool = False


class SiteDiagnosisWorkflow(AgentTool):
    name = "seo_site_diagnosis"
    description = (
        "汇总站点的代表页面、自然搜索现状和可选关键词机会、竞品证据，形成结构化诊断报告。"
        "查询范围有限，不是整站爬取或收录审计。仅在用户要求时开启性能和竞品查询。"
    )
    parameters_schema = SiteDiagnosisArguments.model_json_schema()
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
        options = SiteDiagnosisArguments.model_validate(arguments)
        if options.query is not None and not options.query.strip():
            raise ValueError("研究主题不能为空")
        if options.query is not None and len(options.query.split()) > 10:
            raise ValueError("研究主题最多包含 10 个词")
        steps = WorkflowSteps(self.tools, self.timeout, self.observability, runtime)
        page_args = {
            "include_performance": options.include_performance,
            "force_refresh": options.force_refresh,
        }
        if options.url is not None:
            page_args["url"] = options.url
        await steps.run("page", "seo_site_audit", page_args)
        if steps.coverage["page"].get("error_code") == "INVALID_ARGUMENT":
            return steps.finish(
                "site_diagnosis", "站点诊断报告", [], ["页面地址必须属于当前站点。"]
            )
        params = research_params(options)
        await steps.run("search", "seo_search_diagnosis", params)
        if options.query is not None:
            await steps.run(
                "opportunities",
                "seo_keyword_opportunity",
                {**params, "query": options.query.strip()},
            )
        else:
            steps.skip("opportunities", "未指定研究主题")
        if options.include_competitors:
            await steps.run("competitors", "seo_organic_competitors", params)
        else:
            steps.skip("competitors", "未要求竞品查询")
        for key in ("robots_txt", "sitemap", "indexing"):
            steps.coverage[key] = {"status": "not_implemented"}
        items = []
        for section, payload in steps.evidence.items():
            for item in payload.get("items", []):
                items.append({**item, "key": f"{section}-{item['key']}"})
        for index, row in enumerate(
            evidence_rows(steps.evidence.get("competitors"))[: options.limit]
        ):
            items.append(
                {
                    "key": f"competitor-{index}",
                    "title": str(row.get("domain") or "未提供域名"),
                    "summary": "自然搜索竞品样本，不代表完整关键词差距。",
                    "metadata": [
                        {
                            "label": "交集关键词数",
                            "value": str(row.get("common_keywords_count", "未知")),
                        }
                    ],
                }
            )
        return steps.finish(
            "site_diagnosis",
            "站点诊断报告",
            items,
            [
                "仅汇总本次代表页面和有限搜索样本；完成状态只针对已支持的受检范围。",
                "robots.txt、sitemap 和 Google 收录尚未检查，不能解释为检查通过。",
                "结果用于形成诊断建议；不自动将候选入池、批准机会、创建任务或发布。",
            ],
        )
