"""The ARR column counts an airborne inbound exactly when the ETA the flight board shows is inside max_eta_hours."""

from typing import Any, cast

import pytest

from airport_disambiguator import AirportDisambiguator
from backend.cache.manager import load_aircraft_approach_speeds
from backend.core import analysis, flights
from backend.core.calculations import calculate_eta, haversine_distance_nm
from backend.core.flights import get_airport_flight_details

UNIFIED_AIRPORT_DATA: dict[str, dict[str, Any]] = {
    "LFBO": {"latitude": 43.6291007996, "longitude": 1.3638199568, "country": "FR"},
    "LFPG": {"latitude": 49.0127983093, "longitude": 2.5499999523, "country": "FR"},
    "KDEN": {
        "latitude": 39.8616,
        "longitude": -104.6731,
        "country": "US",
        "elevation": 5431,
    },
    "KORD": {
        "latitude": 41.9786,
        "longitude": -87.9048,
        "country": "US",
        "elevation": 680,
    },
}
SPEEDS = load_aircraft_approach_speeds("data/aircraft_data.csv")

# 440 nm north of LFBO at 460 kt: 0.957 h over the great circle, past 1 h once descent and the 250 kt limit count.
CRUISING_PAST_THE_WINDOW: dict[str, Any] = {
    "callsign": "BAW1",
    "latitude": 50.9624,
    "longitude": 1.3638,
    "groundspeed": 460,
    "altitude": 37000,
    "flight_plan": {
        "departure": "EGLL",
        "arrival": "LFBO",
        "aircraft_short": "A320",
        "route": "",
    },
}

ON_DESCENT_INTO_LFBO: dict[str, Any] = {
    "callsign": "AFR2",
    "latitude": 44.2291,
    "longitude": 1.3638,
    "groundspeed": 250,
    "altitude": 9000,
    "flight_plan": {
        "departure": "LFPG",
        "arrival": "LFBO",
        "aircraft_short": "A320",
        "route": "",
    },
}

# 30 nm east of KDEN, still above 10,000 ft: the field's 5,431 ft elevation shortens the descent.
DESCENDING_INTO_KDEN: dict[str, Any] = {
    "callsign": "UAL3",
    "latitude": 39.8616,
    "longitude": -104.0216,
    "groundspeed": 300,
    "altitude": 11949,
    "flight_plan": {
        "departure": "KORD",
        "arrival": "KDEN",
        "aircraft_short": "B738",
        "route": "",
    },
}


class _NoDisplayNames:
    def get_full_names_batch(self, _icaos: list[str]) -> dict[str, str]:
        return {}


def _board_eta_hours(pilot: dict[str, Any]) -> float:
    flight = {**pilot, "arrival": pilot["flight_plan"]["arrival"]}
    return calculate_eta(flight, UNIFIED_AIRPORT_DATA, SPEEDS)[2]


def _row(
    monkeypatch: pytest.MonkeyPatch,
    icao: str,
    pilots: list[dict[str, Any]],
    max_eta_hours: float,
) -> Any:
    feed = {"general": {}, "pilots": pilots, "controllers": [], "atis": []}
    monkeypatch.setattr(analysis, "download_vatsim_data", lambda: feed)
    airport_rows, _, _, _, _ = analysis.analyze_flights_data(
        max_eta_hours=max_eta_hours,
        airport_allowlist=[icao],
        include_all_arriving=True,
        unified_airport_data=UNIFIED_AIRPORT_DATA,
        disambiguator=cast(AirportDisambiguator, _NoDisplayNames()),
    )
    assert airport_rows is not None
    (row,) = [r for r in airport_rows if r.icao == icao]
    return row


def test_arr_leaves_out_an_inbound_whose_eta_is_past_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lfbo = UNIFIED_AIRPORT_DATA["LFBO"]
    great_circle_hours = (
        haversine_distance_nm(
            CRUISING_PAST_THE_WINDOW["latitude"],
            CRUISING_PAST_THE_WINDOW["longitude"],
            lfbo["latitude"],
            lfbo["longitude"],
        )
        / CRUISING_PAST_THE_WINDOW["groundspeed"]
    )
    assert great_circle_hours <= 1.0 < _board_eta_hours(CRUISING_PAST_THE_WINDOW)

    row = _row(
        monkeypatch,
        "LFBO",
        [CRUISING_PAST_THE_WINDOW, ON_DESCENT_INTO_LFBO],
        max_eta_hours=1.0,
    )

    assert (row.arrivals, row.arrivals_all) == (1, 2), (
        f"ARR {row.arrivals} counts BAW1, whose ETA on the board is {_board_eta_hours(CRUISING_PAST_THE_WINDOW):.3f} h"
    )


def test_next_eta_matches_the_board_at_a_high_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row(monkeypatch, "KDEN", [DESCENDING_INTO_KDEN], max_eta_hours=1.0)
    _, board_arrivals = get_airport_flight_details(
        "KDEN",
        None,
        UNIFIED_AIRPORT_DATA,
        SPEEDS,
        {"pilots": [DESCENDING_INTO_KDEN]},
    )
    (board_row,) = board_arrivals

    assert row.next_eta == board_row.eta_display


def test_arrival_eta_in_window_skips_calculate_eta_past_the_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lfbo = UNIFIED_AIRPORT_DATA["LFBO"]
    bound_hours = (
        haversine_distance_nm(
            CRUISING_PAST_THE_WINDOW["latitude"],
            CRUISING_PAST_THE_WINDOW["longitude"],
            lfbo["latitude"],
            lfbo["longitude"],
        )
        - 5.0
    ) / CRUISING_PAST_THE_WINDOW["groundspeed"]
    assert bound_hours > 0.5

    # Both paths carry the arrival at the top level, as filter_flights_by_airports leaves it
    inflight = {**CRUISING_PAST_THE_WINDOW, "arrival": "LFBO"}
    calls: list[str] = []
    patched_eta = ("9m", "09:00", 0.15)

    def _counting_calculate_eta(
        flight: dict[str, Any],
        airports: dict[str, dict[str, Any]],
        aircraft_approach_speeds: dict[str, int] | None = None,
    ) -> tuple[str, str, float]:
        calls.append(flight["callsign"])
        return patched_eta

    monkeypatch.setattr(flights, "calculate_eta", _counting_calculate_eta)

    assert (
        flights.arrival_eta_in_window(inflight, UNIFIED_AIRPORT_DATA, SPEEDS, 0.5)
        is None
    ), "the bound (great circle - 5 nm) / groundspeed is already past a 0.5 h window"
    assert calls == []

    assert (
        flights.arrival_eta_in_window(inflight, UNIFIED_AIRPORT_DATA, SPEEDS, 0)
        == patched_eta
    )
    assert calls == ["BAW1"]
