"""Exercise workspace turns through HTTP/SSE, with PostgreSQL checkpoints and real tools."""

from __future__ import annotations

import importlib
import json
from collections import deque
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

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
from src.procurement_agent.models import ProcurementIntake
from src.procurement_agent.tools import BUILD_LIST as PROCUREMENT_LIST
from src.procurement_agent.tools import CATALOG as PROCUREMENT_CATALOG
from src.procurement_agent.tools import COMPARE as PROCUREMENT_COMPARE
from src.procurement_agent.tools import RECONCILE as PROCUREMENT_RECONCILE
from src.providers.page_metadata.client import FetchedHtmlPage
from src.providers.page_metadata.provider import PageMetadataProvider
from src.providers.page_metadata.pyseo_client import (
    PythonSeoAnalyzerClient,
    PythonSeoAnalyzerConfig,
)
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.services.system import SeoAgentSystemService
from src.travel_agent import context as travel_context
from src.travel_agent import flyai
from src.travel_agent.intake import TravelIntake
from src.travel_agent.tools import COMPARE, SAVE, TRANSPORT, WEATHER

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
        if schema in (TravelIntake, ProcurementIntake):
            decision = schema.model_validate(planned)
        elif isinstance(planned, dict):
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


def travel_result(events, name):
    return next(
        payload["result"]["data"]
        for payload in reversed(payloads(events, "tool.completed")) if payload["name"] == name
    )


@pytest.mark.asyncio
async def test_procurement_search_renders_local_results_and_reuses_cache(workspace, monkeypatch):
    monkeypatch.setattr(chat.settings, "taobao_tbk_local_catalog", True)
    planned = {
        "route": "shopping_procurement",
        "procurement": {
            "procedure": "search", "needs": [{"item": "纸杯", "specification": "200ml"}],
        },
    }
    workspace.models.decisions.extend([planned, planned])
    async with workspace.serve() as api:
        _, events = await ask(api, "帮我搜200ml的纸杯，给我几个参考价。", conversation_id="catalog")
        assert events[-1]["type"] == "run.completed"
        skill = payloads(events, "skill.loaded")[0]
        assert skill["skill"] == "shopping-and-procurement"
        completed = payloads(events, "tool.completed")
        assert [tool["name"] for tool in completed] == [PROCUREMENT_CATALOG]
        assert completed[0]["render_hint"] == "cards"
        result = travel_result(events, PROCUREMENT_CATALOG)
        assert result["title"] == "商品搜索"
        assert result["provenance"]["source_label"] == "本地商品库"
        assert len(result["items"]) == 5
        assert all("200ml" in item["title"] for item in result["items"])
        source = result["sources"][0]
        assert source["total_results"] == 10 and not source["cache_hit"]

        _, repeated = await ask(api, "找200ml纸杯。", conversation_id="another-catalog")
        next_source = travel_result(repeated, PROCUREMENT_CATALOG)["sources"][0]
        assert next_source["cache_hit"] and next_source["fetched_at"] == source["fetched_at"]


@pytest.mark.asyncio
async def test_procurement_refresh_reuses_the_last_search_filters(workspace, monkeypatch):
    monkeypatch.setattr(chat.settings, "taobao_tbk_local_catalog", True)
    planned = {
        "route": "shopping_procurement",
        "procurement": {
            "procedure": "search",
            "needs": [{"item": "纸杯", "specification": "200ml"}],
            "tmall_only": True,
        },
    }
    workspace.models.decisions.extend([planned, planned])
    async with workspace.serve() as api:
        await ask(api, "只查天猫的200ml纸杯。", conversation_id="catalog-refresh")
        _, latest = await ask(
            api, "查最新的200ml天猫纸杯价格。", conversation_id="catalog-refresh",
        )
        args = payloads(latest, "tool.started")[0]["arguments"]
        assert args["force_refresh"] and args["tmall_only"]
        assert args["keywords"] == ["纸杯 200ml"]
        assert not travel_result(latest, PROCUREMENT_CATALOG)["sources"][0]["cache_hit"]

        for request in ("刷新一下", "最新"):
            _, repeated = await ask(api, request, conversation_id="catalog-refresh")
            assert repeated[-1]["type"] == "run.completed"
            assert payloads(repeated, "tool.started")[0]["arguments"] == args
            source = travel_result(repeated, PROCUREMENT_CATALOG)["sources"][0]
            assert not source["cache_hit"] and source["total_results"] == 4
            assert "已刷新" in payloads(repeated, "message.completed")[-1]["content"]
        assert len(workspace.models.planner_inputs) == 2


