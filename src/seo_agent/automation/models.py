from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DeliveryReviewOutput(BaseModel):
    status: Literal["ACCEPTED", "REVISION_REQUIRED", "REJECTED"]
    score: int = Field(ge=0, le=100)
    passed_items: list[dict] = Field(default_factory=list)
    failed_items: list[dict] = Field(default_factory=list)
    missing_items: list[dict] = Field(default_factory=list)
    revision_instructions: list[str] = Field(default_factory=list)
