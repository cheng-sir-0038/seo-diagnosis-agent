"""Facade 与 Provider 伪模式契约测试。

测试会强制清空外部凭证；真实凭证验收应使用独立集成环境执行。
"""

from datetime import datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.core.config import settings
from src.providers.ahrefs import AhrefsProvider
from src.providers.dataforseo import DataForSEOProvider
from src.providers.dataforseo.provider import _round_optional_int
from src.server import app


@pytest.fixture(autouse=True)
def disable_serper_for_existing_facade_tests(monkeypatch):
    """现有 Facade 用例不受开发机 Serper 环境变量影响。"""
    monkeypatch.setattr(settings, "serper_api_key", "")


@pytest_asyncio.fixture
async def client(monkeypatch):
    """创建显式管理 lifespan、且不会访问真实 Provider 的测试客户端。"""
    monkeypatch.setattr(settings, "dataforseo_login", "")
    monkeypatch.setattr(settings, "dataforseo_api_key", "")
    monkeypatch.setattr(settings, "ahrefs_api_key", "")

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest_asyncio.fixture
async def dataforseo_provider(monkeypatch):
    """创建强制运行在伪模式的 DataForSEO Provider。"""
    monkeypatch.setattr(settings, "dataforseo_login", "")
    monkeypatch.setattr(settings, "dataforseo_api_key", "")

    provider = DataForSEOProvider()
    await provider.startup()
    try:
        yield provider
    finally:
        await provider.shutdown()


