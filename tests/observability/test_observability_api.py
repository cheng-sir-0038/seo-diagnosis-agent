from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.api.routes import observability as route
from src.observability.client import ObservabilityClient
from src.observability.models import IssueStatus, Status
from src.observability.schema import IssueOccurrenceRow, IssueRow, MetricPointRow, ToolCallRow, TraceRow
from src.observability.storage import ObservabilityDatabase
from src.server import app


@pytest.mark.asyncio
async def test_observability_api_lists_trace_and_updates_issue(pg_url: str, monkeypatch) -> None:
    database = ObservabilityDatabase(pg_url)
    await database.startup()
    client = ObservabilityClient(database, project_key="api-test")
    await client.startup()
    monkeypatch.setattr(route, "observability_client", client)
    monkeypatch.setattr(route, "observability_database", database)
    try:
        async with client.trace("api.trace") as trace:
            async with trace.run(trigger_type="test") as run:
                async with run.span("broken") as span:
                    await span.capture_exception(ValueError("invalid structured output"))
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            traces = await http.get("/api/v1/observability/traces")
            issues = await http.get("/api/v1/observability/issues")
            issue_id = issues.json()[0]["id"]
            updated = await http.patch(
                f"/api/v1/observability/issues/{issue_id}/status",
                json={"status": "RESOLVED"},
            )
            detail = await http.get(f"/api/v1/observability/traces/{trace.trace_id}")
        assert traces.status_code == 200 and len(traces.json()) == 1
        assert issues.status_code == 200 and issues.json()[0]["occurrence_count"] == 1
        assert updated.json()["status"] == "RESOLVED"
        assert detail.status_code == 200 and len(detail.json()["issues"]) == 1
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_observability_window_uses_recorded_times_and_uncapped_counts(pg_url: str, monkeypatch) -> None:
    database = ObservabilityDatabase(pg_url)
    client = ObservabilityClient(database, project_key="window-test")
    await client.startup()
    other_project = ObservabilityClient(database, project_key="other-project")
    monkeypatch.setattr(route, "observability_client", client)

    async def trace_at(started_at, *, observer=client, status=Status.COMPLETED, metadata=None):
        async with observer.trace("window.trace", metadata=metadata) as trace:
            pass
        await database.finish_row(TraceRow, trace.trace_id, started_at=started_at, status=status.value)
        return trace.trace_id

    start = "2026-11-24T00:00:00+00:00"
    end = "2026-11-25T00:00:00+00:00"
    bounds = {"start_at": "2026-11-24T08:00:00+08:00", "end_at": end}
    try:
        before = await trace_at(
            "2026-11-23T00:00:00+00:00", status=Status.FAILED, metadata={"application_run_id": "run_older"},
        )
        failed = await trace_at(start, status=Status.FAILED)
        waiting = await trace_at(
            "2026-11-24T08:00:00+00:00", status=Status.INTERRUPTED,
            metadata={"application_status": "waiting_for_input"},
        )
        await trace_at(end, status=Status.FAILED)
        await trace_at("2026-11-24T08:00:00+00:00", observer=other_project, status=Status.FAILED)
        for idx, started_at in enumerate(["2026-11-23T23:59:59+00:00", start, end]):
            tool = await client.record_tool_call(
                trace_id=before, tool_call_id=f"call-{idx}", tool_name="tool", operation="test",
            )
            await database.finish_row(ToolCallRow, tool["id"], started_at=started_at)
            metric = await client.record_metric(metric_name="boundary.metric", value=idx, unit="count")
            await database.finish_row(MetricPointRow, metric["id"], recorded_at=started_at)

        for name, seen_at in (("before", "2026-11-23T00:00:00+00:00"), ("open", start),
                              ("resolved", "2026-11-24T01:00:00+00:00")):
            issue = await client.capture_issue(trace_id=before, message=name, issue_type="Error", title=name)
            await database.finish_row(IssueRow, issue["id"], last_seen_at=seen_at)
            if name == "resolved":
                await client.set_issue_status(issue["id"], IssueStatus.RESOLVED)

        root = "/api/v1/observability"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            overview = await http.get(f"{root}/overview", params=bounds)
            assert overview.status_code == 200
            assert overview.json() == {
                "trace_count": 2, "failed_trace_count": 1, "waiting_trace_count": 1, "tool_call_count": 1,
                "previous": {"trace_count": 1, "failed_trace_count": 1, "tool_call_count": 1},
            }
            traces = await http.get(f"{root}/traces", params={**bounds, "limit": 1})
            assert [row["id"] for row in traces.json()] == [waiting]
            for identifier in (before, "run_older"):
                matched = await http.get(f"{root}/traces", params={"identifier": identifier})
                assert [row["id"] for row in matched.json()] == [before]
            active_issues = await http.get(f"{root}/issues", params={**bounds, "pending": "true", "limit": 1})
            assert [row["title"] for row in active_issues.json()] == ["open"]
            windowed_issues = await http.get(f"{root}/issues", params=bounds)
            assert [row["title"] for row in windowed_issues.json()] == ["resolved", "open"]
            errors = await http.get(f"{root}/traces", params={**bounds, "status": "FAILED"})
            assert [row["id"] for row in errors.json()] == [failed]
            metrics = await http.get(f"{root}/metrics", params={**bounds, "metric_name": "boundary.metric"})
            assert metrics.json()[0]["point_count"] == 1 and metrics.json()[0]["average_value"] == 1
            unbounded = await http.get(f"{root}/metrics", params={"metric_name": "boundary.metric"})
            assert unbounded.json()[0]["point_count"] == 3
            all_time = await http.get(f"{root}/overview")
            assert all_time.json() == {
                "trace_count": 4, "failed_trace_count": 3, "waiting_trace_count": 1, "tool_call_count": 3,
                "previous": None,
            }
            invalid = await http.get(f"{root}/overview", params={"start_at": end, "end_at": start})
            assert invalid.status_code == 422
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_observability_trace_scope_applies_before_limit_and_id_lookup_spans_scopes(pg_url: str, monkeypatch) -> None:
    database = ObservabilityDatabase(pg_url)
    client = ObservabilityClient(database, project_key="trace-scopes-test")
    await client.startup()
    monkeypatch.setattr(route, "observability_client", client)

    async def trace_at(operation, hour, *, metadata=None, status=Status.COMPLETED):
        async with client.trace(operation, metadata=metadata) as trace:
            pass
        await database.finish_row(
            TraceRow, trace.trace_id, started_at=f"2026-11-24T{hour:02d}:00:00+00:00", status=status.value,
        )
        return trace.trace_id

    try:
        business = await trace_at("workspace.chat", 9, metadata={"application_run_id": "run_business"})
        near_prefix = await trace_at("schedulerish.check", 8)
        sync = await trace_at("scheduler.automation_sync", 10, status=Status.FAILED)
        outbox = await trace_at("scheduler.notification_outbox", 11, metadata={"application_run_id": "run_outbox"})
        traces_url = "/api/v1/observability/traces"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            async def ids(**params):
                response = await http.get(traces_url, params=params)
                assert response.status_code == 200
                return [row["id"] for row in response.json()]

            assert await ids() == await ids(scope="all") == [outbox, sync, business, near_prefix]
            assert await ids(scope="business", limit=1) == [business]
            assert await ids(scope="business") == [business, near_prefix]
            assert await ids(scope="system", limit=1) == [outbox]
            assert await ids(scope="product", limit=1) == [business]
            assert await ids(scope="product", identifier=outbox) == []
            visible = (await http.get("/api/v1/observability/overview", params={"hide_maintenance": "true"})).json()
            assert visible["trace_count"] == 2
            assert visible["failed_trace_count"] == 0
            assert await ids(scope="system", status="FAILED", limit=1) == [sync]
            for identifier in (outbox, "run_outbox"):
                assert await ids(scope="business", identifier=identifier, limit=1) == [outbox]
            for identifier in (business, "run_business"):
                assert await ids(scope="system", identifier=identifier) == [business]
            assert await ids(identifier="run_out") == []
            invalid = await http.get(traces_url, params={"scope": "invalid"})
            assert invalid.status_code == 422
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_workbench_excludes_maintenance_across_observability_views(pg_url, monkeypatch):
    database = ObservabilityDatabase(pg_url)
    client = ObservabilityClient(database, project_key="workbench-scope")
    await client.startup()
    monkeypatch.setattr(route, "observability_client", client)

    async def make_trace(operation, hour):
        async with client.trace(operation) as trace:
            pass
        await database.finish_row(TraceRow, trace.trace_id, started_at=f"2026-11-25T{hour:02}:00:00+00:00")
        return trace.trace_id

    async def capture(trace_id, message, title, hour):
        issue = await client.capture_issue(
            trace_id=trace_id, message=message, title=title, issue_type="Error", context={"trace": trace_id},
        )
        occurrence = await database.fetch_one(select(IssueOccurrenceRow.__table__).where(
            IssueOccurrenceRow.trace_id == trace_id, IssueOccurrenceRow.issue_id == issue["id"],
        ))
        occurred_at = f"2026-11-25T{hour:02}:00:00+00:00"
        await database.finish_row(IssueOccurrenceRow, occurrence["id"], occurred_at=occurred_at)
        await database.finish_row(IssueRow, issue["id"], last_seen_at=occurred_at)
        return issue["id"]

    try:
        scan = await make_trace("scheduler.opportunity_scan", 9)
        outbox = await make_trace("scheduler.notification_outbox", 11)
        mixed_issue = await capture(outbox, "shared timeout", "maintenance title", 10)
        await capture(scan, "shared timeout", "scan title", 9)
        await database.finish_row(IssueRow, mixed_issue, last_seen_at="2026-11-25T10:00:00+00:00")
        noise_issue = await capture(outbox, "delivery retry", "maintenance only", 11)
        for trace_id, value in ((scan, 10), (outbox, 90)):
            await client.record_metric(metric_name="test.value", trace_id=trace_id, value=value)
        await client.record_tool_call(trace_id=scan, tool_call_id="scan", tool_name="business", operation="test")
        await client.record_tool_call(trace_id=outbox, tool_call_id="delivery", tool_name="maintenance", operation="test")

        root = "/api/v1/observability"
        scoped = {"hide_maintenance": "true"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            async def get(path, **params):
                response = await http.get(f"{root}/{path}", params=params)
                assert response.status_code == 200, response.text
                return response.json()

            assert [row["id"] for row in await get("traces", scope="product", limit=1)] == [scan]
            assert (await get("overview", **scoped))["tool_call_count"] == 1
            assert (await get(f"traces/{scan}", **scoped))["id"] == scan
            assert (await http.get(f"{root}/traces/{outbox}", params=scoped)).status_code == 404
            assert (await get(f"traces/{outbox}"))["id"] == outbox
            assert (await get("issues", limit=1))[0]["id"] == noise_issue
            [issue] = await get("issues", **scoped, limit=1)
            assert issue["id"] == mixed_issue and issue["title"] == "shared timeout"
            assert issue["occurrence_count"] == issue["affected_trace_count"] == 1
            assert issue["last_seen_at"] == "2026-11-25T09:00:00+00:00"
            assert outbox not in issue["latest_context_json"]
            assert await get("issues", **scoped, start_at="2026-11-25T10:00:00Z", end_at="2026-11-25T12:00:00Z") == []
            assert (await get("metrics", metric_name="test.value"))[0]["average_value"] == 50
            [metric] = await get("metrics", **scoped, metric_name="test.value")
            assert metric["average_value"] == 10 and metric["point_count"] == 1
    finally:
        await client.shutdown()
