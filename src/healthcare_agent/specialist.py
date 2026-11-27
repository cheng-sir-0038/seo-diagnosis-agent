"""Workspace definition for read-only healthcare evidence queries."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from src.agent.providers.base import ToolCall
from src.agent.tools.registry import ToolRegistry
from src.agent.workspace.registry import (
    PublicInstruction,
    PublicSkill,
    RoutingTurn,
    SpecialistDefinition,
    SpecialistProfile,
    ToolContext,
)
from src.healthcare_agent import build_healthcare_registry
from src.healthcare_agent.exams import MedicalExamSyllabus, public_rule_evidence
from src.healthcare_agent.skills import (
    HealthcareSkill,
    _SKILLS,
    render_healthcare_skill,
    skill_for_tool,
    verified_healthcare_answer,
)
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind

if TYPE_CHECKING:
    from src.agent.workspace.execution import SpecialistExecution

__all__ = ["HEALTHCARE_SPECIALIST", "collect_public_syllabus"]


class HealthcareIntake(BaseModel):
    healthcare_skill: HealthcareSkill | None = Field(
        default=None, description="本次医药任务使用的 Skill。需要门店私有数据时留空",
    )


def _explicit_healthcare_skill(question: str) -> HealthcareSkill | None:
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


def prepare_healthcare_route(turn: RoutingTurn) -> None:
    """Persist the selected Skill, honoring a clarification that changes the task."""
    selected = getattr(turn.decision, "healthcare_skill", None)
    if not turn.answered or selected is None:
        selected = _explicit_healthcare_skill(turn.latest) or selected
    turn.state["healthcare_skill"] = selected


def _inherit_healthcare(previous: dict[str, Any]) -> dict[str, Any]:
    return {"previous_healthcare_skill": previous.get("healthcare_skill")}


def _healthcare_routing_context(state: dict[str, Any], _pending_route: str | None) -> dict[str, Any]:
    return {"previous_healthcare_skill": state.get("previous_healthcare_skill")}


async def build_healthcare_tools(context: ToolContext) -> ToolRegistry:
    """Expose the same read-only tools to the directory and to an invocation."""
    return build_healthcare_registry(database=context.resources.database)


async def execute_healthcare(execution: SpecialistExecution) -> None:
    """Run the selected healthcare Skill and answer from its verified receipt."""
    run, executor = execution.run, execution.executor
    saved = run.dispatch_state
    previous_tool = run.human_input.tool_name if run.human_input else None
    if previous_tool is None and run.pending_tool_calls:
        previous_tool = run.pending_tool_calls[0]["name"]
    skill_id = (
        saved.get("healthcare_skill")
        or (skill_for_tool(previous_tool) if previous_tool else None)
        or _explicit_healthcare_skill(saved.get("intent", ""))
    )
    async with observed_operation(
        execution.resources.observability, "workspace.healthcare", kind=SpanKind.AGENT,
        input_data={"route": saved["route"], "skill": skill_id},
    ) as span:
        registry = await execution.tools()
        try:
            skill, executor.tools, prompt = render_healthcare_skill(skill_id, registry)
        except KeyError:
            executor.tools = ToolRegistry()
            run.pending_tool_calls = []
            run.human_input = None
            text = (
                "当前无法读取对应的公开资料。可以换个问题，或稍后重新查询。"
                if skill_id else
                "可以查公开的考研招生、医考公告、医保药品目录和医学文献。"
                "门店优惠、会员、库存和实时药价需要由门店提供数据。"
            )
            await executor.run_turns(run, evidence_answer=lambda _: text)
            return

        saved["healthcare_skill"] = skill_id
        await execution.record_skill(skill, prompt.prompt_hash)
        # A resumed turn must retain the pending tool exchange and its replies.
        dialog = run.messages[1:] if saved.get("active_agent") else await execution.dialog()
        run.messages = [{"role": "system", "content": prompt.system_prompt}, *dialog]
        saved["active_agent"] = execution.definition.id
        await executor.checkpoint(run, stage="healthcare_ready")
        await executor.run_turns(
            run, evidence_answer=lambda current: verified_healthcare_answer(current, skill.tools[0]),
        )
        if span:
            span.output_data = {"status": run.status.value}


async def collect_public_syllabus(execution: SpecialistExecution) -> dict[str, Any]:
    """Return the verified public clause for the workbench's course handoff."""
    run, executor = execution.run, execution.executor
    skill_id = HealthcareSkill.SYLLABUS
    tool = MedicalExamSyllabus.name
    failure = "本次未取得可交接的公开考纲依据，请稍后再试。"
    async with observed_operation(
        execution.resources.observability, "workspace.healthcare", kind=SpanKind.AGENT,
        input_data={"route": run.dispatch_state["route"], "skill": skill_id},
    ) as span:
        registry = await execution.tools()
        try:
            skill, executor.tools, prompt = render_healthcare_skill(skill_id, registry)
        except KeyError:
            raise ValueError(failure) from None
        run.dispatch_state["healthcare_skill"] = skill_id
        await execution.record_skill(skill, prompt.prompt_hash)
        result = await executor.required_tool(run, ToolCall("handoff_syllabus", tool, {}))
        evidence = public_rule_evidence(tool, result)
        if not evidence:
            raise ValueError(failure)
        if span:
            span.output_data = {"public_rule": evidence}
        return evidence


