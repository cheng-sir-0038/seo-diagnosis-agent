"""
本地 TTL 缓存与 SQLite 证据快照

职责:
  1. 进程内 TTL 缓存，避免相同进程内查询重复消耗额度。
     Key 格式: ``provider:capability:hash(params)``
     TTL: 默认 300 秒，可通过 settings.cache_default_ttl 配置。

  2. 预留 SQLite 快照接口，目标是冻结任务结果并支持历史趋势对比。
     表结构:
       - evidence_snapshots: 证据快照主表
       - api_usage_log: API 调用日志（用于用量审计）

SQLite 快照和用量日志持久化；Redis 连接暂不启用。::

    from src.core.cache import CacheManager
    cache = CacheManager()

    # 热缓存
    cached = await cache.get("ahrefs:search_metrics:{hash}")
    await cache.set("ahrefs:search_metrics:{hash}", data, ttl=300)

    # 快照
    await cache.save_snapshot(task_id="t_001", capability="search_metrics", data=...)
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import redis.asyncio as aioredis
from loguru import logger

from src.core.config import settings


class CacheManager:
    """缓存管理器。

    提供进程内 TTL 缓存和 SQLite 快照持久化。

    Redis 客户端依赖保留，但本轮不建立外部 Redis 连接。

    Attributes:
        redis: Redis 异步客户端实例。
        snapshot_dir: SQLite 数据库目录。
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._redis: aioredis.Redis | None = None
        self._db_path = Path(db_path or settings.sqlite_path)
        self._db: aiosqlite.Connection | None = None
        self._memory_cache: dict[str, tuple[float, Any]] = {}

    # ============================================================
    # Redis 热缓存（伪代码）
    # ============================================================

    async def _ensure_redis(self) -> aioredis.Redis:
        """延迟初始化 Redis 连接。

        伪代码阶段返回内存 mock。
        真实实现::

            if self._redis is None:
                self._redis = await aioredis.from_url(
                    settings.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                )
            return self._redis
        """
        # PSEUDO: 后续接入真实 Redis 时替换
        if self._redis is None:
            logger.debug("Redis 连接（伪代码阶段使用内存模拟）")
        return self._redis  # type: ignore[return-value]

    def _build_cache_key(self, provider: str, capability: str, params: dict) -> str:
        """构建缓存 Key。

        规则: ``{provider}:{capability}:{params_hash}``
        params 按字母序排序后取 SHA256 前 16 位，确保相同参数命中同一缓存。

        Args:
            provider: 数据源标识（"ahrefs"）。
            capability: 业务能力标识（"search_metrics"）。
            params: 查询参数字典。

        Returns:
            缓存 key 字符串。
        """
        canonical = json.dumps(params, sort_keys=True, default=str)
        param_hash = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        return f"{provider}:{capability}:{param_hash}"

    async def get(self, key: str) -> Any | None:
        """从 Redis 读取缓存。

        Args:
            key: 缓存 key。

        Returns:
            缓存数据（通常是 dict），未命中返回 None。
        """
        cached = self._memory_cache.get(key)
        if cached is None:
            return None
        expires_at, value = cached
        if expires_at <= time.monotonic():
            self._memory_cache.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """写入 Redis 缓存。

        Args:
            key: 缓存 key。
            value: 缓存数据（将 JSON 序列化）。
            ttl: 过期时间秒数，默认使用 settings.cache_default_ttl。
        """
        ttl = ttl if ttl is not None else settings.cache_default_ttl
        self._memory_cache[key] = (time.monotonic() + max(0, ttl), value)

    async def invalidate(self, provider: str, capability: str | None = None) -> None:
        """批量失效缓存。

        Args:
            provider: 数据源标识。
            capability: 如果指定，只失效该 capability 下的缓存；否则失效整个 provider。
        """
        if capability is None and provider in self._memory_cache:
            self._memory_cache.pop(provider, None)
            return
        prefix = f"{provider}:{capability or ''}:"
        for key in list(self._memory_cache):
            if key.startswith(prefix):
                self._memory_cache.pop(key, None)

    # ============================================================
    # SQLite 证据快照（伪代码）
    # ============================================================

    async def save_snapshot(
        self,
        task_id: str,
        capability: str,
        data: dict,
        provider: str = "ahrefs",
    ) -> str:
        """保存查询结果快照到 SQLite。

        用于任务结果冻结——后续新数据不回写旧结论。

        Args:
            task_id: 任务 ID。
            capability: 业务能力标识。
            data: 查询结果数据。
            provider: 数据源标识。

        Returns:
            快照 ID（UUID）。
        """
        if self._db is None:
            raise RuntimeError("CacheManager 未启动")

        snapshot_id = str(uuid.uuid4())
        meta = data.get("meta", {}) if isinstance(data, dict) else {}
        evidence = data.get("data") if isinstance(data, dict) else None
        first_evidence = evidence[0] if isinstance(evidence, list) and evidence else evidence
        first_evidence = first_evidence if isinstance(first_evidence, dict) else {}
        created_at = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO evidence_snapshots
               (id, task_id, provider, capability, params_hash, source,
                fetched_at, integrity, is_estimated, units_consumed,
                data_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                task_id,
                provider,
                capability,
                meta.get("params_hash"),
                meta.get("source", first_evidence.get("source", provider)),
                first_evidence.get("fetched_at"),
                first_evidence.get("integrity"),
                int(bool(first_evidence.get("is_estimated", False))),
                meta.get("units_consumed", 0),
                json.dumps(data, ensure_ascii=False, default=str),
                created_at,
            ),
        )
        await self._db.commit()
        return snapshot_id

    async def get_snapshot(self, snapshot_id: str) -> dict | None:
        """读取历史快照。

        Args:
            snapshot_id: 快照 ID。

        Returns:
            快照数据，未找到返回 None。
        """
        if self._db is None:
            raise RuntimeError("CacheManager 未启动")
        async with self._db.execute(
            "SELECT data_json FROM evidence_snapshots WHERE id = ?", (snapshot_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return json.loads(row[0]) if row else None

    async def log_api_usage(
        self,
        provider: str,
        endpoint: str,
        units_consumed: int,
        success: bool,
    ) -> None:
        """记录 API 调用日志（用量审计）。

        Args:
            provider: 数据源标识。
            endpoint: 调用的 API 端点。
            units_consumed: 估计消耗的 API Units。
            success: 调用是否成功。
        """
        if self._db is None:
            raise RuntimeError("CacheManager 未启动")
        await self._db.execute(
            """INSERT INTO api_usage_log
               (provider, endpoint, units_consumed, success, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                provider,
                endpoint,
                units_consumed,
                int(success),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await self._db.commit()

    # ============================================================
    # 生命周期
    # ============================================================

    async def startup(self) -> None:
        """初始化缓存连接和 SQLite 表结构。

        在 FastAPI startup 事件中调用。
        """
        if self._db is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS evidence_snapshots (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                capability TEXT NOT NULL,
                params_hash TEXT,
                source TEXT,
                fetched_at TEXT,
                integrity TEXT,
                is_estimated INTEGER NOT NULL DEFAULT 0,
                units_consumed INTEGER NOT NULL DEFAULT 0,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_evidence_task ON evidence_snapshots(task_id);
            CREATE INDEX IF NOT EXISTS idx_evidence_lookup
                ON evidence_snapshots(provider, capability, params_hash);
            CREATE TABLE IF NOT EXISTS api_usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                units_consumed INTEGER NOT NULL DEFAULT 0,
                success INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        await self._db.commit()
        logger.info(f"CacheManager 初始化完成 | sqlite={self._db_path}")

    async def shutdown(self) -> None:
        """关闭缓存连接。

        在 FastAPI shutdown 事件中调用。
        """
        if self._db is not None:
            await self._db.close()
            self._db = None
        self._memory_cache.clear()
        logger.info("CacheManager 已关闭")


# ============================================================
# 全局实例
# ============================================================

cache_manager = CacheManager()
