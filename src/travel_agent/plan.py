from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import groupby, product
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

_LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
_TRANSPORT_MODES = ("rail", "flight")
_GROUND_MODES = ("driving", "transit", "walking", "cycling")
_MAX_PLANS = 3

_Offer = dict[str, Any]


class TripRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    transport: Literal["round_trip", "one_way", "none"] | None = None
    lodging: bool | None = None
    origin: str | None = Field(default=None, min_length=1, max_length=80)
    destination: str | None = Field(default=None, min_length=1, max_length=80)
    depart_on: date | None = None
    return_on: date | None = None
    venue: str | None = Field(default=None, min_length=1, max_length=200)
    arrive_by: AwareDatetime | None = None
    travelers: int | None = Field(default=None, ge=1)
    rooms: int | None = Field(default=None, ge=1)
    budget_cny: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    buffer_minutes: int | None = Field(default=None, ge=0)

    @field_validator("arrive_by", mode="before")
    @classmethod
    def parse_arrive_by(cls, value: Any) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        raise ValueError("到场截止时间需包含日期、时间和时区。")


    @property
    def wants_outbound(self) -> bool:
        return self.transport != "none"

    @property
    def wants_return(self) -> bool:
        return self.transport not in {"one_way", "none"}

    @property
    def wants_lodging(self) -> bool:
        if self.lodging is not None:
            return self.lodging
        # Keep old dated itineraries with a room count, without inferring a stay from dates alone.
        return (self.transport == "none" or self.rooms is not None) and self.depart_on != self.return_on


def _zoned(value: Any) -> datetime | None:
    try:
        instant = datetime.fromisoformat(value) if isinstance(value, str) else value
        if isinstance(instant, datetime) and instant.utcoffset() is not None:
            return instant.astimezone(_LOCAL_ZONE)
    except (ValueError, OverflowError):
        pass
    return None


def _date(value: Any) -> date | None:
    if type(value) is date:
        return value
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _offers(offers: list[dict]) -> list[_Offer]:
    unique = {
        offer["id"]: offer
        for offer in offers
        if isinstance(offer, dict) and isinstance(offer.get("id"), str) and offer["id"].strip()
    }
    return list(unique.values())


def _matching_departure(offer: _Offer, day: date | None) -> bool:
    if offer.get("mode") not in _TRANSPORT_MODES:
        return False
    departure = _zoned(offer.get("departs_at"))
    arrival = _zoned(offer.get("arrives_at"))
    if day is not None and arrival is not None and arrival.date() < day:
        return False
    if departure is None:
        return True
    return (day is None or departure.date() == day) and (arrival is None or arrival >= departure)


def _matching_hotel(offer: _Offer, check_in: date | None, check_out: date | None) -> bool:
    return all(
        actual is None or expected is None or actual == expected
        for actual, expected in (
            (_date(offer.get("check_in")), check_in),
            (_date(offer.get("check_out")), check_out),
        )
    )


def _amount(value: Any) -> Decimal | None:
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    if amount.adjusted() > 13 or amount.as_tuple().exponent < -6:
        return None
    return amount


def _quote(
    offer: _Offer,
    *,
    travelers: int | None,
    room_nights: int | None,
) -> tuple[str, Decimal] | None:
    currency = offer.get("currency")
    amount = _amount(offer.get("amount"))
    basis = offer.get("price_basis")
    if not isinstance(currency, str) or not (
        len(currency) == 3 and currency.isascii() and currency.isalpha()
    ):
        return None
    if not isinstance(basis, str):
        return None
    units = {"total": 1, "person": travelers, "night": room_nights}.get(basis)
    if amount is None or units is None:
        return None
    return currency.upper(), amount * units