class TestQueryAPI:
    """POST /api/v1/query 端点测试。"""

    @pytest.mark.asyncio
    async def test_query_subscription_info(self, client):
        """测试查询订阅信息。"""
        response = await client.post(
            "/api/v1/query",
            json={
                "capability": "subscription_info",
                "site_id": None,
                "params": {},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "meta" in data
        assert "data" in data
        assert data["meta"]["capability"] == "subscription_info"
        assert data["meta"]["source"] == "dataforseo"

    @pytest.mark.asyncio
    async def test_query_search_metrics(self, client):
        """测试查询搜索表现总览。"""
        response = await client.post(
            "/api/v1/query",
            json={
                "capability": "search_metrics",
                "site_id": "example.com",
                "params": {"country": "us"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "domain_rating" in data["data"]

    @pytest.mark.asyncio
    async def test_query_reuses_in_process_cache(self, client):
        payload = {
            "capability": "search_metrics",
            "site_id": "example.com",
            "params": {"country": "us"},
        }

        first = await client.post("/api/v1/query", json=payload)
        second = await client.post("/api/v1/query", json=payload)

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["meta"]["cached"] is False
        assert second.json()["meta"]["cached"] is True

    @pytest.mark.asyncio
    async def test_query_unsupported_capability(self, client):
        """测试枚举中存在、但尚无 Provider 注册的 capability。"""
        response = await client.post(
            "/api/v1/query",
            json={
                "capability": "best_pages_by_links",
                "site_id": "example.com",
                "params": {},
            },
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "UNSUPPORTED_CAPABILITY"

    @pytest.mark.asyncio
    async def test_query_invalid_capability(self, client):
        """测试无效的 capability 应返回 422。"""
        response = await client.post(
            "/api/v1/query",
            json={
                "capability": "not_a_real_capability",
                "site_id": "example.com",
                "params": {},
            },
        )
        assert response.status_code == 422  # Pydantic validation error


class TestDataForSEOProviderContracts:
    """DataForSEO 响应必须能构造统一模型。"""

    @pytest.mark.asyncio
    async def test_search_metrics_preserves_unavailable_vendor_fields_as_none(
        self, dataforseo_provider
    ):
        metrics = await dataforseo_provider.get_search_metrics("example.com")

        assert metrics.domain_rating is None
        assert metrics.ranked_pages_count is None

    def test_round_optional_int_preserves_missing_values(self):
        assert _round_optional_int(None) is None
        assert _round_optional_int(12.5) == 13
        assert _round_optional_int(12.6) == 13
        assert _round_optional_int(12.4) == 12

    def test_provider_default_limit_is_small_and_configurable(self, monkeypatch):
        provider = DataForSEOProvider()
        monkeypatch.setattr(settings, "dataforseo_default_limit", 2)

        assert provider._resolve_limit(None) == 2
        assert provider._resolve_limit(1) == 1

    @pytest.mark.asyncio
    async def test_organic_keywords_normalizes_missing_serp_features(self, dataforseo_provider):
        keywords = await dataforseo_provider.get_organic_keywords("example.com", limit=10)

        assert keywords
        assert keywords[0].serp_features == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method_name",
        ["get_backlinks", "get_refdomains", "get_broken_backlinks"],
    )
    async def test_backlink_methods_normalize_dataforseo_timestamps(
        self, dataforseo_provider, method_name
    ):
        records = await getattr(dataforseo_provider, method_name)("example.com", limit=10)

        assert records
        assert isinstance(records[0].first_seen, datetime)
        assert isinstance(records[0].last_seen, datetime)


class TestProviderHealth:
    """本地客户端状态不能伪装成远端授权已经验证。"""

    @pytest.mark.asyncio
    async def test_dataforseo_session_ready_does_not_mean_auth_valid(self):
        provider = DataForSEOProvider()
        provider.client = SimpleNamespace(
            _session=object(),
            _total_cost=0.0,
            _request_count=0,
        )

        health = await provider.health_check()

        assert health["session_ready"] is True
        assert health["auth_valid"] is None

    @pytest.mark.asyncio
    async def test_ahrefs_pseudo_mode_does_not_mean_auth_valid(self):
        provider = AhrefsProvider()
        await provider.startup()
        try:
            health = await provider.health_check()
        finally:
            await provider.shutdown()

        assert health["status"] == "degraded"
        assert health["mode"] == "pseudo"
        assert health["auth_valid"] is None


@pytest.mark.asyncio
async def test_live_capabilities_exclude_all_pseudo_providers(monkeypatch):
    from src.facade.data_facade import DataFacade
    from src.models.common import Capability

    monkeypatch.setattr(settings, "dataforseo_login", "")
    monkeypatch.setattr(settings, "dataforseo_api_key", "")
    monkeypatch.setattr(settings, "ahrefs_api_key", "")
    facade = DataFacade()
    await facade.startup()
    try:
        assert facade.live_capabilities() == {Capability.PAGE_METADATA}
        assert Capability.SEARCH_HISTORY not in facade.live_capabilities()
    finally:
        await facade.shutdown()


@pytest.mark.asyncio
async def test_live_capabilities_only_include_configured_dataforseo(monkeypatch):
    from src.facade.data_facade import DataFacade
    from src.models.common import Capability

    monkeypatch.setattr(settings, "dataforseo_login", "configured-login")
    monkeypatch.setattr(settings, "dataforseo_api_key", "configured-key")
    monkeypatch.setattr(settings, "ahrefs_api_key", "")
    facade = DataFacade()
    await facade.startup()
    try:
        capabilities = facade.live_capabilities()
        assert Capability.SEARCH_METRICS in capabilities
        assert Capability.ORGANIC_KEYWORDS in capabilities
        assert Capability.PAGE_METADATA in capabilities
        assert Capability.PAGE_PERFORMANCE not in capabilities
        assert Capability.SEARCH_HISTORY not in capabilities
        assert Capability.AUDIT_ISSUES not in capabilities
        assert Capability.TOP_PAGES not in capabilities
        assert Capability.SITE_PROFILE not in capabilities
    finally:
        await facade.shutdown()


@pytest.mark.asyncio
async def test_query_live_rejects_pseudo_only_capability(monkeypatch):
    from src.core.exceptions import InternalDataNotAvailableError
    from src.facade.data_facade import DataFacade
    from src.models.common import Capability

    monkeypatch.setattr(settings, "dataforseo_login", "")
    monkeypatch.setattr(settings, "dataforseo_api_key", "")
    monkeypatch.setattr(settings, "ahrefs_api_key", "")
    facade = DataFacade()
    await facade.startup()
    try:
        with pytest.raises(InternalDataNotAvailableError, match="没有真实 Provider"):
            await facade.query_live(
                Capability.SEARCH_HISTORY,
                site_id="example.com",
                params={"country": "us"},
            )
    finally:
        await facade.shutdown()


@pytest.mark.asyncio
async def test_pagespeed_requires_api_key_for_live_capability(monkeypatch):
    from src.facade.data_facade import DataFacade
    from src.models.common import Capability

    monkeypatch.setattr(settings, "dataforseo_login", "")
    monkeypatch.setattr(settings, "dataforseo_api_key", "")
    monkeypatch.setattr(settings, "ahrefs_api_key", "")
    monkeypatch.setattr(settings, "pagespeed_api_key", "")
    facade = DataFacade()
    await facade.startup()
    try:
        assert Capability.PAGE_PERFORMANCE not in facade.live_capabilities()
    finally:
        await facade.shutdown()


@pytest.mark.asyncio
async def test_pagespeed_is_live_when_api_key_is_configured(monkeypatch):
    from src.facade.data_facade import DataFacade
    from src.models.common import Capability

    monkeypatch.setattr(settings, "dataforseo_login", "")
    monkeypatch.setattr(settings, "dataforseo_api_key", "")
    monkeypatch.setattr(settings, "ahrefs_api_key", "")
    monkeypatch.setattr(settings, "pagespeed_api_key", "configured-key")
    facade = DataFacade()
    await facade.startup()
    try:
        capabilities = facade.live_capabilities()
        assert Capability.PAGE_PERFORMANCE in capabilities
        assert Capability.SEARCH_METRICS not in capabilities
    finally:
        await facade.shutdown()


@pytest.mark.asyncio
async def test_page_metadata_is_always_a_live_capability(monkeypatch):
    from src.facade.data_facade import DataFacade
    from src.models.common import Capability

    monkeypatch.setattr(settings, "dataforseo_login", "")
    monkeypatch.setattr(settings, "dataforseo_api_key", "")
    monkeypatch.setattr(settings, "ahrefs_api_key", "")
    monkeypatch.setattr(settings, "pagespeed_api_key", "")
    facade = DataFacade()
    await facade.startup()
    try:
        assert Capability.PAGE_METADATA in facade.live_capabilities()
        provider = facade._resolve_provider(Capability.PAGE_METADATA, live_only=True)
        assert provider.source.value == "page_html"
    finally:
        await facade.shutdown()
