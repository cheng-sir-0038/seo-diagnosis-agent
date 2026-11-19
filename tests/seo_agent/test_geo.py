from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.agent.loop import AgentLoop
from src.agent.models import RunStatus
from src.agent.observability import AgentRunObserver
from src.agent.providers.base import ModelChunk, ToolCall
from src.agent.providers.fake import FakeModelProvider
from src.agent.run_manager import RunManager
from src.models.common import Capability
from src.models.geo import GeoCitationRequest, GeoContentDraft, GeoQuestionSelection
from src.observability.client import ObservabilityClient
from src.observability.exporters import OpenTelemetryExporter
from src.observability.storage import ObservabilityDatabase
from src.seo_agent.chat.tools import build_seo_agent_registry
from src.seo_agent.geo_a2a import LOCAL_URL, GeoCitationClient, GeoPeerError
from src.seo_agent.geo_search import DeepSeekGeoSearch, OpenAIGeoSearch
from src.seo_agent.workflows.structured_model import StructuredModelRunner

SITE = {"id": "site-1", "domain": "example.com", "default_country": "US", "default_language": "en"}
QUESTION = "How do I store roasted coffee?"
EXCERPT = "Our coffee is packed in a resealable bag."
ANSWER = "Keep roasted coffee in a sealed container."
CITATION = {
    "url": "https://learn.example.com/guide",
    "title": "Storage guide",
    "start_index": 0,
    "end_index": len(ANSWER),
}


def deepseek_body(*, cite=True, search_status="completed"):
    return {
        "id": "deepseek-sample",
        "model": "deepseek-flash",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": ANSWER,
                    "annotations": [{"type": "url_citation", "url_citation": CITATION}]
                    if cite
                    else [],
                    "server_tool_calls": [
                        {
                            "calls": [
                                {
                                    "type": "deepseek_web_search",
                                    "status": search_status,
                                    "action": {
                                        "type": "search",
                                        "queries": ["roasted coffee storage"],
                                    },
                                    "sources": [{"url": "https://example.com/found-but-not-cited"}],
                                }
                            ]
                        }
                    ]
                    if search_status
                    else [],
                },
            }
        ],
        "usage": {"prompt_tokens": 42, "completion_tokens": 12, "total_tokens": 54},
    }


def openai_body():
    return {
        "id": "openai-sample",
        "status": "completed",
        "model": "gpt-6.2-astra",
        "output": [
            {
                "type": "web_search_call",
                "status": "completed",
                "action": {
                    "type": "search",
                    "query": "roasted coffee storage",
                },
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": ANSWER,
                        "annotations": [{"type": "url_citation", **CITATION}],
                    },
                ],
            },
        ],
    }


@pytest.mark.parametrize("provider", ["deepseek", "openai"])
async def test_a2a_verifies_only_answer_citations_using_the_requested_adapter(provider):
    calls = []

    def serve(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json=deepseek_body() if provider == "deepseek" else openai_body()
        )

    transport = httpx.MockTransport(serve)
    client = GeoCitationClient(
        LOCAL_URL,
        3,
        adapters={
            "deepseek": DeepSeekGeoSearch(
                "ds", "https://api.deepseek.com", "deepseek-flash", 3, transport=transport
            ),
            "openai": OpenAIGeoSearch(
                "oa", "https://api.openai.com/v1", "gpt-6.2-astra", 3, transport=transport
            ),
        },
    )
    request = GeoCitationRequest(question=QUESTION, target_domain="example.com", provider=provider)
    report, task_id = await client.verify(request)
    assert task_id and report.request == request and report.citation_status == "cited"
    assert report.matching_urls == [CITATION["url"]]
    assert [citation.answer_excerpt for citation in report.sample.citations] == [ANSWER]
    assert report.sample.queries == ["roasted coffee storage"]
    [upstream] = calls
    body = json.loads(upstream.content)
    if provider == "deepseek":
        assert (
            upstream.url.path == "/chat/completions"
            and upstream.headers["Authorization"] == "Bearer ds"
        )
        assert body["model"] == "deepseek-flash"
        assert body["tools"] == [{"type": "deepseek_web_search"}]
        assert body["messages"][-1]["content"] == QUESTION
    else:
        assert (
            upstream.url.path == "/v1/responses"
            and upstream.headers["Authorization"] == "Bearer oa"
        )
        assert body["tools"] == [{"type": "web_search"}] and body["input"] == QUESTION
    assert "example.com" not in upstream.content.decode()


@pytest.mark.parametrize(
    ("cite", "search_status", "status"),
    [
        (False, "completed", "no_citations"),
        (False, None, "not_searched"),
        (False, "failed", "search_failed"),
        (True, "completed", "not_cited"),
    ],
)
async def test_citation_absence_and_search_failure_are_distinct(cite, search_status, status):
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json=deepseek_body(cite=cite, search_status=search_status))
    )
    client = GeoCitationClient(
        LOCAL_URL,
        3,
        adapters={
            "deepseek": DeepSeekGeoSearch(
                "ds", "https://api.deepseek.com", "deepseek-flash", 3, transport=transport
            ),
        },
    )
    report, _ = await client.verify(
        GeoCitationRequest(
            question=QUESTION,
            target_domain="other-example.com",
        )
    )
    assert report.citation_status == status


