from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from src.api.routes.workspace import travel
from src.travel_agent import context

_LOCAL_TODAY = date(2026, 11, 24)
_WUHAN = {
    "name": "武汉",
    "admin1": "湖北",
    "admin2": "武汉市",
    "feature_code": "PPLA",
    "country_code": "CN",
    "latitude": 30.58333,
    "longitude": 114.26667,
    "timezone": "Asia/Shanghai",
}


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            instant = datetime(2026, 11, 23, 16, tzinfo=UTC)
            return instant.astimezone(tz)

    monkeypatch.setattr(context, "datetime", FixedClock)


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch):
    def mock(*responses: httpx.Response) -> list[httpx.Request]:
        requests: list[httpx.Request] = []
        queued = iter(responses)

        async def respond(
            _transport: httpx.AsyncHTTPTransport, request: httpx.Request
        ) -> httpx.Response:
            requests.append(request)
            return next(queued)

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", respond)
        return requests

    return mock


def _place_response(*places: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"results": places})


def _forecast_response(*days: str, timezone: str = "Asia/Shanghai") -> httpx.Response:
    count = len(days)
    return httpx.Response(
        200,
        json={
            "timezone": timezone,
            "daily": {
                "time": days,
                "weather_code": [2] * count,
                "temperature_2m_max": [17.2] * count,
                "temperature_2m_min": [10.4] * count,
                "precipitation_probability_max": [0] * count,
                "wind_speed_10m_max": [12.6] * count,
            },
        },
    )


@pytest.mark.asyncio
async def test_weather_uses_chinese_city_wgs84_and_local_date(upstream) -> None:
    requests = upstream(
        _place_response(_WUHAN),
        _forecast_response(_LOCAL_TODAY.isoformat()),
    )

    result = await context.trip_weather("武汉市", _LOCAL_TODAY, _LOCAL_TODAY)

    assert result["status"] == "ok"
    assert result["source"] == "open_meteo"
    assert result["queried_at"] == "2026-11-23T16:00:00+00:00"
    assert result["location"] == "武汉，湖北"
    assert result["coordinate_system"] == "WGS84"
    assert result["timezone"] == "Asia/Shanghai"
    assert result["items"] == [
        {
            "date": _LOCAL_TODAY.isoformat(),
            "weather_code": 2,
            "temperature_max_c": 17.2,
            "temperature_min_c": 10.4,
            "precipitation_probability_max_pct": 0,
            "wind_speed_max_kmh": 12.6,
        }
    ]
    geocoding, forecast = requests
    assert geocoding.url.host == "geocoding-api.open-meteo.com"
    assert geocoding.url.params["name"] == "武汉"
    assert geocoding.url.params["language"] == "zh"
    assert geocoding.url.params["countryCode"] == "CN"
    assert forecast.url.host == "api.open-meteo.com"
    assert "weather_code" in forecast.url.params["daily"].split(",")
    assert forecast.url.params["latitude"] == "30.58333"
    assert forecast.url.params["longitude"] == "114.26667"
    assert forecast.url.params["timezone"] == "Asia/Shanghai"
    assert forecast.url.params["start_date"] == _LOCAL_TODAY.isoformat()


@pytest.mark.asyncio
async def test_weather_advice_uses_the_returned_days_and_values(upstream) -> None:
    day = _LOCAL_TODAY.isoformat()
    upstream(
        _place_response(_WUHAN),
        httpx.Response(
            200,
            json={
                "timezone": "Asia/Shanghai",
                "daily": {
                    "time": [day],
                    "temperature_2m_max": [18.0],
                    "temperature_2m_min": [9.0],
                    "precipitation_probability_max": [65],
                    "wind_speed_10m_max": [42.0],
                },
            },
        ),
    )
    result = await context.trip_weather("武汉", _LOCAL_TODAY, _LOCAL_TODAY)
    rain, temperature, wind = result["advice"]
    assert day in rain and "65%" in rain and "带伞" in rain
    assert day in temperature and "9℃" in temperature and "外套" in temperature
    assert day in wind and "42 km/h" in wind and "室内" in wind