_TOOL_USAGE = {
    "postgraduate_admissions_search": (
        "查医药类硕士的初试科目、复试或规培要求，也可对比两所院校的招生资料。",
        "说明要查的事项、1 至 2 所院校、招生年份、专业及学位类型。有专业代码时注明各校对应代码。",
        "按院校、年份、专业和所问事项检索官方资料。"
        "目前收录武汉科技大学、湖北民族大学 2025 和 2026 年的部分临床医学专硕条目。",
        "分校列出匹配的原文、章节和链接，并指出哪些院校或事项缺少依据。录取率和报录比以官方统计为准。",
    ),
    "medical_exam_registration": (
        "查网上报名和审核安排时用「报名流程核对」。"
        "想确认已有成绩能否免去下一次技能考试时用「实践技能考试免考核对」。",
        "两种核对均需报考年份、考区和考试类别。核对免考时还需上次技能考试年份、"
        "考点是否为国家实践技能考试基地、技能是否合格以及同年的医学综合是否通过。",
        "依据 2026 年湖北考区医师资格考试公告核对。"
        "免考条款适用于 2025 年在国家实践技能考试基地参加技能考试且合格、同年医学综合未通过的考生。",
        "报名流程列出网上报名和资格审核时间及公告出处。"
        "免考给出条款适用性和出处，符合条件时免实践技能考试及该项费用，"
        "仍需报名、资格审核并缴纳医学综合考试费。",
    ),
    "medical_exam_syllabus": (
        "查医师资格考试医学人文部分的修订内容，或核对某考试类别是否适用。",
        "只查通知可直接提出。如要核对特定的修订年份或考试类别，请注明。",
        "依据国家医学考试网的 2026 年修订通知，核对 2024 年版医学综合考试大纲中的医学人文部分。",
        "给出通知发布日期、适用考试类别、条款原文和链接，并说明所问范围能否由这份通知核实。",
    ),
    "medicare_drug_catalogue": (
        "核对药品在国家医保常规目录西药部分的收录和分类。",
        "提供已确认的药品通用名。可指定查 2025 年版或 2026 年版。",
        "按通用名查找同名条目。未指定版次时按生效日期选用已收录的现行目录。",
        "列出所查版次、生效日期和匹配条目的甲乙分类、剂型及原文页码。个人报销待遇按目录备注和参保地规则核定。",
    ),
    "pubmed_literature_search": (
        "按疾病、干预或人群主题找公开发表的医学研究。",
        "给出研究主题，可指定发表年份范围和条数，默认 3 条，最多 5 条。",
        "只将公开研究关键词用于 PubMed 检索，按指定年份核对返回的题录。",
        "给出命中数和可核对的题名、期刊、发表日期、文献类型及 PMID 链接。无匹配题录和来源取数失败分别说明。",
    ),
}


def _public_skill(key: HealthcareSkill, summary: str, *, instruction: str | None = None) -> PublicSkill:
    skill = _SKILLS.get(key)
    return PublicSkill.publish(
        skill, summary=summary, instruction=instruction or skill.prompt_fragment, usage=_TOOL_USAGE,
    )


HEALTHCARE_SPECIALIST = SpecialistDefinition(
    id="healthcare",
    profile=SpecialistProfile("医药业务", "book", "rose", "health"),
    routing_prompt=(
        "只读查询公开医药和考试资料，不需要网页。为本轮选择 healthcare_skill："
        "graduate-admissions 查考研招生科目、资格、报录比和录取率；"
        "medical-exam-registration 查医师报名和技能免考；medical-exam-syllabus 查医考考纲；"
        "medicare-catalogue 查国家医保药品目录和甲乙分类；pubmed-literature 查公开医学文献题录。"
        "门店会员、券、提醒名单、实时药价和库存需要额外数据，选 healthcare 但不指定 Skill。"
        "将个人诊疗、用药和个人医保报销金额请求标为 clarify，说明这里只查询公开资料。"
        "明确的医考或门店问题不因含有参考链接而转 SEO。"
        "院校与招生相关的后续问题可沿用 previous_healthcare_skill。"
    ),
    tools=build_healthcare_tools,
    execute=execute_healthcare,
    intake=HealthcareIntake,
    prepare=prepare_healthcare_route,
    inherit=_inherit_healthcare,
    routing_context=_healthcare_routing_context,
    public_skills=(
        _public_skill(HealthcareSkill.ADMISSIONS, "按院校、年份和专业核对公开招生资料，支持两校对比。"),
        _public_skill(
            HealthcareSkill.REGISTRATION,
            "按年份、考区和考试类别核对报名及技能免考条件。",
            instruction=(
                "报名核对需要报考年份、考区和考试类别。现有报名公告覆盖 2026 年湖北医师资格考试。"
                "按用户给出的地区和年份判断公告是否覆盖。报名流程核对区分网上报名、资格审核和缴费。"
                "实践技能考试免考核对按上次技能考试年份、国家实践技能考试基地、技能及医学综合成绩判断。"
                "条件缺失时请补充。免考结论只适用于实践技能考试及该项费用，仍须办理报名、审核并缴纳医学综合考试费。"
            ),
        ),
        _public_skill(HealthcareSkill.SYLLABUS, "核对考纲修订年份、适用考试和公告原文。"),
        _public_skill(HealthcareSkill.MEDICARE, "用药品通用名核对国家医保目录中的分类及剂型。"),
        _public_skill(HealthcareSkill.LITERATURE, "按公开研究主题和年份检索文献题录及 PMID。"),
    ),
    public_instructions=(
        PublicInstruction(
            id="healthcare-instructions", label="医药共用指令",
            text=(
                "按最新请求核对公开资料，保留已确认的条件。个人诊疗、用药建议、报销待遇、录取判定和门店私有数据不在查询范围。\n"
                "采用用户更正后的条件。必要条件缺失时只询问会影响结果的部分，用户补充后沿用已确认的答案。\n"
                "依据实际查询结果说明适用考期、地区、机构和类别，并列出原公告、条款或文献标识。"
                "区分已核实、部分命中、未核实和数据源失败。查无记录时说明查询范围。"
            ),
        ),
    ),
)
