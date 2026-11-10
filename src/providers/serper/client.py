"""Serper Google Search API 异步客户端。"""

from __future__ import annotations

import asyncio
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
from src.providers.serper.config import (
    ENDPOINTS,
    SerperConfig,
    SerperEndpoint,
    SerperSearchType,
)


class SerperClient:
    """封装 Serper 多搜索类型的 HTTP、授权、重试和错误转换。"""

    def __init__(self, config: SerperConfig | None = None) -> None:
        self.config = config or SerperConfig()
        self._session: Any = None
        self._request_count = 0

    @property
    def endpoint(self) -> str:
        """兼容原普通 Search 端点属性。"""
        return self.endpoint_for(SerperSearchType.SEARCH)

    def endpoint_for(self, search_type: SerperSearchType | str) -> str:
        selected = self._resolve_search_type(search_type)
        base = self.config.api_base_url.rstrip("/")
        path = ENDPOINTS[selected].path
        if selected == SerperSearchType.SEARCH and base.endswith(path):
            return base
        return f"{base}{path}"

    async def startup(self) -> None:
        if not self.config.api_key or self._session is not None:
            return
        import aiohttp

        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config.request_timeout),
        )
        logger.info("SerperClient 初始化 | base_url={}", self.config.api_base_url.rstrip("/"))

    async def shutdown(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        logger.info("SerperClient 已关闭 | 总请求={}", self._request_count)

    async def request(
        self,
        search_type: SerperSearchType | str,
        *,
        query: str,
        country: str,
        language: str,
        limit: int | None = None,
        page: int = 1,
        autocorrect: bool = True,
        location: str | None = None,
    ) -> dict[str, Any]:
        selected = self._resolve_search_type(search_type)
        endpoint = ENDPOINTS[selected]
        if not self.config.api_key:
            raise InternalConfigError(
                "Serper API Key 未配置",
                provider="serper",
                debug_context={"configuration": "SERPER_API_KEY"},
            )
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("Serper query 不能为空")
        if location and not endpoint.supports_location:
            raise InternalProviderRequestError(
                f"Serper {selected.value} 不支持 location 参数",
                provider="serper",
                debug_context={"search_type": selected.value},
            )
        if self._session is None:
            await self.startup()
        assert self._session is not None

        payload: dict[str, Any] = {
            "q": normalized_query,
            "gl": country,
            "hl": language,
        }
        if endpoint.supports_num and limit is not None:
            payload["num"] = max(1, min(int(limit), 100))
        if endpoint.supports_page:
            payload["page"] = max(1, int(page))
        if endpoint.supports_autocorrect:
            payload["autocorrect"] = bool(autocorrect)
        if endpoint.supports_location and location:
            payload["location"] = location.strip()

        headers = {
            "X-API-KEY": self.config.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._request_count += 1
        request_context = {
            "search_type": selected.value,
            "country": country,
            "language": language,
            "limit": limit,
            "page": page,
            "has_location": bool(location),
        }

        last_error: InternalAdapterError | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                async with self._session.post(
                    self.endpoint_for(selected),
                    json=payload,
                    headers=headers,
                ) as response:
                    return await self._handle_response(response, endpoint)
            except (InternalAuthError, InternalQuotaExhaustedError):
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
                    "Serper API 请求超时",
                    provider="serper",
                    debug_context=request_context,
                ) from exc
            except Exception as exc:
                raise InternalProviderRequestError(
                    f"Serper 网络请求失败: {type(exc).__name__}",
                    provider="serper",
                    debug_context=request_context,
                ) from exc
        raise last_error or RuntimeError("Serper request failed")

    async def search(
        self,
        *,
        query: str,
        country: str,
        language: str,
        limit: int,
        page: int = 1,
        autocorrect: bool = True,
    ) -> dict[str, Any]:
        """兼容普通 Google Search 入口。"""
        return await self.request(
            SerperSearchType.SEARCH,
            query=query,
            country=country,
            language=language,
            limit=limit,
            page=page,
            autocorrect=autocorrect,
        )

    async def _handle_response(
        self,
        response: Any,
        endpoint: SerperEndpoint | None = None,
    ) -> dict[str, Any]:
        selected_endpoint = endpoint or ENDPOINTS[SerperSearchType.SEARCH]
        status = response.status
        if 200 <= status < 300:
            try:
                payload = await response.json()
            except Exception as exc:
                raise InternalProviderResponseError(
                    "Serper 响应 JSON 解析失败",
                    provider="serper",
                    debug_context={"status": status},
                ) from exc
            if not isinstance(payload, dict):
                raise InternalProviderResponseError(
                    "Serper 响应必须是 JSON 对象",
                    provider="serper",
                    debug_context={"status": status},
                )
            result = payload.get(selected_endpoint.result_field, [])
            if not isinstance(result, list):
                raise InternalProviderResponseError(
                    f"Serper 响应 {selected_endpoint.result_field} 字段格式错误",
                    provider="serper",
                    debug_context={
                        "status": status,
                        "result_field": selected_endpoint.result_field,
                        "result_type": type(result).__name__,
                    },
                )
            return payload

        body = (await response.text())[:1200]
        context = {"status": status, "body": body}
        if status in {401, 403}:
            raise InternalAuthError(
                f"Serper API 授权失败（HTTP {status}）",
                provider="serper",
                debug_context=context,
            )
        if status == 402:
            raise InternalQuotaExhaustedError(
                "Serper API 查询额度已耗尽",
                provider="serper",
                debug_context=context,
            )
        if status == 429:
            retry_after_raw = response.headers.get("Retry-After", "60")
            try:
                retry_after = int(retry_after_raw)
            except (TypeError, ValueError):
                retry_after = 60
            raise InternalRateLimitError(
                "Serper API 请求频率过高",
                retry_after=retry_after,
                provider="serper",
                debug_context=context,
            )
        if status >= 500:
            raise InternalProviderServerError(
                f"Serper API 服务异常（HTTP {status}）",
                provider="serper",
                debug_context=context,
            )
        raise InternalProviderRequestError(
            f"Serper API 请求失败（HTTP {status}）",
            provider="serper",
            debug_context=context,
        )

    @staticmethod
    def _resolve_search_type(value: SerperSearchType | str) -> SerperSearchType:
        if isinstance(value, SerperSearchType):
            return value
        try:
            return SerperSearchType(str(value).strip().lower())
        except ValueError as exc:
            raise InternalProviderRequestError(
                f"未知的 Serper 搜索类型: {value}",
                provider="serper",
                debug_context={"search_type": str(value)},
            ) from exc
