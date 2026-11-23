"""通过 FlyAI CLI 1.0.16 查询交通和酒店候选。"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import partial
from hashlib import sha256
from tempfile import TemporaryFile
from typing import Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

_SOURCE = "flyai"
_TIMEOUT_SECONDS = 15
_MAX_STDOUT_BYTES = 512 * 1024
_MAX_STDERR_BYTES = 16 * 1024
_MAX_ITEMS = 3
_CHINA_TIME_ZONE = ZoneInfo("Asia/Shanghai")
_CNY_PRICE = re.compile(
    r"(?P<currency>CN[¥￥]|CNY|[¥￥])?\s*"
    r"(?P<amount>(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)"
)
_PERMISSION_ERROR = re.compile(
    r"MCP HTTP (?:401|403)\b|unauthorized|forbidden|permission denied|invalid api key|"
    r"无权限|未授权|鉴权失败|认证失败",
    re.IGNORECASE,
)
_RATE_LIMITED = re.compile(r"SentinelBlockException|MCP HTTP 429\b|too many requests", re.I)
_MISSING_KEY = "请在服务端配置 FlyAI API 密钥。"
_EMPTY_RESULT = "飞猪没有找到符合查询条件的候选。"
_INVALID_RESULT = "飞猪返回的数据格式有误。"
_HOTEL_SCOPE = "飞猪按目的地和入住日期搜索，价格仅供参考。入住人数、房间数及可售房型需在飞猪确认。"
_Status = Literal["ok", "no_results", "unconfigured", "error"]
_Mode = Literal["rail", "flight"]
_Row = dict[str, Any]


def _result(
    status: _Status,
    *,
    queried_at: str | None = None,
    items: list[_Row] | None = None,
    message: str | None = None,
) -> _Row:
    result = {"source": _SOURCE, "status": status, "queried_at": queried_at, "items": items or []}
    if message:
        result["message"] = message
    return result


def _text(value: object, max_chars: int = 240) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > max_chars or any(ord(char) < 32 for char in value):
        return None
    return value


def _booking_url(value: object) -> str | None:
    url = _text(value, 4096)
    if not url or "\\" in url or any(char.isspace() for char in url):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme in ("https", "http") and parsed.hostname and not parsed.username:
            return url
    except ValueError:
        return None
    return None


def _amount(value: object) -> tuple[str | None, str | None, str | None]:
    raw = _text(str(value), 64) if type(value) in (int, Decimal) else _text(value, 64)
    match = _CNY_PRICE.fullmatch(raw or "")
    if match is None:
        return None, None, raw
    return match["amount"].replace(",", ""), "CNY" if match["currency"] else None, raw


def _local_time(value: str | None) -> datetime | None:
    if not value or len(value) < 16:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _timestamp(value: datetime | None, mode: _Mode) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        if mode != "rail":
            return None
        value = value.replace(tzinfo=_CHINA_TIME_ZONE)
    return value.isoformat()


def _candidate_id(kind: str, *parts: object) -> str:
    identity = json.dumps(parts, ensure_ascii=True).encode()
    return f"{_SOURCE}:{kind}:{sha256(identity).hexdigest()[:20]}"


def _transport_item(row: _Row, mode: _Mode, travel_date: date) -> _Row | None:
    journeys = row.get("journeys")
    if not isinstance(journeys, list) or len(journeys) != 1 or not isinstance(journeys[0], dict):
        return None
    segments = journeys[0].get("segments")
    if (
        not isinstance(segments, list)
        or not segments
        or not all(isinstance(segment, dict) for segment in segments)
    ):
        return None
    first, last = segments[0], segments[-1]
    from_stop = _text(first.get("depStationName")) or _text(first.get("depCityName"))
    to_stop = _text(last.get("arrStationName")) or _text(last.get("arrCityName"))
    numbers = [_text(segment.get("marketingTransportNo")) for segment in segments]
    if not any(numbers) and not (from_stop and to_stop):
        return None
    name = " / ".join(filter(None, numbers)) or f"{from_stop} → {to_stop}"
    departure_local = _text(first.get("depDateTime"))
    arrival_local = _text(last.get("arrDateTime"))
    departure, arrival = _local_time(departure_local), _local_time(arrival_local)
    if departure is not None and departure.date() != travel_date:
        return None
    classes = {_text(segment.get("seatClassName")) for segment in segments}
    fare_class = classes.pop() if len(classes) == 1 else None
    amount, currency, raw_price = _amount(row.get("adultPrice"))
    price_field = "adultPrice"
    fallback_field = "price" if mode == "rail" else "ticketPrice"
    if amount is None and row.get(fallback_field) is not None:
        amount, currency, raw_price = _amount(row[fallback_field])
        price_field = fallback_field
    return {
        "id": _candidate_id(mode, name, from_stop, to_stop, departure_local, fare_class, raw_price),
        "name": name,
        "mode": mode,
        "source": _SOURCE,
        "booking_url": _booking_url(row.get("jumpUrl")),
        "from_stop": from_stop,
        "to_stop": to_stop,
        "departs_at": _timestamp(departure, mode),
        "arrives_at": _timestamp(arrival, mode),
        "departs_local": departure_local,
        "arrives_local": arrival_local,
        "fare_class": fare_class,
        "amount": amount,
        "currency": currency,
        "price_basis": "person" if amount is not None and price_field == "adultPrice" else None,
        "full_price": False,
        "price_field": price_field if raw_price else None,
        "price_as_returned": raw_price,
    }


def _hotel_item(row: _Row, check_in: date, check_out: date) -> _Row | None:
    name = _text(row.get("name"))
    if not name:
        return None
    address = _text(row.get("address"))
    amount, currency, raw_price = _amount(row.get("price"))
    hotel_id = _text(row.get("shId"))
    return {
        "id": _candidate_id("hotel", hotel_id or name, address, check_in.isoformat()),
        "name": name,
        "source": _SOURCE,
        "booking_url": _booking_url(row.get("detailUrl") or row.get("jumpUrl")),
        "address": address,
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
        "room_type": None,
        "party_size_confirmed": False,
        "amount": amount,
        "currency": currency,
        "price_basis": None,
        "full_price": False,
        "price_field": "price" if raw_price else None,
        "price_as_returned": raw_price,
    }


async def _invoke(
    executable: str,
    api_key: str,
    command: str,
    arguments: tuple[str, ...],
) -> tuple[int, bytes, bytes] | None:
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
    env.update(FLYAI_API_KEY=api_key, FLYAI_JSON="1")
    # The CLI exits after writing JSON. Node writes synchronously to regular files on POSIX.
    with TemporaryFile() as stdout, TemporaryFile() as stderr:
        async with asyncio.timeout(_TIMEOUT_SECONDS):
            process = await asyncio.create_subprocess_exec(
                executable,
                command,
                *arguments,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
            )
            try:
                status = await process.wait()
            finally:
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
        stdout.seek(0)
        stderr.seek(0)
        output, errors = stdout.read(_MAX_STDOUT_BYTES + 1), stderr.read(_MAX_STDERR_BYTES + 1)
        if len(output) > _MAX_STDOUT_BYTES or len(errors) > _MAX_STDERR_BYTES:
            return None
        return status, output, errors


def _provider_failure(raw_message: str, status: object = None) -> str:
    if status in (401, 403, "401", "403") or _PERMISSION_ERROR.search(raw_message):
        return "飞猪认证失败或当前密钥缺少此查询的权限。"
    if status in (429, "429") or _RATE_LIMITED.search(raw_message):
        return "飞猪限制了查询频率，请稍后再查。"
    return "飞猪查询失败，请稍后再试。"


async def _search(
    executable: str,
    api_key: str,
    command: str,
    arguments: tuple[str, ...],
    normalize: Callable[[_Row], _Row | None],
) -> _Row:
    if not api_key.strip():
        return _result("unconfigured", message=_MISSING_KEY)
    queried_at = datetime.now(UTC).isoformat()
    try:
        response = await _invoke(executable, api_key.strip(), command, arguments)
    except FileNotFoundError:
        return _result("unconfigured", message="找不到 FlyAI CLI，请检查服务端配置。")
    except TimeoutError:
        return _result("error", queried_at=queried_at, message="飞猪查询超时。")
    except (OSError, ValueError):
        return _result("error", queried_at=queried_at, message="无法运行 FlyAI CLI。")
    if response is None:
        return _result("error", queried_at=queried_at, message="飞猪查询输出超过大小限制。")
    code, stdout, stderr = response
    if code:
        message = _provider_failure(stderr.decode("utf-8", errors="replace"))
        return _result("error", queried_at=queried_at, message=message)
    try:
        payload = json.loads(stdout, parse_float=Decimal)
    except (UnicodeDecodeError, ValueError, RecursionError):
        detail = stdout.decode("utf-8", errors="replace")
        message = _provider_failure(detail) if _RATE_LIMITED.search(detail) else _INVALID_RESULT
        return _result("error", queried_at=queried_at, message=message)
    if not isinstance(payload, dict) or ("status" not in payload and not payload.get("isError")):
        return _result("error", queried_at=queried_at, message=_INVALID_RESULT)
    if payload.get("isError") or payload["status"] != 0:
        raw_message = _text(payload.get("message")) or ""
        message = _provider_failure(raw_message, payload.get("status"))
        return _result("error", queried_at=queried_at, message=message)
    data = payload.get("data")
    rows = data.get("itemList") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return _result("error", queried_at=queried_at, message=_INVALID_RESULT)
    candidates = (normalize(row) for row in rows if isinstance(row, dict))
    items = {item["id"]: item for item in candidates if item is not None}
    if items:
        return _result("ok", queried_at=queried_at, items=list(items.values())[:_MAX_ITEMS])
    message = _INVALID_RESULT if rows else _EMPTY_RESULT
    return _result("error" if rows else "no_results", queried_at=queried_at, message=message)


async def search_transport(
    origin: str,
    destination: str,
    travel_date: date,
    *,
    api_key: str,
    executable: str = "flyai",
) -> dict:
    """查询同一天的火车和航班，保留各自的结果与失败原因。"""
    source_city, target_city = _text(origin, 120), _text(destination, 120)
    if not source_city or not target_city:
        return _result("error", message="请提供有效的出发地和目的地。")
    args = (
        f"--origin={source_city}",
        f"--destination={target_city}",
        f"--dep-date={travel_date}",
    )
    rail, flight = await asyncio.gather(
        _search(
            executable,
            api_key,
            "search-train",
            args,
            partial(_transport_item, mode="rail", travel_date=travel_date),
        ),
        _search(
            executable,
            api_key,
            "search-flight",
            args,
            partial(_transport_item, mode="flight", travel_date=travel_date),
        ),
    )
    items = rail["items"] + flight["items"]
    queried_at = max(
        (value["queried_at"] for value in (rail, flight) if value["queried_at"]),
        default=None,
    )
    if items:
        result = _result("ok", queried_at=queried_at, items=items)
    else:
        # One failed search means an empty response from the other is inconclusive.
        order = {"error": 2, "no_results": 1, "unconfigured": 0}
        result = max((rail, flight), key=lambda value: order[value["status"]]).copy()
        result["queried_at"] = queried_at
    result["modes"] = {
        mode: {key: value for key, value in outcome.items() if key in ("status", "message")}
        for mode, outcome in (("rail", rail), ("flight", flight))
    }
    return result


async def search_hotels(
    destination: str,
    check_in: date,
    check_out: date,
    *,
    travelers: int,
    rooms: int,
    api_key: str,
    executable: str = "flyai",
) -> dict:
    """查询指定日期的酒店候选及有出处的参考价。"""
    target_city = _text(destination, 120)
    if not target_city or check_out <= check_in or travelers < 1 or rooms < 1:
        return _result("error", message="请检查目的地、入住日期、人数和房间数。")
    result = await _search(
        executable,
        api_key,
        "search-hotel",
        (
            f"--dest-name={target_city}",
            f"--check-in-date={check_in}",
            f"--check-out-date={check_out}",
        ),
        partial(_hotel_item, check_in=check_in, check_out=check_out),
    )
    result["scope"] = {
        "destination": target_city,
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
        "travelers_requested": travelers,
        "rooms_requested": rooms,
        "party_size_confirmed": False,
    }
    if result["status"] == "ok":
        result["message"] = _HOTEL_SCOPE
    return result
