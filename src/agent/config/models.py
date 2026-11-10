from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")
_MODEL_PATTERN = re.compile(r"^[a-z][a-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


class Skill(BaseModel):
    """Domain knowledge injected into an Agent system prompt.

    SEO example::

        Skill(
            name="seo-diagnosis",
            description="Use for evidence-based SEO diagnosis",
            domain="seo",
            prompt_fragment="Do not infer missing SEO data.",
            tools=["seo_page_metadata"],
            metadata={"match_terms": ["TDK", "diagnosis"]},
        )
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    prompt_fragment: str = Field(min_length=1)
    tools: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "domain")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if value != "*" and not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("必须是稳定注册键，只允许字母、数字、点、下划线和连字符")
        return value

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, value: list[str]) -> list[str]:
        return _validate_unique_identifiers(value, field_name="tools")


class AgentConfig(BaseModel):
    """Domain-neutral Agent definition.

    SEO example::

        AgentConfig(
            id="seo-specialist",
            name="SEO Specialist",
            description="Read-only SEO diagnosis Agent",
            instructions="Diagnose the site. {skills} {context}",
            tools=["seo_page_metadata"],
            skills=["seo-diagnosis"],
            goals=["Find evidence-backed issues"],
            constraints=["Never use pseudo data"],
            domain="seo",
        )
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    model: str = "deepseek/deepseek-v4-flash"
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    auto_resolve_skills: bool = True
    goals: list[str] = Field(default_factory=list)
    targets: dict[str, float] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    domain: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "domain")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError("必须是稳定注册键，只允许字母、数字、点、下划线和连字符")
        return value

    @field_validator("model")
    @classmethod
    def validate_model_reference(cls, value: str) -> str:
        if not _MODEL_PATTERN.fullmatch(value):
            raise ValueError("model 必须使用 provider/model 格式")
        return value

    @field_validator("tools", "skills")
    @classmethod
    def validate_registry_keys(cls, value: list[str], info: Any) -> list[str]:
        return _validate_unique_identifiers(value, field_name=info.field_name)

    @field_validator("goals", "constraints")
    @classmethod
    def validate_text_lists(cls, value: list[str], info: Any) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError(f"{info.field_name} 不能包含空字符串")
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"{info.field_name} 不能包含重复项")
        return normalized

    @field_validator("targets")
    @classmethod
    def validate_targets(cls, value: dict[str, float]) -> dict[str, float]:
        normalized: dict[str, float] = {}
        for key, target in value.items():
            clean_key = key.strip()
            if not clean_key:
                raise ValueError("targets 指标名不能为空")
            number = float(target)
            if not math.isfinite(number):
                raise ValueError(f"targets.{clean_key} 必须是有限数值")
            normalized[clean_key] = number
        return normalized

    @model_validator(mode="after")
    def validate_skill_and_tool_names(self) -> Self:
        # Field validators already enforce uniqueness. This validator is kept as
        # a stable extension point for future cross-field config checks.
        return self

    def render_system_prompt(
        self,
        context: dict[str, Any],
        *,
        resolved_skills: Sequence[Skill] | None = None,
    ) -> str:
        """Render a deterministic prompt without reading global registries."""
        from src.agent.config.renderer import render_system_prompt

        return render_system_prompt(
            self,
            context=context,
            resolved_skills=list(resolved_skills or []),
        )


class RenderedAgentPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    system_prompt: str
    resolved_skill_names: list[str]
    prompt_hash: str


def _validate_unique_identifiers(value: list[str], *, field_name: str) -> list[str]:
    normalized = [item.strip() for item in value]
    for item in normalized:
        if not _IDENTIFIER_PATTERN.fullmatch(item):
            raise ValueError(f"{field_name} 包含非法注册键: {item!r}")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} 不能包含重复注册键")
    return normalized
