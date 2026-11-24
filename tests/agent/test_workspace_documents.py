from __future__ import annotations

import json
from io import BytesIO
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from reportlab.pdfgen import canvas

from src.agent.harness.store import AgentChatHarnessStore
from src.agent.providers.fake import FakeModelProvider
from src.agent.run_manager import RunManager
from src.agent.workspace.documents import MAX_FILE_BYTES, parse_document
from src.api.routes import agent as generic_agent
from src.api.routes.workspace import conversations, documents
from src.healthcare_agent.postgraduate import seed_postgraduate_corpus
from src.observability.client import ObservabilityClient
from src.observability.storage import ObservabilityDatabase
from src.seo_agent.db.database import SeoAgentDatabase

DOCS = "/api/v1/workspace/documents"
RUNS = "/api/v1/chat/runs"
OWNER = {"X-Workspace-Key": "owner-" + "a" * 32}
OTHER = {"X-Workspace-Key": "other-" + "b" * 32}
PRIVATE_FACT = "星桥联调口令样例为 JUNIPER-2026-411。"


def test_extracts_a_text_pdf_with_page_reference_and_rejects_scans():
    data = BytesIO()
    pdf = canvas.Canvas(data)
    pdf.drawString(60, 750, "First page evidence")
    pdf.showPage()
    pdf.drawString(60, 750, "Second page evidence")
    pdf.save()
    name, sections = parse_document("regression.pdf", data.getvalue())
    assert name == "regression.pdf"
    assert sections[1] == {"location": "第 2 页 · 片段 1", "text": "Second page evidence"}

    empty = BytesIO()
    pdf = canvas.Canvas(empty)
    pdf.rect(20, 100, 50, 50)
    pdf.save()
    with pytest.raises(ValueError, match="未提取到文字"):
        parse_document("scan.pdf", empty.getvalue())


