from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.seo_agent.runtime import publishing_service

router = APIRouter(prefix="/seo-agent/publishing", tags=["SEO Agent Publishing"])


class PublishRequest(BaseModel):
    task_id: str
    delivery_id: str
    slug: str = Field(min_length=1)
    html_content: str = Field(min_length=1)
    submitted_by_actor_id: str
    idempotency_key: str
    target_environment: str = "production"
    approval_action_id: str | None = None


class ConfirmPublishRequest(BaseModel):
    confirmed_by_actor_id: str
    published_url: str


@router.post("", status_code=201)
async def publish(body: PublishRequest) -> dict:
    try:
        return await publishing_service.publish_delivery(**body.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{publish_id}")
async def get_publish(publish_id: str) -> dict:
    try:
        return await publishing_service.get_publish(publish_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{publish_id}/confirm")
async def confirm_publish(publish_id: str, body: ConfirmPublishRequest) -> dict:
    try:
        return await publishing_service.confirm_publish(publish_id, **body.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
