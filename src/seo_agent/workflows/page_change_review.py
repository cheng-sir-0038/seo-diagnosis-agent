from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.agent.tools.base import AgentTool, ToolProtocol, ToolResult, ToolRuntimeContext
from src.core.cache import CacheManager
from src.core.exceptions import InternalAdapterError
from src.observability.client import ObservabilityClient
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind
from src.providers.page_metadata.client import normalize_site_url, normalized_host
from src.seo_agent.workflows.execution import WorkflowSteps

FIELDS = ("title", "meta_description", "h1s", "canonical_url")


class PageExpectedValues(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    title: str | None = Field(default=None, max_length=2000)
    meta_description: str | None = Field(default=None, max_length=10000)
    h1s: list[str] | None = Field(default=None, max_length=20)
    canonical_url: str | None = Field(default=None, max_length=2048)


class PageChangeReviewArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["capture_baseline", "compare"] = "compare"
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    baseline_snapshot_id: str | None = Field(default=None, min_length=1, max_length=200)
    expected: PageExpectedValues | None = None

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "compare" and not self.baseline_snapshot_id:
            raise ValueError("页面复检必须提供 baseline_snapshot_id；先显式建立基线")
        if self.action == "capture_baseline" and (
            self.baseline_snapshot_id is not None or self.expected is not None
        ):
            raise ValueError("建立基线时不填写旧快照或验收值")
        return self


class PageChangeReviewWorkflow(AgentTool):
    name = "seo_page_change_review"
    description = (
        "页面变更复检：capture_baseline 显式读取并冻结当前页面基线；compare 使用本站基线，"
        "重新读取同一页面，对比 Title、Description、H1、Canonical 和 expected 验收值。"
        "无验收值或证据不足不判定通过；只读网页，不修改、审批或发布。"
    )
    parameters_schema = PageChangeReviewArguments.model_json_schema()
    render_hint = "cards"

    def __init__(
        self,
        page_metadata: ToolProtocol,
        site: dict[str, Any],
        snapshots: CacheManager,
        timeout: float,
        *,
        observability: ObservabilityClient | None = None,
    ) -> None:
        self.page_metadata, self.site, self.snapshots = page_metadata, site, snapshots
        self.timeout, self.observability = timeout, observability

    async def execute(
        self,
        arguments: dict[str, Any],
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        options = PageChangeReviewArguments.model_validate(arguments)
        steps = WorkflowSteps(
            {"seo_page_metadata": self.page_metadata}, self.timeout, self.observability, runtime
        )
        baseline = None
        if options.action == "compare":
            async with observed_operation(
                self.observability,
                "load_page_review_baseline",
                kind=SpanKind.INTERNAL,
                input_data={"baseline_snapshot_id": options.baseline_snapshot_id},
            ) as load:
                baseline = await self.snapshots.get_snapshot(options.baseline_snapshot_id)
                if baseline is None:
                    raise ValueError("页面基线快照不存在")
                if (
                    not isinstance(baseline, dict)
                    or not isinstance(baseline.get("meta"), dict)
                    or baseline["meta"].get("business_site_id") != self.site["id"]
                ):
                    raise ValueError("页面基线不属于当前站点")
                if not self._valid_page(baseline):
                    raise ValueError("基线不是有效的页面 HTML 证据，或缺少必要字段")
                url = normalize_site_url(baseline["data"]["requested_url"])
                if options.url and normalize_site_url(options.url) != url:
                    raise ValueError("页面复检必须读取基线对应的同一 URL")
                steps.evidence["baseline"] = {
                    "raw": baseline,
                    "snapshot_id": options.baseline_snapshot_id,
                }
                steps.coverage["baseline"] = {"status": "complete"}
                if load is not None:
                    load.output_data = {
                        "snapshot_id": options.baseline_snapshot_id,
                        "url": url,
                        "fields": {key: baseline["data"][key] for key in FIELDS},
                    }
        else:
            url = normalize_site_url(options.url or self.site["domain"])
        if normalized_host(url) != normalized_host(self.site["domain"]):
            raise ValueError("页面复检只允许当前站点的页面")
        current = await steps.run(
            "current_page", "seo_page_metadata", {"url": url, "force_refresh": True}
        )
        raw = (current or {}).get("raw", {})
        snapshot_id = None
        valid = self._valid_page(raw) and normalize_site_url(raw["data"]["requested_url"]) == url
        if valid:
            async with observed_operation(
                self.observability,
                "freeze_page_review_evidence",
                kind=SpanKind.INTERNAL,
                input_data={"action": options.action, "url": url},
            ) as freeze:
                snapshot_id = await self.snapshots.save_snapshot(
                    task_id=((runtime.task_id or runtime.run_id) if runtime else None)
                    or f"page-review:{self.site['id']}",
                    capability="page_metadata",
                    provider="page_html",
                    data={
                        **raw,
                        "meta": {
                            **raw["meta"],
                            "business_site_id": self.site["id"],
                            "captured_at": datetime.now(UTC).isoformat(),
                        },
                    },
                )
                current["snapshot_id"] = snapshot_id
                if freeze is not None:
                    freeze.output_data = {"snapshot_id": snapshot_id, "url": url}
        elif current is not None:
            steps.coverage["current_page"].update(
                status="partial", error_code="INVALID_PAGE_EVIDENCE"
            )
        differences, checks = [], []
        status = "insufficient_evidence"
        if valid and options.action == "capture_baseline":
            status = "baseline_recorded"
        elif valid:
            async with observed_operation(
                self.observability,
                "compare_page_acceptance",
                kind=SpanKind.INTERNAL,
                input_data={
                    "baseline_snapshot_id": options.baseline_snapshot_id,
                    "current_snapshot_id": snapshot_id,
                    "expected": options.expected.model_dump(exclude_unset=True)
                    if options.expected
                    else {},
                },
            ) as comparison:
                differences = [
                    {
                        "field": key,
                        "before": baseline["data"][key],
                        "after": raw["data"][key],
                        "changed": baseline["data"][key] != raw["data"][key],
                    }
                    for key in FIELDS
                ]
                expected = (
                    options.expected.model_dump(exclude_unset=True) if options.expected else {}
                )
                checks = [
                    {
                        "field": key,
                        "expected": value,
                        "actual": raw["data"][key],
                        "status": "passed" if value == raw["data"][key] else "failed",
                    }
                    for key, value in expected.items()
                ]
                if checks:
                    status = (
                        "passed" if all(row["status"] == "passed" for row in checks) else "failed"
                    )
                if comparison is not None:
                    comparison.output_data = {
                        "review_status": status,
                        "differences": differences,
                        "checks": checks,
                    }
        limitations = [
            "仅核对本次同一请求 URL 的源 HTML，不执行 JavaScript，不证明收录或排名提升。",
            "建立基线不等于完成复检；只有提供的验收值与完整页面证据一致时才判定通过。",
            "仅保存本地证据快照，不修改网页、审批、派发任务或发布。",
        ]
        if options.action == "compare" and not checks:
            limitations.append("缺少有效页面证据或目标验收值，本次不能判定通过。")
        return steps.finish(
            "page_change_review",
            "页面变更复检",
            [
                {
                    "key": row["field"],
                    "title": row["field"],
                    "summary": row["status"],
                    "metadata": [
                        {"label": "期望", "value": str(row["expected"])},
                        {"label": "实际", "value": str(row["actual"])},
                    ],
                }
                for row in checks
            ]
            or [
                {
                    "key": "review",
                    "title": status,
                    "summary": url,
                    "metadata": [{"label": "快照 ID", "value": snapshot_id or "未取得"}],
                }
            ],
            limitations,
            action=options.action,
            review_status=status,
            url=url,
            baseline_snapshot_id=snapshot_id
            if options.action == "capture_baseline"
            else options.baseline_snapshot_id,
            current_snapshot_id=snapshot_id if options.action == "compare" else None,
            differences=differences,
            checks=checks,
        )

    def _valid_page(self, raw: Any) -> bool:
        if not isinstance(raw, dict):
            return False
        meta, data = raw.get("meta", {}), raw.get("data")
        if (
            not isinstance(meta, dict)
            or not isinstance(data, dict)
            or meta.get("capability") != "page_metadata"
            or meta.get("source") != "page_html"
            or meta.get("cached") is True
            or type(data.get("status_code")) is not int
            or not 200 <= data["status_code"] < 300
            or not set(FIELDS) <= data.keys()
            or not isinstance(data.get("h1s"), list)
            or not all(isinstance(item, str) for item in data["h1s"])
            or any(
                data[key] is not None and not isinstance(data[key], str)
                for key in FIELDS
                if key != "h1s"
            )
        ):
            return False
        for key in ("requested_url", "final_url"):
            if not isinstance(data.get(key), str):
                return False
            try:
                url = normalize_site_url(data[key])
            except (InternalAdapterError, ValueError):
                return False
            if normalized_host(url) != normalized_host(self.site["domain"]):
                return False
        return True
