from __future__ import annotations

import json
import stat

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes.workspace import sources
from src.core.config import Settings
from src.procurement_agent.taobao import TaobaoAffiliate


@pytest.mark.asyncio
async def test_saved_sources_enable_real_catalog_and_hide_keys(tmp_path, monkeypatch):
    path = tmp_path / "sources.json"
    monkeypatch.setenv("WORKSPACE_SOURCE_CONFIG", str(path))
    monkeypatch.delenv("TAOBAO_TBK_LOCAL_CATALOG", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.setattr(sources.settings, "taobao_tbk_local_catalog", False)
    app = FastAPI()
    app.include_router(sources.router, prefix="/api/v1")
    root = "/api/v1/workspace/sources"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        loaded = await http.get(root)
        initial = next(row for row in loaded.json() if row["id"] == "local_catalog")
        assert initial["mode"] == "disabled"
        credential = "isolated-test-key"
        saved = await http.patch(root + "/serper", json={"serper_api_key": credential})
        assert saved.status_code == 200 and credential not in saved.text
        invalid = await http.patch(root + "/serper", json={"database_url": credential})
        assert invalid.status_code == 422 and credential not in invalid.text

        saved = await http.patch(root + "/local_catalog", json={"taobao_tbk_local_catalog": True})
        assert saved.status_code == 200
        item = next(row for row in saved.json() if row["id"] == "local_catalog")
        assert item["mode"] == "disabled" and item["restart_required"]
        persisted = json.loads(path.read_text())
        assert persisted == {"taobao_tbk_local_catalog": True, "serper_api_key": credential}
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

        restarted = Settings(_env_file=None)
        assert restarted.taobao_tbk_local_catalog is True
        provider = TaobaoAffiliate(
            restarted.taobao_tbk_app_key, restarted.taobao_tbk_app_secret,
            restarted.taobao_tbk_adzone_id, local_catalog=restarted.taobao_tbk_local_catalog,
        )
        result = await provider.search("200ml 纸杯", tmall_only=False)
        assert result["source"] == "local_product_catalog" and result["items"]

        monkeypatch.setenv("TAOBAO_TBK_LOCAL_CATALOG", "false")
        assert Settings(_env_file=None).taobao_tbk_local_catalog is False
        locked = await http.patch(root + "/local_catalog", json={"taobao_tbk_local_catalog": True})
        assert locked.status_code == 409


@pytest.mark.asyncio
async def test_optional_source_field_can_be_cleared_independently(tmp_path, monkeypatch):
    path = tmp_path / "sources.json"
    monkeypatch.setenv("WORKSPACE_SOURCE_CONFIG", str(path))
    for name in ("TAOBAO_TBK_APP_KEY", "TAOBAO_TBK_APP_SECRET", "TAOBAO_TBK_ADZONE_ID"):
        monkeypatch.delenv(name, raising=False)
    original = {
        "taobao_tbk_app_key": "test-app-key", "taobao_tbk_app_secret": "test-app-secret",
        "taobao_tbk_adzone_id": "test-placement",
    }
    path.write_text(json.dumps(original))
    monkeypatch.setattr(sources, "settings", Settings(_env_file=None))
    app = FastAPI()
    app.include_router(sources.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        result = await http.patch("/workspace/sources/taobao", json={"taobao_tbk_adzone_id": ""})
    assert result.status_code == 200
    source = next(row for row in result.json() if row["id"] == "taobao")
    fields = {field["id"]: field for field in source["fields"]}
    assert not fields["taobao_tbk_adzone_id"]["required"]
    assert not fields["taobao_tbk_adzone_id"]["configured"]
    assert fields["taobao_tbk_app_key"]["configured"] and source["restart_required"]
    assert all(value not in result.text for value in original.values())
    assert json.loads(path.read_text()) == {**original, "taobao_tbk_adzone_id": ""}
