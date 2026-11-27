from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.agent.harness.store import AgentChatHarnessStore
from src.agent.providers.errors import ModelConfigurationError
from src.agent.run_manager import RunManager
from src.api.routes import agent
from src.api.routes.workspace import conversations as chat
from src.core.config import Settings
from src.models.common import Capability
from src.observability.client import ObservabilityClient
from src.observability.schema import ModelCallRow
from src.observability.storage import ObservabilityDatabase
from src.seo_agent import runtime
from src.seo_agent.db.database import SeoAgentDatabase
from src.seo_agent.services.system import SeoAgentSystemService

KEYS = {"openai": "sk-synthetic-loopback-openai", "deepseek": "sk-synthetic-loopback-deepseek"}
TARGET = "https://example.com/classes"
PAGE_TITLE = "Community class timetable"
PRIVATE_ERROR = "upstream-debug-detail-do-not-publish"


class SyntheticPageSource:
    def live_capabilities(self):
        return {Capability.PAGE_METADATA}

    async def query_live(self, *, params, **_kwargs):
        return {
            "meta": {"source": "page_html"},
            "data": {
                "source": "page_html", "title": PAGE_TITLE, "h1s": [PAGE_TITLE],
                "status_code": 200, "requested_url": params["url"], "final_url": params["url"],
            },
        }


