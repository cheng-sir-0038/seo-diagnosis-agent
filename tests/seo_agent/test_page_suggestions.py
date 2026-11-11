from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.models.common import Capability
from src.providers.page_metadata.client import FetchedHtmlPage
from src.providers.page_metadata.provider import PageMetadataProvider
from src.seo_agent.chat.tools import build_seo_agent_registry
from src.seo_agent.workflows.structured_model import StructuredModelRunner

SITE = {"id": "site-suggest", "domain": "example.com"}


class PageSource:
    def __init__(self):
        self.html = (
            '<html lang="en"><title>Field notes</title>'
            "<body><h1>Field notes</h1><p>By Lin.</p></body>"
        )
        self.status = 200
        self.calls = []
        self.provider = PageMetadataProvider(client=self, analyzer=SimpleNamespace(enabled=False))

    def live_capabilities(self):
        return {Capability.PAGE_METADATA}

    async def fetch(self, *, site_id, url):
        return FetchedHtmlPage(
            requested_url=url,
            final_url=url,
            status_code=self.status,
            content_type="text/html",
            content_length_bytes=len(self.html),
            headers={},
            html=self.html,
        )

    async def query_live(self, **kwargs):
        self.calls.append(kwargs)
        page = await self.provider.get_page_metadata(kwargs["site_id"], **kwargs["params"])
        return {
            "meta": {"source": "page_html", "capability": "page_metadata"},
            "data": page.model_dump(mode="json"),
        }


def draft(schema_type="Article"):
    return {
        "schema_type": schema_type,
        "page_purpose": "A collection of field notes.",
        "title": "Field notes",
        "description": "Read the field notes.",
        "keywords": ["field notes"],
        "citations": [{"source_path": "/h1s/0", "quote": "Field notes"}],
        "json_ld": (
            {
                "@context": "https://schema.org",
                "@type": schema_type,
                "name": "Field notes",
                "url": "https://example.com/",
                "inLanguage": "en",
            }
        ),
        "json_ld_citations": [],
        "missing_information": [],
        "questions": [],
    }


class Model:
    provider_name = "fixture"
    model = "structured-fixture"

    def __init__(self, output=None):
        self.output = draft() if output is None else output
        self.contexts = []
        self.methods = []
        self.delay = 0
        self.fail = False

    def with_structured_output(self, schema, **kwargs):
        self.methods.append(kwargs.get("method"))

        async def ainvoke(messages):
            self.contexts.append(json.loads(messages[-1].content))
            await asyncio.sleep(self.delay)
            if self.fail:
                raise RuntimeError("private upstream failure")
            return schema.model_validate(self.output)

        return SimpleNamespace(ainvoke=ainvoke)


def workflow(source, model, **kwargs):
    runner = StructuredModelRunner(
        model_factory=lambda: model, observability=kwargs.pop("observability", None)
    )
    return build_seo_agent_registry(source, SITE, model_runner=runner, **kwargs).get(
        "seo_page_suggestions"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema_type", ["Article", "CollectionPage", "Recipe", "SoftwareApplication"]
)
async def test_suggestions_adapt_to_content_without_writing_pages_or_business_data(schema_type):
    source = PageSource()
    model = Model(draft(schema_type))
    before = source.html
    result = await workflow(source, model).execute({"force_refresh": True})
    assert result.success and result.data["suggestion_status"] == "draft"
    proposal = result.data["proposal"]
    assert proposal["json_ld"]["@type"] == schema_type
    assert proposal["requires_human_review"] is True
    assert "og:image" not in proposal["open_graph"]
    assert "缺少已确认" in "".join(proposal["missing_information"])
    assert "<title>Field notes</title>" in proposal["html_snippet"]
    assert "Organization" not in proposal["html_snippet"]
    assert model.contexts[0]["page"]["content_excerpt"] == "Field notes By Lin."
    assert len(source.calls) == 1 and source.calls[0]["force_refresh"] is True
    assert source.html == before
    assert model.methods == ["json_mode"]


