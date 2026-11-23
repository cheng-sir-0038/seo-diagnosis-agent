from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from src.travel_agent.plan import TripRequest

TRAVEL_DOMAIN = "travel"
TRAVEL_TIMEZONE = "Asia/Shanghai"
TRAVEL_INTAKE_PROMPT_KEY = "workspace.travel.intake"

TRAVEL_INTAKE_PROMPT = (
    "抽取用户这次国内出差的已确认条件和主动提供的报价。"
    "origin 和 destination 填城市，venue 填实际到访地点或地址。"
    "depart_on 和 return_on 为 ISO 日期。相对日期以 current_date（中国时区）为准。"
    "arrive_by 仅填用户给出的最晚到访日期和时间，使用带 +08:00 的 ISO 日期时间。"
    "人数、房间数、CNY 总预算和接驳缓冲以用户给出的数字为准。每晚预算不能当成总预算。"
    "用户提供报价时，quote 必须逐字截取支持该候选的原话。"
    "保留其中明确写出的票价口径、日期、班次/车站、酒店房型和商品链接。"
    "明确包含完整费用时才设置 full_price。旅游服务商只用用户原话中明确提到的名称。"
    "supplied_offers 用于用户提供的候选。不要自己生成交通、酒店、报价、链接或可订状态。"
    "conditions_to_verify 仅摘录当前无法核对的附加要求，如早餐、退改、行李、每晚预算。"
    "已写入 request 的条件不要重复写入。"
    "修改计划时只输出用户这次更新的字段，把其他请求字段留空。"
)


class OfferKind(StrEnum):
    OUTBOUND = "outbound"
    RETURN = "return"
    HOTEL = "hotel"


class PriceBasis(StrEnum):
    TOTAL = "total"
    PERSON = "person"
    NIGHT = "night"


class SuppliedOffer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: OfferKind
    name: str = Field(min_length=1, max_length=160)
    quote: str = Field(min_length=1, max_length=700)
    source_label: str | None = Field(default=None, max_length=50)
    amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: Literal["CNY", "USD", "EUR", "HKD", "JPY"] | None = None
    price_basis: PriceBasis | None = None
    full_price: bool = False
    booking_url: str | None = Field(default=None, max_length=600)
    mode: Literal["rail", "flight"] | None = None
    from_stop: str | None = Field(default=None, max_length=100)
    to_stop: str | None = Field(default=None, max_length=100)
    departs_at: AwareDatetime | None = None
    arrives_at: AwareDatetime | None = None
    fare_class: str | None = Field(default=None, max_length=70)
    address: str | None = Field(default=None, max_length=200)
    check_in: date | None = None
    check_out: date | None = None
    room_type: str | None = Field(default=None, max_length=70)
    terms: str | None = Field(default=None, max_length=150)
    quoted_at: AwareDatetime | None = None


class TravelIntake(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: TripRequest = Field(default_factory=TripRequest)
    supplied_offers: list[SuppliedOffer] = Field(default_factory=list, max_length=9)
    conditions_to_verify: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="逐字摘录需人工核对的条件，例如每晚预算、席别、早餐、退改或行李",
    )


def china_today() -> date:
    return datetime.now(ZoneInfo(TRAVEL_TIMEZONE)).date()


_PRICE = re.compile(
    r"([¥￥]|CNY\b|RMB\b|USD\b|EUR\b|HKD\b|JPY\b)\s*"
    r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)|"
    r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(元|美元|欧元|港币|日元)",
    re.I,
)
_BASIS_MARKERS = {
    PriceBasis.PERSON: r"每人|单人|/人|／人|人均",
    PriceBasis.NIGHT: r"每晚|单晚|/晚|／晚",
    PriceBasis.TOTAL: r"总价|合计|共计|共[\s]*[¥￥0-9]|实付",
}
_COMPLETE_PRICE = re.compile(r"含税费|含税及服务费|含全部费用|包含全部费用|实付总额")
_EXCLUDED_FEES = re.compile(r"(?:不|未)含|(?:不|未)包含|另计|另收")
_CURRENCY = {
    "¥": "CNY",
    "￥": "CNY",
    "RMB": "CNY",
    "元": "CNY",
    "美元": "USD",
    "欧元": "EUR",
    "港币": "HKD",
    "日元": "JPY",
}


def _source_link(url: str | None, quote: str) -> str | None:
    if not url or url not in quote:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return (
        url
        if (parts.scheme in {"http", "https"} and parts.hostname and parts.username is None)
        else None
    )


def _priced_amount(offer: SuppliedOffer) -> tuple[str | None, str | None]:
    currencies = {
        _CURRENCY.get(marker, marker)
        for prefix, value, prefixed_value, suffix in _PRICE.findall(offer.quote)
        if Decimal((value or prefixed_value).replace(",", "")) == offer.amount
        and (marker := (prefix or suffix).upper())
    }
    if not currencies:
        return None, None
    currency = next(iter(currencies)) if len(currencies) == 1 else None
    return str(offer.amount), currency


def supplied_offers(intake: TravelIntake, text: str) -> list[dict]:
    results = []
    for offer in intake.supplied_offers:
        if offer.quote not in text or offer.name not in offer.quote:
            continue
        record = offer.model_dump(mode="json", exclude={"kind", "quoted_at"})
        for field in ("from_stop", "to_stop", "address", "fare_class", "room_type", "terms"):
            value = record[field]
            if value and value not in offer.quote:
                record[field] = None
        amount, currency = _priced_amount(offer)
        basis = offer.price_basis
        if not amount or not basis or not re.search(_BASIS_MARKERS[basis], offer.quote):
            basis = None
        record.update(
            id=f"user-{hashlib.sha256(offer.quote.encode()).hexdigest()[:16]}",
            kind="hotel" if offer.kind == OfferKind.HOTEL else "transport",
            direction=offer.kind.value,
            source="user",
            source_label=(
                offer.source_label
                if offer.source_label and offer.source_label in offer.quote
                else None
            ),
            amount=amount,
            currency=currency,
            price_basis=basis,
            full_price=bool(
                basis
                and offer.full_price
                and _COMPLETE_PRICE.search(offer.quote)
                and not _EXCLUDED_FEES.search(offer.quote)
            ),
            booking_url=_source_link(offer.booking_url, offer.quote),
            queried_at=offer.quoted_at.isoformat() if offer.quoted_at else None,
        )
        results.append(record)
    return results
