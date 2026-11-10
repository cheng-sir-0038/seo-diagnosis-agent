"""
关键词研究域数据模型 — Keywords Explorer + SERP Overview 对应

核心模型:
  - KeywordOverview: 单个关键词的综合概览
  - KeywordSuggestion: 关键词建议
  - SerpResult: SERP 单条结果
  - KeywordCluster: 关键词簇

状态枚举:
  - KeywordIntent: 搜索意图类型
  - SERPFeatureType: SERP 特征类型

数据来源: Ahrefs Keywords Explorer + SERP Overview API / DataForSEO Keywords + SERP API
"""

from __future__ import annotations

import enum

from pydantic import Field

from src.models.common import BaseEvidence, Country

# ============================================================
# 状态枚举
# ============================================================

class KeywordIntent(str, enum.Enum):
    """搜索意图类型（平台无关）。

    各厂商映射:
      - DataForSEO: search_intent 字段
      - Ahrefs: intent 字段
      - GSC: 通过查询词特征推断
    """
    INFORMATIONAL = "informational"
    COMMERCIAL = "commercial"
    TRANSACTIONAL = "transactional"
    NAVIGATIONAL = "navigational"
    UNKNOWN = "unknown"


class SERPFeatureType(str, enum.Enum):
    """SERP 特征类型（平台无关）。

    用于分析 SERP 结构变化对 CTR 的影响。
    各厂商映射:
      - DataForSEO: SERP API items 中的 type 字段
      - Ahrefs: SERP Overview 中的 result type
    """
    ORGANIC = "organic"
    FEATURED_SNIPPET = "featured_snippet"
    KNOWLEDGE_PANEL = "knowledge_panel"
    LOCAL_PACK = "local_pack"
    VIDEO = "video"
    IMAGE = "image"
    PEOPLE_ALSO_ASK = "people_also_ask"
    SITE_LINKS = "site_links"
    REVIEWS = "reviews"
    TOP_STORIES = "top_stories"
    TWITTER = "twitter"
    ADS_TOP = "ads_top"
    ADS_BOTTOM = "ads_bottom"
    SHOPPING = "shopping"
    RELATED_SEARCHES = "related_searches"
    OTHER = "other"


class VolumeTrend(str, enum.Enum):
    """搜索量趋势（平台无关）。"""
    RISING = "rising"
    STABLE = "stable"
    DECLINING = "declining"
    SEASONAL = "seasonal"
    UNKNOWN = "unknown"


# ============================================================
# 数据模型
# ============================================================


class KeywordOverview(BaseEvidence):
    """单个关键词的综合概览。

    对应 Ahrefs Keywords Explorer Overview。

    Attributes:
        keyword: 关键词文本。
        country: 搜索国家。
        search_volume: 月均搜索量（Ahrefs 估算）。
        global_volume: 全球搜索量。
        keyword_difficulty: 关键词难度（0-100）。
        traffic_potential: 流量潜力估算。
        cpc: 平均每次点击成本。
        parent_topic: 父级主题关键词。
        intent: 搜索意图：informational / navigational / commercial / transactional。
        volume_trend: 搜索量趋势（rising / stable / declining / seasonal）。
        updated_at: 数据更新时间。
    """

    keyword: str
    country: Country | None = None
    search_volume: int = Field(ge=0, description="月均搜索量")
    global_volume: int | None = Field(default=None, description="全球搜索量")
    keyword_difficulty: float = Field(default=0, ge=0, le=100, description="关键词难度")
    traffic_potential: int | None = Field(default=None, description="流量潜力估算")
    cpc: float | None = Field(default=None, description="平均 CPC")
    parent_topic: str | None = Field(default=None, description="父级主题")
    intent: str | None = None
    volume_trend: str | None = Field(
        default=None,
        description="rising / stable / declining / seasonal",
    )
    updated_at: str | None = None


class KeywordSuggestion(BaseEvidence):
    """关键词建议条目。

    来源: 匹配词、相关词、搜索建议。

    Attributes:
        keyword: 建议关键词。
        source_type: 建议来源：matching / related / search_suggestions。
        search_volume: 月均搜索量。
        keyword_difficulty: 难度。
        relevance_score: 与种子词的关联度（0-1）。
        is_question: 是否为疑问句式。
    """

    keyword: str
    source_type: str = Field(description="matching / related / search_suggestions")
    search_volume: int = Field(ge=0)
    keyword_difficulty: float = Field(default=0, ge=0, le=100)
    relevance_score: float | None = Field(default=None, ge=0, le=1)
    is_question: bool = False


class SerpResult(BaseEvidence):
    """SERP 搜索结果单条记录。

    对应 Ahrefs SERP Overview Top 100 结果的一条。

    Attributes:
        rank: 排名位置（1-100）。
        type: 结果类型：organic / featured_snippet / video / image / etc。
        title: 结果标题。
        url: 结果 URL。
        description: 结果描述（snippet）。
        domain_rating: 目标域名的 DR。
        url_rating: 目标页面的 UR。
        backlinks_count: 目标页面的外链数。
        refdomains_count: 目标页面的引用域名数。
        organic_traffic_estimate: 目标页面的自然流量估算。
    """

    rank: int = Field(ge=1, le=200)
    type: str = Field(description="organic / featured_snippet / video / image / ...")
    title: str | None = None
    url: str | None = None
    description: str | None = None
    page: int | None = Field(default=None, ge=1)
    position: str | None = None
    markdown: str | None = None
    reference_urls: list[str] = Field(default_factory=list)
    asynchronous: bool | None = None
    domain_rating: float | None = None
    url_rating: float | None = None
    backlinks_count: int | None = None
    refdomains_count: int | None = None
    organic_traffic_estimate: int | None = None


class AutocompleteSuggestion(BaseEvidence):
    """Google 搜索联想词；不包含搜索量、难度或排名指标。"""

    position: int = Field(ge=1)
    value: str = Field(min_length=1)
    normalized_value: str = Field(min_length=1)


class KeywordCluster(BaseEvidence):
    """关键词簇 — 按主题/父级主题聚合的关键词组。

    用于关键词机会发现页面的展示。

    Attributes:
        topic_name: 主题/簇名称。
        representative_keyword: 代表关键词。
        keywords: 簇内关键词列表。
        total_search_volume: 簇内关键词总搜索量。
        avg_difficulty: 平均难度。
        intent: 主要搜索意图。
        recommended_action: 推荐动作: optimize / create / track / observe / ignore。
    """

    topic_name: str
    representative_keyword: str
    keywords: list[str] = Field(default_factory=list)
    total_search_volume: int = 0
    avg_difficulty: float = 0
    intent: str | None = None
    recommended_action: str | None = Field(
        default=None,
        description="optimize / create / track / observe / ignore",
    )
