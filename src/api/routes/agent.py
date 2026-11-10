from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.agent.loop import AgentLoop
from src.agent.models import AgentEvent
from src.agent.observability import AgentRunObserver
from src.agent.providers.base import ModelProvider
from src.agent.providers.deepseek import DeepSeekProvider
from src.agent.run_manager import run_manager
from src.agent.tools.registry import build_default_registry
from src.core.config import settings
from src.observability.runtime import observability_client, observability_database

router = APIRouter(prefix="/agent", tags=["Agent"])


class CreateRunRequest(BaseModel):
    conversation_id: str | None = None
    messages: list[dict] = Field(min_length=1)


class CreateRunResponse(BaseModel):
    run_id: str
    status: str
    events_url: str
    cancel_url: str


def build_model_provider() -> ModelProvider:
    return DeepSeekProvider(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        timeout=settings.deepseek_request_timeout,
    )


async def _execute(
    run_id: str,
    provider: ModelProvider,
    conversation_id: str | None = None,
) -> None:
    run = run_manager.get(run_id)
    if run is None:
        return
    observer = (
        AgentRunObserver(
            observability_client,
            operation_name="agent.chat",
            session_id=conversation_id,
            agent_name="chat-agent",
        )
        if observability_database.started
        else None
    )
    loop = AgentLoop(
        provider,
        build_default_registry(),
        max_iterations=settings.agent_max_iterations,
        max_tool_calls=settings.agent_max_tool_calls,
        tool_timeout=settings.agent_tool_timeout,
        observer=observer,
    )
    await loop.run(run)


@router.post("/runs", response_model=CreateRunResponse, status_code=status.HTTP_201_CREATED)
async def create_run(body: CreateRunRequest) -> CreateRunResponse:
    try:
        provider = build_model_provider()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    run = run_manager.create_run(body.messages)
    run.task = asyncio.create_task(
        _execute(run.id, provider, body.conversation_id),
        name=f"agent:{run.id}",
    )
    return CreateRunResponse(
        run_id=run.id,
        status=run.status.value,
        events_url=f"/api/v1/agent/runs/{run.id}/events",
        cancel_url=f"/api/v1/agent/runs/{run.id}/cancel",
    )


def _encode(event: AgentEvent) -> str:
    payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    return f"id: {event.sequence}\nevent: {event.type}\ndata: {payload}\n\n"


async def _event_stream(run_id: str) -> AsyncIterator[str]:
    run = run_manager.get(run_id)
    if run is None:
        return
    sent = 0
    while True:
        while sent < len(run.events):
            event = run.events[sent]
            sent += 1
            yield _encode(event)
        if run.terminal:
            return
        try:
            event = await asyncio.wait_for(run.queue.get(), timeout=15)
            if event.sequence <= sent:
                continue
            sent = event.sequence
            yield _encode(event)
        except TimeoutError:
            yield "event: heartbeat\ndata: {}\n\n"


@router.get("/runs/{run_id}/events")
async def stream_events(run_id: str) -> StreamingResponse:
    if run_manager.get(run_id) is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    return StreamingResponse(
        _event_stream(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, str]:
    run = run_manager.cancel(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    return {"run_id": run.id, "status": run.status.value}
