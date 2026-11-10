from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.exceptions import InternalProviderResponseError
from src.models.common import DataIntegrity, DataSource
from src.providers.pagespeed.client import PageSpeedClient
from src.providers.pagespeed.config import PageSpeedConfig
from src.providers.pagespeed.provider import PageSpeedProvider


def pagespeed_response(*, runtime_error: bool = False) -> dict:
    lighthouse = {
        "requestedUrl": "https://example.com/",
        "finalUrl": "https://example.com/final",
        "lighthouseVersion": "12.8.2",
        "fetchTime": "2026-07-25T10:00:00Z",
        "runWarnings": ["A test warning"],
        "categories": {
            "performance": {"score": 0.62},
            "accessibility": {"score": 0.84},
            "best-practices": {"score": 0.78},
            "seo": {"score": 0.91},
        },
        "audits": {
            "first-contentful-paint": {
                "score": 0.72,
                "numericValue": 1900,
                "displayValue": "1.9 s",
            },
            "largest-contentful-paint": {
                "score": 0.45,
                "numericValue": 3600,
                "displayValue": "3.6 s",
            },
            "cumulative-layout-shift": {
                "score": 0.92,
                "numericValue": 0.08,
                "displayValue": "0.08",
            },
            "speed-index": {
                "score": 0.55,
                "numericValue": 4100,
                "displayValue": "4.1 s",
            },
            "total-blocking-time": {
                "score": 0.36,
                "numericValue": 680,
                "displayValue": "680 ms",
            },
            "unused-javascript": {
                "id": "unused-javascript",
                "title": "Reduce unused JavaScript",
                "description": "Remove unused JavaScript.",
                "score": 0.25,
                "scoreDisplayMode": "metricSavings",
                "displayValue": "Potential savings of 1.8 s",
                "details": {"overallSavingsMs": 1800, "overallSavingsBytes": 420000},
            },
            "render-blocking-resources": {
                "id": "render-blocking-resources",
                "title": "Eliminate render-blocking resources",
                "description": "Defer critical resources.",
                "score": 0.75,
                "scoreDisplayMode": "metricSavings",
                "details": {"items": [{"wastedMs": 420}]},
            },
            "modern-image-formats": {
                "id": "modern-image-formats",
                "title": "Serve images in next-gen formats",
                "description": "Use WebP or AVIF.",
                "score": 0.5,
                "scoreDisplayMode": "metricSavings",
                "details": {"overallSavingsBytes": 655360},
            },
            "uses-long-cache-ttl": {
                "id": "uses-long-cache-ttl",
                "title": "Use efficient cache lifetimes",
                "description": "Set a longer cache lifetime.",
                "score": 0.65,
                "scoreDisplayMode": "binary",
            },
            "manual-audit": {
                "id": "manual-audit",
                "title": "Manual audit",
                "score": None,
                "scoreDisplayMode": "manual",
            },
        },
    }
    if runtime_error:
        lighthouse["runtimeError"] = {"code": "ERRORED_DOCUMENT_REQUEST", "message": "Page failed"}
    return {
        "id": "https://example.com/final",
        "analysisUTCTimestamp": "2026-07-25T10:00:00Z",
        "lighthouseResult": lighthouse,
        "loadingExperience": {"metrics": {"LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2700}}},
        "originLoadingExperience": {"metrics": {}},
    }


@pytest.mark.asyncio
async def test_provider_maps_lighthouse_to_canonical_report_and_ignores_crux() -> None:
    client = PageSpeedClient(PageSpeedConfig(api_key="test-key"))
    client.request = AsyncMock(return_value=pagespeed_response())
    provider = PageSpeedProvider(client)

    report = await provider.get_page_performance(site_id="example.com")

    assert report.source == DataSource.PAGESPEED
    assert report.is_estimated is True
    assert report.integrity == DataIntegrity.COMPLETE
    assert report.strategy == "mobile"
    assert report.requested_categories == [
        "performance",
        "accessibility",
        "best-practices",
        "seo",
    ]
    assert report.category_scores == {
        "performance": 62,
        "accessibility": 84,
        "best-practices": 78,
        "seo": 91,
    }
    assert {item.key for item in report.lab_metrics} == {
        "fcp",
        "lcp",
        "cls",
        "speed_index",
        "tbt",
    }
    assert report.metric("lcp").numeric_value == 3600
    assert [item.audit_id for item in report.opportunities] == [
        "unused-javascript",
        "render-blocking-resources",
        "modern-image-formats",
    ]
    assert [item.audit_id for item in report.diagnostics] == ["uses-long-cache-ttl"]
    assert not hasattr(report, "loading_experience")
    client.request.assert_awaited_once_with(
        url="https://example.com/",
        strategy="mobile",
        categories=["performance", "accessibility", "best-practices", "seo"],
        locale="zh-CN",
    )


@pytest.mark.asyncio
async def test_provider_rejects_other_domains_and_url_credentials() -> None:
    provider = PageSpeedProvider(PageSpeedClient(PageSpeedConfig(api_key="test-key")))

    with pytest.raises(ValueError, match="当前站点"):
        await provider.get_page_performance(
            site_id="example.com",
            url="https://competitor.example/page",
        )
    with pytest.raises(ValueError, match="有效"):
        await provider.get_page_performance(
            site_id="example.com",
            url="https://user:password@example.com/",
        )


@pytest.mark.asyncio
async def test_provider_treats_lighthouse_runtime_error_as_failure() -> None:
    client = PageSpeedClient(PageSpeedConfig(api_key="test-key"))
    client.request = AsyncMock(return_value=pagespeed_response(runtime_error=True))
    provider = PageSpeedProvider(client)

    with pytest.raises(InternalProviderResponseError, match="Lighthouse 检测失败"):
        await provider.get_page_performance(site_id="example.com")


def test_provider_is_unavailable_without_api_key() -> None:
    provider = PageSpeedProvider(PageSpeedClient(PageSpeedConfig(api_key="")))
    assert provider.runtime_mode == "unavailable"
    assert provider.meta.source == DataSource.PAGESPEED