class TextUpstream:
    """Local Chat Completions double for both pytest and a browser-bound API server."""

    def __init__(self):
        self.app = web.Application()
        self.app.router.add_post("/{provider}/v1/chat/completions", self.completion)
        self.requests = []
        self.error = None
        self.truncate = False
        self.malformed = False
        self.delay = 0.0
        self.route_reply = {"route": "seo", "needs_page": True, "target_index": 1}

    async def completion(self, request):
        provider = request.match_info["provider"]
        payload = await request.json()
        self.requests.append((provider, payload))
        if request.headers.get("Authorization") != f"Bearer {KEYS[provider]}":
            return web.json_response({"error": {"code": "invalid_api_key"}}, status=401)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            status, code = self.error
            error_body = {"error": {"code": code, "message": f"{PRIVATE_ERROR} {KEYS[provider]}"}}
            if status != 429:
                response = web.json_response(error_body, status=status)
                response.force_close()
                return response
            response = web.StreamResponse(status=status, headers={"Content-Type": "application/json"})
            response.force_close()
            await response.prepare(request)
            wire = json.dumps(error_body).encode()
            await response.write(wire[:10])
            await asyncio.sleep(0.01)
            await response.write(wire[10:])
            return response

        common = {"id": "completion-test", "model": f"{provider}-actual-snapshot", "created": 1}
        usage = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10,
                 "prompt_tokens_details": {"cached_tokens": 2}}
        tools = [item["function"]["name"] for item in payload.get("tools", [])]
        messages = payload["messages"]
        if self.malformed:
            if payload.get("stream"):
                data = json.dumps({"error": {"message": f"{PRIVATE_ERROR} {KEYS[provider]}"}})
                return web.Response(text=f"data: {data}\n\n", content_type="text/event-stream")
            deltas, finish = [{"content": f"{PRIVATE_ERROR} {KEYS[provider]}"}], "stop"
        elif payload.get("response_format") == {"type": "json_object"}:
            answer = json.dumps(self.route_reply)
            deltas, finish = [{"content": answer}], "stop"
        elif "TaskPrdContent" in tools:
            arguments = json.dumps({
                "background": "Synthetic brief", "target_intent": "find class times",
                "target_keywords": ["class times"], "target_audience": "Local residents",
                "competitor_gap": "none recorded", "content_type": "landing page",
                "content_outline": ["Timetable"], "seo_title": PAGE_TITLE,
                "seo_description": "Browse the community classes.",
                "acceptance_criteria": ["Show each class time"],
            })
            deltas, finish = [{"tool_calls": [{
                **({"index": 0} if payload.get("stream") else {}),
                "id": "call_prd", "type": "function",
                "function": {"name": "TaskPrdContent", "arguments": arguments},
            }]}], "tool_calls"
        elif "archive_lookup" in tools and any(message["role"] == "tool" for message in messages):
            deltas, finish = [{"content": "合成档案编号已核对。"}], "stop"
        elif any(message["role"] == "tool" for message in messages):
            deltas, finish = [{"content": "已读取"}, {"content": "合成回执。"}], "stop"
        else:
            if "seo_page_metadata" in tools:
                name, arguments = "seo_page_metadata", {"url": TARGET}
            elif "archive_lookup" in tools:
                name, arguments = "archive_lookup", {}
            else:
                name, arguments = "calculator", {"expression": "2 + 3"}
            fragments = [json.dumps(arguments)[:8], json.dumps(arguments)[8:]]
            deltas = [
                {"tool_calls": [{"index": 0, "id": "call_synthetic", "type": "function",
                                 "function": {"name": name, "arguments": fragments[0]}}]},
                {"tool_calls": [{"index": 0, "function": {"arguments": fragments[1]}}]},
            ]
            finish = "tool_calls"

        if not payload.get("stream"):
            response = web.json_response({
                **common, "object": "chat.completion", "usage": usage,
                "choices": [{"index": 0, "message": {"role": "assistant", **deltas[0]},
                             "finish_reason": finish}],
            })
            response.force_close()
            return response

        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        response.force_close()
        await response.prepare(request)
        deltas[0]["role"] = "assistant"
        for delta in deltas:
            await self.frame(response, common, delta, None)
        if not self.truncate:
            await self.frame(response, common, {}, finish)
            usage_frame = {**common, "object": "chat.completion.chunk", "choices": [], "usage": usage}
            await response.write(f"data: {json.dumps(usage_frame)}\r\n\r\n".encode())
            await response.write(b"data: [DONE]\r\n\r\n")
        return response

    @staticmethod
    async def frame(response, common, delta, finish):
        data = json.dumps({
            **common, "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }, ensure_ascii=False)
        wire = f"data: {data}\r\n\r\n".encode()
        split = wire.find("已".encode()) + 1 if "已" in data else len(wire) // 2
        await response.write(wire[:split])
        await response.write(wire[split:])


@asynccontextmanager
async def model_api(tmp_path, monkeypatch, *, openai=True, deepseek=False, selection="auto", timeout=5):
    upstream = TextUpstream()
    server = web.AppRunner(upstream.app, access_log=None)
    await server.setup()
    try:
        await web.TCPSite(server, "127.0.0.1", 0).start()
        port = server.addresses[0][1]
        monkeypatch.setenv("WORKSPACE_SOURCE_CONFIG", str(tmp_path / "source-settings.json"))
        config = Settings(
            _env_file=None, agent_model_provider=selection,
            openai_api_key=KEYS["openai"] if openai else "",
            deepseek_api_key=KEYS["deepseek"] if deepseek else "",
            openai_base_url=f"http://127.0.0.1:{port}/openai/v1",
            deepseek_base_url=f"http://127.0.0.1:{port}/deepseek/v1",
            openai_model="openai-requested-alias", deepseek_model="deepseek-requested-alias",
            openai_request_timeout=timeout, deepseek_request_timeout=timeout,
        )
        for module in (agent, chat, runtime):
            monkeypatch.setattr(module, "settings", config)
        manager = RunManager()
        monkeypatch.setattr(chat, "run_manager", manager)
        monkeypatch.setattr(agent, "run_manager", manager)
        monkeypatch.setattr(chat, "facade", SyntheticPageSource())
        from src.seo_agent import specialist as seo_specialist

        monkeypatch.setattr(seo_specialist, "configured_citation_client", lambda *_: None)
        monkeypatch.setattr(runtime.structured_model_automation.model_runner, "prompt_registry", None)
        monkeypatch.setattr(runtime.structured_model_automation.model_runner, "observability", None)
        api = FastAPI()
        api.include_router(chat.router, prefix="/api/v1")
        api.include_router(agent.router, prefix="/api/v1")
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
            yield client, upstream, manager
    finally:
        await server.cleanup()


async def run_api(client, manager, channel):
    created = await client.post(f"/api/v1/{channel}/runs", json={
        "messages": [{"role": "user", "content": f"请读取 {TARGET} 的网页标题。"}],
    })
    assert created.status_code == 201, created.text
    returned = created.json()
    events = await asyncio.wait_for(client.get(returned["events_url"]), timeout=8)
    run = manager.get(returned["run_id"])
    await run.task
    return events.text, run


@pytest.mark.asyncio
@pytest.mark.parametrize(("openai", "deepseek", "selection", "provider"), [
    (True, False, "auto", "openai"), (False, True, "auto", "deepseek"),
    (True, True, "auto", "openai"), (True, True, "deepseek", "deepseek"),
])
async def test_normal_chat_and_agent_use_selected_upstream_and_round_trip_tools(
    tmp_path, monkeypatch, openai, deepseek, selection, provider,
):
    async with model_api(tmp_path, monkeypatch, openai=openai, deepseek=deepseek, selection=selection) as (
        client, upstream, manager,
    ):
        workspace_events, run = await run_api(client, manager, "chat")
        detail = (await client.get(f"/api/v1/chat/runs/{run.id}")).json()
        assert detail["model_provider"] == provider
        assert detail["model_name"] == f"{provider}-actual-snapshot", run.error
        agent_events, _ = await run_api(client, manager, "agent")
    for events in (workspace_events, agent_events):
        assert "event: tool.completed" in events
        assert "event: message.delta" in events
        assert "已读取合成回执。" in events
        assert "event: run.completed" in events
        assert all(key not in events for key in KEYS.values())
    assert [recipient for recipient, _ in upstream.requests] == [provider] * 5
    planner, first_workspace, next_workspace, first_agent, next_agent = [
        payload for _, payload in upstream.requests
    ]
    assert planner["response_format"] == {"type": "json_object"}
    for first, reply, name in (
        (first_workspace, next_workspace, "seo_page_metadata"),
        (first_agent, next_agent, "calculator"),
    ):
        assert first["model"] == f"{provider}-requested-alias"
        assert first["stream"] is True and first["stream_options"] == {"include_usage": True}
        assert first["tool_choice"] == "auto"
        assert name in {tool["function"]["name"] for tool in first["tools"]}
        call = next(message for message in reply["messages"] if message.get("tool_calls"))["tool_calls"][0]
        result = next(message for message in reply["messages"] if message["role"] == "tool")
        assert call["id"] == result["tool_call_id"] == "call_synthetic"
        assert call["function"]["name"] == name
        assert json.loads(result["content"])["success"]
        if provider == "openai":
            assert set(result) == {"role", "tool_call_id", "content"}
            assert all(tool["function"]["strict"] is False for tool in first["tools"])
            assert not {"thinking", "temperature", "max_tokens"}.intersection(first)
        else:
            assert first["thinking"] == {"type": "disabled"}
        if name == "seo_page_metadata":
            assert PAGE_TITLE in result["content"]
    if provider == "openai":
        assert not {"thinking", "temperature", "max_tokens"}.intersection(planner)
    assert run.token_usage["cached_tokens"] == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", ["openai", "deepseek"])
async def test_independent_seo_text_workflow_uses_selected_provider(tmp_path, monkeypatch, selected):
    async with model_api(
        tmp_path, monkeypatch, openai=selected == "openai", deepseek=selected == "deepseek",
    ) as (_, upstream, _):
        result = await runtime.structured_model_automation.prepare_prd({
            "site_id": "synthetic", "title": "Community classes", "opportunities": [
                {"id": "synthetic-1", "title": "Make the timetable discoverable",
                 "normalized_intent": "class times"},
            ],
        })
    assert result.output.seo_title == PAGE_TITLE
    assert result.usage["total_tokens"] == 10
    [(provider, payload)] = upstream.requests
    assert provider == selected
    assert payload["model"] == f"{selected}-requested-alias"
    assert payload["tool_choice"]["function"]["name"] == "TaskPrdContent"
    if selected == "openai":
        assert not {"thinking", "temperature", "max_tokens"}.intersection(payload)
    else:
        assert payload["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("openai", "deepseek", "selection", "hint"), [
    (False, False, "auto", "数据源管理"),
    (True, False, "deepseek", "DEEPSEEK_API_KEY"),
    (False, True, "openai", "OPENAI_API_KEY"),
])
async def test_missing_selected_credentials_fail_without_a_request(
    tmp_path, monkeypatch, openai, deepseek, selection, hint,
):
    async with model_api(tmp_path, monkeypatch, openai=openai, deepseek=deepseek, selection=selection) as (
        client, upstream, _,
    ):
        for channel in ("chat", "agent"):
            response = await client.post(f"/api/v1/{channel}/runs", json={
                "messages": [{"role": "user", "content": "Read a page"}],
            })
            assert response.status_code == 503
            assert hint in response.json()["detail"]
        with pytest.raises(ModelConfigurationError, match=hint):
            runtime.build_structured_model()
    assert upstream.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["openai_model", "openai_base_url"])
