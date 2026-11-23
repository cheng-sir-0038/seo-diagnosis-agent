from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import pytest

from src.travel_agent import flyai

_TRIP_DATE = date(2026, 12, 8)
_CHECK_OUT = date(2026, 12, 9)
_KEY = "local-test-only-flyai-key"


def _response(*rows: dict) -> dict:
    return {"status": 0, "data": {"itemList": list(rows)}}


def _trip(
    number: str,
    *,
    adult_price: str | None = None,
    ticket_price: float | None = None,
) -> dict:
    return {
        "adultPrice": adult_price,
        "ticketPrice": ticket_price,
        "journeys": [
            {
                "segments": [
                    {
                        "marketingTransportNo": number,
                        "depStationName": "杭州东",
                        "arrStationName": "上海虹桥",
                        "depDateTime": "2026-12-08 08:00:00",
                        "arrDateTime": "2026-12-08 08:58:00",
                        "seatClassName": "二等座",
                    }
                ],
            }
        ],
        "jumpUrl": "https://www.fliggy.com/example",
    }


@pytest.fixture
def cli(tmp_path: Path) -> Callable[[str], str]:
    def create(body: str) -> str:
        path = tmp_path / "flyai"
        path.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys, time\n"
            f"assert os.environ.get('FLYAI_API_KEY') == {_KEY!r}\n"
            f"assert {_KEY!r} not in sys.argv\n"
            "assert 'DEEPSEEK_API_KEY' not in os.environ\n"
            "assert 'DEBUG_FLYAI_MCP_URL' not in os.environ\n"
            "command, *args = sys.argv[1:]\n" + body,
        )
        path.chmod(0o755)
        return str(path)

    return create


async def test_transport_reads_documented_train_and_observed_flight_fields(cli, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "isolated-test-value")
    monkeypatch.setenv("DEBUG_FLYAI_MCP_URL", "https://example.com")
    rail = _trip("G1234", adult_price="¥225.00")
    flight = _trip("MU1234", ticket_price=1112.00)
    flight["journeys"][0]["segments"][0]["seatClassName"] = "经济舱"
    responses = {"search-train": _response(rail), "search-flight": _response(flight)}
    executable = cli(
        "assert args == ['--origin=杭州', '--destination=上海', '--dep-date=2026-12-08']\n"
        f"print({{key: json.dumps(value) for key, value in {responses!r}.items()}}[command])"
    )

    result = await flyai.search_transport(
        "杭州", "上海", _TRIP_DATE, api_key=_KEY, executable=executable
    )

    assert result["status"] == "ok"
    assert datetime.fromisoformat(result["queried_at"]).tzinfo is not None
    rail_item, flight_item = result["items"]
    assert rail_item["name"] == "G1234"
    assert rail_item["mode"] == "rail"
    assert rail_item["departs_at"] == "2026-12-08T08:00:00+08:00"
    assert rail_item["arrives_at"] == "2026-12-08T08:58:00+08:00"
    assert (rail_item["amount"], rail_item["currency"], rail_item["price_basis"]) == (
        "225.00",
        "CNY",
        "person",
    )
    assert flight_item["name"] == "MU1234"
    assert flight_item["mode"] == "flight"
    assert flight_item["from_stop"] == "杭州东"
    assert flight_item["fare_class"] == "经济舱"
    assert flight_item["departs_at"] is None
    assert flight_item["departs_local"] == "2026-12-08 08:00:00"
    assert flight_item["amount"] == "1112.0"
    assert flight_item["currency"] is None
    assert flight_item["price_basis"] is None
    assert all(not item["full_price"] for item in result["items"])


async def test_train_keeps_price_without_inventing_a_currency_or_basis(cli):
    rail = {**_trip("G1234"), "price": "354.00"}
    executable = cli(
        f"data = {{'search-train': {_response(rail)!r}, 'search-flight': {_response()!r}}}\n"
        "print(json.dumps(data[command]))"
    )

    receipt = await flyai.search_transport(
        "杭州",
        "上海",
        _TRIP_DATE,
        api_key=_KEY,
        executable=executable,
    )

    train = receipt["items"][0]
    assert train["price_as_returned"] == "354.00"
    assert train["amount"] == "354.00"
    assert train["currency"] is None and train["price_basis"] is None


async def test_cli_reads_complete_json_beyond_pipe_capacity(cli):
    executable = cli(
        "import stat\n"
        "assert stat.S_ISREG(os.fstat(sys.stdout.fileno()).st_mode)\n"
        f"receipt = {_response(_trip('G1234'))!r}\n"
        "receipt['note'] = 'a' * 16000\n"
        "print(json.dumps(receipt))"
    )
    receipt = await flyai.search_transport(
        "杭州",
        "上海",
        _TRIP_DATE,
        api_key=_KEY,
        executable=executable,
    )
    assert receipt["status"] == "ok"
    assert len(receipt["items"]) == 2


