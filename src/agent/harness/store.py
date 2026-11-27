from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import cast, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.harness.summary import build_survival_summary
from src.agent.models import (
    AgentEvent,
    AgentRun,
    HumanInputAnswer,
    HumanInputState,
    RunStatus,
)
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import (
    AgentRunCheckpointRow,
    AgentRunEventRow,
    AgentRunRow,
    AgentRuntimeTaskRow,
    AgentSurvivalSummaryRow,
    SiteRow,
    WorkflowThreadRow,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


@dataclass(frozen=True)
class CreatedAgentTaskRun:
    task_id: str
    thread_id: str
    run_id: str


class AgentChatHarnessStore:
    def __init__(self, database: SeoAgentDatabase) -> None:
        self.database = database

    async def create_task_run(
        self,
        *,
        site_id: str | None = None,
        conversation_id: str | None,
        title: str,
        user_intent: str,
        messages: list[dict[str, Any]],
        model_provider: str | None,
        model_name: str | None,
        prompt_version: str | None = None,
        prompt_hash: str | None = None,
        available_tools: list[str] | None = None,
        dispatch_state: dict[str, Any] | None = None,
    ) -> CreatedAgentTaskRun:
        task_id = _id("agtask")
        if not conversation_id:
            thread_key = _id("chat")
        elif site_id is None:
            thread_key = f"conversation:{conversation_id}"
        else:
            thread_key = f"chat_{site_id}_{conversation_id}"
        workflow_type = "AGENT_CHAT" if site_id is None else "SEO_CHAT"
        async with self.database.orm_session(write=True) as session:
            if site_id is not None and await session.get(SiteRow, site_id) is None:
                raise KeyError(f"站点不存在: {site_id}")
            await session.execute(
                insert(WorkflowThreadRow)
                .values(
                    id=_id("wft"),
                    site_id=site_id,
                    thread_id=thread_key,
                    workflow_type=workflow_type,
                    graph_version="chat-v1",
                    policy_version="chat-v1",
                    business_object_type="CONVERSATION",
                    business_object_id=conversation_id or task_id,
                    status="ACTIVE",
                    current_stage="created",
                    created_at=_now(),
                )
                .on_conflict_do_nothing(index_elements=[WorkflowThreadRow.thread_id])
            )
            thread = (
                await session.scalars(
                    select(WorkflowThreadRow)
                    .where(WorkflowThreadRow.thread_id == thread_key)
                    .with_for_update()
                )
            ).one()
            if thread.site_id != site_id or thread.workflow_type != workflow_type:
                raise ValueError("此会话编号已用于其他业务范围")
            thread.status = "ACTIVE"
            thread.current_stage = "created"
            thread.completed_at = None
            thread.business_object_type = "CONVERSATION"
            thread.business_object_id = conversation_id or task_id
            now = _now()
            saved = AgentRunRow(
                id=_id("run"),
                workflow_thread_id=thread.id,
                trigger_type="CHAT",
                status="PENDING",
                current_stage="created",
                model_provider=model_provider,
                model_name=model_name,
                token_usage_json="{}",
                started_at=now,
                created_at=now,
            )
            session.add(saved)
            await session.flush()
            task = AgentRuntimeTaskRow(
                id=task_id,
                site_id=site_id,
                conversation_id=conversation_id,
                workflow_thread_id=thread.id,
                title=title[:200] or "对话任务",
                user_intent=user_intent,
                status="PENDING",
                status_group="pending",
                current_stage="created",
                current_run_id=saved.id,
                survival_summary_json="{}",
                prompt_version=prompt_version,
                prompt_hash=prompt_hash,
                available_tools_json=_dump(available_tools or []),
                created_at=now,
                updated_at=now,
            )
            session.add(task)
            await session.flush()
            run = AgentRun(
                id=saved.id,
                messages=messages,
                task_id=task_id,
                thread_id=thread_key,
                site_id=site_id,
                conversation_id=conversation_id,
                dispatch_state=dict(dispatch_state or {}),
                model_provider=model_provider,
                model_name=model_name,
            )
            await _write_checkpoint(
                session,
                saved,
                task,
                run,
                "created",
                messages=messages,
                iteration=0,
                tool_count=0,
                pending_tool_calls=[],
                token_usage={},
            )
            return CreatedAgentTaskRun(task_id=task_id, thread_id=thread_key, run_id=run.id)

    async def persist_event(self, run: AgentRun, event: AgentEvent) -> None:
        # Token deltas are streamed live. The completed message is stored with its checkpoint.
        if event.type == "message.delta":
            return
        async with self.database.orm_session(write=True) as session:
            saved, task, _ = await _run_records(session, run.id, lock=True)
            await _write_event(session, saved, task, run, event)

    async def save_waiting(
        self,
        run: AgentRun,
        *,
        messages: list[dict[str, Any]],
        events: tuple[AgentEvent, AgentEvent],
    ) -> dict[str, Any]:
        message, wait = events
        if message.type != "message.completed" or wait.type != "run.waiting_for_input":
            raise ValueError("等待事件必须包含回复和补充请求")
        if any(
            e.run_id != run.id or e.sequence != run.sequence + i for i, e in enumerate(events, 1)
        ):
            raise ValueError("等待事件必须属于当前运行并按顺序发布")
        if not run.human_input or wait.payload.get("request_id") != run.human_input.request_id:
            raise ValueError("等待事件必须对应当前的补充问题")
        waiting = replace(
            run,
            status=RunStatus.WAITING_FOR_INPUT,
            messages=messages,
            events=[*run.events, *events],
        )
        async with self.database.orm_session(write=True) as session:
            saved, task, _ = await _run_records(session, run.id, lock=True)
            for event in events:
                await _write_event(session, saved, task, waiting, event)
            return await _write_checkpoint(
                session,
                saved,
                task,
                waiting,
                "waiting_for_input",
                messages=messages,
                iteration=run.iteration,
                tool_count=run.tool_count,
                pending_tool_calls=run.pending_tool_calls,
                token_usage=run.token_usage,
            )

    async def save_checkpoint(
        self,
        run: AgentRun,
        *,
        messages: list[dict[str, Any]],
        iteration: int,
        tool_count: int,
        pending_tool_calls: list[dict[str, Any]],
        stage: str,
        token_usage: dict[str, int],
    ) -> dict[str, Any]:
        async with self.database.orm_session(write=True) as session:
            saved, task, _ = await _run_records(session, run.id, lock=True)
            summary = await _write_checkpoint(
                session,
                saved,
                task,
                run,
                stage,
                messages=messages,
                iteration=iteration,
                tool_count=tool_count,
                pending_tool_calls=pending_tool_calls,
                token_usage=token_usage,
            )
        run.messages = [dict(item) for item in messages]
        run.iteration, run.tool_count = iteration, tool_count
        run.pending_tool_calls = [dict(item) for item in pending_tool_calls]
        run.token_usage, run.survival_summary = dict(token_usage), summary
        return summary

    async def finalize_run(self, run: AgentRun) -> None:
        async with self.database.orm_session(write=True) as session:
            saved, task, _ = await _run_records(session, run.id, lock=True)
            _set_stage(saved, task, run, run.status.value, run.token_usage, run.survival_summary)
            ended = task.status_group == "ended"
            saved.completed_at = task.completed_at = _now() if ended else None
            saved.stop_reason = run.status.value if ended else None
            saved.error_message = task.error_message = run.error
            if ended:
                await session.execute(
                    update(WorkflowThreadRow)
                    .where(WorkflowThreadRow.id == saved.workflow_thread_id)
                    .values(
                        current_stage=run.status.value,
                        status="COMPLETED",
                        completed_at=saved.completed_at,
                    )
                )

    async def restore_run(self, run_id: str, *, mark_recovering: bool = True) -> AgentRun:
        async with self.database.orm_session(write=mark_recovering) as session:
            saved, task, thread_key = await _run_records(session, run_id, lock=mark_recovering)
            run = _restore_run(await _run_detail(session, saved, task, thread_key))
            if mark_recovering:
                _begin_recovery(saved, task, run)
            return run

    async def claim_retry(self, run_id: str) -> AgentRun:
        async with self.database.orm_session(write=True) as session:
            saved, task, thread_key = await _run_records(session, run_id, lock=True)
            if saved.status not in {"FAILED", "RECOVERY_BLOCKED"}:
                raise ValueError("只有失败或恢复受阻的运行可以重试")
            run = _restore_run(await _run_detail(session, saved, task, thread_key))
            _begin_recovery(saved, task, run)
            return run

    async def submit_input(
        self, run_id: str, answer: HumanInputAnswer,
        *, validate_pending: Callable[[HumanInputState], None] | None = None,
    ) -> tuple[AgentRun, bool, int]:
        """Claim input once and validate the pending request under the same run lock."""
        async with self.database.orm_session(write=True) as session:
            # Serialize the receipt check and checkpoint on the run, across processes.
            saved, task, thread_key = await _run_records(session, run_id, lock=True)
            run = _restore_run(await _run_detail(session, saved, task, thread_key))
            for event in run.events:
                if (
                    event.type == "input.submitted"
                    and event.payload.get("request_id") == answer.request_id
                ):
                    if (
                        event.payload.get("answer") != answer.answer
                        or event.payload.get("action", "answer") != answer.action
                        or event.payload.get("followup_revision") != answer.followup_revision
                    ):
                        raise ValueError("这次补充已提交，不能用同一请求替换回答")
                    return run, True, event.sequence
            pending = run.human_input
            if run.status != RunStatus.WAITING_FOR_INPUT or pending is None:
                raise ValueError("当前运行没有等待用户补充")
            if pending.request_id != answer.request_id:
                raise ValueError("补充请求已经变化，请刷新后回答当前问题")
            if answer.action == "use_available" and not pending.allow_use_available:
                raise ValueError("当前步骤必须补充必要信息，不能直接跳过")
            if validate_pending is not None:
                validate_pending(pending)
            pending.answer, pending.action = answer.answer, answer.action
            run.messages.append({"role": "user", "content": answer.answer})
            run.status = RunStatus.RECOVERING
            event = run.emit("input.submitted", answer.model_dump(mode="json", exclude_none=True))
            await _write_event(session, saved, task, run, event)
            run.survival_summary = await _write_checkpoint(
                session,
                saved,
                task,
                run,
                "input_submitted",
                messages=run.messages,
                iteration=run.iteration,
                tool_count=run.tool_count,
                pending_tool_calls=run.pending_tool_calls,
                token_usage=run.token_usage,
            )
            return run, False, event.sequence

    async def list_recoverable_runs(self) -> list[str]:
        async with self.database.orm_session() as session:
            return list(
                await session.scalars(
                    select(AgentRunRow.id)
                    .where(
                        AgentRunRow.trigger_type == "CHAT",
                        AgentRunRow.status.in_(("PENDING", "RUNNING", "RECOVERING")),
                    )
                    .order_by(AgentRunRow.created_at, AgentRunRow.id)
                )
            )

    async def mark_recovery_blocked(self, run_id: str, message: str) -> None:
        async with self.database.orm_session(write=True) as session:
            saved, task, _ = await _run_records(session, run_id, lock=True)
            saved.status = task.status = "RECOVERY_BLOCKED"
            saved.current_stage = task.current_stage = "recovery_blocked"
            saved.error_message = task.error_message = message[:1000]
            task.status_group, task.updated_at = "running", _now()

    async def get_task(self, task_id: str) -> dict[str, Any]:
        async with self.database.orm_session() as session:
            row = (
                await session.execute(_TASKS_WITH_THREAD.where(AgentRuntimeTaskRow.id == task_id))
            ).one_or_none()
            if row is None:
                raise KeyError(f"Agent Task 不存在: {task_id}")
            return _decode_task(*row)

    async def list_tasks(
        self, *, site_id: str | None = None, status_group: str | None = None, limit: int = 100,
        workspace_conversation_ids: list[str] | None = None,
        exclude_prompt_version: str | None = None,
    ) -> list[dict[str, Any]]:
        query = _TASKS_WITH_THREAD.order_by(
            AgentRuntimeTaskRow.updated_at.desc(), AgentRuntimeTaskRow.id.desc(),
        )
        if site_id:
            query = query.where(AgentRuntimeTaskRow.site_id == site_id)
        if workspace_conversation_ids is not None:
            query = query.where(
                AgentRuntimeTaskRow.site_id.is_(None),
                AgentRuntimeTaskRow.conversation_id.in_(workspace_conversation_ids),
            )
        if status_group:
            query = query.where(AgentRuntimeTaskRow.status_group == status_group)
        if exclude_prompt_version:
            query = query.where(
                AgentRuntimeTaskRow.prompt_version.is_distinct_from(exclude_prompt_version),
            )
        async with self.database.orm_session() as session:
            if workspace_conversation_ids is not None:
                run_id = AgentRuntimeTaskRow.current_run_id
                checkpoint_exists = select(AgentRunCheckpointRow.id).where(
                    AgentRunCheckpointRow.agent_run_id == run_id,
                ).exists()
                completion = select(AgentRunEventRow.payload_json).where(
                    AgentRunEventRow.agent_run_id == run_id,
                    AgentRunEventRow.event_type == "run.completed",
                ).order_by(AgentRunEventRow.sequence.desc()).limit(1).scalar_subquery()
                rows = await session.execute(
                    query.add_columns(checkpoint_exists, completion).limit(max(1, min(limit, 500)))
                )
                return [
                    {
                        **_decode_task(task, thread_key),
                        "can_resume": has_checkpoint
                        and task.status in {"FAILED", "RECOVERY_BLOCKED"},
                        "partial": task.status == "COMPLETED"
                        and _json(completed_payload, {}).get("partial") is True,
                    }
                    for task, thread_key, has_checkpoint, completed_payload in rows
                ]
            rows = await session.execute(query.limit(max(1, min(limit, 500))))
            return [_decode_task(*row) for row in rows]

    async def latest_dispatch_state(
        self, conversation_id: str, *, include_dialog: bool = True
    ) -> dict[str, Any]:
        async with self.database.orm_session() as session:
            checkpoint = await _completed_conversation_checkpoint(session, conversation_id)
            if checkpoint is None:
                return {}
            checkpoint_id, run_id, dispatch_state_json = checkpoint
            state = _json(dispatch_state_json, {})
            if state.get("active_agent"):
                state["source_run_id"] = run_id
                if include_dialog:
                    state["server_dialog"] = await _checkpoint_dialog(session, checkpoint_id)
            return state

    async def completed_dialog(
        self, run_id: str, conversation_id: str, agent: str
    ) -> list[dict[str, str]]:
        """Load text only after verifying the pinned run, conversation and specialist."""
        if not run_id or not conversation_id or not agent:
            return []
        async with self.database.orm_session() as session:
            checkpoint = await _completed_conversation_checkpoint(session, conversation_id, run_id)
            if checkpoint is None:
                return []
            checkpoint_id, _, dispatch_state_json = checkpoint
            if _json(dispatch_state_json, {}).get("active_agent") != agent:
                return []
            return await _checkpoint_dialog(session, checkpoint_id)

    async def completed_tool_data(
        self, conversation_id: str, names: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        payload = cast(AgentRunEventRow.payload_json, JSONB)
        query = (
            select(payload["result"]["data"])
            .select_from(AgentRunEventRow)
            .join(AgentRunRow, AgentRunEventRow.agent_run_id == AgentRunRow.id)
            .join(WorkflowThreadRow, AgentRunRow.workflow_thread_id == WorkflowThreadRow.id)
            .where(
                WorkflowThreadRow.thread_id == f"conversation:{conversation_id}",
                WorkflowThreadRow.site_id.is_(None),
                WorkflowThreadRow.workflow_type == "AGENT_CHAT",
                AgentRunRow.trigger_type == "CHAT",
                AgentRunRow.status == "COMPLETED",
                AgentRunEventRow.event_type == "tool.completed",
                payload["name"].astext.in_(names),
            )
            .order_by(AgentRunRow.completed_at, AgentRunEventRow.created_at, AgentRunEventRow.id)
        )
        async with self.database.orm_session() as session:
            return list(await session.scalars(query))

    async def get_run(self, run_id: str) -> dict[str, Any]:
        async with self.database.orm_session() as session:
            saved, task, thread_key = await _run_records(session, run_id)
            return await _run_detail(session, saved, task, thread_key)


_TASKS_WITH_THREAD = select(AgentRuntimeTaskRow, WorkflowThreadRow.thread_id).join(
    WorkflowThreadRow, WorkflowThreadRow.id == AgentRuntimeTaskRow.workflow_thread_id
)


async def _run_records(
    session: AsyncSession, run_id: str, *, lock: bool = False
) -> tuple[AgentRunRow, AgentRuntimeTaskRow, str]:
    query = (
        select(AgentRunRow, AgentRuntimeTaskRow, WorkflowThreadRow.thread_id)
        .join_from(
            AgentRunRow, WorkflowThreadRow, AgentRunRow.workflow_thread_id == WorkflowThreadRow.id
        )
        .join(AgentRuntimeTaskRow, AgentRuntimeTaskRow.current_run_id == AgentRunRow.id)
        .where(AgentRunRow.id == run_id)
    )
    if lock:
        query = query.with_for_update(of=AgentRunRow)
    row = (await session.execute(query)).one_or_none()
    if row is None:
        raise KeyError(f"Agent Run 不存在: {run_id}")
    return row[0], row[1], row[2]


def _set_stage(
    saved: AgentRunRow,
    task: AgentRuntimeTaskRow,
    run: AgentRun,
    stage: str,
    token_usage: dict[str, int],
    summary: dict[str, Any],
) -> None:
    saved.status = task.status = run.status.value.upper()
    saved.current_stage = task.current_stage = stage
    saved.model_provider, saved.model_name = run.model_provider, run.model_name
    saved.token_usage_json = _dump(token_usage)
    task.status_group, task.updated_at = _status_group(saved.status), _now()
    task.survival_summary_json = _dump(summary)


def _begin_recovery(saved: AgentRunRow, task: AgentRuntimeTaskRow, run: AgentRun) -> None:
    run.status = RunStatus.RECOVERING
    run.error = saved.error_message = task.error_message = None
    run.finished_at = saved.completed_at = task.completed_at = None
    saved.stop_reason = None
    _set_stage(saved, task, run, "recovering", run.token_usage, run.survival_summary)


async def _write_event(
    session: AsyncSession,
    saved: AgentRunRow,
    task: AgentRuntimeTaskRow,
    run: AgentRun,
    event: AgentEvent,
) -> None:
    inserted = await session.scalar(
        insert(AgentRunEventRow)
        .values(
            id=event.event_id,
            agent_run_id=run.id,
            sequence=event.sequence,
            event_type=event.type,
            payload_json=_dump(event.payload),
            created_at=event.created_at.isoformat(),
        )
        .on_conflict_do_nothing()
        .returning(AgentRunEventRow.id)
    )
    if inserted is not None:
        _set_stage(
            saved,
            task,
            run,
            str(event.payload.get("stage") or event.type),
            run.token_usage,
            run.survival_summary,
        )


async def _write_checkpoint(
    session: AsyncSession,
    saved: AgentRunRow,
    task: AgentRuntimeTaskRow,
    run: AgentRun,
    stage: str,
    *,
    messages: list[dict[str, Any]],
    iteration: int,
    tool_count: int,
    pending_tool_calls: list[dict[str, Any]],
    token_usage: dict[str, int],
) -> dict[str, Any]:
    summary = build_survival_summary(
        messages=messages,
        events=run.events,
        stage=stage,
        token_usage=token_usage,
    )
    summary.human_input = run.human_input.model_dump(mode="json") if run.human_input else None
    summary_data, summary_json = summary.model_dump(mode="json"), summary.model_dump_json()
    # Every checkpoint writer holds the parent run row lock until this transaction commits.
    previous = await session.scalar(
        select(func.max(AgentRunCheckpointRow.checkpoint_no)).where(
            AgentRunCheckpointRow.agent_run_id == run.id
        )
    )
    now = _now()
    checkpoint = AgentRunCheckpointRow(
        id=_id("agcp"),
        agent_run_id=run.id,
        checkpoint_no=(previous or 0) + 1,
        stage=stage,
        iteration=iteration,
        tool_count=tool_count,
        messages_json=_dump(messages),
        pending_tool_calls_json=_dump(pending_tool_calls),
        token_usage_json=_dump(token_usage),
        survival_summary_json=summary_json,
        dispatch_state_json=_dump(run.dispatch_state),
        created_at=now,
    )
    session.add(checkpoint)
    await session.flush()
    session.add(
        AgentSurvivalSummaryRow(
            id=_id("agsum"),
            agent_runtime_task_id=task.id,
            agent_run_id=run.id,
            checkpoint_id=checkpoint.id,
            summary_json=summary_json,
            created_at=now,
        )
    )
    await session.execute(
        update(WorkflowThreadRow)
        .where(WorkflowThreadRow.id == saved.workflow_thread_id)
        .values(current_stage=stage, current_checkpoint_id=checkpoint.id, last_resumed_at=now)
    )
    _set_stage(saved, task, run, stage, token_usage, summary_data)
    return summary_data


def _decode_task(task: AgentRuntimeTaskRow, thread_key: str) -> dict[str, Any]:
    return {
        **task.as_dict(),
        "thread_id": thread_key,
        "survival_summary": _json(task.survival_summary_json, {}),
        "available_tools": _json(task.available_tools_json, []),
    }


async def _completed_conversation_checkpoint(
    session: AsyncSession,
    conversation_id: str,
    run_id: str | None = None,
) -> Row[tuple[str, str, str]] | None:
    run = (
        select(AgentRunRow.id)
        .join(WorkflowThreadRow, AgentRunRow.workflow_thread_id == WorkflowThreadRow.id)
        .where(
            WorkflowThreadRow.thread_id == f"conversation:{conversation_id}",
            WorkflowThreadRow.site_id.is_(None),
            WorkflowThreadRow.workflow_type == "AGENT_CHAT",
            AgentRunRow.trigger_type == "CHAT",
            AgentRunRow.status == "COMPLETED",
        )
    )
    if run_id is None:
        run = run.order_by(AgentRunRow.started_at.desc(), AgentRunRow.id.desc())
    else:
        run = run.where(AgentRunRow.id == run_id)
    query = (
        select(
            AgentRunCheckpointRow.id,
            AgentRunCheckpointRow.agent_run_id,
            AgentRunCheckpointRow.dispatch_state_json,
        )
        .where(AgentRunCheckpointRow.agent_run_id == run.limit(1).scalar_subquery())
        .order_by(AgentRunCheckpointRow.checkpoint_no.desc())
        .limit(1)
    )
    return (await session.execute(query)).one_or_none()


async def _checkpoint_dialog(session: AsyncSession, checkpoint_id: str) -> list[dict[str, str]]:
    messages = await session.scalar(
        select(AgentRunCheckpointRow.messages_json).where(AgentRunCheckpointRow.id == checkpoint_id)
    )
    return _recent_dialog(_json(messages, []))


def _recent_dialog(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    dialog = []
    for message in reversed(messages):
        role, content = message.get("role"), message.get("content")
        if (
            role in {"user", "assistant"}
            and not message.get("tool_calls")
            and not message.get("tool_call_id")
            and isinstance(content, str)
            and content.strip()
        ):
            dialog.append({"role": role, "content": content[:2000]})
            if len(dialog) == 4:
                break
    return list(reversed(dialog))


async def _run_detail(
    session: AsyncSession,
    saved: AgentRunRow,
    task: AgentRuntimeTaskRow,
    thread_key: str,
) -> dict[str, Any]:
    events = await session.scalars(
        select(AgentRunEventRow)
        .where(AgentRunEventRow.agent_run_id == saved.id)
        .order_by(AgentRunEventRow.sequence)
    )
    checkpoint = await session.scalar(
        select(AgentRunCheckpointRow)
        .where(AgentRunCheckpointRow.agent_run_id == saved.id)
        .order_by(AgentRunCheckpointRow.checkpoint_no.desc())
        .limit(1)
    )
    return {
        **saved.as_dict(),
        "task_id": task.id,
        "site_id": task.site_id,
        "conversation_id": task.conversation_id,
        "task_status": task.status,
        "thread_id": thread_key,
        "token_usage": _json(saved.token_usage_json, {}),
        "events": [
            {**event.as_dict(), "payload": _json(event.payload_json, {})} for event in events
        ],
        "checkpoint": {
            **checkpoint.as_dict(),
            "messages": _json(checkpoint.messages_json, []),
            "dispatch_state": _json(checkpoint.dispatch_state_json, {}),
            "pending_tool_calls": _json(checkpoint.pending_tool_calls_json, []),
            "token_usage": _json(checkpoint.token_usage_json, {}),
            "survival_summary": _json(checkpoint.survival_summary_json, {}),
            "available_tools": [],
        }
        if checkpoint is not None
        else None,
    }


def _restore_run(detail: dict[str, Any]) -> AgentRun:
    run_id = detail["id"]
    checkpoint = detail["checkpoint"]
    if checkpoint is None:
        raise KeyError(f"Run 没有 checkpoint: {run_id}")
    events = [
        AgentEvent(
            event_id=item["id"],
            run_id=run_id,
            type=item["event_type"],
            sequence=item["sequence"],
            created_at=datetime.fromisoformat(item["created_at"]),
            payload=item["payload"],
        )
        for item in detail["events"]
    ]
    human_input = checkpoint["survival_summary"].get("human_input")
    return AgentRun(
        id=run_id,
        messages=checkpoint["messages"],
        status=_run_status_from_db(detail["status"]),
        events=events,
        created_at=datetime.fromisoformat(detail["created_at"]),
        sequence=max((event.sequence for event in events), default=0),
        task_id=detail["task_id"],
        thread_id=detail["thread_id"],
        site_id=detail["site_id"],
        conversation_id=detail["conversation_id"],
        iteration=checkpoint["iteration"],
        tool_count=checkpoint["tool_count"],
        pending_tool_calls=checkpoint["pending_tool_calls"],
        token_usage=checkpoint["token_usage"],
        survival_summary=checkpoint["survival_summary"],
        dispatch_state=checkpoint["dispatch_state"],
        model_provider=detail.get("model_provider"),
        model_name=detail.get("model_name"),
        finished_at=datetime.fromisoformat(detail["completed_at"])
        if detail["completed_at"]
        else None,
        error=detail["error_message"],
        human_input=HumanInputState.model_validate(human_input) if human_input else None,
    )


def _json(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return fallback


def _status_group(status: str) -> str:
    if status == "PENDING":
        return "pending"
    if status in {"RUNNING", "RECOVERING", "RECOVERY_BLOCKED", "WAITING_FOR_INPUT"}:
        return "running"
    return "ended"


def _run_status_from_db(status: str) -> RunStatus:
    try:
        return RunStatus(status.lower())
    except ValueError:
        return RunStatus.FAILED
