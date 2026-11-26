"""Public views of the workspace's built-in specialist capabilities."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from src.agent.config import Skill
from src.agent.tools.registry import ToolRegistry
from src.agent.workspace.routing import Specialist
from src.api.routes.workspace.business_skill_usage import BUSINESS_SKILL_USAGE
from src.api.routes.workspace.seo_skill_usage import SEO_SKILL_USAGE
from src.education_agent.skills import EDUCATION_SKILLS, EducationSkill
from src.followup_agent.workflow import FOLLOWUP_SKILL
from src.healthcare_agent.skills import _SKILLS as HEALTHCARE_SKILLS
from src.healthcare_agent.skills import HEALTHCARE_COMMON_INSTRUCTIONS, HealthcareSkill
from src.procurement_agent.models import INTAKE_PROMPT as PROCUREMENT_INTAKE_PROMPT
from src.procurement_agent.tools import PROCUREMENT_SKILL
from src.seo_agent.skills import SEO_SKILLS, seo_skills_for_tools
from src.seo_agent.workflows.course_review import COURSE_EDITOR_SYSTEM_PROMPT
from src.travel_agent.intake import TRAVEL_INTAKE_PROMPT
from src.travel_agent.tools import TRAVEL_SKILL


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


class DirectorySkill(BaseModel):
    name: str
    label: str
    summary: str
    tools: list[str]


class DirectoryPrompt(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    text: str


class SpecialistTools(BaseModel):
    agent: Specialist
    tools: list[DirectoryTool]
    skills: list[DirectorySkill]
    default_prompts: list[DirectoryPrompt]
    deterministic_path: str | None = None
    note: str | None = None


class ToolDirectory(BaseModel):
    specialists: list[SpecialistTools]


@dataclass(frozen=True)
class _PublicSkill:
    name: str
    label: str
    summary: str
    tools: tuple[str, ...]
    default_fragment: str


def _publish_skill(skill: Skill, summary: str) -> _PublicSkill:
    # Snapshot only the fields approved for publication from the authored skill.
    return _PublicSkill(
        skill.name, skill.description, summary, tuple(skill.tools), skill.prompt_fragment,
    )


@dataclass(frozen=True)
class _DirectoryProfile:
    skills: tuple[_PublicSkill, ...] = ()
    skill_prompt_role: str = "分派指令"
    prompts: tuple[DirectoryPrompt, ...] = ()
    deterministic_path: str | None = None


# Each listed skill opts in its built-in prompt fragment. Use only authored
# source text here. Never look up or render effective prompts or run context.
_PUBLIC_DEFAULTS = {
    Specialist.HEALTHCARE: _DirectoryProfile(
        skills=(
            _publish_skill(
                HEALTHCARE_SKILLS.get(HealthcareSkill.ADMISSIONS),
                "按院校、年份和专业核对公开招生资料，支持两校对比。",
            ),
            _publish_skill(
                HEALTHCARE_SKILLS.get(HealthcareSkill.REGISTRATION),
                "按年份、考区和考试类别核对报名及技能免考条件。",
            ),
            _publish_skill(
                HEALTHCARE_SKILLS.get(HealthcareSkill.SYLLABUS),
                "核对考纲修订年份、适用考试和公告原文。",
            ),
            _publish_skill(
                HEALTHCARE_SKILLS.get(HealthcareSkill.MEDICARE),
                "用药品通用名核对国家医保目录中的分类及剂型。",
            ),
            _publish_skill(
                HEALTHCARE_SKILLS.get(HealthcareSkill.LITERATURE),
                "按公开研究主题和年份检索文献题录及 PMID。",
            ),
        ),
        skill_prompt_role="技能指令",
        prompts=(DirectoryPrompt(
            id="healthcare-instructions", label="医药共用指令",
            text=HEALTHCARE_COMMON_INSTRUCTIONS,
        ),),
    ),
    Specialist.SEO: _DirectoryProfile(skill_prompt_role="技能指令", prompts=(
        # The runtime builders combine instructions with site/page context.
        # Publish literal excerpts so the builders stay outside this boundary.
        DirectoryPrompt(
            id="seo-diagnosis", label="SEO / GEO 诊断（静态节选）",
            text=(
                "你是 SEO/GEO 专员。target_url 是本轮要检查的确切页面；延续分析时沿用完整路径。\n"
                "没有域名时只处理已提供的研究主题，具体页面的信息必须先取得明确的网址。\n\n"
                "工作规则：\n"
                "1. 根据用户意图自行判断是否需要查询数据、调用哪些工具以及调用顺序。\n"
                "2. 不要预设固定调用顺序；每次拿到工具结果后，重新判断证据是否足够、是否冲突、是否需要补查。\n"
                "3. 只使用工具返回的真实数据形成站点结论，不得编造指标、排名、竞品或审计问题。\n"
                "4. 工具参数中的国家默认使用站点国家；除非用户明确要求，不要擅自改变目标市场。\n"
                "5. 结论必须区分：已观察事实、基于证据的推断、数据不足。\n"
                "6. 已有足够证据时停止调用工具并生成简洁结论；没有必要时不要为了展示而调用工具。\n"
                "7. 调查不执行发布、删除或站点写入；分享图仅保存为应用预览。"
            ),
        ),
        DirectoryPrompt(
            id="seo-course-editor", label="课程页与考纲核对",
            text=COURSE_EDITOR_SYSTEM_PROMPT,
        ),
    )),
    Specialist.LEARNING: _DirectoryProfile(
        skills=(
            _publish_skill(
                EDUCATION_SKILLS.get(EducationSkill.MATH_PRACTICE),
                "逐题练习一元一次方程及应用题，可按难度出题、判分和重练错题。",
            ),
            _publish_skill(
                EDUCATION_SKILLS.get(EducationSkill.RESOURCES),
                "按主题查找官方数学课程或练习链接，并记录已完成的练习。",
            ),
        ),
        deterministic_path="出题、判分和资料检索由工具按固定规则执行，无专员提示词。",
    ),
    Specialist.EXAM_PREP: _DirectoryProfile(
        skills=(
            _publish_skill(
                EDUCATION_SKILLS.get(EducationSkill.TEACHER_EXAM),
                "按省份、考期、学段和学科核对报名、笔试科目与考纲。",
            ),
        ),
        deterministic_path="公告匹配和备考清单由工具按固定规则生成，无专员提示词。",
    ),
    Specialist.FOLLOWUP: _DirectoryProfile(
        skills=(
            _publish_skill(
                FOLLOWUP_SKILL,
                "整理本次提交的报告，核对可比数值，生成复诊资料包和已确认的待办。",
            ),
        ),
        deterministic_path="报告对照、资料整理和确认保存按固定规则执行，无专员提示词。",
    ),
    Specialist.TRAVEL: _DirectoryProfile(
        skills=(
            _publish_skill(
                TRAVEL_SKILL,
                "为国内单目的地出差核对往返交通、住宿、接驳、天气和预算。",
            ),
        ),
        prompts=(DirectoryPrompt(
            id="travel-intake", label="商旅条件提取", text=TRAVEL_INTAKE_PROMPT,
        ),),
        deterministic_path="条件确认后的报价核对、行程比较和方案保存按固定规则执行，无额外提示词。",
    ),
    Specialist.PROCUREMENT: _DirectoryProfile(
        skills=(
            _publish_skill(
                PROCUREMENT_SKILL,
                "按规格搜索商品，比较报价，整理采购清单并核对实到数量和支出。",
            ),
        ),
        prompts=(DirectoryPrompt(
            id="procurement-intake", label="采购条件提取", text=PROCUREMENT_INTAKE_PROMPT,
        ),),
        deterministic_path="条件确认后的报价计算、清单汇总与到货核对按固定规则执行，无额外提示词。",
    ),
}


def _public_skills(agent: Specialist, tool_names: list[str]) -> tuple[_PublicSkill, ...]:
    if agent is Specialist.SEO:
        return tuple(
            _publish_skill(skill, skill.metadata["public_summary"])
            for skill in seo_skills_for_tools(tool_names)
        )
    return _PUBLIC_DEFAULTS[agent].skills


def _skill_usage(agent: Specialist, skill: _PublicSkill, tool_name: str) -> DirectorySkillUsage:
    if agent is Specialist.SEO:
        scenario, inputs, output = SEO_SKILL_USAGE[tool_name]
        rules = skill.default_fragment
    else:
        scenario, inputs, rules, output = BUSINESS_SKILL_USAGE[tool_name]
    return DirectorySkillUsage(scenario=scenario, inputs=inputs, rules=rules, output=output)


def bundled_skill_catalog() -> list[dict]:
    """Export approved static text for tool rows from older directory responses."""
    return [
        {
            "agent": agent.value, "name": skill.name, "label": skill.label, "tools": list(skill.tools),
            "summary": skill.summary, "instruction": skill.default_fragment,
            "usage": {name: _skill_usage(agent, skill, name).model_dump() for name in skill.tools},
        }
        for agent in _PUBLIC_DEFAULTS
        for skill in _public_skills(agent, list(SEO_SKILLS) if agent is Specialist.SEO else [])
    ]


def describe_specialist(
    agent: Specialist, registry: ToolRegistry, note: str | None,
) -> SpecialistTools:
    """Project registered skills and tools onto the public directory schema."""
    profile = _PUBLIC_DEFAULTS[agent]
    registered_names = registry.names()
    tool_names = set(registered_names)
    skills = []
    usage_by_tool = {}
    prompts = list(profile.prompts)
    for skill in _public_skills(agent, registered_names):
        related_tools = [name for name in skill.tools if name in tool_names]
        if not related_tools:
            continue
        for name in related_tools:
            usage_by_tool[name] = _skill_usage(agent, skill, name)
        skills.append(DirectorySkill(
            name=skill.name, label=skill.label, summary=skill.summary, tools=related_tools,
        ))
        prompts.append(DirectoryPrompt(
            id=skill.name, label=f"{skill.label}（{profile.skill_prompt_role}）",
            text=skill.default_fragment,
        ))

    return SpecialistTools(
        agent=agent, note=note, skills=skills, default_prompts=prompts,
        deterministic_path=profile.deterministic_path,
        tools=[
            DirectoryTool(
                name=tool.name, description=tool.description,
                availability_note=getattr(tool, "availability_note", None),
                skill_usage=usage_by_tool.get(tool.name),
            )
            for tool in registry.values()
        ],
    )


if __name__ == "__main__":
    import json

    print(json.dumps(bundled_skill_catalog(), ensure_ascii=False, indent=2))
