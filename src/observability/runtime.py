from __future__ import annotations

import base64
import logging
from pathlib import Path

from src.core.config import settings
from src.observability.client import ObservabilityClient
from src.observability.exporters import JsonlExporter, OpenTelemetryExporter
from src.observability.storage import ObservabilityDatabase

logger = logging.getLogger(__name__)


def _build_exporters(database: ObservabilityDatabase):
    exporters = []
    if settings.observability_jsonl_enabled:
        exporters.append(JsonlExporter(Path(settings.observability_jsonl_path)))
    endpoint = settings.observability_otlp_endpoint or None
    headers = None
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        credentials = f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()
        authorization = base64.b64encode(credentials).decode("ascii")
        endpoint = f"{settings.langfuse_host.rstrip('/')}/api/public/otel/v1/traces"
        headers = {"Authorization": f"Basic {authorization}", "x-langfuse-ingestion-version": "4"}
    elif settings.langfuse_public_key or settings.langfuse_secret_key:
        logger.warning("Langfuse 未启用：需要同时配置 public key 和 secret key")
    exporters.append(
        OpenTelemetryExporter(
            service_name=settings.observability_service_name,
            endpoint=endpoint,
            headers=headers,
            database=database,
        )
    )
    return exporters


observability_database = ObservabilityDatabase(settings.observability_sqlite_path)
observability_client = ObservabilityClient(
    observability_database,
    project_key=settings.observability_project_key,
    project_name=settings.observability_project_name,
    service_name=settings.observability_service_name,
    exporters=_build_exporters(observability_database),
)
