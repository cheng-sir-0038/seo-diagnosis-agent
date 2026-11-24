from __future__ import annotations

from decimal import Decimal
from itertools import chain
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from src.core import cache as cache_module
from src.core.cache import CacheManager
from src.procurement_agent import taobao
from src.procurement_agent.local_catalog import material_search_response
from src.procurement_agent.search_intent import (
    is_catalog_refresh_followup,
    requests_catalog_refresh,
)
from src.procurement_agent.taobao import TaobaoAffiliate

_RESPONSE = "tbk_dg_material_optional_upgrade_response"


def test_full_catalog_has_1000_distinct_products_in_the_official_response_shape():
    pages = [
        material_search_response("", page=page, page_size=100)[_RESPONSE]
        for page in range(1, 11)
    ]
    rows = list(chain.from_iterable(page["result_list"]["map_data"] for page in pages))
    assert all(page["total_results"] == 1000 for page in pages)
    assert len(rows) == len({row["item_id"] for row in rows}) == 1000
    assert len({row["item_basic_info"]["category_name"] for row in rows}) == 25
    for row in rows:
        basic, prices = row["item_basic_info"], row["price_promotion_info"]
        assert isinstance(row["item_id"], str) and row["item_id"]
        assert basic["title"] and basic["shop_title"]
        assert type(basic["user_type"]) is int and basic["user_type"] in {0, 1}
        assert type(basic["volume"]) is int and basic["volume"] >= 0
        assert isinstance(row["publish_info"], dict)
        assert isinstance(prices["reserve_price"], str)
        assert isinstance(prices["final_promotion_price"], str)
        assert 0 < Decimal(prices["final_promotion_price"]) <= Decimal(prices["reserve_price"])


def test_local_search_matches_specification_paginate_and_filter_market():
    first = material_search_response("200ml一次性纸杯", page=1, page_size=5)[_RESPONSE]
    second = material_search_response("纸杯 ２００ ｍｌ", page=2, page_size=5)[_RESPONSE]
    assert first["total_results"] == second["total_results"] == 10
    first_rows, second_rows = (page["result_list"]["map_data"] for page in (first, second))
    assert len(first_rows) == len(second_rows) == 5
    assert {row["item_id"] for row in first_rows}.isdisjoint(
        row["item_id"] for row in second_rows
    )
    assert all("200ml" in row["item_basic_info"]["title"] for row in first_rows + second_rows)
    mall = material_search_response("纸杯 200ml", tmall_only=True)[_RESPONSE]
    assert mall["total_results"] == 4
    assert all(row["item_basic_info"]["user_type"] == 1 for row in mall["result_list"]["map_data"])
    by_pack = material_search_response("200毫升 纸杯 50只")[_RESPONSE]
    assert by_pack["total_results"] == 5
    assert all(
        "50只" in row["item_basic_info"]["title"] for row in by_pack["result_list"]["map_data"]
    )
    missing = material_search_response("完全不存在的商品")[_RESPONSE]
    assert missing["total_results"] == 0 and missing["result_list"]["map_data"] == []


@pytest.mark.asyncio
async def test_local_search_uses_existing_parser_with_two_hour_cache_and_no_http(monkeypatch):
    async def unexpected(*_):
        pytest.fail("Local catalogue should not make a network request")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", unexpected)
    clock = SimpleNamespace(now=100)
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    cache = CacheManager()
    client = TaobaoAffiliate(SecretStr(""), SecretStr(""), "", local_catalog=True, cache=cache)
    second_client = TaobaoAffiliate(
        SecretStr(""), SecretStr(""), "", local_catalog=True, cache=cache
    )

    fresh = await client.search("纸杯 200ml", tmall_only=False)
    cached = await second_client.search(" 纸杯   200ml ", tmall_only=False)
    assert fresh["source"] == "local_product_catalog" and fresh["status"] == "ok"
    assert not fresh["cache_hit"] and cached["cache_hit"]
    assert fresh["fetched_at"] == cached["fetched_at"] and fresh["items"] == cached["items"]
    assert len(fresh["items"]) == 5 and fresh["total_results"] == 10
    assert all(item["url"] is None for item in fresh["items"])
    assert (await client.search("纸杯 200ml", tmall_only=True))["total_results"] == 4

    clock.now += 7200
    assert not (await client.search("纸杯 200ml", tmall_only=False))["cache_hit"]
    missing = await client.search("完全不存在的商品", tmall_only=False)
    assert missing["status"] == "no_results" and "本地商品库" in missing["message"]
    live = TaobaoAffiliate(SecretStr(""), SecretStr(""), "", cache=cache)
    assert (await live.search("纸杯 200ml", tmall_only=False))["status"] == "unconfigured"


@pytest.mark.asyncio
async def test_refresh_replaces_only_the_exact_catalog_cache_entry(monkeypatch):
    calls = []

    def search_local(keyword, **filters):
        calls.append((keyword, filters["tmall_only"]))
        return material_search_response(keyword, **filters)

    monkeypatch.setattr(taobao, "material_search_response", search_local)
    client = TaobaoAffiliate(
        SecretStr(""), SecretStr(""), "", local_catalog=True, cache=CacheManager(),
    )
    await client.search("纸杯 200ml", tmall_only=False)
    await client.search("纸杯 200ml", tmall_only=True)
    await client.search("鼠标", tmall_only=False)

    refreshed = await client.search("纸杯 200ml", tmall_only=False, force_refresh=True)
    assert not refreshed["cache_hit"]
    assert (await client.search("纸杯 200ml", tmall_only=True))["cache_hit"]
    assert (await client.search("鼠标", tmall_only=False))["cache_hit"]
    cached = await client.search("纸杯 200ml", tmall_only=False)
    assert cached["cache_hit"] and cached["fetched_at"] == refreshed["fetched_at"]
    assert calls == [
        ("纸杯 200ml", False), ("纸杯 200ml", True), ("鼠标", False), ("纸杯 200ml", False),
    ]


@pytest.mark.parametrize(
    ("text", "refresh", "followup"),
    [
        ("最新", True, True),
        ("帮我刷新一下上次的结果。", True, True),
        ("查一下最新价格", True, True),
        ("清理一下缓存", True, True),
        ("刷新天猫200ml纸杯的价格", True, False),
        ("查200ml纸杯，不用缓存", True, False),
        ("找最新款无线鼠标", False, False),
        ("查高刷新率显示器", False, False),
        ("不要刷新，沿用上次纸杯价格", False, False),
        ("不用查最新的价格", False, False),
    ],
)
def test_only_intentional_catalog_refresh_requests_skip_cache(text, refresh, followup):
    assert requests_catalog_refresh(text) is refresh
    assert is_catalog_refresh_followup(text) is followup