async def test_transport_preserves_partial_results_and_false_shell_flags(cli):
    origin = "--origin; echo literal"
    executable = cli(
        f"assert args[0] == {f'--origin={origin}'!r}\n"
        "if command == 'search-flight':\n"
        "    print('MCP HTTP 403: ' + os.environ['FLYAI_API_KEY'], file=sys.stderr)\n"
        "    sys.exit(1)\n"
        f"print({json.dumps(_response(_trip('G1234')), ensure_ascii=False)!r})"
    )

    result = await flyai.search_transport(
        origin, "上海", _TRIP_DATE, api_key=_KEY, executable=executable
    )

    assert result["status"] == "ok"
    assert [item["mode"] for item in result["items"]] == ["rail"]
    assert result["modes"]["flight"]["status"] == "error"
    assert "权限" in result["modes"]["flight"]["message"]
    assert _KEY not in str(result)


async def test_hotel_dates_scope_and_reference_price(cli):
    rows = [
        {
            "shId": str(i),
            "name": f"酒店 {i}",
            "detailUrl": "https://www.fliggy.com/example" if i == 0 else "javascript:alert(1)",
            "address": "环城西路2号",
            "price": "¥618",
        }
        for i in range(5)
    ]
    executable = cli(
        "assert command == 'search-hotel'\n"
        "assert args == ['--dest-name=杭州', '--check-in-date=2026-12-08', "
        "'--check-out-date=2026-12-09']\n"
        f"print({json.dumps(_response(*rows), ensure_ascii=False)!r})"
    )

    result = await flyai.search_hotels(
        "杭州", _TRIP_DATE, _CHECK_OUT, travelers=3, rooms=2, api_key=_KEY, executable=executable
    )

    assert result["status"] == "ok"
    assert len(result["items"]) == 3
    item = result["items"][0]
    assert item["booking_url"] == "https://www.fliggy.com/example"
    assert item["address"] == "环城西路2号"
    assert (item["check_in"], item["check_out"]) == ("2026-12-08", "2026-12-09")
    assert item["amount"] == "618"
    assert item["currency"] == "CNY"
    assert item["price_basis"] is None and item["room_type"] is None
    assert item["full_price"] is False and item["party_size_confirmed"] is False
    assert result["items"][1]["booking_url"] is None
    assert result["scope"]["travelers_requested"] == 3
    assert result["scope"]["rooms_requested"] == 2


@pytest.mark.parametrize(
    ("body", "expected", "message_part"),
    [
        ("print(json.dumps({'status': 0, 'data': {'itemList': []}}))", "no_results", "候选"),
        ("print(json.dumps({'status': 0, 'data': {'itemList': [{}]}}))", "error", "格式"),
        ("print(json.dumps({'status': 0, 'data': {}}))", "error", "格式"),
        ("print(os.environ['FLYAI_API_KEY'])", "error", "格式"),
        ("print('SentinelBlockException ' + os.environ['FLYAI_API_KEY'])", "error", "查询频率"),
        (
            "print(json.dumps({'status': 401, 'message': os.environ['FLYAI_API_KEY']}))",
            "error",
            "权限",
        ),
        ("print('x' * 2000000)", "error", "大小"),
        ("time.sleep(5)", "error", "超时"),
    ],
)
async def test_empty_invalid_and_failed_responses(cli, body, expected, message_part, monkeypatch):
    if message_part == "超时":
        monkeypatch.setattr(flyai, "_TIMEOUT_SECONDS", 0.5)

    result = await flyai.search_hotels(
        "杭州", _TRIP_DATE, _CHECK_OUT, travelers=1, rooms=1, api_key=_KEY, executable=cli(body)
    )

    assert result["status"] == expected
    assert result["items"] == []
    assert message_part in result["message"]
    assert _KEY not in str(result)


@pytest.mark.parametrize(("key", "message_part"), [("", "密钥"), (_KEY, "CLI")])
async def test_transport_missing_configuration_has_no_query_time(tmp_path, key, message_part):
    result = await flyai.search_transport(
        "杭州", "上海", _TRIP_DATE, api_key=key, executable=str(tmp_path / "missing-flyai")
    )
    assert result["status"] == "unconfigured"
    assert result["queried_at"] is None
    assert result["items"] == []
    assert message_part in result["message"]