@pytest.mark.asyncio
async def test_procurement_refresh_remembers_intent_after_asking_for_the_product(
    workspace, monkeypatch,
):
    monkeypatch.setattr(chat.settings, "taobao_tbk_local_catalog", True)
    workspace.models.decisions.extend([
        {"route": "shopping_procurement", "procurement": {"procedure": "search"}},
        {"needs": [{"item": "纸杯", "specification": "200ml"}]},
    ])
    async with workspace.serve() as api:
        receipt, events = await ask(api, "我想刷新商品价格")
        assert events[-1]["type"] == "run.waiting_for_input"
        assert not payloads(events, "tool.started")
        completed, _, _ = await answer_waiting(api, receipt, events, "200ml纸杯")
        assert completed[-1]["type"] == "run.completed"
        [call] = payloads(completed, "tool.started")
        assert call["arguments"]["force_refresh"]


@pytest.mark.asyncio
async def test_procurement_repeated_refresh_continues_existing_comparison(workspace, monkeypatch):
    monkeypatch.setattr(chat.settings, "taobao_tbk_local_catalog", True)
    workspace.models.decisions.extend([
        {
            "route": "shopping_procurement",
            "procurement": {"needs": [{"item": "纸杯", "specification": "200ml", "quantity": 60}]},
        },
        {},
        {},
    ])
    async with workspace.serve() as api:
        receipt, events = await ask(api, "买60只200ml纸杯，帮我比价。")
        first, _, _ = await answer_waiting(api, receipt, events, "刷新")
        second, _, _ = await answer_waiting(api, receipt, first, "刷新")
        assert second[-1]["type"] == "run.waiting_for_input"
        refreshed = [
            call for call in payloads(second, "tool.started")
            if call["name"] == PROCUREMENT_CATALOG and call["arguments"].get("force_refresh")
        ]
        assert len(refreshed) == 2 and refreshed[0]["tool_call_id"] != refreshed[1]["tool_call_id"]


@pytest.mark.asyncio
async def test_procurement_reuses_a_run_and_reprices_without_a_provider_key(workspace, monkeypatch):
    monkeypatch.setattr(chat.settings, "taobao_tbk_app_key", SecretStr(""))
    monkeypatch.setattr(chat.settings, "taobao_tbk_local_catalog", False)
    need = {"item": "纸杯", "specification": "200ml", "unit": "个", "quantity": 60}
    quote = (
        "甲商家：纸杯200ml，1个装，1个起订，1至99个单价14元，满100个单价12元。"
        "运费60元，含税，无附加费用，无优惠。"
    )
    workspace.models.decisions.extend([
        {"route": "shopping_procurement", "procurement": {"needs": [need], "budget_cny": 1000}},
        {"quotes": [{
            "item": "纸杯", "supplier": "甲商家", "specification": "200ml", "unit": "个",
            "pack_size": 1, "minimum_quantity": 1, "evidence": quote,
            "tiers": [
                {"minimum": 1, "maximum": 99, "price_cny": 14, "basis": "unit",
                 "evidence": "1至99个单价14元"},
                {"minimum": 100, "price_cny": 12, "basis": "unit", "evidence": "满100个单价12元"},
            ],
            "shipping_cny": 60, "tax_cny": 0, "fees_cny": 0, "discount_cny": 0,
        }]},
        {"needs": [{"item": "纸杯", "quantity": 120}]},
    ])
    async with workspace.serve() as api:
        receipt, events = await ask(api, "想买60个200ml纸杯，预算1000元，帮我比价。")
        assert events[-1]["type"] == "run.waiting_for_input"
        dispatch = payloads(events, "agent.dispatched")[0]
        assert dispatch["agent"] == "shopping_procurement"
        assert travel_result(events, PROCUREMENT_CATALOG)["sources"][0]["status"] == "unconfigured"
        assert travel_result(events, PROCUREMENT_COMPARE)["quotes"] == []

        priced, _, _ = await answer_waiting(api, receipt, events, quote)
        compare_result = travel_result(priced, PROCUREMENT_COMPARE)
        assert compare_result["quotes"][0]["total_cny"] == "900.00"
        assert compare_result["quotes"][0]["evidence"] == quote
        assert compare_result["view_type"] == "cards"

        changed, _, _ = await answer_waiting(api, receipt, priced, "纸杯改为120个。")
        new_quote = travel_result(changed, PROCUREMENT_COMPARE)["quotes"][0]
        assert (new_quote["price_minimum"], new_quote["actual_quantity"]) == (100, 120)
        assert new_quote["goods_cny"] == "1440.00" and new_quote["total_cny"] is None
        assert {"确认运费", "确认税费"} <= set(new_quote["issues"])
        done, _, _ = await answer_waiting(api, receipt, changed, "结束本次整理")
        assert done[-1]["type"] == "run.completed"
        assert {tool["agent_run_id"] for tool in payloads(done, "tool.completed")} == {
            dispatch["agent_run_id"],
        }


