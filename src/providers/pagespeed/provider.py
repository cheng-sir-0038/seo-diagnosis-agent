"""Google PageSpeed Insights API v5 数据适配器。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from src.core.exceptions import InternalProviderResponseError
from src.models.common import Capability, DataIntegrity, DataSource
from src.models.performance import (
    PagePerformanceIssue,
    PagePerformanceMetric,
    PagePerformanceReport,
)
from src.providers.base import BaseProvider, ProviderMeta, ProviderMode
from src.providers.pagespeed.client import PageSpeedClient
from src.providers.pagespeed.config import PageSpeedConfig

_DEFAULT_CATEGORIES = ["performance", "accessibility", "best-practices", "seo"]
_CATEGORY_KEYS = set(_DEFAULT_CATEGORIES)
_METRIC_AUDITS = {
    "fcp": ("first-contentful-paint", "ms"),
    "lcp": ("largest-contentful-paint", "ms"),
    "cls": ("cumulative-layout-shift", "unitless"),
    "speed_index": ("speed-index", "ms"),
    "tbt": ("total-blocking-time", "ms"),
}
_EXCLUDED_AUDITS = {audit_id for audit_id, _unit in _METRIC_AUDITS.values()}
_EXCLUDED_DISPLAY_MODES = {"manual", "notApplicable", "informative"}


class PageSpeedProvider(BaseProvider):
    """PageSpeed/Lighthouse 单页面实验室检测 Provider。"""

    def __init__(self, client: PageSpeedClient | None = None) -> None:
        super().__init__()
        self.client = client or PageSpeedClient()

    @property
    def meta(self) -> ProviderMeta:
        return ProviderMeta(
            name="pagespeed",
            label="Google PageSpeed Insights API v5",
            source=DataSource.PAGESPEED,
            supported_capabilities={Capability.PAGE_PERFORMANCE},
        )

    @property
    def runtime_mode(self) -> ProviderMode:
        return "live" if self.client.config.api_key else "unavailable"

    async def startup(self) -> None:
        await self.client.startup()

    async def shutdown(self) -> None:
        await self.client.shutdown()

    async def health_check(self) -> dict[str, Any]:
        configured = bool(self.client.config.api_key)
        return {
            "status": "ok" if configured else "unavailable",
            "mode": self.runtime_mode,
            "api_key_configured": configured,
            "session_ready": self.client._session is not None,
        }

    async def get_page_performance(
        self,
        site_id: str,
        url: str | None = None,
        strategy: str = "mobile",
        categories: list[str] | None = None,
        locale: str = "zh-CN",
        max_opportunities: int | None = None,
        **kwargs: Any,
    ) -> PagePerformanceReport:
        del kwargs
        config = self.client.config if hasattr(self.client, "config") else PageSpeedConfig()
        target = _normalize_site_url(site_id, url)
        selected_strategy = strategy.lower()
        if selected_strategy not in {"mobile", "desktop"}:
            raise ValueError("PageSpeed strategy 仅支持 mobile 或 desktop")
        selected_categories = _normalize_categories(categories or _DEFAULT_CATEGORIES)
        limit = max(1, min(int(max_opportunities or config.max_opportunities), 25))

        raw = await self.client.request(
            url=target,
            strategy=selected_strategy,
            categories=selected_categories,
            locale=locale or config.default_locale,
        )
        lighthouse = _mapping(raw.get("lighthouseResult"))
        runtime_error = _mapping(lighthouse.get("runtimeError"))
        if runtime_error:
            raise InternalProviderResponseError(
                "PageSpeed Lighthouse 检测失败",
                provider="pagespeed",
                debug_context={
                    "runtime_error_code": _text(runtime_error.get("code")),
                    "runtime_error_message": _text(runtime_error.get("message")),
                },
            )

        category_payload = _mapping(lighthouse.get("categories"))
        audits = _mapping(lighthouse.get("audits"))
        category_scores = {
            category: _score_100(_mapping(category_payload.get(category)).get("score"))
            for category in selected_categories
        }
        metrics = [
            _map_metric(key, audit_id, unit, audits)
            for key, (audit_id, unit) in _METRIC_AUDITS.items()
        ]
        opportunities, diagnostics = _map_issues(audits)
        fetch_time = _parse_datetime(lighthouse.get("fetchTime") or raw.get("analysisUTCTimestamp"))
        missing_scores = any(value is None for value in category_scores.values())
        missing_metrics = any(metric.numeric_value is None for metric in metrics)

        return PagePerformanceReport(
            source=DataSource.PAGESPEED,
            fetched_at=fetch_time or datetime.now(UTC),
            data_date=(fetch_time or datetime.now(UTC)).date(),
            is_estimated=True,
            integrity=(
                DataIntegrity.PARTIAL
                if missing_scores or missing_metrics
                else DataIntegrity.COMPLETE
            ),
            requested_url=str(lighthouse.get("requestedUrl") or target),
            final_url=str(lighthouse.get("finalUrl") or raw.get("id") or target),
            strategy=selected_strategy,
            requested_categories=selected_categories,
            lighthouse_version=_text(lighthouse.get("lighthouseVersion")),
            fetch_time=fetch_time,
            category_scores=category_scores,
            lab_metrics=metrics,
            opportunities=opportunities[:limit],
            diagnostics=diagnostics[:limit],
            warnings=[str(item) for item in lighthouse.get("runWarnings") or []],
        )


def _normalize_site_url(site_id: str, value: str | None) -> str:
    site_host = _hostname(site_id)
    if not site_host:
        raise ValueError("当前站点域名无效")
    target = (value or f"https://{site_host}/").strip()
    if "://" not in target:
        target = f"https://{target}"
    parsed = urlparse(target)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("PageSpeed url 必须是当前站点内有效的 http 或 https URL")
    if _hostname(target) != site_host:
        raise ValueError("PageSpeed 只允许检测当前站点域名下的页面")
    return parsed._replace(path=parsed.path or "/", fragment="").geturl()


def _hostname(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    return (urlparse(text).hostname or "").lower().removeprefix("www.")


def _normalize_categories(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        category = str(value).strip().lower().replace("_", "-")
        if category not in _CATEGORY_KEYS:
            raise ValueError(f"不支持的 PageSpeed category: {value}")
        if category not in result:
            result.append(category)
    return result or list(_DEFAULT_CATEGORIES)


def _map_metric(
    key: str,
    audit_id: str,
    unit: str,
    audits: Mapping[str, Any],
) -> PagePerformanceMetric:
    audit = _mapping(audits.get(audit_id))
    return PagePerformanceMetric(
        key=key,
        numeric_value=_number(audit.get("numericValue")),
        unit=unit,
        display_value=_text(audit.get("displayValue")),
        score=_score_100(audit.get("score")),
    )


def _map_issues(
    audits: Mapping[str, Any],
) -> tuple[list[PagePerformanceIssue], list[PagePerformanceIssue]]:
    opportunities: list[PagePerformanceIssue] = []
    diagnostics: list[PagePerformanceIssue] = []
    for audit_id, raw in audits.items():
        audit = _mapping(raw)
        if audit_id in _EXCLUDED_AUDITS:
            continue
        if _text(audit.get("scoreDisplayMode")) in _EXCLUDED_DISPLAY_MODES:
            continue
        score = _score_100(audit.get("score"))
        details = _mapping(audit.get("details"))
        savings_ms = _savings(details, "overallSavingsMs", "wastedMs")
        savings_bytes = _savings(details, "overallSavingsBytes", "wastedBytes")
        if savings_ms is None and savings_bytes is None and (score is None or score >= 90):
            continue
        issue = PagePerformanceIssue(
            audit_id=str(audit_id),
            title=str(audit.get("title") or audit_id),
            description=_text(audit.get("description")),
            score=score,
            display_value=_text(audit.get("displayValue")),
            estimated_savings_ms=savings_ms,
            estimated_savings_bytes=savings_bytes,
            priority=_priority(savings_ms, savings_bytes, score),
        )
        target = (
            opportunities if savings_ms is not None or savings_bytes is not None else diagnostics
        )
        target.append(issue)

    def ordering(item: PagePerformanceIssue) -> tuple[int, int, int, str]:
        return (
            -(item.estimated_savings_ms or 0),
            -(item.estimated_savings_bytes or 0),
            item.score if item.score is not None else 101,
            item.audit_id,
        )

    return sorted(opportunities, key=ordering), sorted(diagnostics, key=ordering)


def _savings(details: Mapping[str, Any], overall_key: str, item_key: str) -> int | None:
    direct = _number(details.get(overall_key))
    if direct is not None and direct > 0:
        return int(round(direct))
    total = 0.0
    found = False
    for item in details.get("items") or []:
        if isinstance(item, Mapping):
            value = _number(item.get(item_key))
            if value is not None and value > 0:
                total += value
                found = True
    return int(round(total)) if found else None


def _priority(savings_ms: int | None, savings_bytes: int | None, score: int | None) -> str:
    if (
        (savings_ms or 0) >= 1000
        or (savings_bytes or 0) >= 500_000
        or (score is not None and score < 50)
    ):
        return "high"
    if (
        (savings_ms or 0) >= 250
        or (savings_bytes or 0) >= 100_000
        or (score is not None and score < 80)
    ):
        return "medium"
    return "low"


def _score_100(value: Any) -> int | None:
    number = _number(value)
    if number is None:
        return None
    return max(0, min(100, int(round(number * 100 if number <= 1 else number))))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
