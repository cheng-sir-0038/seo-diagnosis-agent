from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.db.models import ActorRow, AgentRunRow, SiteRow, WorkflowThreadRow, WorkRecordRow


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class SiteDomainConflictError(ValueError):
    pass


class SeoAgentSystemService:
    def __init__(
        self, database: SeoAgentDatabase, *, graph_version: str = "v1", policy_version: str = "v1"
    ) -> None:
        self.database = database
        self.graph_version = graph_version
        self.policy_version = policy_version

    async def create_site(
        self,
        *,
        name: str,
        domain: str,
        default_country: str,
        default_language: str,
        timezone: str,
    ) -> dict[str, Any]:
        now = _now()
        statement = (
            insert(SiteRow)
            .values(
                id=_id("site"), name=name, domain=domain.lower().strip(),
                default_country=default_country, default_language=default_language,
                timezone=timezone, status="ACTIVE", created_at=now, updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[SiteRow.domain])
            .returning(SiteRow)
        )
        async with self.database.orm_session(write=True) as session:
            site = await session.scalar(statement)
            if site is None:
                raise SiteDomainConflictError("站点域名已存在")
            return site.as_dict()

    async def get_site(self, site_id: str) -> dict[str, Any]:
        async with self.database.orm_session() as session:
            site = await session.get(SiteRow, site_id)
            if site is None:
                raise KeyError(f"站点不存在: {site_id}")
            return site.as_dict()

    async def list_sites(self) -> list[dict[str, Any]]:
        statement = select(SiteRow).where(SiteRow.archived_at.is_(None)).order_by(
            SiteRow.created_at.desc()
        )
        async with self.database.orm_session() as session:
            return [site.as_dict() for site in await session.scalars(statement)]

    async def find_site_by_host(self, host: str) -> dict[str, Any] | None:
        """优先使用精确主机，再匹配其同名的 www 站点。"""
        host = host.lower().rstrip(".")
        domain = host.removeprefix("www.")
        statement = (
            select(SiteRow)
            .where(SiteRow.domain.in_((domain, f"www.{domain}")))
            .order_by((SiteRow.domain == host).desc())
            .limit(1)
        )
        async with self.database.orm_session() as session:
            site = await session.scalar(statement)
            return site.as_dict() if site else None

    async def create_actor(
        self,
        *,
        actor_type: str,
        name: str,
        email: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        actor = ActorRow(
            id=_id("actor"), actor_type=actor_type, name=name, email=email,
            status="ACTIVE", created_at=now, updated_at=now,
        )
        async with self.database.orm_session(write=True) as session:
            session.add(actor)
            await session.flush()
            return actor.as_dict()

    async def list_actors(self, actor_type: str | None = None) -> list[dict[str, Any]]:
        statement = select(ActorRow).where(ActorRow.archived_at.is_(None))
        if actor_type:
            statement = statement.where(ActorRow.actor_type == actor_type)
        async with self.database.orm_session() as session:
            actors = await session.scalars(statement.order_by(ActorRow.created_at.desc()))
            return [actor.as_dict() for actor in actors]

    async def create_thread(
        self,
        *,
        site_id: str,
        workflow_type: str,
        business_object_type: str,
        business_object_id: str,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        async with self.database.orm_session(write=True) as session:
            if await session.get(SiteRow, site_id) is None:
                raise KeyError(f"站点不存在: {site_id}")
            thread = WorkflowThreadRow(
                id=_id("wft"), site_id=site_id, thread_id=thread_id or _id("thread"),
                workflow_type=workflow_type, graph_version=self.graph_version,
                policy_version=self.policy_version, business_object_type=business_object_type,
                business_object_id=business_object_id, status="ACTIVE",
                current_stage="created", created_at=_now(),
            )
            session.add(thread)
            await session.flush()
            return thread.as_dict()

    async def get_thread(self, thread_id: str) -> dict[str, Any]:
        async with self.database.orm_session() as session:
            thread = await session.scalar(
                select(WorkflowThreadRow).where(WorkflowThreadRow.thread_id == thread_id)
            )
            if thread is None:
                raise KeyError(f"Thread 不存在: {thread_id}")
            return thread.as_dict()

    async def start_run(
        self,
        *,
        thread_id: str,
        trigger_type: str,
        model_provider: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        async with self.database.orm_session(write=True) as session:
            internal_id = await session.scalar(
                select(WorkflowThreadRow.id).where(WorkflowThreadRow.thread_id == thread_id)
            )
            if internal_id is None:
                raise KeyError(f"Thread 不存在: {thread_id}")
            now = _now()
            run = AgentRunRow(
                id=_id("run"), workflow_thread_id=internal_id, trigger_type=trigger_type,
                status="RUNNING", current_stage="created", model_provider=model_provider,
                model_name=model_name, started_at=now, created_at=now,
            )
            session.add(run)
            await session.flush()
            return run.as_dict()

    async def append_work_record(
        self,
        *,
        site_id: str,
        title: str,
        summary: str,
        record_type: str,
        actor_id: str | None,
        idempotency_key: str,
        agent_run_id: str | None = None,
        workflow_thread_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _now()
        statement = (
            insert(WorkRecordRow)
            .values(
                id=_id("record"), site_id=site_id, workflow_thread_id=workflow_thread_id,
                agent_run_id=agent_run_id, record_type=record_type, title=title, summary=summary,
                actor_id=actor_id, payload_json=json.dumps(payload or {}, ensure_ascii=False),
                idempotency_key=idempotency_key, occurred_at=now, created_at=now,
            )
            .on_conflict_do_nothing(index_elements=[WorkRecordRow.idempotency_key])
            .returning(WorkRecordRow)
        )
        async with self.database.orm_session(write=True) as session:
            record = await session.scalar(statement)
            if record is None:
                record = await session.scalar(select(WorkRecordRow).where(
                    WorkRecordRow.idempotency_key == idempotency_key,
                    WorkRecordRow.site_id == site_id,
                ))
                if record is None:
                    raise ValueError("幂等键已用于其他站点的工作记录")
            return record.as_dict()

    async def list_work_records(self, site_id: str, limit: int = 100) -> list[dict[str, Any]]:
        statement = (
            select(WorkRecordRow).where(WorkRecordRow.site_id == site_id)
            .order_by(WorkRecordRow.occurred_at.desc()).limit(limit)
        )
        async with self.database.orm_session() as session:
            return [record.as_dict() for record in await session.scalars(statement)]
