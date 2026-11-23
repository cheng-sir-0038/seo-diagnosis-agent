"""Exercise workspace turns through HTTP/SSE, with PostgreSQL checkpoints and real tools."""

from __future__ import annotations

import importlib
import json
from collections import deque
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.agent.harness.store import AgentChatHarnessStore
from src.agent.providers.base import ModelChunk, ModelProvider, ToolCall
from src.agent.run_manager import RunManager
from src.api.routes.workspace import conversations as chat
from src.core.cache import CacheManager
from src.facade.data_facade import DataFacade
from src.healthcare_agent.postgraduate import POSTGRADUATE_TOOL, seed_postgraduate_corpus
from src.models.common import Capability
from src.observability.client import ObservabilityClient
from src.observability.storage import ObservabilityDatabase
from src.providers.page_metadata.client import FetchedHtmlPage
from src.providers.page_metadata.provider import PageMetadataProvider
from src.providers.page_metadata.pyseo_client import (
    PythonSeoAnalyzerClient,
    PythonSeoAnalyzerConfig,
)
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.services.system import SeoAgentSystemService

GUIDE = "https://alpha.example/guides/start"
NEW_PAGE = "https://alpha.example/courses/next"
OTHER = "https://bravo.example/other"
REFERENCE = "https://reference.example/policy"
COURSES = Path(__file__).parents[1] / "fixtures" / "healthcare"
SYLLABUS = "https://www1.nmec.org.cn/Pages/ArticleInfo-4-11903.html"


class Models(ModelProvider):
    """Replace both model APIs while recording what each recipient actually sees."""

    def __init__(self):
        self.decisions = deque()
        self.replies = deque()
        self.planner_inputs = []
        self.specialist_inputs = []

    async def invoke(self, *, schema, context, **_):
        self.planner_inputs.append(deepcopy(context))
        route, index = self.decisions.popleft()
        decision = schema(route=route, needs_page=route != "healthcare", target_index=index)
        return SimpleNamespace(usage={}, output=decision)

    async def stream(self, messages, tools, *, tool_choice="auto"):
        self.specialist_inputs.append(deepcopy(messages))
        reply = self.replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, ToolCall):
            yield ModelChunk(tool_calls=[reply], finish_reason="tool_calls")
        else:
            yield ModelChunk(text=reply, finish_reason="stop")

    def check_page(self):
        self.replies.extend([
            ToolCall("read_page", "seo_page_metadata", {"force_refresh": True}), "已读取页面",
        ])


@pytest.fixture
def workspace(pg_url, monkeypatch):
    models = Models()
    fetched = []

    async def read_external_page(*, site_id, url):
        fetched.append(url)
        course_pages = {
            f"https://courses.example/{name}-course.html": COURSES / f"{name}-course.html"
            for name in ("clinical", "nursing")
        }
        if url in course_pages:
            html = course_pages[url].read_text()
        else:
            assert url in (GUIDE, NEW_PAGE, OTHER)
            html = "<html><title>课程介绍</title><main><h1>课程介绍</h1></main></html>"
        return FetchedHtmlPage(url, url, 200, "text/html", len(html.encode()), {}, html)

    provider = PageMetadataProvider(
        analyzer=PythonSeoAnalyzerClient(PythonSeoAnalyzerConfig(enabled=False))
    )
    monkeypatch.setattr(provider.client, "fetch", read_external_page)
    facade = DataFacade()
    facade._capability_map = {Capability.PAGE_METADATA: [provider]}
    facade_module = importlib.import_module("src.facade.data_facade")
    monkeypatch.setattr(chat, "facade", facade)
    monkeypatch.setattr(chat, "build_conversation_model", lambda: models)
    monkeypatch.setattr(chat.structured_model_automation, "model_runner", models)

    @asynccontextmanager
    async def serve():
        db, cache = SeoAgentDatabase(pg_url), CacheManager(pg_url)
        observer = ObservabilityClient(ObservabilityDatabase(pg_url), project_key="workspace-test")
        manager = RunManager()
        await db.startup()
        await seed_postgraduate_corpus(db)
        await cache.startup()
        await observer.startup()
        monkeypatch.setattr(chat, "run_manager", manager)
        monkeypatch.setattr(chat, "seo_chat_harness_store", AgentChatHarnessStore(db))
        monkeypatch.setattr(chat, "seo_agent_system_service", SeoAgentSystemService(db))
        monkeypatch.setattr(chat, "cache_manager", cache)
        monkeypatch.setattr(facade_module, "cache_manager", cache)
        monkeypatch.setattr(chat, "observability_client", observer)
        monkeypatch.setattr(chat, "observability_database", observer.database)
        app = FastAPI()
        app.include_router(chat.router, prefix="/api/v1")
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
                yield api
        finally:
            await manager.shutdown()
            await observer.shutdown()
            await cache.shutdown()
            await db.shutdown()

    return SimpleNamespace(models=models, serve=serve, fetched=fetched)


async def read_events(api, receipt):
    response = await api.get(receipt["events_url"])
    response.raise_for_status()
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