@pytest.mark.asyncio
async def test_missing_information_asks_a_question_then_uses_real_user_reply_as_separate_source():
    source = PageSource()
    first = draft()
    first.update(schema_type=None, json_ld=None, questions=["这个页面主要提供什么内容？"])
    model = Model(first)
    result = await workflow(source, model, user_messages=["帮我拟定页面 SEO 信息"]).execute({})
    assert result.data["suggestion_status"] == "awaiting_user_input"
    assert result.data["questions"] == first["questions"]
    assert result.data["proposal"]["json_ld"] is None
    assert "application/ld+json" not in result.data["proposal"]["html_snippet"]
    reply = "这里是野外观察笔记，分享图片用 https://images.example/notes.jpg"
    second = draft("CollectionPage")
    second.update(
        image_url="https://images.example/notes.jpg",
        image_citation={"source_path": "/user_supplied/1", "quote": reply},
    )
    second["citations"].append({"source_path": "/user_supplied/1", "quote": reply})
    model.output = second
    followup = await workflow(
        source, model, user_messages=["帮我拟定页面 SEO 信息", reply]
    ).execute({})
    assert followup.data["suggestion_status"] == "draft" and not followup.data["questions"]
    assert followup.data["proposal"]["open_graph"]["og:image"] == "https://images.example/notes.jpg"
    assert any("用户补充" in value for value in followup.data["proposal"]["missing_information"])
    assert followup.data["evidence"]["page"]["raw"]["data"]["og_image"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem",
    [
        "invented_quote",
        "foreign_path",
        "invented_price",
        "hidden_json_fact",
        "wrong_url",
        "invalid_json",
        "remote_context",
        "mismatched_type",
        "extra_citation",
        "false_user_input",
    ],
)
async def test_rejects_untrusted_suggestions_and_keeps_page_evidence(problem):
    output = draft()
    document = dict(output["json_ld"])
    if problem == "invented_quote":
        output["citations"][0]["quote"] = "not on the page"
    elif problem == "foreign_path":
        output["citations"][0]["source_path"] = "/unknown"
    elif problem in {"invented_price", "hidden_json_fact"}:
        document["offers"] = {"@type": "Offer", "price": 99}
        if problem == "hidden_json_fact":
            output["json_ld_citations"] = [
                {
                    "json_pointer": "/offers/price",
                    "source_path": "/existing_json_ld/0/price",
                    "quote": "99",
                }
            ]
    elif problem == "wrong_url":
        document["url"] = "https://foreign.example/"
    elif problem == "remote_context":
        document["@context"] = "https://untrusted.example/schema"
    elif problem == "mismatched_type":
        document["@type"] = "Product"
    elif problem == "extra_citation":
        output["json_ld_citations"] = [{"json_pointer": "/missing", **output["citations"][0]}]
    elif problem == "false_user_input":
        output.update(
            image_url="https://images.example/invented.jpg",
            image_citation={
                "source_path": "/user_supplied/0",
                "quote": "https://images.example/invented.jpg",
            },
        )
    output["json_ld"] = "broken" if problem == "invalid_json" else document
    source, model = PageSource(), Model(output)
    result = await workflow(source, model, user_messages=["给当前页面建议"]).execute({})
    assert result.data["workflow_status"] == "partial"
    assert result.data["proposal"] is None
    assert result.data["suggestion_status"] == "needs_review"
    assert result.data["evidence"]["page"]["raw"]["data"]["title"] == "Field notes"


@pytest.mark.asyncio
async def test_only_includes_optional_author_when_exactly_supported():
    output = draft()
    document = dict(output["json_ld"])
    document["author"] = {"@type": "Person", "name": "Lin"}
    output["json_ld"] = document
    output["json_ld_citations"] = [
        {
            "json_pointer": "/author/name",
            "source_path": "/content_excerpt",
            "quote": "By Lin.",
        }
    ]
    result = await workflow(PageSource(), Model(output)).execute({})
    assert result.data["proposal"]["json_ld"]["author"]["name"] == "Lin"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,html", [(404, "<h1>Error</h1>"), (200, "<body></body>")])
