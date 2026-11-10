from __future__ import annotations

from pathlib import Path

from src.core.config import settings
from src.observability.client import ObservabilityClient
from src.observability.exporters import JsonlExporter, OpenTelemetryExporter
from src.observability.storage import ObservabilityDatabase


def _build_exporters():
    exporters = []
    if settings.observability_jsonl_enabled:
        exporters.append(JsonlExporter(Path(settings.observability_jsonl_path)))
    exporters.append(
        OpenTelemetryExporter(
            service_name=settings.observability_service_name,
            endpoint=settings.observability_otlp_endpoint or None,
        )
    )
    return exporters


observability_database = ObservabilityDatabase(settings.observability_sqlite_path)
observability_client = ObservabilityClient(
    observability_database,
    project_key=settings.observability_project_key,
    project_name=settings.observability_project_name,
    service_name=settings.observability_service_name,
    exporters=_build_exporters(),
)
