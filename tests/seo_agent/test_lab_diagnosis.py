from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest
import pytest_asyncio

from src.agent.models import RunStatus
from src.agent.observability import AgentRunObserver
from src.agent.providers.base import ModelChunk, ToolCall
from src.agent.providers.fake import FakeModelProvider
from src.agent.run_manager import RunManager
from src.agent.runtime.executor import AgentExecutor
from src.core.cache import CacheManager
from src.facade.data_facade import DataFacade
from src.models.common import Capability
from src.observability.client import ObservabilityClient
from src.observability.storage import ObservabilityDatabase
from src.providers.page_metadata import PageMetadataClient, PageMetadataProvider
from src.seo_agent.chat.runtime import build_seo_system_prompt
from src.seo_agent.chat.tools import build_seo_agent_registry

ORIGIN = "https://seo-lab.example.com"
SITE = {"id": "lab", "domain": "seo-lab.example.com"}
FIXTURES = Path(__file__).parents[1] / "fixtures" / "seo_test_lab"
CASES = {
    "healthy": "/healthy/home.html",
    "missing-title": "/single/title/missing.html",
    "duplicate-canonical": "/single/canonical/duplicate.html",
    "noindex": "/single/meta/robots-noindex.html",
}


class LabSession:
    """Only the HTTP transport is replaced. Reads still pass through the production client."""

    def __init__(self):
        self.urls = []
        self.files = {
            ORIGIN + path: (200, "text/html", (FIXTURES / f"{name}.html").read_text())
            for name, path in CASES.items()
        }

    @asynccontextmanager
    async def get(self, url, *, allow_redirects, **_kwargs):
        assert allow_redirects is False
        self.urls.append(url)
        status, media_type, body = self.files[url]

        async def iter_chunked(_size):
            yield body.encode("utf-8")

        yield SimpleNamespace(
            status=status, headers={"Content-Type": media_type}, charset="utf-8",
            content=SimpleNamespace(iter_chunked=iter_chunked),
        )

    async def close(self):
        pass


@pytest_asyncio.fixture
async def lab(pg_url, monkeypatch):
    session = LabSession()
    client = PageMetadataClient()
    client._session = session
    provider = PageMetadataProvider(client=client, analyzer=SimpleNamespace(enabled=False))
    facade = DataFacade()
    facade._capability_map = {capability: [provider] for capability in provider.capabilities}
    snapshots = CacheManager(pg_url)
    await snapshots.startup()
    module = importlib.import_module("src.facade.data_facade")
    monkeypatch.setattr(module, "cache_manager", snapshots)
    try:
        yield facade, session, snapshots
    finally:
        await client.shutdown()
        await snapshots.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(("case", "expected"), [
    ("healthy", set()),
    ("missing-title", {"TITLE_MISSING"}),
    ("duplicate-canonical", {"CANONICAL_MULTIPLE"}),
    ("noindex", {"NOINDEX_DECLARED"}),
])
async def test_lab_page_reports_observations_from_source_html(lab, case, expected):
    facade, _, _ = lab
    url = ORIGIN + CASES[case]
    tool = build_seo_agent_registry(facade, SITE).get("seo_site_audit")
    result = await tool.execute({"url": url})
    assert result.success
    findings = result.data["findings"]
    assert {finding["code"] for finding in findings} == expected
    for finding in findings:
        assert finding["source"] == "page_html" and finding["url"] == url
        assert finding["observed"]
        key = f"finding-{finding['code']}"
        card = next(item for item in result.data["items"] if item["key"] == key)
        assert card["url"] == url
        assert any(entry["label"] == "数据来源" for entry in card["metadata"])
    if case == "missing-title":
        assert findings[0]["observed"] == {"title_count": 0}
    if case == "duplicate-canonical":
        assert findings[0]["observed"]["canonical_count"] == 2
        assert len(findings[0]["observed"]["canonical_hrefs"]) == 2
        assert findings[0]["status"] == "needs_review"
    if case == "noindex":
        assert findings[0]["status"] == "needs_review"
        assert "收录状态未知" in findings[0]["reason"]


@pytest.mark.asyncio
async def test_lab_technical_site_report_reads_files_with_no_provider_keys(lab):
    facade, session, _ = lab
    page_url = ORIGIN + CASES["missing-title"]
    sitemap_url = ORIGIN + "/catalog.xml"
    session.files[ORIGIN + "/robots.txt"] = (
        200, "text/plain", f"User-agent: *\nDisallow: /assets/\nSitemap: {sitemap_url}\n",
    )
    urls = [ORIGIN + f"/sample/{index}" for index in range(7)] + ["https://other.example/"]
    entries = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    session.files[sitemap_url] = (
        200, "application/xml", '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + entries + "</urlset>",
    )

    assert facade.live_capabilities() == {Capability.PAGE_METADATA, Capability.SITE_RESOURCE}
    result = await build_seo_agent_registry(facade, SITE).get("seo_site_diagnosis").execute(
        {"url": page_url}
    )

    assert session.urls == [page_url, ORIGIN + "/robots.txt", sitemap_url]
    assert result.success and result.data["workflow_status"] == "complete"
    assert result.data["coverage"]["search"]["status"] == "skipped"
    assert result.data["coverage"]["indexing"]["status"] == "not_implemented"
    assert result.data["findings"][0]["code"] == "TITLE_MISSING"
    evidence = result.data["evidence"]
    assert evidence["robots_txt"]["raw"]["data"]["rules"] == ["user-agent: *", "disallow: /assets/"]
    sitemap = evidence["sitemap"]["raw"]["data"]
    assert sitemap["entry_count"] == 8
    assert sitemap["excluded_entry_count"] == 1
    assert sitemap["sample_urls"] == urls[:5]


