from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.cache import CacheManager
from src.core.exceptions import InternalProviderRequestError
from src.models.common import Capability
from src.providers.page_metadata.client import FetchedHtmlPage
from src.providers.page_metadata.provider import PageMetadataProvider
from src.seo_agent.chat.tools import build_seo_agent_registry

SITE = {
    "id": "site-review",
    "domain": "example.com",
    "default_country": "US",
    "default_language": "en",
}


class HtmlSource:
    def __init__(self):
        self.html = (
            '<title>Before</title><meta name="description" content="Description"><h1>Before</h1>'
        )
        self.status = 200
        self.fail = False

    async def fetch(self, *, site_id, url):
        if self.fail:
            raise InternalProviderRequestError("private upstream failure")
        return FetchedHtmlPage(
            requested_url=url,
            final_url=url,
            status_code=self.status,
            content_type="text/html",
            content_length_bytes=len(self.html),
            headers={},
            html=self.html,
        )


class PageSource:
    def __init__(self):
        self.transport = HtmlSource()
        self.provider = PageMetadataProvider(
            client=self.transport, analyzer=SimpleNamespace(enabled=False)
        )
        self.calls = []

    def live_capabilities(self):
        return {Capability.PAGE_METADATA}

    async def query_live(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs["capability"] == Capability.PAGE_METADATA
        page = await self.provider.get_page_metadata(kwargs["site_id"], **kwargs["params"])
        return {
            "meta": {"source": "page_html", "capability": "page_metadata", "cached": False},
            "data": page.model_dump(mode="json"),
        }


@pytest.fixture
async def review(tmp_path):
    snapshots = CacheManager(tmp_path / "evidence.db")
    await snapshots.startup()
    source = PageSource()
    try:
        tool = build_seo_agent_registry(source, SITE, snapshots=snapshots).get(
            "seo_page_change_review"
        )
        yield source, snapshots, tool
    finally:
        await snapshots.shutdown()


@pytest.mark.asyncio
async def test_capture_then_compare_uses_real_html_parser_and_keeps_baseline_immutable(review):
    source, snapshots, tool = review
    first = await tool.execute({"action": "capture_baseline", "url": "https://example.com/guide"})
    assert first.data["review_status"] == "baseline_recorded"
    baseline_id = first.data["baseline_snapshot_id"]
    baseline = await snapshots.get_snapshot(baseline_id)
    source.transport.html = (
        '<title>After</title><meta name="description" content="Description">'
        '<link rel="canonical" href="/guide"><h1>After</h1>'
    )
    result = await tool.execute(
        {
            "baseline_snapshot_id": baseline_id,
            "expected": {
                "title": "After",
                "h1s": ["After"],
                "canonical_url": "https://example.com/guide",
            },
        }
    )
    assert result.success and result.data["review_status"] == "passed"
    assert result.data["current_snapshot_id"] != baseline_id
    assert all(row["status"] == "passed" for row in result.data["checks"])
    differences = {row["field"]: row for row in result.data["differences"]}
    assert differences["title"] == {
        "field": "title",
        "before": "Before",
        "after": "After",
        "changed": True,
    }
    assert differences["meta_description"]["changed"] is False
    assert await snapshots.get_snapshot(baseline_id) == baseline
    assert all(call["force_refresh"] is True for call in source.calls)
    current = await snapshots.get_snapshot(result.data["current_snapshot_id"])
    assert current["meta"]["business_site_id"] == SITE["id"]


@pytest.mark.asyncio
async def test_unchanged_or_changed_html_does_not_pass_without_matching_expected_values(review):
    _, _, tool = review
    baseline = await tool.execute({"action": "capture_baseline"})
    key = baseline.data["baseline_snapshot_id"]
    failed = await tool.execute({"baseline_snapshot_id": key, "expected": {"title": "After"}})
    assert failed.data["review_status"] == "failed"
    assert failed.data["checks"][0]["actual"] == "Before"
    unknown = await tool.execute({"baseline_snapshot_id": key})
    assert unknown.data["review_status"] == "insufficient_evidence"


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["http_error", "upstream_failure"])
async def test_failed_current_read_is_insufficient_evidence_not_a_pass(review, problem):
    source, _, tool = review
    baseline = await tool.execute({"action": "capture_baseline"})
    if problem == "http_error":
        source.transport.status = 404
    else:
        source.transport.fail = True
    result = await tool.execute(
        {
            "baseline_snapshot_id": baseline.data["baseline_snapshot_id"],
            "expected": {"title": "Before"},
        }
    )
    assert result.data["review_status"] == "insufficient_evidence"
    assert result.data["workflow_status"] == "partial"
    assert result.data["current_snapshot_id"] is None
    assert "private upstream failure" not in str(result.model_dump())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem",
    [
        "wrong_site",
        "wrong_type",
        "wrong_page",
        "missing_fields",
        "malformed_meta",
        "foreign_url",
        "private_url",
        "cached",
        "wrong_source",
    ],
)
async def test_invalid_baseline_is_rejected_before_fetch(review, problem):
    source, snapshots, tool = review
    baseline = await tool.execute({"action": "capture_baseline"})
    raw = await snapshots.get_snapshot(baseline.data["baseline_snapshot_id"])
    args = {"expected": {"title": "Before"}}
    if problem == "wrong_site":
        raw["meta"]["business_site_id"] = "another-site"
    elif problem == "wrong_type":
        raw["meta"]["capability"] = "serp_overview"
    elif problem == "wrong_page":
        args["url"] = "https://example.com/another-page"
    elif problem == "malformed_meta":
        raw["meta"] = []
    elif problem == "foreign_url":
        raw["data"]["requested_url"] = "https://another.example/"
    elif problem == "private_url":
        raw["data"]["requested_url"] = "http://127.0.0.1/"
    elif problem == "cached":
        raw["meta"]["cached"] = True
    elif problem == "wrong_source":
        raw["meta"]["source"] = "serper"
    else:
        del raw["data"]["h1s"]
    key = await snapshots.save_snapshot(
        "bad-baseline",
        "page_metadata",
        {**raw, "meta": {}} if problem == "malformed_meta" else raw,
        "page_html",
    )
    if problem == "malformed_meta":
        import json

        await snapshots._db.execute(
            "UPDATE evidence_snapshots SET data_json=? WHERE id=?", (json.dumps(raw), key)
        )
        await snapshots._db.commit()
    with pytest.raises(ValueError):
        await tool.execute({"baseline_snapshot_id": key, **args})
    assert len(source.calls) == 1


