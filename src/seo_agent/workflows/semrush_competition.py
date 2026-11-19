from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.agent.tools.base import AgentTool, ToolProtocol, ToolResult, ToolRuntimeContext
from src.observability.client import ObservabilityClient
from src.seo_agent.workflows.execution import WorkflowSteps, evidence_rows


class SemrushCompetitionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    country: str | None = Field(default=None, pattern=r"^[a-zA-Z]{2}$")
    limit: int = Field(default=3, ge=1, le=3, description="排名词和竞品各取的最多条数")
    force_refresh: bool = False


class SemrushCompetitionWorkflow(AgentTool):
    name = "seo_semrush_competition"
    description = (
        "用 Semrush 查看当前根域同一市场的自然搜索竞争。"
        "组合月度流量价值估算、少量排名词及位次变化、共同关键词和竞品竞争度。"
        "适合用户明确要求 Semrush 竞品分析或这组指标时使用。"
    )
    parameters_schema = SemrushCompetitionArguments.model_json_schema()
    render_hint = "cards"

    def __init__(
        self, tools: dict[str, ToolProtocol], timeout: float, *, default_country: str,
        observability: ObservabilityClient | None = None,
    ) -> None:
        self.tools = tools
        self.timeout = timeout
        self.default_country = default_country
        self.observability = observability

    async def execute(
        self, arguments: dict[str, Any], runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        options = SemrushCompetitionArguments.model_validate(arguments)
        country = (options.country or self.default_country).upper()
        params = {
            "country": country, "limit": options.limit, "force_refresh": options.force_refresh,
        }
        steps = WorkflowSteps(self.tools, self.timeout, self.observability, runtime)
        sections = (
            ("overview", "seo_search_metrics"),
            ("keywords", "seo_organic_keywords"),
            ("competitors", "seo_organic_competitors"),
        )
        blocked_reason = None
        overview = {}
        for section, tool in sections:
            if blocked_reason:
                steps.skip(section, blocked_reason)
                continue
            report = await steps.run(section, tool, params)
            coverage = steps.coverage[section]
            code = coverage.get("error_code")
            if code in {"AUTH_FAILED", "QUOTA_EXHAUSTED", "RATE_LIMITED", "INVALID_PARAMS"}:
                blocked_reason = coverage.get("reason") or f"Semrush 查询受阻（{code}）"
            if section == "overview" and report is not None:
                overview = report["raw"]["data"]
                if overview.get("data_date"):
                    params["month"] = overview["data_date"][:7]

        items = []
        if overview:
            items.append({
                "key": "overview", "title": "本站自然搜索", "summary": f"{country} 桌面市场",
                "metadata": _metadata(overview, (
                    ("semrush_rank", "Semrush Rank"), ("organic_keywords_count", "排名关键词"),
                    ("organic_traffic_estimate", "月度流量估算"),
                    ("traffic_value_estimate", "流量价值估算（USD）"),
                )),
            })
        for index, row in enumerate(evidence_rows(steps.evidence.get("keywords"))):
            items.append({
                "key": f"keyword-{index}", "title": row["keyword"],
                "summary": "排名页面：" + (row.get("url") or "未提供"),
                "metadata": _metadata(row, (
                    ("position", "当前排名"), ("previous_position", "上月排名"),
                    ("position_change", "位次变化"), ("search_volume", "月均搜索量"),
                    ("cpc", "CPC（USD）"),
                )),
            })
        for index, row in enumerate(evidence_rows(steps.evidence.get("competitors"))):
            items.append({
                "key": f"competitor-{index}", "title": row["domain"], "summary": "自然搜索竞品",
                "metadata": _metadata(row, (
                    ("common_keywords_count", "共同关键词"),
                    ("competition_density", "搜索竞争度（0–100）"),
                    ("organic_traffic_estimate", "月度流量估算"),
                )),
            })
        result = steps.finish(
            "semrush_competition", "自然搜索竞品分析", items,
            [
                "Semrush 按地区和设备提供月度第三方估算。流量价值按等值广告成本估算。",
                "竞品由自然搜索词重合发现。排名词和竞品样本各至多三条。",
            ],
            country=country, device_type="DESKTOP", month=params.get("month"),
        )
        if not result.success and blocked_reason:
            result.error = blocked_reason
            result.error_code = code
        return result


def _metadata(row: dict[str, Any], fields: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    metadata = [{"label": label, "value": str(row[key])}
                for key, label in fields if row.get(key) is not None]
    if row.get("data_date"):
        metadata.append({"label": "数据月份", "value": row["data_date"][:7]})
    return metadata
