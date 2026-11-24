from __future__ import annotations

import hmac
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from src.procurement_agent.calculations import calculate_quote, compare, reconcile
from src.procurement_agent.evidence import grounded_quote, grounded_record
from src.procurement_agent.models import Need, Quote, Record
from src.procurement_agent.taobao import GATEWAY, TaobaoAffiliate

TODAY = date(2026, 11, 23)


def quote(supplier: str, **changes) -> Quote:
    return Quote.model_validate(
        {
            "item": "纸杯",
            "supplier": supplier,
            "specification": "200ml",
            "unit": "个",
            "pack_size": 1,
            "minimum_quantity": 1,
            "tiers": [{"minimum": 1, "price_cny": "14", "basis": "unit"}],
            "shipping_cny": "60",
            "tax_cny": "0",
            "fees_cny": "0",
            "discount_cny": "0",
            "evidence": supplier + "，纸杯200ml",
            **changes,
        }
    )


def test_same_sku_minimum_purchase_tier_and_unknown_freight():
    need = Need(item="纸杯", specification="200ml", unit="个", quantity=60)
    result = compare(
        [need],
        [
            quote("甲商家"),
            quote(
                "乙商家",
                pack_size=10,
                minimum_quantity=100,
                shipping_cny="80",
                tiers=[
                    {"minimum": 100, "price_cny": "12", "basis": "unit"},
                ],
            ),
            quote("丙商家", shipping_cny=None),
        ],
        today=TODAY,
        people=None,
        budget=Decimal("1000"),
        needed_by=None,
        build_list=False,
    )
    a, b, c = result["quotes"]
    assert a["total_cny"] == "900.00"
    assert a["budget_status"] == "within"
    assert (b["actual_quantity"], b["extra_quantity"], b["total_cny"]) == (100, 40, "1280.00")
    assert b["budget_status"] == "exceeded"
    assert c["known_subtotal_cny"] == "840.00" and c["total_cny"] is None
    assert c["budget_status"] == "unconfirmed" and "确认运费" in c["issues"]


@pytest.mark.parametrize(
    "changes",
    [
        {"specification": "300ml"},
        {"unit": "包"},
        {"pack_size": None},
        {"valid_until": date(2026, 11, 22)},
    ],
)
def test_requires_matching_sku_pack_units_and_current_quote(changes):
    need = Need(item="纸杯", specification="200ml", unit="个", quantity=60)
    row = calculate_quote(
        need, quote("供应商", **changes), people=None, today=TODAY, needed_by=None
    )
    assert row["total_cny"] is None and row["goods_cny"] is None


def test_build_list_rounds_packages_and_flags_estimated_quantity():
    need = Need(item="纸杯", specification="200ml", unit="个", per_person=2)
    supplier = quote(
        "商家",
        pack_size=25,
        shipping_cny="0",
        tiers=[
            {"minimum": 1, "price_cny": "30.50", "basis": "pack"},
        ],
        arrival_date=date(2026, 11, 24),
        arrival_confirmed=False,
    )
    result = compare(
        [need],
        [supplier],
        today=TODAY,
        people=20,
        budget=None,
        needed_by=date(2026, 11, 25),
        build_list=True,
    )
    assert result["lines"][0]["quantity"] == 40 and result["lines"][0]["quantity_estimated"]
    assert result["quotes"][0]["actual_quantity"] == 50
    assert result["quotes"][0]["total_cny"] == "61.00"
    assert result["plans"][0]["delivery_gaps"] == 1


def test_ineligible_coupon_and_unverified_freight_are_not_filled():
    text = "甲商家纸杯 200ml，按个售，1个装，1个起订，单价14元，运费不包邮，券满100减20元"
    item = grounded_quote(
        quote(
            "甲商家",
            evidence=text,
            shipping_cny="0",
            discount_cny="20",
            url="javascript:alert(1)",
        ),
        text,
    )
    assert item is not None
    assert item.shipping_cny is None and item.discount_cny is None and item.url is None
    assert item.tiers[0].price_cny == 14
    assert grounded_quote(quote("伪造商家"), text) is None


def test_grounded_quote_checks_table_columns_and_does_not_use_conditional_freight():
    table = (
        "供应商,品名,规格,单位,每包,起订,单价,运费,税费,附加费用,优惠\n"
        "甲商家,纸杯,200ml,个,1,1,14,60,0,0,0"
    )
    row = grounded_quote(quote("甲商家", evidence=table), table)
    assert row and row.tiers[0].price_cny == 14 and row.shipping_cny == 60

    text = "甲商家纸杯200ml，1个装，1个起订，单价14元，满1000元包邮"
    row = grounded_quote(quote("甲商家", shipping_cny=0, evidence=text), text)
    assert row and row.shipping_cny is None
    wrong = grounded_quote(
        quote(
            "甲商家",
            evidence=table,
            tiers=[
                {"price_cny": "60", "basis": "unit"},
            ],
        ),
        table,
    )
    assert wrong and wrong.tiers == []


def item_record(kind: str, **values) -> Record:
    return Record.model_validate(
        {"reference": "ORDER-327", "kind": kind, "evidence": "订单资料", **values}
    )