async def test_no_page_body_asks_for_source_instead_of_inventing_suggestions(status, html):
    source, model = PageSource(), Model()
    source.status, source.html = status, html
    result = await workflow(source, model).execute({})
    assert result.data["proposal"] is None and result.data["questions"]
    assert not model.contexts


@pytest.mark.asyncio
async def test_timeout_and_model_failure_do_not_expose_upstream_messages_or_discard_facts():
    source, model = PageSource(), Model()
    model.fail = True
    tool = workflow(source, model)
    failed = await tool.execute({})
    assert failed.data["suggestion_status"] == "needs_review"
    assert "private upstream failure" not in str(failed)
    model.fail, model.delay, tool.timeout = False, 0.2, 0.03
    timed_out = await tool.execute({})
    assert timed_out.data["coverage"]["draft"]["status"] == "failed"
    assert timed_out.data["proposal"] is None


@pytest.mark.asyncio
async def test_markup_is_escaped_and_canonical_is_observed_without_rewrite():
    source = PageSource()
    source.html += '<link rel="canonical" href="/canonical">'
    output = draft()
    output["title"] = "</title><script>alert(1)</script>"
    document = dict(output["json_ld"])
    document.update(name=output["title"], url="https://example.com/canonical")
    output["json_ld"] = document
    result = await workflow(source, Model(output)).execute({})
    proposal = result.data["proposal"]
    assert proposal["canonical_observed"] == "https://example.com/canonical"
    assert '<link rel="canonical"' not in proposal["html_snippet"]
    assert "&lt;/title&gt;" in proposal["html_snippet"]
    assert "\\u003cscript>" in proposal["html_snippet"]
    assert proposal["html_snippet"].count("</script>") == 1


@pytest.mark.asyncio
async def test_foreign_url_and_unknown_parameters_are_rejected_before_model():
    source, model = PageSource(), Model()
    tool = workflow(source, model)
    foreign = await tool.execute({"url": "https://foreign.example/"})
    assert not foreign.success and foreign.data["proposal"] is None
    with pytest.raises(ValueError):
        await tool.execute({"auto_publish": True})
    assert not source.calls and not model.contexts


def test_registry_requires_both_page_data_and_model_runner():
    assert "seo_page_suggestions" not in build_seo_agent_registry(PageSource(), SITE).names()


