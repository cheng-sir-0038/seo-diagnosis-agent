"""PageSpeed 单页面性能报告展示适配器。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.models.common import Capability
from src.seo_presentation.contracts import base_contract

_SCORE_LABELS = {
    "performance": "性能",
    "accessibility": "无障碍",
    "best-practices": "最佳实践",
    "seo": "SEO",
}
_METRIC_LABELS = {
    "fcp": "首次内容绘制",
    "lcp": "最大内容绘制",
    "cls": "布局偏移",
    "speed_index": "速度指数",
    "tbt": "总阻塞时间",
}


class PagePerformancePresenter:
    capability = Capability.PAGE_PERFORMANCE

    def present(self, response: Mapping[str, Any]) -> dict[str, Any] | None:
        data = response.get("data")
        if not isinstance(data, Mapping):
            return None
        category_scores = data.get("category_scores")
        if not isinstance(category_scores, Mapping):
            return None

        result = base_contract(
            capability=self.capability.value,
            view_type="chart",
            title_key="seo.page_performance.title",
            title_fallback="页面速度诊断",
            description_key="seo.page_performance.description",
            description_fallback="查看当前页面的 Lighthouse 实验室检测结果和优先优化项。",
            response=response,
            limitations=[
                "这是一次 Lighthouse 实验室模拟检测，不等同于真实用户长期体验。",
                "真实用户 Core Web Vitals 需要后续结合独立 CrUX 数据判断。",
                "移动端和桌面端是两次独立检测，不能视为同一次运行的两个视图。",
            ],
        )
        result["provenance"].update(
            {
                "strategy": _text(data.get("strategy")),
                "requested_url": _text(data.get("requested_url")),
                "final_url": _text(data.get("final_url")),
                "lighthouse_version": _text(data.get("lighthouse_version")),
                "fetch_time": _text(data.get("fetch_time")),
                "categories": [str(item) for item in data.get("requested_categories") or []],
            }
        )
        result.update(
            {
                "variant": "page_performance",
                "scores": [
                    {
                        "key": str(key),
                        "label": _SCORE_LABELS.get(str(key), str(key)),
                        "value": int(value) if isinstance(value, (int, float)) else None,
                        "status": _score_status(value),
                    }
                    for key, value in category_scores.items()
                ],
                "metrics": _metrics(data.get("lab_metrics")),
                "opportunities": _issues(data.get("opportunities")),
                "diagnostics": _issues(data.get("diagnostics")),
                "warnings": [str(item) for item in data.get("warnings") or []],
            }
        )
        return result


def _metrics(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    items = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        key = str(raw.get("key") or "")
        numeric = _number(raw.get("numeric_value"))
        unit = str(raw.get("unit") or "")
        items.append(
            {
                "key": key,
                "label": _METRIC_LABELS.get(key, key.upper()),
                "value": numeric,
                "display_value": _metric_display(key, numeric, unit, raw.get("display_value")),
                "status": _metric_status(key, numeric),
                "score": raw.get("score"),
            }
        )
    return items


def _issues(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    items = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        priority = str(raw.get("priority") or "low")
        items.append(
            {
                "audit_id": str(raw.get("audit_id") or ""),
                "title": str(raw.get("title") or "待优化项目"),
                "description": _clean_description(raw.get("description")),
                "priority": priority,
                "priority_label": {
                    "high": "优先处理",
                    "medium": "建议处理",
                    "low": "可继续优化",
                }.get(priority, priority),
                "score": raw.get("score"),
                "display_value": _text(raw.get("display_value")),
                "savings_label": _savings_label(
                    raw.get("estimated_savings_ms"), raw.get("estimated_savings_bytes")
                ),
            }
        )
    return items


def _score_status(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "unknown"
    if number >= 90:
        return "good"
    if number >= 50:
        return "needs_improvement"
    return "poor"


def _metric_status(key: str, value: float | None) -> str:
    if value is None:
        return "unknown"
    thresholds = {
        "fcp": (1800, 3000),
        "lcp": (2500, 4000),
        "cls": (0.1, 0.25),
        "speed_index": (3400, 5800),
        "tbt": (200, 600),
    }
    good, poor = thresholds.get(key, (float("inf"), float("inf")))
    if value <= good:
        return "good"
    if value <= poor:
        return "needs_improvement"
    return "poor"


def _metric_display(key: str, value: float | None, unit: str, fallback: Any) -> str:
    if value is None:
        return _text(fallback) or "暂无数据"
    if unit == "ms":
        return f"{value / 1000:.1f} 秒" if value >= 1000 else f"{int(round(value))} 毫秒"
    if key == "cls":
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return _text(fallback) or f"{value:g}"


def _savings_label(milliseconds: Any, byte_count: Any) -> str | None:
    parts: list[str] = []
    ms = _number(milliseconds)
    size = _number(byte_count)
    if ms is not None and ms > 0:
        parts.append(f"{ms / 1000:.1f} 秒" if ms >= 1000 else f"{int(round(ms))} 毫秒")
    if size is not None and size > 0:
        parts.append(
            f"{size / 1024 / 1024:.1f} MB"
            if size >= 1024 * 1024
            else f"{int(round(size / 1024))} KB"
        )
    return f"预计节省 {' · '.join(parts)}" if parts else None


def _clean_description(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    return text.split("[Learn more", 1)[0].strip()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _text(value: Any) -> str | None:
    return None if value is None else str(value)
