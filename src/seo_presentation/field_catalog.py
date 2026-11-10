from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from src.seo_presentation.contracts import localized

DataType = Literal["string", "number", "date", "boolean", "url"]
Alignment = Literal["left", "center", "right"]


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    key: str
    label_key: str
    label: str
    data_type: DataType = "string"
    semantic_type: str = "text"
    formatter: str = "text"
    sortable: bool = True
    align: Alignment = "left"
    width: int | None = None
    help_text: str | None = None
    null_text: str = "暂无数据"
    value_map: dict[str, str] = field(default_factory=dict)
    ranges: tuple[dict[str, Any], ...] = ()

    def contract(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "key": self.key,
            "label": localized(self.label_key, self.label),
            "data_type": self.data_type,
            "semantic_type": self.semantic_type,
            "formatter": self.formatter,
            "sortable": self.sortable,
            "align": self.align,
            "null_text": self.null_text,
        }
        if self.width is not None:
            result["width"] = self.width
        if self.help_text:
            result["help_text"] = self.help_text
        if self.value_map:
            result["value_map"] = dict(self.value_map)
        if self.ranges:
            result["ranges"] = [dict(item) for item in self.ranges]
        return result


INTENT_MAP = {
    "informational": "信息查询",
    "navigational": "品牌导航",
    "commercial": "商业调研",
    "transactional": "购买转化",
    "INFORMATIONAL": "信息查询",
    "NAVIGATIONAL": "品牌导航",
    "COMMERCIAL": "商业调研",
    "TRANSACTIONAL": "购买转化",
}

DIFFICULTY_RANGES = (
    {"max": 29, "label": "较容易"},
    {"min": 30, "max": 49, "label": "中等"},
    {"min": 50, "max": 69, "label": "较难"},
    {"min": 70, "label": "非常难"},
)

