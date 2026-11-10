"""
Provider 抽象基类

所有数据源 Provider 必须继承 ``BaseProvider`` 并实现其声明的能力。
Provider 不对外暴露——上层通过 Facade 调用，不直接接触 Provider 实例。

设计原则:
  1. Provider 是"翻译器"：把外部 API 响应翻译成统一数据模型。
  2. Provider 不负责编排：单个方法只做一次（或一组紧密相关）API 调用。
  3. Provider 不计算可信度：confidence 字段由 Facade/Agent 层设置。
  4. Provider 内部异常统一转为 InternalXxxError，不直接抛出。
  5. 每个 Provider 声明自己支持哪些 Capability，Facade 据此路由。

扩展新数据源:
  继承 BaseProvider，实现你需要的能力方法，
  在 ``supported_capabilities`` 中声明支持列表即可。
  Facade 会自动发现并路由。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Literal

from src.models.common import Capability, DataSource
from src.routing.contracts import ProviderProfile

ProviderMode = Literal["live", "pseudo", "unavailable"]


@dataclass
class ProviderMeta:
    """Provider 元信息。

    Attributes:
        name: Provider 唯一标识，如 "ahrefs"。
        label: 人类可读名称，如 "Ahrefs API v3"。
        source: 对应的 DataSource 枚举值。
        supported_capabilities: 该 Provider 支持的 Capability 列表。
        version: Provider 版本号。
    """
    name: str
    label: str
    source: DataSource
    supported_capabilities: set[Capability] = field(default_factory=set)
    version: str = "1.0.0"


class BaseProvider(abc.ABC):
    """所有数据源 Provider 的抽象基类。

    子类必须实现:
      - ``_meta`` 属性：返回 ProviderMeta。
      - 各业务能力方法（如 ``get_search_metrics()``）。

    子类可选覆盖:
      - ``health_check()``：验证连接和授权状态。
      - ``startup()`` / ``shutdown()``：生命周期管理。

    使用方式（Facade 内部）::

        provider = AhrefsProvider()
        if Capability.SEARCH_METRICS in provider.capabilities:
            result = await provider.get_search_metrics(site_id="...", ...)
    """

    def __init__(self) -> None:
        self._meta: ProviderMeta | None = None

    # ============================================================
    # 元信息
    # ============================================================

    @property
    @abc.abstractmethod
    def meta(self) -> ProviderMeta:
        """返回 Provider 元信息。子类必须实现。"""

    @property
    def capabilities(self) -> set[Capability]:
        """该 Provider 支持的 Capability 集合（从 meta 派生）。"""
        if self._meta is None:
            self._meta = self.meta
        return self._meta.supported_capabilities

    @property
    def source(self) -> DataSource:
        """对应的 DataSource 枚举值。"""
        if self._meta is None:
            self._meta = self.meta
        return self._meta.source

    @property
    def runtime_mode(self) -> ProviderMode:
        """Provider 当前运行模式。默认不可用，避免误把新 Provider 当作真实数据源。"""
        return "unavailable"

    @property
    def routing_profile(self) -> ProviderProfile:
        """返回保守能力档案；子类可覆盖以声明更细粒度能力。"""
        return ProviderProfile(
            provider_name=self.meta.name,
            source=self.source,
            runtime_mode=self.runtime_mode,
            capabilities=frozenset(self.capabilities),
        )

    def supports(self, capability: Capability) -> bool:
        """检查是否支持某个 Capability。

        Args:
            capability: 要检查的业务能力。

        Returns:
            True 如果支持。
        """
        return capability in self.capabilities

    # ============================================================
    # 生命周期
    # ============================================================

    async def startup(self) -> None:
        """Provider 初始化（建立连接、验证授权、预热缓存等）。

        在 FastAPI startup 事件中调用。
        默认实现为空，子类按需覆盖。
        """
        pass

    async def shutdown(self) -> None:
        """Provider 清理（关闭连接池、保存状态等）。

        在 FastAPI shutdown 事件中调用。
        默认实现为空，子类按需覆盖。
        """
        pass

    async def health_check(self) -> dict[str, Any]:
        """验证 Provider 连接和授权状态。

        Returns:
            包含 status 和 details 的 dict。
            格式: {"status": "ok", "auth_valid": True, "quota_remaining": 8500}
        """
        return {"status": "ok", "message": "base provider (health_check not implemented)"}

    # ============================================================
    # 业务能力方法签名（子类按需实现）
    # ============================================================

    # -- 搜索表现 --
    async def get_search_metrics(self, site_id: str, country: str, **kwargs: Any) -> Any:
        """获取站点搜索表现总览 → SearchMetrics。"""
        raise NotImplementedError(f"{self.meta.name}: get_search_metrics")

    async def get_top_pages(
        self, site_id: str, country: str, limit: int = 10, **kwargs: Any
    ) -> Any:
        """获取 Top Pages → list[TopPage]。"""
        raise NotImplementedError(f"{self.meta.name}: get_top_pages")

    async def get_organic_keywords(
        self, site_id: str, country: str, limit: int = 100, **kwargs: Any
    ) -> Any:
        """获取自然关键词 → list[OrganicKeyword]。"""
        raise NotImplementedError(f"{self.meta.name}: get_organic_keywords")

    async def get_search_history(
        self, site_id: str, country: str, **kwargs: Any
    ) -> Any:
        """获取搜索表现历史趋势 → list[SearchHistoryPoint]。"""
        raise NotImplementedError(f"{self.meta.name}: get_search_history")

    # -- 技术审计 --
    async def get_audit_health(self, site_id: str, **kwargs: Any) -> Any:
        """获取站点健康分总览 → AuditHealth。"""
        raise NotImplementedError(f"{self.meta.name}: get_audit_health")

    async def get_audit_issues(
        self, site_id: str, severity: str | None = None, **kwargs: Any
    ) -> Any:
        """获取技术问题列表 → list[AuditIssue]。"""
        raise NotImplementedError(f"{self.meta.name}: get_audit_issues")

    async def get_page_explorer(
        self, site_id: str, url: str | None = None, **kwargs: Any
    ) -> Any:
        """获取 Page Explorer 数据 → list[PageExplorerItem]。"""
        raise NotImplementedError(f"{self.meta.name}: get_page_explorer")

    async def get_page_metadata(
        self, site_id: str, url: str | None = None, **kwargs: Any
    ) -> Any:
        """获取页面源 HTML 元信息。"""
        raise NotImplementedError(f"{self.meta.name}: get_page_metadata")

    async def get_page_performance(
        self, site_id: str, url: str | None = None, **kwargs: Any
    ) -> Any:
        """获取单页面 PageSpeed/Lighthouse 实验室报告。"""
        raise NotImplementedError(f"{self.meta.name}: get_page_performance")

    # -- 关键词研究 --
    async def get_keyword_overview(
        self, keywords: list[str], country: str, **kwargs: Any
    ) -> Any:
        """获取关键词概览 → list[KeywordOverview]。"""
        raise NotImplementedError(f"{self.meta.name}: get_keyword_overview")

    async def get_serp_overview(
        self, keyword: str, country: str, **kwargs: Any
    ) -> Any:
        """获取 SERP 结果 → list[SerpResult]。"""
        raise NotImplementedError(f"{self.meta.name}: get_serp_overview")

    async def get_autocomplete(
        self, query: str, country: str, language: str, **kwargs: Any
    ) -> Any:
        """获取 Google 搜索联想词。"""
        raise NotImplementedError(f"{self.meta.name}: get_autocomplete")

    async def get_news_search(
        self, query: str, country: str, language: str, **kwargs: Any
    ) -> Any:
        """获取实时新闻搜索结果。"""
        raise NotImplementedError(f"{self.meta.name}: get_news_search")

    async def get_image_search(
        self, query: str, country: str, language: str, **kwargs: Any
    ) -> Any:
        """获取图片搜索结果。"""
        raise NotImplementedError(f"{self.meta.name}: get_image_search")

    async def get_video_search(
        self, query: str, country: str, language: str, **kwargs: Any
    ) -> Any:
        """获取视频搜索结果。"""
        raise NotImplementedError(f"{self.meta.name}: get_video_search")

    # -- 排名跟踪 --
    async def get_rank_overview(self, site_id: str, **kwargs: Any) -> Any:
        """获取排名项目总览 → RankOverview。"""
        raise NotImplementedError(f"{self.meta.name}: get_rank_overview")

    async def get_ranked_keywords(
        self, site_id: str, limit: int = 100, **kwargs: Any
    ) -> Any:
        """获取单个关键词排名 → list[RankedKeyword]。"""
        raise NotImplementedError(f"{self.meta.name}: get_ranked_keywords")

    # -- 外链 --
    async def get_backlinks(
        self, site_id: str, limit: int = 100, **kwargs: Any
    ) -> Any:
        """获取外链列表 → list[Backlink]。"""
        raise NotImplementedError(f"{self.meta.name}: get_backlinks")

    async def get_refdomains(self, site_id: str, limit: int = 100, **kwargs: Any) -> Any:
        """获取引用域名 → list[RefDomain]。"""
        raise NotImplementedError(f"{self.meta.name}: get_refdomains")

    async def get_broken_backlinks(
        self, site_id: str, limit: int = 100, **kwargs: Any
    ) -> Any:
        """获取失效外链 → list[BrokenBacklink]。"""
        raise NotImplementedError(f"{self.meta.name}: get_broken_backlinks")

    # -- 竞争分析 --
    async def get_organic_competitors(
        self, site_id: str, country: str, **kwargs: Any
    ) -> Any:
        """获取自然搜索竞争对手 → list[OrganicCompetitor]。"""
        raise NotImplementedError(f"{self.meta.name}: get_organic_competitors")

    async def get_batch_analysis(
        self, targets: list[str], **kwargs: Any
    ) -> Any:
        """批量域名分析 → list[BatchAnalysisResult]。"""
        raise NotImplementedError(f"{self.meta.name}: get_batch_analysis")

    # -- 管理 --
    async def get_site_profile(self, site_id: str, **kwargs: Any) -> Any:
        """获取站点档案 → SiteProfile。"""
        raise NotImplementedError(f"{self.meta.name}: get_site_profile")

    async def get_subscription_info(self, **kwargs: Any) -> Any:
        """获取订阅/额度信息 → SubscriptionInfo。"""
        raise NotImplementedError(f"{self.meta.name}: get_subscription_info")
