"""
T2 — Site Explorer（搜索表现）

实现 Capability:
  - SEARCH_METRICS: 站点搜索表现总览 → SearchMetrics
  - SEARCH_HISTORY: 历史趋势 → list[SearchHistoryPoint]
  - TOP_PAGES: Top Pages → list[TopPage]
  - ORGANIC_KEYWORDS: 自然关键词 → list[OrganicKeyword]

数据链路::

    get_search_metrics(site_id, country)
      ├── client.request("site_explorer_metrics", params={target, country, ...})
      ├── 映射 → SearchMetrics
      └── 返回

    get_top_pages(site_id, country, limit=10)
      ├── client.request("site_explorer_top_pages", params={...})
      ├── 遍历 pages[] → list[TopPage]
      └── 按 organic_traffic_estimate 降序排列

    get_organic_keywords(site_id, country, limit=100)
      ├── client.request("site_explorer_organic_keywords", params={...})
      ├── 遍历 keywords[] → list[OrganicKeyword]
      └── 返回

    get_search_history(site_id, country)
      ├── client.request("site_explorer_metrics_history", params={...})
      ├── 遍历 history[] → list[SearchHistoryPoint]
      └── 返回
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from loguru import logger

from src.models.common import Country, DataSource
from src.models.search import OrganicKeyword, SearchHistoryPoint, SearchMetrics, TopPage


async def get_search_metrics(
    client: Any,
    site_id: str,
    country: str = "us",
    **kwargs: Any,
) -> SearchMetrics:
    """获取站点搜索表现总览 → SearchMetrics。

    真实实现::

        domain = resolve_domain(site_id)
        raw = await client.request("site_explorer_metrics", params={
            "target": domain, "country": country, "protocol": "https",
        })
        return SearchMetrics(
            domain_rating=raw["domain_rating"],
            organic_traffic_estimate=raw.get("organic_traffic", 0),
            ...,
            source=DataSource.AHREFS,
            is_estimated=True,  # ← Ahrefs 自然流量为估算
        )
    """
    logger.info(f"[T2-Search] get_search_metrics | site={site_id} country={country}")

    # PSEUDO: 接入真实 Key 后调用 client.request("site_explorer_metrics", ...)
    # 当前返回 client._pseudo_response 中的数据
    raw = await client.request("site_explorer_metrics", params={
        "target": site_id, "country": country,
    })
    now = datetime.now(UTC)
    return SearchMetrics(
        domain_rating=raw.get("domain_rating", 0),
        ahrefs_rank=raw.get("ahrefs_rank"),
        organic_traffic_estimate=raw.get("organic_traffic_estimate", 0),
        traffic_value_estimate=raw.get("traffic_value_estimate"),
        organic_keywords_count=raw.get("organic_keywords_count", 0),
        top_3_count=raw.get("top_3_count", 0),
        top_10_count=raw.get("top_10_count", 0),
        ranked_pages_count=raw.get("ranked_pages_count", 0),
        country=Country(code=country),
        protocol="https",
        source=DataSource.AHREFS,
        fetched_at=now,
        is_estimated=True,
    )


async def get_top_pages(
    client: Any,
    site_id: str,
    country: str = "us",
    limit: int = 10,
    **kwargs: Any,
) -> list[TopPage]:
    """获取 Top Pages → list[TopPage]。

    真实实现::

        raw = await client.request("site_explorer_top_pages", params={
            "target": domain, "country": country, "limit": limit,
        })
        return [
            TopPage(
                url=p["url"], title=p.get("title"),
                organic_traffic_estimate=p.get("organic_traffic", 0),
                traffic_change=p.get("traffic_change"),
                keywords_count=p.get("keywords_count", 0),
                top_keyword=p.get("top_keyword"),
                backlinks_count=p.get("backlinks", 0),
                refdomains_count=p.get("refdomains", 0),
                source=DataSource.AHREFS, is_estimated=True,
            )
            for p in raw.get("pages", [])
        ]
    """
    logger.info(f"[T2-Search] get_top_pages | site={site_id} country={country} limit={limit}")

    raw = await client.request("site_explorer_top_pages", params={
        "target": site_id, "country": country, "limit": limit,
    })
    now = datetime.now(UTC)
    return [
        TopPage(
            url=p["url"],
            title=p.get("title"),
            organic_traffic_estimate=p.get("organic_traffic_estimate", 0),
            traffic_change=p.get("traffic_change"),
            keywords_count=p.get("keywords_count", 0),
            top_keyword=p.get("top_keyword"),
            backlinks_count=p.get("backlinks_count", 0),
            refdomains_count=p.get("refdomains_count", 0),
            source=DataSource.AHREFS,
            fetched_at=now,
            is_estimated=True,
        )
        for p in raw.get("pages", [])
    ]


async def get_organic_keywords(
    client: Any,
    site_id: str,
    country: str = "us",
    limit: int = 100,
    **kwargs: Any,
) -> list[OrganicKeyword]:
    """获取自然关键词 → list[OrganicKeyword]。"""
    logger.info(f"[T2-Search] get_organic_keywords | site={site_id} limit={limit}")

    raw = await client.request("site_explorer_organic_keywords", params={
        "target": site_id, "country": country, "limit": limit,
    })
    now = datetime.now(UTC)
    keywords = raw.get("keywords", [])
    return [
        OrganicKeyword(
            keyword=k["keyword"],
            position=k.get("position", 1),
            previous_position=k.get("previous_position"),
            position_change=k.get("position_change"),
            search_volume=k.get("search_volume", 0),
            keyword_difficulty=k.get("keyword_difficulty", 0),
            traffic_estimate=k.get("traffic_estimate", 0),
            url=k.get("url"),
            intent=k.get("intent"),
            serp_features=k.get("serp_features", []),
            source=DataSource.AHREFS,
            fetched_at=now,
            is_estimated=True,
        )
        for k in keywords
    ]


async def get_search_history(
    client: Any,
    site_id: str,
    country: str = "us",
    **kwargs: Any,
) -> list[SearchHistoryPoint]:
    """获取搜索表现历史趋势 → list[SearchHistoryPoint]。"""
    logger.info(f"[T2-Search] get_search_history | site={site_id}")

    raw = await client.request("site_explorer_metrics_history", params={
        "target": site_id, "country": country,
    })
    now = datetime.now(UTC)
    return [
        SearchHistoryPoint(
            date=h.get("date", date.today()),
            organic_traffic_estimate=h.get("organic_traffic_estimate", 0),
            organic_keywords_count=h.get("organic_keywords_count", 0),
            top_3_count=h.get("top_3_count", 0),
            source=DataSource.AHREFS,
            fetched_at=now,
            is_estimated=True,
        )
        for h in raw.get("history", [])
    ]