async def test_missing_model_or_endpoint_does_not_fall_through(tmp_path, monkeypatch, field):
    async with model_api(tmp_path, monkeypatch, deepseek=True) as (client, upstream, _):
        config = chat.settings.model_copy(update={field: ""})
        monkeypatch.setattr(chat, "settings", config)
        monkeypatch.setattr(runtime, "settings", config)
        response = await client.post("/api/v1/chat/runs", json={
            "messages": [{"role": "user", "content": "Read a page"}],
        })
        assert response.status_code == 503 and field.upper() in response.json()["detail"]
        with pytest.raises(ModelConfigurationError, match=field.upper()):
            runtime.build_structured_model()
    assert upstream.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "code", "hint"), [
    (401, "invalid_api_key", "身份验证"), (403, "access_denied", "项目权限"),
    (404, "model_not_found", "模型名称"), (429, "rate_limit_exceeded", "调用限制"),
    (429, "insufficient_quota", "可用额度不足"),
    (None, "timeout", "请求超时"), (None, "truncated", "响应不完整"),
    (None, "malformed", "格式无效"),
])
async def test_safe_model_failure_events_have_no_implicit_fallback(
    tmp_path, monkeypatch, caplog, status, code, hint,
):
    caplog.set_level("DEBUG", logger="openai")
    async with model_api(tmp_path, monkeypatch, deepseek=True, timeout=0.5) as (client, upstream, manager):
        upstream.error = (status, code) if status else None
        upstream.truncate = code == "truncated"
        upstream.malformed = code == "malformed"
        upstream.delay = 0.75 if code == "timeout" else 0
        channels = ("agent",) if upstream.truncate else ("agent", "chat")
        for channel in channels:
            events, run = await run_api(client, manager, channel)
            assert "event: run.failed" in events and "event: run.completed" not in events
            assert hint in run.error
            assert PRIVATE_ERROR not in events
            assert all(key not in events for key in KEYS.values())
        assert [provider for provider, _ in upstream.requests] == ["openai"] * len(channels)
    assert PRIVATE_ERROR not in caplog.text
    assert all(key not in caplog.text for key in KEYS.values())


