from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from src.agent.loop import AgentLoop
from src.agent.models import AgentRun, RunStatus
from src.agent.providers.base import ModelChunk, ToolCall
from src.agent.providers.fake import FakeModelProvider
from src.agent.run_manager import RunManager
from src.agent.tools.base import AgentTool, ToolResult, ToolRuntimeContext
from src.agent.tools.registry import ToolRegistry
from src.seo_agent.db.database import SeoAgentDatabase


class CountingTool(AgentTool):
    name = "counting_tool"
    description = "count once"
    parameters_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    render_hint = "table"

    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        arguments: dict[str, Any],
        runtime: ToolRuntimeContext | None = None,
    ) -> ToolResult:
        self.calls += 1
        return ToolResult(
            success=True,
            data={
                "schema_version": "1.0",
                "capability": "test",
                "view_type": "table",
                "title": {"fallback": "测试数据"},
                "provenance": {"source": "test", "source_label": "Test"},
                "limitations": [],
                "columns": [{"key": "value", "title": "值"}],
                "rows": [{"value": 1}],
            },
        )


@pytest.mark.asyncio
async def test_chat_harness_persists_task_thread_run_event_checkpoint_and_summary(
    tmp_path: Path,
) -> None:
    from src.agent.harness.store import AgentChatHarnessStore

    database = SeoAgentDatabase(tmp_path / "seo-agent.db")
    await database.startup()
    store = AgentChatHarnessStore(database)
    try:
        await database.execute(
            "INSERT INTO sites(id, name, domain, default_country, default_language, timezone, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
            (
                "site_1",
                "Demo",
                "example.com",
                "US",
                "en-US",
                "UTC",
                "2026-07-27T00:00:00Z",
                "2026-07-27T00:00:00Z",
            ),
        )
        created = await store.create_task_run(
            site_id="site_1",
            conversation_id="conversation_1",
            title="分析自然流量下降",
            user_intent="分析自然流量下降",
            messages=[{"role": "user", "content": "分析自然流量下降"}],
            model_provider="fake",
            model_name="fake-model",
        )
        assert created.task_id.startswith("agtask_")
        assert created.thread_id.startswith("chat_")
        assert created.run_id.startswith("run_")

        run = AgentRun(
            id=created.run_id,
            messages=[{"role": "user", "content": "分析自然流量下降"}],
            task_id=created.task_id,
            thread_id=created.thread_id,
            site_id="site_1",
            conversation_id="conversation_1",
        )
        run.status = RunStatus.RUNNING
        event = run.emit("stage.changed", {"stage": "calling_model"})
        await store.persist_event(run, event)
        await store.save_checkpoint(
            run,
            messages=run.messages,
            iteration=0,
            tool_count=0,
            pending_tool_calls=[],
            stage="calling_model",
            token_usage={"total_tokens": 12},
        )

        task = await store.get_task(created.task_id)
        detail = await store.get_run(created.run_id)
        assert task["status"] == "RUNNING"
        assert task["status_group"] == "running"
        assert detail["thread_id"] == created.thread_id
        assert detail["events"][0]["event_type"] == "stage.changed"
        assert detail["checkpoint"]["iteration"] == 0
        assert detail["checkpoint"]["survival_summary"]["objective"] == "分析自然流量下降"
        assert len(await store.list_tasks(site_id="site_1", status_group="running")) == 1
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_restart_resumes_after_completed_tool_without_repeating_it(tmp_path: Path) -> None:
    from src.agent.harness.persistence import StoreRunPersistence
    from src.agent.harness.store import AgentChatHarnessStore

    database = SeoAgentDatabase(tmp_path / "seo-agent.db")
    await database.startup()
    store = AgentChatHarnessStore(database)
    await database.execute(
        "INSERT INTO sites(id, name, domain, default_country, default_language, timezone, "
        "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
        (
            "site_1",
            "Demo",
            "example.com",
            "US",
            "en-US",
            "UTC",
            "2026-07-27T00:00:00Z",
            "2026-07-27T00:00:00Z",
        ),
    )
    created = await store.create_task_run(
        site_id="site_1",
        conversation_id="conversation_1",
        title="恢复测试",
        user_intent="查询一次工具后继续",
        messages=[{"role": "user", "content": "查询一次工具后继续"}],
        model_provider="fake",
        model_name="fake-model",
    )
    tool = CountingTool()
    registry = ToolRegistry([tool])
    manager = RunManager()
    run = manager.create_run(
        [{"role": "user", "content": "查询一次工具后继续"}],
        run_id=created.run_id,
        task_id=created.task_id,
        thread_id=created.thread_id,
        site_id="site_1",
        conversation_id="conversation_1",
    )
    first_provider = FakeModelProvider(
        [
            [
                ModelChunk(
                    tool_calls=[ToolCall(id="call_1", name="counting_tool", arguments={})],
                    finish_reason="tool_calls",
                )
            ],
            [ModelChunk(text="不会到达"), ModelChunk(finish_reason="stop")],
        ]
    )
    persistence = StoreRunPersistence(store)

    original_checkpoint = persistence.save_checkpoint

    async def pause_after_tool(*args: Any, **kwargs: Any) -> None:
        await original_checkpoint(*args, **kwargs)
        if kwargs.get("stage") == "tool_completed":
            run.request_pause()
            raise asyncio.CancelledError

    persistence.save_checkpoint = pause_after_tool  # type: ignore[method-assign]
    first_task = asyncio.create_task(
        AgentLoop(first_provider, registry, persistence=persistence).run(run)
    )
    with pytest.raises(asyncio.CancelledError):
        await first_task
    assert tool.calls == 1

    await database.shutdown()
    database = SeoAgentDatabase(tmp_path / "seo-agent.db")
    await database.startup()
    store = AgentChatHarnessStore(database)
    restored = await store.restore_run(created.run_id)
    assert restored.status == RunStatus.RECOVERING
    assert restored.pending_tool_calls == []
    assert any(message.get("role") == "tool" for message in restored.messages)

    second_provider = FakeModelProvider(
        [[ModelChunk(text="基于已有工具结果完成。"), ModelChunk(finish_reason="stop")]]
    )
    await AgentLoop(
        second_provider,
        registry,
        persistence=StoreRunPersistence(store),
    ).run(restored)

    assert restored.status == RunStatus.COMPLETED
    assert tool.calls == 1
    assert second_provider.calls == 1
    final = await store.get_run(created.run_id)
    assert final["status"] == "COMPLETED"
    assert final["task_status"] == "COMPLETED"
    await database.shutdown()