@pytest.mark.asyncio
async def test_absent_canonical_is_known_absence_and_can_be_an_explicit_expectation(review):
    _, _, tool = review
    baseline = await tool.execute({"action": "capture_baseline"})
    result = await tool.execute(
        {
            "baseline_snapshot_id": baseline.data["baseline_snapshot_id"],
            "expected": {"canonical_url": None},
        }
    )
    assert result.data["review_status"] == "passed"


def test_review_tool_requires_both_live_html_and_snapshot_store():
    assert "seo_page_change_review" not in build_seo_agent_registry(PageSource(), SITE).names()


@pytest.mark.asyncio
async def test_baseline_capture_accepts_runtime_without_task_or_run_ids(review):
    from src.agent.tools.base import ToolRuntimeContext

    _, snapshots, tool = review
    result = await tool.execute(
        {"action": "capture_baseline"}, runtime=ToolRuntimeContext(site_id=SITE["id"])
    )
    assert result.data["review_status"] == "baseline_recorded"
    assert await snapshots.get_snapshot(result.data["baseline_snapshot_id"])


@pytest.mark.asyncio
async def test_capture_failure_never_produces_a_baseline_id(review):
    source, _, tool = review
    source.transport.status = 500
    result = await tool.execute({"action": "capture_baseline"})
    assert result.data["baseline_snapshot_id"] is None
    assert result.data["review_status"] == "insufficient_evidence"


@pytest.mark.asyncio
async def test_missing_baseline_and_unsupported_actions_fail_before_network(review):
    source, _, tool = review
    for arguments in (
        {},
        {"action": "publish"},
        {"action": "capture_baseline", "auto_publish": True},
    ):
        with pytest.raises(ValueError):
            await tool.execute(arguments)
    with pytest.raises(ValueError, match="不存在"):
        await tool.execute({"baseline_snapshot_id": "not-found"})
    assert not source.calls


