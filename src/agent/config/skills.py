from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.agent.config.models import Skill


class SkillRegistry:
    """Deterministic registry for LLM knowledge fragments.

    SEO example::

        registry = SkillRegistry([seo_diagnosis_skill])
        registry.resolve("seo", "Inspect the homepage TDK")
    """

    def __init__(self, skills: Iterable[Skill] | None = None) -> None:
        self._skills: dict[str, Skill] = {}
        for skill in skills or []:
            self.register(skill)

    def register(self, skill: Skill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"Skill 已注册: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"Skill 不存在: {name}") from exc

    def resolve(self, domain: str, task: str) -> list[Skill]:
        normalized_domain = domain.strip().casefold()
        normalized_task = task.strip().casefold()
        return [
            skill
            for skill in self._skills.values()
            if _domain_matches(skill.domain, normalized_domain)
            and _task_matches(skill, normalized_task)
        ]

    def names(self) -> list[str]:
        return list(self._skills)


def _domain_matches(skill_domain: str, requested_domain: str) -> bool:
    return skill_domain == "*" or skill_domain.casefold() == requested_domain


def _task_matches(skill: Skill, normalized_task: str) -> bool:
    metadata: dict[str, Any] = skill.metadata
    if bool(metadata.get("always_load")):
        return True
    terms: list[str] = [skill.name, skill.description]
    configured = metadata.get("match_terms")
    if isinstance(configured, list):
        terms.extend(str(item) for item in configured)
    return any(term.strip() and term.strip().casefold() in normalized_task for term in terms)
