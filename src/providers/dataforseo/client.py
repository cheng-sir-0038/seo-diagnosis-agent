"""
DataForSEO API v3 异步客户端

职责:
  1. 管理 aiohttp session 生命周期
  2. HTTP Basic Auth 认证（login:api_key → Base64）
  3. 发送 POST JSON Array 请求（DataForSEO 统一请求格式）
  4. 速率限制控制（Semaphore）
  5. 指数退避重试（429 / 5xx）
  6. 异步任务轮询（非 live 端点）
  7. HTTP 错误 → InternalXxxError 转换
  8. 分页支持（offset + limit + search_after_token）
  9. 每请求 cost 追踪

与 AhrefsClient 的关键差异:
  - 认证: Basic Auth vs Bearer Token
  - 请求: 全部 POST JSON Array vs GET/POST 混合
  - 响应: {tasks: [{result: [...]}]} vs 直接的 dict
  - 任务: 非 live 端点需 POST → 轮询 → 获取结果

使用方式::

    client = DataForSEOClient()
    await client.startup()

    data = await client.request("ranked_keywords", task_body={
        "target": "example.com",
        "location_name": "United States",
        "language_name": "English",
        "limit": 10,
    })

    await client.shutdown()
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from typing import Any, Optional

from loguru import logger

from src.core.config import settings
from src.core.exceptions import (
    InternalAdapterError,
    InternalAuthError,
    InternalProviderRequestError,
    InternalProviderResponseError,
    InternalProviderServerError,
    InternalRateLimitError,
    InternalQuotaExhaustedError,
    auth_error,
    rate_limit_error,
    upstream_error,
)
from src.providers.dataforseo.config import DataForSEOConfig, DataForSEOEndpoint, ENDPOINTS


class DataForSEOClient:
    """DataForSEO API v3 异步客户端。

    封装所有 HTTP 通信细节，Provider 模块只调用此类的方法。

    Attributes:
        config: DataForSEO 配置。
        _session: aiohttp ClientSession（延迟初始化）。
        _semaphore: 速率控制信号量。
        _cache: 本地请求缓存 dict。
    """

    def __init__(self, config: DataForSEOConfig | None = None) -> None:
        self.config = config or DataForSEOConfig()

        self._session: Any = None  # aiohttp.ClientSession
        self._semaphore = asyncio.Semaphore(self.config.rate_limit_rpm)
        self._cache: dict[str, dict[str, Any]] = {}

        # ---- 用量统计 ----
        self._total_cost: float = 0.0
        self._request_count: int = 0

    # ============================================================
    # 生命周期
    # ============================================================

    async def startup(self) -> None:
        """初始化 HTTP 会话。

        创建 aiohttp.ClientSession，预置:
          - Content-Type: application/json
          - Authorization: Basic <base64(login:api_key)>
        """
        import aiohttp

        if not self.config.login or not self.config.api_key:
            logger.warning(
                "DataForSEO 未配置 API 凭证，将运行在伪数据模式。"
                "请在 .env.local 中设置 DATAFORSEO_LOGIN 和 DATAFORSEO_API_KEY"
            )
            return

        credentials = f"{self.config.login}:{self.config.api_key}"
        encoded = base64.b64encode(credentials.encode()).decode()

        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=self.config.request_timeout),
        )
        logger.info(
            f"DataForSEOClient 初始化 | "
            f"base_url={self.config.api_base_url} | "
            f"rate_limit={self.config.rate_limit_rpm} rpm"
        )

    async def shutdown(self) -> None:
        """关闭 HTTP 会话。"""
        if self._session:
            await self._session.close()
            self._session = None
        logger.info(
            f"DataForSEOClient 已关闭 | "
            f"总请求: {self._request_count} | "
            f"总花费: ${self._total_cost:.4f}"
        )

    # ============================================================
    # 核心请求方法
    # ============================================================

    async def request(
        self,
        endpoint_key: str,
        task_body: dict[str, Any] | None = None,
        force_refresh: bool = False,
        max_pages: int = 1,
    ) -> dict[str, Any]:
        """发送 API 请求（入口方法）。

        完整调用链::

            request()
              ├── _check_cache()           ← 检查本地短期缓存
              ├── _acquire_rate_limit()    ← 等待速率令牌
              ├── _do_request()            ← POST JSON Array（含重试）
              │     ├── _build_task_array()
              │     ├── HTTP POST
              │     ├── _handle_response()
              │     ├── _track_cost()
              │     └── _poll_task()        ← 非 live 端点轮询
              ├── _update_cache()
              └── 返回结果

        Args:
            endpoint_key: ENDPOINTS 字典中的端点 key。
            task_body: 单个 task 的 body（会被包装为 [{...}]）。
            force_refresh: 是否强制跳过缓存。
            max_pages: 最大分页数（默认 1，不分页）。

        Returns:
            API 响应体（已解析为统一 dict）。

        Raises:
            InternalAuthError: 授权失败（401/403）。
            InternalRateLimitError: 被限流（429）。
            InternalQuotaExhaustedError: 额度耗尽（402）。
            InternalProviderServerError: 上游 5xx。
            InternalProviderResponseError: 响应格式异常。
            InternalProviderRequestError: 请求参数错误或网络错误。
        """
        endpoint = ENDPOINTS.get(endpoint_key)
        if endpoint is None:
            raise InternalProviderRequestError(
                f"未知的 DataForSEO 端点: {endpoint_key}",
                provider="dataforseo",
                debug_context={"endpoint_key": endpoint_key},
            )

        # Step 1: 检查缓存
        if not force_refresh:
            cache_key = self._cache_key(endpoint_key, task_body or {})
            cached = self._check_cache(cache_key)
            if cached is not None:
                logger.debug(f"Cache HIT: {endpoint_key}")
                return cached

        # Step 2: 速率控制
        await self._acquire_rate_limit()

        # Step 3: 发送请求（含重试）
        result = await self._do_request_with_retry(endpoint, task_body or {})

        # Step 4: 分页（如需）
        if max_pages > 1:
            result = await self._paginate(endpoint, task_body or {}, result, max_pages)

        # Step 5: 更新缓存
        if not force_refresh:
            cache_key = self._cache_key(endpoint_key, task_body or {})
            self._update_cache(cache_key, result)

        return result

    # ============================================================
    # 缓存（本地短期，与 Redis 互补）
    # ============================================================

    def _cache_key(self, endpoint_key: str, task_body: dict) -> str:
        """生成本地缓存 key。"""
        canonical = json.dumps(
            {"endpoint": endpoint_key, "body": task_body},
            sort_keys=True,
            default=str,
        )
        return f"dataforseo_local:{hashlib.sha256(canonical.encode()).hexdigest()[:16]}"

    def _check_cache(self, key: str) -> dict | None:
        """检查本地缓存。"""
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

        使用 asyncio.Semaphore 确保不超过 rate_limit_rpm。
        """
        self._request_count += 1
        # Semaphore 自动排队，保证并发不超过限制
        await self._semaphore.acquire()
        # 每条请求完成后释放（在 _do_request 中 release）
        # 为简化，在请求完成后通过回调释放

    def _release_rate_limit(self) -> None:
        """释放速率令牌。"""
        try:
            self._semaphore.release()
        except ValueError:
            pass  # 已释放

    # ============================================================
    # HTTP 请求（含重试）
    # ============================================================

    async def _do_request_with_retry(
        self,
        endpoint: DataForSEOEndpoint,
        task_body: dict,
    ) -> dict[str, Any]:
        """发送 HTTP 请求，含指数退避重试。

        重试策略（仅对 429/5xx 重试）:
          - 第 1 次重试: 等待 2 秒
          - 第 2 次重试: 等待 4 秒
          - 第 3 次重试: 等待 8 秒
          - 超过 max_retries: 抛出异常

        Args:
            endpoint: 端点定义。
            task_body: 单个 task 的请求体。

        Returns:
            解析后的响应 dict。
        """
        last_error: Exception | None = None

        for attempt in range(self.config.max_retries + 1):
            try:
                result = await self._do_request(endpoint, task_body)
                self._release_rate_limit()
                return result
            except InternalRateLimitError as e:
                last_error = e
                self._release_rate_limit()
                wait = self.config.retry_backoff_base * (2 ** attempt)
                logger.warning(
                    f"DataForSEO 速率限制 (attempt {attempt + 1}/{self.config.max_retries + 1}) "
                    f"→ 等待 {wait}s"
                )
                await asyncio.sleep(wait)
                await self._acquire_rate_limit()
            except InternalProviderServerError as e:
                last_error = e
                self._release_rate_limit()
                if attempt < self.config.max_retries:
                    wait = self.config.retry_backoff_base * (2 ** attempt)
                    logger.warning(
                        f"DataForSEO 服务端错误 (attempt {attempt + 1}) → 等待 {wait}s 后重试"
                    )
                    await asyncio.sleep(wait)
                    await self._acquire_rate_limit()
                else:
                    raise
            except (InternalAuthError, InternalProviderRequestError):
                self._release_rate_limit()
                raise

        raise last_error  # type: ignore[misc]

    async def _do_request(
        self,
        endpoint: DataForSEOEndpoint,
        task_body: dict,
    ) -> dict[str, Any]:
        """执行单次 POST JSON Array 请求。

        DataForSEO v3 统一请求格式:
          POST {base_url}{path}
          Body: [task_body]

        伪代码模式:
          当 self._session 为 None（未配置 API 凭证）时，
          返回模拟数据用于链路验证。

        Args:
            endpoint: 端点定义。
            task_body: 单个 task 的 body。

        Returns:
            解析后的响应数据 dict。
        """
        # ---- 伪代码模式 ----
        if self._session is None:
            return self._pseudo_response(endpoint, task_body)

        # ---- 真实请求模式 ----
        url = f"{self.config.api_base_url}{endpoint.path}"
        task_array = self._build_task_array(task_body)

        logger.debug(
            f"DataForSEO POST {endpoint.path} | body_keys={list(task_body.keys())}"
        )

        try:
            async with self._session.post(url, json=task_array) as resp:
                data = await self._handle_response(resp, endpoint)
        except InternalAdapterError:
            raise
        except Exception as e:
            raise InternalProviderRequestError(
                f"DataForSEO 网络请求失败: {e}",
                provider="dataforseo",
                debug_context={
                    "endpoint": endpoint.path,
                    "error": str(e),
                },
            ) from e

        # 非 live 端点：提交后需要轮询获取结果
        if not endpoint.is_live:
            data = await self._poll_task(endpoint, data)

        # 追踪 cost
        self._track_cost(data)

        return data

    # ============================================================
    # 响应处理
    # ============================================================

    async def _handle_response(
        self, response: Any, endpoint: DataForSEOEndpoint
    ) -> dict[str, Any]:
        """处理 HTTP 响应。

        状态码处理:
          200 → 解析 JSON，检查 tasks 层面的 status_code
          401/403 → InternalAuthError
          429 → InternalRateLimitError（含 Retry-After）
          402 → InternalQuotaExhaustedError
          5xx → InternalProviderServerError
          其他 → InternalProviderResponseError

        tasks 层面的错误（task.status_code != 200）也需处理。
        """
        status = response.status

        # 2xx: 解析 JSON
        if 200 <= status < 300:
            try:
                data = await response.json()
            except Exception as e:
                raise InternalProviderResponseError(
                    f"DataForSEO 响应 JSON 解析失败: {e}",
                    provider="dataforseo",
                    debug_context={
                        "endpoint": endpoint.path,
                        "status": status,
                    },
                ) from e

            if not isinstance(data, dict):
                raise InternalProviderResponseError(
                    "DataForSEO 响应格式异常：期望 dict",
                    provider="dataforseo",
                    debug_context={"endpoint": endpoint.path},
                )

            # 检查 tasks 层面的错误
            self._check_task_errors(data, endpoint)

            return data

        # 401/403: 授权失败
        if status in (401, 403):
            raise auth_error("dataforseo", status, await response.text())

        # 429: 被限流
        if status == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            raise rate_limit_error("dataforseo", retry_after, dict(response.headers))

        # 402: 额度耗尽
        if status == 402:
            raise InternalQuotaExhaustedError(
                "DataForSEO 额度已耗尽",
                provider="dataforseo",
                debug_context={"endpoint": endpoint.path},
            )

        # 5xx: 上游错误
        if status >= 500:
            raise upstream_error("dataforseo", endpoint.path, status, await response.text())

        # 4xx: 请求错误
        raise InternalProviderRequestError(
            f"DataForSEO 请求错误: HTTP {status}",
            provider="dataforseo",
            debug_context={
                "endpoint": endpoint.path,
                "status": status,
                "body": await response.text(),
            },
        )

    def _check_task_errors(self, data: dict, endpoint: DataForSEOEndpoint) -> None:
        """检查 DataForSEO tasks 数组层面的错误。

        DataForSEO 在 HTTP 200 的情况下，task 层面仍可能返回错误。
        例如: task.status_code = 40400（目标无数据）。
        """
        tasks = data.get("tasks", [])
        if not tasks:
            return

        for task in tasks:
            task_status = task.get("status_code", 200)

            # 正常
            if task_status == 200 or task_status == 20100:
                continue

            # Task 层面错误码
            task_message = task.get("status_message", "Unknown task error")

            if task_status == 40100:
                raise InternalAuthError(
                    f"DataForSEO task 授权失败: {task_message}",
                    provider="dataforseo",
                )
            elif task_status == 40200:
                raise InternalQuotaExhaustedError(
                    f"DataForSEO task 额度耗尽: {task_message}",
                    provider="dataforseo",
                )
            elif task_status == 40400:
                logger.warning(
                    f"DataForSEO task 无数据: {task_message} | "
                    f"endpoint={endpoint.path}"
                )
                # 无数据不算错误，返回空结果
            elif task_status >= 50000:
                raise InternalProviderServerError(
                    f"DataForSEO task 服务端错误: {task_message}",
                    provider="dataforseo",
                    debug_context={"task_status": task_status},
                )

    # ============================================================
    # 异步任务轮询
    # ============================================================

    async def _poll_task(
        self, endpoint: DataForSEOEndpoint, submit_response: dict
    ) -> dict[str, Any]:
        """轮询非 live 端点的任务结果。

        流程:
          1. POST 提交任务 → 返回 task_id
          2. GET /v3/{path}/{task_id} 轮询
          3. status_code == 20100 (Completed) → 返回结果
          4. 超时 → 抛出异常

        Args:
            endpoint: 端点定义。
            submit_response: POST 提交后返回的响应。

        Returns:
            完成后的完整响应 dict。
        """
        tasks = submit_response.get("tasks", [])
        if not tasks:
            raise InternalProviderResponseError(
                "DataForSEO 任务提交未返回 task_id",
                provider="dataforseo",
                debug_context={"endpoint": endpoint.path},
            )

        task_id = tasks[0].get("id")
        if not task_id:
            return submit_response  # 已有结果，无需轮询

        poll_url = f"{self.config.api_base_url}{endpoint.path}/{task_id}"

        for attempt in range(self.config.task_poll_max_attempts):
            await asyncio.sleep(self.config.task_poll_interval)

            try:
                async with self._session.get(poll_url) as resp:
                    data = await resp.json()
            except Exception as e:
                logger.warning(f"DataForSEO 轮询失败 (attempt {attempt + 1}): {e}")
                continue

            tasks = data.get("tasks", [])
            if tasks:
                status_code = tasks[0].get("status_code", 0)
                if status_code == 20100:  # Completed
                    logger.debug(f"DataForSEO 任务完成: {task_id}")
                    return data
                elif status_code >= 50000:
                    raise InternalProviderServerError(
                        f"DataForSEO 任务执行失败: {tasks[0].get('status_message')}",
                        provider="dataforseo",
                        debug_context={"task_id": task_id, "status_code": status_code},
                    )

            logger.debug(
                f"DataForSEO 轮询中 ({attempt + 1}/{self.config.task_poll_max_attempts}) | "
                f"task_id={task_id}"
            )

        raise InternalProviderRequestError(
            f"DataForSEO 任务轮询超时 ({self.config.task_poll_max_attempts * self.config.task_poll_interval}s)",
            provider="dataforseo",
            debug_context={"task_id": task_id, "endpoint": endpoint.path},
        )

    # ============================================================
    # 分页
    # ============================================================

    async def _paginate(
        self,
        endpoint: DataForSEOEndpoint,
        task_body: dict,
        first_result: dict,
        max_pages: int,
    ) -> dict[str, Any]:
        """分页获取结果。

        DataForSEO 分页方式:
          - offset + limit（最大 offset 20000）
          - search_after_token（超过 20000 条时使用）

        Args:
            endpoint: 端点定义。
            task_body: 原始 task body。
            first_result: 第一页结果。
            max_pages: 最大页数。

        Returns:
            合并后的完整结果。
        """
        current_page = 1
        all_items = self._extract_items(first_result)

        # 检查是否需要继续分页
        search_after_token = first_result.get("data", {}).get("search_after_token")
        total_count = first_result.get("data", {}).get("total_count", len(all_items))
        limit = task_body.get("limit", 100)

        while current_page < max_pages and len(all_items) < total_count:
            current_page += 1
            next_body = {**task_body}

            if search_after_token:
                next_body["search_after_token"] = search_after_token
            else:
                next_body["offset"] = (current_page - 1) * limit

            logger.debug(f"DataForSEO 分页: page={current_page}/{max_pages}")
            page_result = await self._do_request_with_retry(endpoint, next_body)

            page_items = self._extract_items(page_result)
            all_items.extend(page_items)

            search_after_token = page_result.get("data", {}).get("search_after_token")
            if not page_items:
                break

        # 将合并的 items 写回第一个结果的 items 字段
        self._inject_items(first_result, all_items)
        return first_result

    def _extract_items(self, result: dict) -> list[dict]:
        """从 DataForSEO 响应中提取 items 数组。

        处理两种响应结构:
          1. live 端点: result["tasks"][0]["result"] → list
          2. 非 live 端点: result["tasks"][0]["data"]["items"] → list
        """
        tasks = result.get("tasks", [])
        if not tasks:
            return []

        task = tasks[0]

        # live 端点：result 直接在 task 下
        if "result" in task and task["result"] is not None:
            return task["result"] if isinstance(task["result"], list) else []

        # 非 live 端点：items 在 data 下
        items = task.get("data", {}).get("items", [])
        if items:
            return items

        return []

    def _inject_items(self, result: dict, items: list[dict]) -> None:
        """将合并后的 items 写回结果 dict。"""
        tasks = result.get("tasks", [])
        if not tasks:
            return
        task = tasks[0]
        if "result" in task:
            task["result"] = items
        elif "data" in task:
            task["data"]["items"] = items

    # ============================================================
    # 请求构建
    # ============================================================

    def _build_task_array(self, task_body: dict) -> list[dict]:
        """构建 DataForSEO POST JSON Array body。

        DataForSEO v3 所有请求 body 格式:
          [{task_body}]

        可包含多个 task（batch），但当前实现每次一个 task。
        """
        return [task_body]

    # ============================================================
    # cost 追踪
    # ============================================================

    def _track_cost(self, data: dict) -> None:
        """追踪 API 调用 cost。"""
        # 顶层 cost
        top_cost = data.get("cost", 0)
        if top_cost:
            self._total_cost += top_cost

        # tasks 层面 cost
        for task in data.get("tasks", []):
            task_cost = task.get("cost", 0)
            if task_cost:
                self._total_cost += task_cost

        logger.debug(f"DataForSEO 花费 | ${self._total_cost:.4f} 累计")

    # ============================================================
    # 伪数据生成（开发阶段 — 接入真实 API Key 后全部删除）
    # ============================================================

    def _pseudo_response(self, endpoint: DataForSEOEndpoint, task_body: dict) -> dict[str, Any]:
        """生成伪响应数据，用于链路验证。

        TODO: 接入真实 DataForSEO API Key 后，删除此方法及其所有调用。
        """
        from datetime import date, datetime, timezone

        today = str(date.today())
        now = datetime.now(timezone.utc).isoformat()

        target = task_body.get("target", "example.com")
        keyword = task_body.get("keyword", task_body.get("keywords", ["seo"])[0] if isinstance(task_body.get("keywords"), list) else "seo")

        pseudo_templates: dict[str, dict] = {
            "competitors_domain": {
                "status_code": 200,
                "status_message": "Ok.",
                "time": "0.1234",
                "cost": 0.0201,
                "tasks_count": 1,
                "tasks": [{
                    "id": "pseudo-competitors-domain-001",
                    "status_code": 200,
                    "status_message": "Ok.",
                    "time": "0.1000",
                    "cost": 0.0201,
                    "result_count": 3,
                    "result": [
                        {
                            "domain": f"{target}",
                            "metrics": {
                                "organic": {
                                    "count": 15230, "etv": 245000,
                                    "pos_1": 45, "pos_1_5": 320, "pos_6_10": 890,
                                    "pos_11_20": 2100, "pos_21_30": 3400,
                                    "pos_31_40": 4100, "pos_41_50": 2100,
                                    "pos_51_60": 1100, "pos_61_70": 650,
                                    "pos_71_80": 350, "pos_81_90": 135, "pos_91_100": 40,
                                },
                                "paid": {"count": 0, "etv": 0},
                            },
                        },
                        {
                            "domain": "competitor1.com",
                            "metrics": {
                                "organic": {
                                    "count": 28500, "etv": 512000,
                                    "pos_1": 120, "pos_1_5": 780, "pos_6_10": 2100,
                                },
                            },
                        },
                        {
                            "domain": "competitor2.com",
                            "metrics": {
                                "organic": {
                                    "count": 8900, "etv": 132000,
                                    "pos_1": 18, "pos_1_5": 145, "pos_6_10": 430,
                                },
                            },
                        },
                    ],
                }],
            },
            "ranked_keywords": {
                "status_code": 200,
                "status_message": "Ok.",
                "tasks": [{
                    "id": "pseudo-ranked-keywords-001",
                    "status_code": 200,
                    "result_count": 5,
                    "result": [
                        {
                            "keyword": f"best {keyword} tool",
                            "search_volume": 12100, "competition": 0.85, "cpc": 12.50,
                            "keyword_difficulty": 67,
                            "ranked_domain": {"domain": target, "rank_group": 3, "rank_absolute": 3},
                            "impressions": 45000, "clicks": 3200, "estimated_traffic": 18500,
                        },
                        {
                            "keyword": f"{keyword} guide 2026",
                            "search_volume": 8800, "competition": 0.62, "cpc": 8.90,
                            "keyword_difficulty": 45,
                            "ranked_domain": {"domain": target, "rank_group": 1, "rank_absolute": 1},
                            "impressions": 120000, "clicks": 28000, "estimated_traffic": 42000,
                        },
                        {
                            "keyword": f"free {keyword} checker",
                            "search_volume": 5400, "competition": 0.35, "cpc": 3.20,
                            "keyword_difficulty": 28,
                            "ranked_domain": {"domain": target, "rank_group": 8, "rank_absolute": 8},
                            "impressions": 8000, "clicks": 320, "estimated_traffic": 1200,
                        },
                    ],
                }],
            },
            "backlinks_list": {
                "status_code": 200,
                "status_message": "Ok.",
                "tasks": [{
                    "id": "pseudo-backlinks-001",
                    "status_code": 200,
                    "result": [
                        {
                            "domain_from": "referrer-site.com",
                            "page_from": "https://referrer-site.com/resources/seo-basics",
                            "anchor": "SEO guide",
                            "dofollow": True,
                            "rank": 95,
                            "first_seen": "2025-01-15 10:00:00 +00:00",
                            "last_visited": now,
                            "domain_from_rank": 82,
                            "target_title": "Complete SEO Platform",
                            "is_new": False, "is_lost": False, "is_broken": False,
                        },
                        {
                            "domain_from": "another-blog.com",
                            "page_from": "https://another-blog.com/articles/top-tools",
                            "anchor": "best SEO tools",
                            "dofollow": True,
                            "rank": 88,
                            "first_seen": "2024-11-20 08:30:00 +00:00",
                            "domain_from_rank": 71,
                            "is_new": False, "is_lost": False, "is_broken": False,
                        },
                    ],
                }],
            },
            "serp_google_organic": {
                "status_code": 200,
                "status_message": "Ok.",
                "tasks": [{
                    "id": "pseudo-serp-001",
                    "status_code": 200,
                    "result_count": 10,
                    "result": [
                        {
                            "rank_group": 1, "rank_absolute": 1,
                            "domain": "ahrefs.com",
                            "url": "https://ahrefs.com/blog/seo-guide",
                            "title": "SEO Guide: Everything You Need to Know",
                            "type": "organic",
                            "description": "A complete SEO guide covering all aspects...",
                        },
                        {
                            "rank_group": 2, "rank_absolute": 2,
                            "domain": target,
                            "url": f"https://{target}/seo-guide",
                            "title": "The Ultimate SEO Guide 2026",
                            "type": "organic",
                        },
                    ],
                }],
            },
        }

        template = pseudo_templates.get(
            self._match_template(endpoint),
            {
                "status_code": 200,
                "status_message": "Ok. (PSEUDO)",
                "cost": 0,
                "tasks": [{
                    "id": "pseudo-default-001",
                    "status_code": 200,
                    "status_message": f"PSEUDO response for {endpoint.path}",
                    "result": [{"message": "PSEUDO", "endpoint": endpoint.path, "body": task_body}],
                }],
            },
        )
        return template

    def _match_template(self, endpoint: DataForSEOEndpoint) -> str:
        """匹配伪数据模板 key。"""
        path_to_template: dict[str, str] = {
            "competitors_domain": "competitors_domain",
            "ranked_keywords": "ranked_keywords",
            "backlinks/backlinks": "backlinks_list",
            "serp/google/organic": "serp_google_organic",
        }
        for path_fragment, template_key in path_to_template.items():
            if path_fragment in endpoint.path:
                return template_key
        return ""
