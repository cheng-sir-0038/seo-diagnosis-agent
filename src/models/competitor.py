"""
竞争分析域数据模型 — Organic Competitors + Batch Analysis 对应

核心模型:
  - OrganicCompetitor: 自然搜索竞争对手
  - CompetitorKeywordGap: 关键词差距条目
  - CompetitorPageGap: 页面差距条目
  - BatchAnalysisResult: 批量分析结果

数据来源: Ahrefs Site Explorer + Batch Analysis API
    - GET /site-explorer/organic-competitors
    - POST /batch-analysis
"""

from __future__ import annotations

from pydantic import Field

from src.models.common import BaseEvidence


class OrganicCompetitor(BaseEvidence):
    """自然搜索竞争对手。

    自动发现 vs 用户关注分开标记。

    Attributes:
        domain: 竞争对手域名。
        is_user_tracked: 是否为用户手动关注的竞争对手。
        common_keywords_count: 共同关键词数。
        competitor_unique_keywords: 对手独有关键词数。
        our_unique_keywords: 本方独有关键词数。
        overlap_percentage: 关键词重合度（百分比）。
        domain_rating: 对手 DR。
        organic_traffic_estimate: 对手自然流量估算。
        ranked_pages_count: 对手排名页面数。
    """

    domain: str
    is_user_tracked: bool = False
    common_keywords_count: int = 0
    competitor_unique_keywords: int = 0
    our_unique_keywords: int = 0
    overlap_percentage: float | None = None
    domain_rating: float | None = None
    organic_traffic_estimate: int | None = None
    ranked_pages_count: int | None = None


class CompetitorKeywordGap(BaseEvidence):
    """关键词差距单条记录。

    对手有排名但本方没有（或排名更低）的关键词。

    Attributes:
        keyword: 关键词。
        competitor_rank: 对手排名。
        our_rank: 本方排名（None = 未进入前 100）。
        search_volume: 月均搜索量。
        keyword_difficulty: 难度。
        competitor_url: 对手排名 URL。
        business_relevance: 业务相关度评估: high / medium / low / not_relevant。
        recommended_action: 推荐动作: target / observe / ignore。
    """

    keyword: str
    competitor_domain: str
    competitor_rank: int
    our_rank: int | None = None
    search_volume: int = 0
    keyword_difficulty: float | None = None
    competitor_url: str | None = None
    business_relevance: str | None = Field(
        default=None, description="high / medium / low / not_relevant"
    )
    recommended_action: str | None = None


class CompetitorPageGap(BaseEvidence):
    """页面差距单条记录。

    竞争对手增长/表现优异的页面。

    Attributes:
        competitor_domain: 竞争对手域名。
        url: 页面 URL。
        title: 页面标题。
        page_type: 页面类型（blog / product / category / landing / other）。
        organic_traffic_estimate: 页面自然流量估算。
        traffic_change: 流量变化。
        primary_keywords: 主要关键词。
    """

    competitor_domain: str
    url: str
    title: str | None = None
    page_type: str | None = None
    organic_traffic_estimate: int = 0
    traffic_change: float | None = None
    primary_keywords: list[str] = Field(default_factory=list)


class BatchAnalysisResult(BaseEvidence):
    """批量域名分析结果。

    Attributes:
        target_domain: 分析目标域名。
        domain_rating: DR。
        ahrefs_rank: Ahrefs Rank。
        organic_traffic_estimate: 自然流量估算。
        organic_keywords_count: 自然关键词数。
        backlinks_count: 外链总数。
        refdomains_count: 引用域名数。
    """

    target_domain: str
    domain_rating: float | None = None
    ahrefs_rank: int | None = None
    organic_traffic_estimate: int = 0
    organic_keywords_count: int = 0
    backlinks_count: int = 0
    refdomains_count: int = 0
