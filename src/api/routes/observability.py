from __future__ import annotations

from datetime import UTC
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel

from src.observability.client import ObservationWindow, TraceListScope
from src.observability.exporters import OpenTelemetryExporter
from src.observability.models import IssueStatus
from src.observability.runtime import observability_client, observability_database

router = APIRouter(prefix="/observability", tags=["Agent Observability"])


class IssueStatusRequest(BaseModel):
    status: IssueStatus


def time_window(start_at: AwareDatetime | None = None, end_at: AwareDatetime | None = None) -> ObservationWindow | None:
    if start_at is None and end_at is None:
        return None
    if start_at is None or end_at is None or start_at >= end_at:
        raise HTTPException(status_code=422, detail="Provide both start_at and end_at, with start_at before end_at.")
    try:
        return start_at.astimezone(UTC), end_at.astimezone(UTC)
    except OverflowError as exc:
        raise HTTPException(status_code=422, detail="The window is out of range in UTC.") from exc


TimeWindow = Annotated[ObservationWindow | None, Depends(time_window)]


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
    window: TimeWindow,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    identifier: str | None = Query(default=None, min_length=1, max_length=200),
    scope: TraceListScope = Query(default="all", description="Trace list scope. Product scope hides internal maintenance even for ID lookup."),
) -> list[dict[str, Any]]:
    return await observability_client.list_traces(
        status=status_filter, limit=limit, window=window, identifier=identifier, scope=scope,
    )


@router.get("/overview")
async def overview(window: TimeWindow, hide_maintenance: bool = False) -> dict[str, Any]:
    if window:
        start_at, end_at = window
        try:
            start_at - (end_at - start_at)
        except OverflowError as exc:
            raise HTTPException(status_code=422, detail="The preceding comparison window is out of range.") from exc
    return await observability_client.overview(window=window, hide_maintenance=hide_maintenance)


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str, hide_maintenance: bool = False) -> dict[str, Any]:
    try:
        return await observability_client.get_trace(trace_id, hide_maintenance=hide_maintenance)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/issues")
async def list_issues(
    window: TimeWindow,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    pending: bool = False,
    hide_maintenance: bool = False,
) -> list[dict[str, Any]]:
    return await observability_client.list_issues(
        status=status_filter, limit=limit, pending=pending, window=window, hide_maintenance=hide_maintenance,
    )


@router.patch("/issues/{issue_id}/status")
async def update_issue_status(issue_id: str, body: IssueStatusRequest) -> dict[str, Any]:
    try:
        return await observability_client.set_issue_status(issue_id, body.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/metrics")
async def metric_summary(
    window: TimeWindow,
    metric_name: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    hide_maintenance: bool = False,
) -> list[dict[str, Any]]:
    return await observability_client.metric_summary(
        metric_name=metric_name, limit=limit, window=window, hide_maintenance=hide_maintenance,
    )
