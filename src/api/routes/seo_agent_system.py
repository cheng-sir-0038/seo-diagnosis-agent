from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.seo_agent.runtime import (
    adapter_registry,
    checkpoint_manager,
    seo_agent_database,
    seo_agent_system_service,
)

router = APIRouter(prefix="/seo-agent/system", tags=["SEO Agent System"])


class CreateSiteRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    domain: str = Field(min_length=3, max_length=255)
    default_country: str = Field(default="US", min_length=2, max_length=8)
    default_language: str = Field(default="en", min_length=2, max_length=16)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)


class CreateActorRequest(BaseModel):
    actor_type: str
    name: str = Field(min_length=1, max_length=120)
    email: str | None = None


class CreateThreadRequest(BaseModel):
    site_id: str
    workflow_type: str
    business_object_type: str
    business_object_id: str
    thread_id: str | None = None


class StartRunRequest(BaseModel):
    trigger_type: str
    model_provider: str | None = None
    model_name: str | None = None


@router.get("/status")
async def system_status() -> dict:
    return {
        "database_started": seo_agent_database.started,
        "checkpoint_started": checkpoint_manager.saver is not None,
        "adapters": [item.model_dump(mode="json") for item in adapter_registry.list_definitions()],
    }


@router.post("/sites", status_code=201)
async def create_site(body: CreateSiteRequest) -> dict:
    try:
        return await seo_agent_system_service.create_site(**body.model_dump())
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise HTTPException(status_code=409, detail="站点域名已存在") from exc
        raise


@router.get("/sites")
async def list_sites() -> list[dict]:
    return await seo_agent_system_service.list_sites()


@router.get("/sites/{site_id}")
async def get_site(site_id: str) -> dict:
    try:
        return await seo_agent_system_service.get_site(site_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/actors", status_code=201)
async def create_actor(body: CreateActorRequest) -> dict:
    return await seo_agent_system_service.create_actor(**body.model_dump())


@router.get("/actors")
async def list_actors(actor_type: str | None = None) -> list[dict]:
    return await seo_agent_system_service.list_actors(actor_type)


@router.post("/threads", status_code=201)
async def create_thread(body: CreateThreadRequest) -> dict:
    try:
        return await seo_agent_system_service.create_thread(**body.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str) -> dict:
    try:
        return await seo_agent_system_service.get_thread(thread_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/threads/{thread_id}/runs", status_code=201)
async def start_run(thread_id: str, body: StartRunRequest) -> dict:
    try:
        return await seo_agent_system_service.start_run(thread_id=thread_id, **body.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/sites/{site_id}/work-records")
async def list_work_records(site_id: str, limit: int = 100) -> list[dict]:
    return await seo_agent_system_service.list_work_records(site_id, max(1, min(limit, 500)))
