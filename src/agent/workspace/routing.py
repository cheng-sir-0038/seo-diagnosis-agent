from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from src.core.exceptions import InternalAdapterError
from src.education_agent.practice import MathPracticeQuery
from src.education_agent.resources import ResourceQuery
from src.education_agent.skills import (
    EDUCATION_SKILLS,
    EXAM_PREP_DOMAIN,
    LEARNING_DOMAIN,
    EducationSkill,
)
from src.education_agent.teacher_exam import TeacherExamQuery
from src.followup_agent.workflow import DOMAIN as FOLLOWUP_DOMAIN
from src.followup_agent.workflow import FOLLOWUP_SKILL
from src.healthcare_agent.skills import HealthcareSkill
from src.procurement_agent.models import DOMAIN as PROCUREMENT_DOMAIN
from src.procurement_agent.models import INTAKE_PROMPT as PROCUREMENT_INTAKE_PROMPT
from src.procurement_agent.models import ProcurementIntake
from src.procurement_agent.tools import PROCUREMENT_SKILL
from src.providers.page_metadata.client import normalize_site_url
from src.travel_agent.intake import TRAVEL_DOMAIN, TRAVEL_INTAKE_PROMPT, TravelIntake
from src.travel_agent.tools import TRAVEL_SKILL


class Specialist(StrEnum):
    SEO = "seo"
    HEALTHCARE = "healthcare"
    LEARNING = LEARNING_DOMAIN
    EXAM_PREP = EXAM_PREP_DOMAIN
    FOLLOWUP = FOLLOWUP_DOMAIN
    TRAVEL = TRAVEL_DOMAIN
    PROCUREMENT = PROCUREMENT_DOMAIN


class Route(StrEnum):
    SEO = Specialist.SEO
    HEALTHCARE = Specialist.HEALTHCARE
    LEARNING = Specialist.LEARNING
    EXAM_PREP = Specialist.EXAM_PREP
    FOLLOWUP = Specialist.FOLLOWUP
    TRAVEL = Specialist.TRAVEL
    PROCUREMENT = Specialist.PROCUREMENT
    COURSE_REVIEW = "course_review"
    CLARIFY = "clarify"


ROUTE_PROMPT_KEY = "workspace.route.v6"
SKILL_LOADED_EVENT = "skill.loaded"
SUPERVISOR_NODE = "supervisor"
ASK_PAGE = "请发要检查的公开网页链接或域名。"


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: Route
    needs_page: bool = Field(description="需要指定页面或站点才可执行时为 true")
    healthcare_skill: HealthcareSkill | None = Field(
        default=None, description="本次医药任务使用的 Skill。需要门店私有数据时留空",
    )
    learning_skill: Literal[EducationSkill.MATH_PRACTICE, EducationSkill.RESOURCES] | None = None
    math_practice: MathPracticeQuery | None = None
    resources: ResourceQuery | None = None
    teacher_exam: TeacherExamQuery | None = None
    travel: TravelIntake | None = None
    procurement: ProcurementIntake | None = None
    target_index: int | None = Field(default=None, ge=1, description="候选页面从 1 开始的序号")
    question: str | None = Field(default=None, max_length=300, description="意图不清时的追问")


