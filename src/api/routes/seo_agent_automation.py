from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.seo_agent.runtime import (
    automation_lifecycle,
    digest_service,
    local_schedule_provider,
    notification_outbox_worker,
    prompt_evaluation_executor,
    prompt_optimization_loop,
    smtp_email_provider,
    structured_model_automation,
)

router = APIRouter(prefix="/seo-agent/automation", tags=["SEO Agent Automation"])


class ActorRequest(BaseModel):
    actor_id: str


class OptimizePromptRequest(BaseModel):
    dataset_id: str
    failures: list[dict[str, Any]] = Field(default_factory=list)
    success_criteria: dict[str, Any] = Field(default_factory=lambda: {"minimum_pass_rate": 0.8})
    environment: str = "production"
    canary_percentage: int = Field(default=10, ge=1, le=100)


@router.post("/tasks/{task_id}/generate-prd", status_code=201)
async def generate_prd(task_id: str, body: ActorRequest) -> dict:
    try:
        return await structured_model_automation.generate_prd(
            task_id=task_id, created_by_actor_id=body.actor_id
        )
    except ValueError as exc:
        status = 503 if "DEEPSEEK_API_KEY" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/deliveries/{delivery_id}/review", status_code=201)
async def review_delivery(delivery_id: str, body: ActorRequest) -> dict:
    try:
        return await structured_model_automation.review_delivery(
            delivery_id=delivery_id, reviewer_actor_id=body.actor_id
        )
    except ValueError as exc:
        status = 503 if "DEEPSEEK_API_KEY" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/outbox/run")
async def run_outbox(limit: int = Query(default=100, ge=1, le=500)) -> dict:
    result = await notification_outbox_worker.run_once(limit)
    return {
        "sent": result.sent,
        "failed": result.failed,
        "exhausted": result.exhausted,
        "skipped_not_configured": result.skipped_not_configured,
    }


@router.post("/digests/{period}/run", status_code=201)
async def run_digest(period: str, site_id: str, period_key: str) -> dict:
    try:
        return await digest_service.create_digest(
            site_id=site_id, period=period, period_key=period_key
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/schedules/sync")
async def sync_schedules() -> dict[str, int]:
    return await automation_lifecycle.sync()


@router.get("/status")
async def automation_status() -> dict:
    health = await smtp_email_provider.health_check()
    return {
        "smtp": {"status": health.status.value, "message": health.message},
        "scheduler_running": local_schedule_provider.running,
        "jobs": [job.id for job in local_schedule_provider.scheduler.get_jobs()],
    }


@router.post("/prompts/{prompt_id}/optimize", status_code=201)
async def optimize_prompt(prompt_id: str, body: OptimizePromptRequest) -> dict:
    try:
        return await prompt_optimization_loop.run(
            prompt_id=prompt_id,
            dataset_id=body.dataset_id,
            environment=body.environment,
            failures=body.failures,
            executor=prompt_evaluation_executor,
            success_criteria=body.success_criteria,
            canary_percentage=body.canary_percentage,
        )
    except ValueError as exc:
        status = 503 if "DEEPSEEK_API_KEY" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
