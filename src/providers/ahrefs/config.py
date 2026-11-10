"""
Ahrefs Provider 配置

从全局 Settings 继承，附加 Ahrefs 特定的端点映射、重试策略等。

真实实现替换指引:
  1. 替换 settings.ahrefs_api_key 为真实 Key
  2. 替换 api_base_url 为真实 API 地址（当前默认 https://api.ahrefs.com/v3）
  3. 配置实际的 rate_limit 值（默认 60 req/min）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.config import settings


@dataclass
class AhrefsConfig:
    """Ahrefs Provider 配置。

    Attributes:
        api_base_url: API 基础 URL。
        api_key: API Key（从 settings 读取）。
        rate_limit_rpm: 每分钟最大请求数。
        request_timeout: 单次请求超时（秒）。
        max_retries: 最大重试次数。
        retry_backoff_base: 重试退避基础时间（秒）。
        retry_on_statuses: 触发重试的 HTTP 状态码集合。
    """

    api_base_url: str = settings.ahrefs_api_base_url
    api_key: str = field(default_factory=lambda: settings.ahrefs_api_key)

    # 速率控制
    rate_limit_rpm: int = settings.ahrefs_rate_limit
    request_timeout: int = settings.ahrefs_request_timeout

    # 重试策略
    max_retries: int = 3
    retry_backoff_base: float = 2.0  # 指数退避: base * 2^attempt
    retry_on_statuses: set[int] = field(default_factory=lambda: {429, 502, 503, 504})


# ============================================================
# Ahrefs API 端点路由表
# ============================================================

@dataclass
class AhrefsEndpoint:
    """单个 API 端点的定义。

    Attributes:
        method: HTTP 方法。
        path: API 路径（相对于 api_base_url）。
        estimated_units: 估计单次调用消耗的 API Units（基于 Ahrefs 文档）。
        description: 端点简短描述。
    """
    method: str = "GET"
    path: str = ""
    estimated_units: int = 50  # 默认 50 units
    description: str = ""


# 端点映射表（仅包含 P0 最小链路需要用到的端点）
# 完整端点列表见 PRD 附录 C
ENDPOINTS: dict[str, AhrefsEndpoint] = {
    # ---- Site Explorer ----
    "site_explorer_metrics": AhrefsEndpoint(
        method="GET",
        path="/site-explorer/metrics",
        estimated_units=50,
        description="域名/站点搜索表现总览 → SearchMetrics",
    ),
    "site_explorer_metrics_history": AhrefsEndpoint(
        method="GET",
        path="/site-explorer/metrics-history",
        estimated_units=50,
        description="搜索表现历史趋势 → SearchHistoryPoint",
    ),
    "site_explorer_top_pages": AhrefsEndpoint(
        method="GET",
        path="/site-explorer/top-pages",
        estimated_units=50,
        description="Top Pages 榜单 → TopPage",
    ),
    "site_explorer_organic_keywords": AhrefsEndpoint(
        method="GET",
        path="/site-explorer/organic-keywords",
        estimated_units=50,
        description="自然关键词 → OrganicKeyword",
    ),
    "site_explorer_backlinks": AhrefsEndpoint(
        method="GET",
        path="/site-explorer/backlinks",
        estimated_units=50,
        description="外链列表 → Backlink",
    ),
    "site_explorer_refdomains": AhrefsEndpoint(
        method="GET",
        path="/site-explorer/refdomains",
        estimated_units=50,
        description="引用域名 → RefDomain",
    ),
    "site_explorer_broken_backlinks": AhrefsEndpoint(
        method="GET",
        path="/site-explorer/broken-backlinks",
        estimated_units=50,
        description="失效外链 → BrokenBacklink",
    ),
    "site_explorer_organic_competitors": AhrefsEndpoint(
        method="GET",
        path="/site-explorer/organic-competitors",
        estimated_units=50,
        description="自然搜索竞争对手 → OrganicCompetitor",
    ),

    # ---- Site Audit ----
    "site_audit_health": AhrefsEndpoint(
        method="GET",
        path="/site-audit/projects/{project_id}",
        estimated_units=50,
        description="站点健康分总览 → AuditHealth",
    ),
    "site_audit_issues": AhrefsEndpoint(
        method="GET",
        path="/site-audit/issues/{project_id}",
        estimated_units=50,
        description="技术问题列表 → AuditIssue",
    ),
    "site_audit_page_explorer": AhrefsEndpoint(
        method="GET",
        path="/site-audit/page-explorer/{project_id}",
        estimated_units=100,
        description="Page Explorer → PageExplorerItem",
    ),

    # ---- Keywords Explorer ----
    "keywords_explorer_overview": AhrefsEndpoint(
        method="GET",
        path="/keywords-explorer/overview",
        estimated_units=50,
        description="关键词概览 → KeywordOverview",
    ),

    # ---- SERP Overview ----
    "serp_overview": AhrefsEndpoint(
        method="GET",
        path="/serp-overview/search",
        estimated_units=50,
        description="SERP 结果 → SerpResult",
    ),

    # ---- Rank Tracker ----
    "rank_tracker_overview": AhrefsEndpoint(
        method="GET",
        path="/rank-tracker/projects/{project_id}",
        estimated_units=0,  # Rank Tracker 免费
        description="排名总览 → RankOverview",
    ),
    "rank_tracker_keywords": AhrefsEndpoint(
        method="GET",
        path="/rank-tracker/keywords/{project_id}",
        estimated_units=0,
        description="关键词排名详情 → RankedKeyword",
    ),

    # ---- Management ----
    "management_projects": AhrefsEndpoint(
        method="GET",
        path="/management/projects",
        estimated_units=0,  # Management API 免费
        description="项目列表 → AhrefsProject",
    ),

    # ---- Subscription Info ----
    "subscription_info": AhrefsEndpoint(
        method="GET",
        path="/subscription-info/limits-and-usage",
        estimated_units=0,  # 免费
        description="额度信息 → SubscriptionInfo",
    ),

    # ---- Batch Analysis ----
    "batch_analysis": AhrefsEndpoint(
        method="POST",
        path="/batch-analysis",
        estimated_units=100,
        description="批量域名分析 → BatchAnalysisResult",
    ),
}
