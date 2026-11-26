from datetime import date

import pytest
from pydantic import ValidationError

from src.travel_agent.plan import TripRequest, build_travel_plan


def _transport(
    offer_id,
    *,
    local_day="01",
    mode="rail",
    price="150.50",
    **changes,
):
    return {
        "id": offer_id,
        "name": offer_id,
        "source": "user",
        "mode": mode,
        "from_stop": "武汉站",
        "to_stop": "长沙南站",
        "departs_at": f"2027-01-{local_day}T08:00:00+08:00",
        "arrives_at": f"2027-01-{local_day}T09:00:00+08:00",
        "fare_class": "二等座",
        "currency": "CNY",
        "amount": price,
        "price_basis": "person",
        "full_price": True,
    } | changes


def _hotel(**changes):
    return {
        "id": "hotel",
        "name": "会议酒店",
        "address": "长沙市会场附近",
        "room_type": "双床房",
        "check_in": "2027-01-01",
        "check_out": "2027-01-03",
        "currency": "CNY",
        "amount": "198.80",
        "price_basis": "night",
        "full_price": True,
    } | changes


def _request(**changes):
    return TripRequest.model_validate(
        {
            "origin": "武汉",
            "destination": "长沙",
            "depart_on": "2027-01-01",
            "return_on": "2027-01-03",
            "travelers": 2,
            "rooms": 2,
        }
        | changes
    )


def _plan(
    *,
    request=None,
    outbound=None,
    inbound=None,
    hotels=None,
    commute_by_offer=None,
):
    return build_travel_plan(
        request or _request(),
        outbound=[_transport("out")] if outbound is None else outbound,
        inbound=[_transport("back", local_day="03")] if inbound is None else inbound,
        hotels=[_hotel()] if hotels is None else hotels,
        commute_by_offer=commute_by_offer or {},
    )


def _route(minutes):
    return {"status": "ok", "items": [{"mode": "driving", "duration_minutes": minutes}]}


def test_trip_request_preserves_unknowns_and_requires_an_offset():
    request = TripRequest()
    assert request.travelers is None
    assert request.rooms is None
    assert request.buffer_minutes is None
    with pytest.raises(ValidationError):
        TripRequest(arrive_by="2027-01-01T09:30:00")
    with pytest.raises(ValidationError):
        TripRequest.model_validate({"arrive_by": "1790000000"})


def test_quote_rolls_up_transport_by_passenger_and_hotel_by_room_night():
    report = _plan(request=_request(budget_cny="1300"))
    (plan,) = report["plans"]
    assert report["issues"] == []
    assert plan["known_cost"] == "1397.20"
    assert plan["cost_items"] == {
        "outbound": {"status": "included", "amount": "301.00", "currency": "CNY"},
        "return": {"status": "included", "amount": "301.00", "currency": "CNY"},
        "hotel": {"status": "included", "amount": "795.20", "currency": "CNY"},
    }
    assert plan["currency"] == "CNY"
    assert plan["complete_price"] is True
    assert plan["budget_status"] == "exceeded"


def test_day_trip_uses_two_legs_without_hotel_and_accepts_total_price():
    report = _plan(
        request=_request(return_on=date(2027, 1, 1)),
        inbound=[
            _transport(
                "back",
                departs_at="2027-01-01T17:15:00Z",
                arrives_at="2027-01-01T18:00:00Z",
                price_basis="total",
            )
        ],
        hotels=[_hotel()],
    )
    # 17:15 UTC is already January 2 in the destination; reject that return.
    (plan,) = report["plans"]
    assert plan["hotel"] is None
    assert plan["return_transport"] is None
    assert plan["known_cost"] == "301.00"
    assert not plan["complete_price"]
    assert any("返程" in item for item in plan["missing"])

    report = _plan(
        request=_request(return_on=date(2027, 1, 1)),
        inbound=[
            _transport(
                "back",
                departs_at="2027-01-01T09:15:00Z",
                arrives_at="2027-01-01T10:00:00Z",
                price_basis="total",
            )
        ],
        hotels=[_hotel()],
    )
    (plan,) = report["plans"]
    assert plan["hotel"] is None
    assert plan["return_transport"] == "back"
    assert plan["known_cost"] == "451.50"
    assert "hotel" not in plan["cost_items"]
    assert plan["complete_price"]


