from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from src.models.common import Capability
from src.seo_presentation.contracts import base_contract, mapping_rows
from src.seo_presentation.presenters.cards import build_card_contract
from src.seo_presentation.presenters.page_metadata import PageMetadataPresenter
from src.seo_presentation.presenters.page_performance import PagePerformancePresenter
from src.seo_presentation.presenters.search_metrics import SearchMetricsPresenter
from src.seo_presentation.presenters.table import CatalogTablePresenter, TablePresentationSpec


class Presenter(Protocol):
    def present(self, response: Mapping[str, Any]) -> dict[str, Any] | None: ...


class AutocompletePresenter:
    def present(self, response: Mapping[str, Any]) -> dict[str, Any] | None:
        source_rows = mapping_rows(response.get("data"))
        if not source_rows:
            return None
        contract = base_contract(
            capability=Capability.AUTOCOMPLETE.value,
            view_type="table",
            title_key="seo.autocomplete.title",
            title_fallback="Google 搜索联想词",
            description_key="seo.autocomplete.description",
            description_fallback="查看 Google 当前提供的搜索联想表达。",
            response=response,
            limitations=[
                "联想词出现不代表搜索量、关键词难度、CPC 或实际排名。",
                "联想结果会随国家、语言和时间变化。",
            ],
        )
        contract["columns"] = [
            {
                "key": "position",
                "label": {"key": "seo.field.suggestion_order", "fallback": "联想顺序"},
                "data_type": "number",
                "semantic_type": "sequence",
                "formatter": "integer",
                "sortable": True,
                "align": "right",
                "null_text": "暂无顺序",
                "width": 100,
            },
            {
                "key": "value",
                "label": {"key": "seo.field.suggestion", "fallback": "联想词"},
                "data_type": "string",
                "semantic_type": "keyword_suggestion",
                "formatter": "text",
                "sortable": True,
                "align": "left",
                "null_text": "暂无联想词",
                "width": 300,
            },
        ]
        contract["rows"] = [
            {"position": row.get("position"), "value": row.get("value")} for row in source_rows
        ]
        return contract


class NewsPresenter:
    def present(self, response: Mapping[str, Any]) -> dict[str, Any] | None:
        rows = mapping_rows(response.get("data"))
        if not rows:
            return None
        items = []
        for index, row in enumerate(rows, start=1):
            url = str(row.get("url") or "").strip()
            title = str(row.get("title") or "").strip()
            if not url or not title:
                continue
            metadata = []
            if row.get("source_name"):
                metadata.append({"label": "来源", "value": row.get("source_name")})
            published = row.get("published_text") or row.get("published_at")
            if published:
                metadata.append({"label": "发布时间", "value": published})
            items.append(
                {
                    "key": url or f"news-{index}",
                    "title": title,
                    "url": url,
                    "image_url": row.get("image_url"),
                    "summary": row.get("snippet"),
                    "metadata": metadata,
                }
            )
        if not items:
            return None
        return build_card_contract(
            capability=Capability.NEWS_SEARCH,
            response=response,
            variant="news",
            title_key="seo.news.title",
            title="近期相关新闻",
            description_key="seo.news.description",
            description="查看当前媒体报道、来源和发布时间。",
            items=items,
            limitations=(
                "新闻结果反映当前搜索页，不代表长期媒体趋势。",
                "相对时间为上游原文，无法可靠转换时不会生成精确时间。",
            ),
        )


class ImageSearchPresenter:
    def present(self, response: Mapping[str, Any]) -> dict[str, Any] | None:
        rows = mapping_rows(response.get("data"))
        items = []
        for index, row in enumerate(rows, start=1):
            page_url = str(row.get("page_url") or "").strip()
            image_url = str(row.get("image_url") or "").strip()
            if not page_url or not image_url:
                continue
            dimensions = None
            if row.get("image_width") and row.get("image_height"):
                dimensions = f"{row['image_width']} × {row['image_height']}"
            metadata = []
            source = row.get("source_name") or row.get("domain")
            if source:
                metadata.append({"label": "来源", "value": str(source)})
            if dimensions:
                metadata.append({"label": "尺寸", "value": dimensions})
            items.append(
                {
                    "key": page_url + "#" + str(index),
                    "title": row.get("title") or row.get("domain") or "图片结果",
                    "url": page_url,
                    "image_url": image_url,
                    "summary": row.get("domain"),
                    "metadata": metadata,
                }
            )
        if not items:
            return None
        return build_card_contract(
            capability=Capability.IMAGE_SEARCH,
            response=response,
            variant="image-grid",
            title_key="seo.images.title",
            title="Google 图片搜索结果",
            description_key="seo.images.description",
            description="查看图片、来源页面和尺寸。",
            items=items,
            limitations=(
                "图片出现不代表当前站点拥有图片版权。",
                "Google 内部跳转地址不会作为标准结果返回。",
            ),
        )


