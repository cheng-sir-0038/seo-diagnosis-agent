"""可供新闻、图片、视频和商品复用的卡片展示辅助。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.models.common import Capability
from src.seo_presentation.contracts import base_contract


def build_card_contract(
    *,
    capability: Capability,
    response: Mapping[str, Any],
    variant: str,
    title_key: str,
    title: str,
    description_key: str,
    description: str,
    items: Sequence[Mapping[str, Any]],
    limitations: Sequence[str] = (),
) -> dict[str, Any]:
    contract = base_contract(
        capability=capability.value,
        view_type="cards",
        title_key=title_key,
        title_fallback=title,
        description_key=description_key,
        description_fallback=description,
        response=response,
        limitations=limitations,
    )
    contract["variant"] = variant
    contract["items"] = [dict(item) for item in items]
    return contract
