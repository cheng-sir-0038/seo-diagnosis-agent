"""页面 HTML 元信息展示适配器。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.models.common import Capability
from src.seo_presentation.contracts import base_contract


class PageMetadataPresenter:
    capability = Capability.PAGE_METADATA

    def present(self, response: Mapping[str, Any]) -> dict[str, Any] | None:
        data = response.get("data")
        if not isinstance(data, Mapping):
            return None
        if not isinstance(data.get("status_code"), int):
            return None

        result = base_contract(
            capability=self.capability.value,
            view_type="chart",
            title_key="seo.page_metadata.title",
            title_fallback="页面元信息检查",
            description_key="seo.page_metadata.description",
            description_fallback="直接读取页面源 HTML，检查 TDK、Canonical、索引指令和主标题。",
            response=response,
            limitations=[
                (
                    "本结果读取 HTTP 返回的源 HTML，不执行页面 JavaScript；"
                    "脚本运行后注入的标签可能无法看到。"
                ),
                "搜索结果中的标题和摘要可能被搜索引擎改写，不能用 SERP 展示代替页面当前标签。",
                "Meta Keywords 可展示用于兼容性排查，但 Google Search 不使用它进行索引或排名。",
                "python-seo-analyzer 的 warnings 是第三方原始观察，不会直接成为正式 SEO 问题。",
            ],
        )
        result["provenance"].update(
            {
                "requested_url": _text(data.get("requested_url")),
                "final_url": _text(data.get("final_url")),
            }
        )
        result.update(
            {
                "variant": "page_metadata",
                "http": {
                    "status_code": data.get("status_code"),
                    "content_type": _text(data.get("content_type")),
                    "content_length_bytes": data.get("content_length_bytes"),
                    "final_url": _text(data.get("final_url")),
                },
                "indexing": {
                    "declared_noindex": bool(data.get("declared_noindex")),
                    "robots": _text(data.get("robots")),
                    "googlebot": _text(data.get("googlebot")),
                    "x_robots_tag": _text(data.get("x_robots_tag")),
                },
                "checks": [
                    _check(
                        "title", "Title", data.get("title"), data.get("title_count"), required=True
                    ),
                    _check(
                        "description",
                        "Meta Description",
                        data.get("meta_description"),
                        data.get("meta_description_count"),
                        required=True,
                    ),
                    _check(
                        "keywords",
                        "Meta Keywords",
                        data.get("meta_keywords"),
                        data.get("meta_keywords_count"),
                        required=False,
                        note="Google 不使用该标签进行索引或排名。",
                        force_info=True,
                    ),
                    _check(
                        "canonical",
                        "Canonical",
                        data.get("canonical_url"),
                        data.get("canonical_count"),
                        required=False,
                        note="用于声明重复或相似页面的规范 URL。",
                    ),
                    _check(
                        "h1",
                        "H1 主标题",
                        (data.get("h1s") or [None])[0],
                        data.get("h1_count"),
                        required=True,
                    ),
                    _check(
                        "language",
                        "页面语言",
                        data.get("html_lang"),
                        1 if data.get("html_lang") else 0,
                        required=False,
                    ),
                ],
                "headings": [str(item) for item in data.get("h1s") or []],
                "additional": {
                    "charset": _text(data.get("charset")),
                    "og_title": _text(data.get("og_title")),
                    "og_description": _text(data.get("og_description")),
                },
                "analyzer": {
                    "name": _text(data.get("analyzer_name")),
                    "version": _text(data.get("analyzer_version")),
                    "status": _text(data.get("analyzer_status")) or "disabled",
                    "error": _text(data.get("analyzer_error")),
                    "word_count": data.get("word_count"),
                    "content_hash": _text(data.get("content_hash")),
                    "heading_counts": _int_mapping(data.get("heading_counts")),
                    "internal_links_count": data.get("internal_links_count"),
                    "image_missing_alt_count": data.get("image_missing_alt_count"),
                    "generic_anchor_text_count": data.get("generic_anchor_text_count"),
                    "warnings": [
                        str(item) for item in data.get("analyzer_warnings") or []
                    ],
                },
                "warnings": [str(item) for item in data.get("warnings") or []],
            }
        )
        return result


def _check(
    key: str,
    label: str,
    value: Any,
    count: Any,
    *,
    required: bool,
    note: str | None = None,
    force_info: bool = False,
) -> dict[str, Any]:
    text = _text(value)
    amount = int(count) if isinstance(count, (int, float)) else (1 if text else 0)
    if force_info:
        status = "info"
    elif not text:
        status = "missing" if required else "info"
    elif amount > 1:
        status = "warning"
    else:
        status = "good"
    return {
        "key": key,
        "label": label,
        "value": text,
        "length": len(text) if text else 0,
        "count": amount,
        "status": status,
        "note": note,
    }


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _int_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): int(amount)
        for key, amount in value.items()
        if isinstance(amount, (int, float)) and amount >= 0
    }
