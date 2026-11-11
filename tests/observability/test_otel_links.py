from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import aiosqlite
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.observability.client import ObservabilityClient
from src.observability.exporters import OpenTelemetryExporter
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind
from src.observability.storage import ObservabilityDatabase

TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID = re.compile(r"^[0-9a-f]{16}$")


@pytest.mark.asyncio
async def test_business_steps_export_evidence_and_receipt_on_the_same_trace(tmp_path: Path):
    memory = InMemorySpanExporter()
    database = ObservabilityDatabase(tmp_path / "observability.db")
    exporter = OpenTelemetryExporter(
        service_name="seo-agent", endpoint=None, span_exporter=memory, database=database
    )
    client = ObservabilityClient(database, project_key="seo-agent", exporters=[exporter])
    receipt = {"opportunity_ids": ["opportunity-1"], "workflow_status": "partial"}
    try:
        async with observed_operation(
            client, "seo.opportunity_discovery", kind=SpanKind.INTERNAL,
            input_data={"query": "企业名录"},
        ) as workflow:
            async with observed_operation(
                client, "freeze_opportunity_evidence", kind=SpanKind.INTERNAL,
                input_data={"scan_run_id": "scan-1", "api_key": "fake-step-secret"},
            ) as freeze:
                freeze.output_data = {"evidence_snapshot_ids": ["snapshot-1"]}
            workflow.output_data = receipt
        await exporter.force_flush()
        spans = memory.get_finished_spans()
        root = next(span for span in spans if span.parent is None)
        step = next(span for span in spans if span.name == "freeze_opportunity_evidence")
        parent = next(span for span in spans if span.context.span_id == step.parent.span_id)
        assert parent.parent.span_id == root.context.span_id
        assert step.context.trace_id == root.context.trace_id
        assert json.loads(step.attributes["langfuse.observation.input"])["scan_run_id"] == "scan-1"
        assert json.loads(step.attributes["langfuse.observation.output"]) == {
            "evidence_snapshot_ids": ["snapshot-1"]
        }
        assert json.loads(root.attributes["langfuse.observation.output"]) == receipt
        assert "fake-step-secret" not in str([dict(span.attributes) for span in spans])
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_otel_exports_correlated_model_and_tool_calls(tmp_path: Path) -> None:
    memory = InMemorySpanExporter()
    database = ObservabilityDatabase(tmp_path / "observability.db")
    await database.startup()
    exporter = OpenTelemetryExporter(
        service_name="seo-agent", endpoint=None, span_exporter=memory, database=database
    )
    client = ObservabilityClient(database, project_key="seo-agent", exporters=[exporter])
    try:
        async with client.trace("seo.chat", input_data={"message": "private-example"}) as trace:
            async with trace.run(trigger_type="test") as run:
                async with run.span("model.iteration.1", kind=SpanKind.MODEL) as model:
                    await model.model_call(
                        provider="deepseek", model="deepseek-v4-flash", operation="stream",
                        input_tokens=100, output_tokens=50, reasoning_tokens=20, cached_tokens=30,
                        total_tokens=150, latency_ms=125, time_to_first_token_ms=50,
                        input_summary={"text": "private-example"},
                    )
                async with run.span("inspect_page", kind=SpanKind.TOOL) as tool:
                    await tool.tool_call(
                        tool_call_id="call-1", tool_name="inspect_page", operation="query",
                        arguments={
                            "url": "https://private-example.test/", "force_refresh": True,
                            "include_performance": False, "strategy": "mobile",
                        },
                        result_summary={
                            "success": True,
                            "data": {"workflow_status": "partial", "provenance": {"cached": False}},
                        },
                        latency_ms=20,
                    )

        await exporter.force_flush()
        spans = memory.get_finished_spans()
        by_type = {
            span.attributes.get("agent.observability.entity_type"): span
            for span in spans
            if span.attributes.get("agent.observability.entity_type")
            in {"trace", "model_call", "tool_call"}
        }
        root, generation, tool_call = (by_type[k] for k in ("trace", "model_call", "tool_call"))
        assert generation.context.trace_id == root.context.trace_id == tool_call.context.trace_id
        parents = {span.context.span_id: span for span in spans}
        assert parents[generation.parent.span_id].parent.span_id == root.context.span_id
        assert parents[tool_call.parent.span_id].parent.span_id == root.context.span_id
        assert generation.attributes["langfuse.observation.type"] == "generation"
        assert generation.attributes["gen_ai.request.model"] == "deepseek-v4-flash"
        usage = json.loads(generation.attributes["langfuse.observation.usage_details"])
        assert usage == {"input": 70, "cache_read_input_tokens": 30, "output": 50, "total": 150}
        assert abs(generation.end_time - generation.start_time - 125_000_000) < 1_000
        first_token = datetime.fromisoformat(
            generation.attributes["langfuse.observation.completion_start_time"]
        )
        assert (
            abs(first_token.timestamp() * 1_000_000_000 - generation.start_time - 50_000_000)
            < 2_000
        )
        assert tool_call.attributes["langfuse.observation.type"] == "tool"
        assert tool_call.attributes["langfuse.observation.metadata.force_refresh"] is True
        assert tool_call.attributes["langfuse.observation.metadata.include_performance"] is False
        assert tool_call.attributes["langfuse.observation.metadata.strategy"] == "mobile"
        assert tool_call.attributes["langfuse.observation.metadata.workflow_status"] == "partial"
        assert tool_call.attributes["langfuse.observation.metadata.cached"] is False
        assert "private-example" in str([dict(span.attributes) for span in spans])
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_unexpected_tool_arguments_do_not_break_export(tmp_path: Path) -> None:
    memory = InMemorySpanExporter()
    database = ObservabilityDatabase(tmp_path / "observability.db")
    exporter = OpenTelemetryExporter(
        service_name="seo-agent", endpoint=None, span_exporter=memory, database=database
    )
    client = ObservabilityClient(database, project_key="seo-agent", exporters=[exporter])
    try:
        await client.startup()
        async with client.trace("seo.chat") as trace:
            async with trace.run(trigger_type="test") as run:
                async with run.span("tool", kind=SpanKind.TOOL) as tool:
                    await tool.tool_call(
                        tool_call_id="bad", tool_name="tool", operation="execute",
                        arguments={
                            "strategy": ["private"], "force_refresh": {"value": "x"},
                            "password": "fake-secret-value",
                        },
                        result_summary={"data": {"workflow_status": ["private"]}},
                    )
        calls = [span for span in memory.get_finished_spans() if span.name == "tool"]
        assert len(calls) == 2
        assert "private" in str([dict(span.attributes) for span in calls])
        assert "fake-secret-value" not in str([dict(span.attributes) for span in calls])
    finally:
        await client.shutdown()


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
                attributes={
                    "monitoring_plan_id": "plan-1", "check_type": "INDEXING",
                    "url": "https://private-example.test/",
                },
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
        assert "private-example" not in str(link.attributes)
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
