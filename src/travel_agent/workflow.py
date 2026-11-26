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
from src.travel_agent.flyai import parse_transport_time
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
    if not request.destination or (request.wants_outbound and not request.origin):
        cities = []
        if request.wants_outbound and not request.origin:
            cities.append("出发城市")
        if not request.destination:
            cities.append("目的城市" if request.wants_outbound else "住宿城市")
        questions.append(f"{'和'.join(cities)}是哪里？")
    elif request.wants_outbound and request.origin and request.origin.removesuffix("市") == request.destination.removesuffix("市"):
        questions.append("请分别确认出发城市与目的城市。")
    if not request.depart_on:
        questions.append("哪天出发？" if request.wants_outbound else "哪天入住？")
    elif request.depart_on < today:
        questions.append("请核对日期，出发或入住不能早于今天。")
    if (request.wants_return or request.wants_lodging) and not request.return_on:
        if not request.wants_return:
            questions.append("哪天退房？")
        elif request.transport == "round_trip" or request.wants_lodging:
            questions.append("哪天返程？")
        else:
            questions.append("只查去程，还是也要查返程？需要返程的话，请告诉我日期。")
    elif (request.wants_return or request.wants_lodging) and request.depart_on and request.return_on:
        if request.return_on < request.depart_on:
            questions.append("请核对日期，返程或退房不能早于出发或入住。")
        elif request.wants_lodging and request.return_on == request.depart_on:
            questions.append("需要住宿的话请确认退房日期，退房应晚于入住。")
    if request.wants_outbound and request.arrive_by and request.depart_on:
        arrival_day = request.arrive_by.astimezone(ZoneInfo(TRAVEL_TIMEZONE)).date()
        if arrival_day < request.depart_on or (request.wants_return and request.return_on and arrival_day > request.return_on):
            questions.append("最晚抵达时间和出行日期有冲突，请确认日期和时间。")
    if request.wants_lodging:
        details = []
        if not request.travelers:
            details.append("入住人数")
        if not request.rooms:
            details.append("房间数")
        if details:
            questions.append(f"请补充{'和'.join(details)}。")
    return questions[:3]



def _confirmed_summary(request: TripRequest) -> str | None:
    """Repeat known trip conditions so the reply can focus on missing ones."""
    route = " → ".join(city for city in (request.origin if request.wants_outbound else None, request.destination) if city)
    details = [route] if route else []
    if request.depart_on:
        details.append(f"{request.depart_on:%m月%d日}{'出发' if request.wants_outbound else '入住'}")
    if request.return_on and (request.wants_return or request.wants_lodging):
        details.append(f"{request.return_on:%m月%d日}{'返程' if request.wants_return else '退房'}")
    return f"已记下：{'、'.join(details)}。" if details else None


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
    candidates = [
        {
            **item,
            "id": f"{direction}-{item['id']}",
            "direction": direction,
            "kind": kind,
            "queried_at": receipt.get("queried_at"),
        }
        for item in receipt.get("items", [])
    ]
    if kind == "transport":
        # Reused FlyAI receipts may only contain the local clock values.
        for candidate in candidates:
            for timestamp, local in (
                ("departs_at", "departs_local"),
                ("arrives_at", "arrives_local"),
            ):
                if candidate.get(timestamp):
                    continue
                instant = parse_transport_time(candidate.get(local))
                if instant is not None:
                    candidate[timestamp] = instant.isoformat()
    return candidates


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
    directions = _DIRECTIONS if request.get("transport") != "one_way" else ("outbound",)
    keys = {
        direction: _key(
            {
                "origin": request["origin"],
                "destination": request["destination"],
                "date": request["depart_on"] if direction == "outbound" else request["return_on"],
            }
        )
        for direction in directions
    }
    changed = [
        direction
        for direction in directions
        if legs.get(direction, {}).get("key") != keys[direction]
    ]
    if changed:
        criteria = {
            key: request[key] for key in ("origin", "destination", "depart_on", "return_on") if key in request
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
    return {direction: legs[direction]["receipt"] for direction in directions}


async def _gather(executor: AgentExecutor, run: AgentRun, data: dict) -> dict:
    request = data["request"]
    trip = TripRequest.model_validate(request)
    destination = request["destination"]
    depart_on = request["depart_on"]
    return_on = request.get("return_on") if trip.wants_return or trip.wants_lodging else None
    requested = {
        direction for direction, enabled in (
            ("outbound", trip.wants_outbound), ("return", trip.wants_return), ("hotel", trip.wants_lodging),
        ) if enabled
    }
    candidates = await _transport(executor, run, data, request) if trip.wants_outbound else {}
    offers = [item["offer"] for item in data["manual"] if item["offer"]["direction"] in requested]
    providers = []
    for direction, label in (("outbound", "飞猪去程"), ("return", "飞猪返程")):
        if direction not in candidates:
            continue
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
    if trip.wants_lodging:
        hotels = await _call(
            executor, run, HOTELS,
            {"destination": destination, "check_in": depart_on, "check_out": return_on,
             "travelers": trip.travelers, "rooms": trip.rooms},
        )
        offers.extend(_fly_candidates(hotels, "hotel", "hotel"))
        providers.append(_source(hotels, "飞猪酒店", "flyai"))
    addresses = _route_origins(offers)
    commute = {}
    if trip.venue and addresses:
        commute = await _call(
            executor, run, COMMUTE,
            {"city": destination, "destination": trip.venue, "origins": list(dict.fromkeys(addresses.values()))},
        )
        providers.append(_source(commute, "百度地图驾车", "baidu"))
    weather = await _call(
        executor, run, WEATHER, {"city": destination, "start": depart_on, "end": return_on or depart_on}
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
            match = _SELECT.fullmatch(pending.answer)
            if match and (match[1] or (data.get("snapshot") or {}).get("request") == data["request"]):
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
            summary = _confirmed_summary(request)
            notices = []
            for tool_name, requested in (
                (TRANSPORT, request.wants_outbound),
                (HOTELS, request.wants_lodging),
                (COMMUTE, bool(request.venue)),
            ):
                if requested and tool_name in executor.tools.names():
                    note = getattr(executor.tools.get(tool_name), "availability_note", None)
                    if note and note not in notices:
                        notices.append(note)
            if notices:
                summary = "".join(notices) + (
                    "可贴已有班次、报价或链接，我可以按现有资料整理。"
                    + (f"\n\n{summary}" if summary else "")
                )
            await _wait(executor, run, questions, summary)
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
            "卡片列出了已查到的选项、报价来源和还待核对的条件。"
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
