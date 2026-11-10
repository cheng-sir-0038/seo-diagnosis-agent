from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models.common import Capability, DataSource, Device
from src.providers.dataforseo import DataForSEOProvider
from src.providers.serper import SerperProvider
from src.providers.serper.client import SerperClient
from src.providers.serper.config import SerperConfig
from src.routing.contracts import (
    ProviderDecision,
    ProviderFeature,
    ProviderOperatingSystem,
    ProviderSelectionMode,
    QueryIntent,
    QueryPurpose,
)


def test_query_intent_normalizes_structured_requirements_without_raw_prompt() -> None:
    intent = QueryIntent(
        capability=Capability.SERP_OVERVIEW,
        purpose=QueryPurpose.SEO_ANALYSIS,
        country="GB",
        language="EN_gb",
        limit=50,
        os=ProviderOperatingSystem.IOS,
        location_name=" London, England, United Kingdom ",
        required_features={ProviderFeature.AI_OVERVIEW},
        preferred_provider=" DataForSEO ",
    )

    assert intent.country == "gb"
    assert intent.language == "en-gb"
    assert intent.device == Device.MOBILE
    assert intent.location_name == "London, England, United Kingdom"
    assert intent.preferred_provider == "dataforseo"
    assert intent.required_features == frozenset(
        {
            ProviderFeature.COUNTRY_TARGETING,
            ProviderFeature.LANGUAGE_TARGETING,
            ProviderFeature.DEVICE,
            ProviderFeature.OS,
            ProviderFeature.EXACT_LOCATION,
            ProviderFeature.AI_OVERVIEW,
        }
    )
    assert "user_input" not in QueryIntent.model_fields
    assert "prompt" not in QueryIntent.model_fields


def test_query_intent_rejects_conflicting_location_and_device_os() -> None:
    with pytest.raises(ValidationError, match="location_name.*location_coordinate"):
        QueryIntent(
            capability=Capability.SERP_OVERVIEW,
            location_name="London, England, United Kingdom",
            location_coordinate="51.5074,-0.1278,200",
        )

    with pytest.raises(ValidationError, match="ios.*mobile"):
        QueryIntent(
            capability=Capability.SERP_OVERVIEW,
            device=Device.DESKTOP,
            os=ProviderOperatingSystem.IOS,
        )


def test_serper_and_dataforseo_declare_different_routing_profiles() -> None:
    serper = SerperProvider(SerperClient(SerperConfig(api_key="configured")))
    dataforseo = DataForSEOProvider()
    dataforseo.client = type("Client", (), {"_session": object()})()

    serper_profile = serper.routing_profile
    dataforseo_profile = dataforseo.routing_profile

    assert serper_profile.provider_name == "serper"
    assert serper_profile.source == DataSource.SERPER
    assert serper_profile.max_results_for(Capability.SERP_OVERVIEW) == 100
    assert ProviderFeature.AUTOCORRECT in serper_profile.implemented_features
    assert ProviderFeature.DEVICE not in serper_profile.implemented_features
    assert ProviderFeature.AI_OVERVIEW not in serper_profile.implemented_features

    assert dataforseo_profile.provider_name == "dataforseo"
    assert dataforseo_profile.source == DataSource.DATAFORSEO
    assert dataforseo_profile.max_results_for(Capability.SERP_OVERVIEW) == 200
    assert ProviderFeature.DEVICE in dataforseo_profile.supported_features
    assert ProviderFeature.OS in dataforseo_profile.supported_features
    assert ProviderFeature.EXACT_LOCATION in dataforseo_profile.supported_features
    assert ProviderFeature.COORDINATES in dataforseo_profile.supported_features
    assert ProviderFeature.AI_OVERVIEW in dataforseo_profile.supported_features
    assert ProviderFeature.DEEP_RESULTS in dataforseo_profile.implemented_features
    assert ProviderFeature.DEVICE in dataforseo_profile.implemented_features
    assert ProviderFeature.AI_OVERVIEW in dataforseo_profile.implemented_features
    assert dataforseo_profile.fixed_language is None

    advanced = QueryIntent(
        capability=Capability.SERP_OVERVIEW,
        device=Device.MOBILE,
        location_name="London, England, United Kingdom",
        required_features={ProviderFeature.AI_OVERVIEW},
    )
    assert serper_profile.missing_features(advanced.required_features) == frozenset(
        {
            ProviderFeature.DEVICE,
            ProviderFeature.EXACT_LOCATION,
            ProviderFeature.AI_OVERVIEW,
        }
    )
    assert dataforseo_profile.missing_features(advanced.required_features) == frozenset()
    assert dataforseo_profile.upstream_missing_features(advanced.required_features) == frozenset()


def test_provider_decision_preserves_reasons_features_and_fallback_candidates() -> None:
    decision = ProviderDecision(
        mode=ProviderSelectionMode.AUTOMATIC,
        selected_provider="dataforseo",
        selected_source=DataSource.DATAFORSEO,
        reasons=("用户要求移动端", "用户要求精确位置"),
        matched_features={ProviderFeature.DEVICE, ProviderFeature.EXACT_LOCATION},
        fallback_candidates=("serper",),
    )

    assert decision.selected_provider == "dataforseo"
    assert decision.fallback_used is False
    assert decision.missing_features == frozenset()
    assert decision.is_satisfied is True
    assert decision.model_dump(mode="json")["matched_features"] == [
        "device",
        "exact_location",
    ]


def test_provider_decision_rejects_invalid_fallback_and_unavailable_shapes() -> None:
    with pytest.raises(ValidationError, match="fallback_used"):
        ProviderDecision(
            mode=ProviderSelectionMode.AUTOMATIC,
            selected_provider="serper",
            selected_source=DataSource.SERPER,
            fallback_used=True,
        )

    with pytest.raises(ValidationError, match="unavailable"):
        ProviderDecision(
            mode=ProviderSelectionMode.UNAVAILABLE,
            selected_provider="serper",
            selected_source=DataSource.SERPER,
        )
