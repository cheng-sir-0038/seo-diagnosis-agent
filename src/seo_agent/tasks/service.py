from __future__ import annotations

# ruff: noqa: E501
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.seo_agent.artifacts.renderers import MarkdownPrdRenderer, PdfPrdRenderer
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.notifications.service import NotificationService
from src.seo_agent.tasks.models import TaskPrdContent


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class TaskDeliveryService:
    def __init__(
        self,
        database: SeoAgentDatabase,
        *,
        artifact_root: Path = Path("data/artifacts"),
        notifications: NotificationService | None = None,
    ) -> None:
        self.database = database
        self.artifact_root = Path(artifact_root)
        self.notifications = notifications
        self.markdown_renderer = MarkdownPrdRenderer()
        self.pdf_renderer = PdfPrdRenderer()

    async def create_task(
        self,
        *,
        site_id: str,
        title: str,
        task_type: str,
        priority: str,
        opportunity_ids: list[str],
        owner_actor_id: str,
        created_by_run_id: str | None = None,
        deadline_at: str | None = None,
    ) -> dict[str, Any]:
        task_id = _id("task")
        now = _now()
        await self.database.execute(
            "INSERT INTO execution_tasks(id, site_id, title, task_type, status, priority, "
            "deadline_at, created_by_run_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'DRAFT', ?, ?, ?, ?, ?)",
            (
                task_id,
                site_id,
                title,
                task_type,
                priority,
                deadline_at,
                created_by_run_id,
                now,
                now,
            ),
        )
        for index, opportunity_id in enumerate(opportunity_ids):
            await self.database.execute(
                "INSERT INTO task_opportunities(id, task_id, opportunity_id, relation_type, "
                "is_primary, linked_by_run_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    _id("taskopp"),
                    task_id,
                    opportunity_id,
                    "PRIMARY_TARGET" if index == 0 else "SECONDARY_TARGET",
                    int(index == 0),
                    created_by_run_id,
                    now,
                ),
            )
        await self.database.execute(
            "INSERT INTO task_assignments(id, task_id, actor_id, assignment_role, status, "
            "assigned_at, created_at) VALUES (?, ?, ?, 'OWNER', 'ASSIGNED', ?, ?)",
            (_id("assignment"), task_id, owner_actor_id, now, now),
        )
        await self._record_task_status(task_id, None, "DRAFT", "任务已创建", owner_actor_id)
        return await self.get_task(task_id)

    async def get_task(self, task_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT * FROM execution_tasks WHERE id = ?", (task_id,)
        )
        if row is None:
            raise KeyError(f"任务不存在: {task_id}")
        return row

    async def list_tasks(
        self,
        site_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if status:
            return await self.database.fetch_all(
                "SELECT * FROM execution_tasks WHERE site_id = ? AND status = ? "
                "AND archived_at IS NULL ORDER BY created_at DESC LIMIT ?",
                (site_id, status, max(1, min(limit, 500))),
            )
        return await self.database.fetch_all(
            "SELECT * FROM execution_tasks WHERE site_id = ? AND archived_at IS NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (site_id, max(1, min(limit, 500))),
        )

    async def get_task_detail(self, task_id: str) -> dict[str, Any]:
        task = await self.get_task(task_id)
        task["opportunities"] = await self.database.fetch_all(
            "SELECT o.*, r.relation_type, r.is_primary FROM task_opportunities r "
            "JOIN opportunities o ON o.id = r.opportunity_id WHERE r.task_id = ? "
            "ORDER BY r.is_primary DESC, o.current_score DESC",
            (task_id,),
        )
        task["assignments"] = await self.database.fetch_all(
            "SELECT a.*, x.name, x.email, x.actor_type FROM task_assignments a "
            "JOIN actors x ON x.id = a.actor_id WHERE a.task_id = ? ORDER BY a.created_at",
            (task_id,),
        )
        task["prd_versions"] = await self.database.fetch_all(
            "SELECT * FROM task_prd_versions WHERE task_id = ? ORDER BY version_no DESC",
            (task_id,),
        )
        task["dispatches"] = await self.database.fetch_all(
            "SELECT * FROM task_dispatches WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        )
        deliveries = await self.database.fetch_all(
            "SELECT * FROM deliveries WHERE task_id = ? ORDER BY version_no DESC",
            (task_id,),
        )
        for delivery in deliveries:
            delivery["reviews"] = await self.database.fetch_all(
                "SELECT * FROM delivery_reviews WHERE delivery_id = ? ORDER BY created_at DESC",
                (delivery["id"],),
            )
        task["deliveries"] = deliveries
        task["publish_records"] = await self.database.fetch_all(
            "SELECT * FROM publish_records WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        )
        task["status_history"] = await self.database.fetch_all(
            "SELECT * FROM task_status_history WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        )
        return task

    async def create_prd_version(
        self,
        *,
        task_id: str,
        title: str,
        content: TaskPrdContent,
        created_by_actor_id: str,
        source_version_id: str | None = None,
        change_summary: str | None = None,
        generated_by_run_id: str | None = None,
        prompt_version_id: str | None = None,
        source_trace_id: str | None = None,
    ) -> dict[str, Any]:
        await self.get_task(task_id)
        current = await self.database.fetch_value(
            "SELECT COALESCE(MAX(version_no), 0) FROM task_prd_versions WHERE task_id = ?",
            (task_id,),
        )
        version_no = int(current or 0) + 1
        prd_id = _id("prd")
        markdown = self.markdown_renderer.render(title, content)
        await self.database.execute(
            "INSERT INTO task_prd_versions(id, task_id, version_no, status, title, "
            "structured_content_json, markdown_content, prompt_version_id, source_trace_id, "
            "generated_by_run_id, source_version_id, change_summary, created_by_actor_id, created_at) "
            "VALUES (?, ?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                prd_id,
                task_id,
                version_no,
                title,
                content.model_dump_json(),
                markdown,
                prompt_version_id,
                source_trace_id,
                generated_by_run_id,
                source_version_id,
                change_summary,
                created_by_actor_id,
                _now(),
            ),
        )
        await self.database.execute(
            "UPDATE execution_tasks SET current_prd_version_id = ?, updated_at = ? WHERE id = ?",
            (prd_id, _now(), task_id),
        )
        return await self.get_prd_version(prd_id)

    async def get_prd_version(self, prd_version_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT * FROM task_prd_versions WHERE id = ?", (prd_version_id,)
        )
        if row is None:
            raise KeyError(f"PRD 版本不存在: {prd_version_id}")
        return row

    async def render_prd_artifacts(self, prd_version_id: str) -> list[dict[str, Any]]:
        prd = await self.get_prd_version(prd_version_id)
        content = TaskPrdContent.model_validate_json(prd["structured_content_json"])
        task_dir = self.artifact_root / prd["task_id"] / f"prd-v{prd['version_no']}"
        task_dir.mkdir(parents=True, exist_ok=True)
        outputs: list[tuple[str, str, str, bytes]] = []
        markdown_bytes = prd["markdown_content"].encode("utf-8")
        outputs.append(("markdown", "prd.md", "text/markdown", markdown_bytes))
        outputs.append(
            ("pdf", "prd.pdf", "application/pdf", self.pdf_renderer.render(prd["title"], content))
        )
        created: list[dict[str, Any]] = []
        for renderer_key, filename, content_type, payload in outputs:
            checksum = hashlib.sha256(payload).hexdigest()
            path = task_dir / filename
            path.write_bytes(payload)
            existing = await self.database.fetch_one(
                "SELECT * FROM task_prd_artifacts WHERE prd_version_id = ? AND renderer_key = ? AND checksum = ?",
                (prd_version_id, renderer_key, checksum),
            )
            if existing is not None:
                created.append(existing)
                continue
            artifact_id = _id("artifact")
            now = _now()
            await self.database.execute(
                "INSERT INTO task_prd_artifacts(id, prd_version_id, renderer_key, file_name, file_path, "
                "content_type, checksum, generated_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact_id,
                    prd_version_id,
                    renderer_key,
                    filename,
                    str(path),
                    content_type,
                    checksum,
                    now,
                    now,
                ),
            )
            row = await self.database.fetch_one(
                "SELECT * FROM task_prd_artifacts WHERE id = ?", (artifact_id,)
            )
            assert row is not None
            created.append(row)
        return created

    async def dispatch_task(
        self,
        *,
        task_id: str,
        prd_version_id: str,
        recipient_actor_id: str,
        idempotency_key: str,
        dispatcher_adapter: str = "internal_human",
    ) -> dict[str, Any]:
        existing = await self.database.fetch_one(
            "SELECT * FROM task_dispatches WHERE idempotency_key = ?", (idempotency_key,)
        )
        if existing is not None:
            return existing
        task = await self.get_task(task_id)
        await self.get_prd_version(prd_version_id)
        dispatch_id = _id("dispatch")
        now = _now()
        await self.database.execute(
            "INSERT OR IGNORE INTO task_assignments(id, task_id, actor_id, assignment_role, status, "
            "assigned_at, created_at) VALUES (?, ?, ?, 'EXECUTOR', 'ASSIGNED', ?, ?)",
            (_id("assignment"), task_id, recipient_actor_id, now, now),
        )
        await self.database.execute(
            "INSERT INTO task_dispatches(id, task_id, prd_version_id, dispatcher_adapter, "
            "recipient_actor_id, status, idempotency_key, dispatched_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'DISPATCHED', ?, ?, ?)",
            (
                dispatch_id,
                task_id,
                prd_version_id,
                dispatcher_adapter,
                recipient_actor_id,
                idempotency_key,
                now,
                now,
            ),
        )
        await self._set_task_status(task_id, "DISPATCHED", "任务已派发", recipient_actor_id)
        if self.notifications is not None:
            await self.notifications.create_event(
                site_id=task["site_id"],
                event_key="task_progressed",
                business_object_type="execution_task",
                business_object_id=task_id,
                title=f"新任务：{task['title']}",
                content={"summary": "任务已经派发，请查看 PRD"},
                priority=task["priority"],
                idempotency_key=f"task-dispatch:{dispatch_id}",
            )
        row = await self.database.fetch_one(
            "SELECT * FROM task_dispatches WHERE id = ?", (dispatch_id,)
        )
        assert row is not None
        return row

    async def submit_delivery(
        self,
        *,
        task_id: str,
        prd_version_id: str,
        title: str,
        summary: str,
        structured_content: dict[str, Any],
        submitted_by_actor_id: str,
        parent_delivery_id: str | None = None,
    ) -> dict[str, Any]:
        current = await self.database.fetch_value(
            "SELECT COALESCE(MAX(version_no), 0) FROM deliveries WHERE task_id = ?", (task_id,)
        )
        version_no = int(current or 0) + 1
        delivery_id = _id("delivery")
        now = _now()
        await self.database.execute(
            "INSERT INTO deliveries(id, task_id, prd_version_id, version_no, parent_delivery_id, "
            "status, title, summary, structured_content_json, submitted_by_actor_id, submitted_at, "
            "created_at) VALUES (?, ?, ?, ?, ?, 'SUBMITTED', ?, ?, ?, ?, ?, ?)",
            (
                delivery_id,
                task_id,
                prd_version_id,
                version_no,
                parent_delivery_id,
                title,
                summary,
                json.dumps(structured_content, ensure_ascii=False),
                submitted_by_actor_id,
                now,
                now,
            ),
        )
        await self._set_task_status(task_id, "DELIVERED", "执行者已提交交付", submitted_by_actor_id)
        row = await self.database.fetch_one("SELECT * FROM deliveries WHERE id = ?", (delivery_id,))
        assert row is not None
        return row

    async def review_delivery(
        self,
        *,
        delivery_id: str,
        prd_version_id: str,
        reviewer_actor_id: str,
        status: str,
        score: int,
        passed_items: list[dict[str, Any]] | None = None,
        failed_items: list[dict[str, Any]] | None = None,
        missing_items: list[dict[str, Any]] | None = None,
        revision_instructions: list[str] | None = None,
        reviewed_by_run_id: str | None = None,
        prompt_version_id: str | None = None,
        source_trace_id: str | None = None,
    ) -> dict[str, Any]:
        delivery = await self.database.fetch_one(
            "SELECT * FROM deliveries WHERE id = ?", (delivery_id,)
        )
        if delivery is None:
            raise KeyError(f"交付不存在: {delivery_id}")
        review_id = _id("review")
        now = _now()
        await self.database.execute(
            "INSERT INTO delivery_reviews(id, delivery_id, prd_version_id, reviewer_type, "
            "reviewer_actor_id, status, score, passed_items_json, failed_items_json, "
            "missing_items_json, revision_instructions_json, prompt_version_id, source_trace_id, "
            "reviewed_by_run_id, reviewed_at, created_at) "
            "VALUES (?, ?, ?, 'SEO_AGENT', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                review_id,
                delivery_id,
                prd_version_id,
                reviewer_actor_id,
                status,
                score,
                json.dumps(passed_items or [], ensure_ascii=False),
                json.dumps(failed_items or [], ensure_ascii=False),
                json.dumps(missing_items or [], ensure_ascii=False),
                json.dumps(revision_instructions or [], ensure_ascii=False),
                prompt_version_id,
                source_trace_id,
                reviewed_by_run_id,
                now,
                now,
            ),
        )
        await self.database.execute(
            "UPDATE deliveries SET status = ? WHERE id = ?", (status, delivery_id)
        )
        task_status = "DELIVERY_ACCEPTED" if status == "ACCEPTED" else status
        await self._set_task_status(
            delivery["task_id"], task_status, "交付验收完成", reviewer_actor_id
        )
        row = await self.database.fetch_one(
            "SELECT * FROM delivery_reviews WHERE id = ?", (review_id,)
        )
        assert row is not None
        return row

    async def _set_task_status(
        self, task_id: str, status: str, reason: str, actor_id: str | None
    ) -> None:
        task = await self.get_task(task_id)
        now = _now()
        await self.database.execute(
            "UPDATE execution_tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, task_id),
        )
        await self._record_task_status(task_id, task["status"], status, reason, actor_id)

    async def _record_task_status(
        self,
        task_id: str,
        from_status: str | None,
        to_status: str,
        reason: str,
        actor_id: str | None,
    ) -> None:
        await self.database.execute(
            "INSERT INTO task_status_history(id, task_id, from_status, to_status, reason, "
            "changed_by_actor_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_id("taskstatus"), task_id, from_status, to_status, reason, actor_id, _now()),
        )
