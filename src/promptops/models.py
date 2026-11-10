from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class VersionStage(StrEnum):
    DRAFT = "DRAFT"
    CHALLENGER = "CHALLENGER"
    CANARY = "CANARY"
    CURRENT = "CURRENT"
    RETIRED = "RETIRED"


class EvaluationStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CandidatePrompt(BaseModel):
    system_template: str
    user_template: str
    output_schema: dict[str, Any] | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    change_summary: str


class EvaluationScore(BaseModel):
    format_validity: float
    completeness: float
    exact_match: float
    forbidden_term_safety: float
    total_score: float
    passed: bool
    details: dict[str, Any] = Field(default_factory=dict)
