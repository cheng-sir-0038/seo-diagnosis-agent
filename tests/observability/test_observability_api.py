from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.routes import observability as route
from src.observability.client import ObservabilityClient
from src.observability.storage import ObservabilityDatabase
from src.server import app


@pytest.mark.asyncio
async def test_observability_api_lists_trace_and_updates_issue(tmp_path: Path, monkeypatch) -> None:
    database = ObservabilityDatabase(tmp_path / "api.db")
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
