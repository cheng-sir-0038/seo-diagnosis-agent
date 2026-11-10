from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.seo_agent.opportunities.models import OpportunityCandidate
from src.seo_agent.runtime import opportunity_service, scan_run_service

router = APIRouter(prefix="/seo-agent", tags=["SEO Agent Opportunities"])


class StartScanRequest(BaseModel):
    schedule_id: str
    trigger_type: str
    idempotency_key: str
    scheduled_at: str | None = None


class CandidateRequest(BaseModel):
    site_id: str
    scan_run_id: str
    candidate: OpportunityCandidate
    changed_by_actor_id: str
    idempotency_key: str
    default_approver_actor_id: str | None = None


class RejectRequest(BaseModel):
    actor_id: str
    reason: str


@router.post("/scan-runs", status_code=201)
async def start_scan(body: StartScanRequest) -> dict:
    return await scan_run_service.start(**body.model_dump())


@router.post("/scan-runs/{run_id}/complete")
async def complete_scan(run_id: str) -> dict:
    try:
        return await scan_run_service.complete(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/opportunities/ingest", status_code=201)
async def ingest_candidate(body: CandidateRequest) -> dict:
    return await opportunity_service.upsert_candidate(**body.model_dump())


@router.get("/opportunities")
async def list_opportunities(
    site_id: str,
    status: str | None = None,
    priority: str | None = None,
    limit: int = 100,
) -> list[dict]:
    return await opportunity_service.list(
        site_id,
        status=status,
        priority=priority,
        limit=limit,
    )


@router.get("/opportunities/{opportunity_id}")
async def get_opportunity(opportunity_id: str) -> dict:
    try:
        return await opportunity_service.get(opportunity_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/opportunities/{opportunity_id}/detail")
async def get_opportunity_detail(opportunity_id: str) -> dict:
    try:
        return await opportunity_service.get_detail(opportunity_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/opportunities/{opportunity_id}/reject")
async def reject_opportunity(opportunity_id: str, body: RejectRequest) -> dict:
    try:
        return await opportunity_service.reject_opportunity(
            opportunity_id, reason=body.reason, actor_id=body.actor_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
