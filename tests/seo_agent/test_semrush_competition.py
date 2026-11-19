from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr

import src.facade.data_facade as facade_module
from src.core.cache import CacheManager
from src.core.config import settings
from src.core.exceptions import ErrorCode, InternalAdapterError, InternalDataNotAvailableError
from src.facade.data_facade import DataFacade
from src.models.common import Capability
from src.providers.semrush import SemrushProvider
from src.seo_agent.chat.tools import build_seo_agent_registry

SITE = {"domain": "trailguide.example", "default_country": "US", "default_language": "en-US"}
WORKFLOW = "seo_semrush_competition"
API_PREFIX = "/apis/v4/domain-analytics/v1/"


def report_response(request: httpx.Request) -> httpx.Response:
    report = request.url.path.removeprefix(API_PREFIX)
    month = request.url.params.get("month", "2026-10")
    meta = {
        "success": True, "status_code": 200, "country": "US", "device_type": "DESKTOP",
        "effective_url": "https://trailguide.example", "month": month,
    }
    if report == "domain-summaries/monthly":
        data = {
            "rank": 81024, "month": month,
            "database": {"country": "US", "device": "DESKTOP"},
            "metrics": {
                "organic_traffic": 18642, "organic_positions": 3840,
                "organic_positions_1_3": 96, "organic_positions_4_10": 274,
                "organic_traffic_cost": {"value": 12411.60, "currency": "USD"},
            },
        }
    elif report == "organic-search/keywords/monthly":
        data = [
            {"keyword": "day hike essentials", "position": 8, "position_previous": 13,
             "position_difference": 5, "volume": 2900, "traffic": 171,
             "url": "https://trailguide.example/gear/day-hike-essentials",
             "cpc": {"value": 1.42, "currency": "USD"}, "intents": ["INFORMATIONAL"]},
            {"keyword": "how to size a hiking backpack", "position": 9, "position_previous": 7,
             "position_difference": -2, "volume": 1300, "traffic": 66,
             "url": "https://trailguide.example/gear/backpack-sizing"},
            {"keyword": "beginner hiking trails", "position": 15, "volume": 1000, "traffic": 22,
             "url": "https://trailguide.example/trails/beginner"},
        ]
    elif report == "organic-search/competitors/monthly":
        data = [
            {"domain": "alltrails.com", "common_keywords": 817,
             "competition_density": 28.6, "organic_traffic": 486710},
            {"domain": "hikingproject.com", "common_keywords": 603,
             "competition_density": 20.2, "organic_traffic": 66280},
            {"domain": "rei.com", "common_keywords": 408,
             "competition_density": 12.7, "organic_traffic": 3143880},
        ]
    else:
        raise AssertionError(f"unexpected Semrush path: {request.url.path}")
    return httpx.Response(200, json={"meta": meta, "data": data})


@pytest_asyncio.fixture
async def semrush(monkeypatch) -> AsyncIterator[tuple[SemrushProvider, list[httpx.Request]]]:
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return report_response(request)

    client = httpx.AsyncClient
    provider = SemrushProvider()
    with monkeypatch.context() as patch:
        patch.setattr(settings, "semrush_api_key", SecretStr("local-contract-key"))
        patch.setattr(httpx, "AsyncClient", lambda **kwargs: client(
            transport=httpx.MockTransport(respond), **kwargs,
        ))
        await provider.startup()
    try:
        yield provider, calls
    finally:
        await provider.shutdown()


