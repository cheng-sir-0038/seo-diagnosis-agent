"""基于结构化查询意图的确定性 Provider Router。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.models.common import Capability, DataSource
from src.providers.base import BaseProvider
from src.routing.contracts import (
    ProviderDecision,
    ProviderFeature,
    ProviderProfile,
    ProviderRuntimeMode,
    ProviderSelectionMode,
    QueryIntent,
    QueryPurpose,
)


@dataclass(frozen=True, slots=True)
class ProviderRoute:
    """一次可执行的 Provider 路由。"""

    primary: BaseProvider
    fallbacks: tuple[BaseProvider, ...]
    decision: ProviderDecision


class ProviderRoutingError(ValueError):
    """没有 Provider 能完整满足结构化查询要求。"""

    def __init__(self, message: str, decision: ProviderDecision) -> None:
        super().__init__(message)
        self.decision = decision


@dataclass(frozen=True, slots=True)
class _Candidate:
    provider: BaseProvider
    profile: ProviderProfile
    score: int
    matched_features: frozenset[ProviderFeature]


_PURPOSE_SOURCE_SCORES: dict[QueryPurpose, dict[DataSource, int]] = {
    QueryPurpose.QUICK_LOOKUP: {
        DataSource.SERPER: 100,
        DataSource.DATAFORSEO: 60,
    },
    QueryPurpose.SEO_ANALYSIS: {
        DataSource.DATAFORSEO: 110,
        DataSource.SERPER: 50,
    },
    QueryPurpose.RANK_MONITORING: {
        DataSource.DATAFORSEO: 120,
        DataSource.SERPER: 20,
    },
    QueryPurpose.LOCAL_ANALYSIS: {
        DataSource.DATAFORSEO: 100,
        DataSource.SERPER: 40,
    },
    QueryPurpose.RESEARCH: {
        DataSource.DATAFORSEO: 80,
        DataSource.SERPER: 60,
    },
}


class ProviderRouter:
    """根据当前已实现能力选择 Provider，不依赖注册顺序。"""

    def resolve(
        self,
        intent: QueryIntent,
        providers: Iterable[BaseProvider],
        *,
        live_only: bool,
    ) -> ProviderRoute:
        candidates: list[_Candidate] = []
        rejected_missing: list[frozenset[ProviderFeature]] = []
        provider_names: set[str] = set()

        for provider in providers:
            profile = provider.routing_profile
            provider_names.add(profile.provider_name)
            if not self._runtime_allowed(profile, live_only=live_only):
                continue
            if not profile.supports(intent.capability):
                continue

            missing = self._missing_features(profile, intent)
            limit = profile.max_results_for(intent.capability)
            if limit is not None and intent.limit > limit:
                missing = missing | {ProviderFeature.DEEP_RESULTS}
            if missing:
                rejected_missing.append(frozenset(missing))
                continue

            candidates.append(
                _Candidate(
                    provider=provider,
                    profile=profile,
                    score=self._score(profile, intent),
                    matched_features=self._effective_required_features(intent),
                )
            )

        preferred = intent.preferred_provider
        if preferred:
            preferred_candidate = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.profile.provider_name == preferred
                ),
                None,
            )
            if preferred_candidate is not None:
                return self._route_from_candidate(
                    preferred_candidate,
                    candidates,
                    intent,
                    mode=ProviderSelectionMode.PREFERRED,
                    reasons=(f"用户指定 Provider: {preferred}",),
                )
            if not intent.allow_fallback:
                missing = self._combined_missing(rejected_missing)
                decision = ProviderDecision(
                    mode=ProviderSelectionMode.UNAVAILABLE,
                    reasons=(f"指定 Provider {preferred} 无法完整满足本次查询",),
                    missing_features=missing,
                )
                raise ProviderRoutingError(
                    f"指定 Provider {preferred} 无法完整满足本次查询",
                    decision,
                )

        if not candidates:
            missing = self._combined_missing(rejected_missing)
            requested = ", ".join(sorted(item.value for item in missing)) or "运行中 Provider"
            decision = ProviderDecision(
                mode=ProviderSelectionMode.UNAVAILABLE,
                reasons=(f"没有 Provider 能完整满足要求: {requested}",),
                missing_features=missing,
            )
            raise ProviderRoutingError(
                f"没有 Provider 能完整满足本次查询要求: {requested}",
                decision,
            )

        ordered = sorted(
            candidates,
            key=lambda candidate: (-candidate.score, candidate.profile.provider_name),
        )
        primary = ordered[0]
        reasons = list(self._selection_reasons(primary.profile, intent))
        if preferred and preferred not in provider_names:
            reasons.insert(0, f"指定 Provider {preferred} 当前未注册，已选择完整候选")
        elif preferred:
            reasons.insert(0, f"指定 Provider {preferred} 当前不可用，已选择完整候选")
        return self._route_from_candidate(
            primary,
            ordered,
            intent,
            mode=ProviderSelectionMode.AUTOMATIC,
            reasons=tuple(reasons),
        )

    def _route_from_candidate(
        self,
        selected: _Candidate,
        candidates: list[_Candidate],
        intent: QueryIntent,
        *,
        mode: ProviderSelectionMode,
        reasons: tuple[str, ...],
    ) -> ProviderRoute:
        ordered = sorted(
            candidates,
            key=lambda candidate: (-candidate.score, candidate.profile.provider_name),
        )
        fallback_candidates = tuple(
            candidate
            for candidate in ordered
            if candidate.profile.provider_name != selected.profile.provider_name
        )
        if not intent.allow_fallback:
            fallback_candidates = ()
        decision = ProviderDecision(
            mode=mode,
            selected_provider=selected.profile.provider_name,
            selected_source=selected.profile.source,
            reasons=reasons or self._selection_reasons(selected.profile, intent),
            matched_features=selected.matched_features,
            fallback_candidates=tuple(
                item.profile.provider_name for item in fallback_candidates
            ),
        )
        return ProviderRoute(
            primary=selected.provider,
            fallbacks=tuple(item.provider for item in fallback_candidates),
            decision=decision,
        )

    @staticmethod
    def _runtime_allowed(profile: ProviderProfile, *, live_only: bool) -> bool:
        if live_only:
            return profile.runtime_mode == ProviderRuntimeMode.LIVE
        return profile.runtime_mode != ProviderRuntimeMode.UNAVAILABLE

    def _missing_features(
        self, profile: ProviderProfile, intent: QueryIntent
    ) -> frozenset[ProviderFeature]:
        required = set(self._effective_required_features(intent))
        if (
            ProviderFeature.LANGUAGE_TARGETING in required
            and profile.fixed_language is not None
            and self._language_matches(profile.fixed_language, intent.language)
        ):
            required.remove(ProviderFeature.LANGUAGE_TARGETING)
        return profile.missing_features(required)

    @staticmethod
    def _effective_required_features(intent: QueryIntent) -> frozenset[ProviderFeature]:
        required = set(intent.required_features)
        if intent.capability != Capability.SERP_OVERVIEW:
            required.discard(ProviderFeature.COUNTRY_TARGETING)
            required.discard(ProviderFeature.LANGUAGE_TARGETING)
            return frozenset(required)
        if intent.limit > 20:
            required.add(ProviderFeature.DEEP_RESULTS)
        if intent.page > 1:
            required.add(ProviderFeature.PAGINATION)
        return frozenset(required)

    @staticmethod
    def _language_matches(fixed_language: str, requested_language: str) -> bool:
        fixed = fixed_language.lower().replace("_", "-").split("-", 1)[0]
        requested = requested_language.lower().replace("_", "-").split("-", 1)[0]
        return fixed == requested

    @staticmethod
    def _score(profile: ProviderProfile, intent: QueryIntent) -> int:
        purpose_score = _PURPOSE_SOURCE_SCORES.get(intent.purpose, {}).get(
            profile.source, 40
        )
        runtime_score = 20 if profile.runtime_mode == ProviderRuntimeMode.LIVE else 0
        return purpose_score + runtime_score

    @staticmethod
    def _selection_reasons(
        profile: ProviderProfile, intent: QueryIntent
    ) -> tuple[str, ...]:
        if intent.purpose == QueryPurpose.QUICK_LOOKUP:
            purpose = "快速查询优先轻量实时数据源"
        elif intent.purpose == QueryPurpose.RANK_MONITORING:
            purpose = "排名监控优先深度和稳定查询能力"
        elif intent.purpose == QueryPurpose.SEO_ANALYSIS:
            purpose = "SEO 分析优先深度查询能力"
        elif intent.purpose == QueryPurpose.LOCAL_ANALYSIS:
            purpose = "本地分析优先位置能力"
        else:
            purpose = "研究查询按能力完整度选择"
        return (purpose, f"{profile.provider_name} 完整满足当前已实现要求")

    @staticmethod
    def _combined_missing(
        values: list[frozenset[ProviderFeature]],
    ) -> frozenset[ProviderFeature]:
        if not values:
            return frozenset()
        common = set(values[0])
        for value in values[1:]:
            common.intersection_update(value)
        if common:
            return frozenset(common)
        union: set[ProviderFeature] = set()
        for value in values:
            union.update(value)
        return frozenset(union)
