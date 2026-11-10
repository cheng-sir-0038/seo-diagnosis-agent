"""
T5a — 外链分析（Backlinks / Refdomains / Broken）

实现 Capability:
  - BACKLINKS_LIST: 外链列表 → list[Backlink]
  - REFDOMAINS: 引用域名 → list[RefDomain]
  - BROKEN_BACKLINKS: 失效外链 → list[BrokenBacklink]

数据来源: Ahrefs Site Explorer Backlinks API

产品规则（对齐 PRD）:
  - nofollow 不自动判定为无价值
  - DR 仅作为价值信号之一，需结合主题相关性
  - 相似链接默认合并，同一域名大量链接合并避免夸大
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from src.models.backlink import Backlink, RefDomain, BrokenBacklink
from src.models.common import DataSource


async def get_backlinks(
    client: Any,
    site_id: str,
    limit: int = 100,
    **kwargs: Any,
) -> list[Backlink]:
    """获取外链列表 → list[Backlink]。

    真实实现::

        domain = resolve_domain(site_id)
        raw = await client.request("site_explorer_backlinks", params={
            "target": domain, "limit": limit,
        })
        return [Backlink(source_url=b["source_url"], ...) for b in raw.get("backlinks", [])]
    """
    logger.info(f"[T5a-Backlinks] get_backlinks | site={site_id} limit={limit}")

    raw = await client.request("site_explorer_backlinks", params={
        "target": site_id, "limit": limit,
    })
    now = datetime.now(timezone.utc)
    return [
        Backlink(
            source_url=b["source_url"],
            source_title=b.get("source_title"),
            target_url=b["target_url"],
            anchor_text=b.get("anchor_text"),
            link_type=b.get("link_type", "dofollow"),
            source_domain_rating=b.get("source_domain_rating"),
            is_lost=b.get("is_lost", False),
            lost_reason=b.get("lost_reason"),
            source=DataSource.AHREFS,
            fetched_at=now,
            is_estimated=False,
        )
        for b in raw.get("backlinks", [])
    ]


async def get_refdomains(
    client: Any,
    site_id: str,
    limit: int = 100,
    **kwargs: Any,
) -> list[RefDomain]:
    """获取引用域名列表 → list[RefDomain]。"""
    logger.info(f"[T5a-Backlinks] get_refdomains | site={site_id} limit={limit}")

    raw = await client.request("site_explorer_refdomains", params={
        "target": site_id, "limit": limit,
    })
    now = datetime.now(timezone.utc)
    return [
        RefDomain(
            domain=d["domain"],
            domain_rating=d.get("domain_rating"),
            backlinks_count=d.get("backlinks_count", 0),
            dofollow_count=d.get("dofollow_count", 0),
            is_lost=d.get("is_lost", False),
            primary_target_url=d.get("primary_target_url"),
            organic_traffic_estimate=d.get("organic_traffic_estimate"),
            source=DataSource.AHREFS,
            fetched_at=now,
            is_estimated=True,
        )
        for d in raw.get("refdomains", [])
    ]


async def get_broken_backlinks(
    client: Any,
    site_id: str,
    limit: int = 100,
    **kwargs: Any,
) -> list[BrokenBacklink]:
    """获取失效外链 → list[BrokenBacklink]。

    每条的 suggested_action 由 Agent 层判断，Provider 不做建议。
    """
    logger.info(f"[T5a-Backlinks] get_broken_backlinks | site={site_id} limit={limit}")

    raw = await client.request("site_explorer_broken_backlinks", params={
        "target": site_id, "limit": limit,
    })
    now = datetime.now(timezone.utc)
    return [
        BrokenBacklink(
            source_url=b["source_url"],
            target_url=b["target_url"],
            http_status=b.get("http_status"),
            redirect_url=b.get("redirect_url"),
            anchor_text=b.get("anchor_text"),
            source_domain_rating=b.get("source_domain_rating"),
            source=DataSource.AHREFS,
            fetched_at=now,
            is_estimated=False,
        )
        for b in raw.get("broken_backlinks", [])
    ]
