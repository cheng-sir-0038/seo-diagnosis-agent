from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.agent.harness.persistence import StoreRunPersistence
from src.agent.models import AgentEvent, AgentRun, HumanInputAnswer, RunStatus
from src.agent.observability import EXECUTOR_WORKFLOW, AgentRunObserver
from src.agent.providers.base import ModelProvider
from src.agent.providers.deepseek import DeepSeekProvider
from src.agent.run_manager import run_manager
from src.agent.runtime.executor import AgentExecutor
from src.agent.tools.registry import ToolRegistry
from src.agent.workspace.routing import ROUTING_PROMPT, Specialist
from src.agent.workspace.supervisor import WorkspaceSupervisor, conversation_context
from src.core.cache import cache_manager
from src.core.config import settings
from src.facade.data_facade import facade
from src.healthcare_agent import build_healthcare_registry
from src.healthcare_agent.skills import available_healthcare_skills
from src.observability.runtime import observability_client, observability_database
from src.seo_agent.chat.runtime import build_seo_system_prompt
from src.seo_agent.chat.tools import build_seo_agent_registry
from src.seo_agent.geo_a2a import configured_citation_client
from src.seo_agent.runtime import (
    seo_agent_system_service,
    seo_chat_harness_store,
    seo_chat_recovery_service,
    structured_model_automation,
)
from src.seo_agent.workflows.cloudflare_images import cloudflare_share_images

site_chat_router = APIRouter(prefix="/seo-agent/chat", tags=["SEO Agent Chat"])
router = APIRouter(prefix="/chat", tags=["Conversations"])


class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class CreateConversationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str | None = Field(default=None, min_length=1, max_length=256)
    messages: list[ConversationMessage] = Field(min_length=1)

    @model_validator(mode="after")
    def last_message_is_user(self) -> CreateConversationRunRequest:
        if self.messages[-1].role != "user" or not self.messages[-1].content.strip():
            raise ValueError("请以用户的业务问题开始本轮对话")
        return self


class CreateSiteRunRequest(BaseModel):
    site_id: str = Field(min_length=1)
    conversation_id: str | None = None
    messages: list[ConversationMessage] = Field(min_length=1)


class ConversationRunResponse(BaseModel):
    run_id: str
    task_id: str | None = None
    thread_id: str | None = None
    status: str
    events_url: str
    cancel_url: str


class DirectoryTool(BaseModel):
    name: str
    description: str


class DirectorySkill(BaseModel):
    name: str
    label: str
    tools: list[str]


class SpecialistTools(BaseModel):
    agent: Specialist
    tools: list[DirectoryTool]
    skills: list[DirectorySkill] = Field(default_factory=list)
    note: str | None = None


class ToolDirectory(BaseModel):
    specialists: list[SpecialistTools]


def build_conversation_model() -> ModelProvider:
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
    registry: ToolRegistry | None,
    conversation_id: str | None,
) -> None:
    run = run_manager.get(run_id)
    if run is None:
        return
    observer = (
        AgentRunObserver(
            observability_client,
            operation_name="seo_agent.chat" if registry is not None else "workspace.chat",
            session_id=conversation_id,
            agent_name="seo-specialist-agent" if registry is not None else "workspace-supervisor",
            workflow_name=EXECUTOR_WORKFLOW if registry is not None else "workspace-graph",
        )
        if observability_database.started
        else None
    )
    persistence = StoreRunPersistence(seo_chat_harness_store) if _durable_enabled() else None
    executor = AgentExecutor(
        provider,
        registry if registry is not None else ToolRegistry(),
        max_iterations=settings.agent_max_iterations,
        max_tool_calls=settings.agent_max_tool_calls,
        tool_timeout=settings.agent_tool_timeout,
        observer=observer,
        persistence=persistence,
        context_message_limit=settings.agent_context_message_limit,
        context_character_limit=settings.agent_context_character_limit,
    )
    if registry is not None:
        await executor.run(run)
    else:
        supervisor = WorkspaceSupervisor(
            executor, structured_model_automation.model_runner, _workspace_seo,
            lambda: build_healthcare_registry(database=seo_chat_harness_store.database),
            seo_chat_harness_store.completed_dialog,
        )
        await executor.run(run, workflow=supervisor.execute)


def _seo_registry(site: dict[str, Any], user_messages: list[str]) -> ToolRegistry:
    client = observability_client if observability_database.started else None
    return build_seo_agent_registry(
        facade, site,
        observability=client, snapshots=cache_manager,
        model_runner=structured_model_automation.model_runner,
        image_generator=cloudflare_share_images,
        geo_citation_client=configured_citation_client(client),
        user_messages=user_messages,
    )


