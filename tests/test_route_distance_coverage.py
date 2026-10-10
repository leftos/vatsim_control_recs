"""ETA uses a filed route only inside NASR's coverage and only when it is plausible.

NASR carries US navaids and fixes, so a route with an end outside the US would
resolve a foreign ident to its US namesake: WIA357's Filed SKB (St Kitts) lands
on a Texas VOR, inflating the ETA from 21 minutes to 30 hours.
"""

from pathlib import Path
from typing import Any

import pytest

from backend.core import route_distance
from backend.core.calculations import calculate_eta, haversine_distance_nm
from backend.data import navaids

# The WIA357 shape: TNCM to TKPK, 44 nm out, route "VECTORXZPATA/10 PJM A517 SKB".
WIA357_ROUTE = "VECTORXZPATA/10 PJM A517 SKB"

NON_US_AIRPORTS: dict[str, dict[str, Any]] = {
    "TNCM": {"latitude": 18.041, "longitude": -63.109, "country_code": "AN"},
    "TKPK": {"latitude": 17.311, "longitude": -62.719, "country_code": "KN"},
}


def _wia357(route: str) -> dict[str, Any]:
    return {
        "callsign": "WIA357",
        "latitude": 17.97131,
        "longitude": -63.06424,
        "groundspeed": 141,
        "altitude": 5026,
        "arrival": "TKPK",
        "flight_plan": {
            "departure": "TNCM",
            "arrival": "TKPK",
            "aircraft_short": "AT45",
            "route": route,
        },
    }


def test_non_us_route_does_not_inflate_eta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-US route is never resolved: WIA357's ETA is its great-circle ETA, under an hour."""
    monkeypatch.setattr(navaids, "ensure_nasr_data", lambda quiet=False: tmp_path)
    positions = {"PJM": (18.0378, -63.1183), "SKB": (33.7823, -98.4882)}
    monkeypatch.setattr(navaids, "get_waypoint_coordinates", positions.get)
    navaids.clear_navaid_cache()
    route_distance.clear_route_cache()
    try:
        with_route = calculate_eta(_wia357(WIA357_ROUTE), NON_US_AIRPORTS, None)
        without_route = calculate_eta(_wia357(""), NON_US_AIRPORTS, None)
    finally:
        navaids.clear_navaid_cache()
        route_distance.clear_route_cache()

    assert with_route[2] == without_route[2], (
        f"route ETA {with_route[2]:.2f} h against great-circle {without_route[2]:.2f} h"
    )
    assert with_route[2] < 1.0


def _airports(coordinates: dict[str, tuple[float, float]]) -> dict[str, dict[str, Any]]:
    return {
        code: {"latitude": lat, "longitude": lon, "country_code": "US"}
        for code, (lat, lon) in coordinates.items()
    }


def _flight(
    route: str, *, lat: float, lon: float, dep: str, arr: str
) -> dict[str, Any]:
    return {
        "callsign": "TEST1",
        "latitude": lat,
        "longitude": lon,
        "groundspeed": 300,
        "altitude": 3000,
        "arrival": arr,
        "flight_plan": {
            "departure": dep,
            "arrival": arr,
            "aircraft_short": "A320",
            "route": route,
        },
    }


def _eta_hours(flight: dict[str, Any], airports: dict[str, dict[str, Any]]) -> float:
    return calculate_eta(flight, airports, None)[2]