@pytest.mark.asyncio
async def test_lab_site_report_retains_file_status_and_ignores_error_page_tags(lab):
    facade, session, _ = lab
    page_url = ORIGIN + CASES["missing-title"]
    sitemap_url = ORIGIN + "/preview/sitemap.xml"
    status, mime, html = session.files[page_url]
    assert status == 200
    session.files[page_url] = (503, mime, html)
    session.files[ORIGIN + "/robots.txt"] = (404, "text/html", "<title>Not found</title>")
    session.files[sitemap_url] = (200, "text/xml", "<unknown />")
    result = await build_seo_agent_registry(facade, SITE).get("seo_site_diagnosis").execute(
        {"url": page_url, "sitemap_url": sitemap_url}
    )

    assert session.urls == [page_url, ORIGIN + "/robots.txt", sitemap_url]
    assert result.success and result.data["workflow_status"] == "partial"
    assert result.data["findings"] == []
    assert result.data["coverage"]["page"]["status"] == "partial"
    assert result.data["coverage"]["sitemap"]["error_code"] == "INVALID_SITE_FILE"
    assert result.data["evidence"]["robots_txt"]["raw"]["data"]["status"] == "not_found"
    assert result.data["evidence"]["sitemap"]["raw"]["data"]["status_code"] == 200


@pytest.mark.asyncio
async def test_lab_sitemap_index_reports_children_without_fetching_them(lab):
    facade, session, _ = lab
    page_url = ORIGIN + CASES["healthy"]
    sitemap_url = ORIGIN + "/sitemap.xml"
    child_url = ORIGIN + "/sitemap-articles.xml"
    session.files[ORIGIN + "/robots.txt"] = (404, "text/html", "")
    session.files[sitemap_url] = (
        200, "text/xml", f"<sitemapindex><sitemap><loc>{child_url}</loc></sitemap></sitemapindex>",
    )

    result = await build_seo_agent_registry(facade, SITE).get("seo_site_diagnosis").execute(
        {"url": page_url}
    )
    sitemap = result.data["evidence"]["sitemap"]["raw"]["data"]
    assert sitemap["document_type"] == "sitemapindex"
    assert sitemap["sample_urls"] == [child_url]
    assert session.urls == [page_url, ORIGIN + "/robots.txt", sitemap_url]


@pytest.mark.asyncio
async def test_lab_missing_title_diagnostic_baseline_and_recheck(lab, pg_url):
    facade, session, snapshots = lab
    url = ORIGIN + CASES["missing-title"]
    database = ObservabilityDatabase(pg_url)
    observer = ObservabilityClient(database, project_key="seo-lab")
    registry = build_seo_agent_registry(facade, SITE, snapshots=snapshots, observability=observer)

    async def chat(question, calls, reply):
        chunks = [[ModelChunk(tool_calls=[call], finish_reason="tool_calls")] for call in calls]
        chunks.append([ModelChunk(text=reply), ModelChunk(finish_reason="stop")])
        run = RunManager().create_run([
            {"role": "system", "content": build_seo_system_prompt(SITE, registry.schemas())},
            {"role": "user", "content": question},
        ])
        await AgentExecutor(
            FakeModelProvider(chunks), registry,
            observer=AgentRunObserver(observer, operation_name="seo_agent.chat"),
        ).run(run)
        assert run.status == RunStatus.COMPLETED
        return [e.payload["result"]["data"] for e in run.events if e.type == "tool.completed"]

    try:
        audit, before = await chat(
            "看看这页的 SEO 基础信息，给一个标题建议并保存修复前的证据。",
            [
                ToolCall(id="audit", name="seo_site_audit", arguments={"url": url}),
                ToolCall(id="before", name="seo_page_change_review",
                         arguments={"action": "capture_baseline", "url": url}),
            ],
            "源码没有 Title，可采用现有主标题作为候选。",
        )
        assert audit["findings"][0]["code"] == "TITLE_MISSING"
        assert before["review_status"] == "baseline_recorded"
        baseline_id = before["baseline_snapshot_id"]
        title = audit["evidence"]["page"]["raw"]["data"]["h1s"][0]
        recheck = ToolCall(id="compare", name="seo_page_change_review", arguments={
            "action": "compare", "baseline_snapshot_id": baseline_id, "expected": {"title": title},
        })
        [unfixed] = await chat("检查标题现在是否符合目标。", [recheck], "尚未达到目标。")
        assert unfixed["review_status"] == "failed"

        status, mime, html = session.files[url]
        changed = html.replace("</head>", f"<title>{escape(title)}</title></head>")
        session.files[url] = (status, mime, changed)
        [after] = await chat(
            "我已更新页面，请按保存的基线重新读取并复检。", [recheck], "标题与目标一致。",
        )

        assert after["review_status"] == "passed"
        assert after["checks"] == [
            {"field": "title", "expected": title, "actual": title, "status": "passed"}
        ]
        assert after["differences"][0] == {
            "field": "title", "before": None, "after": title, "changed": True,
        }
        assert (await snapshots.get_snapshot(baseline_id))["data"]["title"] is None
        assert all(requested == url for requested in session.urls)
        spans = []
        for trace in await observer.list_traces():
            detail = await observer.get_trace(trace["id"])
            spans.extend(span for run in detail["runs"] for span in run["spans"])
        assert {"seo_site_audit", "freeze_page_review_evidence", "compare_page_acceptance"} <= {
            row["operation_name"] for row in spans
        }
        print(f"Lab Title 复检：缺失 → {title}；未修复时失败，修复后通过。")
    finally:
        await observer.shutdown()
