from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.agent.config import Skill
from src.agent.tools.base import AgentTool, ToolResult, ToolRuntimeContext
from src.agent.tools.registry import ToolRegistry
from src.travel_agent import context, flyai
from src.travel_agent.intake import TRAVEL_DOMAIN
from src.travel_agent.plan import TripRequest, build_travel_plan

TRANSPORT = "travel_transport"
HOTELS = "travel_hotels"
WEATHER = "travel_weather"
COMMUTE = "travel_commute"
COMPARE = "travel_compare_plans"
SAVE = "travel_save_plan"

class WeatherScope(StrEnum):
    ITINERARY = "itinerary"
    NEAR_TERM = "near_term"


TRAVEL_SKILL = Skill(
    name="business-trip-planning",
    domain=TRAVEL_DOMAIN,
    description="商旅行程规划与核对",
    tools=[TRANSPORT, HOTELS, WEATHER, COMMUTE, COMPARE, SAVE],
    prompt_fragment=(
        "travel 处理国内单目的地的出差去返程、住宿与会场安排，核对时间预算并保存用户选择。"
        "即使缺日期或会场，也先派给 travel，保留明确的条件。用户贴的报价和链接是行程资料。"
        "想做旅游站点的 SEO 仍归 seo。"
    ),
)


class _Tool(AgentTool):
    def __init__(
        self,
        name: str,
        description: str,
        schema: type[BaseModel],
        handler: Callable[..., Awaitable[dict]],
    ) -> None:
        self.name = name
        self.description = description
        self.schema = schema
        self.parameters_schema = schema.model_json_schema()
        self.handler = handler

    async def execute(
        self, arguments: dict, runtime: ToolRuntimeContext | None = None
    ) -> ToolResult:
        request = self.schema.model_validate(arguments)
        values = {field: getattr(request, field) for field in self.schema.model_fields}
        return ToolResult(success=True, data=await self.handler(**values))


class _TransportQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: str
    destination: str
    depart_on: date
    return_on: date
    directions: list[Literal["outbound", "return"]] = Field(min_length=1, max_length=2)


class _WeatherQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str
    start: date
    end: date
    scope: WeatherScope = WeatherScope.ITINERARY


class _HotelQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: str
    check_in: date
    check_out: date
    travelers: int = Field(ge=1)
    rooms: int = Field(ge=1)


class _CommuteQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str
    destination: str
    origins: list[str] = Field(max_length=6)


class _CompareQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: TripRequest
    offers: list[dict[str, Any]]
    routes: dict[str, dict]
    providers: list[dict]
    weather: dict
    revision: int = Field(ge=1)


class _SaveQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: dict
    selected_plan_id: str


async def _compare(
    request: TripRequest,
    offers: list[dict],
    routes: dict[str, dict],
    providers: list[dict],
    weather: dict,
    revision: int,
) -> dict:
    comparison = build_travel_plan(
        request,
        outbound=[item for item in offers if item["direction"] == "outbound"],
        inbound=[item for item in offers if item["direction"] == "return"],
        hotels=[item for item in offers if item["direction"] == "hotel"],
        commute_by_offer=routes,
    )
    for plan in comparison["plans"]:
        route = routes.get(plan.get("hotel"), {})
        first = next(iter(route.get("items", [])), {})
        plan["hotel_commute_minutes"] = (
            first.get("duration_minutes") if route.get("status") == "ok" else None
        )
    return {
        **comparison,
        "request": request.model_dump(mode="json", exclude_none=True),
        "offers": offers,
        "routes": routes,
        "providers": providers,
        "weather": weather,
        "revision": revision,
    }


async def _forecast(city: str, start: date, end: date, scope: WeatherScope) -> dict:
    result = await context.trip_weather(city, start, end)
    return {**result, "scope": scope.value, "requested_city": city}


async def _select(snapshot: dict, selected_plan_id: str) -> dict:
    if not any(plan["id"] == selected_plan_id for plan in snapshot["plans"]):
        raise ValueError("要保存的方案与当前行程版本不一致")
    return {**snapshot, "selected_plan_id": selected_plan_id}


def travel_tools(
    *,
    flyai_api_key: str = "",
    flyai_executable: str = "flyai",
    baidu_maps_ak: str = "",
) -> ToolRegistry:
    flyai_options = {"api_key": flyai_api_key, "executable": flyai_executable}

    async def transport(
        origin: str,
        destination: str,
        depart_on: date,
        return_on: date,
        directions: list[str],
    ) -> dict:
        legs = {
            "outbound": (origin, destination, depart_on),
            "return": (destination, origin, return_on),
        }
        results = await asyncio.gather(
            *(flyai.search_transport(*legs[direction], **flyai_options) for direction in directions)
        )
        return dict(zip(directions, results, strict=True))

    async def hotels(
        destination: str,
        check_in: date,
        check_out: date,
        travelers: int,
        rooms: int,
    ) -> dict:
        return await flyai.search_hotels(
            destination,
            check_in,
            check_out,
            travelers=travelers,
            rooms=rooms,
            **flyai_options,
        )

    async def commute(city: str, destination: str, origins: list[str]) -> dict:
        if not baidu_maps_ak.strip():
            return {
                "source": "baidu",
                "status": "unconfigured",
                "message": "百度地图查询尚未配置。",
            }
        results = await asyncio.gather(
            *(
                context.estimate_commute(address, destination, city, api_key=baidu_maps_ak)
                for address in origins
            )
        )
        statuses = {item["status"] for item in results}
        status = next(iter(statuses)) if len(statuses) == 1 else "partial"
        notes = list(
            dict.fromkeys(
                item["message"]
                for item in results
                if item.get("message") and item["status"] != "ok"
            )
        )
        return {
            "source": "baidu",
            "status": status if statuses else "no_results",
            "queried_at": next(
                (item["queried_at"] for item in results if item.get("queried_at")), None
            ),
            "message": (
                (" ".join(notes) or "驾车耗时为查询时的估算。")
                if results
                else "缺少可用于查路线的车站或酒店地址。"
            ),
            "routes": dict(zip(origins, results, strict=True)),
        }

    return ToolRegistry(
        [
            _Tool(TRANSPORT, "查询去返程的飞猪火车和航班候选。", _TransportQuery, transport),
            _Tool(HOTELS, "查询飞猪酒店和参考价。", _HotelQuery, hotels),
            _Tool(WEATHER, "查询目的地逐日预报与出行建议。", _WeatherQuery, _forecast),
            _Tool(COMMUTE, "查询车站或酒店到会场的百度地图驾车估算。", _CommuteQuery, commute),
            _Tool(
                COMPARE, "按已核对的班次、报价、路线和预算生成行程方案。", _CompareQuery, _compare
            ),
            _Tool(SAVE, "核对用户选择的方案属于当前草稿版本。", _SaveQuery, _select),
        ]
    )
