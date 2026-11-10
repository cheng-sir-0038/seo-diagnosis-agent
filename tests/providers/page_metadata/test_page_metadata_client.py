from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from src.core.exceptions import (
    InternalAdapterError,
    InternalDataNotAvailableError,
    InternalProviderResponseError,
)
from src.providers.page_metadata.client import PageMetadataClient
from src.providers.page_metadata.config import PageMetadataConfig


class FakeContent:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        yield self.body


class FakeResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes = b"") -> None:
        self.status = status
        self.headers = headers
        self.content = FakeContent(body)
        self.charset = "utf-8"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def get(self, url: str, **_kwargs):
        self.urls.append(url)
        return self.responses.pop(0)

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_client_follows_only_same_site_redirects() -> None:
    client = PageMetadataClient(PageMetadataConfig(max_redirects=2))
    client._session = FakeSession(
        [
            FakeResponse(301, {"Location": "/home", "Content-Type": "text/html"}),
            FakeResponse(200, {"Content-Type": "text/html"}, b"<title>Home</title>"),
        ]
    )

    page = await client.fetch(site_id="example.com", url="https://www.example.com/")

    assert page.final_url == "https://www.example.com/home"
    assert page.html == "<title>Home</title>"


@pytest.mark.asyncio
async def test_client_rejects_cross_site_redirect_and_credentials() -> None:
    client = PageMetadataClient(PageMetadataConfig(max_redirects=2))
    client._session = FakeSession(
        [
            FakeResponse(302, {"Location": "https://other.example/", "Content-Type": "text/html"}),
        ]
    )

    with pytest.raises(InternalAdapterError, match="以外"):
        await client.fetch(site_id="example.com", url="https://example.com/")
    with pytest.raises(InternalAdapterError, match="有效"):
        await client.fetch(site_id="example.com", url="https://user:pass@example.com/")


@pytest.mark.asyncio
async def test_client_follows_same_site_meta_refresh() -> None:
    client = PageMetadataClient(PageMetadataConfig(max_redirects=2))
    client._session = FakeSession(
        [
            FakeResponse(
                200,
                {"Content-Type": "text/html"},
                b'<meta http-equiv="refresh" content="0;url=http://www.example.com/home">',
            ),
            FakeResponse(200, {"Content-Type": "text/html"}, b"<title>Home</title>"),
        ]
    )

    page = await client.fetch(site_id="example.com", url="https://example.com/")

    assert page.final_url == "http://www.example.com/home"
    assert page.html == "<title>Home</title>"


@pytest.mark.asyncio
async def test_client_rejects_non_html_and_oversized_body() -> None:
    client = PageMetadataClient(PageMetadataConfig(max_response_bytes=5))
    client._session = FakeSession([FakeResponse(200, {"Content-Type": "application/json"}, b"{}")])
    with pytest.raises(InternalDataNotAvailableError, match="不是 HTML"):
        await client.fetch(site_id="example.com")

    client._session = FakeSession([FakeResponse(200, {"Content-Type": "text/html"}, b"123456")])
    with pytest.raises(InternalProviderResponseError, match="大小限制"):
        await client.fetch(site_id="example.com")
