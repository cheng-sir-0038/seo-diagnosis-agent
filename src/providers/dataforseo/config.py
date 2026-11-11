"""
DataForSEO Provider 配置 + 端点映射表

从全局 Settings 继承，附加 DataForSEO 特定的端点映射。
认证方式: HTTP Basic Auth (login:password → Base64)。

真实实现替换指引:
  1. 在 .env.local 填入 DATAFORSEO_LOGIN 和 DATAFORSEO_API_KEY
  2. 端点路径需与 DataForSEO 官方文档对齐
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.config import settings


@dataclass
class DataForSEOConfig:
    """DataForSEO Provider 配置。

    Attributes:
        api_base_url: API 基础 URL。
        login: API 登录名（邮箱）。
        api_key: API 密钥。
        rate_limit_rpm: 每分钟最大请求数。
        request_timeout: 单次请求超时（秒）。
        max_retries: 最大重试次数。
        retry_backoff_base: 重试退避基础时间（秒）。
        task_poll_interval: 异步任务轮询间隔（秒）。
        task_poll_max_attempts: 异步任务最大轮询次数。
    """

    api_base_url: str = field(
        default_factory=lambda: getattr(settings, "dataforseo_api_base_url", "https://api.dataforseo.com")
    )
    login: str = field(
        default_factory=lambda: getattr(settings, "dataforseo_login", "")
    )
    api_key: str = field(
        default_factory=lambda: getattr(settings, "dataforseo_api_key", "")
    )

    # 速率控制
    rate_limit_rpm: int = 30  # DataForSEO 默认 30 req/min
    request_timeout: int = 60  # 部分端点响应较慢

    # 重试策略
    max_retries: int = 3
    retry_backoff_base: float = 2.0
    retry_on_statuses: set[int] = field(default_factory=lambda: {429, 502, 503, 504})

    # 异步任务轮询
    task_poll_interval: float = 2.0  # 每 2 秒轮询一次
    task_poll_max_attempts: int = 30  # 最多轮询 30 次（60 秒）


# ============================================================
# DataForSEO API 端点路由表
# ============================================================

@dataclass
class DataForSEOEndpoint:
    """单个 API 端点的定义。

    所有 DataForSEO v3 端点为 POST，body 为 JSON array of task objects。

    Attributes:
        path: API 路径（相对于 api_base_url/v3/）。
        description: 端点简短描述。
        is_live: 是否为 live 端点（同步返回结果，无需轮询）。
        estimated_cost: 估计单次调用消耗（美元）。
    """
    path: str = ""
    description: str = ""
    is_live: bool = True  # live 端点直接返回结果
    estimated_cost: float = 0.01


ENDPOINTS: dict[str, DataForSEOEndpoint] = {
    # ============================================================
    # DataForSEO Labs — 域名与竞争分析
    # ============================================================

    "competitors_domain": DataForSEOEndpoint(
        path="/v3/dataforseo_labs/google/competitors_domain/live",
        description="自然搜索竞争对手及关键词交集",
        is_live=True,
        estimated_cost=0.02,
    ),
    "domain_rank_overview": DataForSEOEndpoint(
        path="/v3/dataforseo_labs/google/domain_rank_overview/live",
        description="当前域名的自然搜索指标与排名分布",
        is_live=True,
    ),
    "domain_intersection": DataForSEOEndpoint(
        path="/v3/dataforseo_labs/google/domain_intersection/live",
        description="域名关键词交集：共同排名关键词、竞争差距",
        is_live=True,
        estimated_cost=0.02,
    ),
    "serp_competitors": DataForSEOEndpoint(
        path="/v3/dataforseo_labs/google/serp_competitors/live",
        description="SERP 竞争对手：指定关键词的 SERP 竞争者列表",
        is_live=True,
        estimated_cost=0.01,
    ),
    "ranked_keywords": DataForSEOEndpoint(
        path="/v3/dataforseo_labs/google/ranked_keywords/live",
        description="域名排名关键词：完整关键词列表 + 排名 + 搜索量 + 流量估算 → 替代 Ahrefs organic_keywords",
        is_live=True,
        estimated_cost=0.02,
    ),

    # ============================================================
    # SERP API — 搜索引擎结果页
    # ============================================================

    "serp_google_organic": DataForSEOEndpoint(
        path="/v3/serp/google/organic/live/advanced",
        description="Google SERP 实时数据：排名、SERP 特征、结果类型 → 替代 Ahrefs serp_overview",
        is_live=True,
        estimated_cost=0.01,
    ),

    # ============================================================
    # DataForSEO Labs — 关键词指标
    # ============================================================

    "keyword_overview": DataForSEOEndpoint(
        path="/v3/dataforseo_labs/google/keyword_overview/live",
        description="核对指定关键词的搜索量、难度、CPC 和意图",
        is_live=True,
        estimated_cost=0.01,
    ),

    # ============================================================
    # Backlinks API — 外链数据
    # ============================================================

    "backlinks_summary": DataForSEOEndpoint(
        path="/v3/backlinks/summary/live",
        description="外链概览：总量、dofollow/nofollow、引用域名数",
        is_live=True,
        estimated_cost=0.01,
    ),
    "backlinks_list": DataForSEOEndpoint(
        path="/v3/backlinks/backlinks/live",
        description="外链列表：来源 URL、锚文本、属性、首次/最近发现 → 替代 Ahrefs backlinks_list",
        is_live=True,
        estimated_cost=0.02,
    ),
    "bulk_referring_domains": DataForSEOEndpoint(
        path="/v3/backlinks/bulk_referring_domains/live",
        description="批量引用域名：多目标的引用域名统计 → 替代 Ahrefs refdomains",
        is_live=True,
        estimated_cost=0.01,
    ),
    "backlinks_timeseries": DataForSEOEndpoint(
        path="/v3/backlinks/timeseries_new_lost_summary/live",
        description="外链时间序列：新增/丢失外链趋势",
        is_live=True,
        estimated_cost=0.01,
    ),

    # ============================================================
    # Domain Analytics — 域名分析（非 live，需轮询）
    # ============================================================

    "domain_analytics": DataForSEOEndpoint(
        path="/v3/domain_analytics/technologies/domain_technologies/live",
        description="域名技术栈检测（辅助审计）",
        is_live=True,
        estimated_cost=0.01,
    ),

    # ============================================================
    # Appendix — 辅助端点
    # ============================================================

    "locations_and_languages": DataForSEOEndpoint(
        path="/v3/dataforseo_labs/locations_and_languages",
        description="获取可用位置和语言列表（免费、用于参数映射）",
        is_live=False,
        estimated_cost=0.0,
    ),
}