def test_overnight_arrival_starts_a_new_hotel_night_window():
    report = _plan(
        outbound=[_transport("out", arrives_at="2027-01-02T00:15:00+08:00")],
        hotels=[_hotel(id="earlier"), _hotel(check_in="2027-01-02")],
    )
    (plan,) = report["plans"]
    assert plan["hotel"] == "hotel"
    assert plan["known_cost"] == "999.60"
    assert plan["complete_price"]


def test_unknown_basis_or_incomplete_quote_never_becomes_a_full_cost():
    (plan,) = _plan(
        outbound=[_transport("out", full_price=False)],
        hotels=[_hotel(price_basis=None)],
    )["plans"]
    assert plan["known_cost"] == "602.00"
    assert plan["cost_items"]["outbound"] == {
        "status": "partial", "amount": "301.00", "currency": "CNY",
    }
    assert plan["cost_items"]["hotel"] == {"status": "price_unconfirmed"}
    assert not plan["complete_price"]
    assert any("去程" in item for item in plan["missing"])
    assert any("住宿" in item for item in plan["missing"])


def test_unknown_room_count_is_not_used_as_one_room():
    (plan,) = _plan(request=_request(lodging=True, rooms=None))["plans"]
    assert plan["known_cost"] == "602.00"
    assert not plan["complete_price"]
    assert any("房间" in item for item in plan["missing"])


def test_mixed_currency_cannot_be_summed_or_pass_cny_budget():
    (plan,) = _plan(
        request=_request(budget_cny=1500),
        hotels=[_hotel(currency="USD")],
    )["plans"]
    assert plan["known_cost"] is None
    assert plan["currency"] is None
    assert plan["known_costs"] == {"CNY": "602.00", "USD": "795.20"}
    assert not plan["complete_price"]
    assert plan["budget_status"] == "unconfirmed"
    assert any("币种" in warning for warning in plan["warnings"])


@pytest.mark.parametrize(
    ("arrive_by", "buffer", "route", "expected"),
    [
        ("09:30", 10, _route(20), "met"),
        ("09:29", 10, _route(20), "late"),
        ("09:30", None, _route(20), "unconfirmed"),
        ("09:30", 10, None, "unconfirmed"),
        ("08:59", 10, None, "late"),
    ],
)
def test_deadline_uses_actual_leg_plus_station_commute_and_explicit_buffer(
    arrive_by,
    buffer,
    route,
    expected,
):
    (plan,) = _plan(
        request=_request(
            venue="长沙交流会",
            arrive_by=f"2027-01-01T{arrive_by}:00+08:00",
            buffer_minutes=buffer,
        ),
        commute_by_offer={"out": route, "hotel": _route(5)},
    )["plans"]
    assert plan["arrival_status"] == expected
    assert plan["commute_minutes"] == 5
    if expected == "met":
        assert plan["arrival_commute_minutes"] == 20
        assert plan["arrival_at_venue"] == "2027-01-01T09:30:00+08:00"
    else:
        assert plan["missing"] or plan["warnings"]


def test_wall_clock_and_unavailable_route_do_not_confirm_arrival():
    (plan,) = _plan(
        request=_request(venue="会场", arrive_by="2027-01-01T09:30:00+08:00", buffer_minutes=0),
        outbound=[_transport("out", arrives_at="2027-01-01T09:00:00")],
        commute_by_offer={"out": dict(_route(15), status="unavailable")},
    )["plans"]
    assert plan["arrival_status"] == "unconfirmed"
    assert plan["commute_minutes"] is None
    assert any("到站时间" in field for field in plan["missing"])
    assert any("接驳" in field for field in plan["missing"])


