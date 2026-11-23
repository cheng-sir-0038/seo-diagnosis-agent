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
from src.education_agent.practice import MathPractice, question_bank
from src.education_agent.resources import LearningResources
from src.education_agent.teacher_exam import TeacherWrittenExam
from src.facade.data_facade import DataFacade
from src.followup_agent.workflow import FollowupCommand
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
        planned = self.decisions.popleft()
        if isinstance(planned, dict):
            decision = schema.model_validate({"needs_page": False, **planned})
        else:
            route, index = planned
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


async def answer_waiting(api, receipt, events, answer):
    waiting = payloads(events, "run.waiting_for_input")[-1]
    path = f"/api/v1/chat/runs/{receipt['run_id']}/input"
    body = {"request_id": waiting["request_id"], "answer": answer}
    response = await api.post(path, json=body)
    response.raise_for_status()
    resumed = response.json()
    assert resumed["run_id"] == receipt["run_id"]
    return await read_events(api, resumed), path, body


@pytest.mark.asyncio
async def test_followup_reads_one_subject_then_confirms_only_the_current_draft(workspace):
    async with workspace.serve() as api:
        receipt, first = await ask(api, "用合成资料演示复诊准备 marker_do_not_log")
        assert first[-1]["type"] == "run.waiting_for_input"
        assert payloads(first, "agent.dispatched")[0]["agent"] == "followup"
        assert "另一份带检查日期" in payloads(first, "message.completed")[-1]["content"]
        headers = {"X-Followup-Key": receipt["followup_key"]}
        run_id = receipt["run_id"]
        base = f"/api/v1/chat/runs/{run_id}"
        case_path = f"{base}/followup"
        assert (await api.get(case_path, headers={"X-Followup-Key": "wrong"})).status_code == 404
        waiting = payloads(first, "run.waiting_for_input")[-1]
        assert (await api.post(f"{base}/input", json={
            "request_id": waiting["request_id"], "answer": FollowupCommand.CONFIRM,
        })).status_code == 404
        assert (await api.post(f"{base}/input", headers=headers, json={
            "request_id": waiting["request_id"], "answer": "marker_unsolicited_clinical_note",
        })).status_code == 422
        upload = await api.post(
            f"{case_path}/materials",
            headers={**headers, "X-Followup-Subject": "DEMO-001", "X-Followup-File": "other.json"},
            content=json.dumps([
                {"subject_id": "DEMO-001", "kind": "report", "item": "should_not_merge"},
                {"subject_id": "DEMO-999", "kind": "report", "item": "marker_other_subject"},
            ]),
        )
        upload.raise_for_status()
        case = upload.json()
        assert case["files"][-1]["rows"] == 0
        assert "对象编号不一致" in case["files"][-1]["error"]
        assert all(row["record"]["item"] != "should_not_merge" for row in case["records"])
        assert len(case["records"]) == 13
        added = await api.post(f"{case_path}/demo-supplement", headers=headers)
        added.raise_for_status()
        again = await api.post(f"{case_path}/demo-supplement", headers=headers)
        assert again.json() == added.json()
        assert len(added.json()["records"]) == 15

    async def respond(api, events, command):
        waiting = payloads(events, "run.waiting_for_input")[-1]
        body = {"request_id": waiting["request_id"], "answer": command}
        response = await api.post(f"{base}/input", headers=headers, json=body)
        response.raise_for_status()
        return response.json(), body, await read_events(api, response.json())

    async with workspace.serve() as api:
        resumed, _, events = await respond(api, first, FollowupCommand.PREPARE)
        assert resumed["run_id"] == run_id
        case = (await api.get(case_path, headers=headers)).json()
        assert case["saved"] is None
        comparisons = {row["item"]: row for row in case["draft"]["comparisons"]}
        assert comparisons["空腹血糖"]["delta"] == "-0.3"
        assert comparisons["肌酐"]["delta"] is None
        assert comparisons["肌酐"]["reasons"] == ["单位缺失或不同"]
        # Repair a missing date while the prior version is waiting for confirmation.
        entry = next(row for row in case["records"] if row["record"]["kind"] == "medication_note")
        updated = await api.patch(
            f"{case_path}/records/{entry['id']}", headers=headers,
            json={"revision": case["revision"], "fields": {"record_date": "2026-11-16"}},
        )
        updated.raise_for_status()
        case = updated.json()
        corrected = next(row for row in case["records"] if row["id"] == entry["id"])
        assert corrected["record"]["record_date"] == "2026-11-16"
        assert corrected["original"]["record_date"] is None and case["draft"] is None

        _, _, refreshed = await respond(api, events, FollowupCommand.CONFIRM)
        assert refreshed[-1]["type"] == "run.waiting_for_input"
        assert (await api.get(case_path, headers=headers)).json()["saved"] is None
        _, confirmation, completed = await respond(api, refreshed, FollowupCommand.CONFIRM)
        assert completed[-1]["type"] == "run.completed"
        saved = (await api.get(case_path, headers=headers)).json()["saved"]
        assert [(item["title"], item["due_date"]) for item in saved["tasks"]] == [
            ("带上两次检查报告", "2026-11-27"),
        ]
        assert "第 2 行" in saved["tasks"][0]["source"]
        replay = await api.post(f"{base}/input", headers=headers, json=confirmation)
        assert replay.json()["replayed"] is True
        assert (await api.get(case_path, headers=headers)).json()["saved"] == saved
        detail = (await api.get(base)).text
        for private in (
            "marker_do_not_log", "marker_other_subject", "marker_unsolicited_clinical_note",
            "空腹血糖", "合成补充报告.csv", receipt["followup_key"],
        ):
            assert private not in detail
        tools = payloads(completed, "tool.completed")
        assert {tool["name"] for tool in tools} == {
            "followup_read_materials", "followup_prepare_materials", "followup_save_confirmed",
        }
        assert {tool["agent_run_id"] for tool in tools} == {tools[0]["agent_run_id"]}
    assert not workspace.models.planner_inputs
    assert not workspace.models.specialist_inputs


