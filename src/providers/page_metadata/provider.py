"""页面源 HTML 元信息 Provider。"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from src.models.common import Capability, DataIntegrity, DataSource
from src.models.page_metadata import PageMetadataReport
from src.providers.base import BaseProvider, ProviderMeta
from src.providers.page_metadata.client import FetchedHtmlPage, PageMetadataClient
from src.providers.page_metadata.pyseo_client import PythonSeoAnalyzerClient


class PageMetadataProvider(BaseProvider):
    def __init__(
        self,
        client: PageMetadataClient | None = None,
        analyzer: PythonSeoAnalyzerClient | None = None,
    ) -> None:
        super().__init__()
        self.client = client or PageMetadataClient()
        self.analyzer = analyzer or PythonSeoAnalyzerClient()

    @property
    def meta(self) -> ProviderMeta:
        return ProviderMeta(
            name="page_html",
            label="Direct Page HTML",
            source=DataSource.PAGE_HTML,
            supported_capabilities={Capability.PAGE_METADATA},
        )

    @property
    def runtime_mode(self) -> str:
        return "live"

    async def startup(self) -> None:
        await self.client.startup()

    async def shutdown(self) -> None:
        await self.client.shutdown()

    async def health_check(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.client._session is not None else "degraded",
            "mode": "live",
            "session_ready": self.client._session is not None,
        }

    async def get_page_metadata(
        self, site_id: str, url: str | None = None, **kwargs: Any
    ) -> PageMetadataReport:
        del kwargs
        page = await self.client.fetch(site_id=site_id, url=url)
        parsed = parse_page_metadata(page)
        analyzer_evidence = await self._analyze_page(page)
        return PageMetadataReport(
            source=DataSource.PAGE_HTML,
            is_estimated=False,
            integrity=DataIntegrity.COMPLETE,
            requested_url=page.requested_url,
            final_url=page.final_url,
            status_code=page.status_code,
            content_type=page.content_type,
            content_length_bytes=page.content_length_bytes,
            **parsed,
            **analyzer_evidence,
        )

    async def _analyze_page(self, page: FetchedHtmlPage) -> dict[str, Any]:
        if not getattr(self.analyzer, "enabled", False):
            return {
                "analyzer_name": "python-seo-analyzer",
                "analyzer_status": "disabled",
            }
        try:
            raw = await self.analyzer.analyze(page)
        except FileNotFoundError:
            return {
                "analyzer_name": "python-seo-analyzer",
                "analyzer_status": "unavailable",
                "analyzer_error": "python-seo-analyzer 隔离环境不可用",
            }
        except Exception:
            return {
                "analyzer_name": "python-seo-analyzer",
                "analyzer_status": "failed",
                "analyzer_error": "python-seo-analyzer 分析失败",
            }
        return normalize_python_seo_analyzer_result(raw)


def normalize_python_seo_analyzer_result(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "analyzer_name": "python-seo-analyzer",
            "analyzer_status": "failed",
            "analyzer_error": "python-seo-analyzer 返回结果无效",
        }

    status = str(raw.get("analyzer_status") or "completed").strip().lower()
    if status not in {"disabled", "completed", "unavailable", "failed"}:
        status = "failed"
    headings = _normalize_analyzer_headings(raw.get("headings"))
    heading_counts = _normalize_non_negative_mapping(raw.get("heading_counts"))
    if not heading_counts:
        heading_counts = {key: len(value) for key, value in headings.items()}

    return {
        "analyzer_name": _limited_text(raw.get("analyzer_name"), 100)
        or "python-seo-analyzer",
        "analyzer_version": _limited_text(raw.get("analyzer_version"), 50),
        "analyzer_status": status,
        "analyzer_error": _limited_text(raw.get("analyzer_error"), 200),
        "word_count": _non_negative_int(raw.get("word_count")),
        "content_hash": _limited_text(raw.get("content_hash"), 128),
        "headings": headings,
        "heading_counts": heading_counts,
        "internal_links_count": _non_negative_int(raw.get("internal_links_count")),
        "image_missing_alt_count": _non_negative_int(
            raw.get("image_missing_alt_count")
        ),
        "generic_anchor_text_count": _non_negative_int(
            raw.get("generic_anchor_text_count")
        ),
        "analyzer_warnings": [
            item
            for item in (
                _limited_text(value, 500)
                for value in (raw.get("analyzer_warnings") or [])[:100]
            )
            if item
        ],
    }


def _normalize_analyzer_headings(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[str]] = {}
    for key in ("h1", "h2", "h3", "h4", "h5", "h6"):
        items = value.get(key)
        if not isinstance(items, list):
            continue
        normalized = [
            item
            for item in (_limited_text(raw, 500) for raw in items[:100])
            if item
        ]
        if normalized:
            result[key] = normalized
    return result


def _normalize_non_negative_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key in ("h1", "h2", "h3", "h4", "h5", "h6"):
        amount = _non_negative_int(value.get(key))
        if amount is not None:
            result[key] = amount
    return result


def _non_negative_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _limited_text(value: Any, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


def parse_page_metadata(page: FetchedHtmlPage) -> dict[str, Any]:
    parser = _MetadataParser()
    parser.feed(page.html)
    parser.close()

    title = _first(parser.titles)
    description = _first(parser.descriptions)
    keywords = _first(parser.keywords)
    canonical = _first(parser.canonicals)
    if canonical:
        canonical = urljoin(page.final_url, canonical)
    robots = _first(parser.robots)
    googlebot = _first(parser.googlebot)
    x_robots = page.headers.get("x-robots-tag")
    declared_noindex = _contains_noindex(robots, googlebot, x_robots)

    warnings: list[str] = []
    warnings.extend(parser.structured_warnings)
    if not 200 <= page.status_code < 300:
        warnings.append(f"页面返回 HTTP {page.status_code}，当前元信息可能来自错误页。")
    if not title:
        warnings.append("页面源 HTML 中没有非空 Title。")
    if len(parser.titles) > 1:
        warnings.append("页面存在多个 Title，需要保留唯一主标题。")
    if not description:
        warnings.append("页面源 HTML 中没有非空 Meta Description。")
    if len(parser.descriptions) > 1:
        warnings.append("页面存在多个 Meta Description。")
    if len(parser.canonicals) > 1:
        warnings.append("页面存在多个 Canonical 声明。")
    if not parser.h1s:
        warnings.append("页面源 HTML 中没有 H1。")
    elif len(parser.h1s) > 1:
        warnings.append("页面存在多个 H1，需要确认主标题层级是否清晰。")
    if declared_noindex:
        warnings.append("页面声明了 noindex，搜索引擎可能不会收录该页面。")

    return {
        "title": title,
        "title_count": len(parser.titles),
        "meta_description": description,
        "meta_description_count": len(parser.descriptions),
        "meta_keywords": keywords,
        "meta_keywords_count": len(parser.keywords),
        "canonical_url": canonical,
        "canonical_count": len(parser.canonicals),
        "robots": robots,
        "googlebot": googlebot,
        "x_robots_tag": x_robots,
        "declared_noindex": declared_noindex,
        "html_lang": parser.html_lang,
        "charset": parser.charset,
        "h1s": parser.h1s,
        "h1_count": len(parser.h1s),
        "og_title": _first(parser.og_titles),
        "og_description": _first(parser.og_descriptions),
        "og_image": urljoin(page.final_url, parser.meta_tags["og:image"])
        if parser.meta_tags.get("og:image") else None,
        "meta_tags": parser.meta_tags,
        "content_excerpt": _clean(" ".join(parser.text_parts)) or "",
        "content_excerpt_truncated": parser.text_truncated,
        "existing_json_ld": parser.json_ld,
        "warnings": warnings,
    }


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.titles: list[str] = []
        self.descriptions: list[str] = []
        self.keywords: list[str] = []
        self.canonicals: list[str] = []
        self.robots: list[str] = []
        self.googlebot: list[str] = []
        self.og_titles: list[str] = []
        self.og_descriptions: list[str] = []
        self.h1s: list[str] = []
        self.html_lang: str | None = None
        self.charset: str | None = None
        self._title_buffer: list[str] | None = None
        self._h1_buffer: list[str] | None = None
        self._h1_depth = 0
        self.meta_tags: dict[str, str] = {}
        self.text_parts: list[str] = []
        self.text_length = 0
        self.text_truncated = False
        self._excluded: list[str] = []
        self._json_buffer: str | None = None
        self.json_ld: list[Any] = []
        self.structured_warnings: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {str(k).lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        if tag in {"head", "script", "style", "template", "noscript", "title"}:
            self._excluded.append(tag)
        if tag == "script" and values.get("type", "").strip().lower() == "application/ld+json":
            self._json_buffer = ""
        if tag == "html" and not self.html_lang:
            self.html_lang = _clean(values.get("lang"))
        elif tag == "title":
            self._title_buffer = []
        elif tag == "h1":
            if self._h1_depth == 0:
                self._h1_buffer = []
            self._h1_depth += 1
        elif tag == "meta":
            self._handle_meta(values)
        elif tag == "link":
            rel = {item.lower() for item in values.get("rel", "").split()}
            href = _clean(values.get("href"))
            if "canonical" in rel and href:
                self.canonicals.append(href)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._excluded:
            self._excluded = self._excluded[:self._excluded.index(tag)]
        if tag == "script" and self._json_buffer is not None:
            try:
                if len(self.json_ld) >= 5 or len(self._json_buffer) > 20000:
                    raise ValueError("JSON-LD 超出读取范围")
                value = json.loads(self._json_buffer)
                if not isinstance(value, (dict, list)):
                    raise ValueError("JSON-LD 不是对象或数组")
                self.json_ld.append(value)
            except (ValueError, RecursionError):
                self.structured_warnings.append("页面存在无法解析或超出读取范围的 JSON-LD。")
            self._json_buffer = None
        if tag == "title" and self._title_buffer is not None:
            text = _clean("".join(self._title_buffer))
            if text:
                self.titles.append(text)
            else:
                self.titles.append("")
            self._title_buffer = None
        elif tag == "h1" and self._h1_depth:
            self._h1_depth -= 1
            if self._h1_depth == 0 and self._h1_buffer is not None:
                text = _clean("".join(self._h1_buffer))
                if text:
                    self.h1s.append(text)
                self._h1_buffer = None

    def handle_data(self, data: str) -> None:
        if self._json_buffer is not None:
            self._json_buffer = (self._json_buffer + data)[:20001]
        if not self._excluded and (text := _clean(data)):
            remaining = max(0, 6000 - self.text_length - bool(self.text_parts))
            if len(text) > remaining:
                self.text_truncated = True
            if remaining:
                self.text_parts.append(text[:remaining])
                self.text_length += len(text[:remaining]) + (len(self.text_parts) > 1)
        if self._title_buffer is not None:
            self._title_buffer.append(data)
        if self._h1_buffer is not None:
            self._h1_buffer.append(data)

    def _handle_meta(self, values: dict[str, str]) -> None:
        name = values.get("name", "").strip().lower()
        prop = values.get("property", "").strip().lower()
        content = _clean(values.get("content"))
        if values.get("charset") and not self.charset:
            self.charset = _clean(values.get("charset"))
        if values.get("http-equiv", "").lower() == "content-type" and not self.charset:
            match = re.search(r"charset\s*=\s*([^;\s]+)", values.get("content", ""), re.I)
            if match:
                self.charset = match.group(1).strip("\"'")
        targets = {
            "description": self.descriptions,
            "keywords": self.keywords,
            "robots": self.robots,
            "googlebot": self.googlebot,
        }
        if name in targets:
            targets[name].append(content or "")
        if not content:
            return
        key = prop or name
        if key in {
            "author", "article:author", "article:published_time", "article:modified_time",
            "og:site_name", "og:type", "og:url", "og:image",
        }:
            self.meta_tags.setdefault(key, content[:1000])
        if prop == "og:title":
            self.og_titles.append(content)
        elif prop == "og:description":
            self.og_descriptions.append(content)


def _contains_noindex(*values: str | None) -> bool:
    for value in values:
        if value and "noindex" in {item for item in re.split(r"[\s,;:]+", value.lower()) if item}:
            return True
    return False


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _first(values: list[str]) -> str | None:
    return next((item for item in values if item), None)
