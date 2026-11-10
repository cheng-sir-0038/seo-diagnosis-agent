from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.observability.client import ObservabilityClient
from src.observability.exporters import JsonlExporter, OpenTelemetryExporter
from src.observability.models import IssueStatus, SpanKind
from src.observability.storage import ObservabilityDatabase


@pytest.mark.asyncio
async def test_trace_run_span_model_tool_event_and_metrics_are_persisted(tmp_path: Path) -> None:
    database = ObservabilityDatabase(tmp_path / "observability.db")
    await database.startup()
    try:
        client = ObservabilityClient(
            database, project_key="seo-agent", service_name="seo-agent-api"
        )
        async with client.trace("seo.scan", input_data={"domain": "example.com"}) as trace:
            async with trace.run(trigger_type="scheduled", agent_name="seo-specialist") as run:
                async with run.span("collect-keywords", kind=SpanKind.TOOL) as span:
                    await span.event("tool.started", {"tool": "dataforseo.organic_keywords"})
                    await span.model_call(
                        provider="deepseek",
                        model="deepseek-chat",
                        operation="classify",
                        prompt_key="seo.opportunity.classify",
                        prompt_version="v1",
                        input_tokens=100,
                        output_tokens=25,
                        latency_ms=450,
                        structured_output_valid=True,
                    )
                    await span.tool_call(
                        tool_call_id="call-1",
                        tool_name="dataforseo.organic_keywords",
                        adapter_key="dataforseo",
                        operation="query",
                        arguments={"domain": "example.com", "api_key": "secret"},
                        result_summary={"count": 10},
                        latency_ms=120,
                    )
                    await span.metric("tool.result_count", 10, unit="items")
        counts = await database.table_counts()
        assert counts["obs_traces"] == 1
        assert counts["obs_runs"] == 1
        assert counts["obs_spans"] == 1
        assert counts["obs_events"] >= 4
        assert counts["obs_model_calls"] == 1
        assert counts["obs_tool_calls"] == 1
        assert counts["obs_metric_points"] >= 3
        tool = await database.fetch_one("SELECT * FROM obs_tool_calls")
        assert tool is not None
        assert "secret" not in tool["arguments_json"]
        assert "[REDACTED]" in tool["arguments_json"]
        saved_trace = await client.get_trace(trace.trace_id)
        assert saved_trace["status"] == "COMPLETED"
        assert saved_trace["runs"][0]["spans"][0]["operation_name"] == "collect-keywords"
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_repeated_issue_is_aggregated_and_resolved_issue_reopens(tmp_path: Path) -> None:
    database = ObservabilityDatabase(tmp_path / "observability.db")
    await database.startup()
    try:
        client = ObservabilityClient(database, project_key="seo-agent")
        for _ in range(2):
            async with client.trace("seo.scan") as trace:
                async with trace.run(trigger_type="manual") as run:
                    async with run.span("dataforseo", kind=SpanKind.TOOL) as span:
                        await span.capture_exception(
                            TimeoutError("upstream timeout"),
                            issue_type="UPSTREAM_TIMEOUT",
                            component="dataforseo.organic_keywords",
                            error_code="UPSTREAM_TIMEOUT",
                        )
        issue = await database.fetch_one("SELECT * FROM obs_issues")
        assert issue is not None
        assert issue["occurrence_count"] == 2
        assert issue["affected_trace_count"] == 2
        await client.set_issue_status(issue["id"], IssueStatus.RESOLVED)
        async with client.trace("seo.scan") as trace:
            async with trace.run(trigger_type="manual") as run:
                async with run.span("dataforseo", kind=SpanKind.TOOL) as span:
                    await span.capture_exception(
                        TimeoutError("upstream timeout"),
                        issue_type="UPSTREAM_TIMEOUT",
                        component="dataforseo.organic_keywords",
                        error_code="UPSTREAM_TIMEOUT",
                    )
        reopened = await database.fetch_one("SELECT * FROM obs_issues WHERE id = ?", (issue["id"],))
        assert reopened is not None
        assert reopened["status"] == "REOPENED"
        assert reopened["occurrence_count"] == 3
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_jsonl_exporter_receives_completed_entities(tmp_path: Path) -> None:
    output = tmp_path / "events.jsonl"
    database = ObservabilityDatabase(tmp_path / "observability.db")
    await database.startup()
    try:
        client = ObservabilityClient(
            database,
            project_key="seo-agent",
            exporters=[JsonlExporter(output)],
        )
        async with client.trace("test.trace") as trace:
            async with trace.run(trigger_type="test") as run:
                async with run.span("step"):
                    pass
        lines = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        entity_types = {line["entity_type"] for line in lines}
        assert {"trace", "run", "span"}.issubset(entity_types)
    finally:
        await database.shutdown()


def test_opentelemetry_exporter_is_constructible_without_endpoint() -> None:
    exporter = OpenTelemetryExporter(service_name="seo-agent", endpoint=None)
    assert exporter.enabled is False
