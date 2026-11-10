from __future__ import annotations

from src.models.common import Capability
from src.seo_presentation.registry import present_capability


def test_page_performance_presenter_builds_chart_contract() -> None:
    result = present_capability(
        Capability.PAGE_PERFORMANCE,
        {
            "meta": {"capability": "page_performance", "source": "pagespeed"},
            "data": {
                "source": "pagespeed",
                "fetched_at": "2026-07-25T10:00:00Z",
                "is_estimated": True,
                "integrity": "complete",
                "requested_url": "https://example.com/",
                "final_url": "https://example.com/",
                "strategy": "mobile",
                "requested_categories": [
                    "performance",
                    "accessibility",
                    "best-practices",
                    "seo",
                ],
                "lighthouse_version": "12.8.2",
                "fetch_time": "2026-07-25T10:00:00Z",
                "category_scores": {
                    "performance": 62,
                    "accessibility": 84,
                    "best-practices": 78,
                    "seo": 91,
                },
                "lab_metrics": [
                    {
                        "key": "lcp",
                        "numeric_value": 3600,
                        "unit": "ms",
                        "display_value": "3.6 s",
                        "score": 45,
                    },
                    {
                        "key": "cls",
                        "numeric_value": 0.08,
                        "unit": "unitless",
                        "display_value": "0.08",
                        "score": 92,
                    },
                ],
                "opportunities": [
                    {
                        "audit_id": "unused-javascript",
                        "title": "减少未使用的 JavaScript",
                        "description": "移除未使用脚本。",
                        "score": 25,
                        "estimated_savings_ms": 1800,
                        "estimated_savings_bytes": 420000,
                        "priority": "high",
                    }
                ],
                "diagnostics": [],
                "warnings": [],
            },
        },
    )

    assert result is not None
    assert result["schema_version"] == "1.0"
    assert result["view_type"] == "chart"
    assert result["variant"] == "page_performance"
    assert result["provenance"]["source_label"] == "Google PageSpeed Insights"
    assert result["provenance"]["strategy"] == "mobile"
    assert [item["label"] for item in result["scores"]] == [
        "性能",
        "无障碍",
        "最佳实践",
        "SEO",
    ]
    assert result["scores"][0]["status"] == "needs_improvement"
    assert result["scores"][3]["status"] == "good"
    assert result["metrics"][0]["display_value"] == "3.6 秒"
    assert result["opportunities"][0]["savings_label"] == "预计节省 1.8 秒 · 410 KB"
    assert any("CrUX" in item for item in result["limitations"])
