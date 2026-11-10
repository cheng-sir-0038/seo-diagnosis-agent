"""视频搜索统一证据模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from src.models.common import BaseEvidence


class VideoSearchResult(BaseEvidence):
    position: int = Field(ge=1, le=100)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    snippet: str | None = None
    thumbnail_url: str | None = None
    source_name: str | None = None
    channel: str | None = None
    duration_text: str | None = None
    duration_seconds: int | None = Field(default=None, ge=0)
    published_text: str | None = None
    published_at: datetime | None = None
