from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.seo_agent.runtime import approval_service

router = APIRouter(prefix="/seo-agent/approvals", tags=["SEO Agent Approvals"])


class CreateApprovalRequest(BaseModel):
    site_id: str
    action_type: str
    risk_level: str
    target_type: str
    target_id: str
    requested_payload: dict = Field(default_factory=dict)
    approver_actor_id: str
    idempotency_key: str


class DecideApprovalRequest(BaseModel):
    actor_id: str
    decision: str
    edited_payload: dict | None = None
    reason: str | None = None


@router.post("", status_code=201)
async def create_approval(body: CreateApprovalRequest) -> dict:
    return await approval_service.create_action(**body.model_dump())


@router.get("")
async def list_approvals(site_id: str | None = None) -> list[dict]:
    return await approval_service.list_pending(site_id)


@router.get("/{action_id}")
async def get_approval(action_id: str) -> dict:
    try:
        return await approval_service.get_action(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{action_id}/decisions", status_code=201)
async def decide_approval(action_id: str, body: DecideApprovalRequest) -> dict:
    try:
        return await approval_service.decide(action_id=action_id, **body.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