@pytest.mark.asyncio
async def test_followup_import_can_retry_a_document_with_the_correct_subject(workspace):
    async with workspace.serve() as api:
        receipt, events = await ask(api, "整理下次复诊的资料")
        assert events[-1]["type"] == "run.waiting_for_input"
        headers = {"X-Followup-Key": receipt["followup_key"], "X-Followup-File": "supplied.json"}
        case = f"/api/v1/chat/runs/{receipt['run_id']}/followup"
        data = json.dumps([{
            "subject_id": "CASE-01", "kind": "report", "item": "Glucose", "value": 5.9,
        }])
        wrong = await api.post(
            f"{case}/materials", content=data,
            headers={**headers, "X-Followup-Subject": "CASE-02"},
        )
        assert wrong.json()["subject_id"] is None
        corrected = await api.post(
            f"{case}/materials", content=data, headers={**headers, "X-Followup-Subject": "CASE-01"},
        )
        corrected.raise_for_status()
        assert corrected.json()["subject_id"] == "CASE-01"
        assert corrected.json()["records"][0]["record"]["value"] == "5.9"
        assert len(corrected.json()["files"]) == 1
        assert corrected.json()["files"][0]["error"] is None


@pytest.mark.asyncio
async def test_math_practice_resumes_grades_once_and_reviews_the_wrong_question(workspace):
    models = workspace.models
    models.decisions.append({
        "route": "learning", "learning_skill": "math-practice",
        "math_practice": {"topic": "一元一次方程", "count": 4},
    })
    review_route = {
        "route": "learning", "learning_skill": "math-practice",
        "math_practice": {"review": True},
    }
    bank = question_bank()
    assert len(bank) == 50
    async with workspace.serve() as api:
        receipt, first = await ask(api, "给我 4 道一元一次方程练习")
        [question_step] = payloads(first, "tool.completed")
        assert question_step["name"] == MathPractice.name
        data = question_step["result"]["data"]
        assert not any(field in data for field in ("answer", "analysis", "counts"))
        assert len(data["progress"]["question_ids"]) == 4
        first_id, _, third_id, _ = data["progress"]["question_ids"]
        question = bank[first_id]
        wrong_letter = next(letter for letter in question.options if letter != question.answer)
        assert "第 1 / 4 题" in payloads(first, "message.completed")[-1]["content"]
        original_agent_run_id = question_step["agent_run_id"]

    async with workspace.serve() as api:
        models.decisions.append({
            "route": "learning", "learning_skill": "math-resources",
            "resources": {"topic": "Oxford Owl", "completed": True},
        })
        # The resource is completed while a separate practice run is still waiting.
        _, resource = await ask(api, "我已完成 Oxford Owl 的练习")
        assert "已记录完成" in payloads(resource, "message.completed")[-1]["content"]
        events, _, _ = await answer_waiting(api, receipt, first, "两个都选")
        assert "第 1 / 4 题" in payloads(events, "message.completed")[-1]["content"]
        events, path, body = await answer_waiting(api, receipt, events, wrong_letter)
        feedback = payloads(events, "message.completed")[-1]["content"]
        assert "答错了" in feedback and "题库原解析" in feedback and question.source_url in feedback
        assert "第 2 / 4 题" in feedback
        assert (await api.post(path, json=body)).json()["replayed"] is True
        assert (await api.post(path, json={**body, "answer": question.answer})).status_code == 409
        events, _, _ = await answer_waiting(api, receipt, events, "跳过")
        assert "第 3 / 4 题" in payloads(events, "message.completed")[-1]["content"]
        events, _, _ = await answer_waiting(api, receipt, events, f"我选 {bank[third_id].answer}。")
        assert "答对了" in payloads(events, "message.completed")[-1]["content"]
        events, _, _ = await answer_waiting(api, receipt, events, "结束练习")
        assert events[-1]["type"] == "run.completed"
        results = payloads(events, "tool.completed")
        assert {result["agent_run_id"] for result in results} == {original_agent_run_id}
        assert len(payloads(events, "skill.loaded")) == 1
        assert results[-1]["result"]["data"]["counts"] == {
            "correct": 1, "incorrect": 1, "skipped": 1, "unanswered": 1,
        }
        models.decisions.append(review_route)
        review_receipt, review = await ask(api, "重练错题")
        progress = payloads(review, "tool.completed")[-1]["result"]["data"]["progress"]
        assert progress["question_ids"] == [first_id]
        reviewed, _, _ = await answer_waiting(api, review_receipt, review, question.answer)
        assert reviewed[-1]["type"] == "run.completed"
        models.decisions.append(review_route)
        _, no_wrong = await ask(api, "看看还有错题吗")
        assert "暂无可复练的错题" in payloads(no_wrong, "message.completed")[-1]["content"]
        models.decisions.append({
            "route": "learning", "learning_skill": "math-resources", "resources": {"topic": "分数"},
        })
        _, records = await ask(api, "查看分数学习资料")
        assert "这一项已记录完成" in payloads(records, "message.completed")[-1]["content"]
    assert not models.specialist_inputs


