from __future__ import annotations

import httpx
import pytest

from src.core.config import Settings
from src.server import app


def test_default_server_host_is_local() -> None:
    assert Settings(_env_file=None).host == "127.0.0.1"


@pytest.mark.asyncio
async def test_local_frontend_is_allowed_but_external_origin_is_rejected() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        allowed = await client.options(
            "/api/v1/seo-agent/system/sites",
            headers={"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST"},
        )
        forbidden_preflight = await client.options(
            "/api/v1/seo-agent/system/sites",
            headers={"Origin": "https://outside.example", "Access-Control-Request-Method": "POST"},
        )
        forbidden_request = await client.post(
            "/api/v1/seo-agent/system/sites",
            headers={"Origin": "https://outside.example"},
            content="{}",
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert forbidden_preflight.status_code == 403
    assert forbidden_request.status_code == 403