async def _workspace_site(target: str | None) -> dict[str, Any]:
    site: dict[str, Any] = {"default_country": "US", "default_language": "en"}
    if target:
        host = urlsplit(target).hostname
        if not host:
            raise ValueError("请提供有效的网址")
        registered = (
            await seo_agent_system_service.find_site_by_host(host) if _durable_enabled() else None
        )
        if registered:
            if registered.get("status") != "ACTIVE" or registered.get("archived_at"):
                raise ValueError("站点当前不可用于 SEO 调查")
            site.update(registered)
        else:
            site.update(id=f"domain:{host.removeprefix('www.')}", name=host)
        site.update(domain=host, target_url=target)
    return site


async def _workspace_seo(
    target: str | None, user_messages: list[str],
) -> tuple[str, ToolRegistry]:
    site = await _workspace_site(target)
    registry = _seo_registry(site, user_messages)
    prompt = build_seo_system_prompt(site, registry.schemas())
    return prompt, registry


@router.get("/tools", response_model=ToolDirectory)
async def list_workspace_tools(
    conversation_id: str | None = Query(default=None, min_length=1, max_length=256),
) -> ToolDirectory:
    """按当前会话已确认的目标列出专员的工具，仅查看注册表。"""
    previous = {}
    if conversation_id and _durable_enabled():
        previous = await seo_chat_harness_store.latest_dispatch_state(
            conversation_id, include_dialog=False,
        )
    target = previous.get("target_url")
    note = None if target else "指定网页后会载入该页可用的检查工具。"
    try:
        site = await _workspace_site(target)
    except ValueError as exc:
        seo, note = ToolRegistry(), str(exc)
    else:
        seo = _seo_registry(site, [])
    healthcare = build_healthcare_registry(database=seo_chat_harness_store.database)
    return ToolDirectory(specialists=[
        SpecialistTools(
            agent=agent, note=description,
            skills=[
                DirectorySkill(name=skill.name, label=skill.description, tools=skill.tools)
                for skill in available_healthcare_skills(registry)
            ] if agent == Specialist.HEALTHCARE else [],
            tools=[
                DirectoryTool(name=tool.name, description=tool.description)
                for tool in registry.values()
            ],
        )
        for agent, registry, description in (
            (Specialist.HEALTHCARE, healthcare, None), (Specialist.SEO, seo, note),
        )
    ])


async def _schedule_restored_run(run: AgentRun) -> None:
    if run.site_id is None:
        provider = build_conversation_model()
        run.task = asyncio.create_task(
            _execute(run.id, provider, None, run.conversation_id),
            name=f"workspace-recover:{run.id}",
        )
        return
    site = await seo_agent_system_service.get_site(run.site_id)
    registry = _seo_registry(site, [str(message.get("content", "")) for message in run.messages
                                    if message.get("role") == "user"])
    if not registry.schemas():
        raise RuntimeError("恢复运行没有真实 SEO 数据工具可用")
    provider = build_conversation_model()
    run.task = asyncio.create_task(
        _execute(run.id, provider, registry, run.conversation_id),
        name=f"seo-agent-recover:{run.id}",
    )


async def recover_active_conversations() -> dict[str, int]:
    if not settings.agent_chat_recovery_enabled or not _durable_enabled():
        return {"recovered": 0, "blocked": 0}
    return await seo_chat_recovery_service.recover_active(_schedule_restored_run)


@site_chat_router.post(
    "/runs", response_model=ConversationRunResponse, status_code=status.HTTP_201_CREATED,
)
async def create_site_run(body: CreateSiteRunRequest) -> ConversationRunResponse:
    try:
        site = await seo_agent_system_service.get_site(body.site_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if str(site.get("status") or "ACTIVE") != "ACTIVE":
        raise HTTPException(status_code=409, detail="站点当前不可用于 SEO Agent 调查")
    registry = _seo_registry(site, [m.content for m in body.messages if m.role == "user"])
    if not registry.schemas():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "NO_LIVE_SEO_TOOLS",
                "message": "当前没有真实 SEO 数据工具可用，请检查 DataForSEO 凭证并重启服务。",
            },
        )
    return await _start_run(
        body, site_id=body.site_id, registry=registry,
        system_prompt=build_seo_system_prompt(site, registry.schemas()),
    )


@router.post("/runs", response_model=ConversationRunResponse, status_code=201)
async def create_run(body: CreateConversationRunRequest) -> ConversationRunResponse:
    previous = {}
    if _durable_enabled() and body.conversation_id:
        previous = await seo_chat_harness_store.latest_dispatch_state(
            body.conversation_id, include_dialog=False,
        )
    return await _start_run(
        body, site_id=None, registry=None, system_prompt=ROUTING_PROMPT,
        dispatch_state=conversation_context(previous),
    )


