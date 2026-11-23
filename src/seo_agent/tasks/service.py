from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Table, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.seo_agent.artifacts.renderers import MarkdownPrdRenderer, PdfPrdRenderer
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import TABLES
from src.seo_agent.notifications.service import NotificationService
from src.seo_agent.tasks.models import TaskPrdContent

tasks = TABLES["execution_tasks"]
opportunities = TABLES["opportunities"]
actions = TABLES["approval_actions"]
links = TABLES["task_opportunities"]
assignments = TABLES["task_assignments"]
actors = TABLES["actors"]
versions = TABLES["task_prd_versions"]
dispatches = TABLES["task_dispatches"]
deliveries = TABLES["deliveries"]
reviews = TABLES["delivery_reviews"]
artifacts = TABLES["task_prd_artifacts"]
published = TABLES["publish_records"]
status_history = TABLES["task_status_history"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


async def _record(
    db: SeoAgentDatabase, table: Table, object_id: str, label: str, *, lock: bool = False,
) -> dict[str, Any]:
    query = select(table).where(table.c.id == object_id)
    if lock:
        query = query.with_for_update()
    row = await db.fetch_one(query)
    if row is None:
        raise KeyError(f"{label}不存在: {object_id}")
    return row


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
        if not opportunity_ids:
            raise ValueError("至少选择一个机会")
        if len(set(opportunity_ids)) != len(opportunity_ids):
            raise ValueError("不能重复选择同一个机会")
        async with self.database.transaction() as tx:
            await self.validate_opportunities(
                site_id=site_id, opportunity_ids=opportunity_ids, database=tx,
            )
            now = _now()
            task_id = _id("task")
            await tx.execute(insert(tasks).values(
                id=task_id, site_id=site_id, title=title, task_type=task_type, status="DRAFT",
                priority=priority, deadline_at=deadline_at, created_by_run_id=created_by_run_id,
                created_at=now, updated_at=now,
            ))
            await tx.execute(insert(links).values([
                dict(id=_id("taskopp"), task_id=task_id, opportunity_id=opportunity_id,
                     relation_type="PRIMARY_TARGET" if i == 0 else "SECONDARY_TARGET",
                     is_primary=int(i == 0), linked_by_run_id=created_by_run_id, created_at=now)
                for i, opportunity_id in enumerate(opportunity_ids)
            ]))
            await tx.execute(insert(assignments).values(
                id=_id("assignment"), task_id=task_id, actor_id=owner_actor_id,
                assignment_role="OWNER", status="ASSIGNED", assigned_at=now, created_at=now,
            ))
            await tx.execute(insert(status_history).values(
                id=_id("taskstatus"), task_id=task_id, from_status=None, to_status="DRAFT",
                reason="任务已创建", changed_by_actor_id=owner_actor_id, created_at=now,
            ))
            return await _record(tx, tasks, task_id, "任务")

    async def validate_opportunities(
        self, *, site_id: str, opportunity_ids: list[str], database: SeoAgentDatabase | None = None,
    ) -> list[dict[str, Any]]:
        if not opportunity_ids or len(set(opportunity_ids)) != len(opportunity_ids):
            raise ValueError("必须选择不重复的机会")
        db = database or self.database
        # Lock parents in the same order even if two callers select the opportunities in reverse.
        query = select(opportunities).where(
            opportunities.c.id.in_(opportunity_ids), opportunities.c.site_id == site_id,
            opportunities.c.archived_at.is_(None),
        ).order_by(opportunities.c.id)
        if db.in_transaction:
            query = query.with_for_update()
        records = {row["id"]: row for row in await db.fetch_all(query)}
        if len(records) != len(opportunity_ids):
            raise KeyError("机会不存在或不属于当前站点")
        result = []
        for opportunity_id in opportunity_ids:
            latest = select(actions.c.id, actions.c.status).where(
                actions.c.site_id == site_id, actions.c.target_type == "opportunity",
                actions.c.target_id == opportunity_id,
                actions.c.action_type == "ADD_OPPORTUNITY_TO_PRODUCTION",
                actions.c.archived_at.is_(None),
            ).order_by(actions.c.requested_at.desc(), actions.c.id.desc()).limit(1)
            if db.in_transaction:
                latest = latest.with_for_update()
            action = await db.fetch_one(latest)
            row = records[opportunity_id]
            if (row["status"] != "APPROVED" or action is None
                    or action["status"] not in {"APPROVED", "EDITED_AND_APPROVED"}):
                raise ValueError("关联机会尚未通过审批")
            result.append({**row, "approval_action_id": action["id"],
                           "approval_status": action["status"]})
        return result

    async def get_task(self, task_id: str) -> dict[str, Any]:
        return await _record(self.database, tasks, task_id, "任务")

    async def list_tasks(
        self, site_id: str, *, status: str | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = select(tasks).where(tasks.c.site_id == site_id, tasks.c.archived_at.is_(None))
        if status:
            query = query.where(tasks.c.status == status)
        return await self.database.fetch_all(query.order_by(tasks.c.created_at.desc())
                                             .limit(max(1, min(limit, 500))))

    async def get_task_detail(self, task_id: str) -> dict[str, Any]:
        task = await self.get_task(task_id)
        task["opportunities"] = await self.database.fetch_all(
            select(opportunities, links.c.relation_type, links.c.is_primary)
            .join(links, opportunities.c.id == links.c.opportunity_id)
            .where(links.c.task_id == task_id)
            .order_by(links.c.is_primary.desc(), opportunities.c.current_score.desc())
        )
        task["assignments"] = await self.database.fetch_all(
            select(assignments, actors.c.name, actors.c.email, actors.c.actor_type)
            .join(actors, actors.c.id == assignments.c.actor_id)
            .where(assignments.c.task_id == task_id).order_by(assignments.c.created_at)
        )
        for key, table, order in (
            ("prd_versions", versions, versions.c.version_no.desc()),
            ("dispatches", dispatches, dispatches.c.created_at.desc()),
            ("deliveries", deliveries, deliveries.c.version_no.desc()),
            ("publish_records", published, published.c.created_at.desc()),
            ("status_history", status_history, status_history.c.created_at),
        ):
            task[key] = await self.database.fetch_all(
                select(table).where(table.c.task_id == task_id).order_by(order)
            )
        by_delivery = {item["id"]: item for item in task["deliveries"]}
        for item in by_delivery.values():
            item["reviews"] = []
        if by_delivery:
            for review in await self.database.fetch_all(select(reviews).where(
                reviews.c.delivery_id.in_(by_delivery.keys()),
            ).order_by(reviews.c.created_at.desc())):
                by_delivery[review["delivery_id"]]["reviews"].append(review)
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
        markdown = self.markdown_renderer.render(title, content)
        async with self.database.transaction() as tx:
            await _record(tx, tasks, task_id, "任务", lock=True)
            current = await tx.fetch_value(select(func.max(versions.c.version_no)).where(
                versions.c.task_id == task_id,
            ))
            prd_id = _id("prd")
            now = _now()
            await tx.execute(insert(versions).values(
                id=prd_id, task_id=task_id, version_no=(current or 0) + 1, status="DRAFT",
                title=title, structured_content_json=content.model_dump_json(),
                markdown_content=markdown, prompt_version_id=prompt_version_id,
                source_trace_id=source_trace_id, generated_by_run_id=generated_by_run_id,
                source_version_id=source_version_id, change_summary=change_summary,
                created_by_actor_id=created_by_actor_id, created_at=now,
            ))
            await tx.execute(update(tasks).where(tasks.c.id == task_id).values(
                current_prd_version_id=prd_id, updated_at=now,
            ))
            return await _record(tx, versions, prd_id, "PRD 版本")

    async def get_prd_version(self, prd_version_id: str) -> dict[str, Any]:
        return await _record(self.database, versions, prd_version_id, "PRD 版本")

    async def render_prd_artifacts(self, prd_version_id: str) -> list[dict[str, Any]]:
        async with self.database.transaction() as tx:
            prd = await _record(tx, versions, prd_version_id, "PRD 版本", lock=True)
            content = TaskPrdContent.model_validate_json(prd["structured_content_json"])
            task_dir = self.artifact_root / prd["task_id"] / f"prd-v{prd['version_no']}"
            task_dir.mkdir(parents=True, exist_ok=True)
            created = []
            for key, name, media_type in (
                ("markdown", "prd.md", "text/markdown"), ("pdf", "prd.pdf", "application/pdf"),
            ):
                path = task_dir / name
                matching = select(artifacts).where(
                    artifacts.c.prd_version_id == prd_version_id, artifacts.c.renderer_key == key,
                )
                if path.is_file():
                    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
                    saved = await tx.fetch_one(matching.where(artifacts.c.checksum == checksum))
                    if saved is not None:
                        created.append(saved)
                        continue
                data = (prd["markdown_content"].encode() if key == "markdown" else
                        self.pdf_renderer.render(prd["title"], content))
                checksum = hashlib.sha256(data).hexdigest()
                tmp = path.with_name(f".{name}.{uuid.uuid4().hex}")
                try:
                    tmp.write_bytes(data)
                    tmp.replace(path)
                finally:
                    tmp.unlink(missing_ok=True)
                now = _now()
                await tx.execute(pg_insert(artifacts).values(
                    id=_id("artifact"), prd_version_id=prd_version_id, renderer_key=key,
                    file_name=name, file_path=str(path), content_type=media_type, checksum=checksum,
                    generated_at=now, created_at=now,
                ).on_conflict_do_nothing(index_elements=[
                    artifacts.c.prd_version_id, artifacts.c.renderer_key, artifacts.c.checksum,
                ]))
                row = await tx.fetch_one(matching.where(artifacts.c.checksum == checksum))
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
        async with self.database.transaction() as tx:
            task = await _record(tx, tasks, task_id, "任务", lock=True)
            prd = await _record(tx, versions, prd_version_id, "PRD 版本")
            if prd["task_id"] != task_id:
                raise ValueError("PRD 版本不属于当前任务")
            now = _now()
            dispatch_id = _id("dispatch")
            await tx.execute(pg_insert(dispatches).values(
                id=dispatch_id, task_id=task_id, prd_version_id=prd_version_id,
                dispatcher_adapter=dispatcher_adapter, recipient_actor_id=recipient_actor_id,
                status="DISPATCHED", idempotency_key=idempotency_key, dispatched_at=now,
                created_at=now,
            ).on_conflict_do_nothing(index_elements=[dispatches.c.idempotency_key]))
            row = await tx.fetch_one(select(dispatches).where(
                dispatches.c.idempotency_key == idempotency_key,
            ))
            assert row is not None
            if row["id"] != dispatch_id:
                if (row["task_id"], row["prd_version_id"], row["recipient_actor_id"],
                    row["dispatcher_adapter"]) != (
                    task_id, prd_version_id, recipient_actor_id, dispatcher_adapter,
                ):
                    raise ValueError("幂等键已用于不同的任务派发")
                return row
            await tx.execute(pg_insert(assignments).values(
                id=_id("assignment"), task_id=task_id, actor_id=recipient_actor_id,
                assignment_role="EXECUTOR", status="ASSIGNED", assigned_at=now, created_at=now,
            ).on_conflict_do_nothing(index_elements=[
                assignments.c.task_id, assignments.c.actor_id, assignments.c.assignment_role,
            ]))
            await self._set_task_status(tx, task, "DISPATCHED", "任务已派发", recipient_actor_id)
            if self.notifications is not None:
                await NotificationService(tx, self.notifications.provider).create_event(
                    site_id=task["site_id"], event_key="task_progressed",
                    business_object_type="execution_task", business_object_id=task_id,
                    title=f"新任务：{task['title']}",
                    content={"summary": "任务已经派发，请查看 PRD"},
                    priority=task["priority"], idempotency_key=f"task-dispatch:{dispatch_id}",
                )
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
        async with self.database.transaction() as tx:
            task = await _record(tx, tasks, task_id, "任务", lock=True)
            prd = await _record(tx, versions, prd_version_id, "PRD 版本")
            if prd["task_id"] != task_id:
                raise ValueError("PRD 版本不属于当前任务")
            current = await tx.fetch_value(select(func.max(deliveries.c.version_no)).where(
                deliveries.c.task_id == task_id,
            ))
            delivery_id = _id("delivery")
            now = _now()
            await tx.execute(insert(deliveries).values(
                id=delivery_id, task_id=task_id, prd_version_id=prd_version_id,
                version_no=(current or 0) + 1, parent_delivery_id=parent_delivery_id,
                status="SUBMITTED", title=title, summary=summary,
                structured_content_json=json.dumps(structured_content, ensure_ascii=False),
                submitted_by_actor_id=submitted_by_actor_id, submitted_at=now, created_at=now,
            ))
            await self._set_task_status(
                tx, task, "DELIVERED", "执行者已提交交付", submitted_by_actor_id,
            )
            return await _record(tx, deliveries, delivery_id, "交付")

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
        async with self.database.transaction() as tx:
            delivery = await _record(tx, deliveries, delivery_id, "交付")
            if prd_version_id != delivery["prd_version_id"]:
                raise ValueError("PRD 版本与本次交付不匹配")
            task = await _record(tx, tasks, delivery["task_id"], "任务", lock=True)
            if status == "ACCEPTED" and (failed_items or missing_items):
                status = "REVISION_REQUIRED"
            review_id = _id("review")
            now = _now()
            await tx.execute(insert(reviews).values(
                id=review_id, delivery_id=delivery_id, prd_version_id=prd_version_id,
                reviewer_type="SEO_AGENT", reviewer_actor_id=reviewer_actor_id, status=status,
                score=score, passed_items_json=json.dumps(passed_items or [], ensure_ascii=False),
                failed_items_json=json.dumps(failed_items or [], ensure_ascii=False),
                missing_items_json=json.dumps(missing_items or [], ensure_ascii=False),
                revision_instructions_json=json.dumps(
                    revision_instructions or [], ensure_ascii=False,
                ),
                reviewed_by_run_id=reviewed_by_run_id, prompt_version_id=prompt_version_id,
                source_trace_id=source_trace_id, reviewed_at=now, created_at=now,
            ))
            await tx.execute(
                update(deliveries).where(deliveries.c.id == delivery_id).values(status=status)
            )
            await self._set_task_status(
                tx, task, "DELIVERY_ACCEPTED" if status == "ACCEPTED" else status,
                "交付验收完成", reviewer_actor_id,
            )
            return await _record(tx, reviews, review_id, "交付审核")

    @staticmethod
    async def _set_task_status(
        tx: SeoAgentDatabase, task: dict, status: str, reason: str, actor_id: str | None,
    ) -> None:
        now = _now()
        await tx.execute(update(tasks).where(tasks.c.id == task["id"]).values(
            status=status, updated_at=now,
        ))
        await tx.execute(insert(status_history).values(
            id=_id("taskstatus"), task_id=task["id"], from_status=task["status"], to_status=status,
            reason=reason, changed_by_actor_id=actor_id, created_at=now,
        ))