FIELD_CATALOG: dict[str, FieldDefinition] = {
    "keyword": FieldDefinition("keyword", "seo.field.keyword", "关键词", width=200, sortable=True),
    "position": FieldDefinition(
        "position",
        "seo.field.position",
        "当前排名",
        data_type="number",
        semantic_type="rank",
        formatter="integer",
        align="right",
        width=110,
        null_text="暂无排名",
    ),
    "previous_position": FieldDefinition(
        "previous_position",
        "seo.field.previous_position",
        "上次排名",
        data_type="number",
        semantic_type="rank",
        formatter="integer",
        align="right",
        width=110,
        null_text="暂无历史数据",
    ),
    "position_change": FieldDefinition(
        "position_change",
        "seo.field.position_change",
        "排名变化",
        data_type="number",
        semantic_type="rank_delta",
        formatter="rank_delta",
        align="right",
        width=130,
        help_text="正数表示排名上升，负数表示排名下降。",
        null_text="暂无历史数据",
    ),
    "search_volume": FieldDefinition(
        "search_volume",
        "seo.field.search_volume",
        "月均搜索量",
        data_type="number",
        semantic_type="search_volume",
        formatter="integer",
        align="right",
        width=130,
        help_text="目标市场中该关键词的月均搜索次数，通常为第三方估算值。",
    ),
    "global_volume": FieldDefinition(
        "global_volume",
        "seo.field.global_volume",
        "全球搜索量",
        data_type="number",
        semantic_type="search_volume",
        formatter="integer",
        align="right",
        width=130,
    ),
    "traffic_estimate": FieldDefinition(
        "traffic_estimate",
        "seo.field.traffic_estimate",
        "预估流量",
        data_type="number",
        semantic_type="estimated_traffic",
        formatter="integer",
        align="right",
        width=120,
        help_text="第三方平台根据排名、搜索量和点击率模型估算。",
    ),
    "organic_traffic_estimate": FieldDefinition(
        "organic_traffic_estimate",
        "seo.field.organic_traffic_estimate",
        "预估自然流量",
        data_type="number",
        semantic_type="estimated_traffic",
        formatter="integer",
        align="right",
        width=140,
    ),
    "traffic_potential": FieldDefinition(
        "traffic_potential",
        "seo.field.traffic_potential",
        "流量潜力",
        data_type="number",
        semantic_type="estimated_traffic",
        formatter="integer",
        align="right",
        width=120,
    ),
    "keyword_difficulty": FieldDefinition(
        "keyword_difficulty",
        "seo.field.keyword_difficulty",
        "关键词难度",
        data_type="number",
        semantic_type="difficulty",
        formatter="difficulty",
        align="right",
        width=130,
        help_text="难度分级为本产品内部展示口径，各数据源原始算法可能不同。",
        ranges=DIFFICULTY_RANGES,
    ),
    "cpc": FieldDefinition(
        "cpc",
        "seo.field.cpc",
        "点击单价",
        data_type="number",
        semantic_type="currency",
        formatter="decimal",
        align="right",
        width=110,
        null_text="数据源未提供",
    ),
    "intent": FieldDefinition(
        "intent",
        "seo.field.intent",
        "搜索意图",
        semantic_type="search_intent",
        formatter="enum",
        width=120,
        value_map=INTENT_MAP,
        null_text="未识别",
    ),
    "url": FieldDefinition(
        "url",
        "seo.field.ranking_url",
        "排名页面",
        data_type="url",
        semantic_type="url",
        formatter="compact_url",
        sortable=False,
        width=250,
    ),
    "source_url": FieldDefinition(
        "source_url",
        "seo.field.source_url",
        "来源页面",
        data_type="url",
        semantic_type="url",
        formatter="compact_url",
        sortable=False,
        width=250,
    ),
    "target_url": FieldDefinition(
        "target_url",
        "seo.field.target_url",
        "目标页面",
        data_type="url",
        semantic_type="url",
        formatter="compact_url",
        sortable=False,
        width=250,
    ),
    "title": FieldDefinition("title", "seo.field.title", "页面标题", width=220),
    "description": FieldDefinition(
        "description",
        "seo.field.description",
        "搜索摘要",
        sortable=False,
        width=280,
    ),
    "rank": FieldDefinition(
        "rank",
        "seo.field.rank",
        "排名",
        data_type="number",
        semantic_type="rank",
        formatter="integer",
        align="right",
        width=90,
    ),
    "type": FieldDefinition(
        "type",
        "seo.field.result_type",
        "结果类型",
        semantic_type="enum",
        formatter="enum",
        width=120,
        value_map={"organic": "自然结果", "featured_snippet": "精选摘要"},
    ),
    "anchor_text": FieldDefinition(
        "anchor_text", "seo.field.anchor_text", "锚文本", width=180, null_text="无锚文本"
    ),
    "link_type": FieldDefinition(
        "link_type",
        "seo.field.link_type",
        "链接属性",
        semantic_type="enum",
        formatter="enum",
        width=110,
        value_map={
            "dofollow": "Dofollow",
            "nofollow": "Nofollow",
            "ugc": "UGC",
            "sponsored": "赞助链接",
        },
    ),
    "first_seen": FieldDefinition(
        "first_seen",
        "seo.field.first_seen",
        "首次发现",
        data_type="date",
        semantic_type="date",
        formatter="date",
        width=140,
    ),
    "last_seen": FieldDefinition(
        "last_seen",
        "seo.field.last_seen",
        "最近发现",
        data_type="date",
        semantic_type="date",
        formatter="date",
        width=140,
    ),
    "is_lost": FieldDefinition(
        "is_lost",
        "seo.field.is_lost",
        "链接状态",
        data_type="boolean",
        semantic_type="boolean",
        formatter="boolean_status",
        width=100,
        value_map={"true": "已丢失", "false": "正常"},
    ),
    "domain": FieldDefinition(
        "domain",
        "seo.field.domain",
        "竞争域名",
        semantic_type="domain",
        formatter="text",
        width=210,
    ),
    "common_keywords_count": FieldDefinition(
        "common_keywords_count",
        "seo.field.common_keywords_count",
        "共同关键词",
        data_type="number",
        semantic_type="count",
        formatter="integer",
        align="right",
        width=130,
    ),
    "competitor_unique_keywords": FieldDefinition(
        "competitor_unique_keywords",
        "seo.field.competitor_unique_keywords",
        "对手独有关键词",
        data_type="number",
        semantic_type="count",
        formatter="integer",
        align="right",
        width=150,
    ),
    "overlap_percentage": FieldDefinition(
        "overlap_percentage",
        "seo.field.overlap_percentage",
        "关键词重合度",
        data_type="number",
        semantic_type="percentage",
        formatter="percentage",
        align="right",
        width=140,
    ),
    "domain_rating": FieldDefinition(
        "domain_rating",
        "seo.field.domain_rating",
        "域名权重",
        data_type="number",
        semantic_type="score",
        formatter="decimal",
        align="right",
        width=110,
        null_text="当前数据源未提供",
    ),
    # Google Search Console future search_performance capability. Its position
    # is an aggregated average and must never reuse the exact-rank field above.
    "query": FieldDefinition("query", "seo.field.query", "搜索词", width=200),
    "page": FieldDefinition(
        "page",
        "seo.field.page",
        "页面",
        data_type="url",
        semantic_type="url",
        formatter="compact_url",
        sortable=False,
        width=250,
    ),
    "clicks": FieldDefinition(
        "clicks",
        "seo.field.clicks",
        "点击",
        data_type="number",
        semantic_type="count",
        formatter="integer",
        align="right",
        width=100,
    ),
    "impressions": FieldDefinition(
        "impressions",
        "seo.field.impressions",
        "曝光",
        data_type="number",
        semantic_type="count",
        formatter="integer",
        align="right",
        width=110,
    ),
    "ctr": FieldDefinition(
        "ctr",
        "seo.field.ctr",
        "点击率",
        data_type="number",
        semantic_type="percentage",
        formatter="percentage",
        align="right",
        width=110,
    ),
    "average_position": FieldDefinition(
        "average_position",
        "seo.field.average_position",
        "平均排名",
        data_type="number",
        semantic_type="average_rank",
        formatter="decimal",
        align="right",
        width=120,
        help_text="Google Search Console 按所选维度聚合后的平均排名，不是单次精确排名。",
    ),
    "date": FieldDefinition(
        "date",
        "seo.field.date",
        "日期",
        data_type="date",
        semantic_type="date",
        formatter="date",
        width=130,
    ),
    "country": FieldDefinition("country", "seo.field.country", "国家/地区", width=120),
    "device": FieldDefinition(
        "device",
        "seo.field.device",
        "设备",
        semantic_type="enum",
        formatter="enum",
        width=110,
        value_map={"DESKTOP": "桌面端", "MOBILE": "移动端", "TABLET": "平板"},
    ),
}


def field_contract(key: str) -> dict[str, Any]:
    return FIELD_CATALOG[key].contract()
