from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.observability.exporters import OpenTelemetryExporter
from src.observability.models import IssueStatus
from src.observability.runtime import observability_client, observability_database

router = APIRouter(prefix="/observability", tags=["Agent Observability"])


class IssueStatusRequest(BaseModel):
    status: IssueStatus


@router.get("/status")
async def status() -> dict[str, Any]:
    return {
        "database_started": observability_database.started,
        "project_key": observability_client.project_key,
        "exporters": [exporter.__class__.__name__ for exporter in observability_client.exporters],
        "otlp_enabled": any(
            isinstance(exporter, OpenTelemetryExporter) and exporter.enabled
            for exporter in observability_client.exporters
        ),
    }


@router.get("/traces")
async def list_traces(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return await observability_client.list_traces(status=status_filter, limit=limit)


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str) -> dict[str, Any]:
    try:
        return await observability_client.get_trace(trace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/issues")
async def list_issues(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return await observability_client.list_issues(status=status_filter, limit=limit)


@router.patch("/issues/{issue_id}/status")
async def update_issue_status(issue_id: str, body: IssueStatusRequest) -> dict[str, Any]:
    try:
        return await observability_client.set_issue_status(issue_id, body.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/metrics")
async def metric_summary(
    metric_name: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    return await observability_client.metric_summary(metric_name=metric_name, limit=limit)