@pytest.mark.asyncio
async def test_observability_records_returned_model_and_sanitized_failure(pg_url, tmp_path, monkeypatch):
    database = ObservabilityDatabase(pg_url)
    observer = ObservabilityClient(database, project_key="seo-agent")
    business_database = SeoAgentDatabase(pg_url)
    store = AgentChatHarnessStore(business_database)
    async with model_api(tmp_path, monkeypatch, deepseek=True) as (client, upstream, manager):
        await observer.startup()
        try:
            await business_database.startup()
            monkeypatch.setattr(chat, "seo_chat_harness_store", store)
            monkeypatch.setattr(chat, "seo_agent_system_service", SeoAgentSystemService(business_database))
            monkeypatch.setattr(chat, "observability_database", database)
            monkeypatch.setattr(chat, "observability_client", observer)
            monkeypatch.setattr(runtime.structured_model_automation.model_runner, "observability", observer)
            events, completed = await run_api(client, manager, "chat")
            assert "event: run.completed" in events, completed.error
            restored = await store.restore_run(completed.id, mark_recovering=False)
            assert (restored.model_provider, restored.model_name) == ("openai", "openai-actual-snapshot")
            upstream.error = (401, "invalid_api_key")
            _, failed = await run_api(client, manager, "chat")
            request_count = len(upstream.requests)
            monkeypatch.setattr(chat, "settings", chat.settings.model_copy(
                update={"agent_model_provider": "deepseek"},
            ))
            retry = await client.post(f"/api/v1/chat/runs/{failed.id}/resume")
            assert retry.status_code == 503 and "请恢复原服务" in retry.json()["detail"]
            assert len(upstream.requests) == request_count
            calls = await database.fetch_all(select(ModelCallRow.__table__))
            succeeded = [row for row in calls if row["error_code"] is None]
            assert len(succeeded) == 3
            assert {(row["provider"], row["model"]) for row in succeeded} == {
                ("openai", "openai-actual-snapshot"),
            }
            [failure] = [row for row in calls if row["error_code"] is not None]
            assert failure["model"] == "openai-requested-alias"
            assert failure["error_code"] == "HTTP_401" and failure["total_tokens"] is None
            traces = [await observer.get_trace(row["id"]) for row in await observer.list_traces()]
            assert PRIVATE_ERROR not in str(traces) and KEYS["openai"] not in str(traces)
        finally:
            await business_database.shutdown()
            await observer.shutdown()


