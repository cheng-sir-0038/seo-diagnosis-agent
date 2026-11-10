"""
搜索表现域数据模型 — Site Explorer 对应

核心模型:
  - SearchMetrics: 站点/域名级搜索表现总览
  - TopPage: Top Pages 单条记录
  - OrganicKeyword: 自然关键词单条记录
  - SearchHistoryPoint: 历史趋势数据点

数据来源: Ahrefs Site Explorer API
    - GET /site-explorer/metrics
    - GET /site-explorer/top-pages
    - GET /site-explorer/organic-keywords
    - GET /site-explorer/metrics-history
"""

from __future__ import annotations

from datetime import date

from pydantic import Field

from src.models.common import BaseEvidence, Country

# ============================================================
# 搜索表现总览
# ============================================================

class SearchMetrics(BaseEvidence):
    """站点/域名级别的搜索表现总览。

    对应 Ahrefs Site Explorer Overview 面板的核心指标。
    所有流量/价值字段均为 **Ahrefs 估算**，不得称为真实访问量。

    Attributes:
        domain_rating: 域名评级（DR），Ahrefs 的域名外链强度指标（0-100）。
        ahrefs_rank: Ahrefs Rank，域名全球排名。
        organic_traffic_estimate: 自然流量估算（月均访客）。
        traffic_value_estimate: 流量价值估算（等值 PPC 成本）。
        organic_keywords_count: 进入 Top 100 的自然关键词数量。
        top_3_count: 排名 Top 3 的关键词数量。
        top_10_count: 排名 Top 10 的关键词数量。
        ranked_pages_count: 至少有 1 个关键词排名的页面数量。
        country: 数据所属国家/市场。
        protocol: 协议范围（http / https / both）。
    """

    domain_rating: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="域名评级 (DR)，不提供该指标的 Provider 返回 null",
    )
    ahrefs_rank: int | None = Field(default=None, description="Ahrefs 全球排名")
    organic_traffic_estimate: int = Field(ge=0, description="自然流量估算（月均）")
    traffic_value_estimate: float | None = Field(default=None, description="流量价值估算")
    organic_keywords_count: int = Field(ge=0, description="自然关键词数量")
    top_3_count: int = Field(default=0, ge=0, description="Top 3 关键词数")
    top_10_count: int = Field(default=0, ge=0, description="Top 10 关键词数")
    ranked_pages_count: int | None = Field(
        default=None,
        ge=0,
        description="有排名页面数，不提供该指标的 Provider 返回 null",
    )
    country: Country | None = None
    protocol: str = "https"


# ============================================================
# Top Pages
# ============================================================

class TopPage(BaseEvidence):
    """Top Pages 榜单单条记录。

    Attributes:
        url: 页面 URL。
        title: 页面标题。
        organic_traffic_estimate: 该页面自然流量估算。
        traffic_change: 流量变化百分比（与对比周期比较）。
        keywords_count: 该页面进入 Top 100 的关键词数。
        top_keyword: 排名最高的关键词。
        backlinks_count: 外链总数。
        refdomains_count: 引用域名数。
    """

    url: str
    title: str | None = None
    organic_traffic_estimate: int = Field(ge=0)
    traffic_change: float | None = Field(default=None, description="流量变化比例")
    keywords_count: int = Field(ge=0)
    top_keyword: str | None = None
    backlinks_count: int = Field(default=0, ge=0)
    refdomains_count: int = Field(default=0, ge=0)


# ============================================================
# 自然关键词
# ============================================================

class OrganicKeyword(BaseEvidence):
    """自然关键词单条记录。

    Attributes:
        keyword: 关键词文本。
        position: 当前排名（1-100，100+ 表示未进入前 100）。
        previous_position: 上一周期排名。
        position_change: 排名变化（正数=上升，负数=下降）。
        search_volume: 月均搜索量（Ahrefs 估算）。
        keyword_difficulty: 关键词难度（0-100）。
        cpc: 关键词点击单价。
        traffic_estimate: 该关键词带来的估算流量。
        url: 排名的目标 URL。
        intent: 搜索意图（informational / navigational / commercial / transactional）。
        serp_features: SERP 特征列表（featured_snippet / sitelinks / video 等）。
    """

    keyword: str
    position: int | None = Field(default=None, ge=1, description="当前排名，缺失表示未获得排名")
    previous_position: int | None = Field(default=None, description="上周期排名")
    position_change: int | None = Field(default=None, description="排名变化")
    search_volume: int = Field(ge=0, description="月均搜索量")
    keyword_difficulty: int | None = Field(
        default=None, ge=0, le=100, description="关键词难度，缺失表示未知"
    )
    cpc: float | None = Field(default=None, ge=0, description="关键词点击单价，缺失表示未知")
    traffic_estimate: int | None = Field(
        default=None, ge=0, description="估算流量，缺失表示未知"
    )
    url: str | None = None
    intent: str | None = None  # informational / navigational / commercial / transactional
    serp_features: list[str] = Field(default_factory=list, description="SERP 特征")


# ============================================================
# 搜索历史趋势
# ============================================================

class SearchHistoryPoint(BaseEvidence):
    """搜索表现历史趋势数据点。

    Attributes:
        date: 数据日期。
        organic_traffic_estimate: 当日点位的自然流量估算。
        organic_keywords_count: 当日点位的自然关键词数量。
        top_3_count: 当日 Top 3 关键词数。
    """

    date: date
    organic_traffic_estimate: int = Field(ge=0)
    organic_keywords_count: int = Field(ge=0)
    top_3_count: int = Field(default=0, ge=0)


# ============================================================
# 聚合数据类型（Facade 组装多接口返回）
# ============================================================

class SearchPerformanceReport(BaseEvidence):
    """搜索表现聚合报告 — Facade 编排多接口后的汇总结果。

    可用于搜索下降诊断的初始数据视图。

    Attributes:
        metrics: 站点总体指标。
        top_pages: Top Pages 列表（默认 Top 10）。
        history: 历史趋势线（默认 6 个月）。
    """

    metrics: SearchMetrics
    top_pages: list[TopPage] = Field(default_factory=list)
    history: list[SearchHistoryPoint] = Field(default_factory=list)