async def test_unconfigured_provider_does_not_silently_fallback():
    def unexpected(_: httpx.Request) -> httpx.Response:
        pytest.fail("The unrequested provider must not be contacted")

    client = GeoCitationClient(
        LOCAL_URL,
        3,
        adapters={
            "deepseek": DeepSeekGeoSearch(
                "ds",
                "https://api.deepseek.com",
                "deepseek-flash",
                3,
                transport=httpx.MockTransport(unexpected),
            ),
        },
    )
    with pytest.raises(GeoPeerError, match="NOT_CONFIGURED"):
        await client.verify(
            GeoCitationRequest(
                question=QUESTION,
                target_domain="example.com",
                provider="openai",
            )
        )


class EvidenceSource:
    def __init__(self, with_search=True):
        self.with_search = with_search
        self.calls: list[Capability] = []

    def live_capabilities(self) -> set[Capability]:
        return (
            {Capability.PAGE_METADATA, Capability.AUTOCOMPLETE, Capability.SERP_OVERVIEW}
            if self.with_search
            else {Capability.PAGE_METADATA}
        )

    async def query_live(self, *, capability, params, **kwargs):
        self.calls.append(capability)
        source = "page_html" if capability == Capability.PAGE_METADATA else "serper"
        data: Any
        if capability == Capability.PAGE_METADATA:
            data = {
                "source": source,
                "status_code": 200,
                "requested_url": params["url"],
                "final_url": params["url"],
                "title": "Coffee",
                "content_length_bytes": 200,
                "content_excerpt": EXCERPT,
            }
        elif capability == Capability.SERP_OVERVIEW:
            assert params["result_types"] == ["people_also_ask"]
            assert params["preferred_provider"] == "serper" and params["allow_fallback"] is False
            data = [
                {"type": "organic", "title": "Can I choose this unrelated organic result?"},
                {"type": "people_also_ask", "title": QUESTION},
            ]
        elif capability == Capability.AUTOCOMPLETE:
            data = [{"value": QUESTION.removesuffix("?")}, {"value": "coffee storage containers"}]
        else:
            raise AssertionError(capability)
        return {"meta": {"source": source}, "data": data}


class ModelAnswers:
    def __init__(self, question_id=1, invalid_edit=False):
        self.calls: list[tuple[type, dict]] = []
        self.question_id, self.invalid_edit = question_id, invalid_edit

    def with_structured_output(self, schema, **kwargs):
        async def invoke(messages):
            self.calls.append((schema, json.loads(messages[-1].content)))
            if schema is GeoQuestionSelection:
                return {"question_id": self.question_id, "reason": "Readers need storing advice."}
            assert schema is GeoContentDraft
            return {
                "edits": [
                    {
                        "gap": "The page does not explain the packaging.",
                        "citation_id": 1,
                        "page_quote": "fabricated quote" if self.invalid_edit else EXCERPT,
                        "draft": "The package is resealable.",
                    }
                ],
                "needs_verification": [],
            }

        return type("Runnable", (), {"ainvoke": staticmethod(invoke)})()


