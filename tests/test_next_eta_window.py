"""NEXT ETA comes only from the inbound flights the ARR column counts."""

from typing import Any, cast

import pytest

from airport_disambiguator import AirportDisambiguator
from backend.cache.manager import load_aircraft_approach_speeds
from backend.core import analysis
from backend.core.calculations import calculate_eta

UNIFIED_AIRPORT_DATA: dict[str, dict[str, Any]] = {
    "LFBO": {"latitude": 43.6291007996, "longitude": 1.3638199568, "country": "FR"},
    "YMML": {"latitude": -37.6733016968, "longitude": 144.8430023193, "country": "AU"},
}

CLIMBING_OUT_OF_YMML: dict[str, Any] = {
    "callsign": "AIB1",
    "latitude": -37.59,
    "longitude": 144.843,
    "groundspeed": 175,
    "altitude": 2500,
    "flight_plan": {
        "departure": "YMML",
        "arrival": "LFBO",
        "aircraft_short": "A35K",
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
        "departure": "LFPO",
        "arrival": "LFBO",
        "aircraft_short": "A320",
        "route": "",
    },
}


class _NoDisplayNames:
    def get_full_names_batch(self, _icaos: list[str]) -> dict[str, str]:
        return {}


def _expected_eta_display(pilot: dict[str, Any]) -> str:
    flight = {
        **pilot,
        "departure": pilot["flight_plan"]["departure"],
        "arrival": "LFBO",
    }
    airports = analysis.load_airport_data(UNIFIED_AIRPORT_DATA)
    speeds = load_aircraft_approach_speeds("data/aircraft_data.csv")
    display, _, _ = calculate_eta(flight, airports, speeds)
    return display


def _lfbo_row(
    monkeypatch: pytest.MonkeyPatch, pilots: list[dict[str, Any]], max_eta_hours: float
) -> Any:
    feed = {"general": {}, "pilots": pilots, "controllers": [], "atis": []}
    monkeypatch.setattr(analysis, "download_vatsim_data", lambda: feed)
    airport_rows, _, _, _, _ = analysis.analyze_flights_data(
        max_eta_hours=max_eta_hours,
        airport_allowlist=["LFBO"],
        include_all_arriving=True,
        unified_airport_data=UNIFIED_AIRPORT_DATA,
        disambiguator=cast(AirportDisambiguator, _NoDisplayNames()),
    )
    assert airport_rows is not None
    (row,) = [r for r in airport_rows if r.icao == "LFBO"]
    return row


def test_next_eta_is_blank_when_the_only_inbound_is_beyond_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _lfbo_row(monkeypatch, [CLIMBING_OUT_OF_YMML], max_eta_hours=1.0)
    assert (row.arrivals, row.arrivals_all) == (0, 1)
    assert row.next_eta == "", (
        f"NEXT ETA {row.next_eta!r} comes from a flight the ARR column does not count"
    )


def test_next_eta_comes_from_the_inbound_inside_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _lfbo_row(
        monkeypatch, [CLIMBING_OUT_OF_YMML, ON_DESCENT_INTO_LFBO], max_eta_hours=1.0
    )
    assert (row.arrivals, row.arrivals_all) == (1, 2)
    assert row.next_eta == _expected_eta_display(ON_DESCENT_INTO_LFBO)


def test_next_eta_shows_every_airborne_inbound_when_the_window_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _lfbo_row(monkeypatch, [CLIMBING_OUT_OF_YMML], max_eta_hours=0)
    assert (row.arrivals, row.arrivals_all) == (1, 1)
    assert row.next_eta == _expected_eta_display(CLIMBING_OUT_OF_YMML)
