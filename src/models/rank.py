"""
排名跟踪域数据模型 — Rank Tracker 对应

核心模型:
  - RankOverview: 排名项目总览
  - RankedKeyword: 单个跟踪关键词的排名详情
  - RankHistoryPoint: 排名历史数据点

状态枚举:
  - RankingTrend: 排名变化趋势
  - RankingTier: 排名区间分级

数据来源: Ahrefs Rank Tracker API / DataForSEO Rank Tracker / GSC API

产品规则（对齐 PRD）:
  - 排名下降 ≥ 10 名触发异常标记
  - 跌出 Top 3/10/100 触发关键区间丢失
  - 排名仅作为方向性证据，不单独决定 SEO 因果
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from pydantic import Field

from src.models.common import BaseEvidence, Country


# ============================================================
# 状态枚举
# ============================================================

class RankingTrend(str, enum.Enum):
    """排名变化趋势（平台无关）。

    通过比较当前排名与上次排名计算得出。
    规则:
      - 之前没排名、现在有 → NEW
      - 之前有排名、现在没 → LOST
      - 排名数字变小 → UP（实际上升）
      - 排名数字变大 → DOWN（实际下降）
      - 变化 < 1 → STABLE
    """
    UP = "up"
    DOWN = "down"
    STABLE = "stable"
    NEW = "new"
    LOST = "lost"
    UNKNOWN = "unknown"


class RankingTier(str, enum.Enum):
    """排名区间分级（平台无关）。

    用于阈值判断和优先级计算。
    """
    TOP_3 = "top_3"
    TOP_10 = "top_10"
    TOP_20 = "top_20"
    TOP_50 = "top_50"
    TOP_100 = "top_100"
    NOT_RANKING = "not_ranking"


class RankedKeywordStatus(str, enum.Enum):
    """关键词跟踪状态（平台无关）。

    各厂商映射:
      - Ahrefs: active/processing/frozen/paused/error
      - DataForSEO: 任务状态映射
    """
    ACTIVE = "active"
    PROCESSING = "processing"
    FROZEN = "frozen"
    PAUSED = "paused"
    ERROR = "error"


# ============================================================
# 数据模型
# ============================================================


class RankOverview(BaseEvidence):
    """排名跟踪项目总览。

    Attributes:
        tracked_keywords_count: 跟踪关键词总数。
        top_3_count: Top 3 关键词数。
        top_10_count: Top 10 关键词数。
        top_20_count: Top 20 关键词数。
        top_50_count: Top 50 关键词数。
        top_100_count: Top 100 关键词数。
        not_ranked_count: 未进入 Top 100 的关键词数。
        average_position: 平均排名。
        share_of_voice: Share of Voice 百分比。
        country: 跟踪国家。
    """

    tracked_keywords_count: int = 0
    top_3_count: int = 0
    top_10_count: int = 0
    top_20_count: int = 0
    top_50_count: int = 0
    top_100_count: int = 0
    not_ranked_count: int = 0
    average_position: float | None = None
    share_of_voice: float | None = None
    country: Country | None = None


class RankedKeyword(BaseEvidence):
    """单个跟踪关键词的排名详情。

    Attributes:
        keyword: 关键词文本。
        country: 目标国家。
        language: 目标语言。
        device: 设备类型（desktop / mobile）。
        location: 目标位置（如城市、邮编）。
        current_position: 当前排名（None 表示未进入 Top 100）。
        previous_position: 上次排名。
        position_change: 排名变化（正数=上升）。
        url: 当前排名 URL。
        previous_url: 上次排名 URL（不同则发生了 URL 切换）。
        status: 关键词状态: active / processing / frozen / paused / error。
        last_updated: 排名数据最后更新时间。
        serp_date: SERP 数据日期。
    """

    keyword: str
    country: Country | None = None
    language: str | None = None
    device: str = "desktop"
    location: str | None = None
    current_position: int | None = Field(default=None, description="None = 未进入前 100")
    previous_position: int | None = None
    position_change: int | None = None
    url: str | None = None
    previous_url: str | None = None
    status: str = Field(default="active", description="active / processing / frozen / paused / error")
    last_updated: datetime | None = None
    serp_date: date | None = None

    # ---- 便捷属性 ----
    @property
    def is_anomaly(self) -> bool:
        """是否触发排名异常标记。

        Returns:
            True 当排名下降 ≥ 10 名，或跌出 Top 3/10/100，或 URL 切换。
        """
        if self.position_change is not None and self.position_change <= -10:
            return True
        if (
            self.previous_position is not None
            and self.current_position is not None
        ):
            # 跌出关键区间
            for threshold in [3, 10, 100]:
                if self.previous_position <= threshold < self.current_position:
                    return True
        if self.url != self.previous_url and self.previous_url is not None:
            return True
        return False


class RankHistoryPoint(BaseEvidence):
    """排名历史数据点。

    Attributes:
        date: 数据日期。
        position: 当日排名。
        url: 当日排名 URL。
    """

    date: date
    position: int | None = None
    url: str | None = None
