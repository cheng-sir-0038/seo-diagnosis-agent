from __future__ import annotations

from dataclasses import dataclass

from src.seo_agent.opportunities.models import (
    OpportunityCandidate,
    OpportunityScore,
)


@dataclass(frozen=True)
class OpportunityPolicy:
    version: str = "v1"
    high_threshold: int = 80
    medium_threshold: int = 60
    reopen_score_increase: int = 15
    reopen_search_volume_growth_percent: float = 30.0
    rejection_cooldown_days: int = 30

    def score(self, candidate: OpportunityCandidate) -> OpportunityScore:
        factors = candidate.score_factors
        total = round(
            factors.search_demand * 0.20
            + factors.business_intent * 0.20
            + factors.content_gap * 0.20
            + factors.difficulty_fit * 0.15
            + factors.site_fit * 0.15
            + factors.evidence_confidence * 0.10
        )
        priority = (
            "HIGH"
            if total >= self.high_threshold
            else "MEDIUM"
            if total >= self.medium_threshold
            else "LOW"
        )
        return OpportunityScore(
            total=total,
            priority=priority,
            policy_version=self.version,
            factors=factors,
        )

    def is_major_change(
        self,
        *,
        previous_score: float | None,
        current_score: float,
        previous_search_volume: float | None,
        current_search_volume: float | None,
        explicit_reasons: list[str],
    ) -> tuple[bool, list[str]]:
        reasons = list(explicit_reasons)
        if (
            previous_score is not None
            and current_score - previous_score >= self.reopen_score_increase
        ):
            reasons.append(f"score_increase:{current_score - previous_score:.0f}")
        if (
            previous_search_volume not in (None, 0)
            and current_search_volume is not None
            and ((current_search_volume - previous_search_volume) / previous_search_volume) * 100
            >= self.reopen_search_volume_growth_percent
        ):
            growth = (
                (current_search_volume - previous_search_volume) / previous_search_volume
            ) * 100
            reasons.append(f"search_volume_growth:{growth:.1f}%")
        return bool(reasons), list(dict.fromkeys(reasons))
