from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from src.seo_agent.db.database import SeoAgentDatabase


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


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
        site_id = _id("site")
        now = _now()
        await self.database.execute(
            "INSERT INTO sites(id, name, domain, default_country, default_language, timezone, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
            (
                site_id,
                name,
                domain.lower().strip(),
                default_country,
                default_language,
                timezone,
                now,
                now,
            ),
        )
        return await self.get_site(site_id)

    async def get_site(self, site_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one("SELECT * FROM sites WHERE id = ?", (site_id,))
        if row is None:
            raise KeyError(f"站点不存在: {site_id}")
        return row

    async def list_sites(self) -> list[dict[str, Any]]:
        return await self.database.fetch_all(
            "SELECT * FROM sites WHERE archived_at IS NULL ORDER BY created_at DESC"
        )

    async def create_actor(
        self,
        *,
        actor_type: str,
        name: str,
        email: str | None = None,
    ) -> dict[str, Any]:
        actor_id = _id("actor")
        now = _now()
        await self.database.execute(
            "INSERT INTO actors(id, actor_type, name, email, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)",
            (actor_id, actor_type, name, email, now, now),
        )
        row = await self.database.fetch_one("SELECT * FROM actors WHERE id = ?", (actor_id,))
        assert row is not None
        return row

    async def list_actors(self, actor_type: str | None = None) -> list[dict[str, Any]]:
        if actor_type:
            return await self.database.fetch_all(
                "SELECT * FROM actors WHERE actor_type = ? AND archived_at IS NULL "
                "ORDER BY created_at DESC",
                (actor_type,),
            )
        return await self.database.fetch_all(
            "SELECT * FROM actors WHERE archived_at IS NULL ORDER BY created_at DESC"
        )

    async def create_thread(
        self,
        *,
        site_id: str,
        workflow_type: str,
        business_object_type: str,
        business_object_id: str,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        await self.get_site(site_id)
        internal_id = _id("wft")
        stable_thread_id = thread_id or _id("thread")
        now = _now()
        await self.database.execute(
            "INSERT INTO workflow_threads(id, site_id, thread_id, workflow_type, graph_version, "
            "policy_version, business_object_type, business_object_id, status, "
            "current_stage, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 'created', ?)",
            (
                internal_id,
                site_id,
                stable_thread_id,
                workflow_type,
                self.graph_version,
                self.policy_version,
                business_object_type,
                business_object_id,
                now,
            ),
        )
        return await self.get_thread(stable_thread_id)

    async def get_thread(self, thread_id: str) -> dict[str, Any]:
        row = await self.database.fetch_one(
            "SELECT * FROM workflow_threads WHERE thread_id = ?", (thread_id,)
        )
        if row is None:
            raise KeyError(f"Thread 不存在: {thread_id}")
        return row

    async def start_run(
        self,
        *,
        thread_id: str,
        trigger_type: str,
        model_provider: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        thread = await self.get_thread(thread_id)
        run_id = _id("run")
        now = _now()
        await self.database.execute(
            "INSERT INTO agent_runs(id, workflow_thread_id, trigger_type, status, current_stage, "
            "model_provider, model_name, started_at, created_at) "
            "VALUES (?, ?, ?, 'RUNNING', 'created', ?, ?, ?, ?)",
            (run_id, thread["id"], trigger_type, model_provider, model_name, now, now),
        )
        row = await self.database.fetch_one("SELECT * FROM agent_runs WHERE id = ?", (run_id,))
        assert row is not None
        return row

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
        existing = await self.database.fetch_one(
            "SELECT * FROM work_records WHERE idempotency_key = ?", (idempotency_key,)
        )
        if existing is not None:
            return existing
        record_id = _id("record")
        now = _now()
        await self.database.execute(
            "INSERT INTO work_records(id, site_id, workflow_thread_id, agent_run_id, record_type, "
            "title, summary, actor_id, payload_json, idempotency_key, occurred_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id,
                site_id,
                workflow_thread_id,
                agent_run_id,
                record_type,
                title,
                summary,
                actor_id,
                json.dumps(payload or {}, ensure_ascii=False),
                idempotency_key,
                now,
                now,
            ),
        )
        row = await self.database.fetch_one("SELECT * FROM work_records WHERE id = ?", (record_id,))
        assert row is not None
        return row

    async def list_work_records(self, site_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return await self.database.fetch_all(
            "SELECT * FROM work_records WHERE site_id = ? ORDER BY occurred_at DESC LIMIT ?",
            (site_id, limit),
        )
