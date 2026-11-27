"""Register the workspace's local follow-up preparation workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.agent.tools.registry import ToolRegistry
from src.agent.workspace.registry import (
    PublicSkill,
    RoutingTurn,
    SpecialistDefinition,
    SpecialistProfile,
    ToolContext,
    WorkspaceResources,
)
from src.followup_agent.store import FollowupCaseStore
from src.followup_agent.workflow import (
    DOMAIN,
    FOLLOWUP_SKILL,
    PrepareFollowup,
    ReadFollowup,
    SaveFollowup,
    followup_tools,
    run_followup,
)
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind

if TYPE_CHECKING:
    from src.agent.workspace.execution import SpecialistExecution


def _case_store(resources: WorkspaceResources) -> FollowupCaseStore | None:
    database = resources.database
    return FollowupCaseStore(database) if database.started else None


async def build_followup_tools(context: ToolContext) -> ToolRegistry:
    """Build the local tools, sharing the invocation's case store when supplied."""
    if "store" in context.options:
        store = context.options["store"]
    else:
        store = _case_store(context.resources)
    return followup_tools(store)


def prepare_followup_route(turn: RoutingTurn) -> str | None:
    """Keep a follow-up case independent of the conversation's website target."""
    turn.state.pop("target_url", None)
    return None


async def execute_workspace_followup(execution: SpecialistExecution) -> None:
    """Prepare or resume the same local case and its confirmation receipts."""
    run, executor = execution.run, execution.executor
    async with observed_operation(
        execution.resources.observability, "workspace.followup", kind=SpanKind.AGENT,
        input_data={"skill": FOLLOWUP_SKILL.name},
        metadata={"agent_profile": execution.definition.profile.public_data()},
    ) as span:
        store = _case_store(execution.resources)
        executor.tools = await execution.tools(store=store)
        await execution.record_skill(FOLLOWUP_SKILL)
        run.dispatch_state["active_agent"] = execution.definition.id
        await run_followup(executor, run, store)
        if span:
            span.output_data = {"status": run.status.value}


FOLLOWUP_SPECIALIST = SpecialistDefinition(
    id=DOMAIN,
    profile=SpecialistProfile("随访与复诊准备", "profile", "indigo", "health"),
    routing_prompt=FOLLOWUP_SKILL.prompt_fragment,
    tools=build_followup_tools,
    execute=execute_workspace_followup,
    prepare=prepare_followup_route,
    requires_text_model=False,
    directory_note="使用本次提供的资料整理随访与复诊准备清单。",
    deterministic_path="清单整理、报告对照和确认保存按固定规则执行，无专员提示词。",
    public_skills=(PublicSkill.publish(
        FOLLOWUP_SKILL,
        summary="从已有资料整理复诊前清单，逐条标注来源，并保存用户确认的当前版本。",
        instruction=(
            "使用本次资料区的门诊小结、预约单、日常记录或检查报告。有一份可读资料即可整理。"
            "按要带的资料、已有安排、要问医生的问题生成清单，每条标注文件和解析行，"
            "影响条目的资料缺口附在相应条目旁。检查报告仅在日期不同且项目、单位、标本和方法可比时计算差值，"
            "原值和对照依据可在资料细节查看。用户明确确认当前版本后才保存，待办取自未记录完成的已有安排。"
            "资料在工作台内整理，诊断和用药由临床人员判断。"
        ),
        usage={
            ReadFollowup.name: (
                "准备下次复诊时，核对门诊小结、预约单、日常记录或检查报告是否可读取。",
                "在本次资料区导入同一对象的 CSV、JSON 或模板文字 PDF。有一份可读资料即可整理。",
                "在工作台校验对象编号和文件格式，记录每条资料的文件和解析位置。",
                "统计有效记录和未读入的文件，保留报告日期数与可对照项目数供资料核对。",
            ),
            PrepareFollowup.name: (
                "整理要带的资料、已有安排和要问医生的问题。",
                "导入本次复诊的任一可读资料。要算报告差值时，提供日期不同且项目、单位、标本和方法可比的原值。",
                "按固定规则引用已有记录和预约，并把相关记录写成可向医生询问的问题。诊断和用药由临床人员判断。",
                "生成带文件和解析行的清单草稿。有关联的资料缺口附在对应条目旁，检查原值和对照依据可在资料细节查看。",
            ),
            SaveFollowup.name: (
                "核对清单后，将当前版本和已确认的待办保存到本次复诊任务。",
                "在资料区核对草稿与待办条数，再确认保存当前版本。",
                "资料更新后须重新确认。待办只取自未记录完成的已有安排，重复提交沿用原保存记录。",
                "从同一任务回看已保存的资料包和待办，保留每项来源与原安排中已有的日期。",
            ),
        },
    ),),
)
