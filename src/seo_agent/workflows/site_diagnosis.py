from __future__ import annotations

from typing import Any

from pydantic import Field

from src.agent.tools.base import AgentTool, ToolProtocol, ToolResult, ToolRuntimeContext
from src.core.exceptions import InternalAdapterError
from src.observability.client import ObservabilityClient
from src.providers.page_metadata.client import normalize_site_url, normalized_host, site_origin
from src.providers.page_metadata.site_resources import same_origin_location
from src.seo_agent.workflows.execution import (
    ResearchArguments,
    WorkflowSteps,
    evidence_rows,
    research_params,
)


class SiteDiagnosisArguments(ResearchArguments):
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    sitemap_url: str | None = Field(
        default=None, min_length=1, max_length=2048,
        description="用户明确给出时使用的同源站点地图地址。",
    )
    query: str | None = Field(default=None, min_length=1, max_length=80)
    include_performance: bool = False
    include_competitors: bool = False


class SiteDiagnosisWorkflow(AgentTool):
    name = "seo_site_diagnosis"
    description = (
        "读取站点代表页面、robots.txt 和站点地图，形成含证据与覆盖范围的技术诊断；"
        "有可用数据源时补充自然搜索现状和指定主题的关键词机会。"
        "仅在用户要求时开启性能和竞品查询。"
    )
    parameters_schema = SiteDiagnosisArguments.model_json_schema()
    render_hint = "cards"

    def __init__(
        self,
        tools: dict[str, ToolProtocol],
        timeout: float,
        *,
        site_domain: str,
        observability: ObservabilityClient | None = None,
    ) -> None:
        self.tools, self.timeout, self.observability = tools, timeout, observability
        self.site_domain = site_domain

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
        try:
            target = normalize_site_url(options.url or self.site_domain)
        except (ValueError, InternalAdapterError):
            return ToolResult(success=False, error_code="INVALID_PARAMS", error="页面地址无效。")
        if normalized_host(target) != normalized_host(self.site_domain):
            return ToolResult(
                success=False, error_code="INVALID_PARAMS", error="页面地址必须属于当前站点。"
            )
        sitemap_url = None
        if options.sitemap_url is not None:
            sitemap_url = same_origin_location(options.sitemap_url, target)
            if sitemap_url is None:
                return ToolResult(
                    success=False, error_code="INVALID_PARAMS",
                    error="站点地图地址必须与受检页面同源。",
                )

        steps = WorkflowSteps(self.tools, self.timeout, self.observability, runtime)
        page_args = {
            "include_performance": options.include_performance,
            "force_refresh": options.force_refresh,
        }
        if options.url is not None:
            page_args["url"] = options.url
        page = await steps.run("page", "seo_site_audit", page_args)
        if steps.coverage["page"].get("error_code") == "INVALID_ARGUMENT":
            return steps.finish(
                "site_diagnosis", "站点诊断报告", [], ["页面地址必须属于当前站点。"]
            )
        page_evidence = (page or {}).get("evidence", {}).get("page", {})
        source_page = page_evidence.get("raw", {}).get("data", {})
        resource_base = target if sitemap_url else source_page.get("final_url") or target
        origin = site_origin(resource_base)
        robots_url = f"{origin}/robots.txt"
        robots = await self._read_resource(
            steps, "robots_txt", "robots", robots_url, options.force_refresh
        )
        declarations = robots.get("declared_sitemaps") or []
        sitemap_url = sitemap_url or (
            declarations[0] if declarations else f"{origin}/sitemap.xml"
        )
        sitemap = await self._read_resource(
            steps, "sitemap", "sitemap", sitemap_url, options.force_refresh
        )

        params = research_params(options)
        if "seo_search_diagnosis" in self.tools:
            await steps.run("search", "seo_search_diagnosis", params)
        else:
            steps.skip("search", "当前没有可用的搜索指标和排名数据源")
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
        steps.coverage["indexing"] = {"status": "not_implemented"}
        items = []
        for section, payload in steps.evidence.items():
            for item in payload.get("items", []):
                items.append({**item, "key": f"{section}-{item['key']}"})
        items.extend([
            _resource_card("robots_txt", "抓取规则", robots, robots_url),
            _resource_card("sitemap", "站点地图", sitemap, sitemap_url),
        ])
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
                "抓取规则仅展示读到的指令。站点地图最多展示 5 个同源条目，不会逐个抓取。",
                "站点地图索引的抽样条目是子地图。本次不展开子地图，也未核查 Google 实际收录。",
                "结果用于形成诊断建议；不自动将候选入池、批准机会、创建任务或发布。",
            ],
            findings=(page or {}).get("findings", []),
        )

    @staticmethod
    async def _read_resource(
        steps: WorkflowSteps, key: str, resource_type: str, url: str, force_refresh: bool
    ) -> dict[str, Any]:
        result = await steps.run(key, "seo_site_resource", {
            "resource_type": resource_type, "url": url, "force_refresh": force_refresh,
        })
        response = (result or {}).get("raw", {})
        report = response.get("data", {})
        status = report.get("status")
        if status in {"http_error", "invalid"}:
            steps.coverage[key].update(
                status="partial",
                error_code="INVALID_SITE_FILE" if status == "invalid" else "UPSTREAM_ERROR",
            )
        return {**report, "cached": True} if response.get("meta", {}).get("cached") else report


def _resource_card(
    key: str, title: str, report: dict[str, Any], requested_url: str
) -> dict[str, Any]:
    status = report.get("status")
    summary = {
        "parsed": "已读取站点文件。",
        "not_found": "服务器返回文件不存在或已移除。",
        "http_error": "站点文件查询返回 HTTP 错误。",
        "invalid": report.get("message"),
    }
    metadata = [{"label": "来源地址", "value": report.get("final_url") or requested_url}]
    if report.get("cached"):
        metadata.append({"label": "读取方式", "value": "缓存数据"})
    if status_code := report.get("status_code"):
        metadata.append({"label": "HTTP 状态", "value": str(status_code)})
    if rules := report.get("rules"):
        metadata.append({"label": "抓取指令样例", "value": " · ".join(rules)})
    if declarations := report.get("declared_sitemaps"):
        metadata.append({"label": "同源 Sitemap 声明", "value": " · ".join(declarations)})
    if isinstance(report.get("entry_count"), int):
        label = "子地图条目" if report.get("document_type") == "sitemapindex" else "URL 条目"
        metadata.append({"label": label, "value": str(report["entry_count"])})
    if excluded := report.get("excluded_entry_count"):
        metadata.append({"label": "跳过的跨源或无效条目", "value": str(excluded)})
    if samples := report.get("sample_urls"):
        metadata.extend({"label": f"样例 {index}", "value": url}
                        for index, url in enumerate(samples, start=1))
    return {
        "key": key, "title": title, "metadata": metadata,
        "summary": summary.get(status) or "本次未取得可用的站点文件证据。",
    }
