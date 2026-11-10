from __future__ import annotations

import pytest

from src.models.common import Capability, DataSource, Device
from src.providers.base import BaseProvider, ProviderMeta
from src.routing.contracts import (
    ProviderFeature,
    ProviderProfile,
    QueryIntent,
    QueryPurpose,
)
from src.routing.router import ProviderRouter, ProviderRoutingError


class StubProvider(BaseProvider):
    def __init__(self, profile: ProviderProfile) -> None:
        super().__init__()
        self._profile = profile

    @property
    def meta(self) -> ProviderMeta:
        return ProviderMeta(
            name=self._profile.provider_name,
            label=self._profile.provider_name,
            source=self._profile.source,
            supported_capabilities=set(self._profile.capabilities),
        )

    @property
    def runtime_mode(self):
        return self._profile.runtime_mode.value

    @property
    def routing_profile(self) -> ProviderProfile:
        return self._profile


def profile(
    name: str,
    source: DataSource,
    *,
    features: set[ProviderFeature],
    max_results: int,
    fixed_language: str | None = None,
) -> ProviderProfile:
    return ProviderProfile(
        provider_name=name,
        source=source,
        runtime_mode="live",
        capabilities={Capability.SERP_OVERVIEW},
        supported_features=features,
        implemented_features=features,
        capability_limits={Capability.SERP_OVERVIEW: max_results},
        fixed_language=fixed_language,
    )


def providers() -> tuple[StubProvider, StubProvider]:
    serper = StubProvider(
        profile(
            "serper",
            DataSource.SERPER,
            features={
                ProviderFeature.REALTIME,
                ProviderFeature.COUNTRY_TARGETING,
                ProviderFeature.LANGUAGE_TARGETING,
                ProviderFeature.PAGINATION,
                ProviderFeature.AUTOCORRECT,
            },
            max_results=100,
        )
    )
    dataforseo = StubProvider(
        profile(
            "dataforseo",
            DataSource.DATAFORSEO,
            features={
                ProviderFeature.REALTIME,
                ProviderFeature.COUNTRY_TARGETING,
                ProviderFeature.DEEP_RESULTS,
            },
            max_results=200,
            fixed_language="en",
        )
    )
    return serper, dataforseo


def test_router_is_independent_of_provider_registration_order() -> None:
    serper, dataforseo = providers()
    intent = QueryIntent(
        capability=Capability.SERP_OVERVIEW,
        purpose=QueryPurpose.QUICK_LOOKUP,
        country="gb",
        language="en",
        limit=10,
    )

    first = ProviderRouter().resolve(intent, [dataforseo, serper], live_only=True)
    second = ProviderRouter().resolve(intent, [serper, dataforseo], live_only=True)

    assert first.primary.meta.name == "serper"
    assert second.primary.meta.name == "serper"
    assert first.decision.selected_provider == "serper"
    assert first.decision.fallback_candidates == ("dataforseo",)


def test_router_uses_purpose_and_explicit_provider_preference() -> None:
    serper, dataforseo = providers()
    analysis = QueryIntent(
        capability=Capability.SERP_OVERVIEW,
        purpose=QueryPurpose.SEO_ANALYSIS,
        language="en-US",
    )
    preferred = QueryIntent(
        capability=Capability.SERP_OVERVIEW,
        purpose=QueryPurpose.QUICK_LOOKUP,
        language="en",
        preferred_provider="dataforseo",
        allow_fallback=False,
    )

    analysis_route = ProviderRouter().resolve(analysis, [serper, dataforseo], live_only=True)
    preferred_route = ProviderRouter().resolve(preferred, [serper, dataforseo], live_only=True)

    assert analysis_route.primary.meta.name == "dataforseo"
    assert preferred_route.primary.meta.name == "dataforseo"
    assert preferred_route.decision.mode.value == "preferred"
    assert preferred_route.fallbacks == ()


def test_router_matches_pagination_language_depth_and_implemented_features() -> None:
    serper, dataforseo = providers()

    paged = QueryIntent(
        capability=Capability.SERP_OVERVIEW,
        language="en",
        page=2,
    )
    deep = QueryIntent(
        capability=Capability.SERP_OVERVIEW,
        purpose=QueryPurpose.SEO_ANALYSIS,
        language="en",
        limit=50,
    )
    non_english_analysis = QueryIntent(
        capability=Capability.SERP_OVERVIEW,
        purpose=QueryPurpose.SEO_ANALYSIS,
        language="fr",
    )

    paged_route = ProviderRouter().resolve(paged, [dataforseo, serper], live_only=True)
    deep_route = ProviderRouter().resolve(deep, [serper, dataforseo], live_only=True)
    assert paged_route.primary.meta.name == "serper"
    assert deep_route.primary.meta.name == "dataforseo"
    assert (
        ProviderRouter()
        .resolve(non_english_analysis, [dataforseo, serper], live_only=True)
        .primary.meta.name
        == "serper"
    )

    unsupported = QueryIntent(
        capability=Capability.SERP_OVERVIEW,
        language="en",
        device=Device.MOBILE,
    )
    with pytest.raises(ProviderRoutingError) as captured:
        ProviderRouter().resolve(unsupported, [serper, dataforseo], live_only=True)
    assert ProviderFeature.DEVICE in captured.value.decision.missing_features


def test_router_does_not_treat_upstream_only_features_as_implemented() -> None:
    serper, dataforseo = providers()
    upstream_only = dataforseo.routing_profile.model_copy(
        update={
            "supported_features": dataforseo.routing_profile.supported_features
            | {ProviderFeature.AI_OVERVIEW},
        }
    )
    dataforseo = StubProvider(upstream_only)
    intent = QueryIntent(
        capability=Capability.SERP_OVERVIEW,
        language="en",
        required_features={ProviderFeature.AI_OVERVIEW},
    )

    with pytest.raises(ProviderRoutingError) as captured:
        ProviderRouter().resolve(intent, [serper, dataforseo], live_only=True)
    assert captured.value.decision.missing_features == frozenset(
        {ProviderFeature.AI_OVERVIEW}
    )


def test_real_dataforseo_profile_accepts_advanced_serp_intent() -> None:
    from src.providers.dataforseo import DataForSEOProvider

    provider = DataForSEOProvider()
    provider.client = type("Client", (), {"_session": object()})()
    intent = QueryIntent(
        capability=Capability.SERP_OVERVIEW,
        purpose=QueryPurpose.SEO_ANALYSIS,
        country="gb",
        language="fr-FR",
        limit=100,
        device=Device.MOBILE,
        os="ios",
        location_name="London,England,United Kingdom",
        required_features={ProviderFeature.AI_OVERVIEW},
    )

    route = ProviderRouter().resolve(intent, [provider], live_only=True)

    assert route.primary.meta.name == "dataforseo"
    assert route.decision.missing_features == frozenset()
