"""
技术审计域数据模型 — Site Audit 对应

核心模型:
  - AuditHealth: 站点健康分总览
  - AuditIssue: 单条技术问题
  - PageExplorerItem: Page Explorer 页面条目
  - PageContent: 页面内容快照
  - CrawlComparison: 两次抓取对比

状态枚举:
  - IssueSeverity: 问题严重程度
  - IssueState: 问题生命周期状态
  - PageIndexability: 页面可索引状态

数据来源: Ahrefs Site Audit API

产品规则（对齐 PRD）:
  - removed = 问题已消失（可作为修复验证证据）
  - missing = 本次未抓取 → 进入"无法确认"，绝不判为已修复
  - Health Score = 最新完成抓取中无 Error 的内部 URL 比例
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from pydantic import Field

from src.models.common import BaseEvidence


# ============================================================
# 状态枚举
# ============================================================

class IssueSeverity(str, enum.Enum):
    """SEO 问题严重程度（平台无关）。

    各厂商映射:
      - Ahrefs: error / warning / notice
      - DataForSEO: on_page API 的 severity 字段
    """
    ERROR = "error"
    WARNING = "warning"
    NOTICE = "notice"


class IssueState(str, enum.Enum):
    """问题检测状态（平台无关）。

    各厂商映射:
      - Ahrefs: added/new/removed/missing/no_change → 统一状态
    """
    NEW = "new"               # 新增
    ONGOING = "ongoing"       # 持续存在
    REMOVED = "removed"       # 已消失（可作为验证证据）
    MISSING = "missing"       # 本次未抓取（绝不判为已修复）
    REAPPEARED = "reappeared"  # 重新出现


class PageIndexability(str, enum.Enum):
    """页面可索引状态（平台无关）。

    综合判断页面对搜索引擎的可用性。
    """
    INDEXABLE = "indexable"                   # 正常可索引
    NOT_INDEXABLE = "not_indexable"           # 不可索引（泛指）
    BLOCKED_BY_ROBOTS = "blocked_by_robots"   # robots.txt 屏蔽
    NOINDEX_TAG = "noindex_tag"              # meta noindex 标签
    CANONICAL_MISMATCH = "canonical_mismatch"  # Canonical 指向其他 URL
    NOT_FOUND = "not_found"                  # 404 页面不存在
    REDIRECTED = "redirected"                # 已被重定向
    NOT_IN_SITEMAP = "not_in_sitemap"         # 未在 sitemap 中
    ORPHAN_PAGE = "orphan_page"              # 孤岛页面（无内链指向）
    UNKNOWN = "unknown"


# ============================================================
# 数据模型
# ============================================================


class AuditHealth(BaseEvidence):
    """站点健康总览。

    对应 Ahrefs Site Audit 项目概览面板。

    Attributes:
        health_score: 站点健康分（0-100），无 Error 的 URL 比例。
        total_urls_crawled: 本次抓取的总 URL 数。
        urls_with_errors: 存在 Error 级问题的 URL 数。
        urls_with_warnings: 存在 Warning 级问题的 URL 数。
        urls_with_notices: 存在 Notice 级问题的 URL 数。
        crawl_finished_at: 最近一次完成抓取的时间。
        previous_crawl_finished_at: 上一次完成抓取的时间。
        project_verified: 项目是否已通过所有权验证。
    """

    health_score: float = Field(ge=0, le=100, description="站点健康分")
    total_urls_crawled: int = Field(ge=0)
    urls_with_errors: int = Field(default=0, ge=0)
    urls_with_warnings: int = Field(default=0, ge=0)
    urls_with_notices: int = Field(default=0, ge=0)
    crawl_finished_at: datetime | None = None
    previous_crawl_finished_at: datetime | None = None
    project_verified: bool = False


class AuditIssue(BaseEvidence):
    """单条技术 SEO 问题。

    对应 Ahrefs Site Audit Issues 列表项。

    Attributes:
        issue_id: Ahrefs 内部 issue ID。
        issue_name: 问题名称（如 "4xx page"）。
        issue_group: 问题分类（如 "Indexability"）。
        severity: 严重程度: error / warning / notice。
        affected_urls_count: 受影响的 URL 数量。
        detection_status: 检测状态: added / new / removed / missing / no_change。
            - added + new → "新发现"
            - removed → "已消失"（可作为验证证据）
            - missing → "无法确认"（绝不判为已修复）
        importance: Ahrefs 标记的重要程度。
        description: 问题描述。
        fix_hint: 修复提示。
    """

    issue_id: str
    issue_name: str
    issue_group: str | None = None
    severity: str  # error / warning / notice
    affected_urls_count: int = Field(ge=0)
    detection_status: str = Field(description="added / new / removed / missing / no_change")
    importance: str | None = None
    description: str | None = None
    fix_hint: str | None = None


class PageExplorerItem(BaseEvidence):
    """Page Explorer 单个页面条目。

    Attributes:
        url: 页面 URL。
        status_code: HTTP 状态码。
        is_indexable: 是否可索引。
        title: 页面 Title。
        h1: 页面 H1 标签。
        meta_description: Meta Description。
        canonical_url: Canonical URL。
        has_sitemap: 是否在 Sitemap 中。
        hreflang_tags: hreflang 标签配置情况。
        internal_links_count: 内部链接数。
        outgoing_links_count: 出站链接数。
        page_size_kb: 页面大小（KB）。
        load_time_ms: 加载时间（毫秒）。
    """

    url: str
    status_code: int | None = None
    is_indexable: bool | None = None
    title: str | None = None
    h1: str | None = None
    meta_description: str | None = None
    canonical_url: str | None = None
    has_sitemap: bool | None = None
    hreflang_tags: list[str] = Field(default_factory=list)
    internal_links_count: int = Field(default=0)
    outgoing_links_count: int = Field(default=0)
    page_size_kb: float | None = None
    load_time_ms: float | None = None


class PageContent(BaseEvidence):
    """页面内容快照（敏感数据，需权限控制）。

    Attributes:
        url: 页面 URL。
        text_content: 页面文本内容（纯文本提取）。
        markdown_content: Markdown 格式内容。
        rendered_html: 渲染后 HTML（**敏感字段**，仅对有权限用户可见）。
    """

    url: str
    text_content: str | None = None
    markdown_content: str | None = None
    rendered_html: str | None = Field(default=None, description="敏感：渲染 HTML")


class CrawlComparison(BaseEvidence):
    """两次抓取对比结果。

    Attributes:
        current_crawl_date: 当前抓取日期。
        previous_crawl_date: 对比抓取日期。
        new_errors: 新增 Error 级问题数。
        fixed_issues: 已修复问题数（removed）。
        reopened_issues: 重新出现的问题数。
        unconfirmed_issues: 无法确认的问题数（missing）。
    """

    current_crawl_date: datetime
    previous_crawl_date: datetime
    new_errors: int = 0
    fixed_issues: int = 0
    reopened_issues: int = 0
    unconfirmed_issues: int = 0
