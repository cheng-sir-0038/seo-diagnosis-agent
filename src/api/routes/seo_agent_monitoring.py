from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.seo_agent.monitoring.execution import (
    IndexingObservation,
    RankingObservation,
)
from src.seo_agent.runtime import monitoring_execution_service, monitoring_service

router = APIRouter(prefix="/seo-agent/monitoring", tags=["SEO Agent Monitoring"])


class GoalRequest(BaseModel):
    goal_type: str
    target_keyword: str | None = None
    target_value: float | None = None
    comparison_operator: str | None = None
    minimum_improvement: float | None = None
    baseline_value: float | None = None


class IndexingResultRequest(BaseModel):
    indexed: bool
    evidence_snapshot_id: str | None = None
    observed_at: str | None = None


class RankingCheckRequest(BaseModel):
    sequence_no: int
    scheduled_for: str | None = None


class RankingObservationRequest(BaseModel):
    keyword: str
    position: float
    evidence_snapshot_id: str | None = None
    observed_at: str | None = None


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: str) -> dict:
    try:
        return await monitoring_service.get_plan(plan_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/plans/{plan_id}/goals", status_code=201)
async def add_goal(plan_id: str, body: GoalRequest) -> dict:
    return await monitoring_service.add_goal(plan_id, **body.model_dump())


@router.post("/checks/{check_id}/indexing-result")
async def record_indexing(check_id: str, body: IndexingResultRequest) -> dict:
    try:
        return await monitoring_execution_service.ingest_indexing_result(
            check_id=check_id,
            observation=IndexingObservation(**body.model_dump()),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/plans/{plan_id}/ranking-checks", status_code=201)
async def create_ranking_check(plan_id: str, body: RankingCheckRequest) -> dict:
    return await monitoring_service.create_ranking_check(plan_id, **body.model_dump())


@router.post("/checks/{check_id}/ranking-observations", status_code=201)
async def record_ranking(check_id: str, body: RankingObservationRequest) -> dict:
    try:
        values = body.model_dump()
        keyword = values.pop("keyword")
        return await monitoring_execution_service.ingest_ranking_result(
            check_id=check_id,
            keyword=keyword,
            observation=RankingObservation(**values),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/plans/{plan_id}/evaluate")
async def evaluate_plan(plan_id: str) -> dict:
    return await monitoring_service.evaluate_plan(plan_id)
