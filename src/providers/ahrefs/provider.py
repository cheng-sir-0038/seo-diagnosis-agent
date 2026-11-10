"""
Ahrefs Provider 主类

组合所有业务能力模块，实现 BaseProvider 接口。
每个 Capability 路由到对应的内部方法，方法内部调用 AhrefsClient
获取原始数据，然后翻译为统一数据模型返回。

真实实现替换指引:
  各业务方法中的 PSEUDO 标记 → 替换为真实 API 调用 + 数据映射逻辑。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from src.models.common import Capability, DataSource
from src.providers.ahrefs import audit as _audit
from src.providers.ahrefs import backlinks as _backlinks
from src.providers.ahrefs import competitors as _competitors
from src.providers.ahrefs import keywords as _keywords

# 各业务模块（伪代码阶段）
from src.providers.ahrefs import management as _mgmt
from src.providers.ahrefs import rank as _rank
from src.providers.ahrefs import search as _search
from src.providers.ahrefs.client import AhrefsClient
from src.providers.base import BaseProvider, ProviderMeta, ProviderMode


class AhrefsProvider(BaseProvider):
    """Ahrefs API v3 数据适配器。

    实现 BaseProvider 接口，将 Ahrefs API 响应翻译为统一数据模型。

    P0 最小链路支持的 Capability::

        SEARCH_METRICS       → get_search_metrics()
        TOP_PAGES            → get_top_pages()
        ORGANIC_KEYWORDS     → get_organic_keywords()
        SEARCH_HISTORY       → get_search_history()
        AUDIT_HEALTH         → get_audit_health()
        AUDIT_ISSUES         → get_audit_issues()
        AUDIT_PAGE_EXPLORER  → get_page_explorer()
        KEYWORD_OVERVIEW     → get_keyword_overview()
        SERP_OVERVIEW        → get_serp_overview()
        RANK_OVERVIEW        → get_rank_overview()
        RANK_DETAIL          → get_ranked_keywords()
        BACKLINKS_LIST       → get_backlinks()
        BROKEN_BACKLINKS     → get_broken_backlinks()
        REFDOMAINS           → get_refdomains()
        ORGANIC_COMPETITORS  → get_organic_competitors()
        BATCH_ANALYSIS       → get_batch_analysis()
        SITE_PROFILE         → get_site_profile()
        SUBSCRIPTION_INFO    → get_subscription_info()

    Attributes:
        client: Ahrefs HTTP 客户端实例。
    """

    # ============================================================
    # 元信息
    # ============================================================

    @property
    def runtime_mode(self) -> ProviderMode:
        return "pseudo"

    @property
    def meta(self) -> ProviderMeta:
        """Ahrefs Provider 元信息。

        当前伪实现支持 18 个 Capability（Capability 枚举共 26 个）。
        """
        return ProviderMeta(
            name="ahrefs",
            label="Ahrefs API v3",
            source=DataSource.AHREFS,
            supported_capabilities={
                # ---- 管理 (T1) ----
                Capability.SITE_PROFILE,
                Capability.SUBSCRIPTION_INFO,
                # ---- 搜索表现 (T2) ----
                Capability.SEARCH_METRICS,
                Capability.SEARCH_HISTORY,
                Capability.TOP_PAGES,
                Capability.ORGANIC_KEYWORDS,
                # ---- 技术审计 (T3) ----
                Capability.AUDIT_HEALTH,
                Capability.AUDIT_ISSUES,
                Capability.AUDIT_PAGE_EXPLORER,
                # ---- 关键词研究 (T4) ----
                Capability.KEYWORD_OVERVIEW,
                Capability.SERP_OVERVIEW,
                # ---- 排名跟踪 (T4) ----
                Capability.RANK_OVERVIEW,
                Capability.RANK_DETAIL,
                # ---- 外链 (T5) ----
                Capability.BACKLINKS_LIST,
                Capability.BROKEN_BACKLINKS,
                Capability.REFDOMAINS,
                # ---- 竞争 (T5) ----
                Capability.ORGANIC_COMPETITORS,
                Capability.BATCH_ANALYSIS,
            },
            version="1.0.0-pseudo",
        )

    # ============================================================
    # 生命周期
    # ============================================================

    async def startup(self) -> None:
        """初始化 Ahrefs 客户端。"""
        self.client = AhrefsClient()
        await self.client.startup()
        logger.info("AhrefsProvider 初始化完成")

    async def shutdown(self) -> None:
        """关闭客户端连接。"""
        if hasattr(self, "client"):
            await self.client.shutdown()
        logger.info("AhrefsProvider 已关闭")

    async def health_check(self) -> dict[str, Any]:
        """报告 Ahrefs 伪模式状态，不伪造远端授权结论。

        当前 Client 始终返回模拟响应，因此不能通过 Subscription Info
        证明 API Key 有效。
        """
        return {
            "status": "degraded",
            "auth_valid": None,
            "mode": "pseudo",
            "message": "Ahrefs Provider 当前仅提供模拟响应，未验证远端授权",
        }

    # ============================================================
    # T1: 管理 — SITE_PROFILE / SUBSCRIPTION_INFO
    # ============================================================

    async def get_site_profile(self, site_id: str, **kwargs: Any) -> Any:
        """获取站点档案和绑定的 Ahrefs 项目信息。

        数据链路:
          1. 从本地存储获取 site_id → Ahrefs project_id 映射
          2. 调用 Management API GET /management/projects 验证
          3. 组装 SiteProfile 模型

        PSEUDO: 当前返回模拟数据，接入真实 Key 后替换。
        """
        return await _mgmt.get_site_profile(self.client, site_id, **kwargs)

    async def get_subscription_info(self, **kwargs: Any) -> Any:
        """获取订阅和额度信息。

        数据链路:
          1. 调用 GET /subscription-info/limits-and-usage
          2. 解析 → SubscriptionInfo 模型
        """
        return await _mgmt.get_subscription_info(self.client, **kwargs)

    # ============================================================
    # T2: 搜索表现 — SEARCH_METRICS / TOP_PAGES / ORGANIC_KEYWORDS / SEARCH_HISTORY
    # ============================================================

    async def get_search_metrics(
        self, site_id: str, country: str = "us", **kwargs: Any
    ) -> Any:
        """获取站点搜索表现总览 → SearchMetrics。

        数据链路:
          site_id → project_domain → GET /site-explorer/metrics → SearchMetrics
        """
        return await _search.get_search_metrics(self.client, site_id, country, **kwargs)

    async def get_top_pages(
        self, site_id: str, country: str = "us", limit: int = 10, **kwargs: Any
    ) -> Any:
        """获取 Top Pages → list[TopPage]。

        数据链路:
          site_id → project_domain → GET /site-explorer/top-pages → list[TopPage]
        """
        return await _search.get_top_pages(self.client, site_id, country, limit, **kwargs)

    async def get_organic_keywords(
        self, site_id: str, country: str = "us", limit: int = 100, **kwargs: Any
    ) -> Any:
        """获取自然关键词 → list[OrganicKeyword]。

        数据链路:
          site_id → project_domain → GET /site-explorer/organic-keywords → list[OrganicKeyword]
        """
        return await _search.get_organic_keywords(self.client, site_id, country, limit, **kwargs)

    async def get_search_history(
        self, site_id: str, country: str = "us", **kwargs: Any
    ) -> Any:
        """获取搜索表现历史趋势 → list[SearchHistoryPoint]。

        数据链路:
          site_id → project_domain → GET /site-explorer/metrics-history → list[SearchHistoryPoint]
        """
        return await _search.get_search_history(self.client, site_id, country, **kwargs)

    # ============================================================
    # T3: 技术审计 — AUDIT_HEALTH / AUDIT_ISSUES / AUDIT_PAGE_EXPLORER
    # ============================================================

    async def get_audit_health(self, site_id: str, **kwargs: Any) -> Any:
        """获取站点健康分 → AuditHealth。

        数据链路:
          site_id → project_id → GET /site-audit/projects/{project_id} → AuditHealth
        """
        return await _audit.get_audit_health(self.client, site_id, **kwargs)

    async def get_audit_issues(
        self, site_id: str, severity: str | None = None, **kwargs: Any
    ) -> Any:
        """获取技术问题列表 → list[AuditIssue]。

        数据链路:
          site_id → project_id → GET /site-audit/issues/{project_id} → list[AuditIssue]
        """
        return await _audit.get_audit_issues(self.client, site_id, severity, **kwargs)

    async def get_page_explorer(
        self, site_id: str, url: str | None = None, **kwargs: Any
    ) -> Any:
        """获取 Page Explorer → list[PageExplorerItem]。

        数据链路:
          site_id → project_id → GET /site-audit/page-explorer/{project_id} → list[PageExplorerItem]
        """
        return await _audit.get_page_explorer(self.client, site_id, url, **kwargs)

    # ============================================================
    # T4: 关键词 + 排名 — KEYWORD_OVERVIEW / SERP_OVERVIEW / RANK_OVERVIEW / RANK_DETAIL
    # ============================================================

    async def get_keyword_overview(
        self, keywords: list[str], country: str = "us", **kwargs: Any
    ) -> Any:
        """获取关键词概览 → list[KeywordOverview]。

        数据链路:
          批量 keywords → GET /keywords-explorer/overview → list[KeywordOverview]
        """
        return await _keywords.get_keyword_overview(self.client, keywords, country, **kwargs)

    async def get_serp_overview(
        self, keyword: str, country: str = "us", **kwargs: Any
    ) -> Any:
        """获取 SERP 结果 → list[SerpResult]。

        数据链路:
          keyword + country → GET /serp-overview/search → list[SerpResult]
        """
        return await _keywords.get_serp_overview(self.client, keyword, country, **kwargs)

    async def get_rank_overview(self, site_id: str, **kwargs: Any) -> Any:
        """获取排名项目总览 → RankOverview。

        数据链路:
          site_id → project_id → GET /rank-tracker/projects/{project_id} → RankOverview
        """
        return await _rank.get_rank_overview(self.client, site_id, **kwargs)

    async def get_ranked_keywords(
        self, site_id: str, limit: int = 100, **kwargs: Any
    ) -> Any:
        """获取单个关键词排名详情 → list[RankedKeyword]。

        数据链路:
          site_id → project_id → GET /rank-tracker/keywords/{project_id} → list[RankedKeyword]
        """
        return await _rank.get_ranked_keywords(self.client, site_id, limit, **kwargs)

    # ============================================================
    # T5: 外链 + 竞争 — BACKLINKS / REFDOMAINS / BROKEN / COMPETITORS / BATCH
    # ============================================================

    async def get_backlinks(
        self, site_id: str, limit: int = 100, **kwargs: Any
    ) -> Any:
        """获取外链列表 → list[Backlink]。

        数据链路:
          site_id → project_domain → GET /site-explorer/backlinks → list[Backlink]
        """
        return await _backlinks.get_backlinks(self.client, site_id, limit, **kwargs)

    async def get_refdomains(
        self, site_id: str, limit: int = 100, **kwargs: Any
    ) -> Any:
        """获取引用域名 → list[RefDomain]。

        数据链路:
          site_id → project_domain → GET /site-explorer/refdomains → list[RefDomain]
        """
        return await _backlinks.get_refdomains(self.client, site_id, limit, **kwargs)

    async def get_broken_backlinks(
        self, site_id: str, limit: int = 100, **kwargs: Any
    ) -> Any:
        """获取失效外链 → list[BrokenBacklink]。

        数据链路:
          site_id → project_domain → GET /site-explorer/broken-backlinks → list[BrokenBacklink]
        """
        return await _backlinks.get_broken_backlinks(self.client, site_id, limit, **kwargs)

    async def get_organic_competitors(
        self, site_id: str, country: str = "us", **kwargs: Any
    ) -> Any:
        """获取自然搜索竞争对手 → list[OrganicCompetitor]。

        数据链路:
          site_id → project_domain → GET /site-explorer/organic-competitors
          → list[OrganicCompetitor]
        """
        return await _competitors.get_organic_competitors(self.client, site_id, country, **kwargs)

    async def get_batch_analysis(
        self, targets: list[str], **kwargs: Any
    ) -> Any:
        """批量域名分析 → list[BatchAnalysisResult]。

        数据链路:
          targets → POST /batch-analysis → list[BatchAnalysisResult]
        """
        return await _competitors.get_batch_analysis(self.client, targets, **kwargs)