async def ask(api, question, *, history=(), conversation_id="conversation"):
    response = await api.post("/api/v1/chat/runs", json={
        "messages": [*history, {"role": "user", "content": question}],
        "conversation_id": conversation_id,
    })
    response.raise_for_status()
    receipt = response.json()
    return receipt, await read_events(api, receipt)


def payloads(events, event_type):
    return [event["payload"] for event in events if event["type"] == event_type]


@pytest.mark.asyncio
async def test_missing_page_waits_and_resumes_the_same_run_after_restart(workspace):
    models = workspace.models
    models.decisions.append(("seo", None))
    history = [{"role": "assistant", "content": f"你可以参考 {REFERENCE}"}]
    async with workspace.serve() as api:
        receipt, first = await ask(api, "检查一下这个页面的标题", history=history)
        [waiting] = payloads(first, "run.waiting_for_input")
        assert waiting["questions"] and not payloads(first, "agent.dispatched")
        assert models.planner_inputs[0]["candidate_pages"] == []
        assert REFERENCE not in json.dumps(models.planner_inputs[0], ensure_ascii=False)
        assert workspace.fetched == []

    models.check_page()
    async with workspace.serve() as api:
        input_path = f"/api/v1/chat/runs/{receipt['run_id']}/input"
        answer = {"request_id": waiting["request_id"], "answer": GUIDE}
        response = await api.post(input_path, json=answer)
        response.raise_for_status()
        resumed = response.json()
        assert all(resumed[key] == receipt[key] for key in ("run_id", "task_id", "thread_id"))
        events = await read_events(api, resumed)
        assert events[-1]["type"] == "run.completed", events[-1]
        assert len(payloads(events, "run.resumed")) == 1
        assert [p["agent"] for p in payloads(events, "agent.dispatched")] == ["seo"]
        replay = await api.post(input_path, json=answer)
        assert replay.json()["replayed"] is True
        assert workspace.fetched == [GUIDE]
        assert len(models.planner_inputs) == 1


@pytest.mark.asyncio
async def test_followup_reference_and_site_file_do_not_replace_the_requested_page(workspace):
    turns = [
        (f"看一下 {GUIDE} 的标题", 1, [GUIDE], GUIDE),
        (f"参考 {REFERENCE}，仍检查刚才页面和 robots.txt", None, [REFERENCE], GUIDE),
        ("robots.txt 稍后再看，这次只查当前页标题。", None, [], GUIDE),
        (f"同一个站，换成 {NEW_PAGE}", 1, [NEW_PAGE], NEW_PAGE),
        (f"换个网站，检查 {OTHER}", 1, [OTHER], OTHER),
    ]
    async with workspace.serve() as api:
        for question, index, candidates, expected in turns:
            workspace.models.decisions.append(("seo", index))
            workspace.models.check_page()
            _, events = await ask(api, question)
            assert events[-1]["type"] == "run.completed", events[-1]
            [completed] = payloads(events, "tool.completed")
            assert completed["result"]["data"]["raw"]["data"]["requested_url"] == expected
            assert workspace.models.planner_inputs[-1]["candidate_pages"] == candidates
    assert workspace.fetched == [turn[3] for turn in turns]


@pytest.mark.asyncio
async def test_previous_healthcare_information_stays_out_of_followup_seo_turns(workspace):
    models = workspace.models
    private_note = "本地验证标志：私人备注 3210"
    health_question = f"2026 年湖北医师资格报名时间。{private_note}"
    health_answer = "2 月 11 日"
    history = [{"role": "user", "content": health_question},
               {"role": "assistant", "content": health_answer}]
    async with workspace.serve() as api:
        models.decisions.append(("healthcare", None))
        models.replies.append(ToolCall("exam", "medical_exam_registration", {
            "topic": "registration", "year": 2026, "region": "湖北", "exam": "医师资格",
        }))
        _, health_events = await ask(api, health_question)
        [registered] = payloads(health_events, "tool.completed")
        assert registered["result"]["data"]["status"] == "matched"
        first_seo_call = len(models.specialist_inputs)
        for question, index in [(f"现在看这个网页 {GUIDE}", 1), ("继续看同一页的标题", None)]:
            models.decisions.append(("seo", index))
            models.check_page()
            _, events = await ask(api, question, history=history)
            assert events[-1]["type"] == "run.completed", events[-1]
            history.extend([{"role": "user", "content": question},
                            {"role": "assistant", "content": "已读取页面"}])
        seo_inputs = json.dumps(models.specialist_inputs[first_seo_call:], ensure_ascii=False)
        assert GUIDE in seo_inputs and private_note not in seo_inputs