@pytest.mark.asyncio
async def test_procurement_routes_lists_and_reconciles_user_records(workspace, monkeypatch):
    monkeypatch.setattr(chat.settings, "taobao_tbk_app_key", SecretStr(""))
    order = "订单 P-102 纸杯200ml 应到60个。"
    delivery = "收货 P-102 纸杯200ml 实到58个。"
    payment = "支付 P-102 付款凭证 F-81，实付900元。"
    invoice = "发票 P-102 已开票900元。"
    refund = "P-102 退款申请30元，未到账。"
    records = [
        {"reference": "P-102", "kind": "order", "item": "纸杯", "quantity": 60,
         "specification": "200ml", "evidence": order},
        {"reference": "P-102", "kind": "delivery", "item": "纸杯", "quantity": 58,
         "specification": "200ml", "evidence": delivery},
        {"reference": "F-81", "kind": "payment", "document_ref": "F-81", "amount_cny": 900,
         "evidence": payment},
        {"reference": "P-102", "kind": "invoice", "amount_cny": 900, "evidence": invoice},
        {"reference": "P-102", "kind": "refund", "refund_state": "pending", "amount_cny": 30,
         "evidence": refund},
    ]
    workspace.models.decisions.extend([
        {"route": "shopping_procurement", "procurement": {
            "procedure": "list", "people": 20, "budget_cny": 800, "needs": [
                {"item": "纸杯", "unit": "个", "per_person": 2},
                {"item": "礼袋", "unit": "个", "per_person": 1},
            ],
        }},
        {"route": "shopping_procurement", "procurement": {
            "procedure": "reconcile", "records": records,
        }},
    ])
    async with workspace.serve() as api:
        _, list_events = await ask(
            api, "20人的活动，预算800元，准备纸杯和礼袋。", conversation_id="procurement-list",
        )
        list_result = travel_result(list_events, PROCUREMENT_LIST)
        assert [row["quantity"] for row in list_result["lines"]] == [40, 20]
        assert all(row["quantity_estimated"] for row in list_result["lines"])
        assert list_result["plans"] == []

        _, ledger_events = await ask(
            api, "核对采购到货与支出。\n" + "\n".join([order, delivery, payment, invoice, refund]),
            conversation_id="procurement-ledger",
        )
        orders = travel_result(ledger_events, PROCUREMENT_RECONCILE)["orders"]
        assert len(orders) == 1
        ledger = orders[0]
        assert ledger["lines"][0]["difference"] == -2
        assert ledger["net_paid_cny"] == "900.00" and ledger["pending_refunds"] == 1
        assert {item["name"] for item in payloads(ledger_events, "tool.completed")} == {
            PROCUREMENT_RECONCILE,
        }


