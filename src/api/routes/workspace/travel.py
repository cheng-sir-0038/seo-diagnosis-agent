from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from src.core.config import settings
from src.travel_agent.context import MAX_ROUTE_POINTS, route_map_preview

router = APIRouter(prefix="/workspace/travel", tags=["Workspace Travel"])
Longitude = Annotated[float, Field(ge=-180, le=180)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Point = tuple[Longitude, Latitude]


class MapPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: Point
    destination: Point
    path: list[Point] = Field(default_factory=list, max_length=MAX_ROUTE_POINTS)


@router.post("/map-preview", response_class=Response, responses={200: {
    "content": {"image/png": {}}, "description": "百度地图驾车路线预览",
}})
async def preview_map(query: MapPreviewRequest) -> Response:
    api_key = settings.baidu_maps_ak.get_secret_value().strip()
    if not api_key:
        raise HTTPException(503, "百度地图查询尚未配置。")
    image = await route_map_preview(query.origin, query.destination, query.path, api_key=api_key)
    if image is None:
        raise HTTPException(502, "暂时无法加载百度地图预览。")
    return Response(image, media_type="image/png", headers={"Cache-Control": "private, no-store"})
