"""新闻搜索统一证据模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from src.models.common import BaseEvidence


class NewsSearchResult(BaseEvidence):
    """一条实时新闻搜索结果。"""

    position: int = Field(ge=1, le=100)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    snippet: str | None = None
    source_name: str | None = None
    image_url: str | None = None
    published_text: str | None = None
    published_at: datetime | None = None
