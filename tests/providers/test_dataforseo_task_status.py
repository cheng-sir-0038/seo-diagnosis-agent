from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.facade.data_facade as facade_module
from src.core.cache import CacheManager
from src.core.exceptions import (
    ErrorCode,
    InternalAdapterError,
    InternalDataNotAvailableError,
    InternalProviderResponseError,
)
from src.facade.data_facade import DataFacade
from src.models.common import Capability
from src.providers.dataforseo.client import DataForSEOClient
from src.providers.dataforseo.provider import DataForSEOProvider
from src.seo_agent.chat.tools import build_seo_agent_registry


def http_response(response: dict) -> MagicMock:
    http = SimpleNamespace(status=200, json=AsyncMock(return_value=response))
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=http)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def provider_for(response: dict, *, locations: list[dict] | None = None) -> DataForSEOProvider:
    if locations is None:
        locations = [
            {
                "country_iso_code": country,
                "location_code": code,
                "available_languages": [
                    {"language_code": language, "available_sources": ["google"]}
                    for language in languages
                ],
            }
            for country, code, languages in [
                ("US", 2840, ["en"]),
                ("SG", 2702, ["en", "zh-CN"]),
                ("HK", 2344, ["en", "zh-TW"]),
            ]
        ]
    provider = DataForSEOProvider()
    provider.client = DataForSEOClient()
    catalog = {"status_code": 20000, "tasks": [{"status_code": 20000, "result": locations}]}
    provider.client._session = SimpleNamespace(
        get=MagicMock(return_value=http_response(catalog)),
        post=MagicMock(return_value=http_response(response)),
    )
    return provider


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [40501, 40503, 40400, 40402])
async def test_http_200_task_error_is_not_a_successful_empty_ranking(status: int):
    provider = provider_for(
        {
            "status_code": 20000,
            "tasks_error": 1,
            "tasks": [
                {
                    "status_code": status,
                    "status_message": "Invalid Field: 'location_name'.",
                    "result": None,
                }
            ],
        }
    )
    with pytest.raises(InternalProviderResponseError) as exc:
        await provider.get_organic_keywords("example.com")
    assert exc.value.debug_context["task_status"] == status
    assert exc.value.error_code == ErrorCode.UPSTREAM_ERROR
    assert all(
        entry["data"]["tasks"][0]["status_code"] == 20000
        for entry in provider.client._cache.values()
    )
    assert provider.client._session.post.call_count == 1


@pytest.mark.asyncio
async def test_http_200_envelope_error_is_not_a_successful_empty_ranking():
    provider = provider_for(
        {"status_code": 40503, "status_message": "POST Data Is Invalid.", "tasks": []}
    )
    with pytest.raises(InternalProviderResponseError):
        await provider.get_organic_keywords("example.com")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [20000, 40102])
async def test_legitimate_empty_rankings_remain_empty(status: int):
    provider = provider_for(
        {
            "status_code": 20000,
            "tasks": [
                {"status_code": status, "status_message": "No Search Results.", "result": None}
            ],
        }
    )
    assert await provider.get_organic_keywords("example.com") == []


def empty_items_response(items=None) -> dict:
    return {
        "status_code": 20000,
        "tasks": [{
            "status_code": 20000,
            "result": [{"target": "example.com", "items_count": 0, "items": items}],
        }],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("country", "language", "location_code", "language_code"), [
    ("SG", "zh-CN", 2702, "zh-CN"),
    ("sg", "zh", 2702, "zh-CN"),
    ("hk", "ZH_tw", 2344, "zh-TW"),
    ("us", "en-US", 2840, "en"),
])
async def test_ranked_keywords_keep_requested_market_through_facade(
    monkeypatch, country, language, location_code, language_code,
):
    provider = provider_for(empty_items_response())
    facade = DataFacade()
    facade._capability_map[Capability.ORGANIC_KEYWORDS] = [provider]
    monkeypatch.setattr(facade_module, "cache_manager", CacheManager())
    params = {"country": country, "language": language, "limit": 3}

    result = await facade.query_live(Capability.ORGANIC_KEYWORDS, "example.com", params)
    await facade.query_live(Capability.ORGANIC_KEYWORDS, "example.org", params)

    session = provider.client._session
    session.get.assert_called_once_with(
        provider.client.config.api_base_url + "/v3/dataforseo_labs/locations_and_languages"
    )
    assert result["data"] == []
    assert session.post.call_count == 2
    for call in session.post.call_args_list:
        task = call.kwargs["json"][0]
        assert task["location_code"] == location_code
        assert task["language_code"] == language_code
        assert task["item_types"] == ["organic"]
        assert "location_name" not in task