@pytest.mark.asyncio
async def test_semrush_workflow_uses_three_v4_reports_for_the_same_market_and_month(
    monkeypatch, semrush,
) -> None:
    provider, calls = semrush
    facade = DataFacade()
    facade._providers["semrush"] = provider
    facade._capability_map = {capability: [provider] for capability in provider.capabilities}
    monkeypatch.setattr(facade_module, "cache_manager", CacheManager())

    registry = build_seo_agent_registry(facade, SITE)
    assert registry.names() == [WORKFLOW]
    result = await registry.get(WORKFLOW).execute({})

    assert result.success
    assert result.data["workflow_status"] == "complete"
    assert result.data["provenance"]["source"] == "semrush"
    assert len(result.data["items"]) == 7
    assert result.data["month"] == "2026-10"
    evidence = {key: item["raw"]["data"] for key, item in result.data["evidence"].items()}
    assert evidence["overview"]["traffic_value_estimate"] == 12411.60
    assert evidence["overview"]["top_10_count"] == 370
    assert evidence["keywords"][0]["position_change"] == 5
    assert evidence["keywords"][2]["previous_position"] is None
    assert evidence["competitors"][0]["common_keywords_count"] == 817
    assert evidence["competitors"][0]["competition_density"] == 28.6

    assert [request.url.path.removeprefix(API_PREFIX) for request in calls] == [
        "domain-summaries/monthly", "organic-search/keywords/monthly",
        "organic-search/competitors/monthly",
    ]
    assert all(request.url.params["country"] == "US" for request in calls)
    assert all(request.url.params["device_type"] == "DESKTOP" for request in calls)
    assert all(request.headers["Authorization"] == "Apikey local-contract-key" for request in calls)
    assert calls[0].url.params["scope"] == "ROOT_DOMAIN"
    assert calls[1].url.params["position_type"] == "ORGANIC"
    assert [request.url.params["limit"] for request in calls[1:]] == ["3", "3"]
    assert [request.url.params["month"] for request in calls[1:]] == ["2026-10", "2026-10"]
    assert all("key" not in request.url.params for request in calls)


@pytest.mark.asyncio
async def test_semrush_requires_explicit_selection_and_rejects_language_filter(semrush) -> None:
    provider, calls = semrush
    facade = DataFacade()
    facade._capability_map[Capability.ORGANIC_KEYWORDS] = [provider]
    with pytest.raises(InternalDataNotAvailableError):
        await facade.query_live(Capability.ORGANIC_KEYWORDS, SITE["domain"])
    with pytest.raises(InternalAdapterError) as error:
        await facade.query_live(Capability.ORGANIC_KEYWORDS, SITE["domain"], {
            "preferred_provider": "semrush", "country": "US", "language": "zh-CN",
        })
    assert error.value.error_code == ErrorCode.INVALID_PARAMS
    assert calls == []
    assert build_seo_agent_registry(DataFacade(), SITE).names() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "code"), [
    (400, ErrorCode.INVALID_PARAMS), (401, ErrorCode.AUTH_FAILED),
    (402, ErrorCode.QUOTA_EXHAUSTED), (429, ErrorCode.RATE_LIMITED),
    (500, ErrorCode.UPSTREAM_ERROR),
])
async def test_semrush_keeps_provider_error_meaning(status: int, code: ErrorCode) -> None:
    provider = SemrushProvider()
    async with httpx.AsyncClient(
        base_url="https://api.semrush.com/",
        transport=httpx.MockTransport(lambda _: httpx.Response(status)),
    ) as client:
        provider._client = client
        with pytest.raises(InternalAdapterError) as error:
            await provider.get_search_metrics(SITE["domain"])
    assert error.value.error_code == code


@pytest.mark.asyncio
async def test_semrush_refuses_mismatched_market_and_accepts_empty_rankings() -> None:
    provider = SemrushProvider()

    def respond(request: httpx.Request) -> httpx.Response:
        report = report_response(request).json()
        report["data"] = []
        return httpx.Response(200, json=report)

    async with httpx.AsyncClient(
        base_url=f"https://api.semrush.com{API_PREFIX}", transport=httpx.MockTransport(respond),
    ) as client:
        provider._client = client
        assert await provider.get_organic_keywords(SITE["domain"], "US") == []
        with pytest.raises(InternalAdapterError) as error:
            await provider.get_organic_keywords(SITE["domain"], "CA")
    assert error.value.error_code == ErrorCode.UPSTREAM_ERROR


@pytest.mark.asyncio
async def test_semrush_workflow_stops_after_quota_rejection() -> None:
    provider = SemrushProvider()
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(402)

    async with httpx.AsyncClient(
        base_url=f"https://api.semrush.com{API_PREFIX}", transport=httpx.MockTransport(respond),
    ) as client:
        provider._client = client
        facade = DataFacade()
        facade._providers["semrush"] = provider
        facade._capability_map = {capability: [provider] for capability in provider.capabilities}
        result = await build_seo_agent_registry(facade, SITE).get(WORKFLOW).execute({})

    assert not result.success
    assert result.error_code == "QUOTA_EXHAUSTED"
    assert len(requests) == 1
    assert result.data["coverage"]["keywords"]["status"] == "skipped"
    assert result.data["coverage"]["competitors"]["status"] == "skipped"
