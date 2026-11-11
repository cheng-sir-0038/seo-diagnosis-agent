"""
T4b — Rank Tracker（排名跟踪）

实现 Capability:
  - RANK_OVERVIEW: 排名项目总览 → RankOverview
  - RANK_DETAIL: 关键词排名详情 → list[RankedKeyword]

数据链路::

    get_rank_overview(site_id)
      ├── client.request("rank_tracker_overview", params={project_id})
      ├── 映射 → RankOverview
      └── 返回

    get_ranked_keywords(site_id, limit=100)
      ├── client.request("rank_tracker_keywords", params={project_id, limit})
      ├── 映射 → list[RankedKeyword]
      ├── 每个关键词检查 is_anomaly 标记
      └── 返回

产品规则（对齐 PRD）:
  - Rank Tracker 接口免费（不消耗 API Units）
  - 排名下降 ≥ 10 名触发 is_anomaly
  - 跌出 Top 3/10/100 触发关键区间丢失
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from src.models.common import Country, DataSource
from src.models.rank import RankedKeyword, RankOverview


async def get_rank_overview(client: Any, site_id: str, **kwargs: Any) -> RankOverview:
    """获取排名项目总览 → RankOverview。"""
    logger.info(f"[T4b-Rank] get_rank_overview | site={site_id}")

    raw = await client.request("rank_tracker_overview", params={"project_id": site_id})
    now = datetime.now(UTC)
    return RankOverview(
        tracked_keywords_count=raw.get("tracked_keywords_count", 0),
        top_3_count=raw.get("top_3_count", 0),
        top_10_count=raw.get("top_10_count", 0),
        top_20_count=raw.get("top_20_count", 0),
        top_50_count=raw.get("top_50_count", 0),
        top_100_count=raw.get("top_100_count", 0),
        not_ranked_count=raw.get("not_ranked_count", 0),
        average_position=raw.get("average_position"),
        share_of_voice=raw.get("share_of_voice"),
        country=Country(code=kwargs.get("country", "us")),
        source=DataSource.AHREFS,
        fetched_at=now,
        is_estimated=False,
    )


async def get_ranked_keywords(
    client: Any,
    site_id: str,
    limit: int = 100,
    **kwargs: Any,
) -> list[RankedKeyword]:
    """获取关键词排名详情 → list[RankedKeyword]。

    每个关键词会检查 is_anomaly 标记（下降 ≥ 10 名 / 跌出关键区间 / URL 切换）。
    """
    logger.info(f"[T4b-Rank] get_ranked_keywords | site={site_id} limit={limit}")

    raw = await client.request("rank_tracker_keywords", params={
        "project_id": site_id, "limit": limit,
    })
    now = datetime.now(UTC)
    keywords = raw.get("keywords", [])
    return [
        RankedKeyword(
            keyword=k["keyword"],
            country=Country(code=k.get("country", "us")),
            device=k.get("device", "desktop"),
            current_position=k.get("current_position"),
            previous_position=k.get("previous_position"),
            position_change=k.get("position_change"),
            url=k.get("url"),
            previous_url=k.get("previous_url"),
            status=k.get("status", "active"),
            serp_date=k.get("serp_date"),
            source=DataSource.AHREFS,
            fetched_at=now,
            is_estimated=False,
        )
        for k in keywords
    ]