@pytest.mark.asyncio
async def test_trip_weather_is_available_before_the_other_trip_details(workspace, monkeypatch):
    calls = []
    monkeypatch.setattr("src.travel_agent.workflow.china_today", lambda: date(2026, 11, 23))

    async def forecast(city, start, end):
        calls.append((city, start.isoformat(), end.isoformat()))
        return {"status": "ok", "source": "open_meteo", "items": [{"date": start.isoformat()}]}

    monkeypatch.setattr(travel_context, "trip_weather", forecast)
    workspace.models.decisions.extend([
        {"route": "travel", "travel": {"request": {"destination": "杭州"}}},
        {"request": {"depart_on": "2026-11-26"}},
        {"request": {"origin": "上海"}},
        {"request": {"return_on": "2026-11-28"}},
    ])
    async with workspace.serve() as api:
        receipt, first = await ask(api, "我去杭州出差。")
        assert first[-1]["type"] == "run.waiting_for_input"
        assert any("日期" in question for question in first[-1]["payload"]["questions"])
        assert travel_result(first, WEATHER)["scope"] == "near_term"
        assert calls == [("杭州", "2026-11-23", "2026-11-25")]

        dated, _, _ = await answer_waiting(api, receipt, first, "11月26日出发。")
        assert travel_result(dated, WEATHER)["scope"] == "itinerary"
        assert calls[-1] == ("杭州", "2026-11-26", "2026-11-26")

        with_origin, _, _ = await answer_waiting(api, receipt, dated, "从上海出发。")
        assert len(calls) == 2

        resized, _, _ = await answer_waiting(api, receipt, with_origin, "11月28日返回。")
        assert resized[-1]["type"] == "run.waiting_for_input"
        assert calls[-1] == ("杭州", "2026-11-26", "2026-11-28")
        assert len(calls) == 3
        assert {tool["name"] for tool in payloads(resized, "tool.completed")} == {WEATHER}


