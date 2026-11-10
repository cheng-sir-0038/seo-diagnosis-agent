from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from src.models.common import Capability
from src.seo_presentation.contracts import SCHEMA_VERSION
from src.seo_presentation.registry import present_capability

RenderHint = Literal["chart", "table", "tree", "cards"]


def build_presentation(
    capability: Capability,
    hint: RenderHint | None,
    response: dict[str, Any],
) -> dict[str, Any]:
    """将统一业务证据转换为版本化、能力级展示契约。

    正式展示只允许走已注册的 Capability Presenter。未注册能力保留原始响应，
    但标记为 technical_fallback，避免字段名自动转换后进入用户数据证据区。
    """
    converted = present_capability(capability, response)
    if converted is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "capability": capability.value,
            "view_type": "technical",
            "presentation_mode": "technical_fallback",
            "raw": response,
        }
    converted["raw"] = response
    return converted


def can_render(hint: RenderHint | None, payload: Mapping[str, Any]) -> bool:
    if payload.get("presentation_mode") == "technical_fallback":
        return False
    view_type = payload.get("view_type")
    if hint == "chart":
        return view_type == "chart" and (
            bool(payload.get("metrics"))
            or (bool(payload.get("labels")) and bool(payload.get("datasets")))
        )
    if hint == "table":
        return (
            view_type == "table"
            and isinstance(payload.get("columns"), list)
            and isinstance(payload.get("rows"), list)
        )
    if hint == "tree":
        return view_type == "tree" and bool(payload.get("nodes"))
    if hint == "cards":
        return view_type == "cards" and isinstance(payload.get("items"), list)
    return False