@pytest.mark.asyncio
async def test_admissions_skill_requires_evidence_before_showing_an_answer(workspace):
    models = workspace.models
    models.decisions.append(("healthcare", None))
    models.replies.append("两校录取率都是八成。")
    question = "武汉科技大学和湖北民族大学的临床医学专硕录取率分别是多少？"
    async with workspace.serve() as api:
        _, events = await ask(api, question)
        assert events[-1]["type"] == "run.completed"
        [skill] = payloads(events, "skill.loaded")
        assert skill["skill"] == "graduate-admissions"
        assert skill["tools"] == [POSTGRADUATE_TOOL]
        assert models.specialist_inputs[-1][0]["content"].count("<skill ") == 1
        assert not payloads(events, "message.delta")
        [answer] = payloads(events, "message.completed")
        assert "没有取得可复核的数据回执" in answer["content"]
        assert "八成" not in answer["content"]


@pytest.mark.asyncio
async def test_admissions_summary_comes_from_the_same_scoped_receipt(workspace):
    models = workspace.models
    models.decisions.append(("healthcare", None))
    models.replies.append(ToolCall("search", POSTGRADUATE_TOOL, {
        "schools": ["武汉科技大学", "湖北民族大学"], "year": 2026,
        "program": "临床医学专硕", "query": "初试科目",
    }))
    question = "对比武汉科技大学105101和湖北民族大学105100的2026年初试科目"
    async with workspace.serve() as api:
        _, events = await ask(api, question)
        [tool] = payloads(events, "tool.completed")
        receipt = tool["result"]["data"]["receipt"]
        assert receipt["query"] == question and receipt["status"] == "hit"
        [answer] = payloads(events, "message.completed")
        assert answer["content"] == tool["result"]["data"]["answer"]
        assert all(h["quote"] in answer["content"] for h in receipt["hits"])
        assert len(models.specialist_inputs) == 1


@pytest.mark.parametrize(("course", "sentence"), [
    ("clinical", "医学人文仍沿用 2024 版考试大纲。"),
    ("nursing", "护理沟通单元沿用 2024 版教学讲义。"),
])
@pytest.mark.asyncio
async def test_course_editor_gets_exact_page_and_verified_public_rule(workspace, course, sentence):
    models = workspace.models
    url = f"https://courses.example/{course}-course.html"
    models.decisions.append(("course_review", 1))
    models.replies.append("核对完成。")
    async with workspace.serve() as api:
        receipt, events = await ask(api, f"用医学人文考纲核对课程页 {url}")
        assert events[-1]["type"] == "run.completed", events[-1]
        assert [
            (e["type"], e["payload"].get("agent") or e["payload"].get("name"))
            for e in events if e["type"] in {"agent.dispatched", "tool.completed"}
        ] == [
            ("agent.dispatched", "healthcare"), ("tool.completed", "medical_exam_syllabus"),
            ("agent.dispatched", "seo"), ("tool.completed", "seo_page_metadata"),
        ]
        rule_call, page_call = payloads(events, "tool.started")
        rule_receipt, page_receipt = payloads(events, "tool.completed")
        assert rule_call["arguments"] == {}
        assert page_call["arguments"] == {"url": url}
        assert rule_receipt["tool_call_id"] == rule_call["tool_call_id"]
        assert page_receipt["tool_call_id"] == page_call["tool_call_id"]
        assert all(p["result"]["success"] for p in (rule_receipt, page_receipt))
        detail = (await api.get(f"/api/v1/chat/runs/{receipt['run_id']}")).json()
        assert detail["status"] == "COMPLETED"
    [editor_input] = models.specialist_inputs
    material = json.loads(editor_input[-1]["content"])
    rule = material["public_rule"]
    assert set(rule) == {"title", "source_url", "year", "applies_to", "quote"}
    assert rule == rule_receipt["result"]["data"]["evidence"]
    assert rule["year"] == 2026 and rule["source_url"] == SYLLABUS
    assert "临床执业（助理）医师" in rule["applies_to"]
    assert all("护士" not in category for category in rule["applies_to"])
    assert material["page"]["requested_url"] == url
    assert material["page"]["source"] == "page_html"
    assert sentence in material["page"]["content_excerpt"]
    assert workspace.fetched == [url]


@pytest.mark.asyncio
async def test_failed_course_editor_reuses_both_receipts_after_restart(workspace):
    models = workspace.models
    models.decisions.append(("course_review", 1))
    models.replies.append(RuntimeError("测试模型暂时不可用"))
    async with workspace.serve() as api:
        receipt, first = await ask(api, "核对医学人文考纲 https://courses.example/clinical-course.html")
        assert first[-1]["type"] == "run.failed"
        prior = payloads(first, "tool.completed")
        assert len(prior) == 2 and all(result["result"]["success"] for result in prior)
    models.replies.append("已根据此前两份证据核对。")
    async with workspace.serve() as api:
        response = await api.post(f"/api/v1/chat/runs/{receipt['run_id']}/resume")
        response.raise_for_status()
        events = await read_events(api, receipt)
        assert events[-1]["type"] == "run.completed", events[-1]
        assert payloads(events, "tool.completed") == prior
        assert [p["agent"] for p in payloads(events, "agent.dispatched")] == ["healthcare", "seo"]
    assert len(models.planner_inputs) == 1
    assert len(models.specialist_inputs) == 2
    assert models.specialist_inputs[0] == models.specialist_inputs[1]
