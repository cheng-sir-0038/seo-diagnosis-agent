from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TaskPrdContent(BaseModel):
    background: str
    target_intent: str
    target_keywords: list[str] = Field(min_length=1)
    target_audience: str
    competitor_gap: str
    content_type: str
    content_outline: list[str] = Field(min_length=1)
    required_questions: list[str] = Field(default_factory=list)
    seo_title: str
    seo_description: str
    internal_links: list[str] = Field(default_factory=list)
    prohibited_items: list[str] = Field(default_factory=list)
    delivery_requirements: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)
    monitoring_goals: list[dict[str, Any]] = Field(default_factory=list)
