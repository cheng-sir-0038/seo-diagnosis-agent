from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import Field, ValidationError

from src.core.config import settings
from src.core.exceptions import (
    ErrorCode,
    InternalAdapterError,
    InternalAuthError,
    InternalConfigError,
    InternalDataNotAvailableError,
    InternalProviderRequestError,
    InternalProviderResponseError,
    InternalQuotaExhaustedError,
    InternalRateLimitError,
)
from src.models.common import Capability, DataSource
from src.models.competitor import OrganicCompetitor
from src.models.search import OrganicKeyword, SearchMetrics
from src.providers.base import BaseProvider, ProviderMeta, ProviderMode


class SemrushSearchMetrics(SearchMetrics):
    semrush_rank: int | None = Field(default=None, ge=1)


class SemrushOrganicCompetitor(OrganicCompetitor):
    competition_density: float | None = Field(default=None, ge=0, le=100)


def _hostname(value: str) -> str:
    try:
        parsed = urlsplit(value if "://" in value else f"//{value}")
    except ValueError:
        return ""
    return (parsed.hostname or "").removeprefix("www.").rstrip(".")


def _usd(value: Any) -> float | None:
    if isinstance(value, dict) and value.get("currency") == "USD":
        return value.get("value")
    return None


class SemrushProvider(BaseProvider):
    def __init__(self) -> None:
        super().__init__()
        self._client: httpx.AsyncClient | None = None

    @property
    def meta(self) -> ProviderMeta:
        return ProviderMeta("semrush", "Semrush SEO API v4", DataSource.SEMRUSH, {
            Capability.SEARCH_METRICS, Capability.ORGANIC_KEYWORDS, Capability.ORGANIC_COMPETITORS,
        })

    @property
    def runtime_mode(self) -> ProviderMode:
        return "live" if self._client is not None else "unavailable"

    async def startup(self) -> None:
        key = settings.semrush_api_key.get_secret_value().strip()
        if key:
            self._client = httpx.AsyncClient(
                base_url="https://api.semrush.com/apis/v4/domain-analytics/v1/",
                headers={"Authorization": f"Apikey {key}"},
                timeout=10,
            )

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok" if self._client is not None else "degraded",
                "mode": self.runtime_mode, "auth_valid": None}

    async def _report(
        self, path: str, site_id: str, country: str, language: str | None,
        *, month: str | None = None, **params: Any,
    ) -> tuple[Any, date | None]:
        if self._client is None:
            raise InternalConfigError("缺少 Semrush v4 API Key", provider="semrush")
        domain = _hostname(site_id or "")
        country = country.strip().upper()
        valid_country = len(country) == 2 and country.isascii() and country.isalpha()
        if not domain or language or not valid_country:
            raise InternalAdapterError(
                "Semrush 按地区提供根域的桌面搜索数据。请指定有效域名和市场，语言留空。",
                error_code=ErrorCode.INVALID_PARAMS, provider="semrush",
            )
        target = {"domain": domain} if path.startswith("organic-search/competitors/") else {
            "url": domain, "scope": "ROOT_DOMAIN",
        }
        query = {**target, "country": country, "device_type": "DESKTOP", **params}
        if month:
            query["month"] = month
        try:
            response = await self._client.get(path, params=query)
        except httpx.RequestError:
            raise InternalProviderRequestError("Semrush 网络请求失败", provider="semrush") from None

        status = response.status_code
        if status in {401, 403}:
            raise InternalAuthError("Semrush v4 未接受凭据或报告权限", provider="semrush")
        if status == 402:
            raise InternalQuotaExhaustedError("Semrush API 额度不足", provider="semrush")
        if status == 429:
            raise InternalRateLimitError("Semrush 请求频率过高", provider="semrush")
        if status == 404:
            raise InternalDataNotAvailableError("Semrush 未返回该范围的数据", provider="semrush")
        if status in {400, 422}:
            raise InternalAdapterError(
                f"Semrush 拒绝了 {country} 桌面市场请求，请核对该市场和域名。",
                error_code=ErrorCode.INVALID_PARAMS, provider="semrush",
            )
        if not response.is_success:
            raise InternalProviderResponseError(
                f"Semrush 请求失败（HTTP {status}）", provider="semrush",
            )

        try:
            payload = response.json()
            meta = payload["meta"]
            response_target = meta.get("effective_url") or meta.get("url") or meta.get("domain")
            report_month = meta.get("month")
            if (
                meta["success"] is not True
                or str(meta["country"]).upper() != country
                or meta["device_type"] != "DESKTOP"
                or (month and report_month != month)
                or not response_target or _hostname(response_target) != domain
            ):
                raise ValueError("report scope mismatch")
            report_date = date.fromisoformat(f"{report_month}-01") if report_month else None
            return payload["data"], report_date
        except (AttributeError, KeyError, TypeError, ValueError):
            raise InternalProviderResponseError(
                "Semrush 响应与受检域名、市场或报告格式不符", provider="semrush",
            ) from None

    async def get_search_metrics(
        self, site_id: str, country: str = "us", **kwargs: Any,
    ) -> SearchMetrics:
        data, month = await self._report(
            "domain-summaries/monthly", site_id, country, kwargs.get("language"),
            month=kwargs.get("month"),
            fields="rank,month,organic_traffic,organic_traffic_cost,organic_positions,"
                   "organic_positions_1_3,organic_positions_4_10",
        )
        if not data or isinstance(data, dict) and data.get("metrics") == {}:
            raise InternalDataNotAvailableError("Semrush 未返回站点概览指标", provider="semrush")
        try:
            metrics = data["metrics"]
            return SemrushSearchMetrics(
                source=self.source, data_date=month, country={"code": country.upper()},
                semrush_rank=data.get("rank"), organic_traffic_estimate=metrics["organic_traffic"],
                traffic_value_estimate=_usd(metrics.get("organic_traffic_cost")),
                organic_keywords_count=metrics["organic_positions"],
                top_3_count=metrics["organic_positions_1_3"],
                top_10_count=metrics["organic_positions_1_3"] + metrics["organic_positions_4_10"],
            )
        except (AttributeError, KeyError, TypeError, ValidationError):
            raise InternalProviderResponseError(
                "Semrush 站点概览字段缺失或无效", provider="semrush",
            ) from None

    async def get_organic_keywords(
        self, site_id: str, country: str = "us", limit: int | None = None, **kwargs: Any,
    ) -> list[OrganicKeyword]:
        limit = max(1, min(3, limit if limit is not None else 3))
        data, month = await self._report(
            "organic-search/keywords/monthly", site_id, country, kwargs.get("language"),
            month=kwargs.get("month"),
            position_type="ORGANIC", limit=limit, order_by="traffic", direction="DESC",
            fields="keyword,position,position_previous,position_difference,volume,url,"
                   "traffic,keyword_difficulty,cpc,intents",
        )
        if not isinstance(data, list):
            raise InternalProviderResponseError("Semrush 自然排名列表格式无效", provider="semrush")
        try:
            return [OrganicKeyword(
                source=self.source, data_date=month,
                keyword=row["keyword"], position=row["position"],
                previous_position=row.get("position_previous"),
                position_change=row.get("position_difference"), search_volume=row["volume"],
                keyword_difficulty=row.get("keyword_difficulty"), cpc=_usd(row.get("cpc")),
                traffic_estimate=row.get("traffic"), url=row.get("url"),
                intent=", ".join(value.lower() for value in row.get("intents") or []) or None,
            ) for row in data[:limit]]
        except (AttributeError, KeyError, TypeError, ValidationError):
            raise InternalProviderResponseError(
                "Semrush 自然排名字段缺失或无效", provider="semrush",
            ) from None

    async def get_organic_competitors(
        self, site_id: str, country: str = "us", limit: int | None = None, **kwargs: Any,
    ) -> list[OrganicCompetitor]:
        limit = max(1, min(3, limit if limit is not None else 3))
        data, month = await self._report(
            "organic-search/competitors/monthly", site_id, country, kwargs.get("language"),
            month=kwargs.get("month"),
            limit=limit, order_by="common_keywords", direction="DESC",
            fields="domain,common_keywords,competition_density,organic_traffic",
        )
        if not isinstance(data, list):
            raise InternalProviderResponseError("Semrush 竞品列表格式无效", provider="semrush")
        try:
            return [SemrushOrganicCompetitor(
                source=self.source, data_date=month, domain=row["domain"],
                common_keywords_count=row["common_keywords"],
                competition_density=row.get("competition_density"),
                organic_traffic_estimate=row.get("organic_traffic"),
            ) for row in data[:limit] if _hostname(row["domain"]) != _hostname(site_id)]
        except (AttributeError, KeyError, TypeError, ValidationError):
            raise InternalProviderResponseError(
                "Semrush 竞品字段缺失或无效", provider="semrush",
            ) from None
