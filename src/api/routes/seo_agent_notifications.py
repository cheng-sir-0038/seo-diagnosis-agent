from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.seo_agent.runtime import notification_service

router = APIRouter(prefix="/seo-agent/notifications", tags=["SEO Agent Notifications"])


class CreateSubscriptionRequest(BaseModel):
    site_id: str
    subscriber_actor_id: str | None = None
    recipient_email: str
    recipient_role: str
    is_default_approver: bool = False


class PreferenceRequest(BaseModel):
    enabled: bool
    delivery_mode: str | None = None
    schedule: dict | None = None
    threshold: dict | None = None


@router.post("/subscriptions", status_code=201)
async def create_subscription(body: CreateSubscriptionRequest) -> dict:
    return await notification_service.create_subscription(**body.model_dump())


@router.get("/subscriptions")
async def list_subscriptions(site_id: str) -> list[dict]:
    return await notification_service.list_subscriptions(site_id)


@router.get("/subscriptions/{subscription_id}/preferences")
async def list_preferences(subscription_id: str) -> list[dict]:
    return await notification_service.list_preferences(subscription_id)


@router.get("/subscriptions/{subscription_id}/preferences/{event_key}")
async def get_preference(subscription_id: str, event_key: str) -> dict:
    try:
        return await notification_service.get_preference(subscription_id, event_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/subscriptions/{subscription_id}/preferences/{event_key}")
async def set_preference(subscription_id: str, event_key: str, body: PreferenceRequest) -> dict:
    try:
        return await notification_service.set_preference(
            subscription_id, event_key, **body.model_dump()
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
