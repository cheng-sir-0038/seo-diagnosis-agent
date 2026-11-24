from __future__ import annotations

import hmac
import json
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from html import unescape
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from src.procurement_agent.evidence import safe_link

GATEWAY = "https://eco.taobao.com/router/rest"
_TOP_TIMEZONE = timezone(timedelta(hours=8))
_MAX_BYTES = 1_000_000
_PAGE_SIZE = 10
_MAX_ITEMS = 5
_TIMEOUT = 8.0
_ITEM_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
_PRICE = re.compile(r"[0-9]{1,12}(?:\.[0-9]{1,2})?")


class _Method(StrEnum):
    SEARCH = "taobao.tbk.dg.material.optional.upgrade"
    DETAILS = "taobao.tbk.item.info.upgrade.get"
    COUPON = "taobao.tbk.coupon.get"


class Status(StrEnum):
    OK = "ok"
    EMPTY = "no_results"
    UNCONFIGURED = "unconfigured"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    NETWORK_ERROR = "network_error"
    INVALID_RESPONSE = "invalid_response"
    ERROR = "error"


_MESSAGES = {
    Status.EMPTY: "淘宝联盟没有返回匹配资料。",
    Status.UNCONFIGURED: "淘宝联盟应用凭据或搜索所需的推广位待配置，可先使用你提供的资料。",
    Status.PERMISSION_DENIED: "淘宝联盟拒绝了调用，请检查应用凭据及该接口的权限。",
    Status.RATE_LIMITED: "淘宝联盟调用频率或额度受限。",
    Status.NETWORK_ERROR: "淘宝联盟网络请求失败或超时。",
    Status.INVALID_RESPONSE: "淘宝联盟返回的结果结构与文档合同不符。",
    Status.ERROR: "淘宝联盟本次查询失败。",
}


def top_signature(params: dict[str, str], secret: str) -> str:
    payload = "".join(key + value for key, value in sorted(params.items()) if key != "sign")
    return hmac.digest(secret.encode("utf-8"), payload.encode("utf-8"), "md5").hex().upper()


def _record(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: object, limit: int = 200) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"<[^>]*>", "", unescape(value)).strip()
    return text[:limit] or None


def _money(value: object) -> str | None:
    if type(value) not in {int, Decimal, str} or not _PRICE.fullmatch(str(value)):
        return None
    return f"{Decimal(str(value)):.2f}"


