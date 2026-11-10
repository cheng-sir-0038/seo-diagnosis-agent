"""受站点边界保护的页面 HTML 获取客户端。"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from aiohttp.abc import AbstractResolver
from aiohttp.resolver import DefaultResolver

from src.core.exceptions import (
    ErrorCode,
    InternalAdapterError,
    InternalDataNotAvailableError,
    InternalProviderRequestError,
    InternalProviderResponseError,
)
from src.providers.page_metadata.config import PageMetadataConfig


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global and not address.is_multicast


def _fake_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value) in ipaddress.ip_network("198.18.0.0/15")
    except ValueError:
        return False


def _query_public_dns(host: str, record_type: str) -> list[str]:
    # shortcut: one public DNS service; add configurable providers if it is unavailable.
    query = urlencode({"name": host, "type": record_type})
    request = Request(
        f"https://dns.google/resolve?{query}", headers={"Accept": "application/dns-json"}
    )
    with urlopen(request, timeout=5) as response:
        body = response.read(65_537)
    if len(body) > 65_536:
        raise OSError("公网 DNS 响应超过大小限制")
    data = json.loads(body)
    if not isinstance(data, dict) or data.get("Status") != 0:
        raise OSError("公网 DNS 查询失败")
    expected = 1 if record_type == "A" else 28
    return [
        str(item["data"])
        for item in data.get("Answer", [])
        if isinstance(item, dict) and item.get("type") == expected and "data" in item
    ]


class _PublicResolver(AbstractResolver):
    """Return only validated addresses to aiohttp, avoiding a second DNS lookup."""

    def __init__(self) -> None:
        self._resolver = DefaultResolver()

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[dict[str, Any]]:
        records = await self._resolver.resolve(host, port, family)
        addresses = [record["host"] for record in records]
        if addresses and all(_fake_ip(address) for address in addresses):
            answers = await asyncio.gather(
                *(asyncio.to_thread(_query_public_dns, host, kind) for kind in ("A", "AAAA"))
            )
            addresses = list(dict.fromkeys(address for group in answers for address in group))
            records = [
                {
                    "hostname": host, "host": address, "port": port,
                    "family": socket.AF_INET6 if ":" in address else socket.AF_INET,
                    "proto": 0, "flags": 0,
                }
                for address in addresses
            ]
        if not addresses or any(not _public_address(address) for address in addresses):
            raise OSError("页面目标必须解析为公网地址")
        return records

    async def close(self) -> None:
        await self._resolver.close()


@dataclass(frozen=True, slots=True)
class FetchedHtmlPage:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str | None
    content_length_bytes: int
    headers: dict[str, str]
    html: str


class PageMetadataClient:
    def __init__(self, config: PageMetadataConfig | None = None) -> None:
        self.config = config or PageMetadataConfig()
        self._session: Any = None
        self._resolver: _PublicResolver | None = None
        self._request_count = 0

    async def startup(self) -> None:
        import aiohttp

        if self._session is not None:
            return
        self._resolver = _PublicResolver()
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(resolver=self._resolver, use_dns_cache=False),
            timeout=aiohttp.ClientTimeout(total=self.config.request_timeout),
            headers={
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
                "User-Agent": self.config.user_agent,
            },
        )

    async def shutdown(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        if self._resolver is not None:
            await self._resolver.close()
            self._resolver = None

    async def fetch(self, *, site_id: str, url: str | None = None) -> FetchedHtmlPage:
        if self._session is None:
            await self.startup()
        assert self._session is not None

        requested = normalize_site_url(url or site_id)
        site_host = normalized_host(site_id)
        if not site_host or normalized_host(requested) != site_host:
            raise _invalid("页面元信息只允许检测当前站点域名下的页面", requested)

        current = requested
        for redirect_index in range(self.config.max_redirects + 1):
            self._request_count += 1
            try:
                async with self._session.get(current, allow_redirects=False) as response:
                    status = int(response.status)
                    if 300 <= status < 400 and response.headers.get("Location"):
                        if redirect_index >= self.config.max_redirects:
                            raise InternalProviderResponseError(
                                "页面重定向次数超过限制",
                                provider="page_html",
                                debug_context={"requested_url": requested},
                            )
                        next_url = normalize_site_url(
                            urljoin(current, response.headers["Location"])
                        )
                        if normalized_host(next_url) != site_host:
                            raise _invalid("页面重定向到了当前站点以外的域名", next_url)
                        current = next_url
                        continue

                    content_type = response.headers.get("Content-Type")
                    media_type = (content_type or "").split(";", 1)[0].strip().lower()
                    if media_type not in {"text/html", "application/xhtml+xml"}:
                        raise InternalDataNotAvailableError(
                            "目标 URL 返回的不是 HTML 页面",
                            provider="page_html",
                            debug_context={"status": status, "content_type": content_type},
                        )
                    body = await _read_limited(response, self.config.max_response_bytes)
                    charset = response.charset or "utf-8"
                    try:
                        html = body.decode(charset, errors="replace")
                    except LookupError:
                        html = body.decode("utf-8", errors="replace")

                    refresh_target = _meta_refresh_target(html, current)
                    if refresh_target:
                        if redirect_index >= self.config.max_redirects:
                            raise InternalProviderResponseError(
                                "页面客户端跳转次数超过限制",
                                provider="page_html",
                                debug_context={"requested_url": requested},
                            )
                        next_url = normalize_site_url(refresh_target)
                        if normalized_host(next_url) != site_host:
                            raise _invalid("页面客户端跳转到了当前站点以外的域名", next_url)
                        current = next_url
                        continue

                    return FetchedHtmlPage(
                        requested_url=requested,
                        final_url=current,
                        status_code=status,
                        content_type=content_type,
                        content_length_bytes=len(body),
                        headers={str(k).lower(): str(v) for k, v in response.headers.items()},
                        html=html,
                    )
            except InternalAdapterError:
                raise
            except Exception as exc:
                raise InternalProviderRequestError(
                    f"页面 HTML 请求失败: {exc}",
                    provider="page_html",
                    debug_context={"requested_url": requested},
                ) from exc
        raise InternalProviderResponseError("页面重定向处理失败", provider="page_html")


async def _read_limited(response: Any, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > limit:
            raise InternalProviderResponseError(
                "页面 HTML 响应超过大小限制",
                provider="page_html",
                debug_context={"max_response_bytes": limit},
            )
        chunks.append(bytes(chunk))
    return b"".join(chunks)


class _MetaRefreshParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.target: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta" or self.target:
            return
        values = {str(key).lower(): (value or "") for key, value in attrs}
        if values.get("http-equiv", "").strip().lower() != "refresh":
            return
        content = values.get("content", "")
        for part in content.split(";"):
            key, separator, value = part.partition("=")
            if separator and key.strip().lower() == "url":
                self.target = value.strip().strip("\"'") or None
                return


def _meta_refresh_target(html: str, current_url: str) -> str | None:
    parser = _MetaRefreshParser()
    parser.feed(html[:100_000])
    parser.close()
    return urljoin(current_url, parser.target) if parser.target else None


def normalize_site_url(value: str) -> str:
    text = str(value).strip()
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlparse(text)
    try:
        port = parsed.port
    except ValueError as exc:
        raise _invalid("页面 URL 必须是有效的 HTTP/HTTPS 站内地址", text) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or port not in {None, 80, 443}
    ):
        raise _invalid("页面 URL 必须是有效的 HTTP/HTTPS 站内地址", text)
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(
        (".localhost", ".local", ".internal", ".lan", ".home.arpa")
    ):
        raise _invalid("页面 URL 必须是公网地址", text)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise _invalid("页面 URL 必须是公网地址", text) from None
    else:
        if not _public_address(host):
            raise _invalid("页面 URL 必须是公网地址", text)
    return parsed._replace(path=parsed.path or "/", fragment="").geturl()


def normalized_host(value: str) -> str:
    text = str(value).strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    return (urlparse(text).hostname or "").lower().removeprefix("www.")


def _invalid(message: str, url: str) -> InternalAdapterError:
    return InternalAdapterError(
        message,
        error_code=ErrorCode.INVALID_PARAMS,
        provider="page_html",
        debug_context={"url": url},
    )
