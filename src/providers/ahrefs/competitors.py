"""
T5b — 竞争分析（Organic Competitors / Batch Analysis）

实现 Capability:
  - ORGANIC_COMPETITORS: 自然搜索竞争对手 → list[OrganicCompetitor]
  - BATCH_ANALYSIS: 批量域名对比 → list[BatchAnalysisResult]

数据来源: Ahrefs Site Explorer + Batch Analysis API

产品规则（对齐 PRD）:
  - 搜索竞争对手与业务竞争对手分开标记（is_user_tracked）
  - 竞争对手高 DR/流量不自动构成追赶理由
  - 用户批准后才加入长期监控
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from src.models.competitor import OrganicCompetitor, BatchAnalysisResult
from src.models.common import DataSource


async def get_organic_competitors(
    client: Any,
    site_id: str,
    country: str = "us",
    **kwargs: Any,
) -> list[OrganicCompetitor]:
    """获取自然搜索竞争对手 → list[OrganicCompetitor]。

    真实实现::

        domain = resolve_domain(site_id)
        raw = await client.request("site_explorer_organic_competitors", params={
            "target": domain, "country": country,
        })
        return [
            OrganicCompetitor(
                domain=c["domain"],
                is_user_tracked=False,  # 默认自动发现
                common_keywords_count=c.get("common_keywords", 0),
                competitor_unique_keywords=c.get("competitor_unique", 0),
                our_unique_keywords=c.get("our_unique", 0),
                overlap_percentage=c.get("overlap"),
                domain_rating=c.get("domain_rating"),
                organic_traffic_estimate=c.get("organic_traffic"),
                ranked_pages_count=c.get("ranked_pages"),
                source=DataSource.AHREFS,
                is_estimated=True,
            )
            for c in raw.get("competitors", [])
        ]
    """
    logger.info(f"[T5b-Competitors] get_organic_competitors | site={site_id}")

    raw = await client.request("site_explorer_organic_competitors", params={
        "target": site_id, "country": country,
    })
    now = datetime.now(timezone.utc)
    return [
        OrganicCompetitor(
            domain=c["domain"],
            is_user_tracked=False,
            common_keywords_count=c.get("common_keywords_count", 0),
            competitor_unique_keywords=c.get("competitor_unique_keywords", 0),
            our_unique_keywords=c.get("our_unique_keywords", 0),
            overlap_percentage=c.get("overlap_percentage"),
            domain_rating=c.get("domain_rating"),
            organic_traffic_estimate=c.get("organic_traffic_estimate"),
            ranked_pages_count=c.get("ranked_pages_count"),
            source=DataSource.AHREFS,
            fetched_at=now,
            is_estimated=True,
        )
        for c in raw.get("competitors", [])
    ]


async def get_batch_analysis(
    client: Any,
    targets: list[str],
    **kwargs: Any,
) -> list[BatchAnalysisResult]:
    """批量域名分析 → list[BatchAnalysisResult]。

    真实实现::

        raw = await client.request("batch_analysis", body={
            "targets": targets,
        })
        return [
            BatchAnalysisResult(
                target_domain=r["target"],
                domain_rating=r.get("domain_rating"),
                organic_traffic_estimate=r.get("organic_traffic", 0),
                organic_keywords_count=r.get("organic_keywords", 0),
                backlinks_count=r.get("backlinks", 0),
                refdomains_count=r.get("refdomains", 0),
                source=DataSource.AHREFS,
                is_estimated=True,
            )
            for r in raw.get("results", [])
        ]
    """
    logger.info(f"[T5b-Competitors] get_batch_analysis | targets={len(targets)}")

    raw = await client.request("batch_analysis", body={"targets": targets})
    now = datetime.now(timezone.utc)
    return [
        BatchAnalysisResult(
            target_domain=r["target_domain"],
            domain_rating=r.get("domain_rating"),
            ahrefs_rank=r.get("ahrefs_rank"),
            organic_traffic_estimate=r.get("organic_traffic_estimate", 0),
            organic_keywords_count=r.get("organic_keywords_count", 0),
            backlinks_count=r.get("backlinks_count", 0),
            refdomains_count=r.get("refdomains_count", 0),
            source=DataSource.AHREFS,
            fetched_at=now,
            is_estimated=True,
        )
        for r in raw.get("results", [])
    ]