async def test_geo_chain_hands_off_evidence_and_records_the_real_a2a_task(tmp_path):
    database = ObservabilityDatabase(tmp_path / "obs.db")
    exported = InMemorySpanExporter()
    observer = ObservabilityClient(
        database,
        project_key="seo-agent",
        exporters=[
            OpenTelemetryExporter(
                service_name="seo-agent",
                endpoint=None,
                database=database,
                span_exporter=exported,
            ),
        ],
    )
    replies, source = ModelAnswers(), EvidenceSource()
    runner = StructuredModelRunner(model_factory=lambda: replies, observability=observer)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=deepseek_body()))
    peer = GeoCitationClient(
        LOCAL_URL,
        3,
        observability=observer,
        adapters={
            "deepseek": DeepSeekGeoSearch(
                "ds",
                "https://api.deepseek.com",
                "deepseek-flash",
                3,
                transport=transport,
                observability=observer,
            ),
        },
    )
    registry = build_seo_agent_registry(
        source,
        SITE,
        model_runner=runner,
        geo_citation_client=peer,
        observability=observer,
    )
    provider = FakeModelProvider(
        [
            [
                ModelChunk(
                    tool_calls=[
                        ToolCall(
                            id="geo",
                            name="seo_geo_content",
                            arguments={"topic": "coffee storage"},
                        )
                    ],
                    finish_reason="tool_calls",
                )
            ],
            [ModelChunk(text="Reviewed."), ModelChunk(finish_reason="stop")],
        ]
    )
    run = RunManager().create_run([{"role": "user", "content": "Investigate coffee storage."}])
    try:
        await AgentLoop(
            provider,
            registry,
            observer=AgentRunObserver(observer, operation_name="seo_agent.chat"),
        ).run(run)
        assert run.status == RunStatus.COMPLETED
        (event,) = [event for event in run.events if event.type == "tool.completed"]
        data = event.payload["result"]["data"]
        assert data["workflow_status"] == "complete"
        assert data["question"] == QUESTION and len(data["edits"]) == 1
        assert data["evidence"]["research"]["questions"] == [
            {
                "id": 1,
                "question": QUESTION,
                "sources": ["Google 相关问题", "Google 联想词"],
            }
        ]
        assert source.calls.count(Capability.PAGE_METADATA) == 1
        assert [schema for schema, _ in replies.calls] == [GeoQuestionSelection, GeoContentDraft]
        _, edit_input = replies.calls[-1]
        assert edit_input["question"] == QUESTION and edit_input["page"]["excerpt"] == EXCERPT
        assert edit_input["citation_evidence"] == [
            {
                "id": 1,
                "url": CITATION["url"],
                "title": CITATION["title"],
                "answer_excerpt": ANSWER,
            }
        ]
        spans = {
            row["operation_name"]: row
            for row in await database.fetch_all("SELECT * FROM obs_spans")
        }
        task_id = data["evidence"]["citations"]["a2a_task_id"]
        assert task_id in spans["geo_a2a_handoff"]["output_json"]
        for child, parent in (
            ("geo_question_researcher", "seo_geo_questions"),
            ("seo.geo.question_researcher", "geo_question_researcher"),
            ("seo_geo_questions", "seo_geo_content"),
            ("seo_geo_citations", "seo_geo_content"),
            ("geo_a2a_handoff", "seo_geo_citations"),
            ("geo_citation_verifier", "geo_a2a_handoff"),
            ("geo_web_search", "geo_citation_verifier"),
            ("geo_content_editor", "seo_geo_content"),
            ("seo.geo.content_editor", "geo_content_editor"),
        ):
            assert spans[child]["parent_span_id"] == spans[parent]["id"]
            assert spans[child]["trace_id"] == spans[parent]["trace_id"]
        observations = {span.name: span.attributes for span in exported.get_finished_spans()}
        for agent in ("geo_question_researcher", "geo_citation_verifier", "geo_content_editor"):
            assert observations[agent]["langfuse.observation.type"] == "agent"
        handoff = observations["geo_a2a_handoff"]
        peer_span = observations["geo_citation_verifier"]
        key = "langfuse.observation.metadata.a2a_message_id"
        assert handoff[key] == peer_span[key]
        assert peer_span["langfuse.observation.metadata.a2a_task_id"] == task_id
        assert any(
            span.attributes.get("langfuse.observation.type") == "generation"
            and span.name == "geo_web_search"
            for span in exported.get_finished_spans()
        )
    finally:
        await observer.shutdown()


@pytest.mark.parametrize("failure", ["unobserved_question", "no_citations", "invalid_edit"])
async def test_geo_keeps_prior_evidence_when_handoff_cannot_justify_content(failure):
    replies = ModelAnswers(
        question_id=99 if failure == "unobserved_question" else 1,
        invalid_edit=failure == "invalid_edit",
    )
    use_topic = failure == "unobserved_question"
    source = EvidenceSource(with_search=use_topic)
    upstream_questions = []

    def serve(request):
        upstream_questions.append(request)
        return httpx.Response(200, json=deepseek_body(cite=failure != "no_citations"))

    peer = GeoCitationClient(
        LOCAL_URL,
        3,
        adapters={
            "deepseek": DeepSeekGeoSearch(
                "ds",
                "https://api.deepseek.com",
                "deepseek-flash",
                3,
                transport=httpx.MockTransport(serve),
            ),
        },
    )
    registry = build_seo_agent_registry(
        source,
        SITE,
        geo_citation_client=peer,
        model_runner=StructuredModelRunner(model_factory=lambda: replies),
    )
    if not use_topic:
        assert "seo_geo_questions" not in registry.names()
    result = await registry.get("seo_geo_content").execute(
        {"topic": "coffee storage"} if use_topic else {"question": QUESTION},
    )
    assert result.success and result.data["workflow_status"] == "partial"
    assert result.data["edits"] == []
    assert source.calls.count(Capability.PAGE_METADATA) == 1
    assert len(upstream_questions) == int(not use_topic)
    if failure == "unobserved_question":
        assert result.data["evidence"]["research"]["coverage"]["question_researcher"][
            "error_code"
        ] == ("GEO_UNOBSERVED_QUESTION")
    else:
        assert result.data["evidence"]["citations"]["citation_status"] == (
            "no_citations" if failure == "no_citations" else "cited"
        )
        assert len(replies.calls) == (1 if failure == "invalid_edit" else 0)


async def test_openai_authentication_error_survives_the_a2a_handoff():
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            401,
            json={
                "error": {"message": "Provider rejected the API key"},
            },
        )
    )
    client = GeoCitationClient(
        LOCAL_URL,
        3,
        adapters={
            "openai": OpenAIGeoSearch(
                "oa",
                "https://api.openai.com/v1",
                "gpt-6.2-astra",
                3,
                transport=transport,
            ),
        },
    )
    with pytest.raises(GeoPeerError, match="AUTH_FAILED"):
        await client.verify(
            GeoCitationRequest(
                question=QUESTION,
                target_domain="example.com",
                provider="openai",
            )
        )
