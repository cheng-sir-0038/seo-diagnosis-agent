"""Serper Google Search 数据适配器。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from src.core.exceptions import InternalProviderResponseError
from src.models.common import Capability, DataIntegrity, DataSource
from src.models.image_search import ImageSearchResult
from src.models.keyword import AutocompleteSuggestion, SerpResult
from src.models.news import NewsSearchResult
from src.models.video_search import VideoSearchResult
from src.providers.base import BaseProvider, ProviderMeta, ProviderMode
from src.providers.serper.client import SerperClient
from src.providers.serper.config import SerperConfig, SerperSearchType
from src.routing.contracts import ProviderFeature, ProviderProfile


class SerperProvider(BaseProvider):
    """将 Serper 普通 Google 自然搜索结果翻译为统一 SERP 模型。"""

    def __init__(self, client: SerperClient | None = None) -> None:
        super().__init__()
        self.client = client or SerperClient()

    @property
    def meta(self) -> ProviderMeta:
        return ProviderMeta(
            name="serper",
            label="Serper Google Search API",
            source=DataSource.SERPER,
            supported_capabilities={
                Capability.SERP_OVERVIEW,
                Capability.AUTOCOMPLETE,
                Capability.NEWS_SEARCH,
                Capability.IMAGE_SEARCH,
                Capability.VIDEO_SEARCH,
            },
        )

    @property
    def runtime_mode(self) -> ProviderMode:
        return "live" if self.client.config.api_key else "unavailable"

    @property
    def routing_profile(self) -> ProviderProfile:
        return ProviderProfile(
            provider_name=self.meta.name,
            source=self.source,
            runtime_mode=self.runtime_mode,
            capabilities=frozenset(self.capabilities),
            supported_features=frozenset(
                {
                    ProviderFeature.REALTIME,
                    ProviderFeature.COUNTRY_TARGETING,
                    ProviderFeature.LANGUAGE_TARGETING,
                    ProviderFeature.PAGINATION,
                    ProviderFeature.AUTOCORRECT,
                }
            ),
            implemented_features=frozenset(
                {
                    ProviderFeature.REALTIME,
                    ProviderFeature.COUNTRY_TARGETING,
                    ProviderFeature.LANGUAGE_TARGETING,
                    ProviderFeature.PAGINATION,
                    ProviderFeature.AUTOCORRECT,
                }
            ),
            capability_limits={Capability.SERP_OVERVIEW: 100},
        )

    async def startup(self) -> None:
        await self.client.startup()

    async def shutdown(self) -> None:
        await self.client.shutdown()

    async def health_check(self) -> dict[str, Any]:
        configured = bool(self.client.config.api_key)
        return {
            "status": "ok" if configured else "unavailable",
            "mode": self.runtime_mode,
            "api_key_configured": configured,
            "session_ready": self.client._session is not None,
            "auth_valid": None,
        }

    async def get_serp_overview(
        self,
        keyword: str,
        country: str,
        limit: int | None = None,
        language: str | None = None,
        page: int = 1,
        autocorrect: bool = True,
        **kwargs: Any,
    ) -> list[SerpResult]:
        del kwargs
        query = keyword.strip()
        if not query:
            raise ValueError("Serper keyword 不能为空")

        config = self.client.config if hasattr(self.client, "config") else SerperConfig()
        selected_country = _normalize_country(country)
        selected_language = _normalize_language(language or config.default_language)
        selected_limit = max(1, min(int(limit or 20), 100))
        selected_page = max(1, min(int(page), 10))

        raw = await self.client.search(
            query=query,
            country=selected_country,
            language=selected_language,
            limit=selected_limit,
            page=selected_page,
            autocorrect=bool(autocorrect),
        )
        organic = raw.get("organic", [])
        if not isinstance(organic, list):
            raise InternalProviderResponseError(
                "Serper 响应 organic 字段格式错误",
                provider="serper",
                debug_context={"organic_type": type(organic).__name__},
            )

        fetched_at = datetime.now(UTC)
        rows: list[SerpResult] = []
        for index, item in enumerate(organic, start=1):
            if not isinstance(item, Mapping):
                continue
            url = _text(item.get("link"))
            if not url:
                continue
            rank = _rank(
                item.get("position"),
                page=selected_page,
                limit=selected_limit,
                index=index,
            )
            if rank is None:
                continue
            rows.append(
                SerpResult(
                    source=DataSource.SERPER,
                    fetched_at=fetched_at,
                    data_date=fetched_at.date(),
                    is_estimated=False,
                    integrity=DataIntegrity.PARTIAL,
                    rank=rank,
                    type="organic",
                    title=_text(item.get("title")),
                    url=url,
                    description=_text(item.get("snippet")),
                )
            )
            if len(rows) >= selected_limit:
                break

        if organic and not rows:
            raise InternalProviderResponseError(
                "Serper organic 结果无法映射为统一 SERP 数据",
                provider="serper",
                debug_context={"organic_count": len(organic)},
            )
        return rows

    async def get_autocomplete(
        self,
        query: str,
        country: str = "us",
        language: str = "en",
        **kwargs: Any,
    ) -> list[AutocompleteSuggestion]:
        del kwargs
        selected_query = query.strip()
        if not selected_query:
            raise ValueError("Serper autocomplete query 不能为空")
        selected_country = _normalize_country(country)
        selected_language = _normalize_language(language)
        raw = await self.client.request(
            SerperSearchType.AUTOCOMPLETE,
            query=selected_query,
            country=selected_country,
            language=selected_language,
        )
        suggestions = raw.get("suggestions", [])
        if not isinstance(suggestions, list):
            raise InternalProviderResponseError(
                "Serper suggestions 字段格式错误",
                provider="serper",
                debug_context={"suggestions_type": type(suggestions).__name__},
            )

        fetched_at = datetime.now(UTC)
        seen: set[str] = set()
        rows: list[AutocompleteSuggestion] = []
        for item in suggestions:
            value = _suggestion_value(item)
            if value is None:
                continue
            normalized = _normalize_suggestion(value)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            rows.append(
                AutocompleteSuggestion(
                    source=DataSource.SERPER,
                    fetched_at=fetched_at,
                    data_date=fetched_at.date(),
                    is_estimated=False,
                    integrity=DataIntegrity.COMPLETE,
                    position=len(rows) + 1,
                    value=value,
                    normalized_value=normalized,
                )
            )
        return rows

    async def get_news_search(
        self,
        query: str,
        country: str = "us",
        language: str = "en",
        limit: int = 10,
        page: int = 1,
        autocorrect: bool = True,
        **kwargs: Any,
    ) -> list[NewsSearchResult]:
        del kwargs
        selected_query = query.strip()
        if not selected_query:
            raise ValueError("Serper news query 不能为空")
        selected_country = _normalize_country(country)
        selected_language = _normalize_language(language)
        selected_limit = max(1, min(int(limit), 100))
        selected_page = max(1, min(int(page), 10))
        raw = await self.client.request(
            SerperSearchType.NEWS,
            query=selected_query,
            country=selected_country,
            language=selected_language,
            limit=selected_limit,
            page=selected_page,
            autocorrect=bool(autocorrect),
        )
        values = raw.get("news", [])
        if not isinstance(values, list):
            raise InternalProviderResponseError(
                "Serper news 字段格式错误",
                provider="serper",
                debug_context={"news_type": type(values).__name__},
            )
        fetched_at = datetime.now(UTC)
        rows: list[NewsSearchResult] = []
        for index, item in enumerate(values, start=1):
            if not isinstance(item, Mapping):
                continue
            title = _text(item.get("title"))
            url = _text(item.get("link"))
            if not title or not url:
                continue
            position = _positive_int(item.get("position")) or (
                (selected_page - 1) * selected_limit + index
            )
            if not 1 <= position <= 100:
                continue
            published_text = _text(item.get("date"))
            rows.append(
                NewsSearchResult(
                    source=DataSource.SERPER,
                    fetched_at=fetched_at,
                    data_date=fetched_at.date(),
                    is_estimated=False,
                    integrity=DataIntegrity.COMPLETE,
                    position=position,
                    title=title,
                    url=url,
                    snippet=_text(item.get("snippet")),
                    source_name=_text(item.get("source")),
                    image_url=_text(item.get("imageUrl")),
                    published_text=published_text,
                    published_at=_parse_absolute_datetime(published_text),
                )
            )
            if len(rows) >= selected_limit:
                break
        return rows

    async def get_image_search(
        self,
        query: str,
        country: str = "us",
        language: str = "en",
        limit: int = 10,
        page: int = 1,
        autocorrect: bool = True,
        **kwargs: Any,
    ) -> list[ImageSearchResult]:
        del kwargs
        selected_query = query.strip()
        if not selected_query:
            raise ValueError("Serper images query 不能为空")
        selected_country = _normalize_country(country)
        selected_language = _normalize_language(language)
        selected_limit = max(1, min(int(limit), 100))
        selected_page = max(1, min(int(page), 10))
        raw = await self.client.request(
            SerperSearchType.IMAGES,
            query=selected_query,
            country=selected_country,
            language=selected_language,
            limit=selected_limit,
            page=selected_page,
            autocorrect=bool(autocorrect),
        )
        values = raw.get("images", [])
        if not isinstance(values, list):
            raise InternalProviderResponseError(
                "Serper images 字段格式错误",
                provider="serper",
                debug_context={"images_type": type(values).__name__},
            )
        fetched_at = datetime.now(UTC)
        rows = []
        for index, item in enumerate(values, start=1):
            if not isinstance(item, Mapping):
                continue
            page_url = _text(item.get("link"))
            image_url = _text(item.get("imageUrl"))
            if not page_url or not image_url:
                continue
            position = _positive_int(item.get("position")) or (
                (selected_page - 1) * selected_limit + index
            )
            if not 1 <= position <= 100:
                continue
            rows.append(
                ImageSearchResult(
                    source=DataSource.SERPER,
                    fetched_at=fetched_at,
                    data_date=fetched_at.date(),
                    is_estimated=False,
                    integrity=DataIntegrity.COMPLETE,
                    position=position,
                    title=_text(item.get("title")),
                    source_name=_text(item.get("source")),
                    domain=_text(item.get("domain")),
                    page_url=page_url,
                    image_url=image_url,
                    thumbnail_url=_text(item.get("thumbnailUrl")),
                    image_width=_positive_int(item.get("imageWidth")),
                    image_height=_positive_int(item.get("imageHeight")),
                    thumbnail_width=_positive_int(item.get("thumbnailWidth")),
                    thumbnail_height=_positive_int(item.get("thumbnailHeight")),
                )
            )
            if len(rows) >= selected_limit:
                break
        return rows

    async def get_video_search(
        self,
        query: str,
        country: str = "us",
        language: str = "en",
        limit: int = 10,
        page: int = 1,
        autocorrect: bool = True,
        **kwargs: Any,
    ) -> list[VideoSearchResult]:
        del kwargs
        selected_query = query.strip()
        if not selected_query:
            raise ValueError("Serper videos query 不能为空")
        selected_country = _normalize_country(country)
        selected_language = _normalize_language(language)
        selected_limit = max(1, min(int(limit), 100))
        selected_page = max(1, min(int(page), 10))
        raw = await self.client.request(
            SerperSearchType.VIDEOS,
            query=selected_query,
            country=selected_country,
            language=selected_language,
            limit=selected_limit,
            page=selected_page,
            autocorrect=bool(autocorrect),
        )
        values = raw.get("videos", [])
        if not isinstance(values, list):
            raise InternalProviderResponseError(
                "Serper videos 字段格式错误",
                provider="serper",
                debug_context={"videos_type": type(values).__name__},
            )
        fetched_at = datetime.now(UTC)
        rows = []
        for index, item in enumerate(values, start=1):
            if not isinstance(item, Mapping):
                continue
            title = _text(item.get("title"))
            url = _text(item.get("link"))
            if not title or not url:
                continue
            position = _positive_int(item.get("position")) or (
                (selected_page - 1) * selected_limit + index
            )
            if not 1 <= position <= 100:
                continue
            duration_text = _text(item.get("duration"))
            published_text = _text(item.get("date"))
            rows.append(
                VideoSearchResult(
                    source=DataSource.SERPER,
                    fetched_at=fetched_at,
                    data_date=fetched_at.date(),
                    is_estimated=False,
                    integrity=DataIntegrity.COMPLETE,
                    position=position,
                    title=title,
                    url=url,
                    snippet=_text(item.get("snippet")),
                    thumbnail_url=_text(item.get("imageUrl")),
                    source_name=_text(item.get("source")),
                    channel=_text(item.get("channel")),
                    duration_text=duration_text,
                    duration_seconds=_parse_duration_seconds(duration_text),
                    published_text=published_text,
                    published_at=_parse_absolute_datetime(published_text),
                )
            )
            if len(rows) >= selected_limit:
                break
        return rows


def _normalize_country(value: str) -> str:
    country = value.strip().lower()
    if len(country) != 2 or not country.isalpha():
        raise ValueError("Serper country 必须是 ISO 3166-1 两位国家代码")
    return country


def _normalize_language(value: str) -> str:
    language = value.strip().lower().replace("_", "-").split("-", 1)[0]
    if len(language) != 2 or not language.isalpha():
        raise ValueError("Serper language 必须是两位语言代码")
    return language


def _rank(value: Any, *, page: int, limit: int, index: int) -> int | None:
    try:
        rank = int(value)
    except (TypeError, ValueError):
        rank = (page - 1) * limit + index
    return rank if 1 <= rank <= 100 else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _suggestion_value(item: Any) -> str | None:
    if isinstance(item, Mapping):
        return _text(item.get("value"))
    return _text(item)


def _normalize_suggestion(value: str) -> str:
    return " ".join(value.lower().split())


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _parse_absolute_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    lowered = value.lower()
    relative_tokens = (
        "ago",
        "yesterday",
        "today",
        "hour",
        "day",
        "week",
        "month",
        "year",
        "小时前",
        "天前",
        "周前",
        "个月前",
        "年前",
        "昨天",
        "今天",
    )
    if any(token in lowered for token in relative_tokens):
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_duration_seconds(value: str | None) -> int | None:
    if not value:
        return None
    parts = value.strip().split(":")
    if len(parts) not in {2, 3} or any(not part.isdigit() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    if any(number >= 60 for number in numbers[-2:]):
        return None
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
