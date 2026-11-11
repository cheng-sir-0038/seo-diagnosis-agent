from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from src.agent.tools.base import AgentTool, ToolProtocol, ToolResult, ToolRuntimeContext
from src.observability.client import ObservabilityClient
from src.seo_agent.workflows.execution import (
    ResearchArguments,
    WorkflowSteps,
    evidence_rows,
    research_params,
)


class RankingPageArguments(ResearchArguments):
    limit: int = Field(default=20, ge=1, le=50)
    min_position: int = Field(default=4, ge=1, le=100)
    max_position: int = Field(default=20, ge=1, le=100)
    max_pages: int = Field(default=2, ge=1, le=3)
    include_performance: bool = False

    @model_validator(mode="after")
    def validate_range(self):
        if self.min_position > self.max_position:
            raise ValueError("最小排名不能大于最大排名")
        return self


class RankingPageOptimizationWorkflow(AgentTool):
    name = "seo_ranking_page_optimization"
    description = (
        "调查少量已有排名的站内页面：默认从第4-20名关键词样本选择最多2页，"
        "核对源HTML和每页一个代表关键词的SERP，提供优化判断所需证据。"
        "不修改页面；仅用户要求时检查性能，不证明优化会带来增长。"
    )
    parameters_schema = RankingPageArguments.model_json_schema()
    render_hint = "cards"

    def __init__(
        self,
        tools: dict[str, ToolProtocol],
        site: dict[str, Any],
        timeout: float,
        *,
        observability: ObservabilityClient | None = None,
    ) -> None:
        self.tools, self.site, self.timeout, self.observability = (
            tools,
            site,
            timeout,
            observability,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        options = RankingPageArguments.model_validate(arguments)
        steps = WorkflowSteps(self.tools, self.timeout, self.observability, runtime)
        params = research_params(options)
        rankings = await steps.run("rankings", "seo_organic_keywords", params)
        target = self.site["domain"]
        domain = urlsplit(target if "://" in target else f"https://{target}").hostname or ""
        domain = domain.lower().removeprefix("www.")
        pages: dict[str, dict[str, Any]] = {}
        for row in evidence_rows(rankings):
            position, keyword, url = row.get("position"), row.get("keyword"), row.get("url")
            if (
                isinstance(position, bool)
                or not isinstance(position, (int, float))
                or not options.min_position <= position <= options.max_position
                or not isinstance(keyword, str)
                or not keyword.strip()
                or not isinstance(url, str)
            ):
                continue
            try:
                parsed = urlsplit(url)
            except ValueError:
                continue
            if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
                continue
            if (parsed.hostname or "").lower().removeprefix("www.") != domain:
                continue
            url = parsed._replace(fragment="").geturl()
            if url not in pages and len(pages) < options.max_pages:
                pages[url] = {
                    "url": url,
                    "keywords": [],
                    "position": position,
                    "page_verified": False,
                }
            if url in pages and keyword not in pages[url]["keywords"]:
                pages[url]["keywords"].append(keyword)
        items = []
        for index, page in enumerate(pages.values()):
            source = await steps.run(
                f"page_{index}",
                "seo_page_metadata",
                {
                    "url": page["url"],
                    "force_refresh": options.force_refresh,
                },
            )
            raw = (source or {}).get("raw", {}).get("data", {})
            status = raw.get("status_code") if isinstance(raw, dict) else None
            page["page_verified"] = isinstance(status, int) and 200 <= status < 300
            if page["page_verified"]:
                page.update(
                    title=raw.get("title"),
                    meta_description=raw.get("meta_description"),
                    h1s=raw.get("h1s", []),
                    canonical_url=raw.get("canonical_url"),
                )
                serp = await steps.run(
                    f"serp_{index}",
                    "seo_serp_overview",
                    {
                        **params,
                        "keyword": page["keywords"][0],
                        "purpose": "seo_analysis",
                    },
                )
                page["serp_keyword"] = page["keywords"][0] if serp is not None else None
                if options.include_performance:
                    await steps.run(
                        f"performance_{index}",
                        "seo_page_performance",
                        {
                            "url": page["url"],
                            "force_refresh": options.force_refresh,
                        },
                    )
            else:
                if source is not None:
                    steps.coverage[f"page_{index}"]["status"] = "partial"
                steps.skip(f"serp_{index}", "页面未核验，尚不能给出标签优化结论")
                if options.include_performance:
                    steps.skip(f"performance_{index}", "页面未核验，未继续请求性能数据")
            items.append(
                {
                    "key": f"page-{index}",
                    "title": page["keywords"][0],
                    "url": page["url"],
                    "summary": "已取得页面证据，可结合搜索意图判断。"
                    if page["page_verified"]
                    else "页面未核验，保留排名样本等待补查。",
                    "metadata": [
                        {"label": "样本排名", "value": str(page["position"])},
                        {"label": "当前标题", "value": str(page.get("title") or "未知")},
                    ],
                }
            )
        return steps.finish(
            "ranking_page_optimization",
            "已有排名页优化调查",
            items,
            [
                "仅从本次排名样本筛选有限页面；未找到匹配页不代表全站没有优化机会。",
                "每页只核验一个代表关键词的SERP；第三方排名不等于第一方流量或点击数据。",
                "页面标签差异只是观察，建议需结合页面用途；不会修改、批准或发布内容。",
            ],
            optimization_candidates=list(pages.values()),
        )
