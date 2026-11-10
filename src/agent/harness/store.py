from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.agent.harness.summary import build_survival_summary
from src.agent.models import AgentEvent, AgentRun, RunStatus
from src.seo_agent.db.database import SeoAgentDatabase


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


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
        site_id: str,
        conversation_id: str | None,
        title: str,
        user_intent: str,
        messages: list[dict[str, Any]],
        model_provider: str | None,
        model_name: str | None,
        prompt_version: str | None = None,
        prompt_hash: str | None = None,
        available_tools: list[str] | None = None,
    ) -> CreatedAgentTaskRun:
        await self._require_site(site_id)
        task_id = _id("agtask")
        thread_key = f"chat_{site_id}_{conversation_id}" if conversation_id else _id("chat")
        thread = await self.database.fetch_one(
            "SELECT * FROM workflow_threads WHERE thread_id = ?",
            (thread_key,),
        )
        now = _now()
        if thread is None:
            internal_thread_id = _id("wft")
            await self.database.execute(
                "INSERT INTO workflow_threads(id, site_id, thread_id, workflow_type, "
                "graph_version, "
                "policy_version, business_object_type, business_object_id, status, current_stage, "
                "created_at) VALUES (?, ?, ?, 'SEO_CHAT', 'chat-v1', 'chat-v1', "
                "'AGENT_RUNTIME_TASK', ?, 'ACTIVE', 'created', ?)",
                (internal_thread_id, site_id, thread_key, task_id, now),
            )
            thread = await self.database.fetch_one(
                "SELECT * FROM workflow_threads WHERE thread_id = ?", (thread_key,)
            )
        assert thread is not None
        await self.database.execute(
            "UPDATE workflow_threads SET status = 'ACTIVE', current_stage = 'created', "
            "completed_at = NULL, business_object_type = 'CONVERSATION', "
            "business_object_id = ? WHERE id = ?",
            (conversation_id or task_id, thread["id"]),
        )
        run_id = _id("run")
        await self.database.execute(
            "INSERT INTO agent_runs(id, workflow_thread_id, trigger_type, status, current_stage, "
            "model_provider, model_name, token_usage_json, started_at, created_at) "
            "VALUES (?, ?, 'CHAT', 'PENDING', 'created', ?, ?, '{}', ?, ?)",
            (run_id, thread["id"], model_provider, model_name, now, now),
        )
        await self.database.execute(
            "INSERT INTO agent_runtime_tasks(id, site_id, conversation_id, workflow_thread_id, "
            "title, user_intent, status, status_group, current_stage, current_run_id, "
            "survival_summary_json, prompt_version, prompt_hash, available_tools_json, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', 'pending', "
            "'created', ?, '{}', ?, ?, ?, ?, ?)",
            (
                task_id,
                site_id,
                conversation_id,
                thread["id"],
                title[:200] or "SEO 诊断任务",
                user_intent,
                run_id,
                prompt_version,
                prompt_hash,
                json.dumps(available_tools or [], ensure_ascii=False),
                now,
                now,
            ),
        )
        # Initial checkpoint makes a newly created task durable before scheduling.
        run = AgentRun(
            id=run_id,
            messages=[dict(item) for item in messages],
            task_id=task_id,
            thread_id=thread_key,
            site_id=site_id,
            conversation_id=conversation_id,
        )
        await self.save_checkpoint(
            run,
            messages=run.messages,
            iteration=0,
            tool_count=0,
            pending_tool_calls=[],
            stage="created",
            token_usage={},
        )
        return CreatedAgentTaskRun(task_id=task_id, thread_id=thread_key, run_id=run_id)

    async def persist_event(self, run: AgentRun, event: AgentEvent) -> None:
        # Token deltas remain in the live SSE stream. Durable storage keeps stable
        # boundaries only; message.completed and checkpoints preserve the answer.
        if event.type == "message.delta":
            return
        await self.database.execute(
            "INSERT OR IGNORE INTO agent_run_events(id, agent_run_id, sequence, event_type, "
            "payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                run.id,
                event.sequence,
                event.type,
                json.dumps(event.payload, ensure_ascii=False),
                event.created_at.isoformat(),
            ),
        )
        stage = str(event.payload.get("stage") or event.type)
        await self._update_status_from_run(run, stage=stage)

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
        summary = build_survival_summary(
            messages=messages,
            events=run.events,
            stage=stage,
            token_usage=token_usage,
        )
        checkpoint_no = int(
            await self.database.fetch_value(
                "SELECT COALESCE(MAX(checkpoint_no), 0) + 1 FROM agent_run_checkpoints "
                "WHERE agent_run_id = ?",
                (run.id,),
            )
            or 1
        )
        checkpoint_id = _id("agcp")
        now = _now()
        await self.database.execute(
            "INSERT INTO agent_run_checkpoints(id, agent_run_id, checkpoint_no, stage, iteration, "
            "tool_count, messages_json, pending_tool_calls_json, token_usage_json, "
            "survival_summary_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                checkpoint_id,
                run.id,
                checkpoint_no,
                stage,
                iteration,
                tool_count,
                json.dumps(messages, ensure_ascii=False),
                json.dumps(pending_tool_calls, ensure_ascii=False),
                json.dumps(token_usage, ensure_ascii=False),
                summary.model_dump_json(),
                now,
            ),
        )
        if run.task_id:
            await self.database.execute(
                "INSERT INTO agent_survival_summaries(id, agent_runtime_task_id, agent_run_id, "
                "checkpoint_id, summary_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    _id("agsum"),
                    run.task_id,
                    run.id,
                    checkpoint_id,
                    summary.model_dump_json(),
                    now,
                ),
            )
        await self.database.execute(
            "UPDATE workflow_threads SET current_stage = ?, current_checkpoint_id = ?, "
            "last_resumed_at = ? WHERE thread_id = ?",
            (stage, checkpoint_id, now, run.thread_id),
        )
        run.messages = [dict(item) for item in messages]
        run.iteration = iteration
        run.tool_count = tool_count
        run.pending_tool_calls = [dict(item) for item in pending_tool_calls]
        run.token_usage = dict(token_usage)
        run.survival_summary = summary.model_dump(mode="json")
        await self._update_status_from_run(run, stage=stage, summary=run.survival_summary)
        return run.survival_summary

    async def finalize_run(self, run: AgentRun) -> None:
        status = _db_run_status(run.status)
        group = _status_group(status)
        now = _now()
        completed_at = now if group == "ended" else None
        await self.database.execute(
            "UPDATE agent_runs SET status = ?, current_stage = ?, token_usage_json = ?, "
            "completed_at = ?, stop_reason = ?, error_message = ? WHERE id = ?",
            (
                status,
                run.status.value,
                json.dumps(run.token_usage, ensure_ascii=False),
                completed_at,
                run.status.value if group == "ended" else None,
                run.error,
                run.id,
            ),
        )
        if run.task_id:
            await self.database.execute(
                "UPDATE agent_runtime_tasks SET status = ?, status_group = ?, current_stage = ?, "
                "survival_summary_json = ?, error_message = ?, updated_at = ?, completed_at = ? "
                "WHERE id = ?",
                (
                    status,
                    group,
                    run.status.value,
                    json.dumps(run.survival_summary, ensure_ascii=False),
                    run.error,
                    now,
                    completed_at,
                    run.task_id,
                ),
            )
        if run.thread_id and group == "ended":
            await self.database.execute(
                "UPDATE workflow_threads SET current_stage = ?, status = 'COMPLETED', "
                "completed_at = ? WHERE thread_id = ?",
                (run.status.value, now, run.thread_id),
            )

    async def restore_run(
        self,
        run_id: str,
        *,
        mark_recovering: bool = True,
    ) -> AgentRun:
        detail = await self.get_run(run_id)
        checkpoint = detail.get("checkpoint")
        if not isinstance(checkpoint, dict):
            raise KeyError(f"Run 没有 checkpoint: {run_id}")
        events = [
            AgentEvent(
                event_id=item["id"],
                run_id=run_id,
                type=item["event_type"],
                sequence=int(item["sequence"]),
                created_at=datetime.fromisoformat(item["created_at"]),
                payload=item["payload"],
            )
            for item in detail["events"]
        ]
        restored_status = (
            RunStatus.RECOVERING if mark_recovering else _run_status_from_db(str(detail["status"]))
        )
        run = AgentRun(
            id=run_id,
            messages=checkpoint["messages"],
            status=restored_status,
            events=events,
            created_at=datetime.fromisoformat(detail["created_at"]),
            sequence=max((event.sequence for event in events), default=0),
            task_id=detail["task_id"],
            thread_id=detail["thread_id"],
            site_id=detail["site_id"],
            conversation_id=detail.get("conversation_id"),
            iteration=int(checkpoint["iteration"]),
            tool_count=int(checkpoint["tool_count"]),
            pending_tool_calls=checkpoint["pending_tool_calls"],
            token_usage=checkpoint["token_usage"],
            survival_summary=checkpoint["survival_summary"],
            finished_at=(
                datetime.fromisoformat(detail["completed_at"])
                if detail.get("completed_at")
                else None
            ),
            error=detail.get("error_message"),
        )
        if mark_recovering:
            await self.database.execute(
                "UPDATE agent_runs SET status = 'RECOVERING', "
                "current_stage = 'recovering' WHERE id = ?",
                (run_id,),
            )
            await self.database.execute(
                "UPDATE agent_runtime_tasks SET status = 'RECOVERING', "
                "status_group = 'running', current_stage = 'recovering', "
                "updated_at = ? WHERE id = ?",
                (_now(), run.task_id),
            )
        return run

    async def list_recoverable_runs(self) -> list[str]:
        rows = await self.database.fetch_all(
            "SELECT id FROM agent_runs WHERE trigger_type = 'CHAT' "
            "AND status IN ('PENDING', 'RUNNING', 'RECOVERING') ORDER BY created_at"
        )
        return [str(row["id"]) for row in rows]

    async def mark_recovery_blocked(self, run_id: str, message: str) -> None:
        now = _now()
        detail = await self.get_run(run_id)
        await self.database.execute(
            "UPDATE agent_runs SET status = 'RECOVERY_BLOCKED', "
            "current_stage = 'recovery_blocked', "
            "error_message = ? WHERE id = ?",
            (message[:1000], run_id),
        )
        await self.database.execute(
            "UPDATE agent_runtime_tasks SET status = 'RECOVERY_BLOCKED', status_group = 'running', "
            "current_stage = 'recovery_blocked', error_message = ?, updated_at = ? WHERE id = ?",
            (message[:1000], now, detail["task_id"]),
        )

    async def get_task(self, task_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT t.*, w.thread_id FROM agent_runtime_tasks t "
            "JOIN workflow_threads w ON w.id = t.workflow_thread_id WHERE t.id = ?",
            (task_id,),
        )
        if row is None:
            raise KeyError(f"Agent Task 不存在: {task_id}")
        return _decode_task(row)

    async def list_tasks(
        self,
        *,
        site_id: str | None = None,
        status_group: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if site_id:
            clauses.append("t.site_id = ?")
            params.append(site_id)
        if status_group:
            clauses.append("t.status_group = ?")
            params.append(status_group)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 500)))
        rows = await self.database.fetch_all(
            "SELECT t.*, w.thread_id FROM agent_runtime_tasks t "
            "JOIN workflow_threads w ON w.id = t.workflow_thread_id"
            + where
            + " ORDER BY t.updated_at DESC LIMIT ?",
            tuple(params),
        )
        return [_decode_task(row) for row in rows]

    async def get_run(self, run_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT r.*, t.id AS task_id, t.site_id, t.conversation_id, t.status AS task_status, "
            "w.thread_id FROM agent_runs r "
            "JOIN workflow_threads w ON w.id = r.workflow_thread_id "
            "JOIN agent_runtime_tasks t ON t.current_run_id = r.id WHERE r.id = ?",
            (run_id,),
        )
        if row is None:
            raise KeyError(f"Agent Run 不存在: {run_id}")
        events = await self.database.fetch_all(
            "SELECT * FROM agent_run_events WHERE agent_run_id = ? ORDER BY sequence", (run_id,)
        )
        checkpoint_row = await self.database.fetch_one(
            "SELECT * FROM agent_run_checkpoints WHERE agent_run_id = ? "
            "ORDER BY checkpoint_no DESC LIMIT 1",
            (run_id,),
        )
        return {
            **row,
            "token_usage": _json(row.get("token_usage_json"), {}),
            "events": [
                {**event, "payload": _json(event.get("payload_json"), {})} for event in events
            ],
            "checkpoint": _decode_checkpoint(checkpoint_row) if checkpoint_row else None,
        }

    async def _require_site(self, site_id: str) -> None:
        if await self.database.fetch_one("SELECT id FROM sites WHERE id = ?", (site_id,)) is None:
            raise KeyError(f"站点不存在: {site_id}")

    async def _update_status_from_run(
        self,
        run: AgentRun,
        *,
        stage: str,
        summary: dict[str, Any] | None = None,
    ) -> None:
        status = _db_run_status(run.status)
        group = _status_group(status)
        now = _now()
        await self.database.execute(
            "UPDATE agent_runs SET status = ?, current_stage = ?, "
            "token_usage_json = ? WHERE id = ?",
            (status, stage, json.dumps(run.token_usage, ensure_ascii=False), run.id),
        )
        if run.task_id:
            await self.database.execute(
                "UPDATE agent_runtime_tasks SET status = ?, status_group = ?, current_stage = ?, "
                "survival_summary_json = ?, updated_at = ? WHERE id = ?",
                (
                    status,
                    group,
                    stage,
                    json.dumps(summary or run.survival_summary, ensure_ascii=False),
                    now,
                    run.task_id,
                ),
            )


def _decode_task(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "survival_summary": _json(row.get("survival_summary_json"), {}),
        "available_tools": _json(row.get("available_tools_json"), []),
    }


def _decode_checkpoint(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "messages": _json(row.get("messages_json"), []),
        "pending_tool_calls": _json(row.get("pending_tool_calls_json"), []),
        "token_usage": _json(row.get("token_usage_json"), {}),
        "survival_summary": _json(row.get("survival_summary_json"), {}),
        "available_tools": _json(row.get("available_tools_json"), []),
    }


def _json(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return fallback


def _db_run_status(status: RunStatus) -> str:
    return status.value.upper()


def _status_group(status: str) -> str:
    if status in {"PENDING"}:
        return "pending"
    if status in {"RUNNING", "RECOVERING", "RECOVERY_BLOCKED"}:
        return "running"
    return "ended"


def _run_status_from_db(status: str) -> RunStatus:
    normalized = status.lower()
    try:
        return RunStatus(normalized)
    except ValueError:
        return RunStatus.FAILED
