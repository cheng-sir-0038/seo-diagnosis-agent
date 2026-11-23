from __future__ import annotations

import re
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from src.agent.config import Skill
from src.agent.models import AgentRun, HumanInputState
from src.agent.providers.base import ToolCall
from src.agent.runtime.executor import AgentExecutor
from src.agent.tools.base import AgentTool, ToolResult, ToolRuntimeContext
from src.agent.tools.registry import ToolRegistry
from src.followup_agent.store import FollowupCaseStore

DOMAIN = "followup"
SAFE_INTENT = "请整理本次随访与复诊资料。"
_EXPLICIT_INTENT = re.compile(
    r"随访|复诊|复查.{0,8}(?:记录|报告|材料)|回访.{0,8}(?:记录|药师|提纲)|检查报告.{0,8}(?:对照|对比)"
)
_PUBLIC_SITE_REQUEST = re.compile(r"\bseo\b|网页|站点|页面标题|落地页", re.I)
_EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


def explicit_followup(text: str) -> bool:
    return bool(_EXPLICIT_INTENT.search(text) and not _PUBLIC_SITE_REQUEST.search(text))


class FollowupCommand(StrEnum):
    PREPARE = "整理并核对资料"
    USE_AVAILABLE = "按现有资料整理"
    CONFIRM = "确认保存资料与待办"
    FINISH = "结束本次整理"


class FollowupTool(AgentTool):
    parameters_schema = _EMPTY_SCHEMA

    def __init__(self, store: FollowupCaseStore | None = None) -> None:
        self.store = store

    async def execute(
        self, arguments: dict[str, Any], runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        if self.store is None or runtime is None or not runtime.run_id:
            return ToolResult(success=False, error="复诊资料存储未启动。")
        try:
            result = await self.apply(self.store, runtime.run_id, arguments)
        except (SQLAlchemyError, KeyError, ValueError):
            return ToolResult(success=False, error="复诊资料处理失败，可恢复本次运行后重试。")
        return ToolResult(success=True, data=result)

    async def apply(self, store: FollowupCaseStore, run_id: str, arguments: dict) -> dict:
        raise NotImplementedError


class ReadFollowup(FollowupTool):
    name = "followup_read_materials"
    description = "统计本次资料里已读取的记录、报告日期与无法读取的文件。"

    async def apply(self, store: FollowupCaseStore, run_id: str, arguments: dict) -> dict:
        return await store.read_receipt(run_id)


class PrepareFollowup(FollowupTool):
    name = "followup_prepare_materials"
    description = "校验报告的日期、单位、标本与方法，生成可核对的复诊资料包。"

    async def apply(self, store: FollowupCaseStore, run_id: str, arguments: dict) -> dict:
        return await store.prepare(run_id)


class ConfirmQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0)


class SaveFollowup(FollowupTool):
    name = "followup_save_confirmed"
    description = "核对用户确认的资料版本，保存资料包和已有安排生成的待办。"
    parameters_schema = ConfirmQuery.model_json_schema()

    async def apply(self, store: FollowupCaseStore, run_id: str, arguments: dict) -> dict:
        return await store.confirm(run_id, ConfirmQuery.model_validate(arguments).revision)


FOLLOWUP_SKILL = Skill(
    name="followup-preparation", domain=DOMAIN, description="随访与复诊资料整理",
    tools=[ReadFollowup.name, PrepareFollowup.name, SaveFollowup.name],
    prompt_fragment=(
        "followup 处理用户提供的检查报告、已有安排与日常记录，"
        "可准备复诊带齐清单、回访提纲、做同条件报告数值对照。"
        "医保目录和公开文献继续走 healthcare。涉及诊断或用药决定时明确业务范围。"
    ),
)


def followup_tools(store: FollowupCaseStore | None = None) -> ToolRegistry:
    return ToolRegistry([ReadFollowup(store), PrepareFollowup(store), SaveFollowup(store)])


async def _tool(
    executor: AgentExecutor, run: AgentRun, name: str, step: str, arguments: dict | None = None,
) -> dict:
    result = await executor.required_tool(run, ToolCall(f"{name}:{step}", name, arguments or {}))
    if not result.success:
        raise RuntimeError(result.error or "复诊资料处理失败。")
    return result.data


async def run_followup(
    executor: AgentExecutor, run: AgentRun, store: FollowupCaseStore | None,
) -> None:
    key_hash = run.dispatch_state.get("followup_key_hash")
    if store is None or not key_hash:
        await _finish(executor, run, "请在已启动资料库的工作台整理复诊资料。")
        return
    try:
        await store.create(run.id, key_hash, demo=bool(run.dispatch_state.get("followup_demo")))
    except SQLAlchemyError:
        raise RuntimeError("复诊资料存储暂时不可用。") from None

    pending = run.human_input
    if pending and pending.answer is None:
        await executor.run_turns(run)
        return
    command = pending.answer if pending else None
    step = pending.request_id if pending else "initial"
    if pending and not any(
        event.type == "input.accepted" and event.payload.get("request_id") == step
        for event in run.events
    ):
        await executor.emit(run, "input.accepted", {
            "request_id": step, "answer": command, "action": "answer",
        })
    if command == FollowupCommand.FINISH:
        await _finish(executor, run, "本次资料整理已结束，可在资料区查看已读记录。")
        return
    if (
        command == FollowupCommand.CONFIRM and pending and pending.tool_name == SaveFollowup.name
    ):
        result = await _tool(executor, run, SaveFollowup.name, step, pending.tool_arguments)
        if result["saved"]:
            await _finish(executor, run, f"已保存本次复诊资料包和 {result['tasks']} 条已确认待办。")
            return

    read = await _tool(executor, run, ReadFollowup.name, step)
    needs_report = read["report_dates"] < 2 and command != FollowupCommand.USE_AVAILABLE
    if not read["records"] or needs_report:
        message = (
            "还需要另一份带检查日期的报告。可在资料区补充，也可选择用现有记录继续。"
            if read["records"]
            else "请在资料区导入这次要整理的记录，核对后点击「整理并核对资料」。"
        )
        await _wait(executor, run, ReadFollowup.name, message, {})
        return
    draft = await _tool(executor, run, PrepareFollowup.name, step)
    if draft.get("already_saved"):
        await _finish(executor, run, "本次复诊资料包已保存。")
        return
    await _wait(
        executor, run, SaveFollowup.name,
        "请核对资料来源、对照结果和待办。确认后，工作台会保存这份资料包。",
        {"revision": draft["revision"]},
    )


async def _wait(
    executor: AgentExecutor, run: AgentRun, tool: str, message: str, arguments: dict,
) -> None:
    run.human_input = HumanInputState(
        request_id=f"input_{uuid4().hex}", tool_name=tool, tool_arguments=arguments,
        tool_result={}, questions=[message], message=message,
    )
    await executor.run_turns(run)


async def _finish(executor: AgentExecutor, run: AgentRun, answer: str) -> None:
    run.human_input = None
    await executor.run_turns(run, evidence_answer=lambda _: answer)
