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


@pytest.mark.asyncio
async def test_text_model_status_uses_startup_values_and_reports_saved_selection(tmp_path, monkeypatch):
    source_file = tmp_path / "source-settings.json"
    default_env = tmp_path / ".env"
    local_env = tmp_path / ".env.local"
    deepseek_key = "synthetic-deepseek-for-source-test"
    openai_key = "synthetic-openai-for-source-test"
    default_env.write_text(f"DEEPSEEK_API_KEY={deepseek_key}\nDEEPSEEK_MODEL=default-deepseek\n")
    local_env.write_text("DEEPSEEK_MODEL=local-deepseek\nGEO_OPENAI_MODEL=geo-only-sample\n")
    monkeypatch.setenv("WORKSPACE_SOURCE_CONFIG", str(source_file))
    monkeypatch.setenv("ENVIRONMENT", "development")
    for key in ("OPENAI_API_KEY", "OPENAI_MODEL", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL",
                "AGENT_MODEL_PROVIDER", "GEO_OPENAI_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", (default_env, local_env))
    monkeypatch.setattr(sources, "settings", Settings())
    app = FastAPI()
    app.include_router(sources.router)
    root = "/workspace/sources"
    current = {"provider": "deepseek", "model": "local-deepseek", "configured": True}
    pending = {"provider": "openai", "model": "openai-saved", "configured": True}

    def row(result, source_id="agent_model"):
        assert result.status_code == 200
        assert openai_key not in result.text and deepseek_key not in result.text
        return next(item for item in result.json() if item["id"] == source_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        first = row(await http.get(root))
        assert first["text_model"] == current and not first["restart_required"]
        registered = await http.patch(root + "/openai", json={
            "openai_api_key": openai_key, "openai_model": "openai-saved",
        })
        assert row(registered)["text_model"] == current
        assert row(registered)["pending_text_model"] == pending
        assert row(registered, "openai")["restart_required"]
        assert Settings().geo_openai_model == "geo-only-sample"
        assert (await http.patch(root + "/agent_model", json={"agent_model_provider": "other"})).status_code == 422
        assert (await http.patch(root + "/openai", json={"openai_model": "a b"})).status_code == 422

        explicitly_saved = row(await http.patch(root + "/agent_model", json={"agent_model_provider": "deepseek"}))
        assert explicitly_saved["fields"][0]["value"] == "deepseek"
        assert explicitly_saved["restart_required"]
        assert "pending_text_model" not in explicitly_saved
        monkeypatch.setattr(sources, "settings", Settings())
        assert not row(await http.get(root))["restart_required"]

        monkeypatch.setenv("AGENT_MODEL_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_MODEL", "server-openai")
        monkeypatch.setattr(sources, "settings", Settings())
        managed = await http.get(root)
        assert row(managed)["text_model"] == {**pending, "model": "server-openai"}
        assert not row(managed)["fields"][0]["editable"]
        assert (await http.patch(root + "/agent_model", json={"agent_model_provider": "auto"})).status_code == 409
        assert (await http.patch(root + "/openai", json={"openai_model": "openai-edit"})).status_code == 409