def _route_minutes(routes: dict[str, dict], offer: _Offer) -> int | None:
    result = routes.get(offer.get("id")) or {}
    if result.get("status") != "ok":
        return None
    items = result.get("items") or []
    # Use the returned primary route, rather than a speed or distance assumption.
    first = next(
        (item for item in items if isinstance(item, dict) and item.get("mode") in _GROUND_MODES),
        {},
    )
    minutes = first.get("duration_minutes")
    return minutes if type(minutes) is int and minutes >= 0 else None


def _check_arrival(
    request: TripRequest,
    arrival: datetime | None,
    minutes: int | None,
    missing: list[str],
) -> tuple[str, str | None]:
    if request.arrive_by is None:
        return "not_requested", None

    required = {"有时区的去程到站时间": arrival}
    if request.venue:
        required["到站后前往到访地点的接驳时间"] = minutes
    if request.venue or request.buffer_minutes is not None:
        required["抵达缓冲时间"] = request.buffer_minutes
    missing.extend(label for label, value in required.items() if value is None)
    if arrival is None:
        return "unconfirmed", None

    seconds_to_deadline = (request.arrive_by - arrival).total_seconds()
    known_minutes = sum(value for value in (minutes, request.buffer_minutes) if value is not None)
    status = "late" if known_minutes * 60 > seconds_to_deadline else "unconfirmed"
    if any(value is None for value in required.values()):
        return status, None
    try:
        estimated = (arrival + timedelta(minutes=known_minutes)).isoformat()
    except OverflowError:
        return status, None
    return ("late" if status == "late" else "met"), estimated


def _check_budget(
    request: TripRequest,
    *,
    cny: Decimal | None,
    complete: bool,
) -> str:
    if request.budget_cny is None:
        return "not_requested"
    if cny is None:
        return "unconfirmed"
    if cny > request.budget_cny:
        return "exceeded"
    return "within" if complete and request.venue is None else "unconfirmed"


def _cost(
    request: TripRequest,
    *,
    outbound: _Offer,
    inbound: _Offer,
    hotel: _Offer,
    check_in: date | None,
    check_out: date | None,
    missing: list[str],
) -> tuple[dict[str, Decimal], bool, dict[str, dict]]:
    totals: dict[str, Decimal] = {}
    cost_items: dict[str, dict] = {}
    nights = (check_out - check_in).days if check_in is not None and check_out is not None else None
    room_required = request.wants_lodging and nights != 0
    hotel_dates_match = nights is not None and (
        _date(hotel.get("check_in")),
        _date(hotel.get("check_out")),
    ) == (check_in, check_out)
    room_nights = None
    if nights is not None and hotel_dates_match and request.rooms is not None:
        room_nights = nights * request.rooms
    legs = []
    if request.wants_outbound:
        legs.append(("outbound", "去程交通", outbound))
    if request.wants_return:
        legs.append(("return", "返程交通", inbound))
    if room_required:
        legs.append(("hotel", "住宿", hotel))
        if hotel and not hotel_dates_match:
            missing.append("与行程一致的住宿日期")
        if request.rooms is None:
            missing.append("房间数")
    if request.travelers is None:
        missing.append("出行人数")

    complete = request.travelers is not None and (
        not room_required or (request.rooms is not None and hotel_dates_match)
    )
    for kind, label, offer in legs:
        if not offer:
            missing.append(f"{label}候选")
            cost_items[kind] = {"status": "no_offer"}
            complete = False
            continue
        dates_unconfirmed = kind == "hotel" and not hotel_dates_match
        quote = _quote(
            offer,
            travelers=request.travelers,
            room_nights=room_nights if kind == "hotel" else None,
        )
        if quote is None or dates_unconfirmed:
            missing.append(f"{label}的可核算报价")
            status = "stay_unconfirmed" if dates_unconfirmed else "price_unconfirmed"
            cost_items[kind] = {"status": status}
            complete = False
            continue
        currency, amount = quote
        totals[currency] = totals.get(currency, Decimal(0)) + amount
        full_price = offer.get("full_price") is True
        cost_items[kind] = {
            "status": "included" if full_price else "partial",
            "amount": format(amount, "f"),
            "currency": currency,
        }
        if not full_price:
            missing.append(f"{label}的完整费用")
            complete = False

    return totals, complete and len(totals) == 1, cost_items


