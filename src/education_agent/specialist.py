"""Workspace definitions for learning practice and teacher exam preparation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from src.agent.config import Skill
from src.agent.providers.base import ToolCall
from src.agent.tools.registry import ToolRegistry
from src.agent.workspace.registry import (
    PublicSkill,
    RoutingTurn,
    SpecialistDefinition,
    SpecialistProfile,
    ToolContext,
)
from src.education_agent.practice import MAX_QUESTIONS, MathPractice, MathPracticeQuery
from src.education_agent.resources import LearningResources, ResourceQuery
from src.education_agent.skills import (
    EDUCATION_SKILLS,
    EXAM_PREP_DOMAIN,
    LEARNING_DOMAIN,
    EducationSkill,
    education_tools,
    learning_history,
)
from src.education_agent.teacher_exam import TeacherExamQuery
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind

if TYPE_CHECKING:
    from src.agent.models import AgentRun
    from src.agent.workspace.execution import SpecialistExecution

__all__ = ["LEARNING_SPECIALIST", "EXAM_PREP_SPECIALIST"]


class LearningIntake(BaseModel):
    learning_skill: Literal[EducationSkill.MATH_PRACTICE, EducationSkill.RESOURCES] | None = None
    math_practice: MathPracticeQuery | None = None
    resources: ResourceQuery | None = None


class ExamPrepIntake(BaseModel):
    teacher_exam: TeacherExamQuery | None = None


def prepare_learning_route(turn: RoutingTurn) -> str | None:
    """Keep the validated arguments for the selected learning activity."""
    skill_id = getattr(turn.decision, "learning_skill", None)
    if not skill_id:
        return "你想做数学题，还是找可以跟着练习的官方学习资料？"
    field = "math_practice" if skill_id == EducationSkill.MATH_PRACTICE else "resources"
    request = getattr(turn.decision, field, None)
    turn.state.update(
        learning_skill=skill_id,
        learning_arguments=request.model_dump(mode="json") if request else {},
    )
    return None


def prepare_exam_prep_route(turn: RoutingTurn) -> str | None:
    """Merge new fields into the current pending exam scope and ask for missing facts."""
    pending = (turn.state.get("teacher_exam") or {}) if turn.pending_route == EXAM_PREP_DOMAIN else {}
    extracted = getattr(turn.decision, "teacher_exam", None) or TeacherExamQuery()
    query = TeacherExamQuery.model_validate({**pending, **extracted.model_dump(exclude_none=True)})
    turn.state["teacher_exam"] = query.model_dump(mode="json", exclude_none=True)
    return query.clarification()


def _inherit_exam_prep(previous: dict[str, Any]) -> dict[str, Any]:
    return {"previous_teacher_exam": previous.get("teacher_exam")}


def _exam_prep_routing_context(state: dict[str, Any], pending_route: str | None) -> dict[str, Any]:
    return {
        "previous_teacher_exam": state.get("previous_teacher_exam"),
        "pending_teacher_exam": state.get("teacher_exam") if pending_route == EXAM_PREP_DOMAIN else None,
    }


async def build_learning_tools(context: ToolContext) -> ToolRegistry:
    """Build math practice and resource tools with optional conversation history."""
    return education_tools(
        LEARNING_DOMAIN,
        review_ids=context.options.get("review_ids"),
        completed_resources=context.options.get("completed_resources"),
        practice_limit=context.options.get("practice_limit", MAX_QUESTIONS),
    )


async def build_exam_prep_tools(_context: ToolContext) -> ToolRegistry:
    """Build the official written-exam lookup tool."""
    return education_tools(EXAM_PREP_DOMAIN)


async def execute_learning(execution: SpecialistExecution) -> None:
    """Run the selected learning activity with the conversation's learning records."""
    run = execution.run
    saved = run.dispatch_state
    if (
        "learning_history" not in saved and execution.load_tool_data is not None
        and run.conversation_id
    ):
        receipts = await execution.load_tool_data(
            run.conversation_id, (MathPractice.name, LearningResources.name),
        )
        saved["learning_history"] = learning_history(receipts)
    await _execute_education(
        execution, EDUCATION_SKILLS.get(saved["learning_skill"]), saved["learning_arguments"],
        **saved.get("learning_history", {}),
        practice_limit=max(0, min(MAX_QUESTIONS, execution.executor.max_tool_calls - 1)),
    )


async def execute_exam_prep(execution: SpecialistExecution) -> None:
    """Look up the written exam for the confirmed scope."""
    await _execute_education(
        execution, EDUCATION_SKILLS.get(EducationSkill.TEACHER_EXAM),
        execution.run.dispatch_state["teacher_exam"],
    )


