"""图片搜索统一证据模型。"""

from __future__ import annotations

from pydantic import Field

from src.models.common import BaseEvidence


class ImageSearchResult(BaseEvidence):
    position: int = Field(ge=1, le=100)
    title: str | None = None
    source_name: str | None = None
    domain: str | None = None
    page_url: str = Field(min_length=1)
    image_url: str = Field(min_length=1)
    thumbnail_url: str | None = None
    image_width: int | None = Field(default=None, ge=1)
    image_height: int | None = Field(default=None, ge=1)
    thumbnail_width: int | None = Field(default=None, ge=1)
    thumbnail_height: int | None = Field(default=None, ge=1)