@pytest.mark.asyncio
async def test_teacher_exam_retains_confirmed_scope_and_never_fills_with_another_notice(workspace):
    models = workspace.models
    models.decisions.extend([
        {"route": "exam_prep", "teacher_exam": {
            "region": "湖北", "year": 2026, "subject": "初中数学",
        }},
        {"route": "exam_prep", "teacher_exam": {"session": "second"}},
        {"route": "exam_prep", "teacher_exam": {
            "year": 2027, "region": "湖北", "subject": "初中数学",
        }},
        {"route": "exam_prep", "teacher_exam": {
            "region": "湖北", "year": 2026, "subject": "初中数学", "session": "first",
        }},
    ])
    async with workspace.serve() as api:
        receipt, first = await ask(api, "2026 年湖北初中数学教资笔试何时报名？")
        [need_input] = payloads(first, "run.waiting_for_input")
        assert "上半年或下半年" in need_input["questions"][0]
        assert not payloads(first, "tool.completed")
        events, _, _ = await answer_waiting(api, receipt, first, "下半年")
        [dispatch] = payloads(events, "agent.dispatched")
        [call] = payloads(events, "tool.completed")
        assert dispatch["agent"] == call["agent"] == "exam_prep"
        assert dispatch["agent_run_id"] == call["agent_run_id"]
        assert call["name"] == TeacherWrittenExam.name
        answer = payloads(events, "message.completed")[-1]["content"]
        assert "7 月 3 日" in answer and "15957.html" in answer and "347-1.htm" in answer
        assert "15603.html" not in answer

        _, out_of_scope = await ask(api, "2027 年湖北初中数学教资笔试何时报名？")
        result = payloads(out_of_scope, "tool.completed")[-1]["result"]["data"]
        assert result["scope"]["year"] == 2027 and result["matched"] is False
        assert "7 月 3 日" not in result["answer"]
        _, first_session = await ask(api, "2026 年上半年湖北初中数学教资笔试呢？")
        answer = payloads(first_session, "message.completed")[-1]["content"]
        assert "1 月 9 日" in answer and "15603.html" in answer
        assert "15957.html" not in answer


