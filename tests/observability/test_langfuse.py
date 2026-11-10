from __future__ import annotations

import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from src.observability import runtime
from src.observability.client import ObservabilityClient
from src.observability.models import SpanKind
from src.observability.storage import ObservabilityDatabase


@pytest.mark.asyncio
async def test_langfuse_cloud_export_uses_otlp_and_project_credentials(
    tmp_path: Path, monkeypatch
) -> None:
    requests: list[tuple[str, str, str, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((
                self.path, self.headers.get("Authorization", ""),
                self.headers.get("x-langfuse-ingestion-version", ""), body,
            ))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(runtime.settings, "langfuse_host", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(runtime.settings, "langfuse_public_key", "pk-test")
    monkeypatch.setattr(runtime.settings, "langfuse_secret_key", "sk-test")
    monkeypatch.setattr(runtime.settings, "observability_jsonl_enabled", False)
    database = ObservabilityDatabase(tmp_path / "observability.db")
    client = ObservabilityClient(
        database, project_key="seo-agent", exporters=runtime._build_exporters(database)
    )
    try:
        await client.startup()
        async with client.trace("seo.chat", input_data={
            "message": "private-example", "headers": {"X-API-Key": "fake-header-secret"},
        }) as trace:
            async with trace.run(trigger_type="test") as run:
                async with run.span("model", kind=SpanKind.MODEL) as span:
                    await span.model_call(
                        provider="deepseek", model="deepseek-v4-flash", operation="stream",
                        input_tokens=10, output_tokens=5, total_tokens=15, latency_ms=30,
                    )
        assert await client.exporters[-1].force_flush()
        assert requests
        path, authorization, version, body = requests[-1]
        assert path == "/api/public/otel/v1/traces"
        assert authorization == "Basic " + base64.b64encode(b"pk-test:sk-test").decode()
        assert version == "4"
        message = ExportTraceServiceRequest.FromString(body)
        spans = [span for resource in message.resource_spans for scope in resource.scope_spans
                 for span in scope.spans]
        assert len(spans) == 3
        assert len({span.trace_id for span in spans}) == 1
        assert b"private-example" in body
        assert b"fake-header-secret" not in body
        stored_input = await database.fetch_value("SELECT input_json FROM obs_traces")
        assert "fake-header-secret" not in stored_input
    finally:
        await client.shutdown()
        server.shutdown()
        server.server_close()
        thread.join()


def test_incomplete_langfuse_credentials_do_not_enable_export(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(runtime.settings, "langfuse_public_key", "pk-test")
    monkeypatch.setattr(runtime.settings, "langfuse_secret_key", "")
    monkeypatch.setattr(runtime.settings, "observability_otlp_endpoint", "")
    monkeypatch.setattr(runtime.settings, "observability_jsonl_enabled", False)
    exporters = runtime._build_exporters(ObservabilityDatabase(tmp_path / "observability.db"))
    assert exporters[-1].enabled is False