class VideoSearchPresenter:
    def present(self, response: Mapping[str, Any]) -> dict[str, Any] | None:
        rows = mapping_rows(response.get("data"))
        items = []
        for index, row in enumerate(rows, start=1):
            url = str(row.get("url") or "").strip()
            title = str(row.get("title") or "").strip()
            if not url or not title:
                continue
            metadata = []
            for label, key in (
                ("平台", "source_name"),
                ("频道", "channel"),
                ("时长", "duration_text"),
                ("发布时间", "published_text"),
            ):
                if row.get(key):
                    metadata.append({"label": label, "value": str(row[key])})
            items.append(
                {
                    "key": url or f"video-{index}",
                    "title": title,
                    "url": url,
                    "image_url": row.get("thumbnail_url"),
                    "summary": row.get("snippet"),
                    "metadata": metadata,
                }
            )
        if not items:
            return None
        return build_card_contract(
            capability=Capability.VIDEO_SEARCH,
            response=response,
            variant="video",
            title_key="seo.videos.title",
            title="Google 视频搜索结果",
            description_key="seo.videos.description",
            description="查看视频平台、频道、时长和发布时间。",
            items=items,
            limitations=("视频出现不代表可自由转载。", "相对发布时间无法可靠转换时保留原文。"),
        )


ORGANIC_KEYWORDS_LIMITATIONS = (
    "排名、搜索量和预估流量来自第三方 SEO 数据源，通常属于估算值。",
    "关键词难度分级为本产品内部展示口径，不代表所有数据源使用相同算法。",
)

PRESENTERS: dict[Capability, Presenter] = {
    Capability.SEARCH_METRICS: SearchMetricsPresenter(),
    Capability.PAGE_METADATA: PageMetadataPresenter(),
    Capability.PAGE_PERFORMANCE: PagePerformancePresenter(),
    Capability.AUTOCOMPLETE: AutocompletePresenter(),
    Capability.NEWS_SEARCH: NewsPresenter(),
    Capability.IMAGE_SEARCH: ImageSearchPresenter(),
    Capability.VIDEO_SEARCH: VideoSearchPresenter(),
    Capability.ORGANIC_KEYWORDS: CatalogTablePresenter(
        TablePresentationSpec(
            capability=Capability.ORGANIC_KEYWORDS,
            title_key="seo.organic_keywords.title",
            title="关键词明细",
            description_key="seo.organic_keywords.description",
            description="查看具体关键词、排名、搜索量和对应页面。",
            fields=(
                "keyword",
                "position",
                "position_change",
                "search_volume",
                "traffic_estimate",
                "keyword_difficulty",
                "intent",
                "url",
            ),
            limitations=ORGANIC_KEYWORDS_LIMITATIONS,
        )
    ),
    Capability.KEYWORD_OVERVIEW: CatalogTablePresenter(
        TablePresentationSpec(
            capability=Capability.KEYWORD_OVERVIEW,
            title_key="seo.keyword_overview.title",
            title="关键词价值分析",
            description_key="seo.keyword_overview.description",
            description="查看关键词搜索需求、竞争难度和商业价值。",
            fields=(
                "keyword",
                "search_volume",
                "global_volume",
                "keyword_difficulty",
                "cpc",
                "intent",
                "traffic_potential",
            ),
            limitations=("关键词难度分级为本产品内部展示口径。",),
        )
    ),
    Capability.SERP_OVERVIEW: CatalogTablePresenter(
        TablePresentationSpec(
            capability=Capability.SERP_OVERVIEW,
            title_key="seo.serp_overview.title",
            title="搜索结果页分析",
            description_key="seo.serp_overview.description",
            description="查看指定关键词当前排名页面和结果类型。",
            fields=("rank", "title", "type", "url", "description"),
        )
    ),
    Capability.BACKLINKS_LIST: CatalogTablePresenter(
        TablePresentationSpec(
            capability=Capability.BACKLINKS_LIST,
            title_key="seo.backlinks.title",
            title="外链明细",
            description_key="seo.backlinks.description",
            description="查看链接来源、锚文本、目标页面和链接状态。",
            fields=(
                "source_url",
                "anchor_text",
                "target_url",
                "link_type",
                "first_seen",
                "is_lost",
            ),
        )
    ),
    Capability.ORGANIC_COMPETITORS: CatalogTablePresenter(
        TablePresentationSpec(
            capability=Capability.ORGANIC_COMPETITORS,
            title_key="seo.organic_competitors.title",
            title="自然搜索竞争对手",
            description_key="seo.organic_competitors.description",
            description="查看与本站共享关键词的主要竞争网站。",
            fields=(
                "domain",
                "common_keywords_count",
                "competitor_unique_keywords",
                "organic_traffic_estimate",
                "overlap_percentage",
                "domain_rating",
            ),
        )
    ),
}


def present_capability(
    capability: Capability, response: Mapping[str, Any]
) -> dict[str, Any] | None:
    presenter = PRESENTERS.get(capability)
    return presenter.present(response) if presenter else None
