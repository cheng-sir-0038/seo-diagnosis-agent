from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.agent.models import AgentRun, HumanInputState
from src.agent.providers.base import ToolCall
from src.agent.runtime.executor import AgentExecutor
from src.seo_agent.workflows.structured_model import StructuredModelRunner
from src.travel_agent.intake import (
    TRAVEL_INTAKE_PROMPT_KEY,
    TRAVEL_TIMEZONE,
    TravelIntake,
    china_today,
    supplied_offers,
)
from src.travel_agent.plan import TripRequest
from src.travel_agent.tools import COMMUTE, COMPARE, HOTELS, SAVE, TRANSPORT, WEATHER, WeatherScope

_CONTINUE = "travel_continue"
_SELECT = re.compile(r"(?:(?:保存|选择|选)\s*(?:方案)?\s*|方案\s*)([1-9][0-9]?)|([1-9][0-9]?)")
_FINISH = {"结束", "结束规划", "结束本次规划"}
_REFRESH_TRANSPORT = {"重查交通", "重新查询交通", "重新查交通"}
_DIRECTIONS = ("outbound", "return")
_NEAR_TERM_FORECAST_DAYS = 3


class _ToolBudgetError(Exception):
    pass


def _key(arguments: dict) -> str:
    return hashlib.sha256(
        json.dumps(arguments, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _questions(request: TripRequest) -> list[str]:
    today = china_today()
    questions = []
    if not request.origin or not request.destination:
        cities = (
            "出发城市和目的城市"
            if not request.origin and not request.destination
            else ("出发城市" if not request.origin else "目的城市")
        )
        questions.append(f"{cities}是什么？")
    elif request.origin.removesuffix("市") == request.destination.removesuffix("市"):
        questions.append("请分别确认出发城市与目的城市。")
    if not request.depart_on or not request.return_on:
        missing_date = (
            "出发和返程日期"
            if not request.depart_on and not request.return_on
            else ("出发日期" if not request.depart_on else "返程日期")
        )
        questions.append(f"{missing_date}是哪一天？")
    elif request.depart_on < today or request.return_on < request.depart_on:
        questions.append("请核对出发和返程日期，出发不得早于今天，返程不得早于出发。")
    if request.arrive_by and request.depart_on and request.return_on:
        meeting_day = request.arrive_by.astimezone(ZoneInfo(TRAVEL_TIMEZONE)).date()
        if not request.depart_on <= meeting_day <= request.return_on:
            questions.append("最晚到场日期和行程日期有冲突，请确认日期和到场时间。")
    details = []
    if not request.venue:
        details.append("会场的名称或地址")
    if not request.travelers:
        details.append("出行人数")
    if not request.rooms and request.depart_on != request.return_on:
        details.append("需要几间房")
    if details:
        questions.append(f"还请补充{'、'.join(details)}。")
    return questions[:3]


def _bind_conditions(request: TripRequest, direction: str) -> dict:
    values = request.model_dump(mode="json")
    fields = {
        "outbound": ("origin", "destination", "depart_on", "travelers"),
        "return": ("origin", "destination", "return_on", "travelers"),
        "hotel": ("destination", "depart_on", "return_on", "travelers", "rooms"),
    }[direction]
    return {key: values[key] for key in fields if values[key] is not None}


def _merge(data: dict, intake: TravelIntake, text: str) -> None:
    request = TripRequest.model_validate(
        {
            **data.get("request", {}),
            **intake.request.model_dump(exclude_none=True),
        }
    )
    offers = {}
    for saved in data.get("manual", []):
        item = saved["offer"]
        bound = _bind_conditions(request, item["direction"])
        if all(bound.get(key) == value for key, value in saved["conditions"].items()):
            offers[item["id"]] = {"offer": item, "conditions": bound}
    for item in supplied_offers(intake, text):
        offers[item["id"]] = {
            "offer": item,
            "conditions": _bind_conditions(request, item["direction"]),
        }
    conditions = [value for value in intake.conditions_to_verify if value and value in text]
    if conditions:
        data["conditions_to_verify"] = conditions
    data.update(
        request=request.model_dump(mode="json", exclude_none=True),
        manual=list(offers.values())[-9:],
    )


async def _call(
    executor: AgentExecutor,
    run: AgentRun,
    name: str,
    arguments: dict,
    *,
    attempt: int = 0,
) -> dict:
    signature = _key({"arguments": arguments, "attempt": attempt})
    call = ToolCall(f"{name}:{signature[:20]}", name, arguments)
    cached = any(
        event.type == "tool.completed" and event.payload.get("tool_call_id") == call.id
        for event in run.events
    )
    reserve_for_save = int(name != SAVE)
    if not cached and run.tool_count >= executor.max_tool_calls - reserve_for_save:
        raise _ToolBudgetError
    result = await executor.required_tool(run, call)
    if result.success:
        return result.data
    if name in {COMPARE, SAVE}:
        raise RuntimeError(result.error or "商旅行程处理失败")
    return {"status": "error", "items": [], "message": result.error or "数据来源暂时不可用。"}


def _fly_candidates(receipt: dict, direction: str, kind: str) -> list[dict]:
    return [
        {
            **item,
            "id": f"{direction}-{item['id']}",
            "direction": direction,
            "kind": kind,
            "queried_at": receipt.get("queried_at"),
        }
        for item in receipt.get("items", [])
    ]


def _source(receipt: dict, label: str, source: str) -> dict:
    return {
        "label": label,
        "source": source,
        "status": receipt["status"],
        "queried_at": receipt.get("queried_at"),
        "message": receipt.get("message"),
    }


def _route_origins(offers: list[dict]) -> dict[str, str]:
    def addresses(direction: str, field: str) -> dict[str, str]:
        matching = {
            item["id"]: item[field]
            for item in offers
            if (item["direction"] == direction and item.get(field))
        }
        chosen = set(list(dict.fromkeys(matching.values()))[:3])
        return {item_id: address for item_id, address in matching.items() if address in chosen}

    return {**addresses("outbound", "to_stop"), **addresses("hotel", "address")}


async def _transport(
    executor: AgentExecutor,
    run: AgentRun,
    data: dict,
    request: dict,
) -> dict:
    legs = data.setdefault("legs", {})
    keys = {
        direction: _key(
            {
                "origin": request["origin"],
                "destination": request["destination"],
                "date": request["depart_on"] if direction == "outbound" else request["return_on"],
            }
        )
        for direction in _DIRECTIONS
    }
    changed = [
        direction
        for direction in _DIRECTIONS
        if legs.get(direction, {}).get("key") != keys[direction]
    ]
    if changed:
        criteria = {
            key: request[key] for key in ("origin", "destination", "depart_on", "return_on")
        }
        results = await _call(
            executor,
            run,
            TRANSPORT,
            {**criteria, "directions": changed},
            attempt=data.get("transport_attempt", 0),
        )
        legs.update(
            {
                direction: {"key": keys[direction], "receipt": results.get(direction, results)}
                for direction in changed
            }
        )
    return {direction: legs[direction]["receipt"] for direction in _DIRECTIONS}


async def _gather(executor: AgentExecutor, run: AgentRun, data: dict) -> dict:
    request = data["request"]
    destination = request["destination"]
    depart_on, return_on = request["depart_on"], request["return_on"]
    candidates = await _transport(executor, run, data, request)
    offers = [item["offer"] for item in data["manual"]]
    providers = []
    for direction, label in (("outbound", "飞猪去程"), ("return", "飞猪返程")):
        receipt = candidates[direction]
        offers.extend(_fly_candidates(receipt, direction, "transport"))
        if modes := receipt.get("modes"):
            providers.extend(
                _source({**mode, "queried_at": receipt.get("queried_at")}, label + title, "flyai")
                for name, title in (("rail", "火车"), ("flight", "航班"))
                if (mode := modes.get(name))
            )
        else:
            providers.append(_source(receipt, label, "flyai"))
    if depart_on != return_on:
        hotels = await _call(
            executor,
            run,
            HOTELS,
            {
                "destination": destination,
                "check_in": depart_on,
                "check_out": return_on,
                "travelers": request["travelers"],
                "rooms": request["rooms"],
            },
        )
        offers.extend(_fly_candidates(hotels, "hotel", "hotel"))
        providers.append(_source(hotels, "飞猪酒店", "flyai"))
    addresses = _route_origins(offers)
    commute = await _call(
        executor,
        run,
        COMMUTE,
        {
            "city": destination,
            "destination": request["venue"],
            "origins": list(dict.fromkeys(addresses.values())),
        },
    )
    providers.append(_source(commute, "百度地图驾车", "baidu"))
    weather = await _call(
        executor, run, WEATHER, {"city": destination, "start": depart_on, "end": return_on}
    )
    return {
        "request": request,
        "offers": offers,
        "providers": providers,
        "routes": {
            item_id: commute["routes"][address]
            for item_id, address in addresses.items()
            if address in commute.get("routes", {})
        },
        "weather": {"source": "open_meteo", **weather},
    }


async def _forecast_while_waiting(
    executor: AgentExecutor, run: AgentRun, request: TripRequest
) -> None:
    if not request.destination or run.tool_count >= executor.max_tool_calls - 1:
        return
    today = china_today()
    start = request.depart_on or request.return_on
    if start:
        end = request.return_on or start
        if today <= start <= end:
            await _call(
                executor,
                run,
                WEATHER,
                {
                    "city": request.destination,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
            )
        return
    end = today + timedelta(days=_NEAR_TERM_FORECAST_DAYS - 1)
    await _call(
        executor,
        run,
        WEATHER,
        {
            "city": request.destination,
            "start": today.isoformat(),
            "end": end.isoformat(),
            "scope": WeatherScope.NEAR_TERM.value,
        },
    )


async def _wait(
    executor: AgentExecutor, run: AgentRun, questions: list[str], message: str | None = None
) -> None:
    run.human_input = HumanInputState(
        request_id=f"input_{uuid4().hex}",
        tool_name=_CONTINUE,
        tool_arguments={},
        tool_result={},
        questions=questions,
        message=message,
    )
    await executor.run_turns(run)


async def _finish(executor: AgentExecutor, run: AgentRun, answer: str) -> None:
    run.human_input = None
    await executor.run_turns(run, evidence_answer=lambda _: answer)


async def _save(executor: AgentExecutor, run: AgentRun, data: dict, number: int) -> None:
    snapshot = data.get("snapshot") or {}
    plans = snapshot.get("plans", [])
    if snapshot.get("request") != data["request"] or not 1 <= number <= len(plans):
        await _wait(executor, run, ["请回复当前方案序号，或补充报价与行程条件。"])
        return
    result = await _call(
        executor,
        run,
        SAVE,
        {
            "snapshot": snapshot,
            "selected_plan_id": plans[number - 1]["id"],
        },
    )
    data["snapshot"] = result
    await executor.checkpoint(run, stage="travel_draft_saved")
    await _finish(
        executor,
        run,
        f"方案 {number} 已选为本次行程草稿。详情保留了来源和待确认的项目，"
        "价格和可订情况请以平台为准。",
    )


async def run_travel(
    executor: AgentExecutor, run: AgentRun, planner: StructuredModelRunner
) -> None:
    saved = run.dispatch_state
    pending = run.human_input
    if pending and pending.answer is None:
        await executor.run_turns(run)
        return
    data = saved.setdefault("travel", {})
    if not data:
        initial_text = next(
            (
                str(message["content"])
                for message in reversed(run.messages)
                if message["role"] == "user" and message.get("content")
            ),
            saved.get("intent", ""),
        )
        _merge(
            data,
            TravelIntake.model_validate(saved.get("travel_intake") or {}),
            initial_text,
        )
    try:
        if pending and pending.answer:
            if not any(
                event.type == "input.accepted"
                and event.payload.get("request_id") == pending.request_id
                for event in run.events
            ):
                await executor.emit(
                    run,
                    "input.accepted",
                    {
                        "request_id": pending.request_id,
                        "answer": pending.answer,
                        "action": pending.action,
                    },
                )
            if pending.answer in _FINISH:
                await _finish(executor, run, "本次行程规划已结束。")
                return
            if match := _SELECT.fullmatch(pending.answer):
                await _save(executor, run, data, int(match[1] or match[2]))
                return
            needs_extraction = data.get("processed_input") != pending.request_id
            if needs_extraction and pending.answer in _REFRESH_TRANSPORT:
                data.pop("legs", None)
                data["transport_attempt"] = data.get("transport_attempt", 0) + 1
                data["processed_input"] = pending.request_id
            elif needs_extraction:
                try:
                    extraction = await planner.invoke(
                        prompt_key=TRAVEL_INTAKE_PROMPT_KEY,
                        schema=TravelIntake,
                        method="json_mode",
                        context={
                            "current_date": china_today().isoformat(),
                            "confirmed_request": data["request"],
                            "user_message": pending.answer,
                        },
                        operation="workspace.travel.intake",
                        business_object={"run_id": run.id},
                    )
                except ValueError:
                    await _wait(executor, run, ["请用具体日期、地点或报价重新描述本次变更。"])
                    return
                for key, count in extraction.usage.items():
                    run.token_usage[key] = run.token_usage.get(key, 0) + count
                _merge(data, extraction.output, pending.answer)
                data["processed_input"] = pending.request_id

        request = TripRequest.model_validate(data["request"])
        questions = _questions(request)
        if questions:
            await _forecast_while_waiting(executor, run, request)
            await _wait(executor, run, questions)
            return
        await executor.checkpoint(run, stage="travel_conditions_confirmed")
        evidence = await _gather(executor, run, data)
        comparison_key = _key(evidence)
        if data.get("comparison_key") != comparison_key:
            versions = data.setdefault("versions", [])
            snapshot = await _call(
                executor, run, COMPARE, {**evidence, "revision": len(versions) + 1}
            )
            versions.append(snapshot)
            data.update(snapshot=snapshot, comparison_key=comparison_key)
        snapshot = data["snapshot"]
    except _ToolBudgetError:
        await _finish(
            executor,
            run,
            "本次行程已达到查询次数，已有结果保留在对话里。需要继续调整时，可发起新的行程规划。",
        )
        return
    verification = data.get("conditions_to_verify", [])
    note = f"\n\n仍需在平台逐项核对：{'；'.join(verification)}。" if verification else ""
    if any(
        source["source"] == "flyai"
        and source["label"] != "飞猪酒店"
        and source["status"] == "error"
        for source in snapshot["providers"]
    ):
        note += "\n\n部分交通查询失败，可回复「重查交通」。"
    if snapshot["plans"]:
        await _wait(
            executor,
            run,
            ["选择方案，或补充报价与行程条件。"],
            f"已经整理出 {len(snapshot['plans'])} 个候选方案。"
            "卡片里可以核对各段交通、酒店、已知费用和接驳估算。"
            "\n\n可回复「保存方案 1」保存草稿，也可以直接补充报价或更新出差条件。"
            f"{note}",
        )
        return
    await _wait(
        executor,
        run,
        ["可贴上查到的班次或酒店报价，修改行程条件，或回复「结束规划」。"],
        f"暂时没有可组合的交通或住宿候选。查询结果和可用的天气信息已放在卡片里。"
        f"\n\n可以直接补充平台报价和链接，或回复「结束规划」。{note}",
    )