def _make_plan(
    request: TripRequest,
    routes: dict[str, dict],
    outbound: _Offer,
    inbound: _Offer,
    hotel: _Offer,
    arrival: datetime | None,
    check_in: date | None,
    check_out: date | None,
) -> dict:
    offer_ids = [offer.get("id") for offer in (outbound, inbound, hotel)]
    missing: list[str] = []
    warnings: list[str] = []
    totals, complete_price, cost_items = _cost(
        request,
        outbound=outbound,
        inbound=inbound,
        hotel=hotel,
        check_in=check_in,
        check_out=check_out,
        missing=missing,
    )
    currency = next(iter(totals)) if len(totals) == 1 else None
    if len(totals) > 1:
        warnings.append("候选币种不同，分别查看各报价。")

    arrival_commute = _route_minutes(routes, outbound) if request.venue else None
    hotel_commute = _route_minutes(routes, hotel) if request.venue else None
    if request.venue:
        cost_items["ground"] = {"status": "price_unconfirmed"}
        warnings.append("已知报价未含地面接驳，相关费用待核对。")
        if hotel and hotel_commute is None:
            missing.append("酒店至到访地点的接驳时间")
    arrival_status, estimated = _check_arrival(request, arrival, arrival_commute, missing)
    if arrival_status == "late":
        warnings.append("按已有时刻和接驳信息，无足够时间在截止前抵达。")
    budget_status = _check_budget(request, cny=totals.get("CNY"), complete=complete_price)
    if budget_status == "exceeded":
        warnings.append("已知人民币报价小计超过预算。")
    if budget_status == "unconfirmed":
        warnings.append("预算是否满足仍待核算，需核对币种、报价缺项及接驳费用。")

    for label, offer in (("去程", outbound), ("返程", inbound)):
        if not offer:
            continue
        if not (_zoned(offer.get("departs_at")) and _zoned(offer.get("arrives_at"))):
            missing.append(f"{label}有时区的完整时刻")
        if not (offer.get("from_stop") and offer.get("to_stop")):
            missing.append(f"{label}站点或机场")
        if not offer.get("fare_class"):
            missing.append(f"{label}席别或舱位")
    if hotel and not hotel.get("room_type"):
        missing.append("住宿房型")
    if hotel and not hotel.get("address"):
        missing.append("酒店地址")

    fingerprint = hashlib.sha256(json.dumps(offer_ids, ensure_ascii=False).encode()).hexdigest()
    known_costs = {code: format(total, "f") for code, total in sorted(totals.items())}
    return {
        "id": f"trip-{fingerprint[:16]}",
        "transport": offer_ids[0],
        "return_transport": offer_ids[1],
        "hotel": offer_ids[2],
        "known_cost": known_costs[currency] if currency is not None else None,
        "known_costs": known_costs,
        "cost_items": cost_items,
        "currency": currency,
        "complete_price": complete_price,
        "missing": missing,
        "warnings": warnings,
        "commute_minutes": hotel_commute if hotel_commute is not None else arrival_commute,
        "arrival_commute_minutes": arrival_commute,
        "arrival_at_venue": estimated,
        "arrival_status": arrival_status,
        "budget_status": budget_status,
    }


