from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from itertools import product

from src.procurement_agent.evidence import item_key
from src.procurement_agent.models import (
    Need,
    PriceBasis,
    Quote,
    Record,
    RecordKind,
    RefundState,
    Tier,
)

_FEES = {
    "shipping_cny": "运费",
    "tax_cny": "税费",
    "fees_cny": "附加费用",
    "discount_cny": "本次数量适用的优惠",
}
_MAX_OPTIONS = 3
_ZERO = Decimal("0.00")


def money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def required_quantity(need: Need, people: int | None) -> int | None:
    if need.quantity is not None:
        return need.quantity
    return need.per_person * people if need.per_person and people else None


def purchase_quantity(need: Need, quote: Quote, people: int | None) -> int | None:
    quantity = required_quantity(need, people)
    if not (
        quantity
        and quote.pack_size
        and quote.minimum_quantity
        and need.unit
        and quote.unit
        and item_key(need.unit) == item_key(quote.unit)
    ):
        return None
    multiple = quote.pack_size
    return ((max(quantity, quote.minimum_quantity) + multiple - 1) // multiple) * multiple


def _tier_at(tiers: list[Tier], quantity: int) -> Tier | None:
    applicable = [
        tier
        for tier in tiers
        if tier.minimum <= quantity and (tier.maximum is None or quantity <= tier.maximum)
    ]
    if not applicable:
        return None
    threshold = max(tier.minimum for tier in applicable)
    highest = [tier for tier in applicable if tier.minimum == threshold]
    if len({(tier.price_cny, tier.basis) for tier in highest}) != 1:
        return None
    return highest[0]


def _same_specification(need: Need, quote: Quote) -> bool:
    return bool(
        need.specification
        and quote.specification
        and item_key(need.specification) == item_key(quote.specification)
    )


def _charges(quote: Quote, actual_quantity: int | None) -> dict[str, Decimal | None]:
    if actual_quantity is None or quote.charges_for_quantity not in (None, actual_quantity):
        return dict.fromkeys(_FEES)
    return {field: getattr(quote, field) for field in _FEES}


def calculate_quote(
    need: Need,
    quote: Quote,
    *,
    people: int | None,
    today: date,
    needed_by: date | None,
) -> dict:
    quantity = required_quantity(need, people)
    issues = []
    matched = _same_specification(need, quote)
    if not matched:
        issues.append(
            "报价规格与需求不同"
            if need.specification and quote.specification
            else "核对相同型号与规格"
        )
    if not need.unit or not quote.unit or item_key(need.unit) != item_key(quote.unit):
        issues.append("核对相同的计量单位")
    if quantity is None:
        issues.append("确认需求数量")
    if quote.pack_size is None:
        issues.append("确认每包数量")
    if quote.minimum_quantity is None:
        issues.append("确认起订量")
    actual = purchase_quantity(need, quote, people) if matched else None
    expired = bool(quote.valid_until and quote.valid_until < today)
    if expired:
        issues.append("报价有效期已过，请更新")
    tier = _tier_at(quote.tiers, actual) if actual and not expired else None
    if not tier:
        issues.append("核对该规格与实买数量适用的价格")
    goods = None
    if tier is not None and actual is not None and quote.pack_size is not None:
        price_count = actual // quote.pack_size if tier.basis == PriceBasis.PACK else actual
        goods = tier.price_cny * price_count
    fees = _charges(quote, actual)
    if expired:
        fees = dict.fromkeys(_FEES)
    discount = fees["discount_cny"]
    if goods is not None and discount is not None and discount > goods:
        fees["discount_cny"] = None
        issues.append("优惠金额超过商品金额，请核对")
    issues.extend(f"确认{label}" for field, label in _FEES.items() if fees[field] is None)
    subtotal = None
    if goods is not None:
        extra = sum((fees[field] or _ZERO for field in _FEES if field != "discount_cny"), _ZERO)
        subtotal = goods + extra - (fees["discount_cny"] or _ZERO)
    complete = subtotal is not None and all(amount is not None for amount in fees.values())
    delivery = "not_requested"
    if needed_by:
        if not quote.arrival_date or not quote.arrival_confirmed or expired:
            delivery = "unconfirmed"
            issues.append("确认能否在要求日期前送达")
        else:
            delivery = "late" if quote.arrival_date > needed_by else "met"
            if delivery == "late":
                issues.append("承诺到货日晚于使用期限")
    return {
        "item": need.item,
        "supplier": quote.supplier,
        "specification": quote.specification,
        "unit": need.unit,
        "quote_unit": quote.unit,
        "needed_quantity": quantity,
        "actual_quantity": actual,
        "extra_quantity": actual - quantity
        if actual is not None and quantity is not None
        else None,
        "pack_size": quote.pack_size,
        "minimum_quantity": quote.minimum_quantity,
        "price_cny": money(tier.price_cny) if tier else None,
        "price_basis": tier.basis.value if tier else None,
        "price_minimum": tier.minimum if tier else None,
        "goods_cny": money(goods),
        "known_subtotal_cny": money(subtotal),
        "total_cny": money(subtotal) if complete else None,
        "fees": {key: money(amount) for key, amount in fees.items()},
        "delivery": delivery,
        "arrival_date": quote.arrival_date.isoformat() if quote.arrival_date else None,
        "arrival_confirmed": quote.arrival_confirmed,
        "delivery_terms": quote.delivery_terms,
        "quoted_on": quote.quoted_on.isoformat() if quote.quoted_on else None,
        "url": quote.url,
        "evidence": quote.evidence,
        "issues": issues,
    }


def _quote_rank(row: dict) -> tuple:
    total = row["total_cny"]
    return (
        row["delivery"] == "late",
        row["goods_cny"] is None,
        row["delivery"] == "unconfirmed",
        total is None,
        Decimal(total) if total is not None else _ZERO,
        row["extra_quantity"] or 0,
        row["supplier"],
    )


def _budget_status(total: Decimal | None, budget: Decimal | None) -> str:
    if budget is None:
        return "not_requested"
    if total is None:
        return "unconfirmed"
    return "within" if total <= budget else "exceeded"


def _combinations(groups: list[list[dict]], budget: Decimal | None) -> list[dict]:
    choices = [
        [row for row in group if row["goods_cny"] is not None and row["delivery"] != "late"][
            :_MAX_OPTIONS
        ]
        for group in groups
    ]
    if not choices or not all(choices):
        return []
    combinations = []
    for selection in product(*choices):
        complete = all(row["total_cny"] is not None for row in selection)
        known = sum((Decimal(row["known_subtotal_cny"]) for row in selection), _ZERO)
        delivery_gaps = sum(row["delivery"] == "unconfirmed" for row in selection)
        ranking = (delivery_gaps, not complete, known if complete else _ZERO)
        combinations.append(
            (
                ranking,
                {
                    "lines": [
                        {
                            "item": row["item"],
                            "supplier": row["supplier"],
                            "quantity": row["actual_quantity"],
                        }
                        for row in selection
                    ],
                    "known_subtotal_cny": money(known),
                    "total_cny": money(known) if complete else None,
                    "budget_status": _budget_status(known if complete else None, budget),
                    "delivery_gaps": delivery_gaps,
                },
            )
        )
    return [item[1] for item in sorted(combinations, key=lambda pair: pair[0])[:_MAX_OPTIONS]]


def compare(
    needs: list[Need],
    quotes: list[Quote],
    *,
    people: int | None,
    budget: Decimal | None,
    today: date,
    needed_by: date | None,
    build_list: bool,
) -> dict:
    indexed: dict[str, list[Quote]] = defaultdict(list)
    for quote in quotes:
        indexed[item_key(quote.item)].append(quote)
    groups = [
        sorted(
            (
                calculate_quote(need, quote, people=people, today=today, needed_by=needed_by)
                for quote in indexed[item_key(need.item)]
            ),
            key=_quote_rank,
        )
        for need in needs
    ]
    lines = [
        {
            "item": need.item,
            "specification": need.specification,
            "unit": need.unit,
            "quantity": required_quantity(need, people),
            "quantity_estimated": need.quantity is None and bool(people and need.per_person),
            "people": people,
            "per_person": need.per_person,
        }
        for need in needs
    ]
    rows = [row for group in groups for row in group]
    if not build_list and len(needs) == 1:
        for row in rows:
            total = row["total_cny"]
            row["budget_status"] = _budget_status(
                Decimal(total) if total is not None else None, budget
            )
    return {
        "lines": lines,
        "quotes": rows,
        "plans": _combinations(groups, budget) if build_list else [],
        "budget_cny": money(budget),
        "needed_by": needed_by.isoformat() if needed_by else None,
    }


def _sum_if_distinct(records: list[Record], field: str):
    """Calculate once for a document. Conflicting or unidentified extra values need review."""
    explicit = defaultdict(set)
    anonymous = set()
    for record in records:
        value = getattr(record, field)
        if value is None:
            return None
        if record.document_ref:
            explicit[record.document_ref].add(value)
        else:
            anonymous.add(value)
    if any(len(values) != 1 for values in explicit.values()) or len(anonymous) > 1:
        return None
    identified = [next(iter(values)) for values in explicit.values()]
    if identified:
        return sum(identified) if anonymous.issubset(identified) else None
    return next(iter(anonymous), None)


def _quantities(orders: list[Record], deliveries: list[Record]) -> list[dict]:
    keys = dict.fromkeys(record.item for record in [*orders, *deliveries])
    lines = []
    for item in keys:
        planned = [record for record in orders if record.item == item]
        arrived = [record for record in deliveries if record.item == item]
        expected = _sum_if_distinct(planned, "quantity")
        actual = _sum_if_distinct(arrived, "quantity")
        reference_specs = {
            item_key(record.specification) for record in planned if record.specification
        }
        actual_specs = {
            item_key(record.specification) for record in arrived if record.specification
        }
        spec = "unconfirmed"
        if reference_specs and actual_specs:
            spec = (
                "matched"
                if len(reference_specs) == 1 and reference_specs == actual_specs
                else "different"
            )
        lines.append(
            {
                "item": item,
                "expected": expected,
                "received": actual,
                "difference": actual - expected
                if expected is not None and actual is not None
                else None,
                "specification_status": spec,
            }
        )
    return lines


def reconcile(records: list[Record]) -> dict:
    orders: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        orders[record.reference].append(record)
    reconciled = []
    for reference, materials in orders.items():
        by_kind = {
            kind: [record for record in materials if record.kind == kind] for kind in RecordKind
        }
        paid = _sum_if_distinct(by_kind[RecordKind.PAYMENT], "amount_cny")
        refunded = [
            row for row in by_kind[RecordKind.REFUND] if row.refund_state == RefundState.RECEIVED
        ]
        cash_back = _sum_if_distinct(refunded, "amount_cny") if refunded else _ZERO
        settled_ids = {row.document_ref for row in refunded if row.document_ref}
        pending_refunds = sum(
            row.refund_state != RefundState.RECEIVED and row.document_ref not in settled_ids
            for row in by_kind[RecordKind.REFUND]
        )
        issues = []
        if paid is None:
            issues.append("补充或核对付款金额与凭证编号")
        if cash_back is None or (paid is not None and cash_back > paid):
            issues.append("核对已到账退款与付款金额")
        net = (
            paid - cash_back
            if paid is not None and cash_back is not None and cash_back <= paid
            else None
        )
        quantities = _quantities(by_kind[RecordKind.ORDER], by_kind[RecordKind.DELIVERY])
        if not quantities or any(line["difference"] is None for line in quantities):
            issues.append("补充可对应的订单商品与实收数量")
        reconciled.append(
            {
                "reference": reference,
                "paid_cny": money(paid),
                "refunded_cny": money(cash_back),
                "net_paid_cny": money(net),
                "pending_refunds": pending_refunds,
                "lines": quantities,
                "evidence": list(dict.fromkeys(record.evidence for record in materials)),
                "issues": issues,
            }
        )
    return {"orders": reconciled}
