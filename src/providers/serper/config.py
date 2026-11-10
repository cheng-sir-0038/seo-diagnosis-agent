"""Serper Google Search Provider 配置和垂直接口注册表。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from src.core.config import settings


class SerperSearchType(str, enum.Enum):
    """Serper 已注册的搜索类型。"""

    SEARCH = "search"
    IMAGES = "images"
    NEWS = "news"
    MAPS = "maps"
    PLACES = "places"
    VIDEOS = "videos"
    SHOPPING = "shopping"
    SCHOLAR = "scholar"
    PATENTS = "patents"
    AUTOCOMPLETE = "autocomplete"


@dataclass(frozen=True, slots=True)
class SerperEndpoint:
    """一个 Serper 搜索类型的 HTTP 和响应契约。"""

    path: str
    result_field: str
    supports_num: bool = True
    supports_page: bool = True
    supports_autocorrect: bool = True
    supports_location: bool = False


ENDPOINTS: dict[SerperSearchType, SerperEndpoint] = {
    SerperSearchType.SEARCH: SerperEndpoint("/search", "organic"),
    SerperSearchType.IMAGES: SerperEndpoint("/images", "images"),
    SerperSearchType.NEWS: SerperEndpoint("/news", "news"),
    SerperSearchType.MAPS: SerperEndpoint(
        "/maps",
        "places",
        supports_location=True,
    ),
    SerperSearchType.PLACES: SerperEndpoint(
        "/places",
        "places",
        supports_location=True,
    ),
    SerperSearchType.VIDEOS: SerperEndpoint("/videos", "videos"),
    SerperSearchType.SHOPPING: SerperEndpoint("/shopping", "shopping"),
    SerperSearchType.SCHOLAR: SerperEndpoint("/scholar", "organic"),
    SerperSearchType.PATENTS: SerperEndpoint("/patents", "organic"),
    SerperSearchType.AUTOCOMPLETE: SerperEndpoint(
        "/autocomplete",
        "suggestions",
        supports_num=False,
        supports_page=False,
        supports_autocorrect=False,
    ),
}


@dataclass(frozen=True, slots=True)
class SerperConfig:
    api_base_url: str = field(default_factory=lambda: settings.serper_api_base_url)
    api_key: str = field(default_factory=lambda: settings.serper_api_key)
    request_timeout: float = field(default_factory=lambda: settings.serper_request_timeout)
    max_retries: int = field(default_factory=lambda: settings.serper_max_retries)
    default_language: str = field(default_factory=lambda: settings.serper_default_language)