@pytest.mark.asyncio
async def test_one_registration_drives_promptops_tools_and_recovery(pg_url, tmp_path, monkeypatch):
    from registered_archive_fixture import ARCHIVE_SPECIALIST

    from src.agent.workspace.bootstrap import workspace_specialists
    from src.agent.workspace.registry import SpecialistRegistry
    from src.agent.workspace.routing import routing_prompt
    from src.promptops.registry import PromptRegistry
    from src.seo_agent.automation.prompts import SeoPromptSeeder

    specialists = SpecialistRegistry(
        (*workspace_specialists, ARCHIVE_SPECIALIST), handoffs=workspace_specialists.handoffs,
    )
    old_prompt = routing_prompt(workspace_specialists)[0]
    new_prompt, definition = routing_prompt(specialists)
    assert new_prompt != old_prompt
    business_db, prompt_db = SeoAgentDatabase(pg_url), ObservabilityDatabase(pg_url)
    prompts = PromptRegistry(prompt_db)
    store = AgentChatHarnessStore(business_db)
    await business_db.startup()
    try:
        await SeoPromptSeeder(prompts).seed()
        deployed = (await SeoPromptSeeder(prompts, specialists=specialists).seed())[new_prompt]
        assert deployed["output_schema_data"] == definition["output_schema"]
        assert ARCHIVE_SPECIALIST.id in deployed["output_schema_data"]["properties"]["route"]["enum"]
        assert (await prompts.resolve_version(old_prompt, environment="production"))["id"] != deployed["id"]
        marker = "验证部署版本的合成标记"
        override = await prompts.create_version(
            prompt_id=deployed["prompt_id"], system_template=f"{deployed['system_template']}\n{marker}",
            user_template=deployed["user_template"], output_schema=deployed["output_schema_data"],
        )
        await prompts.promote(deployed["prompt_id"], override["id"], environment="production")
        async with model_api(tmp_path, monkeypatch) as (client, upstream, manager):
            monkeypatch.setattr(chat, "workspace_specialists", specialists)
            monkeypatch.setattr(chat, "seo_chat_harness_store", store)
            monkeypatch.setattr(chat, "seo_agent_system_service", SeoAgentSystemService(business_db))
            monkeypatch.setattr(runtime.structured_model_automation.model_runner, "prompt_registry", prompts)
            upstream.route_reply = {"route": ARCHIVE_SPECIALIST.id, "needs_page": False}

            async def start(conversation_id):
                response = await client.post("/api/v1/chat/runs", json={
                    "conversation_id": conversation_id,
                    "messages": [{"role": "user", "content": "请核对合成档案。"}],
                })
                assert response.status_code == 201, response.text
                receipt = response.json()
                await asyncio.wait_for(client.get(receipt["events_url"]), timeout=8)
                await manager.get(receipt["run_id"]).task
                return f"/api/v1/chat/runs/{receipt['run_id']}"

            def payloads(detail, event):
                return [item["payload"] for item in detail["events"] if item["event_type"] == event]

            path = await start("archive-run")
            initial = (await client.get(path)).json()
            assert initial["status"] == "WAITING_FOR_INPUT", initial["error_message"]
            [waiting] = payloads(initial, "run.waiting_for_input")
            [assigned] = payloads(initial, "agent.dispatched")
            assert assigned["agent_profile"] == ARCHIVE_SPECIALIST.profile.public_data()
            model_system = upstream.requests[0][1]["messages"][0]["content"]
            assert marker in model_system and ARCHIVE_SPECIALIST.routing_prompt in model_system
            assert f'"{ARCHIVE_SPECIALIST.id}"' in model_system
            assert [tool["function"]["name"] for tool in upstream.requests[1][1]["tools"]] == ["archive_lookup"]

            directory = (await client.get("/api/v1/chat/tools")).json()["specialists"]
            row = next(item for item in directory if item["agent"] == ARCHIVE_SPECIALIST.id)
            [skill] = row["skills"]
            assert skill["tools"] == ["archive_lookup"]
            assert skill["usage"]["archive_lookup"]["inputs"] == "档案编号"
            assert ARCHIVE_SPECIALIST.public_skills[0].instruction in str(row)
            assert all(text not in str(row) for text in (marker, "内部运行条件", ARCHIVE_SPECIALIST.instructions))

            body = {"request_id": waiting["request_id"], "answer": "ARCH-001"}
            retried = await client.post(f"{path}/input", json=body)
            assert retried.is_success and retried.json()["run_id"] == initial["id"]
            await manager.get(initial["id"]).task
            final = (await client.get(path)).json()
            assert final["status"] == "COMPLETED", final["error_message"]
            [result] = [p for p in payloads(final, "tool.completed") if p["result"]["data"]]
            assert result["result"]["data"] == {"archive_id": "ARCH-001", "source": "合成档案库"}
            [done] = payloads(final, "agent.completed")
            assert done["agent_run_id"] == assigned["agent_run_id"]
            assert (await client.post(f"{path}/input", json=body)).json()["run_id"] == initial["id"]

            upstream.route_reply = {"route": "unregistered_route", "needs_page": False}
            unknown = (await client.get(await start("unknown-route"))).json()
            assert unknown["status"] == "WAITING_FOR_INPUT", unknown["error_message"]
            assert unknown["checkpoint"]["dispatch_state"]["route"] == "clarify"
            assert "未能匹配到可用专员" in str(payloads(unknown, "run.waiting_for_input"))
            assert not payloads(unknown, "agent.dispatched")
            assert "unregistered_route" not in str(unknown["checkpoint"]["dispatch_state"])

            upstream.route_reply = {"route": ARCHIVE_SPECIALIST.id, "needs_page": False}
            removed_path = await start("removed-specialist")
            old_record = (await client.get(removed_path)).json()
            [prior_request] = payloads(old_record, "run.waiting_for_input")
            monkeypatch.setattr(chat, "workspace_specialists", workspace_specialists)
            request_count = len(upstream.requests)
            response = await client.post(f"{removed_path}/input", json={
                "request_id": prior_request["request_id"], "answer": "ARCH-002",
            })
            assert response.status_code == 409
            assert "补充已保存" in response.json()["detail"] and "专员当前不可用" in response.json()["detail"]
            blocked = (await client.get(removed_path)).json()
            assert blocked["status"] == "RECOVERY_BLOCKED"
            assert "已有结果仍可查看" in blocked["error_message"]
            assert payloads(blocked, "tool.completed") == payloads(old_record, "tool.completed")
            response = await client.post(f"{removed_path}/resume")
            assert response.status_code == 409 and "专员当前不可用" in response.json()["detail"]
            assert len(upstream.requests) == request_count
            assert (await client.get(path)).json()["events"] == final["events"]

            monkeypatch.setattr(chat, "workspace_specialists", specialists)
            incompatible = await prompts.create_version(
                prompt_id=deployed["prompt_id"], system_template=deployed["system_template"],
                user_template=deployed["user_template"], output_schema=routing_prompt(workspace_specialists)[1]["output_schema"],
            )
            await prompts.promote(deployed["prompt_id"], incompatible["id"], environment="production")
            failed = (await client.get(await start("incompatible-prompt"))).json()
            assert failed["status"] == "FAILED"
            assert "输出约定与已登记专员不一致" in failed["error_message"]
            assert not payloads(failed, "agent.dispatched")
            assert len(upstream.requests) == request_count
    finally:
        await business_db.shutdown()
        await prompt_db.shutdown()
