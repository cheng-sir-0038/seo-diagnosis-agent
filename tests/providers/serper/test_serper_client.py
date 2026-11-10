from __future__ import annotations

import pytest

from src.core.exceptions import (
    InternalAuthError,
    InternalConfigError,
    InternalProviderResponseError,
    InternalQuotaExhaustedError,
    InternalRateLimitError,
)
from src.providers.serper.client import SerperClient
from src.providers.serper.config import SerperConfig


class FakeResponse:
    def __init__(
        self,
        status: int,
        *,
        payload: object | None = None,
        body: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.payload = payload
        self.body = body
        self.headers = headers or {}

    async def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    async def text(self) -> str:
        return self.body


class FakeContextManager:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    async def __aenter__(self) -> FakeResponse:
        return self.response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict, dict[str, str]]] = []

    def post(
        self,
        endpoint: str,
        *,
        json: dict,
        headers: dict[str, str],
    ) -> FakeContextManager:
        self.calls.append((endpoint, json, headers))
        return FakeContextManager(self.response)


@pytest.mark.asyncio
async def test_client_requires_api_key_before_network_request() -> None:
    client = SerperClient(SerperConfig(api_key=""))

    with pytest.raises(InternalConfigError, match="API Key"):
        await client.search(query="seo agent", country="us", language="en", limit=10)


