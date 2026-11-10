from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.agent.providers.fake import FakeModelProvider
from src.api.routes import agent as agent_route
from src.server import app


@pytest.mark.asyncio
async def test_agent_api_streams_terminal_event(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_route, "build_model_provider", lambda: FakeModelProvider.text("测试回复")
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/agent/runs", json={"messages": [{"role": "user", "content": "你好"}]}
        )
        assert created.status_code == 201
        run_id = created.json()["run_id"]
        response = await client.get(f"/api/v1/agent/runs/{run_id}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: run.completed" in response.text
    assert "event: message.delta" in response.text


@pytest.mark.asyncio
async def test_cancel_is_idempotent(monkeypatch) -> None:
    monkeypatch.setattr(agent_route, "build_model_provider", lambda: FakeModelProvider.text("ok"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/agent/runs", json={"messages": [{"role": "user", "content": "你好"}]}
        )
        run_id = created.json()["run_id"]
        first = await client.post(f"/api/v1/agent/runs/{run_id}/cancel")
        second = await client.post(f"/api/v1/agent/runs/{run_id}/cancel")
    assert first.status_code == 200
    assert second.status_code == 200
