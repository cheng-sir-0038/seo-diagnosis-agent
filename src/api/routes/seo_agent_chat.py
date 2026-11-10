from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.agent.harness.persistence import StoreRunPersistence
from src.agent.loop import AgentLoop
from src.agent.models import AgentEvent, AgentRun
from src.agent.observability import AgentRunObserver
from src.agent.providers.base import ModelProvider
from src.agent.providers.deepseek import DeepSeekProvider
from src.agent.run_manager import run_manager
from src.core.config import settings
from src.facade.data_facade import facade
from src.observability.runtime import observability_client, observability_database
from src.seo_agent.chat.runtime import build_seo_system_prompt
from src.seo_agent.chat.tools import build_seo_agent_registry
from src.seo_agent.runtime import (
    seo_agent_system_service,
    seo_chat_harness_store,
    seo_chat_recovery_service,
)

router = APIRouter(prefix="/seo-agent/chat", tags=["SEO Agent Chat"])


class SeoChatMessageRequest(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class CreateSeoAgentRunRequest(BaseModel):
    site_id: str = Field(min_length=1)
    conversation_id: str | None = None
    messages: list[SeoChatMessageRequest] = Field(min_length=1)


class CreateSeoAgentRunResponse(BaseModel):
    run_id: str
    task_id: str | None = None
    thread_id: str | None = None
    status: str
    events_url: str
    cancel_url: str


def build_seo_model_provider() -> ModelProvider:
    return DeepSeekProvider(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        timeout=settings.deepseek_request_timeout,
    )


def _durable_enabled() -> bool:
    return seo_chat_harness_store.database.started


async def _execute(
    run_id: str,
    provider: ModelProvider,
    registry: Any,
    conversation_id: str | None,
) -> None:
    run = run_manager.get(run_id)
    if run is None:
        return
    observer = (
        AgentRunObserver(
            observability_client,
            operation_name="seo_agent.chat",
            session_id=conversation_id,
            agent_name="seo-specialist-agent",
        )
        if observability_database.started
        else None
    )
    persistence = StoreRunPersistence(seo_chat_harness_store) if _durable_enabled() else None
    await AgentLoop(
        provider,
        registry,
        max_iterations=settings.agent_max_iterations,
        max_tool_calls=settings.agent_max_tool_calls,
        tool_timeout=settings.agent_tool_timeout,
        observer=observer,
        persistence=persistence,
        context_message_limit=settings.agent_context_message_limit,
        context_character_limit=settings.agent_context_character_limit,
    ).run(run)


async def _schedule_restored_run(run: AgentRun) -> None:
    if not run.site_id:
        raise RuntimeError("恢复运行缺少 site_id")
    site = await seo_agent_system_service.get_site(run.site_id)
    registry = build_seo_agent_registry(facade, site)
    if not registry.schemas():
        raise RuntimeError("恢复运行没有真实 SEO 数据工具可用")
    provider = build_seo_model_provider()
    run.task = asyncio.create_task(
        _execute(run.id, provider, registry, run.conversation_id),
        name=f"seo-agent-recover:{run.id}",
    )


async def recover_active_seo_chat_runs() -> dict[str, int]:
    if not settings.agent_chat_recovery_enabled or not _durable_enabled():
        return {"recovered": 0, "blocked": 0}
    return await seo_chat_recovery_service.recover_active(_schedule_restored_run)


@router.post("/runs", response_model=CreateSeoAgentRunResponse, status_code=status.HTTP_201_CREATED)
async def create_run(body: CreateSeoAgentRunRequest) -> CreateSeoAgentRunResponse:
    try:
        site = await seo_agent_system_service.get_site(body.site_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if str(site.get("status") or "ACTIVE") != "ACTIVE":
        raise HTTPException(status_code=409, detail="站点当前不可用于 SEO Agent 调查")
    registry = build_seo_agent_registry(facade, site)
    if not registry.schemas():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "NO_LIVE_SEO_TOOLS",
                "message": "当前没有真实 SEO 数据工具可用，请检查 DataForSEO 凭证并重启服务。",
            },
        )
    try:
        provider = build_seo_model_provider()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    system_message = {
        "role": "system",
        "content": build_seo_system_prompt(site, registry.schemas()),
    }
    messages = [
        system_message,
        *[message.model_dump(mode="json") for message in body.messages],
    ]
    task_id: str | None = None
    thread_id: str | None = None
    if _durable_enabled():
        intent = next(
            (message.content for message in reversed(body.messages) if message.role == "user"),
            body.messages[-1].content,
        )
        created = await seo_chat_harness_store.create_task_run(
            site_id=body.site_id,
            conversation_id=body.conversation_id,
            title=intent[:120],
            user_intent=intent,
            messages=messages,
            model_provider=provider.provider_name,
            model_name=provider.model_name,
            prompt_version="seo-chat-v1",
            prompt_hash=hashlib.sha256(system_message["content"].encode("utf-8")).hexdigest(),
            available_tools=[
                str(item.get("function", {}).get("name"))
                for item in registry.schemas()
                if item.get("function", {}).get("name")
            ],
        )
        task_id = created.task_id
        thread_id = created.thread_id
        run = run_manager.create_run(
            messages,
            run_id=created.run_id,
            task_id=task_id,
            thread_id=thread_id,
            site_id=body.site_id,
            conversation_id=body.conversation_id,
        )
    else:
        run = run_manager.create_run(
            messages,
            site_id=body.site_id,
            conversation_id=body.conversation_id,
        )
    run.task = asyncio.create_task(
        _execute(run.id, provider, registry, body.conversation_id),
        name=f"seo-agent:{run.id}",
    )
    return CreateSeoAgentRunResponse(
        run_id=run.id,
        task_id=task_id,
        thread_id=thread_id,
        status=run.status.value,
        events_url=f"/api/v1/seo-agent/chat/runs/{run.id}/events",
        cancel_url=f"/api/v1/seo-agent/chat/runs/{run.id}/cancel",
    )