@pytest.mark.asyncio
async def test_weather_asks_for_region_for_same_name_cities(upstream) -> None:
    northeast = {**_WUHAN, "name": "朝阳", "admin1": "辽宁", "admin2": "朝阳市"}
    second = {**northeast, "admin1": "吉林", "admin2": "吉林市"}
    requests = upstream(_place_response(northeast, second))

    result = await context.trip_weather("朝阳", _LOCAL_TODAY, _LOCAL_TODAY)

    assert result["status"] == "needs_selection"
    assert result["choices"] == ["朝阳，辽宁", "朝阳，吉林，吉林市"]
    assert len(requests) == 1

    requests = upstream(
        _place_response(northeast, second),
        _forecast_response(_LOCAL_TODAY.isoformat()),
    )
    result = await context.trip_weather("朝阳，辽宁", _LOCAL_TODAY, _LOCAL_TODAY)
    assert result["status"] == "ok"
    assert result["location"] == "朝阳，辽宁"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_out_of_range_weather_skips_forecast_request(upstream) -> None:
    requests = upstream(_place_response(_WUHAN))
    travel_day = _LOCAL_TODAY + timedelta(days=17)

    result = await context.trip_weather("武汉", travel_day, travel_day)

    assert result["status"] == "out_of_range"
    assert result["items"] == []
    assert result["advice"] == ["临近出发再查看天气。"]
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_partial_weather_preserves_missing_fields_and_requested_dates(upstream) -> None:
    last_forecast = _LOCAL_TODAY + timedelta(days=15)
    next_day = last_forecast + timedelta(days=1)
    requests = upstream(
        _place_response(_WUHAN),
        httpx.Response(
            200,
            json={
                "timezone": "Asia/Shanghai",
                "daily": {
                    "time": [last_forecast.isoformat(), next_day.isoformat()],
                    "temperature_2m_max": [18.0, 19.0],
                    "precipitation_probability_max": [None, 25],
                },
            },
        ),
    )

    result = await context.trip_weather("武汉", last_forecast, next_day)

    assert result["status"] == "partial"
    assert result["coverage_end"] == last_forecast.isoformat()
    assert [day["date"] for day in result["items"]] == [last_forecast.isoformat()]
    assert result["items"][0]["precipitation_probability_max_pct"] is None
    assert requests[1].url.params["end_date"] == last_forecast.isoformat()


@pytest.mark.asyncio
async def test_forecast_of_wrong_timezone_is_not_used(upstream) -> None:
    upstream(
        _place_response(_WUHAN),
        _forecast_response(_LOCAL_TODAY.isoformat(), timezone="GMT"),
    )

    result = await context.trip_weather("武汉", _LOCAL_TODAY, _LOCAL_TODAY)

    assert result["status"] == "error"
    assert result["error_code"] == "invalid_response"
    assert result["items"] == []


@pytest.mark.asyncio
async def test_weather_rate_limit_preserves_source_and_drops_upstream_body(upstream) -> None:
    requests = upstream(httpx.Response(429, json={"reason": "debug fixture"}))

    result = await context.trip_weather("武汉", _LOCAL_TODAY, _LOCAL_TODAY)

    assert result["status"] == "error"
    assert result["source"] == "open_meteo"
    assert result["error_code"] == "rate_limited"
    assert "debug fixture" not in str(result)
    assert len(requests) == 1


def _baidu_point_response(latitude: float, longitude: float, precise: int = 1) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": 0,
            "result": {"location": {"lat": latitude, "lng": longitude}, "precise": precise},
        },
    )


@pytest.mark.asyncio
async def test_commute_without_key_never_requests_provider(upstream) -> None:
    requests = upstream()

    result = await context.estimate_commute("机场", "酒店", "武汉", api_key=" ")

    assert result["status"] == "unconfigured"
    assert result["queried_at"] is None
    assert requests == []


