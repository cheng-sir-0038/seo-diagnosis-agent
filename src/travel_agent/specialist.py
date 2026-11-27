"""Register the workspace's business travel workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from src.agent.tools.registry import ToolRegistry
from src.agent.workspace.registry import (
    PublicInstruction,
    PublicSkill,
    RoutingTurn,
    SpecialistDefinition,
    SpecialistProfile,
    ToolContext,
)
from src.observability.instrumentation import observed_operation
from src.observability.models import SpanKind
from src.travel_agent.intake import TRAVEL_DOMAIN, TRAVEL_INTAKE_PROMPT, TravelIntake
from src.travel_agent.tools import (
    COMMUTE,
    COMPARE,
    HOTELS,
    SAVE,
    TRANSPORT,
    TRAVEL_SKILL,
    WEATHER,
    travel_tools,
)
from src.travel_agent.workflow import run_travel

if TYPE_CHECKING:
    from src.agent.workspace.execution import SpecialistExecution


class TravelRouteInput(BaseModel):
    travel: TravelIntake | None = None


async def build_travel_tools(context: ToolContext) -> ToolRegistry:
    """Create live travel tools with the workspace's provider configuration."""
    settings = context.resources.settings
    return travel_tools(
        flyai_api_key=settings.flyai_api_key.get_secret_value(),
        flyai_executable=settings.flyai_executable,
        baidu_maps_ak=settings.baidu_maps_ak.get_secret_value(),
    )


def prepare_travel_route(turn: RoutingTurn) -> str | None:
    """Checkpoint extracted conditions for travel without adopting a page target."""
    turn.state.pop("target_url", None)
    intake = getattr(turn.decision, "travel", None) or TravelIntake()
    turn.state["travel_intake"] = intake.model_dump(mode="json")
    return None


async def execute_workspace_travel(execution: SpecialistExecution) -> None:
    """Continue travel intake, evidence gathering, comparison and draft selection."""
    run, executor = execution.run, execution.executor
    async with observed_operation(
        execution.resources.observability, "workspace.travel", kind=SpanKind.AGENT,
        input_data={"skill": TRAVEL_SKILL.name},
        metadata={"agent_profile": execution.definition.profile.public_data()},
    ) as span:
        executor.tools = await execution.tools()
        await execution.record_skill(TRAVEL_SKILL)
        run.dispatch_state["active_agent"] = execution.definition.id
        await run_travel(executor, run, execution.resources.planner)
        if span:
            span.output_data = {"status": run.status.value}


TRAVEL_SPECIALIST = SpecialistDefinition(
    id=TRAVEL_DOMAIN,
    profile=SpecialistProfile("商旅行程", "travel", "lavender", "travel"),
    routing_prompt=TRAVEL_SKILL.prompt_fragment + (
        "\n选择 travel 后填写 travel 字段，不需要网页。"
        "报价只能摘自最后一条用户消息。"
    ) + TRAVEL_INTAKE_PROMPT,
    tools=build_travel_tools,
    execute=execute_workspace_travel,
    intake=TravelRouteInput,
    prepare=prepare_travel_route,
    directory_note="核对国内出差的交通、住宿、路线和天气。",
    deterministic_path="条件确认后的报价核对、行程比较和方案保存按固定规则执行，无额外提示词。",
    public_skills=(PublicSkill.publish(
        TRAVEL_SKILL,
        summary="为国内单目的地出差核对往返交通、住宿、接驳、天气和预算。",
        instruction=(
            "先确认要查单程、往返、住宿还是组合行程，保留已明确的城市、日期和查询范围。"
            "按用户所需查班次、住宿、接驳和天气，列出实际取得的结果及来源。"
            "按原报价口径和币种核对费用，依据到站时刻、接驳估算和已提供的缓冲时间核对到场安排。"
            "将影响费用或到场判断的缺项标在相应方案旁。只保存用户从当前候选中选定的草稿，"
            "实际价格、可订情况和付款以平台为准。"
        ),
        usage={
            TRANSPORT: (
                "查国内单目的地出差的单程或往返火车、航班。",
                "提供出发城市、目的城市和出发日期。查往返时再提供返程日期。",
                "通过已配置的飞猪服务和 FlyAI 客户端按出行方向分别查询火车与航班。仅查询班次时可省略人数。",
                "列出查到的班次、时刻、车站或机场、可见参考价和平台链接。实际票价与可订情况以平台为准。",
            ),
            HOTELS: (
                "按入住和退房日期查出差酒店，也可只查住宿。",
                "提供住宿城市、入住及退房日期、入住人数和房间数。",
                "通过已配置的飞猪服务和 FlyAI 客户端按城市和日期查询。查询保留所需人数和房间数，房型及能否入住由平台确认。",
                "列出查到的酒店、地址、可见参考价和平台链接。结算金额以所选房型和实际订房条件为准。",
            ),
            WEATHER: (
                "查出差目的地的逐日天气，准备衣物和雨具。",
                "提供目的城市和行程起止日期。只有目的城市时，可先看标明日期的近 3 天预报。",
                "按 Open-Meteo 的城市级数据查询，预报窗口从当地当天起最多覆盖 16 天。",
                "给出实际覆盖日期的气温、降雨概率、风速等预报及出行建议。超出预报范围时提示临近出发再查。",
            ),
            COMMUTE: (
                "估算从到达车站、机场或备选酒店到会场等到访地点的驾车接驳。",
                "提供目的城市和到访地点的名称或地址，接驳起点取本次交通或酒店候选中已有的地点。",
                "已配置百度地图且两端地点能定位时，采用查询时返回的驾车路线。实际耗时随出发时间和路况变化。",
                "按起点列出预计车程、距离和地图链接。地点无法定位时，指出需核对的地点。",
            ),
            COMPARE: (
                "组合交通和住宿候选，比较费用、接驳时间和到场安排。",
                "确认行程日期和所需交通、住宿，可贴已有班次或酒店报价原文。按需提供人数、房间数、总预算、到访地点、"
                "最晚到场时间和缓冲时间。",
                "按日期和先后顺序组合候选，费用按原报价口径与币种核算。到场时间依据到站时刻、接驳估算及已提供的缓冲时间。",
                "最多给出 3 个方案，列出报价出处、已知费用，以及到场与预算的核对结果和影响结论的具体缺项。",
            ),
            SAVE: (
                "从本次行程候选中选定一个，留作预订前的草稿。",
                "回复当前方案序号，例如「保存方案 1」。",
                "核对所选方案仍属于本次最新的行程草稿。行程条件变化后须从更新的候选中选择。",
                "在本次任务中保存所选草稿，保留报价出处和需核对的项目。实际预订和付款由你在平台办理。",
            ),
        },
    ),),
    public_instructions=(PublicInstruction(
        id="travel-intake",
        label="商旅条件提取",
        text=(
            "按用户明确提出的需求记录出行范围、城市、日期、住宿与到访条件。相对日期以中国时区为准，"
            "最晚到达时间只取明确给出的日期和时间。区分行程总预算与每晚住宿预算。"
            "从本轮消息逐字摘录报价，保留原文中的班次、房型、价格口径和链接。"
            "更新行程时以最新的更正为准，只有适用条件仍相符的历史报价可沿用。"
        ),
    ),),
)
