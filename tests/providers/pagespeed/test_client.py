from __future__ import annotations

import pytest

from src.core.exceptions import (
    InternalAuthError,
    InternalConfigError,
    InternalProviderResponseError,
    InternalQuotaExhaustedError,
    InternalRateLimitError,
)
from src.providers.pagespeed.client import PageSpeedClient
from src.providers.pagespeed.config import PageSpeedConfig


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


@pytest.mark.asyncio
async def test_client_requires_api_key_before_network_request() -> None:
    client = PageSpeedClient(PageSpeedConfig(api_key=""))

    with pytest.raises(InternalConfigError, match="API Key") as captured:
        await client.request(
            url="https://example.com/",
            strategy="mobile",
            categories=["performance"],
            locale="zh-CN",
        )

    assert "test" not in str(captured.value.debug_context)


@pytest.mark.asyncio
async def test_client_maps_auth_rate_limit_and_quota_errors() -> None:
    client = PageSpeedClient(PageSpeedConfig(api_key="secret-key"))

    with pytest.raises(InternalAuthError):
        await client._handle_response(FakeResponse(403, body="API key invalid"))
    with pytest.raises(InternalQuotaExhaustedError):
        await client._handle_response(
            FakeResponse(403, body='{"reason":"dailyLimitExceeded","message":"quota"}')
        )
    with pytest.raises(InternalRateLimitError) as limited:
        await client._handle_response(FakeResponse(429, headers={"Retry-After": "12"}))
    assert limited.value.retry_after == 12


@pytest.mark.asyncio
async def test_client_rejects_invalid_success_payload() -> None:
    client = PageSpeedClient(PageSpeedConfig(api_key="secret-key"))

    with pytest.raises(InternalProviderResponseError, match="lighthouseResult"):
        await client._handle_response(FakeResponse(200, payload={"id": "x"}))
    with pytest.raises(InternalProviderResponseError, match="JSON"):
        await client._handle_response(FakeResponse(200, payload=ValueError("invalid")))


def test_client_endpoint_and_debug_paths_do_not_include_api_key() -> None:
    client = PageSpeedClient(
        PageSpeedConfig(
            api_base_url="https://pagespeedonline.googleapis.com/pagespeedonline/v5",
            api_key="super-secret",
        )
    )
    assert client.endpoint.endswith("/runPagespeed")
    assert "super-secret" not in client.endpoint


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
        self.calls: list[tuple[str, list[tuple[str, str]]]] = []

    def get(self, endpoint: str, *, params: list[tuple[str, str]]) -> FakeContextManager:
        self.calls.append((endpoint, params))
        return FakeContextManager(self.response)


@pytest.mark.asyncio
async def test_client_sends_repeated_categories_and_key_only_to_google_request() -> None:
    client = PageSpeedClient(
        PageSpeedConfig(
            api_key="super-secret",
            max_retries=0,
            rate_limit_rpm=100,
        )
    )
    session = FakeSession(FakeResponse(200, payload={"lighthouseResult": {}}))
    client._session = session

    response = await client.request(
        url="https://example.com/",
        strategy="mobile",
        categories=["performance", "accessibility", "best-practices", "seo"],
        locale="zh-CN",
    )

    assert response == {"lighthouseResult": {}}
    endpoint, params = session.calls[0]
    assert endpoint.endswith("/runPagespeed")
    assert [value for key, value in params if key == "category"] == [
        "performance",
        "accessibility",
        "best-practices",
        "seo",
    ]
    assert ("key", "super-secret") in params
    assert "super-secret" not in endpoint
