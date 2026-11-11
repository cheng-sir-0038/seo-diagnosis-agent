"""
Ahrefs API 客户端

职责:
  1. 管理 aiohttp session 生命周期
  2. 发送 HTTP 请求，自动附带 Authorization 头
  3. 速率限制控制（Token Bucket / Semaphore）
  4. 指数退避重试（429 / 5xx）
  5. HTTP 错误 → InternalXxxError 转换
  6. API 响应 → dict 解析 + 字段完整性校验
  7. 自动上报 API 用量日志

真实实现替换指引:
  1. 将 _PSEUDO_RESPONSES 中的伪数据替换为真实 aiohttp 调用
  2. 接入 asyncio.Semaphore 或 token-bucket 做精确速率控制
  3. 接入真实的 settings.ahrefs_api_key
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC
from typing import Any

from loguru import logger

from src.core.config import settings
from src.core.exceptions import (
    InternalAuthError,
    InternalProviderRequestError,
    InternalProviderServerError,
    InternalRateLimitError,
)
from src.providers.ahrefs.config import ENDPOINTS, AhrefsConfig, AhrefsEndpoint


class AhrefsClient:
    """Ahrefs API v3 异步客户端。

    封装所有 HTTP 通信细节，Provider 模块只调用此类的方法，
    不直接操作 aiohttp。

    使用方式::

        client = AhrefsClient()
        await client.startup()

        data = await client.request("site_explorer_metrics", params={
            "target": "example.com",
            "country": "us",
        })

        await client.shutdown()

    Attributes:
        config: Ahrefs 配置。
        _session: aiohttp ClientSession（延迟初始化）。
        _semaphore: 速率控制信号量。
        _cache: 本地请求缓存 dict（短期，与 Redis 互补）。
    """

    def __init__(self, config: AhrefsConfig | None = None) -> None:
        self.config = config or AhrefsConfig()

        # ---- 伪代码阶段使用内存替代 ----
        self._session: Any = None  # 真实: aiohttp.ClientSession
        self._semaphore = asyncio.Semaphore(self.config.rate_limit_rpm)
        self._cache: dict[str, dict[str, Any]] = {}  # 短期本地缓存

        # ---- 用量统计（本地计数器） ----
        self._units_consumed: int = 0
        self._request_count: int = 0

    # ============================================================
    # 生命周期
    # ============================================================

    async def startup(self) -> None:
        """初始化 HTTP 会话和速率控制器。

        真实实现::

            self._session = aiohttp.ClientSession(
                base_url=self.config.api_base_url,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=self.config.request_timeout),
            )
        """
        logger.info(
            f"AhrefsClient 初始化 | "
            f"base_url={self.config.api_base_url} | "
            f"rate_limit={self.config.rate_limit_rpm} rpm"
        )
        # PSEUDO: 真实实现时取消下面的注释
        # self._session = aiohttp.ClientSession(...)

    async def shutdown(self) -> None:
        """关闭 HTTP 会话。"""
        # PSEUDO: 真实实现时取消下面的注释
        # if self._session:
        #     await self._session.close()
        logger.info(
            f"AhrefsClient 已关闭 | "
            f"总请求: {self._request_count} | "
            f"估计消耗: {self._units_consumed} units"
        )

    # ============================================================
    # 核心请求方法
    # ============================================================

    async def request(
        self,
        endpoint_key: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """发送 API 请求（入口方法）。

        完整调用链::

            request()
              ├── _check_cache()         ← 检查本地短期缓存
              ├── _acquire_rate_limit()  ← 等待速率令牌
              ├── _do_request()          ← 发送 HTTP 请求（含重试）
              │     ├── _handle_response()  ← 状态码处理
              │     └── _parse_body()       ← 响应体解析
              ├── _update_cache()        ← 更新缓存
              └── _log_usage()           ← 记录用量

        Args:
            endpoint_key: ENDPOINTS 字典中的端点 key，如 "site_explorer_metrics"。
            params: URL 查询参数。
            body: POST 请求体（仅 batch_analysis 等 POST 端点使用）。
            force_refresh: 是否强制跳过缓存。

        Returns:
            API 响应体（已解析为 dict）。

        Raises:
            InternalAuthError: 授权失败。
            InternalRateLimitError: 被限流。
            InternalProviderRequestError: 网络错误。
            InternalProviderServerError: 上游 5xx。
            InternalProviderResponseError: 响应格式异常。
        """
        endpoint = ENDPOINTS.get(endpoint_key)
        if endpoint is None:
            raise InternalProviderRequestError(
                f"未知的 Ahrefs 端点: {endpoint_key}",
                provider="ahrefs",
                debug_context={"endpoint_key": endpoint_key},
            )

        # Step 1: 检查缓存
        if not force_refresh:
            cache_key = self._cache_key(endpoint_key, params or {}, body or {})
            cached = self._check_cache(cache_key)
            if cached is not None:
                logger.debug(f"Cache HIT: {endpoint_key}")
                return cached

        # Step 2: 速率控制
        await self._acquire_rate_limit()

        # Step 3: 发送请求（含重试）
        result = await self._do_request_with_retry(endpoint, params, body)

        # Step 4: 更新缓存
        if not force_refresh:
            self._update_cache(cache_key, result)

        # Step 5: 用量记录
        self._log_usage(endpoint, success=True)

        return result

    # ============================================================
    # 缓存（本地短期，与 Redis 互补）
    # ============================================================

    def _cache_key(self, endpoint_key: str, params: dict, body: dict) -> str:
        """生成本地缓存 key。"""
        canonical = json.dumps(
            {"endpoint": endpoint_key, "params": params, "body": body},
            sort_keys=True,
            default=str,
        )
        return f"ahrefs_local:{hashlib.sha256(canonical.encode()).hexdigest()[:16]}"

    def _check_cache(self, key: str) -> dict | None:
        """检查本地缓存。TTL 基于 settings.cache_default_ttl。"""
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.time() - entry["_cached_at"] > settings.cache_default_ttl:
            del self._cache[key]
            return None
        return entry["data"]

    def _update_cache(self, key: str, data: dict) -> None:
        """写入本地缓存。"""
        self._cache[key] = {"data": data, "_cached_at": time.time()}

    # ============================================================
    # 速率控制
    # ============================================================

    async def _acquire_rate_limit(self) -> None:
        """获取速率令牌。

        真实实现:
          使用 asyncio.Semaphore + 时间窗口计数器。
          也可以接入更精确的 token bucket 算法。

        当前伪代码: 简单 delay 模拟。
        """
        # PSEUDO: 真实实现改为 semaphore + token bucket
        self._request_count += 1
        delay = settings.ahrefs_rate_limit_delay
        if delay > 0:
            await asyncio.sleep(delay * 0.1)  # 伪代码只 sleep 10%

    # ============================================================
    # HTTP 请求（含重试）
    # ============================================================

    async def _do_request_with_retry(
        self,
        endpoint: AhrefsEndpoint,
        params: dict | None,
        body: dict | None,
    ) -> dict[str, Any]:
        """发送 HTTP 请求，含指数退避重试。

        重试策略（仅对 429/5xx 重试）:
          - 第 1 次重试: 等待 2 秒
          - 第 2 次重试: 等待 4 秒
          - 第 3 次重试: 等待 8 秒
          - 超过 max_retries: 抛出异常

        Args:
            endpoint: 端点定义。
            params: 查询参数。
            body: 请求体。

        Returns:
            解析后的响应 dict。

        Raises:
            各种 InternalXxxError。
        """
        last_error: Exception | None = None

        for attempt in range(self.config.max_retries + 1):
            try:
                return await self._do_request(endpoint, params, body)
            except InternalRateLimitError as e:
                last_error = e
                wait = self.config.retry_backoff_base * (2 ** attempt)
                logger.warning(
                    f"Ahrefs 速率限制 (attempt {attempt + 1}/{self.config.max_retries + 1}) "
                    f"→ 等待 {wait}s"
                )
                await asyncio.sleep(wait)
            except InternalProviderServerError as e:
                last_error = e
                if attempt < self.config.max_retries:
                    wait = self.config.retry_backoff_base * (2 ** attempt)
                    logger.warning(
                        f"Ahrefs 服务端错误 (attempt {attempt + 1}) → 等待 {wait}s 后重试"
                    )
                    await asyncio.sleep(wait)
                else:
                    raise
            except (InternalAuthError, InternalProviderRequestError):
                # 这些错误重试无意义，直接抛出
                raise

        # 重试耗尽
        raise last_error  # type: ignore[misc]

    async def _do_request(
        self,
        endpoint: AhrefsEndpoint,
        params: dict | None,
        body: dict | None,
    ) -> dict[str, Any]:
        """执行单次 HTTP 请求（伪代码）。

        真实实现::

            url = f"{self.config.api_base_url}{endpoint.path}"
            if endpoint.method == "GET":
                resp = await self._session.get(url, params=params)
            else:
                resp = await self._session.post(url, params=params, json=body)
            return await self._handle_response(resp, endpoint)

        当前伪代码:
          返回与目标数据结构近似的模拟数据，用于验证全链路。
          当 self.config.api_key 配置为真实 Key 后，替换为真实 aiohttp 调用。
        """
        logger.debug(
            f"Ahrefs API 请求 | {endpoint.method} {endpoint.path} | "
            f"params={params} | body={body}"
        )

        # ============================================================
        # PSEUDO: 真实实现 ← 替换下面的模拟逻辑
        # ============================================================
        return self._pseudo_response(endpoint, params or {})

    # ============================================================
    # 响应处理（真实实现）
    # ============================================================

    async def _handle_response(self, response: Any, endpoint: AhrefsEndpoint) -> dict[str, Any]:
        """处理 HTTP 响应（伪代码占位）。

        真实实现::

            status = response.status
            if status == 401 or status == 403:
                raise auth_error("ahrefs", status, await response.text())
            if status == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                raise rate_limit_error("ahrefs", retry_after, dict(response.headers))
            if status >= 500:
                raise upstream_error("ahrefs", endpoint.path, status, await response.text())
            if status == 402 or "quota" in (await response.text()).lower():
                raise InternalQuotaExhaustedError(...)

            data = await response.json()
            if not isinstance(data, dict):
                raise InternalProviderResponseError(...)

            return data
        """
        # PSEUDO: 真实实现时取消注释上面的逻辑
        return {}

    # ============================================================
    # 伪数据生成（开发阶段 — 接入真实 API Key 后全部删除）
    # ============================================================

    def _pseudo_response(self, endpoint: AhrefsEndpoint, params: dict) -> dict[str, Any]:
        """生成伪响应数据，用于链路验证。

        TODO: 接入真实 Ahrefs API Key 后，删除此方法及其所有调用。
        """
        from datetime import date, datetime

        today = date.today()
        now = datetime.now(UTC).isoformat()

        pseudo_templates: dict[str, dict] = {
            "site_explorer_metrics": {
                "domain_rating": 68.5,
                "ahrefs_rank": 45230,
                "organic_traffic_estimate": 125000,
                "traffic_value_estimate": 89400.0,
                "organic_keywords_count": 38420,
                "top_3_count": 1230,
                "top_10_count": 8200,
                "ranked_pages_count": 5100,
                "country": {"code": params.get("country", "us"), "name": "美国"},
                "protocol": "https",
                "source": "ahrefs",
                "fetched_at": now,
                "data_date": {"start": str(today), "end": str(today), "label": "今日"},
                "is_estimated": True,
            },
            "site_explorer_top_pages": {
                "pages": [
                    {
                        "url": f"https://{params.get('target', 'example.com')}/blog/seo-guide",
                        "title": "Complete SEO Guide 2026",
                        "organic_traffic_estimate": 12800,
                        "traffic_change": -0.15,
                        "keywords_count": 3420,
                        "top_keyword": "seo guide",
                        "backlinks_count": 890,
                        "refdomains_count": 245,
                    },
                    {
                        "url": (
                            f"https://{params.get('target', 'example.com')}"
                            "/tools/keyword-research"
                        ),
                        "title": "Free Keyword Research Tool",
                        "organic_traffic_estimate": 9600,
                        "traffic_change": 0.08,
                        "keywords_count": 2100,
                        "top_keyword": "keyword research tool",
                        "backlinks_count": 1560,
                        "refdomains_count": 412,
                    },
                ],
                "source": "ahrefs",
                "fetched_at": now,
                "is_estimated": True,
            },
            "site_audit_health": {
                "health_score": 87.3,
                "total_urls_crawled": 15230,
                "urls_with_errors": 320,
                "urls_with_warnings": 1450,
                "urls_with_notices": 3100,
                "crawl_finished_at": now,
                "previous_crawl_finished_at": now,
                "project_verified": True,
                "source": "ahrefs",
                "fetched_at": now,
                "is_estimated": False,
            },
            "subscription_info": {
                "plan_name": "Ahrefs Advanced",
                "api_units_limit": 500000,
                "api_units_used": 235000,
                "api_units_remaining": 265000,
                "usage_percentage": 47.0,
                "reset_date": str(today.replace(day=1)),
                "rate_limit_per_minute": 60,
                "source": "ahrefs",
                "fetched_at": now,
                "is_estimated": False,
            },
        }

        # 返回匹配的模板，或通用空响应
        template = pseudo_templates.get(
            self._match_template(endpoint, params),
            {
                "message": f"PSEUDO response for {endpoint.path}",
                "params": params,
                "source": "ahrefs",
                "fetched_at": now,
                "is_estimated": True,
            },
        )
        return template

    def _match_template(self, endpoint: AhrefsEndpoint, params: dict) -> str:
        """匹配伪数据模板 key。"""
        path_to_template: dict[str, str] = {
            "site-explorer/metrics": "site_explorer_metrics",
            "site-explorer/top-pages": "site_explorer_top_pages",
            "site-audit/projects": "site_audit_health",
            "subscription-info/limits-and-usage": "subscription_info",
        }
        for path_fragment, template_key in path_to_template.items():
            if path_fragment in endpoint.path:
                return template_key
        return ""

    # ============================================================
    # 用量日志
    # ============================================================

    def _log_usage(self, endpoint: AhrefsEndpoint, success: bool = True) -> None:
        """记录 API 用量。"""
        self._units_consumed += endpoint.estimated_units
        logger.debug(
            f"Ahrefs 用量 | {endpoint.path} | "
            f"~{endpoint.estimated_units} units | "
            f"累计: {self._units_consumed}"
        )