@pytest.mark.asyncio
async def test_chat_sse_followup_and_restored_run_keep_user_facts_and_observation_tree(
    tmp_path, monkeypatch
):
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

    db = SeoAgentDatabase(tmp_path / "business.db")
    await db.startup()
    system = SeoAgentSystemService(db)
    site = await system.create_site(
        name="Field notes",
        domain="example.com",
        default_country="US",
        default_language="en",
        timezone="UTC",
    )
    observations = ObservabilityDatabase(tmp_path / "observations.db")
    memory = InMemorySpanExporter()
    exporter = OpenTelemetryExporter(
        service_name="test", endpoint=None, span_exporter=memory, database=observations
    )
    observer = ObservabilityClient(observations, project_key="test", exporters=[exporter])
    await observer.startup()
    source, model, manager = PageSource(), Model(), RunManager()
    runner = StructuredModelRunner(model_factory=lambda: model, observability=observer)

    providers = []

    def chat_model():
        final = [ModelChunk(text="建议已整理，请人工确认。"), ModelChunk(finish_reason="stop")]
        chunks = (
            [final]
            if providers
            else [
                [
                    ModelChunk(
                        tool_calls=[
                            ToolCall(id="suggest", name="seo_page_suggestions", arguments={})
                        ],
                        finish_reason="tool_calls",
                    )
                ],
                final,
            ]
        )
        provider = FakeModelProvider(chunks)
        providers.append(provider)
        return provider

    for name, value in {
        "facade": source,
        "seo_agent_system_service": system,
        "seo_chat_harness_store": AgentChatHarnessStore(db),
        "run_manager": manager,
        "observability_client": observer,
        "observability_database": observations,
        "build_seo_model_provider": chat_model,
        "structured_model_automation": SimpleNamespace(model_runner=runner),
    }.items():
        monkeypatch.setattr(seo_agent_chat, name, value)
    app = FastAPI()
    app.include_router(seo_agent_chat.router, prefix="/api/v1")
    messages = [{"role": "user", "content": "请根据当前页面内容给我一份 SEO 信息建议。"}]
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            model.output.update(
                schema_type=None, json_ld=None, questions=["这个页面主要提供什么内容？"]
            )
            response = await client.post(
                "/api/v1/seo-agent/chat/runs",
                json={
                    "site_id": site["id"],
                    "messages": messages,
                },
            )
            assert response.status_code == 201
            run_id = response.json()["run_id"]
            first_events = await client.get(response.json()["events_url"])
            payloads = [
                json.loads(line[6:])
                for line in first_events.text.splitlines()
                if line.startswith("data: ")
            ]
            waiting = next(item for item in payloads if item["type"] == "run.waiting_for_input")
            request_id = waiting["payload"]["request_id"]
            assert manager.get(run_id).status.value == "waiting_for_input"
            assert len(model.contexts) == 1 and len(source.calls) == 1
            assert run_id not in await seo_agent_chat.seo_chat_harness_store.list_recoverable_runs()
            assert (
                await client.post(f"/api/v1/seo-agent/chat/runs/{run_id}/resume")
            ).status_code == 409
            await manager.shutdown()
            manager = RunManager()
            monkeypatch.setattr(seo_agent_chat, "run_manager", manager)
            assert (await client.get(f"/api/v1/seo-agent/chat/runs/{run_id}")).json()[
                "status"
            ] == "WAITING_FOR_INPUT"
            endpoint = f"/api/v1/seo-agent/chat/runs/{run_id}/input"
            assert (
                await client.post(endpoint, json={"request_id": "stale", "answer": "text"})
            ).status_code == 409
            assert (
                await client.post(endpoint, json={"request_id": request_id, "answer": "  "})
            ).status_code == 422
            original_save = AgentChatHarnessStore.save_checkpoint

            async def fail_input_save(self, run, **kwargs):
                if kwargs.get("stage") == "input_submitted":
                    raise RuntimeError("checkpoint fixture failure")
                return await original_save(self, run, **kwargs)

            monkeypatch.setattr(AgentChatHarnessStore, "save_checkpoint", fail_input_save)
            with pytest.raises(RuntimeError, match="checkpoint fixture failure"):
                await client.post(
                    endpoint, json={"request_id": request_id, "answer": "这里汇总野外观察笔记。"}
                )
            monkeypatch.setattr(AgentChatHarnessStore, "save_checkpoint", original_save)
            saved = await seo_agent_chat.seo_chat_harness_store.get_run(run_id)
            assert saved["status"] == "WAITING_FOR_INPUT"
            assert not any(item["event_type"] == "input.submitted" for item in saved["events"])
            model.output = draft("CollectionPage")
            model.output["citations"].append(
                {
                    "source_path": "/user_supplied/1",
                    "quote": "这里汇总野外观察笔记。",
                }
            )
            answer = {"request_id": request_id, "answer": "这里汇总野外观察笔记。"}
            accepted, duplicate = await asyncio.gather(
                client.post(endpoint, json=answer), client.post(endpoint, json=answer)
            )
            assert accepted.status_code == duplicate.status_code == 200
            assert sorted([accepted.json()["replayed"], duplicate.json()["replayed"]]) == [
                False,
                True,
            ]
            response = accepted if not accepted.json()["replayed"] else duplicate
            assert response.json()["run_id"] == run_id
            events = await client.get(
                response.json()["events_url"],
                params={"after_sequence": response.json()["after_sequence"]},
            )
            completed = next(
                json.loads(line[6:])
                for line in events.text.splitlines()
                if line.startswith("data: ") and json.loads(line[6:])["type"] == "tool.completed"
            )
            assert completed["payload"]["result"]["data"]["suggestion_status"] == "draft"
            assert manager.get(run_id).status.value == "completed"
            assert len(source.calls) == 1
            assert len(model.contexts) == 2
            assert providers[1].calls == 1
            assert (
                await client.post(endpoint, json={**answer, "answer": "different"})
            ).status_code == 409
            assert await db.fetch_value("SELECT COUNT(*) FROM agent_runtime_tasks") == 1
            messages.append({"role": "user", "content": answer["answer"]})
            assert model.contexts[-1]["page"]["user_supplied"] == [
                message["content"] for message in messages if message["role"] == "user"
            ]
            restored = manager.get(response.json()["run_id"])
            captured = []

            async def restored_execute(run_id, provider, registry, conversation_id):
                captured.extend(registry.get("seo_page_suggestions").user_messages)

            monkeypatch.setattr(seo_agent_chat, "_execute", restored_execute)
            await seo_agent_chat._schedule_restored_run(restored)
            await restored.task
            assert captured == model.contexts[-1]["page"]["user_supplied"]
            pending = await seo_agent_chat.seo_chat_harness_store.restore_run(
                run_id, mark_recovering=False
            )
            from src.agent.models import HumanInputState, RunStatus

            pending.status = RunStatus.WAITING_FOR_INPUT
            pending.human_input = HumanInputState(
                questions=["补充内容？"],
                request_id="cancel-pending",
                tool_name="seo_page_suggestions",
                tool_arguments={},
                tool_result={},
            )
            await seo_agent_chat.seo_chat_harness_store.save_checkpoint(
                pending,
                messages=pending.messages,
                iteration=pending.iteration,
                tool_count=pending.tool_count,
                pending_tool_calls=[],
                stage="waiting_for_input",
                token_usage=pending.token_usage,
            )
            manager = RunManager()
            monkeypatch.setattr(seo_agent_chat, "run_manager", manager)
            cancelled = await client.post(f"/api/v1/seo-agent/chat/runs/{run_id}/cancel")
            assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
            assert (
                await client.post(endpoint, json={"request_id": "cancel-pending", "answer": "late"})
            ).status_code == 409
        traces = await observations.fetch_all("SELECT * FROM obs_traces")
        assert len(traces) == 2
        spans = await observations.fetch_all("SELECT * FROM obs_spans")
        for trace in traces:
            names = {row["operation_name"] for row in spans if row["trace_id"] == trace["id"]}
            assert {
                "seo_page_suggestions",
                "seo.page.suggestions",
                "validate_page_suggestions",
            } <= names
        names = {row["operation_name"] for row in spans}
        assert {
            "seo_page_metadata",
            "restore_page_suggestion_facts",
            "wait_for_user_input",
            "human_input_received",
        } <= names
        calls = await observations.fetch_all(
            "SELECT * FROM obs_model_calls WHERE prompt_key='seo.page.suggestions'"
        )
        assert len(calls) == 2
        assert "这里汇总野外观察笔记" in calls[1]["input_summary_json"]
        await exporter.force_flush()
        validated = [
            span for span in memory.get_finished_spans() if span.name == "validate_page_suggestions"
        ]
        assert all(span.parent is not None for span in validated)
        assert "CollectionPage" in validated[-1].attributes["langfuse.observation.output"]
        for table in ("approval_actions", "execution_tasks", "publish_records"):
            assert await db.fetch_value(f"SELECT COUNT(*) FROM {table}") == 0
    finally:
        await manager.shutdown()
        await observer.shutdown()
        await db.shutdown()


