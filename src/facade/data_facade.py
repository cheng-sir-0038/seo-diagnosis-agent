"""
Facade 统一查询入口

职责:
  1. 接收标准化的 QueryRequest（capability + site_id + params）
  2. 根据 capability 路由到支持该能力的 Provider
  3. 对 Provider 返回的原始数据做统一包装（添加 QueryMeta）
  4. 异常捕获 → 转换 InternalError → 消毒 → 抛出（由 API 层的 exception handler 处理）
  5. 缓存管理：查 Redis → 命中返回 → 未命中调 Provider → 写 Redis → 返回

架构位置::

    POST /api/v1/query ─→ Facade ─→ Provider(s) ─→ 外部 API / 伪模式
                             │
                             ├── CacheManager (Redis + SQLite)
                             └── 异常转换

多 Provider 路由:

    Facade 维护一个 Provider 注册表: dict[Capability, list[Provider]]
    收到请求后:
      1. 将结构化参数转换为 QueryIntent
      2. ProviderRouter 按当前已实现能力和查询目的选择主 Provider
      3. 只有完整满足要求的候选才能成为备用 Provider
      4. 调用 → 可控回退 → 返回 → 包装

当前限制:
  - ProviderRouter 根据结构化意图和当前已实现能力确定主 Provider
  - 仅向完整满足要求的候选执行受控失败回退
  - CacheManager 提供进程内 TTL 缓存和 SQLite 快照；Redis 未接入
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from loguru import logger

from src.core.cache import cache_manager
from src.core.exceptions import (
    ErrorCode,
    InternalAdapterError,
    InternalDataNotAvailableError,
)
from src.models.common import (
    BaseEvidence,
    Capability,
    DataSource,
    QueryMeta,
)
from src.providers.ahrefs import AhrefsProvider
from src.providers.base import BaseProvider
from src.providers.dataforseo import DataForSEOProvider
from src.providers.page_metadata import PageMetadataProvider
from src.providers.pagespeed import PageSpeedProvider
from src.providers.serper import SerperProvider
from src.routing.contracts import (
    ProviderDecision,
    ProviderFeature,
    ProviderSelectionMode,
    QueryIntent,
)
from src.routing.router import ProviderRoute, ProviderRouter, ProviderRoutingError


class DataFacade:
    """统一数据查询入口。

    所有外部 API 调用必须通过此 Facade，不直接访问 Provider。

    Attributes:
        _providers: Provider 注册表 {provider_name: BaseProvider}。
        _capability_map: Capability → [Provider] 路由表（启动时构建）。
    """

    def __init__(self) -> None:
        self._providers: dict[str, BaseProvider] = {}
        self._capability_map: dict[Capability, list[BaseProvider]] = {}
        self._provider_router = ProviderRouter()

    # ============================================================
    # 生命周期
    # ============================================================

    async def startup(self) -> None:
        """初始化所有 Provider 并构建路由表。

        真实实现:
          1. 遍历已注册的 Provider 实例
          2. 调用 provider.startup() 建立连接
          3. 构建 capability → [providers] 路由表
          4. 预热健康检查
        """
        # Provider 注册顺序只用于生命周期管理，不再决定业务查询选择。
        dataforseo = DataForSEOProvider()
        await dataforseo.startup()
        self._providers["dataforseo"] = dataforseo

        serper = SerperProvider()
        await serper.startup()
        self._providers["serper"] = serper

        # 注册直接页面 HTML Provider；仅允许查询当前站点内 URL。
        page_metadata = PageMetadataProvider()
        await page_metadata.startup()
        self._providers["page_html"] = page_metadata

        # 注册 PageSpeed Provider；未配置 Key 时保持 unavailable，不进入 live 工具。
        pagespeed = PageSpeedProvider()
        await pagespeed.startup()
        self._providers["pagespeed"] = pagespeed

        # 注册 Ahrefs Provider（伪代码，补充 DataForSEO 不支持的 Capability）
        ahrefs = AhrefsProvider()
        await ahrefs.startup()
        self._providers["ahrefs"] = ahrefs

        # 构建路由表: Capability → [Provider]
        self._capability_map.clear()
        for provider in self._providers.values():
            for cap in provider.capabilities:
                if cap not in self._capability_map:
                    self._capability_map[cap] = []
                self._capability_map[cap].append(provider)

        logger.info(
            f"DataFacade 初始化完成 | "
            f"providers={list(self._providers.keys())} | "
            f"capabilities={len(self._capability_map)}"
        )

    async def shutdown(self) -> None:
        """关闭所有 Provider。"""
        for name, provider in self._providers.items():
            await provider.shutdown()
            logger.info(f"Provider [{name}] 已关闭")

    # ============================================================
    # 主入口：统一查询
    # ============================================================

    async def query(
        self,
        capability: Capability,
        site_id: str | None = None,
        params: dict[str, Any] | None = None,
        force_refresh: bool = False,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """兼容查询入口，允许现有数据适配层使用 pseudo Provider。"""
        return await self._query(
            capability=capability,
            site_id=site_id,
            params=params,
            force_refresh=force_refresh,
            task_id=task_id,
            live_only=False,
        )

    async def query_live(
        self,
        capability: Capability,
        site_id: str | None = None,
        params: dict[str, Any] | None = None,
        force_refresh: bool = False,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """真实数据查询入口，只允许 runtime_mode=live 的 Provider。"""
        return await self._query(
            capability=capability,
            site_id=site_id,
            params=params,
            force_refresh=force_refresh,
            task_id=task_id,
            live_only=True,
        )

    def live_capabilities(self) -> set[Capability]:
        """返回当前进程中至少有一个真实 Provider 支持的能力。"""
        return {
            capability
            for capability, providers in self._capability_map.items()
            if any(provider.runtime_mode == "live" for provider in providers)
        }

    async def _query(
        self,
        *,
        capability: Capability,
        site_id: str | None,
        params: dict[str, Any] | None,
        force_refresh: bool,
        task_id: str | None,
        live_only: bool,
    ) -> dict[str, Any]:
        routing_params = dict(params or {})
        self._validate_capability(capability)
        intent = self._build_query_intent(capability, routing_params)
        route = self._resolve_route(intent, live_only=live_only)
        attempts = (route.primary, *route.fallbacks)
        last_error: InternalAdapterError | None = None

        for index, provider in enumerate(attempts):
            decision = (
                route.decision
                if index == 0
                else self._fallback_decision(
                    route,
                    provider=provider,
                    failed_provider=attempts[index - 1],
                    remaining=attempts[index + 1 :],
                )
            )
            cache_key = self._build_query_cache_key(provider, capability, site_id, routing_params)
            if not force_refresh:
                cached = await cache_manager.get(cache_key)
                if cached is not None:
                    logger.info(f"Cache HIT: {capability.value} [{provider.meta.name}]")
                    cached_response = deepcopy(cached)
                    cached_response["meta"]["cached"] = True
                    cached_response["meta"]["provider_selection"] = decision.model_dump(mode="json")
                    return cached_response

            try:
                raw_data = await self._dispatch(
                    provider,
                    capability,
                    site_id,
                    self._provider_params(routing_params),
                )
            except InternalAdapterError as exc:
                current_error = exc
            except Exception as exc:
                logger.error(f"Provider 调用失败: {exc}")
                current_error = InternalAdapterError(
                    f"数据查询失败: {exc}",
                    error_code=ErrorCode.UPSTREAM_ERROR,
                    provider=provider.source.value,
                    debug_context={
                        "capability": capability.value,
                        "error": str(exc),
                        "live_only": live_only,
                    },
                )
            else:
                response = self._wrap_response(
                    capability=capability,
                    source=provider.source,
                    data=raw_data,
                    params_hash=cache_key.split(":")[-1],
                    provider_decision=decision,
                )
                await cache_manager.set(cache_key, response)

                if task_id:
                    await cache_manager.save_snapshot(
                        task_id=task_id,
                        capability=capability.value,
                        data=response,
                        provider=provider.source.value,
                    )
                return response

            last_error = current_error
            has_fallback = index + 1 < len(attempts)
            if has_fallback and intent.allow_fallback and self._should_fallback(current_error):
                logger.warning(
                    "Provider [{}] 失败，尝试完整满足要求的备用 Provider [{}]",
                    provider.meta.name,
                    attempts[index + 1].meta.name,
                )
                continue
            raise current_error

        raise last_error or InternalDataNotAvailableError(
            f"没有 Provider 完成 {capability.value}",
            provider="facade",
        )

    # ============================================================
    # Provider 路由
    # ============================================================

    def _validate_capability(self, capability: Capability) -> None:
        """校验 Capability 是否被至少一个 Provider 支持。"""
        if capability not in self._capability_map:
            raise InternalAdapterError(
                f"不支持的能力: {capability.value}",
                error_code=ErrorCode.UNSUPPORTED_CAPABILITY,
                debug_context={"capability": capability.value},
            )

    def _resolve_route(self, intent: QueryIntent, *, live_only: bool) -> ProviderRoute:
        candidates = self._capability_map.get(intent.capability, [])
        if live_only and not any(provider.runtime_mode == "live" for provider in candidates):
            raise InternalDataNotAvailableError(
                f"没有真实 Provider 支持 {intent.capability.value}",
                provider="facade",
                debug_context={
                    "capability": intent.capability.value,
                    "live_only": True,
                },
            )
        try:
            return self._provider_router.resolve(intent, candidates, live_only=live_only)
        except ProviderRoutingError as exc:
            raise InternalDataNotAvailableError(
                str(exc),
                provider="facade",
                debug_context={
                    "capability": intent.capability.value,
                    "live_only": live_only,
                    "provider_selection": exc.decision.model_dump(mode="json"),
                },
            ) from exc

    def _resolve_provider(
        self,
        capability: Capability,
        *,
        params: dict[str, Any] | None = None,
        live_only: bool = False,
    ) -> BaseProvider:
        """兼容出口：通过 ProviderRouter 返回主 Provider。"""
        intent = self._build_query_intent(capability, dict(params or {}))
        return self._resolve_route(intent, live_only=live_only).primary

    @staticmethod
    def _build_query_intent(capability: Capability, params: dict[str, Any]) -> QueryIntent:
        required_features = set(params.get("required_features") or ())
        if bool(params.get("load_ai_overview", False)):
            required_features.add(ProviderFeature.AI_OVERVIEW)
        result_types_raw = params.get("result_types") or ()
        result_types = [result_types_raw] if isinstance(result_types_raw, str) else result_types_raw
        if "ai_overview" in {str(item).lower() for item in result_types}:
            required_features.add(ProviderFeature.AI_OVERVIEW)
        return QueryIntent(
            capability=capability,
            purpose=params.get("purpose", "quick_lookup"),
            country=params.get("country", "us"),
            language=params.get("language", "en"),
            limit=params.get("limit") or 10,
            page=params.get("page") or 1,
            autocorrect=(params.get("autocorrect") if "autocorrect" in params else None),
            device=params.get("device"),
            os=params.get("os"),
            location_name=params.get("location_name"),
            location_coordinate=params.get("location_coordinate"),
            required_features=required_features,
            preferred_provider=params.get("preferred_provider"),
            allow_fallback=bool(params.get("allow_fallback", True)),
        )

    @staticmethod
    def _provider_params(params: dict[str, Any]) -> dict[str, Any]:
        routing_only = {
            "purpose",
            "required_features",
            "preferred_provider",
            "allow_fallback",
        }
        return {key: value for key, value in params.items() if key not in routing_only}

    @staticmethod
    def _fallback_decision(
        route: ProviderRoute,
        *,
        provider: BaseProvider,
        failed_provider: BaseProvider,
        remaining: tuple[BaseProvider, ...],
    ) -> ProviderDecision:
        return ProviderDecision(
            mode=ProviderSelectionMode.FALLBACK,
            selected_provider=provider.meta.name,
            selected_source=provider.source,
            reasons=(
                *route.decision.reasons,
                f"{failed_provider.meta.name} 调用失败，切换到完整满足要求的备用 Provider",
            ),
            matched_features=route.decision.matched_features,
            fallback_candidates=tuple(item.meta.name for item in remaining),
            fallback_used=True,
        )

    @staticmethod
    def _should_fallback(error: InternalAdapterError) -> bool:
        return error.error_code in {
            ErrorCode.AUTH_FAILED,
            ErrorCode.RATE_LIMITED,
            ErrorCode.QUOTA_EXHAUSTED,
            ErrorCode.UPSTREAM_ERROR,
            ErrorCode.UPSTREAM_TIMEOUT,
            ErrorCode.DATA_NOT_AVAILABLE,
        }

    # ============================================================
    # 方法路由（Capability → Provider 方法）
    # ============================================================

    async def _dispatch(
        self,
        provider: BaseProvider,
        capability: Capability,
        site_id: str | None,
        params: dict[str, Any],
    ) -> Any:
        """根据 Capability 调用 Provider 的对应方法。

        这个 if-elif 链是 Facade 的核心路由逻辑。

        Args:
            provider: 目标 Provider。
            capability: 业务能力。
            site_id: 站点 ID。
            params: 附加参数。

        Returns:
            Provider 方法返回值（BaseEvidence 子类或 list）。
        """
        # 提取共用参数，避免与 **params 中的同名字段冲突
        country = params.pop("country", "us")
        limit = params.pop("limit", None)
        keywords = params.pop("keywords", [])
        keyword = params.pop("keyword", None)
        query = params.pop("query", None)
        language = params.pop("language", "en")
        severity = params.pop("severity", None)
        url = params.pop("url", None)
        targets = params.pop("targets", [])
        strategy = params.pop("strategy", "mobile")
        categories = params.pop("categories", None)
        locale = params.pop("locale", "zh-CN")
        max_opportunities = params.pop("max_opportunities", None)
        provider_limit = limit if provider.source == DataSource.DATAFORSEO else (limit or 100)

        # ---- 管理 (T1) ----
        if capability == Capability.SITE_PROFILE:
            return await provider.get_site_profile(site_id=site_id, **params)

        if capability == Capability.SUBSCRIPTION_INFO:
            return await provider.get_subscription_info(**params)

        # ---- 搜索表现 (T2) ----
        if capability == Capability.SEARCH_METRICS:
            return await provider.get_search_metrics(
                site_id=site_id,
                country=country,
                limit=limit,
                **params,
            )

        if capability == Capability.TOP_PAGES:
            return await provider.get_top_pages(
                site_id=site_id,
                country=country,
                limit=limit or 10,
                **params,
            )

        if capability == Capability.ORGANIC_KEYWORDS:
            return await provider.get_organic_keywords(
                site_id=site_id,
                country=country,
                limit=provider_limit,
                **params,
            )

        if capability == Capability.SEARCH_HISTORY:
            return await provider.get_search_history(
                site_id=site_id,
                country=country,
                **params,
            )

        # ---- 技术审计 (T3) ----
        if capability == Capability.AUDIT_HEALTH:
            return await provider.get_audit_health(site_id=site_id, **params)

        if capability == Capability.AUDIT_ISSUES:
            return await provider.get_audit_issues(
                site_id=site_id,
                severity=severity,
                **params,
            )

        if capability == Capability.AUDIT_PAGE_EXPLORER:
            return await provider.get_page_explorer(
                site_id=site_id,
                url=url,
                **params,
            )

        if capability == Capability.PAGE_METADATA:
            return await provider.get_page_metadata(
                site_id=site_id or "",
                url=url,
                **params,
            )

        if capability == Capability.PAGE_PERFORMANCE:
            return await provider.get_page_performance(
                site_id=site_id or "",
                url=url,
                strategy=strategy,
                categories=categories,
                locale=locale,
                max_opportunities=max_opportunities,
                **params,
            )

        # ---- 关键词 (T4) ----
        if capability == Capability.KEYWORD_OVERVIEW:
            return await provider.get_keyword_overview(
                keywords=keywords,
                country=country,
                **params,
            )

        if capability == Capability.SERP_OVERVIEW:
            return await provider.get_serp_overview(
                keyword=keyword or "",
                country=country,
                limit=limit,
                language=language,
                **params,
            )

        if capability == Capability.AUTOCOMPLETE:
            return await provider.get_autocomplete(
                query=query or "",
                country=country,
                language=language,
                **params,
            )

        if capability == Capability.NEWS_SEARCH:
            return await provider.get_news_search(
                query=query or "",
                country=country,
                language=language,
                limit=limit or 10,
                page=params.pop("page", 1),
                autocorrect=params.pop("autocorrect", True),
                **params,
            )

        if capability == Capability.IMAGE_SEARCH:
            return await provider.get_image_search(
                query=query or "",
                country=country,
                language=language,
                limit=limit or 10,
                page=params.pop("page", 1),
                autocorrect=params.pop("autocorrect", True),
                **params,
            )

        if capability == Capability.VIDEO_SEARCH:
            return await provider.get_video_search(
                query=query or "",
                country=country,
                language=language,
                limit=limit or 10,
                page=params.pop("page", 1),
                autocorrect=params.pop("autocorrect", True),
                **params,
            )

        # ---- 排名 (T4) ----
        if capability == Capability.RANK_OVERVIEW:
            return await provider.get_rank_overview(site_id=site_id, **params)

        if capability == Capability.RANK_DETAIL:
            return await provider.get_ranked_keywords(
                site_id=site_id,
                limit=limit or 100,
                **params,
            )

        # ---- 外链 (T5) ----
        if capability == Capability.BACKLINKS_LIST:
            return await provider.get_backlinks(
                site_id=site_id,
                limit=provider_limit,
                **params,
            )

        if capability == Capability.REFDOMAINS:
            return await provider.get_refdomains(
                site_id=site_id,
                limit=provider_limit,
                **params,
            )

        if capability == Capability.BROKEN_BACKLINKS:
            return await provider.get_broken_backlinks(
                site_id=site_id,
                limit=provider_limit,
                **params,
            )

        # ---- 竞争 (T5) ----
        if capability == Capability.ORGANIC_COMPETITORS:
            return await provider.get_organic_competitors(
                site_id=site_id,
                country=country,
                limit=limit,
                **params,
            )

        if capability == Capability.BATCH_ANALYSIS:
            return await provider.get_batch_analysis(
                targets=targets,
                **params,
            )

        raise InternalAdapterError(
            f"未实现的路由: {capability.value}",
            error_code=ErrorCode.UNSUPPORTED_CAPABILITY,
            debug_context={"capability": capability.value},
        )

    # ============================================================
    # 响应包装
    # ============================================================

    def _wrap_response(
        self,
        capability: Capability,
        source: DataSource,
        data: Any,
        params_hash: str,
        provider_decision: ProviderDecision | None = None,
    ) -> dict[str, Any]:
        """将 Provider 返回值包装为标准响应格式。

        输出格式::

            {
                "meta": {
                    "capability": "search_metrics",
                    "source": "ahrefs",
                    "params_hash": "abc123",
                    "cached": false,
                    "units_consumed": 50,
                },
                "data": <BaseEvidence 子类实例或 list>
            }

        Args:
            capability: 业务能力。
            source: 数据来源。
            data: Provider 返回的数据。
            params_hash: 请求参数哈希。

        Returns:
            标准化的响应 dict。
        """
        meta = QueryMeta(
            capability=capability,
            source=source,
            params_hash=params_hash,
            cached=False,
            units_consumed=0,  # TODO: 从 Provider 获取实际消耗
        )
        meta_payload = meta.model_dump(mode="json")
        if provider_decision is not None:
            meta_payload["provider_selection"] = provider_decision.model_dump(mode="json")
        return {
            "meta": meta_payload,
            "data": self._serialize_data(data),
        }

    @staticmethod
    def _serialize_data(data: Any) -> Any:
        """将数据序列化为 JSON 兼容格式。

        - BaseEvidence 实例 → model_dump(mode="json")
        - list[BaseEvidence] → list of dicts
        - 其他类型 → 直接返回
        """
        if isinstance(data, BaseEvidence):
            return data.model_dump(mode="json")
        if isinstance(data, list):
            return [
                item.model_dump(mode="json") if isinstance(item, BaseEvidence) else item
                for item in data
            ]
        return data

    # ============================================================
    # 缓存辅助
    # ============================================================

    def _build_query_cache_key(
        self,
        provider: BaseProvider,
        capability: Capability,
        site_id: str | None,
        params: dict,
    ) -> str:
        """构建查询的 Redis 缓存 key。

        格式: ``{provider_name}:{capability}:{params_hash}``
        """
        payload = {
            "capability": capability.value,
            "site_id": site_id,
            "params": params,
        }
        canonical = json.dumps(payload, sort_keys=True, default=str)
        param_hash = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        return f"{provider.source.value}:{capability.value}:{param_hash}"


# ============================================================
# 全局单例
# ============================================================

facade = DataFacade()
