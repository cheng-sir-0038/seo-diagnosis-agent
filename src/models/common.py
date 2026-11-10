"""
统一数据模型 — 公共基类与枚举

所有数据模型继承 ``BaseEvidence``，强制标注数据来源、获取时间和估算标记。
上层调用方（Agent / Facade）通过这三个字段判断数据可信度，不依赖 Provider 实现细节。

使用方式::

    from src.models.common import DataSource, BaseEvidence, DateRange

    class SearchMetrics(BaseEvidence):
        domain_rating: float
        organic_traffic_estimate: int
"""

from __future__ import annotations

import enum
from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

# ============================================================
# 枚举
# ============================================================

class DataSource(str, enum.Enum):
    """数据来源枚举。

    每个枚举值对应一个已接入的数据平台。
    后续新增数据源时在此添加枚举值，不影响已有模型。
    """
    AHREFS = "ahrefs"
    DATAFORSEO = "dataforseo"        # 外部竞争全景
    SERPER = "serper"                # 实时 Google SERP
    GSC = "google_search_console"    # 自有站点搜索表现
    BING = "bing_webmaster_tools"     # 预留
    GA4 = "google_analytics_4"        # 站内行为+转化
    SEMRUSH = "semrush"               # 预留
    PAGESPEED = "pagespeed"             # Lighthouse 实验室页面性能
    PAGE_HTML = "page_html"               # 站点页面源 HTML 元信息


class Capability(str, enum.Enum):
    """业务能力枚举 — Facade 统一入口的路由 Key。

    每个值对应一个业务查询能力，调用方只需指定 capability，
    Facade 自动路由到支持该能力的 Provider。

    命名规则: ``{domain}_{scope}``
    """
    # ---- 站点管理 ----
    SITE_PROFILE = "site_profile"
    SUBSCRIPTION_INFO = "subscription_info"

    # ---- 搜索表现 (Site Explorer) ----
    SEARCH_METRICS = "search_metrics"
    SEARCH_HISTORY = "search_history"
    TOP_PAGES = "top_pages"
    ORGANIC_KEYWORDS = "organic_keywords"
    BEST_PAGES_BY_LINKS = "best_pages_by_links"

    # ---- 技术审计 (Site Audit) ----
    AUDIT_HEALTH = "audit_health"
    AUDIT_ISSUES = "audit_issues"
    AUDIT_PAGE_EXPLORER = "audit_page_explorer"
    AUDIT_PAGE_CONTENT = "audit_page_content"
    PAGE_PERFORMANCE = "page_performance"
    PAGE_METADATA = "page_metadata"

    # ---- 关键词研究 (Keywords Explorer + SERP) ----
    KEYWORD_OVERVIEW = "keyword_overview"
    KEYWORD_SUGGESTIONS = "keyword_suggestions"
    AUTOCOMPLETE = "autocomplete"
    NEWS_SEARCH = "news_search"
    IMAGE_SEARCH = "image_search"
    VIDEO_SEARCH = "video_search"
    SERP_OVERVIEW = "serp_overview"

    # ---- 排名跟踪 (Rank Tracker) ----
    RANK_OVERVIEW = "rank_overview"
    RANK_DETAIL = "rank_detail"
    RANK_HISTORY = "rank_history"

    # ---- 外链 (Site Explorer Backlinks) ----
    BACKLINKS_LIST = "backlinks_list"
    BROKEN_BACKLINKS = "broken_backlinks"
    REFDOMAINS = "refdomains"
    ANCHORS = "anchors"

    # ---- 竞争分析 (Site Explorer + Batch) ----
    ORGANIC_COMPETITORS = "organic_competitors"
    COMPETITOR_KEYWORD_GAP = "competitor_keyword_gap"
    COMPETITOR_PAGE_GAP = "competitor_page_gap"
    COMPETITOR_BACKLINK_GAP = "competitor_backlink_gap"
    BATCH_ANALYSIS = "batch_analysis"


class EvidenceConfidence(str, enum.Enum):
    """证据可信度等级。

    对齐 PRD 第 7.2 节定义:
      - HIGH: 数据直接、新鲜、多个方向一致、无关键冲突。
      - MEDIUM: 有数据但含推断、部分缺失或仍存在合理解释。
      - LOW: 数据旧、样本小、关键字段缺失或证据冲突。
    """
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ChangeDirection(str, enum.Enum):
    """指标变化方向 — 所有时间对比查询的统一表达。

    用于搜索表现、排名、外链、流量等任何指标的对比结果。
    """
    INCREASED = "increased"       # 上升
    DECREASED = "decreased"       # 下降
    UNCHANGED = "unchanged"       # 基本不变
    NEW = "new"                   # 新出现
    REMOVED = "removed"           # 已消失
    UNKNOWN = "unknown"           # 无法判断


class TaskStatus(str, enum.Enum):
    """Agent 任务状态 — 对齐 PRD 第 13.2 节。

    贯穿任务全生命周期。
    """
    DRAFT = "draft"                       # 草稿
    PLANNING = "planning"                 # 规划中
    INVESTIGATING = "investigating"       # 调查中
    WAITING_FOR_DATA = "waiting_for_data"          # 等待 Ahrefs 数据
    WAITING_FOR_USER = "waiting_for_user"          # 等待用户补充范围
    WAITING_FOR_APPROVAL = "waiting_for_approval"  # 等待审批
    PARTIAL = "partial"                   # 部分完成
    COMPLETED = "completed"               # 已完成
    MONITORING = "monitoring"             # 持续监控中
    FAILED = "failed"                     # 失败
    CANCELLED = "cancelled"               # 已取消