def _plans_for_pair(
    request: TripRequest,
    routes: dict[str, dict],
    outbound: _Offer,
    inbound: _Offer,
    hotels: list[_Offer],
) -> list[dict]:
    arrival = _zoned(outbound.get("arrives_at"))
    return_departure = _zoned(inbound.get("departs_at"))
    if arrival is not None and return_departure is not None and return_departure <= arrival:
        return []
    check_in = request.depart_on if request.transport == "none" or arrival is None else arrival.date()
    check_out = request.return_on if return_departure is None else return_departure.date()
    if check_in is not None and check_out is not None and check_out < check_in:
        return []
    choices = [hotel for hotel in hotels if _matching_hotel(hotel, check_in, check_out)]
    if not request.wants_lodging or (check_in is not None and check_in == check_out):
        choices = []
    return [
        _make_plan(request, routes, outbound, inbound, hotel, arrival, check_in, check_out)
        for hotel in choices or [{}]
        if outbound or inbound or hotel
    ]


def _constraint_rank(plan: dict) -> tuple[int, int]:
    conflicts = (plan["arrival_status"] == "late") + (plan["budget_status"] == "exceeded")
    unknowns = (plan["arrival_status"] == "unconfirmed") + (plan["budget_status"] == "unconfirmed")
    return conflicts, unknowns


def _priority(plan: dict) -> tuple:
    commute = plan["commute_minutes"]
    return (
        *_constraint_rank(plan),
        len(plan["missing"]),
        commute is None,
        commute or 0,
        plan["id"],
    )


def _choose(
    pool: list[dict],
    limit: int,
    modes: dict[str, tuple[str | None, str | None]],
    already_picked: list[dict],
) -> list[dict]:
    seen = {modes[plan["id"]] for plan in already_picked}
    variants = []
    extra = []
    for plan in pool:
        mode_pair = modes[plan["id"]]
        if mode_pair in seen:
            extra.append(plan)
            continue
        seen.add(mode_pair)
        variants.append(plan)
    return sorted(variants + extra, key=_constraint_rank)[:limit]


def _pick(plans: list[dict], modes: dict[str, str]) -> list[dict]:
    ordered = sorted(plans, key=_priority)
    mode_pairs = {
        plan["id"]: (modes.get(plan["transport"]), modes.get(plan["return_transport"]))
        for plan in plans
    }
    picked: list[dict] = []
    for _, group in groupby(ordered, key=lambda plan: _constraint_rank(plan)[0]):
        slots = _MAX_PLANS - len(picked)
        if not slots:
            break
        tier = list(group)
        complete = [plan for plan in tier if plan["complete_price"]]
        partial = [plan for plan in tier if not plan["complete_price"]]
        # Keep a price-incomplete alternative within the same conflict tier when space allows.
        reserve_partial = slots > 1 and bool(partial)
        picked += _choose(complete, slots - reserve_partial, mode_pairs, picked)
        picked += _choose(partial, _MAX_PLANS - len(picked), mode_pairs, picked)
    return [dict(plan, label=f"方案 {index}") for index, plan in enumerate(picked, 1)]


def build_travel_plan(
    request: TripRequest,
    *,
    outbound: list[dict],
    inbound: list[dict],
    hotels: list[dict],
    commute_by_offer: dict[str, dict],
) -> dict:
    if (request.wants_return or request.wants_lodging) and request.depart_on and request.return_on and request.return_on < request.depart_on:
        return {"plans": [], "issues": ["返程日期早于出发日期，请核对行程。"]}

    outbound_choices = [
        offer for offer in _offers(outbound) if request.wants_outbound and _matching_departure(offer, request.depart_on)
    ]
    inbound_choices = [
        offer for offer in _offers(inbound) if request.wants_return and _matching_departure(offer, request.return_on)
    ]
    hotel_choices = _offers(hotels)
    plans: list[dict] = []
    for out, back in product(outbound_choices or [{}], inbound_choices or [{}]):
        plans.extend(_plans_for_pair(request, commute_by_offer, out, back, hotel_choices))

    if not plans:
        return {"plans": [], "issues": ["暂未取得可按本次日期和顺序组合的行程候选。"]}
    modes = {offer["id"]: offer["mode"] for offer in outbound_choices + inbound_choices}
    return {"plans": _pick(plans, modes), "issues": []}
