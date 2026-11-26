from __future__ import annotations

import asyncio
import json
import shutil

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from src.api.routes.workspace.request_body import read_limited_body
from src.core.config import Settings, settings
from src.core.source_settings import (
    SOURCES,
    SourceDefinition,
    environment_manages,
    save_source_settings,
)
from src.facade.data_facade import facade

router = APIRouter(prefix="/workspace/sources", tags=["Workspace Data Sources"])
_HEADERS = {"Cache-Control": "private, no-store"}


def _value(config: Settings, name: str) -> str | bool:
    value = getattr(config, name)
    return value.get_secret_value() if isinstance(value, SecretStr) else value


def _editable(field: str) -> bool:
    return not settings.is_production and not environment_manages(field)


def _describe(source: SourceDefinition, saved: Settings, modes: dict[str, str]) -> dict:
    configured = all(
        bool(_value(settings, field.name)) for field in source.fields if field.required
    )
    missing_flyai = source.id == "flyai" and not shutil.which(settings.flyai_executable)
    mode = modes.get(source.id) or (
        "unconfigured" if not configured
        else "missing_client" if missing_flyai
        else "configured" if source.fields
        else "builtin"
    )
    if source.id == "local_catalog":
        mode = "builtin" if settings.taobao_tbk_local_catalog else "disabled"
    return {
        "id": source.id, "name": source.name, "group": source.group,
        "description": source.description, "mode": mode,
        "restart_required": any(
            _value(saved, field.name) != _value(settings, field.name) for field in source.fields
        ),
        "fields": [{
            "id": field.name, "label": field.label, "kind": field.kind, "required": field.required,
            "editable": _editable(field.name), "configured": bool(_value(saved, field.name)),
        } for field in source.fields],
    }


def _catalogue() -> list[dict]:
    try:
        saved = Settings()
    except (OSError, ValueError):
        raise HTTPException(503, "暂时无法读取保存的数据源配置。") from None
    modes = facade.provider_modes()
    return [_describe(source, saved, modes) for source in SOURCES]


@router.get("")
async def list_sources() -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(_catalogue), headers=_HEADERS)


@router.patch("/{source_id}")
async def update_source(source_id: str, request: Request) -> JSONResponse:
    source = next((item for item in SOURCES if item.id == source_id and item.fields), None)
    if source is None:
        raise HTTPException(404, "此数据源没有可修改的配置。")
    if settings.is_production:
        raise HTTPException(403, "此部署的数据源由服务器环境配置管理。")
    raw = await read_limited_body(request, 16_384, "配置内容超过大小上限。")
    try:
        data = json.loads(raw)
    except (UnicodeError, ValueError, RecursionError):
        raise HTTPException(422, "数据源配置格式无效。") from None
    fields = {field.name: field for field in source.fields}
    if not isinstance(data, dict) or not data or any(name not in fields for name in data):
        raise HTTPException(422, "请选择此数据源支持的配置项。")
    changes: dict[str, str | bool] = {}
    for name, value in data.items():
        if not _editable(name):
            raise HTTPException(409, "此配置项由服务器环境变量管理。")
        if fields[name].kind == "switch":
            if type(value) is not bool:
                raise HTTPException(422, "开关配置须为布尔值。")
            changes[name] = value
        else:
            if (
                not isinstance(value, str) or len(value) > 4096
                or any(ord(char) < 32 for char in value)
            ):
                raise HTTPException(422, "请提供单行配置值。")
            changes[name] = value.strip()
    try:
        await asyncio.to_thread(save_source_settings, changes)
    except (OSError, ValueError):
        raise HTTPException(503, "暂时无法保存数据源配置。") from None
    return JSONResponse(await asyncio.to_thread(_catalogue), headers=_HEADERS)