class HypothesisStatus(str, enum.Enum):
    """假设验证状态 — 对齐 PRD 第 7.1 节。

    Agent 调查过程中对每个假设的验证结论。
    """
    PENDING = "pending"                   # 待验证
    SUPPORTED = "supported"               # 已支持
    PARTIALLY_SUPPORTED = "partially_supported"  # 部分支持
    EXCLUDED = "excluded"                 # 已排除
    INSUFFICIENT_DATA = "insufficient_data"     # 数据不足
    NOT_RELEVANT = "not_relevant"         # 暂不相关


class ActionStatus(str, enum.Enum):
    """行动状态 — 对齐 PRD 第 15.3 节。

    从建议到验证的完整生命周期。
    """
    SUGGESTED = "suggested"                       # 建议中
    APPROVED = "approved"                         # 已批准
    PENDING = "pending"                           # 待开始
    IN_PROGRESS = "in_progress"                   # 处理中
    AWAITING_VERIFICATION = "awaiting_verification"  # 待验证
    VERIFIED_EFFECTIVE = "verified_effective"     # 已验证有效
    VERIFIED_PARTIAL = "verified_partial"         # 部分有效
    VERIFIED_FAILED = "verified_failed"           # 验证未通过
    UNVERIFIABLE = "unverifiable"                 # 无法验证
    IGNORED = "ignored"                           # 已忽略
    REOPENED = "reopened"                         # 已重新打开


class OpportunityStatus(str, enum.Enum):
    """增长机会状态 — 对齐 PRD 第 14.2 节。

    从发现到归档的机会生命周期。
    """
    NEW = "new"                   # 新发现
    EVALUATED = "evaluated"       # 已评估
    RECOMMENDED = "recommended"   # 建议行动
    APPROVED = "approved"         # 已批准
    IN_PROGRESS = "in_progress"   # 执行中
    OBSERVING = "observing"       # 持续观察
    DEFERRED = "deferred"         # 暂缓
    ARCHIVED = "archived"         # 已归档


class DataIntegrity(str, enum.Enum):
    """数据完整性标签 — 对齐 PRD 第 20.5 节。

    用于标注单次查询返回数据的完整程度。
    """
    COMPLETE = "complete"           # 完整
    PARTIAL = "partial"             # 部分（Top N / 部分国家）
    STALE = "stale"                 # 旧数据（超过默认新鲜度）
    ESTIMATED = "estimated"         # 估算值（非第一方实际值）
    PERMISSION_LIMITED = "permission_limited"   # 权限受限
    QUOTA_LIMITED = "quota_limited"             # 额度受限缩小了范围
    NOT_AVAILABLE = "not_available"             # 已查询但无结果
    NOT_QUERIED = "not_queried"                 # 计划中跳过，尚未执行


# ============================================================
# 公共类型
# ============================================================

class DateRange(BaseModel):
    """日期范围。

    所有时间对比查询统一使用此结构。

    Attributes:
        start: 起始日期。
        end: 结束日期。
        label: 人类可读标签，如 "最近 28 天"。
    """
    start: date
    end: date
    label: str | None = None


class Country(BaseModel):
    """国家/市场。

    Attributes:
        code: ISO 3166-1 alpha-2 国家代码，如 "us"、"cn"。
        name: 中文名称，如 "美国"。
    """
    code: str
    name: str | None = None


class Device(str, enum.Enum):
    """设备类型。"""
    DESKTOP = "desktop"
    MOBILE = "mobile"


# ============================================================
# 根基类 — 所有数据模型的父类
# ============================================================

class BaseEvidence(BaseModel):
    """所有统一数据模型的根基类。

    强制要求每个数据对象标注:
      - source: 数据来自哪个平台（Ahrefs / GSC / Bing ...）
      - fetched_at: 数据实际获取时间（UTC）
      - data_date: 数据对应的业务日期范围
      - is_estimated: 该数据是否为平台估算值
      - integrity: 数据完整性标签

    Agent 层 / Facade 层根据这些字段自动判断可信度，
    不需要理解 Provider 的实现细节。

    使用示例::

        class SearchMetrics(BaseEvidence):
            domain_rating: float
            organic_traffic_estimate: int = Field(..., description="Ahrefs 自然流量估算")
    """

    source: DataSource = Field(..., description="数据来源平台")
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="数据获取时间（UTC）",
    )
    data_date: DateRange | date | None = Field(
        default=None,
        description="数据对应的业务日期",
    )
    is_estimated: bool = Field(
        default=True,
        description="是否为估算值。Ahrefs 自然流量/搜索量等均为估算",
    )
    integrity: DataIntegrity = Field(
        default=DataIntegrity.COMPLETE,
        description="数据完整性标签",
    )

    confidence: EvidenceConfidence | None = Field(
        default=None,
        description="可信度等级（由 Facade/Agent 计算，Provider 不填）",
    )

    # ============================================================
    # 便捷方法
    # ============================================================

    def mark_confidence(self, confidence: EvidenceConfidence) -> None:
        """设置可信度等级。

        Facade 在组装数据后调用此方法，Provider 不负责计算可信度。
        """
        self.confidence = confidence

    def to_dict(self) -> dict[str, Any]:
        """转换为普通字典（用于序列化缓存）。

        Returns:
            包含所有字段的 dict。
        """
        return self.model_dump(mode="json")


# ============================================================
# 通用响应包装
# ============================================================

class QueryMeta(BaseModel):
    """查询元信息 — 每次 API 响应都附带。

    Attributes:
        capability: 本次查询的业务能力。
        source: 实际响应的数据源。
        params_hash: 请求参数的哈希值（用于缓存匹配）。
        cached: 是否来自缓存。
        units_consumed: 本次查询估计消耗的 API Units。
    """
    capability: Capability
    source: DataSource
    params_hash: str
    cached: bool = False
    units_consumed: int = 0
