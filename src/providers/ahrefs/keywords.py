"""
T4a — Keywords Explorer + SERP Overview

实现 Capability:
  - KEYWORD_OVERVIEW: 关键词概览 → list[KeywordOverview]
  - SERP_OVERVIEW: SERP 结果 → list[SerpResult]

数据链路::

    get_keyword_overview(keywords, country)
      ├── 逐个/批量调用 client.request("keywords_explorer_overview", ...)
      ├── 映射 → list[KeywordOverview]
      └── 返回

    get_serp_overview(keyword, country)
      ├── client.request("serp_overview", params={keyword, country})
      ├── 解析 Top 100 结果 → list[SerpResult]
      └── 返回
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from src.models.keyword import KeywordOverview, SerpResult
from src.models.common import DataSource, Country


async def get_keyword_overview(
    client: Any,
    keywords: list[str],
    country: str = "us",
    **kwargs: Any,
) -> list[KeywordOverview]:
    """获取关键词概览（支持批量） → list[KeywordOverview]。

    真实实现::

        results = []
        for kw in keywords:
            raw = await client.request("keywords_explorer_overview", params={
                "keyword": kw, "country": country,
            })
            results.append(KeywordOverview(
                keyword=kw,
                search_volume=raw.get("volume", 0),
                keyword_difficulty=raw.get("difficulty", 0),
                traffic_potential=raw.get("traffic_potential"),
                parent_topic=raw.get("parent_topic"),
                intent=raw.get("intent"),
                source=DataSource.AHREFS,
                is_estimated=True,
            ))
        return results
    """
    logger.info(f"[T4a-Keywords] get_keyword_overview | keywords={len(keywords)} country={country}")

    now = datetime.now(timezone.utc)
    results: list[KeywordOverview] = []
    for kw in keywords[:20]:  # 限制批量大小
        raw = await client.request("keywords_explorer_overview", params={
            "keyword": kw, "country": country,
        })
        results.append(KeywordOverview(
            keyword=kw,
            country=Country(code=country),
            search_volume=raw.get("search_volume", 0),
            keyword_difficulty=raw.get("keyword_difficulty", 0),
            parent_topic=raw.get("parent_topic"),
            intent=raw.get("intent"),
            source=DataSource.AHREFS,
            fetched_at=now,
            is_estimated=True,
        ))
    return results


async def get_serp_overview(
    client: Any,
    keyword: str,
    country: str = "us",
    **kwargs: Any,
) -> list[SerpResult]:
    """获取 SERP Top 100 结果 → list[SerpResult]。

    真实实现::

        raw = await client.request("serp_overview", params={
            "keyword": keyword, "country": country,
        })
        return [
            SerpResult(
                rank=r["position"],
                type=r.get("type", "organic"),
                title=r.get("title"),
                url=r["url"],
                domain_rating=r.get("domain_rating"),
                ...
            )
            for r in raw.get("results", [])
        ]
    """
    logger.info(f"[T4a-Keywords] get_serp_overview | keyword={keyword} country={country}")

    raw = await client.request("serp_overview", params={
        "keyword": keyword, "country": country,
    })
    now = datetime.now(timezone.utc)
    return [
        SerpResult(
            rank=r.get("rank", i + 1),
            type=r.get("type", "organic"),
            title=r.get("title"),
            url=r["url"],
            description=r.get("description"),
            domain_rating=r.get("domain_rating"),
            url_rating=r.get("url_rating"),
            organic_traffic_estimate=r.get("organic_traffic_estimate"),
            source=DataSource.AHREFS,
            fetched_at=now,
            is_estimated=True,
        )
        for i, r in enumerate(raw.get("results", []))
    ]
