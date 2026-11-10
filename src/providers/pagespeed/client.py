"""Google PageSpeed Insights API v5 异步客户端。"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from loguru import logger

from src.core.exceptions import (
    InternalAdapterError,
    InternalAuthError,
    InternalConfigError,
    InternalProviderRequestError,
    InternalProviderResponseError,
    InternalProviderServerError,
    InternalQuotaExhaustedError,
    InternalRateLimitError,
)
from src.providers.pagespeed.config import PageSpeedConfig


class PageSpeedClient:
    """只负责真实 HTTP、限流、重试和上游错误转换。"""

    def __init__(self, config: PageSpeedConfig | None = None) -> None:
        self.config = config or PageSpeedConfig()
        self._session: Any = None
        self._request_count = 0
        self._request_times: deque[float] = deque()
        self._rate_lock = asyncio.Lock()

    @property
    def endpoint(self) -> str:
        base = self.config.api_base_url.rstrip("/")
        return base if base.endswith("/runPagespeed") else f"{base}/runPagespeed"

    async def startup(self) -> None:
        if not self.config.api_key or self._session is not None:
            return
        import aiohttp

        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config.request_timeout),
            headers={"Accept": "application/json"},
        )
        logger.info(
            "PageSpeedClient 初始化 | base_url={} | rate_limit={} rpm",
            self.config.api_base_url,
            self.config.rate_limit_rpm,
        )

    async def shutdown(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        logger.info("PageSpeedClient 已关闭 | 总请求={}", self._request_count)

    async def request(
        self,
        *,
        url: str,
        strategy: str,
        categories: list[str],
        locale: str,
    ) -> dict[str, Any]:
        if not self.config.api_key:
            raise InternalConfigError(
                "PageSpeed API Key 未配置",
                provider="pagespeed",
                debug_context={"configuration": "PAGESPEED_API_KEY"},
            )
        if self._session is None:
            await self.startup()
        assert self._session is not None

        await self._respect_rate_limit()
        params: list[tuple[str, str]] = [
            ("url", url),
            ("strategy", strategy),
            ("locale", locale),
            ("key", self.config.api_key),
        ]
        params.extend(("category", category) for category in categories)
        self._request_count += 1

        last_error: InternalAdapterError | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                async with self._session.get(self.endpoint, params=params) as response:
                    return await self._handle_response(response)
            except InternalAuthError:
                raise
            except InternalQuotaExhaustedError:
                raise
            except (InternalRateLimitError, InternalProviderServerError) as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    raise
                await asyncio.sleep(2**attempt)
            except InternalAdapterError:
                raise
            except TimeoutError as exc:
                raise InternalProviderRequestError(
                    "PageSpeed API 请求超时",
                    provider="pagespeed",
                    debug_context={"strategy": strategy, "categories": categories},
                ) from exc
            except Exception as exc:
                raise InternalProviderRequestError(
                    f"PageSpeed 网络请求失败: {type(exc).__name__}",
                    provider="pagespeed",
                    debug_context={"strategy": strategy, "categories": categories},
                ) from exc
        raise last_error or RuntimeError("PageSpeed request failed")

    async def _respect_rate_limit(self) -> None:
        limit = max(1, self.config.rate_limit_rpm)
        async with self._rate_lock:
            now = time.monotonic()
            while self._request_times and now - self._request_times[0] >= 60:
                self._request_times.popleft()
            if len(self._request_times) >= limit:
                wait_for = 60 - (now - self._request_times[0])
                await asyncio.sleep(max(0, wait_for))
                now = time.monotonic()
                while self._request_times and now - self._request_times[0] >= 60:
                    self._request_times.popleft()
            self._request_times.append(time.monotonic())

    async def _handle_response(self, response: Any) -> dict[str, Any]:
        status = response.status
        if 200 <= status < 300:
            try:
                payload = await response.json()
            except Exception as exc:
                raise InternalProviderResponseError(
                    "PageSpeed 响应 JSON 解析失败",
                    provider="pagespeed",
                    debug_context={"status": status},
                ) from exc
            if not isinstance(payload, dict) or not isinstance(
                payload.get("lighthouseResult"), dict
            ):
                raise InternalProviderResponseError(
                    "PageSpeed 响应缺少 lighthouseResult",
                    provider="pagespeed",
                    debug_context={
                        "status": status,
                        "keys": list(payload) if isinstance(payload, dict) else [],
                    },
                )
            return payload

        body = (await response.text())[:1200]
        body_lower = body.lower()
        context = {"status": status, "body": body}
        if status in {401, 403}:
            if any(token in body_lower for token in ("dailylimitexceeded", "quota", "rate limit")):
                raise InternalQuotaExhaustedError(
                    "PageSpeed API 配额已耗尽",
                    provider="pagespeed",
                    debug_context=context,
                )
            raise InternalAuthError(
                f"PageSpeed API 授权失败（HTTP {status}）",
                provider="pagespeed",
                debug_context=context,
            )
        if status == 429:
            retry_after_raw = response.headers.get("Retry-After", "60")
            try:
                retry_after = int(retry_after_raw)
            except (TypeError, ValueError):
                retry_after = 60
            raise InternalRateLimitError(
                "PageSpeed API 请求频率过高",
                retry_after=retry_after,
                provider="pagespeed",
                debug_context=context,
            )
        if status >= 500:
            raise InternalProviderServerError(
                f"PageSpeed API 服务异常（HTTP {status}）",
                provider="pagespeed",
                debug_context=context,
            )
        raise InternalProviderRequestError(
            f"PageSpeed API 请求失败（HTTP {status}）",
            provider="pagespeed",
            debug_context=context,
        )
