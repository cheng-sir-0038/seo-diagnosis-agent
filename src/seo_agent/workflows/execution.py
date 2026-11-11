from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.agent.observability import run_observed_tool
from src.agent.tools.base import ToolProtocol, ToolResult, ToolRuntimeContext
from src.core.exceptions import InternalAdapterError
from src.observability.client import ObservabilityClient
from src.seo_presentation.contracts import base_contract, build_provenance


class ResearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    country: str | None = Field(default=None, pattern=r"^[a-zA-Z]{2}$")
    language: str | None = Field(default=None, min_length=2, max_length=16)
    limit: int = Field(default=5, ge=1, le=10)
    force_refresh: bool = False


def evidence_rows(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = (payload or {}).get("raw", {}).get("data")
    return [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []


def research_params(options: ResearchArguments) -> dict[str, Any]:
    return options.model_dump(
        exclude_none=True, include={"country", "language", "limit", "force_refresh"}
    )


class WorkflowSteps:
    """执行有限的业务步骤，共享截止时间并保留已经取得的证据。"""

    def __init__(
        self,
        tools: dict[str, ToolProtocol],
        timeout: float,
        observability: ObservabilityClient | None,
        runtime: ToolRuntimeContext | None,
    ) -> None:
        self.tools = tools
        self.observability = observability
        self.evidence: dict[str, dict[str, Any]] = {}
        self.coverage: dict[str, dict[str, Any]] = {}
        reserve = min(0.2, timeout / 10)
        self.deadline = time.monotonic() + timeout - reserve
        context = runtime or ToolRuntimeContext()
        inherited = context.metadata.get("workflow_deadline")
        if isinstance(inherited, (int, float)):
            self.deadline = min(self.deadline, inherited - min(0.03, timeout / 20))
        self.runtime = context.model_copy(
            update={"metadata": {**context.metadata, "workflow_deadline": self.deadline}}
        )

    async def run(self, key: str, name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        step = self.coverage[key] = {"tool": name, "status": "unavailable"}
        tool = self.tools.get(name)
        if tool is None:
            step["error_code"] = "NOT_CONFIGURED"
            return None
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            step.update(status="not_executed", error_code="TIME_BUDGET_EXHAUSTED")
            return None
        try:
            result = await run_observed_tool(
                self.observability,
                tool,
                arguments,
                runtime=self.runtime,
                component_name="seo_workflow",
                timeout=remaining,
            )
        except TimeoutError:
            step.update(status="failed", error_code="TOOL_TIMEOUT")
            return None
        except InternalAdapterError as exc:
            step.update(status="failed", error_code=exc.error_code.value)
            return None
        except ValueError:
            step.update(status="failed", error_code="INVALID_ARGUMENT")
            return None
        except Exception:
            step.update(status="failed", error_code="TOOL_FAILED")
            return None
        if not result.success or not isinstance(result.data, dict):
            step.update(status="failed", error_code="TOOL_FAILED")
            return None
        self.evidence[key] = result.data
        step["status"] = result.data.get("workflow_status", "complete")
        return result.data

    def skip(self, key: str, reason: str) -> None:
        self.coverage[key] = {"status": "skipped", "reason": reason}

    def finish(
        self,
        capability: str,
        title: str,
        items: list[dict[str, Any]],
        limitations: list[str],
        **details: Any,
    ) -> ToolResult:
        partial = any(
            step["status"] not in {"complete", "skipped", "not_implemented"}
            for step in self.coverage.values()
        )
        payload = base_contract(
            capability=capability,
            view_type="cards",
            title_key=f"seo.{capability}.title",
            title_fallback=title,
            description_key=f"seo.{capability}.description",
            description_fallback="部分步骤未完成，已保留可用证据。"
            if partial
            else "已取得本次受检范围的证据。",
            response={},
            limitations=limitations,
        )
        payload.update(
            items=items,
            evidence=self.evidence,
            coverage=self.coverage,
            workflow_status="partial" if partial else "complete",
            **details,
        )
        sources = {}
        for data in self.evidence.values():
            provenance = data.get("provenance") or build_provenance(data.get("raw", {}))
            for source in provenance.get("sources", [provenance]):
                if source.get("source"):
                    sources[source["source"]] = source
        if sources:
            payload["provenance"].update(
                source=next(iter(sources)) if len(sources) == 1 else "multiple",
                source_label="、".join(
                    str(source.get("source_label") or source["source"])
                    for source in sources.values()
                ),
                is_estimated=any(source.get("is_estimated") for source in sources.values()),
                sources=list(sources.values()),
            )
        return ToolResult(
            success=bool(self.evidence),
            data=payload,
            error=None if self.evidence else "本次没有取得可用诊断证据。",
        )