def _platform_link(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    url = safe_link("https:" + value if value.startswith("//") else value)
    if not url:
        return None
    host = urlsplit(url).hostname or ""
    if any(
        host == root or host.endswith("." + root) for root in ("taobao.com", "tmall.com", "tb.cn")
    ):
        return url
    return None


def _item(row: dict) -> dict | None:
    basic = _record(row.get("item_basic_info"))
    promotion = _record(row.get("price_promotion_info"))
    publish = _record(row.get("publish_info"))
    title = _text(basic.get("title"))
    item_id = row.get("item_id")
    if not title or type(item_id) not in {int, str} or not _ITEM_ID.fullmatch(str(item_id)):
        return None
    product_url = _platform_link(basic.get("item_url"))
    promotion_url = _platform_link(publish.get("click_url"))
    return {
        "item_id": str(item_id),
        "title": title,
        "shop": _text(basic.get("shop_title")),
        "platform": "tmall" if str(basic.get("user_type")) == "1" else "taobao",
        "url": product_url or promotion_url,
        "promotion_link": not product_url and bool(promotion_url),
        "reference_price_cny": _money(promotion.get("reserve_price")),
        "estimated_price_cny": _money(promotion.get("final_promotion_price")),
        "sku_verified": False,
    }


def _status(error: dict) -> Status:
    code, subcode = str(error.get("code", "")), str(error.get("sub_code", ""))
    if code == "7" or "call-limited" in subcode or subcode.startswith("accesscontrol.limited"):
        return Status.RATE_LIMITED
    if code == "11" or subcode.startswith("isv.permission-") or subcode == "isv.invalid-permission":
        return Status.PERMISSION_DENIED
    return Status.ERROR


def _result(
    method: _Method,
    status: Status,
    *,
    fetched_at: str | None = None,
    items: list[dict] | None = None,
) -> dict:
    return {
        "source": "taobao_affiliate",
        "method": method.value,
        "status": status.value,
        "fetched_at": fetched_at,
        "items": items or [],
        "message": _MESSAGES.get(status),
    }


class TaobaoAffiliate:
    def __init__(self, app_key: SecretStr, app_secret: SecretStr, adzone_id: str) -> None:
        self._app_key, self._app_secret, self._adzone_id = app_key, app_secret, adzone_id.strip()

    async def _call(self, method: _Method, args: dict[str, str]) -> tuple[dict, dict]:
        key = self._app_key.get_secret_value().strip()
        secret = self._app_secret.get_secret_value()
        if (
            not key
            or not secret
            or (method == _Method.SEARCH and not re.fullmatch(r"[1-9][0-9]{0,19}", self._adzone_id))
        ):
            return _result(method, Status.UNCONFIGURED), {}
        instant = datetime.now(UTC)
        timestamp = instant.astimezone(_TOP_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
        params = {
            "app_key": key,
            "sign_method": "hmac",
            "method": method.value,
            "timestamp": timestamp,
            "format": "json",
            "v": "2.0",
            **args,
        }
        params["sign"] = top_signature(params, secret)
        fetched_at = instant.isoformat()
        try:
            async with (
                httpx.AsyncClient(timeout=_TIMEOUT) as client,
                client.stream(
                    "POST",
                    GATEWAY,
                    data=params,
                ) as response,
            ):
                blocked = {
                    401: Status.PERMISSION_DENIED,
                    403: Status.PERMISSION_DENIED,
                    429: Status.RATE_LIMITED,
                }.get(response.status_code)
                if blocked:
                    return _result(method, blocked, fetched_at=fetched_at), {}
                response.raise_for_status()
                content = bytearray()
                async for part in response.aiter_bytes():
                    content.extend(part)
                    if len(content) > _MAX_BYTES:
                        return _result(method, Status.INVALID_RESPONSE, fetched_at=fetched_at), {}
                payload = _record(json.loads(content, parse_float=Decimal))
        except httpx.RequestError:
            return _result(method, Status.NETWORK_ERROR, fetched_at=fetched_at), {}
        except httpx.HTTPStatusError:
            return _result(method, Status.ERROR, fetched_at=fetched_at), {}
        except (ValueError, RecursionError):
            return _result(method, Status.INVALID_RESPONSE, fetched_at=fetched_at), {}
        if (error := payload.get("error_response")) is not None:
            return _result(method, _status(_record(error)), fetched_at=fetched_at), {}
        envelope = payload.get(method.removeprefix("taobao.").replace(".", "_") + "_response")
        if not isinstance(envelope, dict):
            return _result(method, Status.INVALID_RESPONSE, fetched_at=fetched_at), {}
        return _result(method, Status.OK, fetched_at=fetched_at), envelope

    @staticmethod
    def _items(receipt: dict, envelope: dict, group: str, key: str) -> dict:
        if receipt["status"] != Status.OK:
            return receipt
        container = envelope.get(group)
        rows = container.get(key, []) if isinstance(container, dict) else container
        if rows is None and (group in envelope or str(envelope.get("total_results")) == "0"):
            rows = []
        if not isinstance(rows, list):
            return {
                **receipt,
                "status": Status.INVALID_RESPONSE.value,
                "message": _MESSAGES[Status.INVALID_RESPONSE],
            }
        items = [candidate for row in rows if isinstance(row, dict) and (candidate := _item(row))]
        if rows and not items:
            return {
                **receipt,
                "status": Status.INVALID_RESPONSE.value,
                "message": _MESSAGES[Status.INVALID_RESPONSE],
            }
        if not items:
            return {**receipt, "status": Status.EMPTY.value, "message": _MESSAGES[Status.EMPTY]}
        return {
            **receipt,
            "items": list({item["item_id"]: item for item in items}.values())[:_MAX_ITEMS],
        }

    async def search(self, keyword: str, *, tmall_only: bool) -> dict:
        args = {
            "q": keyword,
            "adzone_id": self._adzone_id,
            "page_no": "1",
            "page_size": str(_PAGE_SIZE),
        }
        if tmall_only:
            args["is_tmall"] = "true"
        receipt, envelope = await self._call(_Method.SEARCH, args)
        return self._items(receipt, envelope, "result_list", "map_data")

    async def details(self, item_id: str) -> dict:
        receipt, envelope = await self._call(_Method.DETAILS, {"item_id": item_id})
        return self._items(receipt, envelope, "results", "tbk_item_detail")

    async def coupon(self, item_id: str, activity_id: str) -> dict:
        receipt, envelope = await self._call(
            _Method.COUPON,
            {"item_id": item_id, "activity_id": activity_id},
        )
        if receipt["status"] != Status.OK:
            return receipt
        offer = _record(envelope.get("data"))
        if not offer:
            return {**receipt, "status": Status.EMPTY.value, "message": _MESSAGES[Status.EMPTY]}
        return {
            **receipt,
            "items": [
                {
                    "item_id": item_id,
                    "activity_id": activity_id,
                    "amount_cny": _money(offer.get("coupon_amount")),
                    "minimum_spend_cny": _money(offer.get("coupon_start_fee")),
                    "starts_on": _text(offer.get("coupon_start_time"), 30),
                    "ends_on": _text(offer.get("coupon_end_time"), 30),
                    "remaining": (
                        offer["coupon_remain_count"]
                        if type(offer.get("coupon_remain_count")) is int
                        else None
                    ),
                }
            ],
        }
