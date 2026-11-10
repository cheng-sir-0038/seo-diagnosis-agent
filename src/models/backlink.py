"""
外链域数据模型 — Site Explorer Backlinks 对应

核心模型:
  - Backlink: 单条外链记录
  - RefDomain: 引用域名
  - BrokenBacklink: 失效外链
  - Anchor: 锚文本统计

状态枚举:
  - BacklinkStatus: 外链生命周期状态
  - BacklinkAttribute: 外链属性类型

数据来源: Ahrefs Site Explorer Backlinks API / DataForSEO Backlinks API

产品规则（对齐 PRD）:
  - nofollow 不自动判定为无价值
  - DR 仅作为一个价值信号，需结合主题相关性和目标页面价值
  - 相似链接默认合并
  - 同一域名大量站点级链接默认合并避免夸大影响
"""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import Field

from src.models.common import BaseEvidence


# ============================================================
# 状态枚举
# ============================================================

class BacklinkStatus(str, enum.Enum):
    """外链生命周期状态（平台无关）。

    各厂商映射:
      - DataForSEO: is_lost/is_broken/is_redirect → 统一状态
      - Ahrefs: status 字段 → 统一状态
    """
    LIVE = "live"           # 存活
    LOST = "lost"           # 已丢失
    BROKEN = "broken"       # 已失效（目标页 4xx/5xx）
    REDIRECT = "redirect"   # 已重定向


class BacklinkAttribute(str, enum.Enum):
    """外链属性类型（平台无关）。

    对应 rel 属性值。
    """
    DOFOLLOW = "dofollow"
    NOFOLLOW = "nofollow"
    UGC = "ugc"
    SPONSORED = "sponsored"


# ============================================================
# 数据模型
# ============================================================


class Backlink(BaseEvidence):
    """单条外链记录。

    Attributes:
        source_url: 来源页面 URL。
        source_title: 来源页面标题。
        target_url: 目标页面 URL。
        anchor_text: 锚文本。
        link_type: 链接类型: dofollow / nofollow / sponsored / ugc。
        domain_rating: 来源域名的 DR。
        url_rating: 来源页面的 UR。
        source_traffic_estimate: 来源页面自然流量估算。
        first_seen: 首次发现日期。
        last_seen: 最近发现日期。
        is_lost: 是否已丢失。
        lost_reason: 丢失原因（如 4xx、noindex、redirect）。
    """

    source_url: str
    source_title: str | None = None
    target_url: str
    anchor_text: str | None = None
    link_type: str = Field(default="dofollow", description="dofollow / nofollow / sponsored / ugc")
    source_domain_rating: float | None = None
    source_url_rating: float | None = None
    source_traffic_estimate: int | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    is_lost: bool = False
    lost_reason: str | None = None


class RefDomain(BaseEvidence):
    """引用域名条目。

    Attributes:
        domain: 引用域名。
        domain_rating: 域名 DR。
        backlinks_count: 来自该域名的外链总数。
        dofollow_count: dofollow 外链数。
        first_seen: 首次发现。
        last_seen: 最近发现。
        is_lost: 整个域名的外链是否全部丢失。
        primary_target_url: 该域名链接最多的目标页面。
        organic_traffic_estimate: 来源域名的自然流量估算。
    """

    domain: str
    domain_rating: float | None = None
    backlinks_count: int = 0
    dofollow_count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    is_lost: bool = False
    primary_target_url: str | None = None
    organic_traffic_estimate: int | None = None


class BrokenBacklink(BaseEvidence):
    """失效外链。

    Attributes:
        source_url: 来源页面 URL。
        target_url: 原目标页面 URL（已失效）。
        http_status: 目标页面的 HTTP 状态码（4xx/5xx）。
        redirect_url: 如果有重定向，跳转目标 URL。
        anchor_text: 锚文本。
        source_domain_rating: 来源域名 DR。
        first_seen: 首次发现。
        last_seen: 最近发现。
        suggested_action: 建议处理: 301_redirect / restore_page / contact_source / accept_loss。
    """

    source_url: str
    target_url: str
    http_status: int | None = None
    redirect_url: str | None = None
    anchor_text: str | None = None
    source_domain_rating: float | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    suggested_action: str | None = Field(
        default=None,
        description="301_redirect / restore_page / contact_source / accept_loss",
    )


class AnchorStat(BaseEvidence):
    """锚文本统计条目。

    Attributes:
        anchor_text: 锚文本。
        backlinks_count: 使用该锚文本的外链数。
        refdomains_count: 使用该锚文本的引用域名数。
        primary_target_url: 该锚文本最常指向的页面。
    """

    anchor_text: str
    backlinks_count: int = 0
    refdomains_count: int = 0
    primary_target_url: str | None = None
