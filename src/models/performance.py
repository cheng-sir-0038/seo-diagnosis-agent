"""Google PageSpeed Insights / Lighthouse 统一性能模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.models.common import BaseEvidence

PageStrategy = Literal["mobile", "desktop"]
IssuePriority = Literal["high", "medium", "low"]
MetricUnit = Literal["ms", "unitless"]


class PagePerformanceMetric(BaseModel):
    """单个 Lighthouse 实验室指标。"""

    key: Literal["fcp", "lcp", "cls", "speed_index", "tbt"]
    numeric_value: float | None = None
    unit: MetricUnit
    display_value: str | None = None
    score: int | None = Field(default=None, ge=0, le=100)


class PagePerformanceIssue(BaseModel):
    """可操作的 Lighthouse 优化机会或诊断项。"""

    audit_id: str
    title: str
    description: str | None = None
    score: int | None = Field(default=None, ge=0, le=100)
    display_value: str | None = None
    estimated_savings_ms: int | None = Field(default=None, ge=0)
    estimated_savings_bytes: int | None = Field(default=None, ge=0)
    priority: IssuePriority


class PagePerformanceReport(BaseEvidence):
    """单个 URL、单个设备策略的一次 Lighthouse 实验室报告。"""

    requested_url: str
    final_url: str
    strategy: PageStrategy
    requested_categories: list[str] = Field(default_factory=list)
    lighthouse_version: str | None = None
    fetch_time: datetime | None = None
    category_scores: dict[str, int | None] = Field(default_factory=dict)
    lab_metrics: list[PagePerformanceMetric] = Field(default_factory=list)
    opportunities: list[PagePerformanceIssue] = Field(default_factory=list)
    diagnostics: list[PagePerformanceIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def metric(self, key: str) -> PagePerformanceMetric:
        for item in self.lab_metrics:
            if item.key == key:
                return item
        raise KeyError(key)
