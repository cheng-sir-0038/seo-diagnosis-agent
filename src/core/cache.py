"""进程内 TTL 缓存，PostgreSQL 证据快照与 API 用量日志。"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from src.core.config import settings
from src.core.database import PostgresDatabase
from src.core.evidence_models import ApiUsage, EvidenceBase, EvidenceSnapshot


class CacheManager:
    def __init__(self, url: str | None = None, *, schema: str | None = None) -> None:
        self.database = PostgresDatabase(EvidenceBase.metadata, url, schema=schema)
        self._memory_cache: dict[str, tuple[float, Any]] = {}

    async def get(self, key: str) -> Any | None:
        cached = self._memory_cache.get(key)
        if cached is None:
            return None
        expires_at, value = cached
        if expires_at <= time.monotonic():
            self._memory_cache.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        ttl = ttl if ttl is not None else settings.cache_default_ttl
        self._memory_cache[key] = (time.monotonic() + max(0, ttl), value)

    async def invalidate(self, provider: str, capability: str | None = None) -> None:
        if capability is None:
            self._memory_cache.pop(provider, None)
        prefix = f"{provider}:" if capability is None else f"{provider}:{capability}:"
        for key in list(self._memory_cache):
            if key.startswith(prefix):
                self._memory_cache.pop(key)

    async def startup(self) -> None:
        await self.database.startup()

    async def shutdown(self) -> None:
        await self.database.shutdown()
        self._memory_cache.clear()

    async def save_snapshot(
        self, task_id: str, capability: str, data: dict, provider: str = "ahrefs",
    ) -> str:
        """冻结本次工具响应并返回引用 ID。调用方可以据此复查原始证据。"""
        payload = json.loads(json.dumps(data, ensure_ascii=False, default=str))
        meta = payload.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        evidence = payload.get("data")
        if isinstance(evidence, list):
            evidence = evidence[0] if evidence else None
        evidence = evidence if isinstance(evidence, dict) else {}
        snapshot_id = str(uuid.uuid4())
        snapshot = EvidenceSnapshot(
            id=snapshot_id, task_id=task_id, capability=capability, provider=provider,
            params_hash=meta.get("params_hash"),
            source=meta.get("source", evidence.get("source", provider)),
            fetched_at=evidence.get("fetched_at") or meta.get("fetched_at"),
            integrity=evidence.get("integrity") or meta.get("integrity"),
            is_estimated=bool(evidence.get("is_estimated", meta.get("is_estimated", False))),
            units_consumed=meta.get("units_consumed") or 0,
            data_json=payload, created_at=datetime.now(UTC),
        )
        async with self.database.orm_session(write=True) as session:
            session.add(snapshot)
        return snapshot_id

    async def get_snapshot(self, snapshot_id: str) -> dict | None:
        async with self.database.orm_session() as session:
            return await session.scalar(
                select(EvidenceSnapshot.data_json).where(EvidenceSnapshot.id == snapshot_id)
            )

    async def log_api_usage(
        self, provider: str, endpoint: str, units_consumed: int, success: bool,
    ) -> None:
        async with self.database.orm_session(write=True) as session:
            session.add(ApiUsage(
                provider=provider, endpoint=endpoint, units_consumed=units_consumed,
                success=success, created_at=datetime.now(UTC),
            ))


cache_manager = CacheManager()
