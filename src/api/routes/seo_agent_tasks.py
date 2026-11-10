from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.seo_agent.runtime import task_delivery_service
from src.seo_agent.tasks.models import TaskPrdContent

router = APIRouter(prefix="/seo-agent/tasks", tags=["SEO Agent Tasks"])


class CreateTaskRequest(BaseModel):
    site_id: str
    title: str
    task_type: str
    priority: str
    opportunity_ids: list[str] = Field(min_length=1)
    owner_actor_id: str
    deadline_at: str | None = None


class CreatePrdRequest(BaseModel):
    title: str
    content: TaskPrdContent
    created_by_actor_id: str
    source_version_id: str | None = None
    change_summary: str | None = None


class DispatchRequest(BaseModel):
    prd_version_id: str
    recipient_actor_id: str
    idempotency_key: str


class DeliveryRequest(BaseModel):
    prd_version_id: str
    title: str
    summary: str
    structured_content: dict
    submitted_by_actor_id: str
    parent_delivery_id: str | None = None


class ReviewRequest(BaseModel):
    prd_version_id: str
    reviewer_actor_id: str
    status: str
    score: int = Field(ge=0, le=100)
    passed_items: list[dict] = Field(default_factory=list)
    failed_items: list[dict] = Field(default_factory=list)
    missing_items: list[dict] = Field(default_factory=list)
    revision_instructions: list[str] = Field(default_factory=list)


@router.get("")
async def list_tasks(site_id: str, status: str | None = None, limit: int = 100) -> list[dict]:
    return await task_delivery_service.list_tasks(site_id, status=status, limit=limit)


@router.post("", status_code=201)
async def create_task(body: CreateTaskRequest) -> dict:
    return await task_delivery_service.create_task(**body.model_dump())


@router.get("/{task_id}")
async def get_task(task_id: str) -> dict:
    try:
        return await task_delivery_service.get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{task_id}/detail")
async def get_task_detail(task_id: str) -> dict:
    try:
        return await task_delivery_service.get_task_detail(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/prd-versions", status_code=201)
async def create_prd(task_id: str, body: CreatePrdRequest) -> dict:
    return await task_delivery_service.create_prd_version(task_id=task_id, **body.model_dump())


@router.post("/prd-versions/{prd_version_id}/artifacts", status_code=201)
async def render_prd(prd_version_id: str) -> list[dict]:
    return await task_delivery_service.render_prd_artifacts(prd_version_id)


@router.post("/{task_id}/dispatches", status_code=201)
async def dispatch_task(task_id: str, body: DispatchRequest) -> dict:
    return await task_delivery_service.dispatch_task(task_id=task_id, **body.model_dump())


@router.post("/{task_id}/deliveries", status_code=201)
async def submit_delivery(task_id: str, body: DeliveryRequest) -> dict:
    return await task_delivery_service.submit_delivery(task_id=task_id, **body.model_dump())


@router.post("/deliveries/{delivery_id}/reviews", status_code=201)
async def review_delivery(delivery_id: str, body: ReviewRequest) -> dict:
    return await task_delivery_service.review_delivery(delivery_id=delivery_id, **body.model_dump())
