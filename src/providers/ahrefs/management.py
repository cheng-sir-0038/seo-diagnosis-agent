"""
T1 — 站点管理与订阅信息

实现 Capability:
  - SITE_PROFILE: 获取站点档案 → SiteProfile
  - SUBSCRIPTION_INFO: 获取额度信息 → SubscriptionInfo

数据链路::

    get_site_profile(site_id)
      ├── 从本地存储解析 site_id → Ahrefs project_id
      ├── client.request("management_projects", params={...})
      ├── 映射 → SiteProfile + AhrefsProject
      └── 返回

    get_subscription_info()
      ├── client.request("subscription_info", params={})
      ├── 映射 → SubscriptionInfo
      └── 返回
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from src.models.management import SiteProfile, AhrefsProject, SubscriptionInfo
from src.models.common import DataSource, Country


async def get_site_profile(
    client: Any,   # AhrefsClient
    site_id: str,
    **kwargs: Any,
) -> SiteProfile:
    """获取站点档案 → SiteProfile。

    真实实现步骤::

        # 1. 从本地 DB/配置获取 site_id → project_id 映射
        project_id = await resolve_project_id(site_id)

        # 2. 调用 Management API
        raw = await client.request("management_projects", params={
            "project_id": project_id,
        })

        # 3. 解析项目信息
        project = AhrefsProject(
            project_id=raw["id"],
            project_name=raw["name"],
            target_domain=raw["target"],
            target_mode=raw.get("target_mode", "domain"),
            protocol=raw.get("protocol", "https"),
            is_verified=raw.get("verified", False),
            created_at=raw.get("created_at"),
            status=raw.get("status", "active"),
            source=DataSource.AHREFS,
            is_estimated=False,
        )

        # 4. 组装 SiteProfile（本地配置 + Ahrefs 项目）
        profile = SiteProfile(
            site_id=site_id,
            site_name=raw.get("name", site_id),
            primary_domain=raw["target"],
            ...本地配置字段...
            ahrefs_project=project,
            source=DataSource.AHREFS,
            is_estimated=False,
        )
        return profile
    """
    logger.info(f"[T1-Management] get_site_profile | site_id={site_id}")

    # PSEUDO: 真实实现时替换为上述步骤
    now = datetime.now(timezone.utc).isoformat()
    project = AhrefsProject(
        project_id="proj_abc123",
        project_name="示例站点项目",
        target_domain="example.com",
        target_mode="domain",
        protocol="https",
        is_verified=True,
        created_at="2026-01-15",
        status="active",
        source=DataSource.AHREFS,
        fetched_at=datetime.now(timezone.utc),
        is_estimated=False,
    )

    return SiteProfile(
        site_id=site_id,
        site_name="示例站点",
        primary_domain="example.com",
        target_mode="domain",
        default_country=Country(code="us", name="美国"),
        default_language="en",
        default_device="desktop",
        timezone="America/New_York",
        brand_terms=["example", "example.com"],
        business_description="示例 SaaS 产品",
        focus_pages=["/pricing", "/blog", "/docs"],
        business_competitors=["competitor1.com", "competitor2.com"],
        allow_agent_writes=False,
        ahrefs_project=project,
        data_domain_status={
            "audit": "available",
            "search": "available",
            "keywords": "available",
            "rank": "available",
            "backlinks": "available",
            "competitors": "available",
        },
        source=DataSource.AHREFS,
        fetched_at=datetime.now(timezone.utc),
        is_estimated=False,
    )


async def get_subscription_info(
    client: Any,
    **kwargs: Any,
) -> SubscriptionInfo:
    """获取订阅与额度信息 → SubscriptionInfo。

    真实实现步骤::

        raw = await client.request("subscription_info", params={})
        return SubscriptionInfo(
            plan_name=raw.get("plan"),
            api_units_limit=raw["units_limit"],
            api_units_used=raw["units_used"],
            api_units_remaining=raw["units_limit"] - raw["units_used"],
            usage_percentage=(raw["units_used"] / raw["units_limit"]) * 100,
            reset_date=raw.get("reset_date"),
            api_key_expiry=raw.get("api_key_expiry"),
            rate_limit_per_minute=raw.get("rate_limit", 60),
            workspace_name=raw.get("workspace"),
            source=DataSource.AHREFS,
            is_estimated=False,
        )
    """
    logger.info("[T1-Management] get_subscription_info")

    # PSEUDO: 真实实现时调用 client.request("subscription_info")
    now = datetime.now(timezone.utc).isoformat()
    return SubscriptionInfo(
        plan_name="Ahrefs Advanced",
        api_units_limit=500000,
        api_units_used=235000,
        api_units_remaining=265000,
        usage_percentage=47.0,
        reset_date="2026-08-01",
        api_key_expiry="2027-01-15",
        rate_limit_per_minute=60,
        workspace_name="主工作区",
        source=DataSource.AHREFS,
        fetched_at=datetime.now(timezone.utc),
        is_estimated=False,
    )
