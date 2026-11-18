from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ShareImageVisual(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    subject: str = Field(min_length=1, max_length=150)
    scene: str = Field(min_length=1, max_length=200)
    tone: str = Field(min_length=1, max_length=100)
    composition: str = Field(min_length=1, max_length=160)
    avoid: str = Field(min_length=1, max_length=150)
    evidence_quote: str = Field(min_length=1, max_length=160)


class ShareImageBrief(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    visual: ShareImageVisual | None
