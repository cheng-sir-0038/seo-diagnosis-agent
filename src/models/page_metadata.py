"""单页面 HTML 元信息统一模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from src.models.common import BaseEvidence


class PageMetadataReport(BaseEvidence):
    """直接读取页面源 HTML 后得到的元信息证据。"""

    requested_url: str
    final_url: str
    status_code: int = Field(ge=100, le=599)
    content_type: str | None = None
    content_length_bytes: int = Field(default=0, ge=0)

    title: str | None = None
    title_count: int = Field(default=0, ge=0)
    meta_description: str | None = None
    meta_description_count: int = Field(default=0, ge=0)
    meta_keywords: str | None = None
    meta_keywords_count: int = Field(default=0, ge=0)

    canonical_url: str | None = None
    canonical_count: int = Field(default=0, ge=0)
    robots: str | None = None
    googlebot: str | None = None
    x_robots_tag: str | None = None
    declared_noindex: bool = False

    html_lang: str | None = None
    charset: str | None = None
    h1s: list[str] = Field(default_factory=list)
    h1_count: int = Field(default=0, ge=0)
    og_title: str | None = None
    og_description: str | None = None
    warnings: list[str] = Field(default_factory=list)

    analyzer_name: str | None = None
    analyzer_version: str | None = None
    analyzer_status: Literal["disabled", "completed", "unavailable", "failed"] = "disabled"
    analyzer_error: str | None = None
    word_count: int | None = Field(default=None, ge=0)
    content_hash: str | None = None
    headings: dict[str, list[str]] = Field(default_factory=dict)
    heading_counts: dict[str, int] = Field(default_factory=dict)
    internal_links_count: int | None = Field(default=None, ge=0)
    image_missing_alt_count: int | None = Field(default=None, ge=0)
    generic_anchor_text_count: int | None = Field(default=None, ge=0)
    analyzer_warnings: list[str] = Field(default_factory=list)