async def _start_run(
    body: CreateConversationRunRequest | CreateSiteRunRequest, *, site_id: str | None,
    registry: ToolRegistry | None, system_prompt: str, dispatch_state: dict | None = None,
) -> ConversationRunResponse:
    try:
        provider = build_conversation_model()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    user_turn = body.messages[-1:] if site_id is None else body.messages
    messages = [
        {"role": "system", "content": system_prompt},
        *[message.model_dump(mode="json") for message in user_turn],
    ]
    created = None
    if _durable_enabled():
        intent = next(
            (m.content for m in reversed(body.messages) if m.role == "user"),
            body.messages[-1].content,
        )
        created = await seo_chat_harness_store.create_task_run(
            site_id=site_id, conversation_id=body.conversation_id, title=intent[:120],
            user_intent=intent, messages=messages, model_provider=provider.provider_name,
            model_name=provider.model_name, dispatch_state=dispatch_state,
            prompt_version="seo-chat-v1" if site_id else "workspace-v1",
            prompt_hash=hashlib.sha256(system_prompt.encode()).hexdigest(),
            available_tools=registry.names() if registry is not None else [],
        )
    run = run_manager.create_run(
        messages, run_id=created.run_id if created else None,
        task_id=created.task_id if created else None,
        thread_id=created.thread_id if created else None, site_id=site_id,
        conversation_id=body.conversation_id,
    )
    run.dispatch_state = dispatch_state or {}
    run.task = asyncio.create_task(
        _execute(run.id, provider, registry, body.conversation_id), name=f"chat:{run.id}",
    )
    return ConversationRunResponse(
        run_id=run.id, task_id=run.task_id, thread_id=run.thread_id, status=run.status.value,
        **_run_urls(run),
    )


def _run_urls(run: AgentRun) -> dict[str, str]:
    prefix = "/api/v1/seo-agent/chat" if run.site_id else "/api/v1/chat"
    return {
        "events_url": f"{prefix}/runs/{run.id}/events",
        "cancel_url": f"{prefix}/runs/{run.id}/cancel",
    }


@site_chat_router.get("/images/{image_id}", response_class=FileResponse)
def get_share_image(image_id: UUID) -> FileResponse:
    stored = cloudflare_share_images.get_file(image_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="分享图不存在")
    path, media_type = stored
    return FileResponse(
        path, media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"},
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
    live_start_sequence = run.sequence

    async for event in run.follow_events():
        if event is None:
            yield "event: heartbeat\ndata: {}\n\n"
        elif event.sequence > cursor and not (
            cursor > 0 and event.type == "message.delta" and event.sequence <= live_start_sequence
        ):
            yield _encode(event)


@site_chat_router.get("/tasks")
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


@site_chat_router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    if not _durable_enabled():
        raise HTTPException(status_code=503, detail="Agent 运行 Harness 未启动")
    try:
        return await seo_chat_harness_store.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs/{run_id}")
@site_chat_router.get("/runs/{run_id}")
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
@site_chat_router.get("/runs/{run_id}/events")
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
@site_chat_router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str) -> dict[str, str]:
    if not _durable_enabled():
        raise HTTPException(status_code=503, detail="Agent 运行 Harness 未启动")
    existing = run_manager.get(run_id)
    if existing is not None and existing.task is not None and not existing.task.done():
        raise HTTPException(status_code=409, detail="run 正在执行")
    try:
        run = await seo_chat_harness_store.claim_retry(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    run_manager.register(run)
    try:
        await _schedule_restored_run(run)
    except Exception as exc:
        await seo_chat_harness_store.mark_recovery_blocked(run_id, str(exc))
        raise HTTPException(status_code=503, detail="run 恢复条件不足") from exc
    return {"run_id": run.id, "status": run.status.value}


@router.post("/runs/{run_id}/input")
@site_chat_router.post("/runs/{run_id}/input")
async def submit_run_input(run_id: str, body: HumanInputAnswer) -> dict[str, Any]:
    if not _durable_enabled():
        raise HTTPException(status_code=503, detail="Agent 运行 Harness 未启动")
    existing = run_manager.get(run_id)
    if (
        existing is not None and existing.task is not None and not existing.task.done()
        and existing.status == RunStatus.WAITING_FOR_INPUT
    ):
        try:
            await asyncio.wait_for(asyncio.shield(existing.task), timeout=5)
        except TimeoutError as exc:
            raise HTTPException(status_code=409, detail="正在保存进度，请稍后再提交") from exc
    try:
        run, replayed, sequence = await seo_chat_harness_store.submit_input(run_id, body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not replayed:
        run_manager.register(run)
        try:
            await _schedule_restored_run(run)
        except Exception as exc:
            await seo_chat_harness_store.mark_recovery_blocked(run_id, str(exc))
            raise HTTPException(status_code=503, detail="补充已保存，运行暂时无法恢复") from exc
    return {
        "run_id": run.id, "task_id": run.task_id, "thread_id": run.thread_id,
        "status": run.status.value, "after_sequence": sequence, "replayed": replayed,
        **_run_urls(run),
    }


@router.post("/runs/{run_id}/cancel")
@site_chat_router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, str]:
    await _load_runtime_run(run_id)
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