@pytest.mark.asyncio
async def test_client_posts_search_contract_without_key_in_url() -> None:
    client = SerperClient(
        SerperConfig(
            api_base_url="https://google.serper.dev/",
            api_key="super-secret",
            max_retries=0,
        )
    )
    session = FakeSession(FakeResponse(200, payload={"organic": []}))
    client._session = session

    result = await client.search(
        query="seo agent",
        country="gb",
        language="en",
        limit=20,
        page=2,
        autocorrect=False,
    )

    assert result == {"organic": []}
    endpoint, payload, headers = session.calls[0]
    assert endpoint == "https://google.serper.dev/search"
    assert "super-secret" not in endpoint
    assert payload == {
        "q": "seo agent",
        "gl": "gb",
        "hl": "en",
        "num": 20,
        "page": 2,
        "autocorrect": False,
    }
    assert headers == {
        "X-API-KEY": "super-secret",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


@pytest.mark.asyncio
async def test_client_maps_auth_quota_and_rate_limit_errors() -> None:
    client = SerperClient(SerperConfig(api_key="secret"))

    with pytest.raises(InternalAuthError):
        await client._handle_response(FakeResponse(403, body="invalid key"))
    with pytest.raises(InternalQuotaExhaustedError):
        await client._handle_response(FakeResponse(402, body="not enough credits"))
    with pytest.raises(InternalRateLimitError) as limited:
        await client._handle_response(FakeResponse(429, headers={"Retry-After": "9"}))
    assert limited.value.retry_after == 9


@pytest.mark.asyncio
async def test_client_rejects_invalid_success_payload() -> None:
    client = SerperClient(SerperConfig(api_key="secret"))

    with pytest.raises(InternalProviderResponseError, match="JSON"):
        await client._handle_response(FakeResponse(200, payload=ValueError("invalid")))
    with pytest.raises(InternalProviderResponseError, match="organic"):
        await client._handle_response(FakeResponse(200, payload={"organic": {}}))


def test_endpoint_registry_covers_all_supported_serper_search_types() -> None:
    from src.providers.serper.config import ENDPOINTS, SerperSearchType

    assert set(ENDPOINTS) == set(SerperSearchType)
    assert {
        search_type.value: (endpoint.path, endpoint.result_field)
        for search_type, endpoint in ENDPOINTS.items()
    } == {
        "search": ("/search", "organic"),
        "images": ("/images", "images"),
        "news": ("/news", "news"),
        "maps": ("/maps", "places"),
        "places": ("/places", "places"),
        "videos": ("/videos", "videos"),
        "shopping": ("/shopping", "shopping"),
        "scholar": ("/scholar", "organic"),
        "patents": ("/patents", "organic"),
        "autocomplete": ("/autocomplete", "suggestions"),
    }


@pytest.mark.asyncio
async def test_generic_request_uses_vertical_endpoint_and_parameter_contract() -> None:
    from src.providers.serper.config import SerperSearchType

    client = SerperClient(
        SerperConfig(api_base_url="https://google.serper.dev/", api_key="secret", max_retries=0)
    )
    session = FakeSession(FakeResponse(200, payload={"images": []}))
    client._session = session

    result = await client.request(
        SerperSearchType.IMAGES,
        query="seo dashboard",
        country="gb",
        language="en",
        limit=12,
        page=2,
        autocorrect=False,
    )

    assert result == {"images": []}
    endpoint, payload, headers = session.calls[0]
    assert endpoint == "https://google.serper.dev/images"
    assert payload == {
        "q": "seo dashboard",
        "gl": "gb",
        "hl": "en",
        "num": 12,
        "page": 2,
        "autocorrect": False,
    }
    assert headers["X-API-KEY"] == "secret"
    assert "secret" not in endpoint


@pytest.mark.asyncio
async def test_maps_request_allows_location_and_autocomplete_omits_unsupported_fields() -> None:
    from src.providers.serper.config import SerperSearchType

    maps_client = SerperClient(SerperConfig(api_key="secret", max_retries=0))
    maps_session = FakeSession(FakeResponse(200, payload={"places": []}))
    maps_client._session = maps_session

    await maps_client.request(
        SerperSearchType.MAPS,
        query="seo agency",
        country="gb",
        language="en",
        limit=20,
        page=1,
        autocorrect=True,
        location="London, England, United Kingdom",
    )

    _, maps_payload, _ = maps_session.calls[0]
    assert maps_payload["location"] == "London, England, United Kingdom"
    assert maps_payload["num"] == 20

    autocomplete = SerperClient(SerperConfig(api_key="secret", max_retries=0))
    autocomplete_session = FakeSession(FakeResponse(200, payload={"suggestions": []}))
    autocomplete._session = autocomplete_session

    await autocomplete.request(
        SerperSearchType.AUTOCOMPLETE,
        query="seo agent",
        country="us",
        language="en",
        limit=99,
        page=8,
        autocorrect=False,
    )

    endpoint, payload, _ = autocomplete_session.calls[0]
    assert endpoint == "https://google.serper.dev/autocomplete"
    assert payload == {"q": "seo agent", "gl": "us", "hl": "en"}


@pytest.mark.asyncio
async def test_vertical_response_validates_registered_result_field_but_allows_no_results() -> None:
    from src.providers.serper.config import ENDPOINTS, SerperSearchType

    client = SerperClient(SerperConfig(api_key="secret"))

    assert await client._handle_response(
        FakeResponse(200, payload={"searchParameters": {"q": "seo"}}),
        ENDPOINTS[SerperSearchType.NEWS],
    ) == {"searchParameters": {"q": "seo"}}

    with pytest.raises(InternalProviderResponseError, match="images"):
        await client._handle_response(
            FakeResponse(200, payload={"images": {"bad": "shape"}}),
            ENDPOINTS[SerperSearchType.IMAGES],
        )


@pytest.mark.asyncio
async def test_unknown_search_type_is_rejected_before_network() -> None:
    from src.core.exceptions import InternalProviderRequestError

    client = SerperClient(SerperConfig(api_key="secret", max_retries=0))
    session = FakeSession(FakeResponse(200, payload={}))
    client._session = session

    with pytest.raises(InternalProviderRequestError, match="未知的 Serper 搜索类型"):
        await client.request(
            "unknown",
            query="seo",
            country="us",
            language="en",
        )

    assert session.calls == []


def test_search_endpoint_keeps_legacy_base_url_with_search_suffix() -> None:
    client = SerperClient(
        SerperConfig(
            api_base_url="https://google.serper.dev/search",
            api_key="secret",
        )
    )

    assert client.endpoint == "https://google.serper.dev/search"