@pytest.mark.asyncio
async def test_upload_retrieve_and_use_only_authorized_documents(pg_url, monkeypatch):
    database = SeoAgentDatabase(pg_url)
    observer_db = ObservabilityDatabase(pg_url)
    await database.startup()
    await seed_postgraduate_corpus(database)
    observer = ObservabilityClient(observer_db, project_key="workspace-doc-test")
    await observer.startup()
    manager = RunManager()

    class EvidenceModel(FakeModelProvider):
        async def stream(self, messages, tools, *, tool_choice="auto"):
            assert PRIVATE_FACT in messages[-1]["content"]
            assert "联调手册.md" in messages[-1]["content"]
            assert not tools and tool_choice == "none"
            async for chunk in super().stream(messages, tools, tool_choice=tool_choice):
                yield chunk

    monkeypatch.setattr(conversations, "build_conversation_model", lambda: EvidenceModel.text(
        f"{PRIVATE_FACT} [资料 1] 联调手册.md",
    ))
    monkeypatch.setattr(conversations, "run_manager", manager)
    monkeypatch.setattr(conversations, "seo_chat_harness_store", AgentChatHarnessStore(database))
    monkeypatch.setattr(conversations, "observability_client", observer)
    monkeypatch.setattr(conversations, "observability_database", observer_db)
    monkeypatch.setattr(generic_agent, "run_manager", manager)
    app = FastAPI()
    for router in (documents.router, conversations.router, conversations.site_chat_router,
                   generic_agent.router):
        app.include_router(router, prefix="/api/v1")

    def upload_headers(name: str) -> dict:
        return {**OWNER, "X-Workspace-File": quote(name)}

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            file = f"# 开发须知\n{PRIVATE_FACT}".encode()
            private_url = f"{DOCS}?conversation_id=conversation-a"
            put = await http.post(private_url, headers=upload_headers("联调手册.md"), content=file)
            assert put.status_code == 201, put.text
            doc_id = put.json()["id"]
            duplicate = await http.post(
                private_url, headers=upload_headers("重试.md"), content=file,
            )
            assert duplicate.json()["id"] == doc_id
            assert (await http.get(DOCS, headers=OWNER)).json()["documents"] == []
            bad = await http.post(DOCS, headers=upload_headers("bad.json"), content=b"{")
            bigger = await http.post(
                DOCS, headers=upload_headers("big.txt"), content=b"x" * (MAX_FILE_BYTES + 1),
            )
            assert bad.status_code == 422 and bigger.status_code == 413

            stored = await http.post(DOCS, headers=upload_headers("公开验收说明.txt"), content=file)
            library_id = stored.json()["id"]
            hits = await http.get(DOCS, headers=OWNER, params={"q": "星桥联调"})
            assert hits.json()["documents"][0]["id"] == library_id
            assert "JUNIPER-2026-411" in hits.json()["documents"][0]["excerpt"]["text"]
            assert (await http.get(DOCS, headers=OTHER)).json()["documents"] == []
            public = await http.get(DOCS, headers=OWNER, params={"q": "武汉科技大学"})
            assert any(
                row["source_url"].startswith("https://") for row in public.json()["references"]
            )
            download_url = f"{DOCS}/{library_id}/download"
            downloaded = await http.get(download_url, headers=OWNER)
            assert downloaded.content == file
            assert "attachment;" in downloaded.headers["content-disposition"]
            assert (await http.get(download_url, headers=OTHER)).status_code == 404

            body = {
                "conversation_id": "conversation-a",
                "messages": [{"role": "user", "content": "联调口令是什么？"}],
                "document_ids": [doc_id],
            }
            other_case = await http.post(
                RUNS, json={**body, "conversation_id": "conversation-b"}, headers=OWNER,
            )
            assert other_case.status_code == 404
            assert (await http.post(RUNS, json=body, headers=OTHER)).status_code == 404
            assert (await http.post(RUNS, json=body, headers=OWNER)).status_code == 201
            # Wait for the same runtime used by both chat APIs.
            task = (await conversations.seo_chat_harness_store.list_tasks())[0]
            run_id = task["current_run_id"]
            await manager.get(run_id).task
            url = f"{RUNS}/{run_id}"
            events = await http.get(url + "/events", headers=OWNER)
            assert "JUNIPER-2026-411" in events.text and "run.completed" in events.text
            stats = await http.get(url + "/stats", headers=OWNER)
            assert stats.status_code == 200
            assert stats.json()["elapsed_ms"] > 0
            assert stats.json()["total_tokens"] is None
            assert (await http.get(url + "/stats", headers=OTHER)).status_code == 404
            assert (await http.get(f"{RUNS}/unavailable/stats", headers=OWNER)).status_code == 404
            assert (await http.get(url, headers=OTHER)).status_code == 404
            assert (await http.get(url + "/events", headers=OTHER)).status_code == 404
            assert (await http.post(url + "/cancel", headers=OTHER)).status_code == 404
            with monkeypatch.context() as offline:
                offline.setattr(conversations, "_durable_enabled", lambda: False)
                assert (await http.get(url, headers=OTHER)).status_code == 404
                assert (await http.get(url, headers=OWNER)).status_code == 200
            assert (await http.get(f"/api/v1/agent/runs/{run_id}/events")).status_code == 404
            older_task = "/api/v1/seo-agent/chat/tasks"
            assert (await http.get(older_task)).json() == []
            assert (await http.get(f"{older_task}/{task['id']}")).status_code == 404
            [trace] = await observer.list_traces()
            receipt = await observer.get_trace(trace["id"])
            assert receipt["operation_name"] == "workspace.documents"
            assert PRIVATE_FACT not in json.dumps(receipt, ensure_ascii=False)
            assert "联调手册.md" not in json.dumps(receipt, ensure_ascii=False)

            class FailingModel(EvidenceModel):
                async def stream(self, messages, tools, *, tool_choice="auto"):
                    async for chunk in super().stream(messages, tools, tool_choice=tool_choice):
                        if chunk.text:
                            raise RuntimeError(PRIVATE_FACT)
                        yield chunk

            monkeypatch.setattr(
                conversations, "build_conversation_model", lambda: FailingModel.text("回复"),
            )
            failed = await http.post(RUNS, json=body, headers=OWNER)
            assert failed.status_code == 201
            await manager.get(failed.json()["run_id"]).task
            failure = await http.get(failed.json()["events_url"], headers=OWNER)
            assert "资料问答模型请求失败" in failure.text and PRIVATE_FACT not in failure.text
            for trace in await observer.list_traces():
                receipt = await observer.get_trace(trace["id"])
                assert PRIVATE_FACT not in json.dumps(receipt, ensure_ascii=False)

            assert (await http.delete(f"{DOCS}/{library_id}", headers=OWNER)).status_code == 204
            assert (await http.get(download_url, headers=OWNER)).status_code == 404
    finally:
        await manager.shutdown()
        await observer.shutdown()
        await database.shutdown()