def test_unpriced_mode_is_retained_and_ids_are_stable():
    outbound = [
        *(_transport(f"rail-{i}") for i in range(4)),
        _transport("flight", mode="flight", amount=None),
    ]
    plans = _plan(outbound=outbound)["plans"]
    assert 1 < len(plans) <= 3
    assert plans[0]["complete_price"]
    assert any(plan["transport"] == "flight" and not plan["complete_price"] for plan in plans)
    assert _plan(outbound=outbound[::-1])["plans"] == plans


@pytest.mark.parametrize("blocked_by", ["deadline", "budget"])
def test_known_conflicts_rank_below_unpriced_candidates(blocked_by):
    late = blocked_by == "deadline"
    request = (
        _request(arrive_by="2027-01-01T09:30:00+08:00", venue="会场", buffer_minutes=0)
        if late
        else _request(budget_cny="1300")
    )
    complete = _transport(
        "complete", arrives_at=f"2027-01-01T{'10' if late else '09'}:00:00+08:00",
    )
    partial = [_transport(f"partial-{i}", amount=None) for i in range(3)]
    routes = {offer["id"]: _route(15) for offer in partial}
    plans = _plan(request=request, outbound=[complete, partial[0]], commute_by_offer=routes)["plans"]
    assert [plan["transport"] for plan in plans] == ["partial-0", "complete"]
    if late:
        assert plans[1]["arrival_status"] == "late"
    else:
        assert plans[1]["budget_status"] == "exceeded"
    plans = _plan(request=request, outbound=[complete, *partial], commute_by_offer=routes)["plans"]
    assert len(plans) == 3 and all(not plan["complete_price"] for plan in plans)


def test_no_offer_and_invalid_return_dates_have_no_artificial_zero():
    report = _plan(outbound=[], inbound=[], hotels=[])
    assert report["plans"] == []
    assert report["issues"]
    bad_dates = _plan(request=_request(return_on=date(2026, 12, 31)))
    assert bad_dates["plans"] == []
    assert bad_dates["issues"]


def test_complete_transport_and_hotel_do_not_confirm_budget_with_unknown_ground_cost():
    (plan,) = _plan(request=_request(venue="会场", budget_cny=2000))["plans"]
    assert plan["complete_price"]
    assert plan["cost_items"]["ground"] == {"status": "price_unconfirmed"}
    assert plan["budget_status"] == "unconfirmed"
    assert any("接驳费用" in warning for warning in plan["warnings"])


def test_unknown_prices_are_not_reported_as_a_zero_cost():
    (plan,) = _plan(
        outbound=[_transport("out", amount=None)],
        inbound=[_transport("back", local_day="03", price_basis=None)],
        hotels=[_hotel(amount="NaN")],
    )["plans"]
    assert plan["known_cost"] is None
    assert plan["known_costs"] == {}
    assert not plan["complete_price"]


def test_return_must_follow_arrival_even_on_a_single_day():
    report = _plan(
        request=_request(return_on=date(2027, 1, 1)),
        inbound=[_transport("back")],
        hotels=[],
    )
    assert report["plans"] == []
    assert report["issues"]


def test_outbound_day_item_with_an_earlier_arrival_is_not_used():
    (plan,) = _plan(
        outbound=[
            _transport("out", departs_at=None, arrives_at="2026-12-31T23:00:00+08:00"),
        ]
    )["plans"]
    assert plan["transport"] is None
    assert not plan["complete_price"]



def test_one_way_transport_does_not_include_return_hotel_or_ground():
    (plan,) = _plan(request=_request(transport="one_way", lodging=False, return_on=None,
                                     arrive_by="2027-01-01T10:00:00+08:00"))["plans"]
    assert set(plan["cost_items"]) == {"outbound"}
    assert plan["return_transport"] is None and plan["hotel"] is None
    assert plan["arrival_status"] == "met"
    assert plan["complete_price"]


def test_hotel_only_does_not_require_transport():
    (plan,) = _plan(request=_request(transport="none", lodging=True, origin=None))["plans"]
    assert set(plan["cost_items"]) == {"hotel"}
    assert plan["known_cost"] == "795.20"
    assert plan["complete_price"]
