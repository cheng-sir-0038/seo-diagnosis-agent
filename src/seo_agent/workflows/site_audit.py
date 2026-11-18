from __future__ import annotations

import time
from typing import Any

from src.agent.observability import run_observed_tool
from src.agent.tools.base import AgentTool, ToolProtocol, ToolResult, ToolRuntimeContext
from src.core.exceptions import InternalAdapterError
from src.observability.client import ObservabilityClient
from src.seo_agent.workflows.page_findings import page_findings
from src.seo_presentation.contracts import base_contract


class SiteAuditWorkflow(AgentTool):
    name = "seo_site_audit"
    description = (
        "对当前站点的一个页面做 SEO 基础体检：读取源 HTML 并整理元信息证据；"
        "用户明确要求性能检查时，可选调用 PageSpeed。不是整站爬取或 Google 收录检查。"
    )
    render_hint = "cards"
    parameters_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "站内完整页面 URL，默认站点首页。"},
            "include_performance": {
                "type": "boolean", "default": False,
                "description": "仅在用户要求性能检查时开启，可能消耗外部服务额度。",
            },
            "strategy": {"type": "string", "enum": ["mobile", "desktop"], "default": "mobile"},
            "force_refresh": {"type": "boolean", "default": False},
        },
        "required": [],
        "additionalProperties": False,
    }

    def __init__(
        self, page: ToolProtocol, performance: ToolProtocol | None, timeout: float,
        *, observability: ObservabilityClient | None = None,
    ) -> None:
        self.page = page
        self.performance = performance
        self.timeout = timeout
        self.observability = observability

    async def execute(
        self, arguments: dict[str, Any], runtime: ToolRuntimeContext | None = None
    ) -> ToolResult:
        if set(arguments) - set(self.parameters_schema["properties"]):
            raise ValueError("包含不支持的体检参数")
        include_performance = arguments.get("include_performance", False)
        force_refresh = arguments.get("force_refresh", False)
        if not isinstance(include_performance, bool) or not isinstance(force_refresh, bool):
            raise ValueError("include_performance 和 force_refresh 必须是布尔值")
        strategy = arguments.get("strategy", "mobile")
        if strategy not in {"mobile", "desktop"}:
            raise ValueError("strategy 仅支持 mobile 或 desktop")

        started = time.monotonic()
        deadline = started + self.timeout
        inherited = runtime.metadata.get("workflow_deadline") if runtime else None
        if isinstance(inherited, (int, float)):
            deadline = min(deadline, inherited)
        page_args = {key: arguments[key] for key in ("url", "force_refresh") if key in arguments}
        page_result = await run_observed_tool(
            self.observability, self.page, page_args, runtime=runtime, component_name="page_html"
        )
        if not page_result.success:
            return page_result
        page = page_result.data
        evidence: dict[str, Any] = {"page": page}
        raw = page.get("raw", {})
        raw_page = raw.get("data", {})
        cached = raw.get("meta", {}).get("cached") is True
        status_code = raw_page.get("status_code") if isinstance(raw_page, dict) else None
        complete = (
            type(status_code) is int and 200 <= status_code < 300
            and status_code not in {204, 205, 206} and raw_page.get("content_length_bytes") != 0
        )
        findings = page_findings(raw_page, cached=cached) if complete else []
        warnings = [str(item) for item in page.get("warnings", [])]
        summary = " ".join(warnings) if warnings else "已读取页面元信息。"
        if findings:
            summary = f"已读取页面元信息，有 {len(findings)} 项观察可核对。"
        elif not complete:
            summary = (
                f"页面返回 HTTP {status_code}，本次无法确认页面标签状态。"
                if status_code is not None else "缺少有效的 HTTP 页面证据。"
            )
        checks = [
            {"label": str(item.get("label")), "value": str(item.get("value") or "未提供")}
            for item in page.get("checks", []) if isinstance(item, dict)
        ] if complete else []
        cards = [{
            "key": "page",
            "title": "页面源 HTML",
            "summary": summary,
            "metadata": checks + [{"label": "数据来源", "value": "页面源 HTML"}],
        }]
        if cached:
            cards[0]["metadata"].append({"label": "读取方式", "value": "缓存数据"})
        cards.extend(finding.card() for finding in findings)
        status = "complete" if complete else "partial"

        if include_performance:
            performance: dict[str, Any]
            if self.performance is None:
                performance = {"error_code": "NOT_CONFIGURED", "message": "性能数据源未配置。"}
            else:
                # shortcut: shared deadline; persist steps separately for mid-check recovery.
                remaining = (
                    deadline - time.monotonic() - min(0.5, self.timeout / 10)
                )
                try:
                    if remaining <= 0:
                        raise TimeoutError
                    result = await run_observed_tool(
                        self.observability, self.performance, {**page_args, "strategy": strategy},
                        runtime=runtime, component_name="pagespeed", timeout=remaining,
                    )
                    performance = result.data if result.success else {
                        "error_code": result.error_code or "TOOL_FAILED",
                        "message": "性能检查失败。",
                    }
                except TimeoutError:
                    performance = {"error_code": "TOOL_TIMEOUT", "message": "性能检查超时。"}
                except InternalAdapterError as exc:
                    performance = {
                        "error_code": exc.error_code.value, "message": "性能数据暂不可用。"
                    }
            evidence["performance"] = performance
            if "error_code" in performance:
                status = "partial"
                cards.append({
                    "key": "performance", "title": "页面性能", "summary": performance["message"]
                })
            else:
                cards.append({
                    "key": "performance", "title": "页面性能",
                    "summary": f"{strategy} 实验室检测结果。",
                    "metadata": [
                        {"label": str(item.get("label")), "value": str(item.get("value"))}
                        for item in performance.get("scores", []) if isinstance(item, dict)
                    ] + [{"label": "数据来源", "value": "PageSpeed Insights"}],
                })

        presentation = base_contract(
            capability="site_audit",
            view_type="cards",
            title_key="seo.site_audit.title",
            title_fallback="页面 SEO 基础体检",
            description_key="seo.site_audit.description",
            description_fallback=(
                "已获取页面证据；部分检查未完成。"
                if status == "partial" else "基于本次只读查询整理页面证据。"
            ),
            response=raw,
            limitations=[
                "只检查一个页面的源 HTML，不代表整站，也不能证明 Google 抓取或收录状态。",
                "性能数据仅在用户要求且数据源可用时获取；实验室结果不代表真实用户体验。",
            ],
        )
        presentation.update({
            "items": cards,
            "workflow_status": status,
            "evidence": evidence,
            "findings": [finding.model_dump(mode="json") for finding in findings],
        })
        presentation["provenance"]["cached"] = raw.get("meta", {}).get("cached")
        return ToolResult(success=True, data=presentation)
