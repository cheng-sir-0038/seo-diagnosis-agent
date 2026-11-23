from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from src.agent.models import HumanInputRequest
from src.agent.tools.base import AgentTool, RenderHint, ToolResult, ToolRuntimeContext
from src.seo_presentation.contracts import base_contract


class RuleStatus(StrEnum):
    MATCHED = "matched"
    OUTSIDE_SCOPE = "outside_scope"
    UNCONFIRMED = "unconfirmed"
    NOT_EXEMPT = "not_exempt_under_rule"
    NO_RECORD = "no_record"


class EvidenceSource(StrEnum):
    OFFICIAL_EXCERPT = "official_excerpt"


class HealthcareTool(AgentTool):
    def resolve_render_hint(self, result_payload: dict[str, Any]) -> RenderHint | None:
        return "cards" if (result_payload.get("data") or {}).get("items") else None

    def cards(
        self,
        title: str,
        source: str,
        conclusion: str,
        items: list[dict],
        **facts: Any,
    ) -> ToolResult:
        data = base_contract(
            capability=self.name,
            view_type="cards",
            title_key=f"{self.name}.title",
            title_fallback=title,
            description_key=f"{self.name}.description",
            description_fallback=conclusion,
            response={"meta": {"source": source}},
        )
        return ToolResult(success=True, data={**data, "items": items, **facts})


def ask(field: str, question: str, confirmed: dict) -> ToolResult:
    return ToolResult(
        success=True,
        data={"status": "needs_input", "pending_field": field, "confirmed": confirmed},
        metadata={"human_input": HumanInputRequest(questions=[question]).model_dump(mode="json")},
    )


def resumed_arguments(
    tool: str,
    arguments: dict,
    runtime: ToolRuntimeContext | None,
    parse_reply: Callable[[str, str], object | None],
) -> tuple[dict, ToolResult | None]:
    resume = runtime.metadata.get("human_input_resume") if runtime else None
    if not resume or resume.get("tool_name") != tool or resume.get("tool_arguments") != arguments:
        return arguments, None
    previous = resume["tool_result"]["data"]
    confirmed = previous["confirmed"]
    answer = str(resume["answer"]).strip()
    field = previous["pending_field"]
    parsed = parse_reply(field, answer)
    if isinstance(parsed, ToolResult):
        return confirmed, parsed
    if parsed is not None:
        return {**confirmed, field: parsed}, None
    # Let the existing model interpret a free-form correction (possibly to several fields)
    # and issue a new typed query. The answer itself is never returned as rule evidence.
    return confirmed, ToolResult(
        success=True,
        data={
            "status": "clarification_received",
            "pending_field": field,
            "confirmed": confirmed,
            "answer": answer,
        },
    )
