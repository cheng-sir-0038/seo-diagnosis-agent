"""
T3 — Site Audit（技术审计）

实现 Capability:
  - AUDIT_HEALTH: 站点健康分 → AuditHealth
  - AUDIT_ISSUES: 技术问题列表 → list[AuditIssue]
  - AUDIT_PAGE_EXPLORER: Page Explorer → list[PageExplorerItem]
  - AUDIT_PAGE_CONTENT: 页面内容快照 → PageContent（后续迭代）

产品规则（对齐 PRD）:
  - removed → "已消失"（可作为验证成功证据）
  - missing → "无法确认"（绝不判为已修复）
  - Health Score = 无 Error 的内部 URL 比例
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from src.models.audit import AuditHealth, AuditIssue, PageExplorerItem
from src.models.common import DataSource


async def get_audit_health(client: Any, site_id: str, **kwargs: Any) -> AuditHealth:
    """获取站点健康总览 → AuditHealth。

    真实实现::

        project_id = resolve_project_id(site_id)
        raw = await client.request("site_audit_health", params={
            "project_id": project_id,
        })
        return AuditHealth(
            health_score=raw["health_score"],
            total_urls_crawled=raw.get("total_urls", 0),
            urls_with_errors=raw.get("error_urls", 0),
            urls_with_warnings=raw.get("warning_urls", 0),
            urls_with_notices=raw.get("notice_urls", 0),
            crawl_finished_at=parse_datetime(raw.get("crawl_finished")),
            project_verified=raw.get("verified", False),
            source=DataSource.AHREFS,
            is_estimated=False,
        )
    """
    logger.info(f"[T3-Audit] get_audit_health | site={site_id}")

    raw = await client.request("site_audit_health", params={"project_id": site_id})
    now = datetime.now(timezone.utc)
    return AuditHealth(
        health_score=raw.get("health_score", 0),
        total_urls_crawled=raw.get("total_urls_crawled", 0),
        urls_with_errors=raw.get("urls_with_errors", 0),
        urls_with_warnings=raw.get("urls_with_warnings", 0),
        urls_with_notices=raw.get("urls_with_notices", 0),
        crawl_finished_at=now,
        previous_crawl_finished_at=now,
        project_verified=raw.get("project_verified", False),
        source=DataSource.AHREFS,
        fetched_at=now,
        is_estimated=False,
    )


async def get_audit_issues(
    client: Any,
    site_id: str,
    severity: str | None = None,
    **kwargs: Any,
) -> list[AuditIssue]:
    """获取技术问题列表 → list[AuditIssue]。

    检测状态映射（PRD 附录 A.1）::

        added + new       → 新发现
        no_change         → 持续存在
        removed           → 已消失（可进入验证成功）
        missing           → 无法确认（绝不判为修复）
    """
    logger.info(f"[T3-Audit] get_audit_issues | site={site_id} severity={severity}")

    raw = await client.request("site_audit_issues", params={
        "project_id": site_id, "severity": severity or "all",
    })
    now = datetime.now(timezone.utc)
    issues = raw.get("issues", [])
    return [
        AuditIssue(
            issue_id=i["id"],
            issue_name=i["name"],
            issue_group=i.get("group"),
            severity=i.get("severity", "notice"),
            affected_urls_count=i.get("affected_urls", 0),
            detection_status=i.get("detection_status", "no_change"),
            importance=i.get("importance"),
            description=i.get("description"),
            fix_hint=i.get("fix_hint"),
            source=DataSource.AHREFS,
            fetched_at=now,
            is_estimated=False,
        )
        for i in issues
    ]


async def get_page_explorer(
    client: Any,
    site_id: str,
    url: str | None = None,
    **kwargs: Any,
) -> list[PageExplorerItem]:
    """获取 Page Explorer 数据 → list[PageExplorerItem]。

    支持按 URL 筛选，或按问题类型筛选。
    """
    logger.info(f"[T3-Audit] get_page_explorer | site={site_id} url={url}")

    raw = await client.request("site_audit_page_explorer", params={
        "project_id": site_id, "url": url,
    })
    now = datetime.now(timezone.utc)
    pages = raw.get("pages", [])
    return [
        PageExplorerItem(
            url=p["url"],
            status_code=p.get("status_code"),
            is_indexable=p.get("is_indexable"),
            title=p.get("title"),
            h1=p.get("h1"),
            meta_description=p.get("meta_description"),
            canonical_url=p.get("canonical_url"),
            source=DataSource.AHREFS,
            fetched_at=now,
            is_estimated=False,
        )
        for p in pages
    ]
