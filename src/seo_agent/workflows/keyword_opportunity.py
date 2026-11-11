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


class KeywordOpportunityArguments(ResearchArguments):
    query: str = Field(min_length=1, max_length=80)


class KeywordOpportunityWorkflow(AgentTool):
    name = "seo_keyword_opportunity"
    description = (
        "围绕主题研究关键词机会：读取搜索联想、批量核对有限候选词的指标，"
        "并查看输入主题词的 SERP。返回候选与证据，不自动入池、审批或创建任务。"
    )
    parameters_schema = KeywordOpportunityArguments.model_json_schema()
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
        options = KeywordOpportunityArguments.model_validate(arguments)
        query = options.query.strip()
        if not query:
            raise ValueError("研究主题不能为空")
        if len(query.split()) > 10:
            raise ValueError("研究主题最多包含 10 个词")
        steps = WorkflowSteps(self.tools, self.timeout, self.observability, runtime)
        params = research_params(options)
        suggestions = await steps.run(
            "suggestions",
            "seo_autocomplete",
            {key: value for key, value in {**params, "query": query}.items() if key != "limit"},
        )
        candidates = [query]
        seen = {query.casefold()}
        for row in evidence_rows(suggestions):
            word = row.get("value")
            if not isinstance(word, str):
                continue
            word = word.strip()
            if (
                word
                and len(word) <= 80
                and len(word.split()) <= 10
                and word.casefold() not in seen
                and len(candidates) < options.limit
            ):
                candidates.append(word)
                seen.add(word.casefold())
        metrics = await steps.run(
            "metrics", "seo_keyword_overview", {**params, "keywords": candidates}
        )
        serp = await steps.run(
            "serp", "seo_serp_overview", {**params, "keyword": query, "purpose": "research"}
        )
        by_keyword = {
            str(row.get("keyword") or "").casefold(): row for row in evidence_rows(metrics)
        }
        rows = [
            {
                "keyword": word,
                "search_volume": by_keyword.get(word.casefold(), {}).get("search_volume"),
                "keyword_difficulty": by_keyword.get(word.casefold(), {}).get("keyword_difficulty"),
                "intent": by_keyword.get(word.casefold(), {}).get("intent"),
            }
            for word in candidates
        ]
        items = [
            {
                "key": f"candidate-{index}",
                "title": row["keyword"],
                "summary": "待结合业务价值判断的关键词候选。",
                "metadata": [
                    {"label": label, "value": str(row[key]) if row[key] is not None else "未知"}
                    for key, label in (
                        ("search_volume", "搜索量估算"),
                        ("keyword_difficulty", "关键词难度"),
                    )
                ],
            }
            for index, row in enumerate(rows)
        ]
        return steps.finish(
            "keyword_opportunity",
            "关键词机会研究",
            items,
            [
                "候选词和指标是研究证据，尚未成为已批准的业务机会；不会自动创建任务。",
                "联想词不代表搜索量；缺失指标保留为未知，不按零或低难度处理。",
                "SERP 仅查询输入主题词，不代表每个候选词都经过实时排名核验。",
            ],
            keyword_candidates=rows,
            serp_keyword=query if serp is not None else None,
        )
