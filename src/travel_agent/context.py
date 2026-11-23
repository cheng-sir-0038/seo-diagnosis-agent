"""Read-only city forecasts and driving estimates for business trips."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from math import ceil, isfinite
from typing import Any
from unicodedata import normalize
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_BAIDU_GEOCODING_URL = "https://api.map.baidu.com/geocoding/v3/"
_BAIDU_DRIVING_URL = "https://api.map.baidu.com/directionlite/v1/driving"
_BAIDU_PLACE_URL = "https://api.map.baidu.com/place/v3/region"
_POI_SUFFIXES = (
    "国际机场",
    "航站楼",
    "机场",
    "高铁站",
    "火车站",
    "客运站",
    "汽车站",
    "公交站",
    "地铁站",
    "会展中心",
    "博览中心",
    "会议中心",
    "展览中心",
    "展览馆",
    "体育馆",
    "体育场",
    "会场",
    "站",
)
_POI_PAGE_SIZE = 20
_FORECAST_DAYS = 16
_RAIN_ADVICE_PCT = 50
_COOL_ADVICE_C = 12
_HOT_ADVICE_C = 30
_WIND_ADVICE_KMH = 35
_HTTP_TIMEOUT = 5.0
_CITY_FEATURE_CODES = {"PPLC", "PPLA", "PPLA2"}
_DAILY_FIELDS = {
    "temperature_2m_max": "temperature_max_c",
    "temperature_2m_min": "temperature_min_c",
    "precipitation_probability_max": "precipitation_probability_max_pct",
    "wind_speed_10m_max": "wind_speed_max_kmh",
}


class _ProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _BaiduTransport(httpx.AsyncHTTPTransport):
    def __init__(self, api_key: str) -> None:
        super().__init__()
        self._api_key = api_key

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # 百度只接受 URL 中的 AK，将密钥放在 transport 内以保护 HTTPX 请求日志。
        outbound = httpx.Request(
            request.method,
            request.url.copy_merge_params({"ak": self._api_key}),
            headers=request.headers,
            stream=request.stream,
            extensions=request.extensions,
        )
        return await super().handle_async_request(outbound)


def _receipt(source: str) -> dict[str, Any]:
    return {"source": source, "status": "no_results", "queried_at": None, "items": []}


def _failure(result: dict[str, Any], error: _ProviderError) -> dict[str, Any]:
    return {**result, "status": "error", "error_code": error.code, "message": str(error)}


async def _read_json(client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        response = await client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            raise _ProviderError("rate_limited", "数据源调用受限，请稍后再查询。") from None
        if exc.response.status_code in {401, 403}:
            raise _ProviderError("permission_denied", "数据源拒绝了查询权限。") from None
        raise _ProviderError("unavailable", "数据源暂时无法提供结果。") from None
    except httpx.TimeoutException:
        raise _ProviderError("timeout", "数据源查询超时，请稍后再试。") from None
    except httpx.HTTPError:
        raise _ProviderError("unavailable", "数据源网络请求失败。") from None
    except ValueError:
        raise _ProviderError("invalid_response", "数据源响应格式不完整。") from None

    if not isinstance(payload, dict):
        raise _ProviderError("invalid_response", "数据源响应格式不完整。")
    return payload


def _number(value: Any) -> int | float | None:
    if type(value) is int or (type(value) is float and isfinite(value)):
        return value
    return None


def _coordinates(latitude: Any, longitude: Any) -> tuple[float, float] | None:
    lat, lon = _number(latitude), _number(longitude)
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _place_name(value: str) -> str:
    return normalize("NFKC", value).strip().casefold().removesuffix("市").removesuffix("省")


def _regions(record: dict[str, Any]) -> list[str]:
    return [
        value
        for field in ("admin1", "admin2", "admin3", "admin4")
        if isinstance(value := record.get(field), str) and value.strip()
    ]


def _location_label(record: dict[str, Any]) -> str:
    name = record["name"]
    regions = [region for region in _regions(record) if _place_name(region) != _place_name(name)]
    return "，".join(dict.fromkeys([name, *regions]))


async def _weather_location(
    client: httpx.AsyncClient, city: str, result: dict[str, Any]
) -> dict[str, Any] | None:
    name, *regions = map(_place_name, city.split(","))
    region_filter = set(filter(None, regions))
    payload = await _read_json(
        client,
        _GEOCODING_URL,
        {
            "name": name,
            "count": 30,
            "language": "zh" if any("\u4e00" <= char <= "\u9fff" for char in name) else "en",
            "countryCode": "CN",
        },
    )
    records = payload.get("results", [])
    if not isinstance(records, list):
        raise _ProviderError("invalid_response", "天气地名查询返回格式不完整。")
    candidates = [
        item
        for item in records
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and item.get("country_code") == "CN"
        and region_filter.issubset(_place_name(region) for region in _regions(item))
    ]
    exact_matches = [item for item in candidates if _place_name(item["name"]) == name]
    cities = [item for item in exact_matches if item.get("feature_code") in _CITY_FEATURE_CODES]
    matches = cities or exact_matches
    if len(matches) == 1:
        return matches[0]
    if candidates:
        choices = [_location_label(item) for item in matches or candidates]
        result.update(
            status="needs_selection",
            choices=list(dict.fromkeys(choices))[:8],
            message="找到多个可能地点或近似地点。请确认城市和所在省份。",
        )
    else:
        result["message"] = "天气数据源没有定位到该城市，请确认城市和省份。"
    return None


def _forecast_days(daily: dict[str, Any], start: date, end: date) -> list[dict[str, Any]]:
    expected_dates = {
        (start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)
    }
    days: dict[str, dict[str, Any]] = {}
    dates = daily.get("time")
    if not isinstance(dates, list):
        raise _ProviderError("invalid_response", "天气响应缺少预报日期。")
    columns = {
        output_name: values if isinstance(values := daily.get(key), list) else []
        for key, output_name in _DAILY_FIELDS.items()
    }
    for index, day in enumerate(dates):
        if not isinstance(day, str) or day not in expected_dates:
            continue
        fields = {
            name: _number(values[index]) if index < len(values) else None
            for name, values in columns.items()
        }
        if any(value is not None for value in fields.values()):
            days[day] = {"date": day, **fields}
    return [days[day] for day in sorted(days)]


def _weather_advice(days: list[dict[str, Any]]) -> list[str]:
    def extreme(field: str, *, lowest: bool = False) -> tuple[int | float, str] | None:
        values = (
            (value, day["date"]) for day in days if (value := _number(day.get(field))) is not None
        )
        select = min if lowest else max
        return select(values, key=lambda value: value[0], default=None)

    advice = []
    rain = extreme("precipitation_probability_max_pct")
    if rain and rain[0] >= _RAIN_ADVICE_PCT:
        advice.append(f"{rain[1]} 降水概率最高 {rain[0]:g}%，建议带伞，通勤多留一点时间。")
    cold = extreme("temperature_min_c", lowest=True)
    heat = extreme("temperature_max_c")
    if cold and cold[0] <= _COOL_ADVICE_C:
        advice.append(f"{cold[1]} 最低 {cold[0]:g}℃，建议带一件方便增减的外套。")
    elif heat and heat[0] >= _HOT_ADVICE_C:
        advice.append(f"{heat[1]} 最高 {heat[0]:g}℃，建议备水和防晒，少安排正午室外步行。")
    elif cold and heat:
        advice.append(f"已查日期的气温约 {cold[0]:g}–{heat[0]:g}℃，按早晚出门的温度准备衣物。")
    wind = extreme("wind_speed_max_kmh")
    if wind and wind[0] >= _WIND_ADVICE_KMH:
        advice.append(f"{wind[1]} 最大风速 {wind[0]:g} km/h，有户外安排可准备室内备选。")
    return advice


async def trip_weather(city: str, start: date, end: date) -> dict[str, Any]:
    """Query the city-level Open-Meteo forecast for Chinese mainland trips."""
    result = _receipt("open_meteo")
    city = normalize("NFKC", city).replace("、", ",").strip()
    if not city or not _place_name(city.partition(",")[0]) or start > end:
        return {**result, "message": "请提供城市与有效的起止日期。"}
    result.update(
        queried_at=datetime.now(UTC).isoformat(),
        requested_start=start.isoformat(),
        requested_end=end.isoformat(),
    )

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            place = await _weather_location(client, city, result)
            if place is None:
                return result
            coordinates = _coordinates(place.get("latitude"), place.get("longitude"))
            zone = place.get("timezone")
            if coordinates is None or not isinstance(zone, str) or not zone:
                raise _ProviderError("invalid_response", "天气地名查询缺少有效经纬度或时区。")
            latitude, longitude = coordinates
            today = datetime.now(ZoneInfo(zone)).date()
            forecast_start = max(today, start)
            forecast_end = min(today + timedelta(days=_FORECAST_DAYS - 1), end)
            result.update(
                location=_location_label(place),
                latitude=latitude,
                longitude=longitude,
                coordinate_system="WGS84",
                timezone=zone,
            )
            if forecast_start > forecast_end:
                return {
                    **result,
                    "status": "out_of_range",
                    "message": "出差日期超出天气预报范围。",
                    "advice": ["临近出发再查看天气。"],
                }
            forecast = await _read_json(
                client,
                _FORECAST_URL,
                {
                    "latitude": latitude,
                    "longitude": longitude,
                    "timezone": zone,
                    "start_date": forecast_start.isoformat(),
                    "end_date": forecast_end.isoformat(),
                    "daily": ",".join(_DAILY_FIELDS),
                    "temperature_unit": "celsius",
                    "wind_speed_unit": "kmh",
                },
            )
        daily = forecast.get("daily")
        if forecast.get("timezone") != zone or not isinstance(daily, dict):
            raise _ProviderError("invalid_response", "天气响应缺少日预报或当地时区不匹配。")
        days = _forecast_days(daily, forecast_start, forecast_end)
    except _ProviderError as exc:
        return _failure(result, exc)
    except (ZoneInfoNotFoundError, ValueError):
        return _failure(result, _ProviderError("invalid_response", "天气数据源返回了无效时区。"))

    if not days:
        return {**result, "message": "数据源没有返回所选出差日期的预报。"}

    result.update(
        items=days,
        coverage_start=days[0]["date"],
        coverage_end=days[-1]["date"],
        advice=_weather_advice(days),
    )
    covers_trip = len(days) == (end - start).days + 1
    has_all_fields = all(None not in day.values() for day in days)
    result["status"] = "ok" if covers_trip and has_all_fields else "partial"
    if result["status"] == "partial":
        result["message"] = "预报只覆盖部分出差日期或有天气指标缺项，请以返回的日期和数值为准。"
    return result


async def _baidu_payload(
    client: httpx.AsyncClient, url: str, params: dict[str, Any]
) -> dict[str, Any]:
    payload = await _read_json(client, url, params)
    status = payload.get("status")
    if type(status) is not int:
        raise _ProviderError("invalid_response", "百度地图响应缺少查询状态。")
    if status == 7:
        return {}
    if status == 4 or 300 <= status < 400:
        raise _ProviderError("rate_limited", "百度地图的调用频率或查询配额已达上限。")
    if status in (3, 5, 101, 102) or 200 <= status < 300:
        raise _ProviderError("permission_denied", "百度地图未授权该查询。")
    if status != 0:
        raise _ProviderError("unavailable", "百度地图暂时无法提供结果。")
    return payload


async def _baidu_result(
    client: httpx.AsyncClient, url: str, params: dict[str, Any]
) -> dict[str, Any]:
    payload = await _baidu_payload(client, url, params)
    if not payload:
        return {}
    data = payload.get("result")
    if not isinstance(data, dict):
        raise _ProviderError("invalid_response", "百度地图响应缺少结果。")
    return data


def _poi_name(value: str) -> str:
    return normalize("NFKC", value).strip().casefold()


def _is_named_poi(value: str) -> bool:
    name = _poi_name(value)
    suffix = next((candidate for candidate in _POI_SUFFIXES if name.endswith(candidate)), None)
    return suffix is not None and bool(
        re.fullmatch(r"[^\W_][\w·() -]{1,60}", name.removesuffix(suffix))
    )


async def _baidu_poi(
    client: httpx.AsyncClient, address: str, city: str
) -> tuple[float, float] | None:
    payload = await _baidu_payload(
        client,
        _BAIDU_PLACE_URL,
        {
            "query": address,
            "region": city,
            "region_limit": "true",
            "page_num": 0,
            "page_size": _POI_PAGE_SIZE,
            "output": "json",
        },
    )
    places = payload.get("results")
    total = payload.get("total")
    # A truncated result cannot establish uniqueness. Use one city-scoped request.
    if (
        payload.get("result_type") != "poi_type"
        or not isinstance(places, list)
        or type(total) is not int
        or total != len(places)
    ):
        return None

    if not all(isinstance(place, dict) and isinstance(place.get("name"), str) for place in places):
        return None
    expected_name = _poi_name(address)
    city_prefix = _place_name(city)
    names = {expected_name}
    if not expected_name.startswith(city_prefix):
        names.add(city_prefix + expected_name)
    matches = [place for place in places if _poi_name(place["name"]) in names]
    if len(matches) != 1:
        return None
    poi = matches[0]
    poi_city, uid, point = poi.get("city"), poi.get("uid"), poi.get("location")
    if (
        not isinstance(poi_city, str)
        or _place_name(poi_city) != _place_name(city)
        or not isinstance(uid, str)
        or not uid.strip()
        or not isinstance(point, dict)
    ):
        return None
    return _coordinates(point.get("lat"), point.get("lng"))


async def _baidu_point(
    client: httpx.AsyncClient, address: str, city: str, label: str, result: dict[str, Any]
) -> str | None:
    data = await _baidu_result(
        client,
        _BAIDU_GEOCODING_URL,
        {"address": address, "city": city, "output": "json"},
    )
    point = data.get("location")
    if not isinstance(point, dict):
        result["message"] = f"百度地图未找到{label}，请补充具体地址。"
        return None
    coordinates = None
    if data.get("precise") == 1 and data.get("level") not in ("省", "城市", "区县"):
        coordinates = _coordinates(point.get("lat"), point.get("lng"))
    elif data.get("precise") == 0 and _is_named_poi(address):
        coordinates = await _baidu_poi(client, address, city)
    if coordinates is None:
        result.update(status="needs_selection", message=f"请确认{label}的完整地址。")
        return None
    return f"{coordinates[0]},{coordinates[1]}"


async def estimate_commute(
    origin: str, destination: str, city: str, *, api_key: str
) -> dict[str, Any]:
    """Estimate a mainland driving route through Baidu geocoding and routing."""
    result = _receipt("baidu")
    if not api_key.strip():
        return {**result, "status": "unconfigured", "message": "百度地图查询尚未配置。"}
    origin, destination, city = origin.strip(), destination.strip(), city.strip()
    if not (origin and destination and city):
        return {**result, "message": "请提供城市、出发地址和到达地址。"}
    result.update(queried_at=datetime.now(UTC).isoformat(), city=city)
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT, transport=_BaiduTransport(api_key.strip())
        ) as client:
            start = await _baidu_point(client, origin, city, "出发地点", result)
            if start is None:
                return result
            end = await _baidu_point(client, destination, city, "到达地点", result)
            if end is None:
                return result
            data = await _baidu_result(
                client,
                _BAIDU_DRIVING_URL,
                {"origin": start, "destination": end, "coord_type": "bd09ll"},
            )
        routes = data.get("routes", [])
        if not routes:
            return {**result, "message": "百度地图未返回驾车路线。"}
        if not isinstance(routes, list) or not isinstance(routes[0], dict):
            raise _ProviderError("invalid_response", "百度地图路线响应格式不完整。")
        duration = _number(routes[0].get("duration"))
        distance = _number(routes[0].get("distance"))
        if duration is None or distance is None or duration < 0 or distance < 0:
            raise _ProviderError("invalid_response", "百度地图路线缺少可靠的耗时或距离。")
    except _ProviderError as exc:
        return _failure(result, exc)

    return {
        **result,
        "status": "ok",
        "items": [
            {
                "mode": "driving",
                "duration_minutes": ceil(duration / 60),
                "distance_meters": distance,
            }
        ],
        "message": "百度地图查询时的驾车估算，实际耗时取决于出发时间和路况。",
    }