@pytest.mark.asyncio
async def test_commute_driving_uses_bd09_and_hides_key_in_logs(upstream, caplog) -> None:
    caplog.set_level(logging.INFO, logger="httpx")
    requests = upstream(
        _baidu_point_response(30.7766, 114.2199),
        _baidu_point_response(30.5011, 114.4167),
        httpx.Response(
            200,
            json={
                "status": 0,
                "result": {
                    "routes": [
                        {
                            "duration": 1501, "distance": 34200, "steps": [
                                {"path": "114.2199,30.7766;114.3601,30.67"},
                                {"path": "114.3601,30.67;114.4167,30.5011"},
                            ],
                        },
                    ]
                },
            },
        ),
    )
    secret = "baidu-key-for-contract-test"

    result = await context.estimate_commute("武汉天河机场", "武汉站", "武汉", api_key=secret)

    assert result["source"] == "baidu"
    assert result["status"] == "ok"
    assert result["items"] == [
        {"mode": "driving", "duration_minutes": 26, "distance_meters": 34200}
    ]
    assert result["map"] == {
        "origin": [114.2199, 30.7766],
        "destination": [114.4167, 30.5011],
        "path": [(114.2199, 30.7766), (114.3601, 30.67), (114.4167, 30.5011)],
    }
    map_url = httpx.URL(result["source_url"])
    assert map_url.host == "api.map.baidu.com" and map_url.path == "/direction"
    assert map_url.params["origin"] == "latlng:30.7766,114.2199"
    assert map_url.params["destination"] == "latlng:30.5011,114.4167"
    assert map_url.params["coord_type"] == "bd09ll"
    origin, destination, route = requests
    assert origin.url.path == destination.url.path == "/geocoding/v3/"
    assert origin.url.params["city"] == "武汉"
    assert origin.url.params["address"] == "武汉天河机场"
    assert destination.url.params["address"] == "武汉站"
    assert route.url.path == "/directionlite/v1/driving"
    assert route.url.params["origin"] == "30.7766,114.2199"
    assert route.url.params["destination"] == "30.5011,114.4167"
    assert route.url.params["coord_type"] == "bd09ll"
    assert all(request.url.params["ak"] == secret for request in requests)
    assert "HTTP Request" in caplog.text
    assert secret not in caplog.text
    assert secret not in str(result)


async def test_map_preview_reuses_route_and_fetches_legacy_geometry(upstream, monkeypatch):
    secret = "test-preview-ak"
    monkeypatch.setattr(travel.settings, "baidu_maps_ak", SecretStr(secret))
    png = b"\x89PNG\r\n\x1a\nimage"
    requests = upstream(
        httpx.Response(200, json={"status": 0, "result": {"routes": [
            {"steps": [{"path": "114.22,30.77;114.41,30.50"}]},
        ]}}),
        httpx.Response(200, content=png),
        httpx.Response(200, json={"status": 240}),
    )
    app = FastAPI()
    app.include_router(travel.router)
    url = "/workspace/travel/map-preview"
    coords = {"origin": [114.22, 30.77], "destination": [114.41, 30.5]}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        invalid = await api.post(url, json={**coords, "origin": [114.22, 91]})
        assert invalid.status_code == 422
        assert requests == []

        historical = await api.post(url, json=coords)
        assert historical.status_code == 200
        assert historical.headers["content-type"] == "image/png" and historical.content == png
        assert secret not in historical.text
        assert requests[0].url.params["origin"] == "30.77,114.22"
        assert requests[0].url.params["steps_info"] == "1"
        assert requests[1].url.params["paths"] == "114.22000,30.77000;114.41000,30.50000"
        assert requests[1].url.params["ak"] == secret

        unavailable = await api.post(url, json={**coords, "path": [
            coords["origin"], coords["destination"],
        ]})
        assert unavailable.status_code == 502
        assert [request.url.path for request in requests] == [
            "/directionlite/v1/driving", "/staticimage/v2", "/staticimage/v2",
        ]
        assert secret not in unavailable.text


@pytest.mark.asyncio
async def test_commute_asks_for_precise_geocode_before_routing(upstream) -> None:
    requests = upstream(_baidu_point_response(30.58, 114.27, precise=0))

    result = await context.estimate_commute("武汉", "武汉站", "武汉", api_key="test")

    assert result["status"] == "needs_selection"
    assert result["items"] == []
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "error_code"), [(3, "permission_denied"), (4, "rate_limited")])
async def test_commute_exposes_provider_permission_and_quota(upstream, status, error_code) -> None:
    requests = upstream(httpx.Response(200, json={"status": status}))

    result = await context.estimate_commute("机场", "酒店", "武汉", api_key="test")

    assert result["status"] == "error"
    assert result["error_code"] == error_code
    assert len(requests) == 1