def _encode(event: AgentEvent) -> str:
    payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    event_id = "" if event.type == "message.delta" else f"id: {event.sequence}\n"
    return f"{event_id}event: {event.type}\ndata: {payload}\n\n"


async def _load_runtime_run(run_id: str) -> AgentRun | None:
    run = run_manager.get(run_id)
    if run is not None:
        return run
    if not _durable_enabled():
        return None
    try:
        run = await seo_chat_harness_store.restore_run(run_id, mark_recovering=False)
    except KeyError:
        return None
    return run_manager.register(run)


async def _event_stream(
    run_id: str,
    *,
    after_sequence: int = 0,
) -> AsyncIterator[str]:
    run = await _load_runtime_run(run_id)
    if run is None:
        return
    cursor = max(0, after_sequence)
    reconnecting = cursor > 0
    live_start_sequence = run.sequence

    for event in list(run.events):
        if event.type == "message.delta":
            if reconnecting:
                continue
        elif event.sequence <= cursor:
            continue
        yield _encode(event)
        if event.type != "message.delta":
            cursor = max(cursor, event.sequence)

    if run.terminal:
        return

    while True:
        try:
            event = await asyncio.wait_for(run.queue.get(), timeout=15)
            if event.type == "message.delta":
                if reconnecting and event.sequence <= live_start_sequence:
                    continue
            elif event.sequence <= cursor:
                continue
            yield _encode(event)
            if event.type != "message.delta":
                cursor = max(cursor, event.sequence)
            if event.type in {"run.completed", "run.failed", "run.cancelled"}:
                return
        except TimeoutError:
            yield "event: heartbeat\ndata: {}\n\n"


@router.get("/tasks")
async def list_tasks(
    site_id: str | None = None,
    status_group: Literal["pending", "running", "ended"] | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    if not _durable_enabled():
        return []
    return await seo_chat_harness_store.list_tasks(
        site_id=site_id,
        status_group=status_group,
        limit=limit,
    )


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    if not _durable_enabled():
        raise HTTPException(status_code=503, detail="Agent 运行 Harness 未启动")
    try:
        return await seo_chat_harness_store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    if _durable_enabled():
        try:
            return await seo_chat_harness_store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    run = run_manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    return {
        "id": run.id,
        "status": run.status.value,
        "current_stage": run.events[-1].type if run.events else "created",
        "events": [event.model_dump(mode="json") for event in run.events],
    }


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    after_sequence: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    if await _load_runtime_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    header_cursor = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
    cursor = max(after_sequence, header_cursor)
    return StreamingResponse(
        _event_stream(run_id, after_sequence=cursor),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str) -> dict[str, str]:
    if not _durable_enabled():
        raise HTTPException(status_code=503, detail="Agent 运行 Harness 未启动")
    existing = run_manager.get(run_id)
    if existing is not None and existing.task is not None and not existing.task.done():
        raise HTTPException(status_code=409, detail="run 正在执行")
    try:
        run = await seo_chat_harness_store.restore_run(run_id)
        run_manager.register(run)
        await _schedule_restored_run(run)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        await seo_chat_harness_store.mark_recovery_blocked(run_id, str(exc))
        raise HTTPException(status_code=503, detail="run 恢复条件不足") from exc
    return {"run_id": run.id, "status": run.status.value}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, str]:
    run = run_manager.cancel(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run 不存在")
    if _durable_enabled() and (run.task is None or run.task.done()):
        if run.events:
            await seo_chat_harness_store.persist_event(run, run.events[-1])
        await seo_chat_harness_store.save_checkpoint(
            run,
            messages=run.messages,
            iteration=run.iteration,
            tool_count=run.tool_count,
            pending_tool_calls=run.pending_tool_calls,
            stage="cancelled",
            token_usage=run.token_usage,
        )
        await seo_chat_harness_store.finalize_run(run)
    return {"run_id": run.id, "status": run.status.value}
