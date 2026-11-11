from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import (
    ErrorCode,
    InternalDataNotAvailableError,
    InternalProviderResponseError,
)
from src.providers.dataforseo.client import DataForSEOClient
from src.providers.dataforseo.provider import DataForSEOProvider


def provider_for(response: dict) -> DataForSEOProvider:
    provider = DataForSEOProvider()
    provider.client = DataForSEOClient()
    http = SimpleNamespace(status=200, json=AsyncMock(return_value=response))
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=http)
    context.__aexit__ = AsyncMock(return_value=False)
    provider.client._session = SimpleNamespace(post=MagicMock(return_value=context))
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
        await provider.get_organic_keywords("example.com", country="cn", language="zh")
    assert exc.value.debug_context["task_status"] == status
    assert exc.value.error_code == ErrorCode.UPSTREAM_ERROR
    assert provider.client._cache == {}
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
