from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.seo_agent.runtime import work_schedule_service

router = APIRouter(prefix="/seo-agent/schedules", tags=["SEO Agent Schedules"])


class CreateScheduleRequest(BaseModel):
    site_id: str
    schedule_type: str = "OPPORTUNITY_SCAN"
    name: str = Field(min_length=1)
    frequency: str
    timezone: str
    execution_time: str | None = None
    weekday: int | None = None
    config: dict = Field(default_factory=dict)


@router.post("", status_code=201)
async def create_schedule(body: CreateScheduleRequest) -> dict:
    try:
        return await work_schedule_service.create_schedule(**body.model_dump())
    except Exception as exc:
        if "ux_active_opportunity_schedule" in str(exc) or "UNIQUE constraint failed" in str(exc):
            raise HTTPException(status_code=409, detail="站点已有启用中的主机会扫描计划") from exc
        raise


@router.get("")
async def list_schedules(site_id: str) -> list[dict]:
    return await work_schedule_service.list_schedules(site_id)


@router.get("/{schedule_id}/runs")
async def list_schedule_runs(schedule_id: str, limit: int = 50) -> list[dict]:
    return await work_schedule_service.list_runs(schedule_id, limit)


@router.post("/{schedule_id}/enable")
async def enable_schedule(schedule_id: str) -> dict:
    return await work_schedule_service.set_enabled(schedule_id, True)


@router.post("/{schedule_id}/disable")
async def disable_schedule(schedule_id: str) -> dict:
    return await work_schedule_service.set_enabled(schedule_id, False)
