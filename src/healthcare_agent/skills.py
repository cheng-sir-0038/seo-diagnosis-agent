from __future__ import annotations

from enum import StrEnum

from src.agent.config import (
    AgentConfig,
    AgentPromptRenderer,
    RenderedAgentPrompt,
    Skill,
    SkillRegistry,
)
from src.agent.models import AgentRun
from src.agent.tools.registry import ToolRegistry
from src.healthcare_agent.exams import MedicalExamRegistration, MedicalExamSyllabus
from src.healthcare_agent.medicare import MedicareDrugCatalogue
from src.healthcare_agent.postgraduate import PostgraduateAdmissionsSearch, render_admissions_answer
from src.healthcare_agent.pubmed import PubMedLiteratureSearch


class HealthcareSkill(StrEnum):
    ADMISSIONS = "graduate-admissions"
    REGISTRATION = "medical-exam-registration"
    SYLLABUS = "medical-exam-syllabus"
    MEDICARE = "medicare-catalogue"
    LITERATURE = "pubmed-literature"


_DOMAIN = "healthcare"
_BASE = AgentConfig(
    id="healthcare-specialist", domain=_DOMAIN, name="医药专员",
    description="核对公开考试、医保和医学文献资料",
    instructions=(
        "你负责从用户问题提取工具参数。参数只采用已提供或确认的信息。"
        "缺少必需信息时留空。收到 clarification_received 时，根据 confirmed 与用户补充重新填参。"
        "只在资料检索范围内处理，不做个体的诊断、用药、医保待遇或录取结果判断。"
        "\n\n{skills}"
    ),
    auto_resolve_skills=False,
)

_SKILLS = SkillRegistry([
    Skill(
        name=HealthcareSkill.ADMISSIONS, domain=_DOMAIN, description="考研招生核对",
        tools=[PostgraduateAdmissionsSearch.name],
        prompt_fragment=(
            "保留用户原始检索问题、院校、招生年份和专业。比较两校时一次查询两校。"
            "明确给出的六位专业代码按院校填写 school_program_codes。"
            "录取率、报录比或分数线须检索相应资料，不能改查相近的科目或从招生人数估算。"
            "留空未确认的筛选项，由工具决定追问。"
        ),
    ),
    Skill(
        name=HealthcareSkill.REGISTRATION, domain=_DOMAIN, description="医考报名核对",
        tools=[MedicalExamRegistration.name],
        prompt_fragment=(
            "按公告年份、考区、考试类别核对报名。技能免考使用 skills_retake。"
            "技能考试时间、成绩与国家实践技能考试基地条件都从用户实际提供的信息取值。"
        ),
    ),
    Skill(
        name=HealthcareSkill.SYLLABUS, domain=_DOMAIN, description="医考考纲核对",
        tools=[MedicalExamSyllabus.name],
        prompt_fragment="根据已确认年份和考试类别查询修订公告。只使用公告列明的适用范围。",
    ),
    Skill(
        name=HealthcareSkill.MEDICARE, domain=_DOMAIN, description="医保药品目录",
        tools=[MedicareDrugCatalogue.name],
        prompt_fragment=(
            "只传药品通用名。版本字段表示文件版次。用户问现在的目录时省略版本，"
            "按生效日期选版。目录类目和剂型以国家医保局原文为准。"
        ),
    ),
    Skill(
        name=HealthcareSkill.LITERATURE, domain=_DOMAIN, description="医学文献检索",
        tools=[PubMedLiteratureSearch.name],
        prompt_fragment=(
            "提取公开研究主题，可翻译成英文关键词，按用户要求设置年份。"
            "个人病历、身份、联系方式与其他隐私资料不进入外部检索词。"
            "返回文献题录与 PMID，不推导治疗建议或疗效结论。"
        ),
    ),
])
_RENDERER = AgentPromptRenderer(_SKILLS)


def available_healthcare_skills(registry: ToolRegistry) -> list[Skill]:
    names = set(registry.names())
    skills = [_SKILLS.get(key) for key in HealthcareSkill]
    return [skill for skill in skills if names.issuperset(skill.tools)]


def render_healthcare_skill(
    key: HealthcareSkill, registry: ToolRegistry,
) -> tuple[Skill, ToolRegistry, RenderedAgentPrompt]:
    skill = _SKILLS.get(key)
    tools = ToolRegistry(registry.get(name) for name in skill.tools)
    config = _BASE.model_copy(update={"skills": [key], "tools": tools.names()})
    return skill, tools, _RENDERER.render(config, context={}, task="")


def skill_for_tool(name: str) -> HealthcareSkill | None:
    return next((key for key in HealthcareSkill if name in _SKILLS.get(key).tools), None)


def verified_healthcare_answer(run: AgentRun, tool_name: str) -> str | None:
    for event in reversed(run.events):
        if event.type != "tool.completed" or event.payload.get("name") != tool_name:
            continue
        result = event.payload.get("result") or {}
        data = result.get("data") or {}
        incomplete = data.get("status") in {"needs_input", "clarification_received"}
        if not result.get("success") or incomplete:
            return None
        if receipt := data.get("receipt"):
            return render_admissions_answer(receipt) if receipt.get("run_id") == run.id else None
        if answer := data.get("answer"):
            return answer
        description = (data.get("description") or {}).get("fallback")
        source = next((item for item in data.get("items", []) if item.get("url")), None)
        if description and source:
            return f"{description}\n\n[{source['title']}]({source['url']})"
        return None
    return None