async def _execute_education(
    execution: SpecialistExecution, skill: Skill, arguments: dict[str, Any], **options: Any,
) -> None:
    run, executor = execution.run, execution.executor
    agent = execution.definition.id
    tool_name = skill.tools[0]
    async with observed_operation(
        execution.resources.observability, f"workspace.{agent}", kind=SpanKind.AGENT,
        input_data={"skill": skill.name, "arguments": arguments},
    ) as span:
        registry = await execution.tools(**options)
        executor.tools = ToolRegistry(registry.get(name) for name in skill.tools)
        await execution.record_skill(skill)
        run.dispatch_state["active_agent"] = agent

        def answer(current: AgentRun) -> str | None:
            for event in reversed(current.events):
                if event.type not in {"tool.completed", "tool.failed"} or (
                    event.payload.get("name") != tool_name
                ):
                    continue
                result = event.payload["result"]
                if not result["success"]:
                    return result.get("error") or "本次未能取得资料，请稍后重试。"
                return result["data"].get("answer")
            return None

        await executor.run_tool_workflow(
            run, ToolCall(f"{tool_name}_start", tool_name, arguments), evidence_answer=answer,
        )
        if span:
            span.output_data = {"status": run.status.value}


_TOOL_USAGE = {
    "tal_math_practice": (
        "逐题练习一元一次方程及应用题，或重做这段对话里的错题。",
        "可指定题数和难度。作答时回复当前题的选项，也可以跳过或结束。",
        "从 TAL-SCQ5K 原题中逐道出题，默认 3 道，单次最多 5 道。"
        "重练只取本对话的错题，按题库实际覆盖的题型和难度出题。",
        "作答后给出正误、原题解析和出处。结束时汇总答对、答错、跳过和未答题数。",
    ),
    "math_resource_lookup": (
        "找适合学习主题或年龄的官方数学活动，或记录一项已完成的练习。",
        "找资料可提供主题或年龄。记录完成时请说明已完成哪项活动。",
        "从 Oxford Owl 的分数、小数、百分数活动和 Cambridge NRICH 的方程探究中筛选。"
        "明确完成且匹配到单项活动时才记录。",
        "给出匹配活动的官网链接、适龄范围和练习建议，或确认本对话记录的已完成活动。",
    ),
    "ntce_written_exam_lookup": (
        "核对教师资格笔试的报名要求、考试科目和大纲，安排分科备考。",
        "提供报考省份、年份、上半年或下半年，以及完整的学段和学科。",
        "按报考条件匹配当期公告，现有资料覆盖 2026 年湖北初中数学教资笔试的上下半年。",
        "给出报名、缴费、打印准考证与笔试时间、报名核对项、笔试科目和官方大纲链接。另附分科练习建议。",
    ),
}


def _public_skill(key: EducationSkill, summary: str) -> PublicSkill:
    skill = EDUCATION_SKILLS.get(key)
    return PublicSkill.publish(
        skill, summary=summary, instruction="\n".join(_TOOL_USAGE[skill.tools[0]]), usage=_TOOL_USAGE,
    )


LEARNING_SPECIALIST = SpecialistDefinition(
    id=LEARNING_DOMAIN,
    profile=SpecialistProfile("学习陪练", "calculator", "indigo", "learning"),
    routing_prompt="\n".join((
        "负责数学陪练和推荐学习资料。考研仍走 healthcare。",
        EDUCATION_SKILLS.get(EducationSkill.MATH_PRACTICE).prompt_fragment,
        EDUCATION_SKILLS.get(EducationSkill.RESOURCES).prompt_fragment,
    )),
    tools=build_learning_tools,
    execute=execute_learning,
    intake=LearningIntake,
    prepare=prepare_learning_route,
    public_skills=(
        _public_skill(
            EducationSkill.MATH_PRACTICE,
            "逐题练习一元一次方程及应用题，可按难度出题、判分和重练错题。",
        ),
        _public_skill(
            EducationSkill.RESOURCES,
            "按主题查找官方数学课程或练习链接，并记录已完成的练习。",
        ),
    ),
    deterministic_path="出题、判分和资料检索由工具按固定规则执行，无专员提示词。",
    requires_text_model=False,
)


EXAM_PREP_SPECIALIST = SpecialistDefinition(
    id=EXAM_PREP_DOMAIN,
    profile=SpecialistProfile("报考备考", "form", "amber", "learning"),
    routing_prompt=(
        "教师资格笔试走 exam_prep，条件不全也先保留已明确的字段。"
        "待补充 teacher_exam 时用 pending_teacher_exam 与用户刚补充的条件合并，"
        "该次查询尚未结束。后续追问同一考期时可沿用 previous_teacher_exam。\n"
        + EDUCATION_SKILLS.get(EducationSkill.TEACHER_EXAM).prompt_fragment
    ),
    tools=build_exam_prep_tools,
    execute=execute_exam_prep,
    intake=ExamPrepIntake,
    prepare=prepare_exam_prep_route,
    inherit=_inherit_exam_prep,
    routing_context=_exam_prep_routing_context,
    public_skills=(
        _public_skill(EducationSkill.TEACHER_EXAM, "按省份、考期、学段和学科核对报名、笔试科目与考纲。"),
    ),
    deterministic_path="公告匹配和备考清单由工具按固定规则生成，无专员提示词。",
    requires_text_model=False,
)
