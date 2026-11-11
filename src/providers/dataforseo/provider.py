"""
DataForSEO Provider 主类

组合所有业务能力，调用 DataForSEOClient 获取原始数据，
翻译为统一数据模型（BaseEvidence）返回。

三层架构位置:
  Facade → Provider → Client → DataForSEO API

与 AhrefsProvider 的关系:
  - 接口相同（都继承 BaseProvider）
  - Facade 通过 _capability_map 路由，不区分数据源
  - 覆盖不同的 Capability 集合（DataForSEO 无审计/排名跟踪）

DataForSEO 覆盖的 Capability:
  ✅ 搜索表现: search_metrics (domain_rank_overview)
  ✅ 关键词:   organic_keywords, keyword_overview, serp_overview
  ✅ 外链:     backlinks_list, refdomains, broken_backlinks
  ✅ 竞争:     organic_competitors
  ✅ 管理:     subscription_info
  ❌ 审计:     audit_health / audit_issues / page_explorer
  ❌ 排名:     rank_overview / rank_detail
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from src.core.config import settings
from src.core.exceptions import InternalProviderResponseError
from src.models.backlink import Backlink, BrokenBacklink, RefDomain
from src.models.common import (
    Capability,
    DataIntegrity,
    DataSource,
)
from src.models.competitor import OrganicCompetitor
from src.models.keyword import KeywordOverview, SerpResult
from src.models.management import SubscriptionInfo
from src.models.search import OrganicKeyword, SearchMetrics
from src.providers.base import BaseProvider, ProviderMeta, ProviderMode
from src.providers.dataforseo.client import DataForSEOClient
from src.routing.contracts import ProviderFeature, ProviderProfile


class DataForSEOProvider(BaseProvider):
    """DataForSEO API v3 数据适配器。

    实现 BaseProvider 接口，将 DataForSEO API 响应翻译为统一数据模型。

    支持的 Capability:
      - SEARCH_METRICS / ORGANIC_KEYWORDS
      - KEYWORD_OVERVIEW / SERP_OVERVIEW
      - BACKLINKS_LIST / REFDOMAINS / BROKEN_BACKLINKS
      - ORGANIC_COMPETITORS
      - SUBSCRIPTION_INFO（基于 cost 追踪推算）
    """

    # ============================================================
    # 元信息
    # ============================================================

    @property
    def runtime_mode(self) -> ProviderMode:
        client = getattr(self, "client", None)
        return "live" if client is not None and client._session is not None else "pseudo"

    @property
    def routing_profile(self) -> ProviderProfile:
        return ProviderProfile(
            provider_name=self.meta.name,
            source=self.source,
            runtime_mode=self.runtime_mode,
            capabilities=frozenset(self.capabilities),
            supported_features=frozenset(
                {
                    ProviderFeature.REALTIME,
                    ProviderFeature.COUNTRY_TARGETING,
                    ProviderFeature.LANGUAGE_TARGETING,
                    ProviderFeature.DEVICE,
                    ProviderFeature.OS,
                    ProviderFeature.EXACT_LOCATION,
                    ProviderFeature.COORDINATES,
                    ProviderFeature.AI_OVERVIEW,
                    ProviderFeature.DEEP_RESULTS,
                }
            ),
            implemented_features=frozenset(
                {
                    ProviderFeature.REALTIME,
                    ProviderFeature.COUNTRY_TARGETING,
                    ProviderFeature.LANGUAGE_TARGETING,
                    ProviderFeature.DEVICE,
                    ProviderFeature.OS,
                    ProviderFeature.EXACT_LOCATION,
                    ProviderFeature.COORDINATES,
                    ProviderFeature.AI_OVERVIEW,
                    ProviderFeature.DEEP_RESULTS,
                }
            ),
            capability_limits={Capability.SERP_OVERVIEW: 200},
        )

    @property
    def meta(self) -> ProviderMeta:
        return ProviderMeta(
            name="dataforseo",
            label="DataForSEO API v3",
            source=DataSource.DATAFORSEO,
            supported_capabilities={
                # ---- 搜索表现 ----
                Capability.SEARCH_METRICS,
                Capability.ORGANIC_KEYWORDS,
                # ---- 关键词研究 ----
                Capability.KEYWORD_OVERVIEW,
                Capability.SERP_OVERVIEW,
                # ---- 外链 ----
                Capability.BACKLINKS_LIST,
                Capability.REFDOMAINS,
                Capability.BROKEN_BACKLINKS,
                # ---- 竞争分析 ----
                Capability.ORGANIC_COMPETITORS,
                # ---- 管理 ----
                Capability.SUBSCRIPTION_INFO,
            },
            version="1.0.0-alpha",
        )

    # ============================================================
    # 生命周期
    # ============================================================

    async def startup(self) -> None:
        self.client = DataForSEOClient()
        await self.client.startup()
        logger.info("DataForSEOProvider 初始化完成")

    async def shutdown(self) -> None:
        if hasattr(self, "client"):
            await self.client.shutdown()
        logger.info("DataForSEOProvider 已关闭")

    async def health_check(self) -> dict[str, Any]:
        """报告本地客户端就绪状态，不伪造远端授权结论。

        aiohttp session 创建成功只证明本地凭证已配置、客户端可发请求，
        不能证明 DataForSEO 已接受该凭证。
        """
        has_session = self.client._session is not None
        return {
            "status": "ok" if has_session else "degraded",
            "session_ready": has_session,
            "auth_valid": None,
            "mode": self.runtime_mode,
            "total_cost": self.client._total_cost,
            "request_count": self.client._request_count,
        }

    # ============================================================
    # 辅助方法
    # ============================================================

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _resolve_limit(limit: int | None) -> int:
        """解析单次查询条数，默认使用小批量配置并限制最大值。"""
        value = settings.dataforseo_default_limit if limit is None else limit
        return max(1, min(int(value), 1000))

    def _extract_result(self, raw: dict) -> list[dict]:
        """从 DataForSEO 响应中提取 items 数组。

        DataForSEO 两种响应结构:
          1. task["result"][0]["items"] — competitors_domain / ranked_keywords 等 Labs API
          2. task["result"] → list — backlinks / SERP 等直接返回列表
          3. task["data"]["items"] — 非 live 端点
        """
        tasks = raw.get("tasks")
        if not tasks:
            return []
        task = tasks[0]

        result = task.get("result")
        if result and isinstance(result, list) and len(result) > 0:
            # 检查是否嵌套了 items
            if isinstance(result[0], dict) and "items" in result[0]:
                return result[0]["items"]
            return result

        data = task.get("data")
        if data and isinstance(data, dict):
            return data.get("items", [])

        return []

    # ============================================================
    # 搜索表现 — SEARCH_METRICS / ORGANIC_KEYWORDS
    # ============================================================

    async def get_search_metrics(
        self, site_id: str, country: str = "us", **kwargs: Any
    ) -> SearchMetrics:
        """获取站点搜索表现总览。

        使用域名总览端点，并校验响应 target；没有本站指标时明确报错。
        """
        limit = self._resolve_limit(kwargs.get("limit"))
        raw = await self.client.request(
            "domain_rank_overview",
            task_body={
                "target": site_id,
                "location_name": _country_to_name(country),
                "language_code": _language_code(str(kwargs.get("language") or "en")),
                "limit": limit,
            },
        )
        results = self._extract_result(raw)
        now = self._now()

        groups = (raw.get("tasks") or [{}])[0].get("result") or []
        own = None
        for group in groups:
            if not isinstance(group, dict) or not _is_same_domain(
                str(group.get("target") or ""), site_id
            ):
                continue
            own = next((item for item in group.get("items", []) if isinstance(item, dict)), None)
            if own is not None:
                break
        # 兼容已有离线样本，仍必须准确匹配域名。
        if own is None and not any(
            isinstance(group, dict) and "target" in group for group in groups
        ):
            own = next((
                row for row in results if isinstance(row, dict)
                and isinstance(row.get("domain"), str)
                and _is_same_domain(row["domain"], site_id)
            ), None)
        if own is None:
            raise InternalProviderResponseError(
                "站点总览响应缺少当前域名的指标，不能使用竞品数据代替",
                provider="dataforseo",
            )

        metrics = own.get("metrics", {}).get("organic", {})
        if not isinstance(metrics, dict) or not metrics:
            raise InternalProviderResponseError("站点总览缺少自然搜索指标", provider="dataforseo")

        return SearchMetrics(
            source=DataSource.DATAFORSEO,
            fetched_at=now,
            data_date=None,
            is_estimated=True,
            integrity=DataIntegrity.COMPLETE if results else DataIntegrity.NOT_AVAILABLE,
            domain_rating=None,  # DataForSEO 不提供 DR
            ahrefs_rank=None,
            organic_traffic_estimate=_round_optional_int(metrics.get("etv")) or 0,
            traffic_value_estimate=None,
            organic_keywords_count=metrics.get("count", 0),
            top_3_count=_ranking_bucket_total(metrics, "top_3"),
            top_10_count=_ranking_bucket_total(metrics, "top_10"),
            ranked_pages_count=None,
            country={"code": country},
            protocol="https",
        )

    async def get_organic_keywords(
        self, site_id: str, country: str = "us", limit: int | None = None, **kwargs: Any
    ) -> list[OrganicKeyword]:
        """获取域名排名关键词列表。

        优先解析 DataForSEO 当前 ``keyword_data`` / ``ranked_serp_element``
        嵌套契约，并兼容项目历史伪响应的扁平结构。
        """
        limit = self._resolve_limit(limit)
        raw = await self.client.request(
            "ranked_keywords",
            task_body={
                "target": site_id,
                "location_name": _country_to_name(country),
                "language_code": _language_code(str(kwargs.get("language") or "en")),
                "limit": min(limit, 1000),
            },
            max_pages=(limit // 100) + 1 if limit > 100 else 1,
        )
        results = self._extract_result(raw)[:limit]
        if not results:
            return []

        mapped_rows: list[dict[str, Any]] = []
        invalid_count = 0
        for item in results:
            mapped = _map_organic_keyword(item)
            if mapped is None:
                invalid_count += 1
                continue
            mapped_rows.append(mapped)

        if not mapped_rows:
            sample_keys = sorted(results[0].keys()) if isinstance(results[0], dict) else []
            raise InternalProviderResponseError(
                "DataForSEO ranked_keywords 响应包含条目，但无法解析关键词明细字段",
                provider="dataforseo",
                debug_context={
                    "item_count": len(results),
                    "sample_keys": sample_keys,
                },
            )

        integrity = DataIntegrity.PARTIAL if invalid_count else DataIntegrity.COMPLETE
        now = self._now()
        return [
            OrganicKeyword(
                source=DataSource.DATAFORSEO,
                fetched_at=now,
                is_estimated=True,
                integrity=integrity,
                **mapped,
            )
            for mapped in mapped_rows
        ]

    # ============================================================
    # 关键词研究 — KEYWORD_OVERVIEW / SERP_OVERVIEW
    # ============================================================

    async def get_keyword_overview(
        self, keywords: list[str], country: str = "us", **kwargs: Any
    ) -> list[KeywordOverview]:
        """获取关键词概览。

        DataForSEO Labs keyword_overview，按 20 个词分批核对。
        """
        if not isinstance(keywords, list) or not 1 <= len(keywords) <= 50 or any(
            not isinstance(word, str) or not word.strip()
            or len(word.strip()) > 80 or len(word.split()) > 10
            for word in keywords
        ):
            raise ValueError("需要 1-50 个非空关键词，每个最多 80 个字符、10 个词")
        all_items = []
        batch_size = 20

        for i in range(0, len(keywords), batch_size):
            batch = keywords[i : i + batch_size]
            raw = await self.client.request(
                "keyword_overview",
                task_body={
                    "keywords": batch,
                    "location_name": _country_to_name(country),
                    "language_code": _language_code(str(kwargs.get("language") or "en")),
                    "include_serp_info": False,
                },
            )
            results = self._extract_result(raw)
            now = self._now()

            for item in results:
                keyword = item.get("keyword")
                if not isinstance(keyword, str) or not keyword.strip():
                    continue
                info = _mapping(item.get("keyword_info")) if "keyword_info" in item else item
                properties = _mapping(item.get("keyword_properties"))
                intent = _mapping(item.get("search_intent_info"))
                all_items.append(
                    KeywordOverview(
                        source=DataSource.DATAFORSEO,
                        fetched_at=now,
                        is_estimated=True,
                        integrity=(
                            DataIntegrity.COMPLETE if info.get("search_volume") is not None
                            else DataIntegrity.PARTIAL
                        ),
                        keyword=keyword.strip(),
                        country={"code": country},
                        search_volume=info.get("search_volume"),
                        global_volume=item.get("global_search_volume"),
                        keyword_difficulty=properties.get(
                            "keyword_difficulty", item.get("keyword_difficulty")
                        ),
                        traffic_potential=item.get("traffic_potential"),
                        cpc=info.get("cpc"),
                        intent=intent.get("main_intent", item.get("search_intent")),
                        volume_trend=None,
                        updated_at=info.get("last_updated_time", item.get("last_updated_time")),
                    )
                )

        return all_items

    async def get_serp_overview(
        self, keyword: str, country: str = "us", **kwargs: Any
    ) -> list[SerpResult]:
        """获取 Google Organic Advanced SERP 结果和标准化 SERP 特征摘要。"""
        query = keyword.strip()
        if not query:
            raise ValueError("DataForSEO keyword 不能为空")

        limit = max(1, min(int(kwargs.get("limit") or 10), 200))
        language_code = _language_code(str(kwargs.get("language") or "en"))
        device, operating_system = _normalize_device_os(
            kwargs.get("device"), kwargs.get("os")
        )
        location_name = _optional_text(kwargs.get("location_name"))
        location_coordinate = _optional_text(kwargs.get("location_coordinate"))
        if location_name and location_coordinate:
            raise ValueError("location_name 与 location_coordinate 不能同时提供")

        task_body: dict[str, Any] = {
            "keyword": query,
            "language_code": language_code,
            "depth": limit,
        }
        if location_coordinate:
            task_body["location_coordinate"] = location_coordinate
        else:
            task_body["location_name"] = location_name or _country_to_name(country)
        if device:
            task_body["device"] = device
        if operating_system:
            task_body["os"] = operating_system
        if bool(kwargs.get("load_ai_overview", False)):
            task_body["load_async_ai_overview"] = True
        se_domain = _normalize_se_domain(kwargs.get("se_domain"))
        if se_domain:
            task_body["se_domain"] = se_domain

        raw = await self.client.request(
            "serp_google_organic",
            task_body=task_body,
        )
        results = self._extract_result(raw)
        requested_types = _normalize_result_types(kwargs.get("result_types"))
        if requested_types:
            results = [item for item in results if str(item.get("type")) in requested_types]

        now = self._now()
        mapped: list[SerpResult] = []
        for item in results:
            row = _map_serp_result(item, now)
            if row is not None:
                mapped.append(row)
            if len(mapped) >= limit:
                break
        return mapped

    # ============================================================
    # 外链 — BACKLINKS_LIST / REFDOMAINS / BROKEN_BACKLINKS
    # ============================================================

    async def get_backlinks(
        self, site_id: str, limit: int | None = None, **kwargs: Any
    ) -> list[Backlink]:
        """获取外链列表。

        DataForSEO: backlinks/backlinks/live → list[Backlink]
        """
        limit = self._resolve_limit(limit)
        raw = await self.client.request(
            "backlinks_list",
            task_body={
                "target": site_id,
                "limit": min(limit, 1000),
                "backlinks_status_type": "all",
            },
            max_pages=(limit // 100) + 1 if limit > 100 else 1,
        )
        results = self._extract_result(raw)
        now = self._now()

        items = []
        for item in results[:limit]:
            link_type = "dofollow" if item.get("dofollow") else "nofollow"
            items.append(
                Backlink(
                    source=DataSource.DATAFORSEO,
                    fetched_at=now,
                    is_estimated=False,
                    source_url=item.get("page_from", ""),
                    source_title=item.get("page_from_title"),
                    target_url=item.get("target_url", item.get("url_to", "")),
                    anchor_text=item.get("anchor"),
                    link_type=link_type,
                    source_domain_rating=None,
                    source_url_rating=None,
                    source_traffic_estimate=None,
                    first_seen=_normalize_datetime(item.get("first_seen")),
                    last_seen=_normalize_datetime(item.get("last_visited")),
                    is_lost=item.get("is_lost", False),
                    lost_reason="broken" if item.get("is_broken") else None,
                )
            )
        return items

    async def get_refdomains(
        self, site_id: str, limit: int | None = None, **kwargs: Any
    ) -> list[RefDomain]:
        """获取引用域名。

        DataForSEO: 通过 backlinks/backlinks/live + mode=one_per_domain
        或 bulk_referring_domains 获取。
        """
        limit = self._resolve_limit(limit)
        raw = await self.client.request(
            "backlinks_list",
            task_body={
                "target": site_id,
                "mode": "one_per_domain",
                "limit": min(limit, 1000),
            },
        )
        results = self._extract_result(raw)
        now = self._now()

        # 按 domain_from 聚合
        domain_map: dict[str, dict] = {}
        for item in results:
            domain = item.get("domain_from", "")
            if domain not in domain_map:
                domain_map[domain] = {
                    "domain": domain,
                    "backlinks_count": 0,
                    "dofollow_count": 0,
                    "domain_from_rank": item.get("domain_from_rank"),
                    "first_seen": item.get("first_seen"),
                    "last_seen": item.get("last_visited"),
                    "primary_url": item.get("page_from"),
                }
            domain_map[domain]["backlinks_count"] += 1
            if item.get("dofollow"):
                domain_map[domain]["dofollow_count"] += 1

        items = []
        for d in list(domain_map.values())[:limit]:
            items.append(
                RefDomain(
                    source=DataSource.DATAFORSEO,
                    fetched_at=now,
                    is_estimated=False,
                    domain=d["domain"],
                    domain_rating=None,
                    backlinks_count=d["backlinks_count"],
                    dofollow_count=d["dofollow_count"],
                    first_seen=_normalize_datetime(d["first_seen"]),
                    last_seen=_normalize_datetime(d["last_seen"]),
                    is_lost=False,
                    primary_target_url=d.get("primary_url"),
                    organic_traffic_estimate=None,
                )
            )
        return items

    async def get_broken_backlinks(
        self, site_id: str, limit: int | None = None, **kwargs: Any
    ) -> list[BrokenBacklink]:
        """获取失效外链。

        DataForSEO: backlinks/backlinks/live + filter is_broken=true
        """
        limit = self._resolve_limit(limit)
        raw = await self.client.request(
            "backlinks_list",
            task_body={
                "target": site_id,
                "filters": [["is_broken", "=", True]],
                "limit": min(limit, 1000),
            },
        )
        results = self._extract_result(raw)
        now = self._now()

        items = []
        for item in results[:limit]:
            items.append(
                BrokenBacklink(
                    source=DataSource.DATAFORSEO,
                    fetched_at=now,
                    is_estimated=False,
                    source_url=item.get("page_from", ""),
                    target_url=item.get("target_url", ""),
                    http_status=item.get("target_status_code"),
                    redirect_url=item.get("target_redirect_url"),
                    anchor_text=item.get("anchor"),
                    source_domain_rating=None,
                    first_seen=_normalize_datetime(item.get("first_seen")),
                    last_seen=_normalize_datetime(item.get("last_visited")),
                    suggested_action=_suggest_broken_action(item),
                )
            )
        return items

    # ============================================================
    # 竞争分析 — ORGANIC_COMPETITORS
    # ============================================================

    async def get_organic_competitors(
        self, site_id: str, country: str = "us", limit: int | None = None, **kwargs: Any
    ) -> list[OrganicCompetitor]:
        """获取自然搜索竞争对手。

        DataForSEO: competitors_domain → list[OrganicCompetitor]
        返回的 items 第一个是目标自身，需跳过。
        """
        limit = self._resolve_limit(limit)
        raw = await self.client.request(
            "competitors_domain",
            task_body={
                "target": site_id,
                "location_name": _country_to_name(country),
                "language_code": _language_code(str(kwargs.get("language") or "en")),
                "include_clickstream_data": False,
                "limit": limit,
            },
        )
        results = self._extract_result(raw)
        now = self._now()

        items = []
        for item in results:
            domain = item.get("domain", "")
            # 跳过目标自身
            if _is_same_domain(domain, site_id):
                continue

            metrics = item.get("metrics", {}).get("organic", {})
            items.append(
                OrganicCompetitor(
                    source=DataSource.DATAFORSEO,
                    fetched_at=now,
                    is_estimated=True,
                    domain=domain,
                    domain_rating=None,
                    common_keywords_count=item.get("intersections", metrics.get("count", 0)),
                    competitor_keywords_count=metrics.get("count", 0),
                    overlap_percentage=None,
                    organic_traffic_estimate=int(metrics.get("etv", 0)),
                    top_pages_count=None,
                )
            )
        return items[:limit]

    # ============================================================
    # 管理 — SUBSCRIPTION_INFO
    # ============================================================

    async def get_subscription_info(self, **kwargs: Any) -> SubscriptionInfo:
        """获取订阅额度信息。

        DataForSEO 没有专门的额度查询端点，基于 client 内 cost 追踪返回。
        """
        return SubscriptionInfo(
            source=DataSource.DATAFORSEO,
            fetched_at=self._now(),
            is_estimated=False,
            plan_name="DataForSEO Pay-as-you-go",
            api_units_limit=0,
            api_units_used=0,
            api_units_remaining=-1,  # 按量付费，无固定额度
            usage_percentage=0,
            rate_limit_per_minute=self.client.config.rate_limit_rpm,
        )


# ============================================================
# 辅助映射函数
# ============================================================


def _map_organic_keyword(item: dict[str, Any]) -> dict[str, Any] | None:
    """将真实或历史伪结构转换为 OrganicKeyword 字段。"""
    keyword_data = _mapping(item.get("keyword_data"))
    keyword_info = _mapping(keyword_data.get("keyword_info"))
    keyword_properties = _mapping(keyword_data.get("keyword_properties"))
    search_intent = _mapping(keyword_data.get("search_intent_info"))
    serp_info = _mapping(keyword_data.get("serp_info"))

    ranked_element = _mapping(item.get("ranked_serp_element"))
    serp_item = _mapping(ranked_element.get("serp_item"))
    rank_changes = _mapping(serp_item.get("rank_changes"))

    legacy_ranked = _mapping(item.get("ranked_domain"))
    legacy_changes = _mapping(legacy_ranked.get("rank_changes"))

    keyword = _first_value(keyword_data.get("keyword"), item.get("keyword"))
    position = _round_optional_int(
        _first_value(
            serp_item.get("rank_absolute"),
            serp_item.get("rank_group"),
            legacy_ranked.get("rank_absolute"),
            legacy_ranked.get("rank_group"),
        )
    )
    previous_position = _round_optional_int(
        _first_value(
            rank_changes.get("previous_rank_absolute"),
            legacy_changes.get("previous_rank_absolute"),
            item.get("previous_position"),
        )
    )
    url = _first_value(serp_item.get("url"), legacy_ranked.get("url"), item.get("url"))

    if not isinstance(keyword, str) or not keyword.strip() or (position is None and not url):
        return None

    search_volume = _round_optional_int(
        _first_value(keyword_info.get("search_volume"), item.get("search_volume"))
    )
    keyword_difficulty = _round_optional_int(
        _first_value(
            keyword_properties.get("keyword_difficulty"),
            ranked_element.get("keyword_difficulty"),
            item.get("keyword_difficulty"),
        )
    )
    traffic_estimate = _round_optional_int(
        _first_value(serp_item.get("etv"), item.get("estimated_traffic"))
    )
    intent = _first_value(search_intent.get("main_intent"), item.get("search_intent"))
    serp_features = _first_value(
        ranked_element.get("serp_item_types"),
        serp_info.get("serp_item_types"),
        item.get("serp_features"),
    )
    if not isinstance(serp_features, list):
        serp_features = []

    return {
        "keyword": keyword.strip(),
        "position": position,
        "previous_position": previous_position,
        "position_change": (
            previous_position - position
            if previous_position is not None and position is not None
            else None
        ),
        "search_volume": search_volume or 0,
        "keyword_difficulty": keyword_difficulty,
        "cpc": _first_value(keyword_info.get("cpc"), item.get("cpc")),
        "url": str(url) if url else None,
        "traffic_estimate": traffic_estimate,
        "intent": str(intent) if intent else None,
        "serp_features": [str(value) for value in serp_features],
    }


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_value(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _ranking_bucket_total(metrics: dict[str, Any], bucket: str) -> int:
    """将 DataForSEO 互斥排名区间转换为累计 Top 3 / Top 10。"""
    pos_1 = _round_optional_int(metrics.get("pos_1")) or 0
    pos_2_3 = _round_optional_int(metrics.get("pos_2_3"))
    pos_4_10 = _round_optional_int(metrics.get("pos_4_10"))

    if pos_2_3 is not None or pos_4_10 is not None:
        top_3 = pos_1 + (pos_2_3 or 0)
        return top_3 if bucket == "top_3" else top_3 + (pos_4_10 or 0)

    # 兼容历史伪响应。旧契约无法精确拆出 2-3 名，因此只用于测试和旧数据回放。
    legacy_top_5 = _round_optional_int(metrics.get("pos_1_5")) or pos_1
    legacy_6_10 = _round_optional_int(metrics.get("pos_6_10")) or 0
    return pos_1 if bucket == "top_3" else legacy_top_5 + legacy_6_10


def _round_optional_int(value: Any) -> int | None:
    """将供应商数值按四舍五入转为整数，缺失或非法值保留为 None。"""
    if value is None:
        return None
    try:
        return int(float(value) + 0.5)
    except (TypeError, ValueError, OverflowError):
        return None


def _normalize_datetime(value: str | datetime | None) -> str | datetime | None:
    """移除 DataForSEO 时间字符串中时区前的多余空格。"""
    if not isinstance(value, str):
        return value
    return value.replace(" +", "+").replace(" -", "-")


def _language_code(value: str) -> str:
    language = value.strip().lower().replace("_", "-").split("-", 1)[0]
    if len(language) != 2 or not language.isalpha():
        raise ValueError("DataForSEO language 必须是有效两位语言代码")
    return language


def _normalize_device_os(device: Any, operating_system: Any) -> tuple[str | None, str | None]:
    selected_device = _enum_text(device)
    selected_os = _enum_text(operating_system)
    if selected_device and selected_device not in {"desktop", "mobile"}:
        raise ValueError("DataForSEO device 仅支持 desktop 或 mobile")
    if selected_os:
        allowed = {
            "desktop": {"windows", "macos"},
            "mobile": {"android", "ios"},
        }
        inferred_device = "mobile" if selected_os in {"android", "ios"} else "desktop"
        selected_device = selected_device or inferred_device
        if selected_os not in allowed[selected_device]:
            raise ValueError(f"{selected_os} 不能用于 {selected_device} 设备")
    return selected_device, selected_os


def _normalize_result_types(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        values = [value]
    else:
        values = list(value)
    return {str(item).strip().lower() for item in values if str(item).strip()}


def _map_serp_result(item: Any, fetched_at: str) -> SerpResult | None:
    if not isinstance(item, dict):
        return None
    try:
        rank = int(item.get("rank_absolute", item.get("rank_group", 0)))
    except (TypeError, ValueError):
        return None
    if not 1 <= rank <= 200:
        return None

    result_type = str(item.get("type") or "organic")
    is_ai_overview = result_type == "ai_overview"
    markdown = _optional_text(item.get("markdown"))
    description = _optional_text(item.get("description") or item.get("snippet"))
    if is_ai_overview and description is None:
        description = _first_nested_text(item.get("items")) or markdown
    title = _optional_text(item.get("title"))
    if is_ai_overview and title is None:
        title = "AI Overview"

    return SerpResult(
        source=DataSource.DATAFORSEO,
        fetched_at=fetched_at,
        is_estimated=False,
        rank=rank,
        type=result_type,
        title=title,
        url=_optional_text(item.get("url")),
        description=description,
        page=_optional_int(item.get("page")),
        position=_optional_text(item.get("position")),
        markdown=markdown,
        reference_urls=_collect_reference_urls(item.get("items")),
        asynchronous=(
            _optional_bool(item.get("asynchronous_ai_overview"))
            if is_ai_overview
            else None
        ),
    )


def _first_nested_text(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("text", "description", "snippet", "markdown"):
            text = _optional_text(value.get(key))
            if text:
                return text
        for nested in value.values():
            text = _first_nested_text(nested)
            if text:
                return text
    elif isinstance(value, list):
        for nested in value:
            text = _first_nested_text(nested)
            if text:
                return text
    return None


def _collect_reference_urls(value: Any) -> list[str]:
    urls: list[str] = []

    def visit(node: Any, *, in_reference: bool = False) -> None:
        if isinstance(node, dict):
            node_type = str(node.get("type") or "")
            reference = in_reference or "reference" in node_type or "link" in node_type
            url = _optional_text(node.get("url"))
            if reference and url and url not in urls:
                urls.append(url)
            for key, nested in node.items():
                visit(nested, in_reference=reference or key in {"references", "links"})
        elif isinstance(node, list):
            for nested in node:
                visit(nested, in_reference=in_reference)

    visit(value)
    return urls


def _normalize_se_domain(value: Any) -> str | None:
    domain = _optional_text(value)
    if domain is None:
        return None
    domain = domain.lower()
    if not domain.startswith("google.") or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789.-"
        for character in domain
    ):
        raise ValueError("DataForSEO se_domain 必须是有效 Google 域名")
    return domain


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _enum_text(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return _optional_text(raw.lower() if isinstance(raw, str) else raw)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _country_to_name(code: str) -> str:
    """国家代码 → DataForSEO location_name。"""
    mapping = {
        "us": "United States",
        "gb": "United Kingdom",
        "cn": "China",
        "de": "Germany",
        "fr": "France",
        "jp": "Japan",
        "au": "Australia",
        "ca": "Canada",
        "br": "Brazil",
        "in": "India",
    }
    return mapping.get(code.lower(), "United States")


def _suggest_broken_action(item: dict) -> str:
    """根据失效外链情况推荐处理方式。"""
    status = item.get("target_status_code")
    if status and 300 <= status < 400:
        return "301_redirect"
    if status == 404 or status == 410:
        return "restore_page"
    if item.get("domain_from_rank", 0) > 70:
        return "contact_source"
    return "accept_loss"


def _is_same_domain(domain: str, target: str) -> bool:
    """判断 domain 是否与 target 相同（忽略 www 和协议前缀）。"""

    def clean(d: str) -> str:
        d = d.lower().strip()
        for prefix in ("https://", "http://", "www."):
            if d.startswith(prefix):
                d = d[len(prefix) :]
        return d.rstrip("/")

    return clean(domain) == clean(target)