@pytest.mark.asyncio
async def test_review_chat_api_runs_registered_workflow_and_records_correlated_steps(
    review, tmp_path, monkeypatch
):
    import json

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from src.agent.harness.store import AgentChatHarnessStore
    from src.agent.providers.base import ModelChunk, ToolCall
    from src.agent.providers.fake import FakeModelProvider
    from src.agent.run_manager import RunManager
    from src.api.routes import seo_agent_chat
    from src.observability.client import ObservabilityClient
    from src.observability.exporters import OpenTelemetryExporter
    from src.observability.storage import ObservabilityDatabase
    from src.seo_agent.db.database import SeoAgentDatabase
    from src.seo_agent.services.system import SeoAgentSystemService

    source, snapshots, _ = review
    db = SeoAgentDatabase(tmp_path / "chat.db")
    await db.startup()
    system = SeoAgentSystemService(db)
    site = await system.create_site(
        name="Review",
        domain="example.com",
        default_country="US",
        default_language="en",
        timezone="UTC",
    )
    baseline = (
        await build_seo_agent_registry(source, site, snapshots=snapshots)
        .get("seo_page_change_review")
        .execute({"action": "capture_baseline"})
    )
    source.transport.html = "<title>After</title><h1>After</h1>"
    observations = ObservabilityDatabase(tmp_path / "observations.db")
    memory = InMemorySpanExporter()
    exporter = OpenTelemetryExporter(
        service_name="test", endpoint=None, span_exporter=memory, database=observations
    )
    observer = ObservabilityClient(observations, project_key="test", exporters=[exporter])
    await observer.startup()
    manager = RunManager()
    model = FakeModelProvider(
        [
            [
                ModelChunk(
                    tool_calls=[
                        ToolCall(
                            id="review",
                            name="seo_page_change_review",
                            arguments={
                                "baseline_snapshot_id": baseline.data["baseline_snapshot_id"],
                                "expected": {"title": "After", "h1s": ["After"]},
                            },
                        )
                    ],
                    finish_reason="tool_calls",
                )
            ],
            [
                ModelChunk(text="已按验收值核对，Title 和 H1 一致。"),
                ModelChunk(finish_reason="stop"),
            ],
        ]
    )
    for name, value in {
        "facade": source,
        "cache_manager": snapshots,
        "seo_agent_system_service": system,
        "seo_chat_harness_store": AgentChatHarnessStore(db),
        "run_manager": manager,
        "observability_client": observer,
        "observability_database": observations,
        "build_seo_model_provider": lambda: model,
    }.items():
        monkeypatch.setattr(seo_agent_chat, name, value)
    app = FastAPI()
    app.include_router(seo_agent_chat.router, prefix="/api/v1")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/seo-agent/chat/runs",
                json={
                    "site_id": site["id"],
                    "messages": [{"role": "user", "content": "复检页面 Title 和 H1"}],
                },
            )
            assert response.status_code == 201
            events = await client.get(response.json()["events_url"])
            payloads = [
                json.loads(line[6:])
                for line in events.text.splitlines()
                if line.startswith("data: ")
            ]
            completed = next(item for item in payloads if item["type"] == "tool.completed")
            assert completed["payload"]["render_hint"] == "cards"
            assert completed["payload"]["result"]["data"]["review_status"] == "passed"
        traces = await observations.fetch_all("SELECT * FROM obs_traces")
        spans = await observations.fetch_all("SELECT * FROM obs_spans")
        assert len(traces) == 1
        assert {row["operation_name"] for row in spans} >= {
            "seo_page_change_review",
            "load_page_review_baseline",
            "seo_page_metadata",
            "freeze_page_review_evidence",
            "compare_page_acceptance",
        }
        assert {row["trace_id"] for row in spans} == {traces[0]["id"]}
        await exporter.force_flush()
        comparison = next(
            span for span in memory.get_finished_spans() if span.name == "compare_page_acceptance"
        )
        assert "passed" in comparison.attributes["langfuse.observation.output"]
        for table in ("approval_actions", "execution_tasks", "publish_records"):
            assert await db.fetch_value(f"SELECT COUNT(*) FROM {table}") == 0
    finally:
        await manager.shutdown()
        await observer.shutdown()
        await db.shutdown()
