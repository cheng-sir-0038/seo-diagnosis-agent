"""Public projection of deployed specialist tools and opt-in static instructions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.agent.tools.registry import ToolRegistry
from src.agent.workspace.bootstrap import workspace_specialists
from src.agent.workspace.registry import SpecialistDefinition, SpecialistProfile


class DirectorySkillUsage(BaseModel):
    scenario: str
    inputs: str
    rules: str
    output: str


class DirectoryTool(BaseModel):
    name: str
    description: str
    guidance: str | None = None
    availability_note: str | None = None
    skill_usage: DirectorySkillUsage | None = None
    public_prompt_ids: list[str]


class DirectorySkill(BaseModel):
    name: str
    label: str
    summary: str
    tools: list[str]
    usage: dict[str, DirectorySkillUsage]


class DirectoryPrompt(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    text: str


class SpecialistTools(BaseModel):
    agent: str
    tools: list[DirectoryTool]
    skills: list[DirectorySkill]
    default_prompts: list[DirectoryPrompt]
    deterministic_path: str | None = None
    note: str | None = None
    profile: SpecialistProfile
    skill_catalog_version: Literal[1] = 1


class ToolDirectory(BaseModel):
    specialists: list[SpecialistTools]


def _describe_usage(values: tuple[str, str, str, str]) -> DirectorySkillUsage:
    scenario, inputs, rules, output = values
    return DirectorySkillUsage(scenario=scenario, inputs=inputs, rules=rules, output=output)


def bundled_skill_catalog() -> dict[str, list[dict]]:
    """Export the approved, tool-scoped material for unversioned directories."""
    return {
        "skills": [
            {
                "agent": definition.id, "name": skill.name, "label": skill.label,
                "tools": list(skill.tools), "summary": skill.summary, "instruction": skill.instruction,
                "usage": {tool: _describe_usage(usage).model_dump() for tool, usage in skill.usage.items()},
            }
            for definition in workspace_specialists
            for skill in definition.public_skills
        ],
        "instructions": [
            {
                "agent": definition.id, "id": prompt.id, "label": prompt.label,
                "text": prompt.text, "tools": list(prompt.tools) if prompt.tools is not None else None,
            }
            for definition in workspace_specialists
            for prompt in definition.public_instructions if prompt.attach_to_tools
        ],
    }


def describe_registered(
    definition: SpecialistDefinition, registry: ToolRegistry, note: str | None,
) -> SpecialistTools:
    """Project live tools and explicitly published static material for a registered specialist."""
    tools = registry.names()
    order = {name: index for index, name in enumerate(tools)}
    skills = []
    usage_by_tool = {}
    prompts = [DirectoryPrompt(id=p.id, label=p.label, text=p.text) for p in definition.public_instructions]
    bindings = [(skill, [name for name in skill.tools if name in order]) for skill in definition.public_skills]
    for skill, related in sorted(bindings, key=lambda item: min(
        (order[name] for name in item[1]), default=len(order),
    )):
        if not related:
            continue
        usage = {name: _describe_usage(skill.usage[name]) for name in related if name in skill.usage}
        skills.append(DirectorySkill(
            name=skill.name, label=skill.label, summary=skill.summary, tools=related, usage=usage,
        ))
        prompts.append(DirectoryPrompt(id=skill.name, label=f"{skill.label}（技能指令）", text=skill.instruction))
        for name, guide in usage.items():
            usage_by_tool.setdefault(name, guide)
    return SpecialistTools(
        agent=definition.id, profile=definition.profile, skill_catalog_version=1,
        note=note, deterministic_path=definition.deterministic_path,
        skills=skills, default_prompts=prompts,
        tools=[DirectoryTool(
            name=tool.name, description=tool.description,
            availability_note=getattr(tool, "availability_note", None),
            skill_usage=usage_by_tool.get(tool.name),
            public_prompt_ids=[
                prompt.id for prompt in definition.public_instructions
                if prompt.attach_to_tools and (prompt.tools is None or tool.name in prompt.tools)
            ],
        ) for tool in registry.values()],
    )


if __name__ == "__main__":
    import json

    print(json.dumps(bundled_skill_catalog(), ensure_ascii=False, indent=2))
