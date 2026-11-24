from __future__ import annotations

import csv
import re
from collections.abc import Collection
from datetime import date
from decimal import Decimal
from unicodedata import normalize
from urllib.parse import urlsplit

from src.procurement_agent.models import PriceBasis, Quote, Record, RecordKind, RefundState, Tier

_NUMBER = re.compile(r"(?<![\d.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d.])")
_FOREIGN_CURRENCY = re.compile(r"\b(?:USD|EUR|HKD|JPY|GBP)\b|美元|欧元|港币|日元|英镑", re.I)
_CONDITIONAL_FEE = re.compile(r"满|超过|达到|以上|以下|仅限|估算|预计|待确认")
_PRICE_TERMS = {
    "shipping_cny": (
        r"运费|邮费|配送费",
        r"(?<!不)包邮|(?<!不)免运费|(?<!不)免邮|(?<!不)(?<!未)含运费",
    ),
    "tax_cny": (r"税费|税额|税|开票费", r"(?<!不)(?<!未)含税|税已包含"),
    "fees_cny": (
        r"附加费(?:用)?|其他费(?:用)?|服务费|杂费",
        r"无(?:其他|附加|额外)费|没有(?:其他|附加)费",
    ),
    "discount_cny": (r"优惠|折扣|立减", r"无优惠|没有优惠|无折扣"),
}
_RECORD_WORDS = {
    RecordKind.ORDER: r"订单|应到|订购|应收",
    RecordKind.PAYMENT: r"付款|支付|已付|实付|打款",
    RecordKind.INVOICE: r"发票|已开票",
    RecordKind.DELIVERY: r"到货|实收|收货|实到|签收",
    RecordKind.REFUND: r"退款|已退|退回",
}


def item_key(value: str) -> str:
    return " ".join(normalize("NFKC", value).casefold().split())