def test_survival_summary_compacts_long_context_without_losing_current_turn() -> None:
    from src.agent.harness.summary import build_survival_summary, compact_model_context

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "SEO system"},
        *[
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"历史消息 {index} " + "x" * 200,
            }
            for index in range(20)
        ],
        {"role": "user", "content": "检查当前首页 TDK"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_meta",
                    "type": "function",
                    "function": {"name": "seo_page_metadata", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_meta",
            "name": "seo_page_metadata",
            "content": '{"success":true,"data":{"title":{"fallback":"页面元信息"},"checks":[]}}',
        },
    ]
    events = [
        {
            "type": "tool.completed",
            "payload": {
                "tool_call_id": "call_meta",
                "name": "seo_page_metadata",
                "result": {
                    "success": True,
                    "data": {
                        "title": {"fallback": "页面元信息检查"},
                        "provenance": {"source_label": "页面源 HTML"},
                        "checks": [{"label": "Title", "value": "Example"}],
                        "limitations": ["不执行 JavaScript"],
                    },
                },
            },
        }
    ]
    summary = build_survival_summary(
        messages=messages,
        events=events,
        stage="tool_completed",
        token_usage={"total_tokens": 9000},
    )
    compacted = compact_model_context(
        messages,
        summary,
        message_limit=8,
        character_limit=1200,
    )

    assert summary.objective == "检查当前首页 TDK"
    assert summary.completed_tools[0]["name"] == "seo_page_metadata"
    assert any("Example" in fact for fact in summary.confirmed_facts)
    assert "不执行 JavaScript" in summary.limitations
    assert compacted[0]["role"] == "system"
    assert "Survival Summary" in compacted[1]["content"]
    assert any(message.get("content") == "检查当前首页 TDK" for message in compacted)
    assert any(message.get("role") == "tool" for message in compacted)
    assert len(compacted) < len(messages)
    assert sum(len(json.dumps(item, ensure_ascii=False)) for item in compacted) <= 1200
    tool_message = next(item for item in compacted if item.get("role") == "tool")
    assert "完整工具结果已保存在持久化 Checkpoint" in tool_message["content"]


