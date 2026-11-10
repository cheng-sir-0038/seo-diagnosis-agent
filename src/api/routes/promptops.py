from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.promptops.models import VersionStage
from src.promptops.runtime import (
    prompt_evaluation_service,
    prompt_experiment_service,
    prompt_registry,
)

router = APIRouter(prefix="/promptops", tags=["PromptOps"])


class CreatePromptRequest(BaseModel):
    prompt_key: str
    name: str
    purpose: str


class CreateVersionRequest(BaseModel):
    system_template: str
    user_template: str
    output_schema: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    stage: VersionStage = VersionStage.DRAFT
    parent_version_id: str | None = None
    change_summary: str | None = None
    created_by: str | None = None


class CreateDatasetRequest(BaseModel):
    name: str
    prompt_id: str | None = None
    description: str | None = None


class CreateCaseRequest(BaseModel):
    name: str
    input_data: Any
    expected_output: Any = Field(default_factory=dict)
    grading_rules: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    source_trace_id: str | None = None
    source_issue_id: str | None = None


class CanaryRequest(BaseModel):
    version_id: str
    environment: str = "production"
    percentage: int = Field(default=10, ge=1, le=100)


class PromoteRequest(BaseModel):
    version_id: str
    environment: str = "production"
    reason: str | None = None


class RollbackRequest(BaseModel):
    version_id: str
    environment: str = "production"
    reason: str


@router.get("/prompts")
async def list_prompts() -> list[dict[str, Any]]:
    return await prompt_registry.list_prompts()


@router.post("/prompts", status_code=201)
async def create_prompt(body: CreatePromptRequest) -> dict[str, Any]:
    return await prompt_registry.create_prompt(**body.model_dump())


@router.get("/prompts/{prompt_id}/versions")
async def list_versions(prompt_id: str) -> list[dict[str, Any]]:
    return await prompt_registry.list_versions(prompt_id)


@router.post("/prompts/{prompt_id}/versions", status_code=201)
async def create_version(prompt_id: str, body: CreateVersionRequest) -> dict[str, Any]:
    return await prompt_registry.create_version(prompt_id=prompt_id, **body.model_dump())


@router.get("/prompts/{prompt_id}/deployments")
async def list_deployments(prompt_id: str) -> list[dict[str, Any]]:
    return await prompt_registry.list_deployments(prompt_id)


@router.post("/prompts/{prompt_id}/canary")
async def start_canary(prompt_id: str, body: CanaryRequest) -> dict[str, Any]:
    return await prompt_registry.start_canary(
        prompt_id,
        body.version_id,
        environment=body.environment,
        percentage=body.percentage,
    )


@router.post("/prompts/{prompt_id}/promote")
async def promote(prompt_id: str, body: PromoteRequest) -> dict[str, Any]:
    return await prompt_registry.promote(
        prompt_id,
        body.version_id,
        environment=body.environment,
        reason=body.reason,
    )


@router.post("/prompts/{prompt_id}/rollback")
async def rollback(prompt_id: str, body: RollbackRequest) -> dict[str, Any]:
    return await prompt_registry.rollback(
        prompt_id,
        body.version_id,
        environment=body.environment,
        reason=body.reason,
    )


@router.get("/datasets")
async def list_datasets(prompt_id: str | None = None) -> list[dict[str, Any]]:
    return await prompt_registry.list_datasets(prompt_id)


@router.post("/datasets", status_code=201)
async def create_dataset(body: CreateDatasetRequest) -> dict[str, Any]:
    return await prompt_registry.create_dataset(**body.model_dump())


@router.get("/datasets/{dataset_id}/cases")
async def list_cases(dataset_id: str) -> list[dict[str, Any]]:
    return await prompt_registry.list_cases(dataset_id)


@router.post("/datasets/{dataset_id}/cases", status_code=201)
async def create_case(dataset_id: str, body: CreateCaseRequest) -> dict[str, Any]:
    return await prompt_registry.add_case(dataset_id=dataset_id, **body.model_dump())


@router.get("/evaluations")
async def list_evaluations(
    prompt_version_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return await prompt_evaluation_service.list_runs(
        prompt_version_id=prompt_version_id,
        limit=limit,
    )


@router.get("/evaluations/{run_id}")
async def get_evaluation(run_id: str) -> dict[str, Any]:
    try:
        return await prompt_evaluation_service.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/experiments")
async def list_experiments(
    prompt_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return await prompt_experiment_service.list_experiments(
        prompt_id=prompt_id,
        limit=limit,
    )
