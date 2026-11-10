from __future__ import annotations

from typing import Any

import pytest

from src.core.exceptions import InternalDataNotAvailableError, InternalProviderServerError
from src.facade.data_facade import DataFacade
from src.models.common import Capability, DataSource
from src.providers.base import BaseProvider, ProviderMeta
from src.routing.contracts import ProviderFeature, ProviderProfile


class StubSerpProvider(BaseProvider):
    def __init__(
        self,
        *,
        name: str,
        source: DataSource,
        features: set[ProviderFeature],
        fixed_language: str | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__()
        self.name = name
        self._source = source
        self.features = frozenset(features)
        self.fixed_language = fixed_language
        self.error = error
        self.calls: list[dict[str, Any]] = []

    @property
    def meta(self) -> ProviderMeta:
        return ProviderMeta(
            name=self.name,
            label=self.name,
            source=self._source,
            supported_capabilities={Capability.SERP_OVERVIEW},
        )

    @property
    def runtime_mode(self):
        return "live"

    @property
    def routing_profile(self) -> ProviderProfile:
        return ProviderProfile(
            provider_name=self.name,
            source=self._source,
            runtime_mode="live",
            capabilities={Capability.SERP_OVERVIEW},
            supported_features=self.features,
            implemented_features=self.features,
            fixed_language=self.fixed_language,
            capability_limits={Capability.SERP_OVERVIEW: 200},
        )

    async def get_serp_overview(self, keyword: str, country: str, **kwargs: Any) -> list[dict]:
        self.calls.append({"keyword": keyword, "country": country, **kwargs})
        if self.error is not None:
            raise self.error
        return [{"rank": 1, "url": f"https://{self.name}.example/result"}]


def build_facade(*providers: StubSerpProvider) -> DataFacade:
    facade = DataFacade()
    facade._providers = {provider.name: provider for provider in providers}
    facade._capability_map = {Capability.SERP_OVERVIEW: list(providers)}
    return facade


def serper(*, error: Exception | None = None) -> StubSerpProvider:
    return StubSerpProvider(
        name="serper",
        source=DataSource.SERPER,
        features={
            ProviderFeature.REALTIME,
            ProviderFeature.COUNTRY_TARGETING,
            ProviderFeature.LANGUAGE_TARGETING,
            ProviderFeature.PAGINATION,
        },
        error=error,
    )


def dataforseo(*, error: Exception | None = None) -> StubSerpProvider:
    return StubSerpProvider(
        name="dataforseo",
        source=DataSource.DATAFORSEO,
        features={
            ProviderFeature.REALTIME,
            ProviderFeature.COUNTRY_TARGETING,
            ProviderFeature.DEEP_RESULTS,
        },
        fixed_language="en",
        error=error,
    )


@pytest.mark.asyncio
async def test_facade_selects_by_intent_and_returns_provider_decision() -> None:
    dfs = dataforseo()
    sp = serper()
    facade = build_facade(dfs, sp)

    quick = await facade.query_live(
        Capability.SERP_OVERVIEW,
        params={
            "keyword": "seo agent",
            "country": "gb",
            "language": "en",
            "purpose": "quick_lookup",
            "limit": 10,
        },
        force_refresh=True,
    )
    analysis = await facade.query_live(
        Capability.SERP_OVERVIEW,
        params={
            "keyword": "seo agent",
            "country": "gb",
            "language": "en",
            "purpose": "seo_analysis",
            "limit": 10,
        },
        force_refresh=True,
    )

    assert quick["meta"]["source"] == "serper"
    assert quick["meta"]["provider_selection"]["selected_provider"] == "serper"
    assert analysis["meta"]["source"] == "dataforseo"
    assert analysis["meta"]["provider_selection"]["selected_provider"] == "dataforseo"
    assert "purpose" not in sp.calls[0]
    assert "preferred_provider" not in dfs.calls[0]


@pytest.mark.asyncio
async def test_facade_falls_back_only_to_provider_that_fully_satisfies_intent() -> None:
    sp = serper(
        error=InternalProviderServerError("Serper unavailable", provider="serper")
    )
    dfs = dataforseo()
    facade = build_facade(sp, dfs)

    result = await facade.query_live(
        Capability.SERP_OVERVIEW,
        params={
            "keyword": "seo agent",
            "country": "gb",
            "language": "en",
            "purpose": "quick_lookup",
            "allow_fallback": True,
        },
        force_refresh=True,
    )

    selection = result["meta"]["provider_selection"]
    assert result["meta"]["source"] == "dataforseo"
    assert selection["mode"] == "fallback"
    assert selection["fallback_used"] is True
    assert len(sp.calls) == 1
    assert len(dfs.calls) == 1


@pytest.mark.asyncio
async def test_facade_respects_no_fallback_and_never_silently_downgrades() -> None:
    sp = serper(
        error=InternalProviderServerError("Serper unavailable", provider="serper")
    )
    dfs = dataforseo()
    facade = build_facade(sp, dfs)

    with pytest.raises(InternalProviderServerError):
        await facade.query_live(
            Capability.SERP_OVERVIEW,
            params={
                "keyword": "seo agent",
                "language": "en",
                "preferred_provider": "serper",
                "allow_fallback": False,
            },
            force_refresh=True,
        )
    assert len(dfs.calls) == 0

    with pytest.raises(InternalDataNotAvailableError, match="完整满足"):
        await facade.query_live(
            Capability.SERP_OVERVIEW,
            params={
                "keyword": "seo agent",
                "language": "en",
                "device": "mobile",
            },
            force_refresh=True,
        )
