from __future__ import annotations

import pytest

from src.core.config import settings
from src.facade.data_facade import DataFacade
from src.models.common import Capability, DataSource
from src.seo_agent.chat.tools import build_seo_agent_registry

SITE = {
    "id": "site-1",
    "domain": "example.com",
    "default_country": "GB",
    "default_language": "en",
}


@pytest.mark.asyncio
async def test_serper_live_provider_owns_serp_route_and_exposes_existing_agent_tool(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "serper_api_key", "configured-serper-key")
    monkeypatch.setattr(settings, "dataforseo_login", "")
    monkeypatch.setattr(settings, "dataforseo_api_key", "")
    monkeypatch.setattr(settings, "ahrefs_api_key", "")
    monkeypatch.setattr(settings, "pagespeed_api_key", "")

    facade = DataFacade()
    await facade.startup()
    try:
        provider = facade._resolve_provider(Capability.SERP_OVERVIEW, live_only=True)
        registry = build_seo_agent_registry(facade, SITE)
        schemas = registry.schemas()
        names = {schema["function"]["name"] for schema in schemas}
        serp_schema = next(
            schema for schema in schemas if schema["function"]["name"] == "seo_serp_overview"
        )
        properties = serp_schema["function"]["parameters"]["properties"]

        assert provider.source == DataSource.SERPER
        assert Capability.SERP_OVERVIEW in facade.live_capabilities()
        assert "seo_serp_overview" in names
        assert {
            "keyword",
            "country",
            "language",
            "limit",
            "page",
            "autocorrect",
            "purpose",
            "preferred_provider",
            "allow_fallback",
            "device",
            "os",
            "location_name",
            "location_coordinate",
            "load_ai_overview",
            "result_types",
            "se_domain",
        } <= set(properties)
    finally:
        await facade.shutdown()


@pytest.mark.asyncio
async def test_serper_is_preferred_over_live_dataforseo_for_serp_only(monkeypatch) -> None:
    monkeypatch.setattr(settings, "serper_api_key", "configured-serper-key")
    monkeypatch.setattr(settings, "dataforseo_login", "configured-login")
    monkeypatch.setattr(settings, "dataforseo_api_key", "configured-key")
    monkeypatch.setattr(settings, "ahrefs_api_key", "")
    monkeypatch.setattr(settings, "pagespeed_api_key", "")

    facade = DataFacade()
    await facade.startup()
    try:
        serp_provider = facade._resolve_provider(Capability.SERP_OVERVIEW, live_only=True)
        keyword_provider = facade._resolve_provider(Capability.KEYWORD_OVERVIEW, live_only=True)

        assert serp_provider.source == DataSource.SERPER
        assert keyword_provider.source == DataSource.DATAFORSEO
    finally:
        await facade.shutdown()
