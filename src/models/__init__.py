"""
统一数据模型层。

所有模型继承自 ``src.models.common.BaseEvidence``，
强制标注数据来源（source）、获取时间（fetched_at）和是否估算（is_estimated）。

上层调用方（Agent / Facade）通过这三个字段判断数据可信度，
不依赖 Provider 实现细节。
"""

from src.models.audit import (
    AuditHealth,
    AuditIssue,
    CrawlComparison,
    PageContent,
    PageExplorerItem,
)
from src.models.backlink import AnchorStat, Backlink, BrokenBacklink, RefDomain
from src.models.common import (
    BaseEvidence,
    Capability,
    Country,
    DataIntegrity,
    DataSource,
    DateRange,
    Device,
    EvidenceConfidence,
    QueryMeta,
)
from src.models.competitor import (
    BatchAnalysisResult,
    CompetitorKeywordGap,
    CompetitorPageGap,
    OrganicCompetitor,
)
from src.models.keyword import (
    KeywordCluster,
    KeywordOverview,
    KeywordSuggestion,
    SerpResult,
)
from src.models.management import (
    AhrefsProject,
    ApiUsageRecord,
    SiteProfile,
    SubscriptionInfo,
)
from src.models.page_metadata import PageMetadataReport
from src.models.performance import (
    PagePerformanceIssue,
    PagePerformanceMetric,
    PagePerformanceReport,
)
from src.models.rank import RankedKeyword, RankHistoryPoint, RankOverview
from src.models.search import (
    OrganicKeyword,
    SearchHistoryPoint,
    SearchMetrics,
    SearchPerformanceReport,
    TopPage,
)

__all__ = [
    # Common
    "BaseEvidence",
    "Capability",
    "Country",
    "DataIntegrity",
    "DataSource",
    "DateRange",
    "Device",
    "EvidenceConfidence",
    "QueryMeta",
    # Search
    "OrganicKeyword",
    "SearchHistoryPoint",
    "SearchMetrics",
    "SearchPerformanceReport",
    "TopPage",
    # Audit
    "AuditHealth",
    "AuditIssue",
    "CrawlComparison",
    "PageContent",
    "PageExplorerItem",
    # Keyword
    "KeywordCluster",
    "KeywordOverview",
    "KeywordSuggestion",
    "SerpResult",
    # Rank
    "RankedKeyword",
    "RankHistoryPoint",
    "RankOverview",
    # Backlink
    "AnchorStat",
    "Backlink",
    "BrokenBacklink",
    "RefDomain",
    # Competitor
    "BatchAnalysisResult",
    "CompetitorKeywordGap",
    "CompetitorPageGap",
    "OrganicCompetitor",
    # Page metadata
    "PageMetadataReport",
    # Page performance
    "PagePerformanceIssue",
    "PagePerformanceMetric",
    "PagePerformanceReport",
    # Management
    "AhrefsProject",
    "ApiUsageRecord",
    "SiteProfile",
    "SubscriptionInfo",
]