@pytest.mark.asyncio
async def test_json_schema_requires_an_object_and_failure_observations_keep_response_and_tokens(
    tmp_path,
):
    from langchain_core.messages import AIMessage

    from src.models.page_suggestions import PageSeoDraft, PageSuggestionInput
    from src.observability.client import ObservabilityClient
    from src.observability.storage import ObservabilityDatabase

    assert PageSeoDraft.model_json_schema()["properties"]["json_ld"]["anyOf"][0]["$ref"].endswith(
        "/JsonLdDraft"
    )
    assert PageSuggestionInput.model_json_schema()["properties"]["page"]["$ref"].endswith(
        "/PageSuggestionEvidence"
    )
    observer = ObservabilityClient(
        ObservabilityDatabase(tmp_path / "observations.db"), project_key="test"
    )
    await observer.startup()
    raw = AIMessage(
        content="",
        invalid_tool_calls=[
            {"name": "PageSeoDraft", "args": "invalid-json", "id": "call-1", "error": None}
        ],
        usage_metadata={"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
    )

    class MalformedModel:
        def with_structured_output(self, schema, **kwargs):
            async def ainvoke(messages):
                return {"raw": raw, "parsed": None, "parsing_error": None}

            return SimpleNamespace(ainvoke=ainvoke)

    try:
        runner = StructuredModelRunner(model_factory=MalformedModel, observability=observer)
        with pytest.raises(ValueError, match="可解析"):
            await runner.invoke(
                prompt_key="seo.page.suggestions",
                schema=PageSeoDraft,
                context={},
                operation="test",
                business_object={},
            )
        call = await observer.database.fetch_one("SELECT * FROM obs_model_calls")
        assert call["status"] == "FAILED" and call["total_tokens"] == 25
        assert "invalid-json" in call["output_summary_json"]
    finally:
        await observer.shutdown()


@pytest.mark.asyncio
async def test_user_can_supply_missing_body_and_continue_without_refetch():
    from src.agent.tools.base import ToolRuntimeContext

    source = PageSource()
    source.html = "<title>Field notes</title><body></body>"
    model = Model()
    first = await workflow(source, model).execute({})
    assert first.metadata["human_input"]["questions"]
    reply = "This page is a collection called Field notes."
    model.output = draft("CollectionPage")
    model.output["json_ld"].pop("inLanguage")
    model.output["citations"] = [{"source_path": "/user_supplied/1", "quote": reply}]
    context = ToolRuntimeContext(
        metadata={
            "human_input_resume": {
                "request_id": "request-1",
                "tool_arguments": {},
                "tool_result": first.model_dump(mode="json"),
                "answer": reply,
            }
        }
    )
    continued = await workflow(source, model, user_messages=["Give me suggestions", reply]).execute(
        {}, context
    )
    assert continued.data["suggestion_status"] == "draft"
    assert continued.data["workflow_status"] == "partial"
    assert continued.data["proposal"]["json_ld"]["name"] == "Field notes"
    assert len(source.calls) == 1


@pytest.mark.asyncio
async def test_available_facts_choice_stops_questions_but_keeps_validation():
    from src.agent.tools.base import ToolRuntimeContext

    source, model = PageSource(), Model()
    model.output["questions"] = ["请提供分享图片地址。"]
    first = await workflow(source, model).execute({})
    assert first.metadata["human_input"]["allow_use_available"] is True
    context = ToolRuntimeContext(
        metadata={
            "human_input_resume": {
                "request_id": "request-1",
                "tool_arguments": {},
                "tool_result": first.model_dump(mode="json"),
                "answer": "用现有事实生成",
                "action": "use_available",
            }
        }
    )
    final = await workflow(source, model).execute({}, context)
    assert final.data["suggestion_status"] == "draft" and not final.data["questions"]
    assert not final.metadata.get("human_input")
    assert "og:image" not in final.data["proposal"]["open_graph"]
    assert len(source.calls) == 1
    model.output["citations"][0]["quote"] = "invented"
    invalid = await workflow(source, model).execute({}, context)
    assert invalid.data["proposal"] is None


def test_observation_preserves_nested_tool_schema_and_still_redacts_credentials():
    from src.observability.sanitizer import observation_json
    from src.seo_agent.workflows.page_change_review import PageChangeReviewWorkflow

    payload = {
        "messages": [],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "seo_page_change_review",
                    "parameters": PageChangeReviewWorkflow.parameters_schema,
                },
            }
        ],
    }
    assert json.loads(observation_json(payload)) == payload
    assert "fake-secret-for-test" not in observation_json({"api_key": "fake-secret-for-test"})
