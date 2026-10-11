"""Characterization of the diversion airport search.

Pins ``find_suitable_diversions`` and ``find_nearby_airports``. The data loaders
(runway lengths, runway summaries, approach lists, the required runway length and
the spatial index) are faked at the module boundary; ``bearing_to_compass`` and
``calculate_bearing`` stay real, so the compass string and the bearing of each
result come from the production code.
"""

from typing import Any

import pytest

from backend.core import diversions
from backend.core.diversions import DiversionFilters, DiversionOption

AIRPORTS: dict[str, dict[str, Any]] = {
    "KJFK": {"name": "John F. Kennedy International Airport"},
    "KLAX": {"name": "Los Angeles International Airport"},
    "KSFO": {"name": "San Francisco International Airport"},
    "KOAK": {"name": "Oakland International Airport"},
}


@pytest.fixture
def loaders(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake the loaders the search calls and record how it calls them."""
    state: dict[str, Any] = {
        "required": 5000,
        "required_calls": [],
        "runways": {},
        "summaries": {},
        "approaches": {},
        "nearby": [],
        "nearby_calls": [],
    }

    def fake_required(aircraft_type: str) -> int:
        state["required_calls"].append(aircraft_type)
        return state["required"]

    def fake_nearby(lat, lon, airports_data, radius_nm=100.0, max_results=500):
        state["nearby_calls"].append((lat, lon, airports_data, radius_nm, max_results))
        return state["nearby"]

    def fake_longest(icao: str, open_only: bool = True) -> int | None:
        return state["runways"].get(icao)

    def fake_summary(icao: str) -> str | None:
        return state["summaries"].get(icao)

    def fake_approaches(icao: str) -> list[str]:
        return list(state["approaches"].get(icao, []))

    monkeypatch.setattr(diversions, "get_required_runway_length", fake_required)
    monkeypatch.setattr(diversions, "find_nearby_airports", fake_nearby)
    monkeypatch.setattr(diversions, "get_longest_runway", fake_longest)
    monkeypatch.setattr(diversions, "get_runway_summary", fake_summary)
    monkeypatch.setattr(diversions, "get_approach_list_for_airport", fake_approaches)
    return state


def test_no_candidates(loaders: dict[str, Any]) -> None:
    assert diversions.find_suitable_diversions(40.0, -73.0, "B738", AIRPORTS) == []


def test_search_arguments(loaders: dict[str, Any]) -> None:
    diversions.find_suitable_diversions(40.0, -73.0, "B738", AIRPORTS, radius_nm=150.0)
    assert loaders["nearby_calls"] == [(40.0, -73.0, AIRPORTS, 150.0, 500)]


def test_required_runway_length_is_requested_once(loaders: dict[str, Any]) -> None:
    diversions.find_suitable_diversions(40.0, -73.0, "B738", AIRPORTS)
    assert loaders["required_calls"] == ["B738"]


def test_no_aircraft_type_skips_the_required_runway_lookup(
    loaders: dict[str, Any],
) -> None:
    loaders["nearby"] = [("KJFK", 10.0, 0.0)]
    result = diversions.find_suitable_diversions(40.0, -73.0, "", AIRPORTS)
    assert loaders["required_calls"] == []
    assert [option.icao for option in result] == ["KJFK"]


def test_zero_required_runway_skips_the_runway_filter(loaders: dict[str, Any]) -> None:
    loaders["required"] = 0
    loaders["nearby"] = [("KJFK", 10.0, 0.0)]
    result = diversions.find_suitable_diversions(40.0, -73.0, "B738", AIRPORTS)
    assert [option.icao for option in result] == ["KJFK"]
    assert result[0].longest_runway_ft is None


@pytest.mark.parametrize("longest", [None, 4000])
def test_short_or_missing_runway_is_rejected(loaders: dict[str, Any], longest) -> None:
    loaders["runways"] = {"KJFK": longest}
    loaders["nearby"] = [("KJFK", 10.0, 0.0)]
    assert diversions.find_suitable_diversions(40.0, -73.0, "B738", AIRPORTS) == []


def test_runway_at_the_requirement_is_kept(loaders: dict[str, Any]) -> None:
    loaders["runways"] = {"KJFK": 5000}
    loaders["nearby"] = [("KJFK", 10.0, 0.0)]
    result = diversions.find_suitable_diversions(40.0, -73.0, "B738", AIRPORTS)
    assert [option.icao for option in result] == ["KJFK"]


def test_option_fields(loaders: dict[str, Any]) -> None:
    loaders["runways"] = {"KJFK": 12000}
    loaders["summaries"] = {"KJFK": "12000ft (04L/22R)"}
    loaders["approaches"] = {"KJFK": ["ILS RWY 04L", "RNAV (GPS) Z RWY 22R"]}
    loaders["nearby"] = [("KJFK", 12.34, 90.0)]
    result = diversions.find_suitable_diversions(
        40.0,
        -73.0,
        "B738",
        AIRPORTS,
        weather_data={"KJFK": ("VFR", "10SM BKN050")},
        controller_data={"KJFK": ["TWR", "APP"]},
    )
    assert result == [
        DiversionOption(
            icao="KJFK",
            name="John F. Kennedy International Airport",
            distance_nm=12.3,
            bearing_deg=90.0,
            bearing_compass="E",
            longest_runway_ft=12000,
            runway_summary="12000ft (04L/22R)",
            approaches=["ILS RWY 04L", "RNAV (GPS) Z RWY 22R"],
            has_approaches=True,
            weather_category="VFR",
            weather_details="10SM BKN050",
            staffed_positions=["TWR", "APP"],
            is_staffed=True,
        )
    ]


def test_name_falls_back_to_the_icao(loaders: dict[str, Any]) -> None:
    loaders["nearby"] = [("KZZZ", 5.0, 0.0)]
    result = diversions.find_suitable_diversions(40.0, -73.0, "", AIRPORTS)
    assert result[0].name == "KZZZ"


def test_require_approaches_filters_out_airports_without_any(
    loaders: dict[str, Any],
) -> None:
    loaders["nearby"] = [("KJFK", 10.0, 0.0), ("KLAX", 20.0, 90.0)]
    loaders["approaches"] = {"KLAX": ["ILS RWY 25L"]}
    filters = DiversionFilters(require_runway_capability=False, require_approaches=True)
    result = diversions.find_suitable_diversions(
        40.0, -73.0, "B738", AIRPORTS, filters=filters
    )
    assert [option.icao for option in result] == ["KLAX"]
    assert result[0].has_approaches is True


def test_require_good_weather_keeps_vfr_mvfr_and_unknown(
    loaders: dict[str, Any],
) -> None:
    loaders["nearby"] = [
        ("KJFK", 10.0, 0.0),
        ("KLAX", 20.0, 90.0),
        ("KSFO", 30.0, 180.0),
        ("KOAK", 40.0, 270.0),
    ]
    weather = {
        "KJFK": ("IFR", "2SM OVC005"),
        "KLAX": ("MVFR", "5SM BKN020"),
        "KSFO": ("LIFR", "1/2SM FG"),
    }
    filters = DiversionFilters(
        require_runway_capability=False, require_good_weather=True
    )
    result = diversions.find_suitable_diversions(
        40.0, -73.0, "B738", AIRPORTS, filters=filters, weather_data=weather
    )
    assert [option.icao for option in result] == ["KLAX", "KOAK"]
    assert result[0].weather_category == "MVFR"


def test_require_staffed_keeps_only_airports_with_positions(
    loaders: dict[str, Any],
) -> None:
    loaders["nearby"] = [
        ("KJFK", 10.0, 0.0),
        ("KLAX", 20.0, 90.0),
        ("KSFO", 30.0, 180.0),
    ]
    controllers = {"KLAX": ["TWR"], "KSFO": []}
    filters = DiversionFilters(require_runway_capability=False, require_staffed=True)
    result = diversions.find_suitable_diversions(
        40.0, -73.0, "B738", AIRPORTS, filters=filters, controller_data=controllers
    )
    assert [option.icao for option in result] == ["KLAX"]
    assert result[0].is_staffed is True
    assert result[0].staffed_positions == ["TWR"]


def test_max_results_caps_the_list(loaders: dict[str, Any]) -> None:
    loaders["nearby"] = [
        ("KJFK", 10.0, 0.0),
        ("KLAX", 20.0, 90.0),
        ("KSFO", 30.0, 180.0),
    ]
    result = diversions.find_suitable_diversions(
        40.0, -73.0, "", AIRPORTS, max_results=2
    )
    assert [option.icao for option in result] == ["KJFK", "KLAX"]


@pytest.mark.parametrize("max_results", [0, -1])
def test_max_results_zero_or_negative_returns_nothing(
    loaders: dict[str, Any], max_results: int
) -> None:
    loaders["nearby"] = [
        ("KJFK", 10.0, 0.0),
        ("KLAX", 20.0, 90.0),
        ("KSFO", 30.0, 180.0),
    ]
    result = diversions.find_suitable_diversions(
        40.0, -73.0, "", AIRPORTS, max_results=max_results
    )
    assert result == []


def test_candidate_order_is_kept(loaders: dict[str, Any]) -> None:
    loaders["nearby"] = [
        ("KSFO", 30.0, 180.0),
        ("KJFK", 10.0, 0.0),
        ("KLAX", 20.0, 90.0),
    ]
    result = diversions.find_suitable_diversions(40.0, -73.0, "", AIRPORTS)
    assert [option.icao for option in result] == ["KSFO", "KJFK", "KLAX"]


def test_default_filters_are_used_when_none_is_passed(loaders: dict[str, Any]) -> None:
    loaders["runways"] = {"KJFK": 4000}
    loaders["nearby"] = [("KJFK", 10.0, 0.0)]
    assert diversions.find_suitable_diversions(40.0, -73.0, "B738", AIRPORTS) == []
    assert (
        diversions.find_suitable_diversions(
            40.0, -73.0, "B738", AIRPORTS, filters=DiversionFilters()
        )
        == []
    )


class _FakeIndex:
    """A spatial index answering with a fixed list of (icao, distance) pairs."""

    def __init__(self, found: list[tuple[str, float]]) -> None:
        self._found = found
        self.calls: list[tuple[float, float, float]] = []

    def find_within_distance(self, lat, lon, radius_nm):
        self.calls.append((lat, lon, radius_nm))
        return self._found


def test_nearby_airports_skips_airports_without_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _FakeIndex([("KJFK", 5.0), ("KNOW", 6.0), ("KLAX", 7.0), ("KZZZ", 8.0)])
    monkeypatch.setattr(diversions, "get_airport_spatial_index", lambda data: index)
    airports_data = {
        "KJFK": {"latitude": 0.0, "longitude": 1.0},
        "KNOW": {"latitude": None, "longitude": 0.0},
        "KLAX": {"latitude": 0.0},
    }
    result = diversions.find_nearby_airports(0.0, 0.0, airports_data, radius_nm=25.0)
    assert index.calls == [(0.0, 0.0, 25.0)]
    assert len(result) == 1
    icao, distance, bearing = result[0]
    assert (icao, distance) == ("KJFK", 5.0)
    assert bearing == pytest.approx(90.0)


def test_nearby_airports_limits_to_max_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _FakeIndex([("KJFK", 5.0), ("KLAX", 6.0), ("KSFO", 7.0)])
    monkeypatch.setattr(diversions, "get_airport_spatial_index", lambda data: index)
    airports_data = {
        "KJFK": {"latitude": 0.0, "longitude": 1.0},
        "KLAX": {"latitude": 0.0, "longitude": 2.0},
        "KSFO": {"latitude": 0.0, "longitude": 3.0},
    }
    result = diversions.find_nearby_airports(0.0, 0.0, airports_data, max_results=2)
    assert [icao for icao, _, _ in result] == ["KJFK", "KLAX"]


def test_nearby_airports_limit_counts_only_airports_with_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _FakeIndex([("KJFK", 5.0), ("KNOW", 6.0), ("KLAX", 7.0), ("KSFO", 8.0)])
    monkeypatch.setattr(diversions, "get_airport_spatial_index", lambda data: index)
    airports_data = {
        "KJFK": {"latitude": 0.0, "longitude": 1.0},
        "KNOW": {"latitude": None, "longitude": 0.0},
        "KLAX": {"latitude": 0.0, "longitude": 2.0},
        "KSFO": {"latitude": 0.0, "longitude": 3.0},
    }
    result = diversions.find_nearby_airports(0.0, 0.0, airports_data, max_results=2)
    assert [icao for icao, _, _ in result] == ["KJFK", "KLAX"]