def test_reconciliation_uses_distinct_payments_and_only_received_refunds():
    payment = item_record("payment", document_ref="P1", amount_cny="900")
    result = reconcile(
        [
            item_record("order", item="纸杯", specification="200ml", quantity=60, amount_cny="900"),
            item_record("delivery", item="纸杯", specification="250ml", quantity=58),
            payment,
            payment,
            item_record("invoice", amount_cny="900"),
            item_record("refund", document_ref="R1", refund_state="received", amount_cny="12"),
            item_record("refund", document_ref="R2", refund_state="pending", amount_cny="50"),
        ]
    )["orders"][0]
    assert result["paid_cny"] == "900.00" and result["net_paid_cny"] == "888.00"
    assert result["pending_refunds"] == 1
    assert result["lines"][0]["difference"] == -2
    assert result["lines"][0]["specification_status"] == "different"
    conflicting = item_record("payment", document_ref="P1", amount_cny="850")
    assert reconcile([payment, conflicting])["orders"][0]["net_paid_cny"] is None


def test_pending_refund_stays_pending_even_if_model_extraction_is_wrong():
    text = "订单 ORDER-327 申请退款 50 元，仍未到账。"
    row = grounded_record(
        item_record(
            "refund",
            refund_state="received",
            amount_cny="50",
            evidence=text,
        ),
        text,
    )
    assert row and row.refund_state is None


def _affiliate(key="sample-app", secret="sample-secret", adzone="1234") -> TaobaoAffiliate:
    return TaobaoAffiliate(SecretStr(key), SecretStr(secret), adzone)


@pytest.mark.asyncio
async def test_taobao_signing_and_documented_catalog_envelopes(monkeypatch):
    seen = []

    async def handle(_transport, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == GATEWAY and request.method == "POST"
        signed = {
            key: values[0] for key, values in parse_qs((await request.aread()).decode()).items()
        }
        signature = signed.pop("sign")
        documented = "".join(field + signed[field] for field in sorted(signed))
        assert (
            hmac.new(b"sample-secret", documented.encode(), "md5").hexdigest().upper() == signature
        )
        assert signed["sign_method"] == "hmac" and signed["v"] == "2.0" and "session" not in signed
        assert "sample-secret" not in (await request.aread()).decode()
        method = signed["method"]
        seen.append(signed)
        item = {
            "item_id": "34112",
            "item_basic_info": {"title": "无图案纸杯", "user_type": 1, "shop_title": "店铺"},
            "price_promotion_info": {"reserve_price": "30", "final_promotion_price": "19.9"},
            "publish_info": {"click_url": "//s.click.taobao.com/item-demo"},
        }
        if method == "taobao.tbk.dg.material.optional.upgrade":
            response = {"total_results": 1, "result_list": {"map_data": [item]}}
        elif method == "taobao.tbk.item.info.upgrade.get":
            response = {"results": {"tbk_item_detail": [item]}}
        else:
            response = {"data": {"coupon_amount": "10", "coupon_start_fee": "50"}}
        return httpx.Response(
            200, json={method.removeprefix("taobao.").replace(".", "_") + "_response": response}
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    client = _affiliate()
    result = await client.search("纸杯 200ml", tmall_only=True)
    details = await client.details("34112")
    coupon = await client.coupon("34112", "coupon-1")

    assert (seen[0]["adzone_id"], seen[0]["q"], seen[0]["is_tmall"]) == (
        "1234",
        "纸杯 200ml",
        "true",
    )
    assert set(seen[1]) - set(seen[0]) == {"item_id"}
    assert seen[2]["item_id"] == "34112" and seen[2]["activity_id"] == "coupon-1"
    assert details["items"][0] == result["items"][0]
    assert result["items"][0]["estimated_price_cny"] == "19.90"
    assert result["items"][0]["sku_verified"] is False
    assert result["items"][0]["url"] == "https://s.click.taobao.com/item-demo"
    assert coupon["items"][0]["minimum_spend_cny"] == "50.00"
    assert all(result["fetched_at"] for result in (result, details, coupon))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "status"),
    [
        (
            {"error_response": {"code": 11, "sub_msg": "private vendor message"}},
            "permission_denied",
        ),
        ({"error_response": {"code": 7}}, "rate_limited"),
        ({"tbk_dg_material_optional_upgrade_response": {"total_results": 0}}, "no_results"),
        ({"unexpected": {}}, "invalid_response"),
        (None, "network_error"),
    ],
)
async def test_taobao_failure_and_empty_receipts_keep_their_meaning(monkeypatch, payload, status):
    async def handle(_transport, request):
        if payload is None:
            raise httpx.ConnectError("network", request=request)
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    receipt = await _affiliate().search("纸杯", tmall_only=False)
    assert receipt["status"] == status
    assert not receipt["items"] and "private vendor message" not in repr(receipt)


@pytest.mark.asyncio
async def test_taobao_without_credentials_never_calls_network(monkeypatch):
    async def unexpected(*_):
        pytest.fail("Unconfigured provider should never make a request")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", unexpected)
    receipt = await _affiliate(key="", secret="", adzone="").search("纸杯", tmall_only=False)
    assert receipt["status"] == "unconfigured" and receipt["fetched_at"] is None