@pytest.mark.asyncio
async def test_trip_collects_conditions_rechecks_time_and_saves_once(workspace, monkeypatch):
    calls = []
    monkeypatch.setattr("src.travel_agent.workflow.china_today", lambda: date(2026, 11, 23))
    monkeypatch.setattr(chat.settings, "baidu_maps_ak", SecretStr("test"))

    async def transport(origin, destination, travel_date, **_):
        calls.append(("transport", origin, destination, travel_date))
        if travel_date.day == 28:
            return {"status": "error", "source": "flyai", "items": [], "message": "飞猪暂不可用"}
        return {
            "status": "ok", "source": "flyai",
            "items": [{
                "id": "ticket", "name": "直达航班", "mode": "flight", "source": "flyai",
                "departs_local": f"{travel_date} 09:00:00",
                "arrives_local": f"{travel_date} 12:00:00",
                "to_stop": "上海虹桥机场", "booking_url": "https://www.fliggy.com/",
            }],
        }

    async def hotels(destination, check_in, check_out, **_):
        calls.append(("hotel", destination, check_in, check_out))
        return {"status": "no_results", "source": "flyai", "items": []}

    async def weather(city, start, end):
        calls.append(("weather", city, start, end))
        return {"status": "ok", "source": "open_meteo", "items": [{"date": start.isoformat()}]}

    async def commute(origin, destination, city, **_):
        calls.append(("commute", origin, destination, city))
        return {"status": "ok", "items": [{"mode": "driving", "duration_minutes": 40}]}

    monkeypatch.setattr(flyai, "search_transport", transport)
    monkeypatch.setattr(flyai, "search_hotels", hotels)
    monkeypatch.setattr(travel_context, "trip_weather", weather)
    monkeypatch.setattr(travel_context, "estimate_commute", commute)
    quote = "测试宾馆 2026-11-26 入住、2026-11-28 退房，大床房每晚 400元 含税费"
    workspace.models.decisions.extend([
        {"route": "travel", "travel": {
            "request": {"origin": "武汉", "travelers": 1, "rooms": 1},
        }},
        {"request": {
            "destination": "上海",
            "depart_on": "2026-11-26", "return_on": "2026-11-28", "venue": "上海世博展览馆",
            "arrive_by": "2026-11-26T14:00:00+08:00", "buffer_minutes": 15, "budget_cny": 1500,
        }, "supplied_offers": [{
            "kind": "hotel", "name": "测试宾馆", "quote": quote, "amount": 400,
            "check_in": "2026-11-26", "check_out": "2026-11-28", "room_type": "大床房",
            "price_basis": "night", "full_price": True,
        }, {
            "kind": "hotel", "name": "模型自造宾馆", "quote": "模型自造宾馆每晚 20元", "amount": 20,
        }]},
        {"request": {"arrive_by": "2026-11-26T12:30:00+08:00"}},
        {"request": {"return_on": "2026-11-29"}},
    ])
    async with workspace.serve() as api:
        receipt, first = await ask(api, "从武汉去外地出差，1个人1间房。")
        assert first[-1]["type"] == "run.waiting_for_input"
        assert any("日期" in question for question in first[-1]["payload"]["questions"])
        agent_run_id = payloads(first, "agent.dispatched")[0]["agent_run_id"]
        assert payloads(first, "skill.loaded")[0]["skill"] == "business-trip-planning"
        assert not calls

        first_plan, _, _ = await answer_waiting(
            api, receipt, first,
            "去上海，2026-11-26 出发，2026-11-28 返回。14:00 前到上海世博展览馆，"
            "预留15分钟，预算1500元。" + quote,
        )
        comparison = travel_result(first_plan, COMPARE)
        assert comparison["revision"] == 1
        assert len(calls) == 5
        assert comparison["plans"][0]["known_costs"] == {"CNY": "800"}
        assert comparison["plans"][0]["arrival_status"] == "met"
        assert comparison["plans"][0]["arrival_at_venue"] == "2026-11-26T12:55:00+08:00"
        assert not comparison["plans"][0]["complete_price"]
        assert comparison["weather"]["source"] == "open_meteo"
        assert any(source["status"] == "error" for source in comparison["providers"])
        assert {offer["source"] for offer in comparison["offers"]} == {"flyai", "user"}
        assert all("模型自造" not in offer["name"] for offer in comparison["offers"])
        assert {tool["agent_run_id"] for tool in payloads(first_plan, "tool.completed")} == {
            agent_run_id,
        }

        changed, _, _ = await answer_waiting(api, receipt, first_plan, "当天 12:30 就要到会场。")
        revised = travel_result(changed, COMPARE)
        assert len(calls) == 5
        assert revised["revision"] == 2
        assert revised["plans"][0]["arrival_status"] == "late"
        assert revised["request"]["venue"] == comparison["request"]["venue"]

        retried, _, _ = await answer_waiting(api, receipt, changed, "重查交通")
        assert [call[0] for call in calls[5:]] == ["transport", "transport"]
        assert "重查交通" in payloads(first_plan, "message.completed")[-1]["content"]
        assert len({
            tool["tool_call_id"] for tool in payloads(retried, "tool.completed")
            if tool["name"] == TRANSPORT
        }) == 2

        date_changed, _, _ = await answer_waiting(api, receipt, retried, "返程改成2026-11-29")
        assert [call[0] for call in calls[7:]] == ["transport", "hotel", "weather"]
        assert calls[7][1:3] == ("上海", "武汉")
        assert travel_result(date_changed, TRANSPORT).keys() == {"return"}
        third = travel_result(date_changed, COMPARE)
        assert third["revision"] == 3
        assert all(offer["source"] != "user" for offer in third["offers"])
        assert third["request"]["arrive_by"] == revised["request"]["arrive_by"]

        done, path, body = await answer_waiting(api, receipt, date_changed, "保存方案 1")
        selected = travel_result(done, SAVE)
        assert selected["selected_plan_id"] == third["plans"][0]["id"]
        assert done[-1]["type"] == "run.completed"
        assert (await api.post(path, json=body)).json()["replayed"] is True
        stored = (await chat.seo_chat_harness_store.latest_dispatch_state("conversation"))["travel"]
        assert [revision["revision"] for revision in stored["versions"]] == [1, 2, 3]
        assert stored["snapshot"]["selected_plan_id"] == selected["selected_plan_id"]


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