@pytest.mark.asyncio
async def test_durable_chat_api_exposes_task_and_run_status(tmp_path: Path, monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from src.agent.harness.store import AgentChatHarnessStore
    from src.api.routes import seo_agent_chat as route
    from src.seo_agent.services.system import SeoAgentSystemService
    from src.server import app

    database = SeoAgentDatabase(tmp_path / "api-harness.db")
    await database.startup()
    system = SeoAgentSystemService(database)
    site = await system.create_site(
        name="Demo",
        domain="example.com",
        default_country="US",
        default_language="en-US",
        timezone="UTC",
    )
    store = AgentChatHarnessStore(database)
    monkeypatch.setattr(route, "seo_agent_system_service", system)
    monkeypatch.setattr(route, "seo_chat_harness_store", store)
    monkeypatch.setattr(route, "build_seo_model_provider", lambda: FakeModelProvider.text("完成"))

    class LiveFacade:
        def live_capabilities(self) -> set[Any]:
            from src.models.common import Capability

            return {Capability.SEARCH_METRICS}

        async def query_live(self, **kwargs: Any) -> dict[str, Any]:
            return {"meta": {}, "data": {}}

    monkeypatch.setattr(
        route,
        "build_seo_agent_registry",
        lambda _facade, site_data: __import__(
            "src.seo_agent.chat.tools", fromlist=["build_seo_agent_registry"]
        ).build_seo_agent_registry(LiveFacade(), site_data),
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/seo-agent/chat/runs",
                json={
                    "site_id": site["id"],
                    "conversation_id": "conversation_1",
                    "messages": [{"role": "user", "content": "分析当前站点"}],
                },
            )
            assert created.status_code == 201
            payload = created.json()
            assert payload["task_id"].startswith("agtask_")
            assert payload["thread_id"].startswith("chat_")
            events = await client.get(payload["events_url"])
            tasks = await client.get(
                "/api/v1/seo-agent/chat/tasks",
                params={"site_id": site["id"], "status_group": "ended"},
            )
            detail = await client.get(f"/api/v1/seo-agent/chat/runs/{payload['run_id']}")

        assert events.status_code == 200
        assert "event: run.completed" in events.text
        assert tasks.status_code == 200
        assert tasks.json()[0]["id"] == payload["task_id"]
        assert tasks.json()[0]["status_group"] == "ended"
        assert tasks.json()[0]["prompt_version"] == "seo-chat-v1"
        assert tasks.json()[0]["prompt_hash"]
        assert tasks.json()[0]["available_tools"] == ["seo_search_metrics"]
        assert detail.status_code == 200
        assert detail.json()["checkpoint"]["stage"] == "completed"
        assert detail.json()["task_status"] == "COMPLETED"
    finally:
        await database.shutdown()


@pytest.mark.asyncio
async def test_recovery_service_loads_pending_runs_for_scheduler(tmp_path: Path) -> None:
    from src.agent.harness.store import AgentChatHarnessStore
    from src.agent.run_manager import RunManager
    from src.seo_agent.chat.service import SeoChatRecoveryService

    database = SeoAgentDatabase(tmp_path / "recover.db")
    await database.startup()
    await database.execute(
        "INSERT INTO sites(id, name, domain, default_country, default_language, timezone, "
        "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
        (
            "site_1",
            "Demo",
            "example.com",
            "US",
            "en-US",
            "UTC",
            "2026-07-27T00:00:00Z",
            "2026-07-27T00:00:00Z",
        ),
    )
    store = AgentChatHarnessStore(database)
    created = await store.create_task_run(
        site_id="site_1",
        conversation_id="conversation_1",
        title="待恢复",
        user_intent="待恢复",
        messages=[{"role": "user", "content": "待恢复"}],
        model_provider="fake",
        model_name="fake-model",
    )
    manager = RunManager()
    service = SeoChatRecoveryService(store, manager)
    scheduled: list[str] = []

    async def schedule(run: AgentRun) -> None:
        scheduled.append(run.id)

    result = await service.recover_active(schedule)

    assert result == {"recovered": 1, "blocked": 0}
    assert scheduled == [created.run_id]
    assert manager.get(created.run_id) is not None
    assert manager.get(created.run_id).status == RunStatus.RECOVERING
    await database.shutdown()


@pytest.mark.asyncio
async def test_store_does_not_persist_streaming_token_deltas(tmp_path: Path) -> None:
    from src.agent.harness.store import AgentChatHarnessStore

    database = SeoAgentDatabase(tmp_path / "delta.db")
    await database.startup()
    await database.execute(
        "INSERT INTO sites(id, name, domain, default_country, default_language, timezone, "
        "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
        (
            "site_1",
            "Demo",
            "example.com",
            "US",
            "en-US",
            "UTC",
            "2026-07-27T00:00:00Z",
            "2026-07-27T00:00:00Z",
        ),
    )
    store = AgentChatHarnessStore(database)
    created = await store.create_task_run(
        site_id="site_1",
        conversation_id="conversation_1",
        title="delta",
        user_intent="delta",
        messages=[{"role": "user", "content": "delta"}],
        model_provider="fake",
        model_name="fake",
    )
    run = AgentRun(
        id=created.run_id,
        messages=[{"role": "user", "content": "delta"}],
        task_id=created.task_id,
        thread_id=created.thread_id,
        site_id="site_1",
    )
    await store.persist_event(run, run.emit("message.delta", {"delta": "一"}))
    await store.persist_event(run, run.emit("message.completed", {"content": "一"}))

    rows = await database.fetch_all(
        "SELECT event_type FROM agent_run_events WHERE agent_run_id = ? ORDER BY sequence",
        (created.run_id,),
    )
    assert [row["event_type"] for row in rows] == ["message.completed"]
    await database.shutdown()


@pytest.mark.asyncio
async def test_event_stream_reconnect_replays_only_stable_events_after_cursor() -> None:
    from datetime import UTC, datetime

    from src.agent.models import AgentEvent
    from src.api.routes import seo_agent_chat as route

    run_id = "run_reconnect_cursor"
    run = AgentRun(
        id=run_id,
        messages=[],
        status=RunStatus.COMPLETED,
        sequence=6,
        events=[
            AgentEvent(
                event_id="evt_1",
                run_id=run_id,
                type="run.started",
                sequence=1,
                created_at=datetime.now(UTC),
                payload={"status": "running"},
            ),
            AgentEvent(
                event_id="evt_2",
                run_id=run_id,
                type="tool.completed",
                sequence=2,
                created_at=datetime.now(UTC),
                payload={"tool_call_id": "tool_1", "name": "seo_page_metadata"},
            ),
            AgentEvent(
                event_id="evt_3",
                run_id=run_id,
                type="message.delta",
                sequence=3,
                created_at=datetime.now(UTC),
                payload={"delta": "旧"},
            ),
            AgentEvent(
                event_id="evt_4",
                run_id=run_id,
                type="message.delta",
                sequence=4,
                created_at=datetime.now(UTC),
                payload={"delta": "文本"},
            ),
            AgentEvent(
                event_id="evt_5",
                run_id=run_id,
                type="message.completed",
                sequence=5,
                created_at=datetime.now(UTC),
                payload={"content": "完整答案"},
            ),
            AgentEvent(
                event_id="evt_6",
                run_id=run_id,
                type="run.completed",
                sequence=6,
                created_at=datetime.now(UTC),
                payload={"status": "completed"},
            ),
        ],
    )
    route.run_manager.register(run)

    frames = [frame async for frame in route._event_stream(run_id, after_sequence=2)]
    payload = "".join(frames)

    assert "message.delta" not in payload
    assert "message.completed" in payload
    assert "run.completed" in payload
    assert "id: 5" in payload
    assert "id: 6" in payload


@pytest.mark.asyncio
async def test_events_endpoint_accepts_last_event_id_header() -> None:
    from datetime import UTC, datetime

    from httpx import ASGITransport, AsyncClient

    from src.agent.models import AgentEvent
    from src.api.routes import seo_agent_chat as route
    from src.server import app

    run_id = "run_last_event_header"
    route.run_manager.register(
        AgentRun(
            id=run_id,
            messages=[],
            status=RunStatus.COMPLETED,
            sequence=4,
            events=[
                AgentEvent(
                    event_id="evt_h1",
                    run_id=run_id,
                    type="run.started",
                    sequence=1,
                    created_at=datetime.now(UTC),
                    payload={"status": "running"},
                ),
                AgentEvent(
                    event_id="evt_h2",
                    run_id=run_id,
                    type="tool.completed",
                    sequence=2,
                    created_at=datetime.now(UTC),
                    payload={"tool_call_id": "tool_1", "name": "seo_page_metadata"},
                ),
                AgentEvent(
                    event_id="evt_h3",
                    run_id=run_id,
                    type="message.completed",
                    sequence=3,
                    created_at=datetime.now(UTC),
                    payload={"content": "完整答案"},
                ),
                AgentEvent(
                    event_id="evt_h4",
                    run_id=run_id,
                    type="run.completed",
                    sequence=4,
                    created_at=datetime.now(UTC),
                    payload={"status": "completed"},
                ),
            ],
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/seo-agent/chat/runs/{run_id}/events",
            headers={"Last-Event-ID": "2"},
        )

    assert response.status_code == 200
    assert "id: 1" not in response.text
    assert "id: 2" not in response.text
    assert "id: 3" in response.text
    assert "id: 4" in response.text
