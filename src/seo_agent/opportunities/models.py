from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class OpportunityScoreFactors(BaseModel):
    search_demand: float = Field(ge=0, le=100)
    business_intent: float = Field(ge=0, le=100)
    content_gap: float = Field(ge=0, le=100)
    difficulty_fit: float = Field(ge=0, le=100)
    site_fit: float = Field(ge=0, le=100)
    evidence_confidence: float = Field(ge=0, le=100)


class OpportunityCandidate(BaseModel):
    normalized_intent: str = Field(min_length=1, max_length=500)
    opportunity_type: str = Field(min_length=1, max_length=80)
    target_country: str = Field(min_length=2, max_length=16)
    target_language: str = Field(min_length=2, max_length=16)
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    competitor_pages: list[str] = Field(default_factory=list)
    evidence_snapshot_ids: list[str] = Field(default_factory=list)
    score_factors: OpportunityScoreFactors
    search_volume: float | None = Field(default=None, ge=0)
    keyword_difficulty: float | None = Field(default=None, ge=0, le=100)
    competitor_count: int | None = Field(default=None, ge=0)
    major_change_reasons: list[str] = Field(default_factory=list)

    @field_validator("normalized_intent")
    @classmethod
    def normalize_intent(cls, value: str) -> str:
        return " ".join(value.lower().split())

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(" ".join(value.lower().split()) for value in values if value.strip())
        )


class OpportunityScore(BaseModel):
    total: int = Field(ge=0, le=100)
    priority: str
    policy_version: str
    factors: OpportunityScoreFactors
