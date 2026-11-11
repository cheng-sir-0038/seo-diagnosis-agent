"""
站点管理与订阅域数据模型 — Management + Subscription Info 对应

核心模型:
  - SiteProfile: 站点档案（Agent 长期使用的上下文）
  - AhrefsProject: Ahrefs 项目绑定信息
  - SubscriptionInfo: 订阅/额度信息
  - ApiUsageRecord: API 用量记录

状态枚举:
  - AuthStatus: 授权状态
  - SubscriptionTier: 订阅级别（用于 TierRouter）

数据来源: Ahrefs Management API + Subscription Info API
"""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import Field

from src.models.common import BaseEvidence, Country

# ============================================================
# 状态枚举
# ============================================================

class AuthStatus(enum.StrEnum):
    """授权状态（平台无关）。

    各 Provider 通过调用各自的健康检查端点判断，映射为统一状态。
    """
    VALID = "valid"                   # 有效
    INVALID = "invalid"               # 无效
    EXPIRED = "expired"               # 已过期
    INSUFFICIENT = "insufficient"     # 权限不足
    UNKNOWN = "unknown"


class SubscriptionTier(enum.StrEnum):
    """订阅级别（平台无关）。

    用于 TierRouter 选择最优 Provider。
    各厂商的套餐名不同，Provider 层负责映射。
    """
    FREE = "free"
    BASIC = "basic"
    PRO = "pro"
    ENTERPRISE = "enterprise"
    UNKNOWN = "unknown"


class DataDomainStatus(enum.StrEnum):
    """数据域可用状态（平台无关）。

    用于站点档案和各数据域状态检查。
    """
    AVAILABLE = "available"                   # 可用
    UNAVAILABLE = "unavailable"               # 不可用（未接入）
    WAITING = "waiting"                       # 等待首次数据
    PERMISSION_DENIED = "permission_denied"   # 权限不足
    QUOTA_EXHAUSTED = "quota_exhausted"       # 额度耗尽


# ============================================================
# 数据模型
# ============================================================


class AhrefsProject(BaseEvidence):
    """Ahrefs 项目绑定信息。

    Attributes:
        project_id: Ahrefs 项目 ID。
        project_name: 项目名称。
        target_domain: 目标域名。
        target_mode: 目标模式: subdomain / domain / domain_with_subdomains / url。
        protocol: 协议: http / https / both。
        is_verified: 所有权是否已验证。
        created_at: 项目创建时间。
        status: 项目状态: active / paused / archived。
    """

    project_id: str
    project_name: str
    target_domain: str
    target_mode: str = Field(description="subdomain / domain / domain_with_subdomains / url")
    protocol: str = "https"
    is_verified: bool = False
    created_at: str | None = None
    status: str = "active"


class SiteProfile(BaseEvidence):
    """站点档案 — 最小站点档案（对齐 PRD 10.4 节）。

    用于 Agent 长期使用的业务和分析上下文。
    切换站点时不得复用上一站点的对话上下文和证据。

    Attributes:
        site_id: 内部站点 ID。
        site_name: 站点名称。
        primary_domain: 主域名。
        target_mode: 目标范围模式。
        default_country: 默认国家。
        default_language: 默认语言。
        default_device: 默认设备。
        timezone: 站点时区。
        brand_terms: 品牌词及常见拼写。
        business_description: 主要业务/产品的一句话说明。
        focus_pages: 重点页面或目录列表。
        business_competitors: 业务竞争对手域名列表。
        allow_agent_writes: 是否允许 Agent 建议写入关键词/竞争对手/自动化。
        ahrefs_project: 绑定的 Ahrefs 项目（可选）。
        data_domain_status: 各数据域可用状态 {audit: "available", search: "available", ...}。
    """

    site_id: str
    site_name: str
    primary_domain: str
    target_mode: str = "domain"
    default_country: Country | None = None
    default_language: str | None = None
    default_device: str = "desktop"
    timezone: str = "UTC"
    brand_terms: list[str] = Field(default_factory=list)
    business_description: str | None = None
    focus_pages: list[str] = Field(default_factory=list)
    business_competitors: list[str] = Field(default_factory=list)
    allow_agent_writes: bool = False
    ahrefs_project: AhrefsProject | None = None
    data_domain_status: dict[str, str] = Field(
        default_factory=dict,
        description="各数据域可用状态: available / unavailable / waiting / permission_denied",
    )


class SubscriptionInfo(BaseEvidence):
    """订阅与额度信息。

    对齐 PRD 第 22.1 节管理员用量看板。

    Attributes:
        plan_name: 套餐名称。
        api_units_limit: 本周期 API Units 总额度。
        api_units_used: 已使用额度。
        api_units_remaining: 剩余额度。
        usage_percentage: 使用比例（百分比）。
        reset_date: 额度重置日期。
        api_key_expiry: API Key 过期时间。
        rate_limit_per_minute: 每分钟速率限制。
        workspace_name: 工作区名称。
    """

    plan_name: str | None = None
    api_units_limit: int = 0
    api_units_used: int = 0
    api_units_remaining: int = 0
    usage_percentage: float = 0
    reset_date: str | None = None
    api_key_expiry: str | None = None
    rate_limit_per_minute: int = 60
    workspace_name: str | None = None

    # ---- 便捷属性 ----
    @property
    def is_warning(self) -> bool:
        """是否触发额度预警（剩余 < 20%）。"""
        return self.usage_percentage > 80

    @property
    def is_critical(self) -> bool:
        """是否触发严重预警（剩余 < 5%）。"""
        return self.usage_percentage > 95

    @property
    def is_exhausted(self) -> bool:
        """额度是否已耗尽。"""
        return self.api_units_remaining <= 0


class ApiUsageRecord(BaseEvidence):
    """API 调用用量记录（审计用）。

    Attributes:
        task_id: 关联任务 ID（可选）。
        capability: 调用的业务能力。
        endpoint: 实际 API 端点。
        units_consumed: 消耗的 API Units。
        cached: 是否命中缓存（缓存命中 = 0 units）。
        success: 是否成功。
        timestamp: 调用时间。
    """

    task_id: str | None = None
    capability: str
    endpoint: str
    units_consumed: int = 0
    cached: bool = False
    success: bool = True
    timestamp: datetime = Field(default_factory=datetime.utcnow)