@pytest.mark.asyncio
async def test_official_learning_resource_completion_is_saved_in_the_conversation(workspace):
    query = {"topic": "分数", "age": 9}
    workspace.models.decisions.extend([
        {"route": "learning", "learning_skill": "math-resources", "resources": params}
        for params in (query, {"topic": "Oxford Owl", "completed": True}, query)
    ])
    async with workspace.serve() as api:
        _, events = await ask(api, "9 岁想学分数，推荐一个官方练习")
        [tool] = payloads(events, "tool.completed")
        assert tool["name"] == LearningResources.name
        assert tool["result"]["data"]["sources"] == [
            "https://home.oxfordowl.co.uk/maths/primary-fractions/",
        ]
        await ask(api, "已经完成了 Oxford Owl 的分数练习")
        _, saved = await ask(api, "再看看 9 岁的分数练习")
        assert "这一项已记录完成" in payloads(saved, "message.completed")[-1]["content"]


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
            (e["type"], e["payload"]["agent"])
            for e in events
            if e["type"] in {"agent.dispatched", "tool.completed", "agent.completed"}
        ] == [
            ("agent.dispatched", "healthcare"), ("tool.completed", "healthcare"),
            ("agent.completed", "healthcare"), ("agent.dispatched", "seo"),
            ("tool.completed", "seo"), ("agent.completed", "seo"),
        ]
        dispatches = payloads(events, "agent.dispatched")
        [medical_id, seo_id] = [item["agent_run_id"] for item in dispatches]
        assert medical_id != seo_id
        assert [item["agent_run_id"] for item in payloads(events, "agent.completed")] == [
            medical_id, seo_id,
        ]
        rule_call, page_call = payloads(events, "tool.started")
        rule_receipt, page_receipt = payloads(events, "tool.completed")
        assert rule_call["arguments"] == {}
        assert page_call["arguments"] == {"url": url}
        assert rule_receipt["tool_call_id"] == rule_call["tool_call_id"]
        assert page_receipt["tool_call_id"] == page_call["tool_call_id"]
        assert rule_receipt["agent_run_id"] == rule_call["agent_run_id"] == medical_id
        assert page_receipt["agent_run_id"] == page_call["agent_run_id"] == seo_id
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
        [failure] = payloads(first, "agent.failed")
        assert failure["agent"] == "seo"
        prior = payloads(first, "tool.completed")
        assert len(prior) == 2 and all(result["result"]["success"] for result in prior)
    models.replies.append("已根据此前两份证据核对。")
    async with workspace.serve() as api:
        response = await api.post(f"/api/v1/chat/runs/{receipt['run_id']}/resume")
        response.raise_for_status()
        events = await read_events(api, receipt)
        assert events[-1]["type"] == "run.completed", events[-1]
        assert payloads(events, "tool.completed") == prior
        dispatches = payloads(events, "agent.dispatched")
        assert [p["agent"] for p in dispatches] == ["healthcare", "seo", "seo"]
        retry_id = failure["agent_run_id"]
        assert dispatches[1]["agent_run_id"] == dispatches[2]["agent_run_id"] == retry_id
        assert payloads(events, "agent.completed")[-1]["agent_run_id"] == failure["agent_run_id"]
    assert len(models.planner_inputs) == 1
    assert len(models.specialist_inputs) == 2
    assert models.specialist_inputs[0] == models.specialist_inputs[1]