_HONGQIAO_STATION = {
    "name": "上海虹桥站",
    "city": "上海市",
    "uid": "hongqiao-station",
    "location": {"lat": 31.2007, "lng": 121.327},
}


def _baidu_place_response(*places: dict[str, Any], total: int | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": 0,
            "result_type": "poi_type",
            "total": len(places) if total is None else total,
            "results": places,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("station", ["上海虹桥站", "虹桥站"])
async def test_commute_uses_unique_same_city_poi_after_fuzzy_geocode(
    upstream,
    caplog,
    station,
) -> None:
    caplog.set_level(logging.INFO, logger="httpx")
    requests = upstream(
        _baidu_point_response(31.23, 121.47, precise=0),
        _baidu_place_response(
            _HONGQIAO_STATION,
            {**_HONGQIAO_STATION, "name": "上海虹桥站-到达层", "uid": "arrival"},
        ),
        _baidu_point_response(31.19, 121.70),
        httpx.Response(
            200, json={"status": 0, "result": {"routes": [{"duration": 1501, "distance": 34200}]}}
        ),
    )
    key = "baidu-poi-contract-key"

    result = await context.estimate_commute(station, "道路1号", "上海", api_key=key)

    assert result["status"] == "ok"
    assert result["items"][0]["duration_minutes"] == 26
    _, search, _, route = requests
    assert search.url.path == "/place/v3/region"
    assert search.url.params["query"] == station
    assert search.url.params["region"] == "上海"
    assert search.url.params["region_limit"] == "true"
    assert search.url.params["page_num"] == "0"
    assert search.url.params["page_size"] == "20"
    assert route.url.params["origin"] == "31.2007,121.327"
    assert route.url.params["coord_type"] == "bd09ll"
    assert search.url.params["ak"] == key
    assert key not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        pytest.param(
            _baidu_place_response(_HONGQIAO_STATION, {**_HONGQIAO_STATION, "uid": "other"}),
            id="same-name-duplicate",
        ),
        pytest.param(
            _baidu_place_response({**_HONGQIAO_STATION, "city": "苏州市"}),
            id="wrong-city",
        ),
        pytest.param(
            _baidu_place_response({**_HONGQIAO_STATION, "name": "上海虹桥站-到达层"}),
            id="nearby-name",
        ),
        pytest.param(_baidu_place_response(_HONGQIAO_STATION, total=23), id="unseen-page"),
        pytest.param(
            _baidu_place_response({**_HONGQIAO_STATION, "location": None}), id="no-coordinates"
        ),
    ],
)
async def test_commute_needs_selection_when_poi_cannot_be_verified(upstream, response) -> None:
    requests = upstream(_baidu_point_response(31.23, 121.47, precise=0), response)

    result = await context.estimate_commute("上海虹桥站", "道路1号", "上海", api_key="test")

    assert result["status"] == "needs_selection"
    assert result["items"] == []
    assert [request.url.path for request in requests] == ["/geocoding/v3/", "/place/v3/region"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_code"), [(4, "rate_limited"), (240, "permission_denied")]
)
async def test_commute_stops_if_poi_search_is_unavailable(upstream, status, error_code) -> None:
    requests = upstream(
        _baidu_point_response(31.23, 121.47, precise=0),
        httpx.Response(200, json={"status": status}),
    )

    result = await context.estimate_commute("上海虹桥站", "道路1号", "上海", api_key="test")

    assert result["status"] == "error"
    assert result["error_code"] == error_code
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["机场", "火车站", "上海市申虹路100号"])
async def test_commute_does_not_search_for_generic_poi_or_address(upstream, origin) -> None:
    requests = upstream(_baidu_point_response(31.23, 121.47, precise=0))

    result = await context.estimate_commute(origin, "上海虹桥站", "上海", api_key="test")

    assert result["status"] == "needs_selection"
    assert len(requests) == 1