def safe_link(value: str | None) -> str | None:
    if not value or "\\" in value or any(char.isspace() or ord(char) < 32 for char in value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme in {"https", "http"} and url.hostname and url.username is None:
            return value
    except ValueError:
        pass
    return None


def _numbers(evidence: str) -> set[Decimal]:
    return {
        Decimal(value.replace(",", "")) for value in _NUMBER.findall(normalize("NFKC", evidence))
    }


def _has_date(value: date | None, text: str) -> bool:
    return bool(
        value
        and (
            value.isoformat() in text
            or re.search(rf"(?<!\d)0?{value.month}月0?{value.day}[日号]", text)
        )
    )


def _table_amount(quote: Quote, label: str, amount: Decimal) -> bool:
    lines = quote.evidence.splitlines()
    if len(lines) < 2:
        return False
    separator = "|" if "|" in lines[0] else ","
    headers, *rows = csv.reader(lines, delimiter=separator)
    fields = [index for index, header in enumerate(headers) if re.search(label, header)]
    return any(
        index < len(row) and amount in _numbers(row[index])
        for row in rows
        if quote.supplier in " ".join(row)
        and quote.item in " ".join(row)
        and (not quote.specification or quote.specification in " ".join(row))
        for index in fields
    )


def _labeled_amount(text: str, label: str, amount: Decimal) -> bool:
    for token in _NUMBER.finditer(text):
        if Decimal(token[0].replace(",", "")) != amount:
            continue
        prefix = text[max(0, token.start() - 25) : token.start()]
        if re.search(rf"(?:{label})(?:\s|[:：=为是¥￥]|人民币)*$", prefix):
            return True
    return False


def _tier_supported(tier: Tier, quote: Quote) -> bool:
    evidence = tier.evidence or quote.evidence
    if evidence not in quote.evidence or re.search(r"预估|券后|到手价|最低价", evidence):
        return False
    unit = "包" if tier.basis == PriceBasis.PACK else quote.unit
    if not unit:
        return False
    unit_pattern = re.escape(unit)
    label = f"每{unit_pattern}|{unit_pattern}价"
    label += "|整包价|整包" if tier.basis == PriceBasis.PACK else "|单价"
    supported = _labeled_amount(evidence, label, tier.price_cny) or any(
        Decimal(match[0].replace(",", "")) == tier.price_cny
        and re.match(rf"\s*元\s*(?:/|／|一){unit_pattern}", evidence[match.end() :])
        for match in _NUMBER.finditer(evidence)
    )
    if not tier.evidence:
        return (
            len(quote.tiers) == 1
            and tier.minimum == 1
            and tier.maximum is None
            and (supported or _table_amount(quote, label, tier.price_cny))
        )
    terms = _numbers(evidence)
    return (
        (supported or (len(quote.tiers) == 1 and _table_amount(quote, label, tier.price_cny)))
        and (tier.minimum == 1 or tier.minimum in terms)
        and (tier.maximum is None or tier.maximum in terms)
    )


def _known_fee(field: str, quote: Quote, numbers: set[Decimal]) -> Decimal | None:
    amount = getattr(quote, field)
    terms, zero_terms = _PRICE_TERMS[field]
    if amount is None:
        return None
    clauses = re.split(r"[，。；;\n]", quote.evidence)
    confirmed = any(
        not _CONDITIONAL_FEE.search(clause)
        and (
            _labeled_amount(clause, terms, amount)
            or (amount == 0 and re.search(zero_terms, clause))
        )
        for clause in clauses
    )
    if not confirmed and not _table_amount(quote, terms, amount):
        return None
    if (
        field == "discount_cny"
        and (
            quote.charges_for_quantity not in numbers
            or not re.search(r"已确认|本单已享|实际优惠", quote.evidence)
        )
        and amount > 0
    ):
        return None
    return amount


def grounded_quote(quote: Quote, text: str) -> Quote | None:
    source = quote.evidence
    if source not in text or quote.supplier not in source or quote.item not in source:
        return None
    numbers = _numbers(source)
    cny = not _FOREIGN_CURRENCY.search(source)
    updates = {
        field: value if value in numbers else None
        for field in ("pack_size", "minimum_quantity", "charges_for_quantity")
        if (value := getattr(quote, field)) is not None
    }
    updates.update(
        {
            field: value if value in source else None
            for field in ("specification", "unit", "delivery_terms")
            if (value := getattr(quote, field)) is not None
        }
    )
    updates.update(
        {field: _known_fee(field, quote, numbers) if cny else None for field in _PRICE_TERMS}
    )
    arrival_terms = [
        clause
        for clause in re.split(r"[，。；;\n]", source)
        if re.search(r"到货|送达|交付", clause) and _has_date(quote.arrival_date, clause)
    ]
    updates.update(
        tiers=[tier for tier in quote.tiers if cny and _tier_supported(tier, quote)],
        url=safe_link(quote.url) if quote.url and quote.url in source else None,
        arrival_date=quote.arrival_date if arrival_terms else None,
        arrival_confirmed=quote.arrival_confirmed
        and any(
            re.search(r"承诺|保证|最迟|确认", clause)
            and not re.search(r"预计|预估|无法|不保证|未确认", clause)
            for clause in arrival_terms
        ),
        valid_until=quote.valid_until if _has_date(quote.valid_until, source) else None,
        quoted_on=quote.quoted_on if _has_date(quote.quoted_on, source) else None,
    )
    return quote.model_copy(update=updates)


def _contains_reference(source: str, value: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z0-9_-]){re.escape(value)}(?![A-Za-z0-9_-])", source))


def grounded_record(
    record: Record,
    text: str,
    order_references: Collection[str] = (),
) -> Record | None:
    source = record.evidence
    if source not in text or not re.search(_RECORD_WORDS[record.kind], source):
        return None
    matching_orders = [ref for ref in order_references if _contains_reference(source, ref)]
    if len(matching_orders) > 1:
        return None
    reference = matching_orders[0] if matching_orders else record.reference
    if not _contains_reference(source, reference) or (
        record.kind != RecordKind.ORDER and reference == record.document_ref
    ):
        return None
    numbers = _numbers(source)
    updates = {
        field: value if value in source else None
        for field in ("document_ref", "item", "specification")
        if (value := getattr(record, field)) is not None
    }
    refunded = re.search(r"到账|退款成功|已退回", source) and not re.search(
        r"未到账|还没到账|申请中|待到账|未退|处理中", source
    )
    updates.update(
        reference=reference,
        quantity=record.quantity if record.quantity in numbers else None,
        amount_cny=record.amount_cny
        if (record.amount_cny in numbers and not _FOREIGN_CURRENCY.search(source))
        else None,
        refund_state=(
            RefundState.RECEIVED
            if record.kind == RecordKind.REFUND
            and record.refund_state == RefundState.RECEIVED
            and refunded
            else None
        ),
    )
    return record.model_copy(update=updates)
