from __future__ import annotations

import re
from pathlib import Path

import aiosqlite
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.observability.client import ObservabilityClient
from src.observability.exporters import OpenTelemetryExporter
from src.observability.storage import ObservabilityDatabase

TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID = re.compile(r"^[0-9a-f]{16}$")


@pytest.mark.asyncio
async def test_trace_link_is_exported_as_real_otel_span_link(tmp_path: Path) -> None:
    memory = InMemorySpanExporter()
    exporter = OpenTelemetryExporter(
        service_name="seo-agent",
        endpoint=None,
        span_exporter=memory,
    )
    database = ObservabilityDatabase(tmp_path / "observability.db")
    await database.startup()
    client = ObservabilityClient(
        database,
        project_key="seo-agent",
        exporters=[exporter],
    )
    try:
        async with client.trace("seo.publish") as publish_trace:
            pass
        async with client.trace("seo.monitor.indexing") as monitoring_trace:
            await client.link_traces(
                source_trace_id=publish_trace.trace_id,
                target_trace_id=monitoring_trace.trace_id,
                relation_type="FOLLOW_UP",
                attributes={"monitoring_plan_id": "plan-1", "check_type": "INDEXING"},
            )

        await exporter.force_flush()
        spans = memory.get_finished_spans()
        trace_spans = {
            span.attributes["agent.internal_trace_id"]: span
            for span in spans
            if span.attributes.get("agent.observability.entity_type") == "trace"
        }
        assert set(trace_spans) == {publish_trace.trace_id, monitoring_trace.trace_id}
        assert not any(
            span.attributes.get("agent.observability.entity_type") == "trace_link" for span in spans
        )

        publish_row = await database.fetch_one(
            "SELECT * FROM obs_traces WHERE id = ?", (publish_trace.trace_id,)
        )
        monitoring_row = await database.fetch_one(
            "SELECT * FROM obs_traces WHERE id = ?", (monitoring_trace.trace_id,)
        )
        assert publish_row is not None and monitoring_row is not None

        publish_span = trace_spans[publish_trace.trace_id]
        monitoring_span = trace_spans[monitoring_trace.trace_id]
        assert f"{publish_span.context.trace_id:032x}" == publish_row["otel_trace_id"]
        assert f"{publish_span.context.span_id:016x}" == publish_row["otel_root_span_id"]
        assert f"{monitoring_span.context.trace_id:032x}" == monitoring_row["otel_trace_id"]
        assert f"{monitoring_span.context.span_id:016x}" == monitoring_row["otel_root_span_id"]
        assert monitoring_span.context.trace_id != publish_span.context.trace_id
        assert monitoring_span.parent is None
        assert len(monitoring_span.links) == 1
        link = monitoring_span.links[0]
        assert f"{link.context.trace_id:032x}" == publish_row["otel_trace_id"]
        assert f"{link.context.span_id:016x}" == publish_row["otel_root_span_id"]
        assert link.attributes["agent.link.relation_type"] == "FOLLOW_UP"
        assert link.attributes["agent.link.monitoring_plan_id"] == "plan-1"
        assert link.attributes["agent.link.check_type"] == "INDEXING"
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_existing_database_backfills_valid_stable_otel_ids(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    async with aiosqlite.connect(path) as connection:
        await connection.executescript(
            """
            CREATE TABLE obs_projects (
                id TEXT PRIMARY KEY,
                project_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                service_name TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE obs_traces (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                operation_name TEXT NOT NULL,
                session_id TEXT,
                user_id TEXT,
                status TEXT NOT NULL,
                input_json TEXT NOT NULL DEFAULT '{}',
                output_json TEXT NOT NULL DEFAULT '{}',
                error_code TEXT,
                error_message TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                duration_ms REAL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            INSERT INTO obs_projects(id, project_key, name, created_at, updated_at)
            VALUES ('project-1', 'seo-agent', 'SEO Agent', '2026-07-24', '2026-07-24');
            INSERT INTO obs_traces(
                id, project_id, operation_name, status, started_at
            ) VALUES ('trace-legacy', 'project-1', 'legacy.run', 'COMPLETED', '2026-07-24');
            """
        )
        await connection.commit()

    database = ObservabilityDatabase(path)
    await database.startup()
    try:
        first = await database.fetch_one(
            "SELECT otel_trace_id, otel_root_span_id FROM obs_traces WHERE id = 'trace-legacy'"
        )
        assert first is not None
        assert TRACE_ID.fullmatch(first["otel_trace_id"])
        assert SPAN_ID.fullmatch(first["otel_root_span_id"])
        assert int(first["otel_trace_id"], 16) != 0
        assert int(first["otel_root_span_id"], 16) != 0
    finally:
        await database.shutdown()

    restarted = ObservabilityDatabase(path)
    await restarted.startup()
    try:
        second = await restarted.fetch_one(
            "SELECT otel_trace_id, otel_root_span_id FROM obs_traces WHERE id = 'trace-legacy'"
        )
        assert second == first
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_completed_target_trace_rejects_late_otel_link(tmp_path: Path) -> None:
    database = ObservabilityDatabase(tmp_path / "observability.db")
    await database.startup()
    client = ObservabilityClient(database, project_key="seo-agent")
    try:
        async with client.trace("source") as source:
            pass
        async with client.trace("target") as target:
            pass
        with pytest.raises(ValueError, match="目标 Trace 已结束"):
            await client.link_traces(
                source_trace_id=source.trace_id,
                target_trace_id=target.trace_id,
                relation_type="FOLLOW_UP",
            )
    finally:
        await client.shutdown()