@pytest.mark.asyncio
@pytest.mark.parametrize(("capability", "endpoint", "items"), [
    (Capability.SEARCH_METRICS, "domain_rank_overview", [{"metrics": {"organic": {"count": 12}}}]),
    (Capability.KEYWORD_OVERVIEW, "keyword_overview", []),
    (Capability.ORGANIC_COMPETITORS, "competitors_domain", []),
])
async def test_other_labs_queries_keep_selected_language(capability, endpoint, items):
    provider = provider_for(empty_items_response(items))

    await DataFacade()._dispatch(provider, capability, "example.com", {
        "country": "SG", "language": "zh-CN", "keywords": ["样本"],
    })

    call = provider.client._session.post.call_args
    assert call.args[0].endswith(f"/{endpoint}/live")
    assert call.kwargs["json"][0]["location_code"] == 2702
    assert call.kwargs["json"][0]["language_code"] == "zh-CN"


@pytest.mark.asyncio
@pytest.mark.parametrize(("country", "language", "reason"), [
    ("CN", "zh-CN", "CN 市场"),
    ("SG", "zh-TW", "zh-CN"),
])
async def test_unsupported_labs_market_stops_before_paid_query(country, language, reason):
    provider = provider_for(empty_items_response())
    with pytest.raises(InternalAdapterError, match=reason) as exc:
        await provider.get_organic_keywords("example.com", country=country, language=language)
    assert exc.value.error_code == ErrorCode.INVALID_PARAMS
    provider.client._session.post.assert_not_called()


@pytest.mark.asyncio
async def test_search_diagnosis_explains_unsupported_labs_market():
    provider = provider_for(empty_items_response())
    facade = DataFacade()
    facade._capability_map = {
        Capability.SEARCH_METRICS: [provider], Capability.ORGANIC_KEYWORDS: [provider],
    }
    registry = build_seo_agent_registry(facade, {"domain": "example.com"})
    workflow = registry.get("seo_search_diagnosis")

    result = await workflow.execute({"country": "CN", "language": "zh-CN"})

    assert result.data["workflow_status"] == "partial"
    for step in result.data["coverage"].values():
        assert step["error_code"] == "INVALID_PARAMS"
        assert "不覆盖 CN 市场" in step["reason"]
    provider.client._session.get.assert_called_once()
    provider.client._session.post.assert_not_called()


@pytest.mark.asyncio
async def test_empty_labs_catalog_is_an_upstream_error():
    provider = provider_for(empty_items_response(), locations=[])
    with pytest.raises(InternalProviderResponseError):
        await provider.get_organic_keywords("example.com")
    provider.client._session.post.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("items", [None, []])
@pytest.mark.parametrize("method,args", [
    ("get_organic_keywords", ["example.com"]),
    ("get_keyword_overview", [["unknown topic"]]),
    ("get_organic_competitors", ["example.com"]),
    ("get_backlinks", ["example.com"]),
])
async def test_successful_nested_empty_items_are_not_upstream_errors(items, method, args):
    provider = provider_for(empty_items_response(items))
    assert await getattr(provider, method)(*args) == []
    assert provider.client._session.post.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("items", [None, []])
async def test_empty_domain_overview_has_no_metrics_instead_of_zero_or_upstream_error(items):
    provider = provider_for(empty_items_response(items))
    with pytest.raises(InternalDataNotAvailableError) as exc:
        await provider.get_search_metrics("example.com")
    assert exc.value.error_code == ErrorCode.DATA_NOT_AVAILABLE


@pytest.mark.asyncio
@pytest.mark.parametrize("items", [{"keyword": "unexpected object"}, "unexpected string"])
async def test_malformed_nested_items_are_still_response_errors(items):
    provider = provider_for(empty_items_response(items))
    with pytest.raises(InternalProviderResponseError):
        await provider.get_organic_keywords("example.com")