def _recording_route_distance(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the route distance with one that records its calls and returns a long route."""
    calls: list[str] = []

    def fake(
        route_string: str,
        aircraft_lat: float,
        aircraft_lon: float,
        dest_lat: float,
        dest_lon: float,
        airports: dict[str, tuple[float, float]] | None = None,
        departure: str | None = None,
        arrival: str | None = None,
    ) -> float:
        calls.append(route_string)
        return 1000.0

    monkeypatch.setattr(route_distance, "get_route_remaining_distance", fake)
    return calls


def _scaled_route_distance(monkeypatch: pytest.MonkeyPatch, factor: float) -> list[str]:
    """Replace the route distance with the great circle times factor."""
    calls: list[str] = []

    def fake(
        route_string: str,
        aircraft_lat: float,
        aircraft_lon: float,
        dest_lat: float,
        dest_lon: float,
        airports: dict[str, tuple[float, float]] | None = None,
        departure: str | None = None,
        arrival: str | None = None,
    ) -> float:
        calls.append(route_string)
        great_circle = haversine_distance_nm(
            aircraft_lat, aircraft_lon, dest_lat, dest_lon
        )
        return factor * great_circle

    monkeypatch.setattr(route_distance, "get_route_remaining_distance", fake)
    return calls


def test_us_to_non_us_route_uses_great_circle(monkeypatch: pytest.MonkeyPatch) -> None:
    """A US departure with a foreign arrival keeps the great circle; the route is never resolved."""
    calls = _recording_route_distance(monkeypatch)
    airports = _airports({"MMQT": (20.617, -100.186)})
    with_route = _flight("MEX1 MMQT", lat=32.0, lon=-110.0, dep="KSFO", arr="MMQT")
    empty = _flight("", lat=32.0, lon=-110.0, dep="KSFO", arr="MMQT")

    assert _eta_hours(with_route, airports) == _eta_hours(empty, airports)
    assert calls == []


def test_non_us_to_us_route_uses_great_circle(monkeypatch: pytest.MonkeyPatch) -> None:
    """A foreign departure with a US arrival keeps the great circle; the route is never resolved."""
    calls = _recording_route_distance(monkeypatch)
    airports = _airports({"KSFO": (37.6188, -122.375)})
    with_route = _flight("MMQT1 KSFO", lat=32.0, lon=-110.0, dep="MMQT", arr="KSFO")
    empty = _flight("", lat=32.0, lon=-110.0, dep="MMQT", arr="KSFO")

    assert _eta_hours(with_route, airports) == _eta_hours(empty, airports)
    assert calls == []


def test_us_detour_within_bound_keeps_route_distance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A US domestic detour at 1.5x the great circle still lengthens the ETA."""
    _scaled_route_distance(monkeypatch, 1.5)
    airports = _airports({"KLAX": (33.9425, -118.408)})
    with_route = _flight("HEC V21 KLAX", lat=37.0, lon=-120.0, dep="KSFO", arr="KLAX")
    empty = _flight("", lat=37.0, lon=-120.0, dep="KSFO", arr="KLAX")

    assert _eta_hours(with_route, airports) > _eta_hours(empty, airports)


def test_us_route_over_both_bounds_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A route at 3x a 150 nm great circle, 300 nm longer, falls back to the great circle."""
    _scaled_route_distance(monkeypatch, 3.0)
    airports = _airports({"KSFO": (42.5, -100.0)})
    with_route = _flight("GARBLED KSFO", lat=40.0, lon=-100.0, dep="KLAX", arr="KSFO")
    empty = _flight("", lat=40.0, lon=-100.0, dep="KLAX", arr="KSFO")

    assert _eta_hours(with_route, airports) == _eta_hours(empty, airports)


def test_us_route_over_ratio_with_small_excess_keeps_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3x a 20 nm great circle is kept: 40 nm of excess is under the 100 nm guard."""
    _scaled_route_distance(monkeypatch, 3.0)
    airports = _airports({"KSFO": (40.3333, -100.0)})
    with_route = _flight("KLAX1 KSFO", lat=40.0, lon=-100.0, dep="KLAX", arr="KSFO")
    empty = _flight("", lat=40.0, lon=-100.0, dep="KLAX", arr="KSFO")

    assert _eta_hours(with_route, airports) > _eta_hours(empty, airports)


def test_us_route_long_excess_under_ratio_keeps_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1.8x a 600 nm great circle is kept: 480 nm of excess alone is not implausible."""
    _scaled_route_distance(monkeypatch, 1.8)
    airports = _airports({"KSFO": (40.0, -100.0)})
    with_route = _flight("KLAX2 KSFO", lat=30.0, lon=-100.0, dep="KLAX", arr="KSFO")
    empty = _flight("", lat=30.0, lon=-100.0, dep="KLAX", arr="KSFO")

    assert _eta_hours(with_route, airports) > _eta_hours(empty, airports)


@pytest.mark.parametrize(
    ("ident", "expected"),
    [
        ("KSFO", True),
        ("ksfo", True),
        (" ksfo ", True),
        ("PANC", True),
        ("PHNL", True),
        ("PGUM", True),
        ("PWAK", True),
        ("PMDY", True),
        ("TJSJ", True),
        ("TIST", True),
        ("NSTU", True),
        ("NSFA", False),
        ("TNCM", False),
        ("TKPK", False),
        ("CYVR", False),
        ("EGLL", False),
        ("MMQT", False),
        ("RKSI", False),
        ("", False),
        ("SFO", False),
        ("K", False),
        (None, False),
    ],
)
def test_is_us_airport(ident: str | None, expected: bool) -> None:
    """Only a stripped, upper-cased 4-character ICAO ident with a US prefix is inside coverage."""
    from backend.core.calculations import is_us_airport

    assert is_us_airport(ident) is expected