ROUTING_PROMPT = (
    "你是业务工作台的主 Agent。根据最近对话和当前问题选择实际执行的专员。"
    "seo 处理网站、搜索、关键词、内容草稿和 GEO。页面操作和按域名查排名需要页面；"
    "单独研究主题词、搜索结果或解释 SEO 概念不要求网址。"
    "healthcare 只读查询公开医药和考试资料。为本轮选择 healthcare_skill："
    "graduate-admissions 查考研招生科目、资格、报录比和录取率；"
    "medical-exam-registration 查医师报名和技能免考；medical-exam-syllabus 查医考考纲；"
    "medicare-catalogue 查国家医保药品目录和甲乙分类；pubmed-literature 查公开医学文献题录。"
    "门店会员、券、提醒名单、实时药价和库存需要额外数据，选 healthcare 但不指定 Skill。"
    "将个人诊疗、用药和个人医保报销金额请求标为 clarify，说明这里只查询公开资料。"
    "course_review 专用于拿医学人文考纲核对招生或课程页的公开说法，需要页面。"
    "用户提供了课程页、或刚问过医学人文修订又要求对照网页，使用 course_review；"
    "单纯问网页的 Title 仍由 seo 处理。明确的医考或门店问题不因含有参考链接而转 SEO。"
    "用户要求检查或更换具体网页时，target_index 必填，即使只有一个也选 1。"
    "它引用候选页序号。参考文献链接不选，沿用上轮页面时不选。"
    "多个链接且无法分清目标时不选。不从助手答案或作为依据的引用中推测网址。"
    "用户沿用上文的问题时结合前一轮业务与当前页面。问题里有足够信息就直接分派。"
    "院校与招生相关的后续问题可沿用 previous_healthcare_skill。"
    "\nlearning 负责数学陪练和推荐学习资料。考研仍走 healthcare。"
    "教师资格笔试走 exam_prep，条件不全也先保留已明确的字段。"
    "待补充 teacher_exam 时用 pending_teacher_exam 与用户刚补充的条件合并，"
    "该次查询尚未结束。后续追问同一考期时可沿用 previous_teacher_exam。"
    "没有明确的业务意图或超出这些业务就选 clarify，给一个自然、简短的追问。"
) + "\n" + "\n".join(
    EDUCATION_SKILLS.get(name).prompt_fragment for name in EDUCATION_SKILLS.names()
) + "\n" + FOLLOWUP_SKILL.prompt_fragment + "\n" + TRAVEL_SKILL.prompt_fragment + (
    "\n选择 travel 后填写 travel 字段，不需要网页。"
    "报价只能摘自最后一条用户消息。"
) + TRAVEL_INTAKE_PROMPT + "\n" + PROCUREMENT_SKILL.prompt_fragment + (
    "\n仅在选择 shopping_procurement 后填写 procurement，needs_page 设为 false。"
) + PROCUREMENT_INTAKE_PROMPT


def explicit_healthcare_skill(question: str) -> HealthcareSkill | None:
    """Keep an explicit business request in its evidence workflow."""
    if re.search(r"\bpubmed\b|医学文献|文献题录|pmid", question, re.I):
        return HealthcareSkill.LITERATURE
    if re.search(r"考研|研究生|硕士|初试|复试|报录比|录取率", question):
        return HealthcareSkill.ADMISSIONS
    if re.search(r"医保.{0,4}(?:目录|药品)|药品.{0,4}医保|甲类还是乙类", question):
        return HealthcareSkill.MEDICARE
    if re.search(r"考纲|考试大纲", question):
        return HealthcareSkill.SYLLABUS
    if re.search(r"医考|医师资格|技能.{0,4}(?:免考|重考)|(?:报名|考试).{0,4}资格", question):
        return HealthcareSkill.REGISTRATION
    return None

# Only user-authored URL tokens can introduce a target. The selected item is
# subsequently validated by the same public URL rules as the page provider.
_TOKEN = r"""[^\s<>"'，。；！？、（）【】《》「」\]\[()]+"""
_TARGET = re.compile(
    rf"(?<![A-Za-z0-9_@./-])(?:[a-z][a-z0-9+.-]*://{_TOKEN}|"
    rf"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:[a-z]{{2,63}}|[0-9]{{1,3}})"
    rf"(?::[0-9]+)?(?:[/?#](?:{_TOKEN})?)?)", re.I | re.ASCII,
)


def page_candidates(text: str) -> tuple[list[str], bool]:
    targets: list[str] = []
    invalid = False
    for match in _TARGET.finditer(text):
        value = match.group().rstrip(".,;:!?")
        if "://" not in value and value.lower().endswith(
            (".txt", ".xml", ".html", ".htm", ".json", ".js", ".css", ".pdf", ".md")
        ) and "/" not in value:
            continue
        try:
            target = normalize_site_url(value)
            host = urlsplit(target).hostname or ""
            if not host.isascii():
                host.encode("idna")
        except (InternalAdapterError, UnicodeError, ValueError):
            invalid = True
            continue
        if target not in targets:
            targets.append(target)
    return targets, invalid


def select_page(
    current: str | None, candidates: list[str], invalid: bool, decision: RouteDecision,
) -> tuple[str | None, str | None]:
    index = decision.target_index
    if index and index <= len(candidates):
        return candidates[index - 1], None
    if candidates and (index or len(candidates) > 1 or not current):
        return None, "请指出要检查的是哪个页面，或直接贴出目标网址。"
    target = None if invalid else current
    if not target and (decision.needs_page or decision.route == Route.COURSE_REVIEW):
        return None, ASK_PAGE
    return target, None
