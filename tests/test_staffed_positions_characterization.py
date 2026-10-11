"""Characterization of the staffed-position extractor.

Pins ``get_staffed_positions`` over the call-sign shapes it accepts and rejects:
controller and ATIS stations, the implied ``K`` prefix for three-letter US
airports, unknown or malformed call-signs, the excluded frequency, and the ATIS
flag the display layer turns into "TOP-DOWN". Airport type plays no part here: a
NON-ATCT airport resolves exactly like a towered one, and the display layer is
what prints "N/A" for it.
"""

from typing import Any

import pytest

from backend.core.controllers import get_staffed_positions

AIRPORTS: dict[str, dict[str, Any]] = {
    "KJFK": {"name": "John F. Kennedy International Airport", "country_code": "US"},
    "KLAX": {"name": "Los Angeles International Airport", "country_code": "US"},
    "EGLL": {"name": "London Heathrow", "country_code": "GB"},
}


def _controller(callsign: str, frequency: str = "120.400") -> dict[str, Any]:
    return {"callsign": callsign, "frequency": frequency}


def _atis(callsign: str) -> dict[str, Any]:
    return {"callsign": callsign}


def test_empty_data() -> None:
    assert get_staffed_positions({}, AIRPORTS) == {}


def test_single_controller_position() -> None:
    data = {"controllers": [_controller("KJFK_APP")]}
    assert get_staffed_positions(data, AIRPORTS) == {"KJFK": ["APP"]}


def test_positions_follow_control_position_order() -> None:
    data = {
        "controllers": [
            _controller("KJFK_GND"),
            _controller("KJFK_TWR"),
            _controller("KJFK_APP"),
            _controller("KJFK_DEL"),
            _controller("KJFK_DEP"),
        ]
    }
    assert get_staffed_positions(data, AIRPORTS) == {
        "KJFK": ["APP", "DEP", "TWR", "GND", "DEL"]
    }


def test_duplicate_positions_collapse() -> None:
    data = {
        "controllers": [
            _controller("KJFK_TWR", "118.100"),
            _controller("KJFK_TWR", "119.100"),
        ]
    }
    assert get_staffed_positions(data, AIRPORTS) == {"KJFK": ["TWR"]}


def test_default_excluded_frequency() -> None:
    data = {"controllers": [_controller("KJFK_APP", "199.998")]}
    assert get_staffed_positions(data, AIRPORTS) == {}


def test_custom_excluded_frequency() -> None:
    data = {"controllers": [_controller("KJFK_APP", "121.900")]}
    assert get_staffed_positions(data, AIRPORTS, excluded_frequency="121.900") == {}


def test_missing_frequency_is_kept() -> None:
    data = {"controllers": [{"callsign": "KJFK_APP"}]}
    assert get_staffed_positions(data, AIRPORTS) == {"KJFK": ["APP"]}


def test_three_letter_us_callsign_gets_the_k_prefix() -> None:
    airports = {"KSFO": {"name": "San Francisco", "country_code": "US"}}
    data = {"controllers": [_controller("SFO_TWR")]}
    assert get_staffed_positions(data, airports) == {"KSFO": ["TWR"]}


def test_three_letter_prefix_is_not_added_for_a_non_us_airport() -> None:
    airports = {"KSFO": {"name": "San Francisco", "country_code": "CA"}}
    data = {"controllers": [_controller("SFO_TWR")]}
    assert get_staffed_positions(data, airports) == {}


def test_three_letter_callsign_matching_a_key_is_used_directly() -> None:
    airports = {"SFO": {"name": "Unknown SFO", "country_code": "GB"}}
    data = {"controllers": [_controller("SFO_TWR")]}
    assert get_staffed_positions(data, airports) == {"SFO": ["TWR"]}


@pytest.mark.parametrize(
    "callsign",
    ["", "_APP", "KJFK_", "A_TWR", "ABCDEF_TWR", "KJFK", "KJFK_FOO"],
)
def test_unusable_controller_callsigns_are_skipped(callsign: str) -> None:
    data = {"controllers": [_controller(callsign)]}
    assert get_staffed_positions(data, AIRPORTS) == {}


def test_lowercase_icao_prefix_resolves() -> None:
    data = {"controllers": [_controller("kjfk_APP")]}
    assert get_staffed_positions(data, AIRPORTS) == {"KJFK": ["APP"]}


def test_lowercase_position_suffix_is_skipped() -> None:
    data = {"controllers": [_controller("KJFK_app")]}
    assert get_staffed_positions(data, AIRPORTS) == {}


def test_only_the_last_underscore_part_is_the_position() -> None:
    data = {"controllers": [_controller("KJFK_GND_TWR")]}
    assert get_staffed_positions(data, AIRPORTS) == {"KJFK": ["TWR"]}


def test_atis_suffix_in_the_controllers_list_is_ignored() -> None:
    data = {"controllers": [_controller("KJFK_ATIS")]}
    assert get_staffed_positions(data, AIRPORTS) == {}


def test_atis_station_is_recorded() -> None:
    data = {"atis": [_atis("KJFK_ATIS")]}
    assert get_staffed_positions(data, AIRPORTS) == {"KJFK": ["ATIS"]}


def test_atis_is_appended_after_controller_positions() -> None:
    data = {
        "controllers": [_controller("KJFK_GND"), _controller("KJFK_TWR")],
        "atis": [_atis("KJFK_ATIS")],
    }
    assert get_staffed_positions(data, AIRPORTS) == {"KJFK": ["TWR", "GND", "ATIS"]}


def test_atis_frequency_is_not_excluded() -> None:
    data = {"atis": [{"callsign": "KJFK_ATIS", "frequency": "199.998"}]}
    assert get_staffed_positions(data, AIRPORTS) == {"KJFK": ["ATIS"]}


@pytest.mark.parametrize(
    "callsign",
    ["", "KJFK", "ABCDEF_ATIS", "KJFK_ATIS2", "KJFK_TWR"],
)
def test_unusable_atis_callsigns_are_skipped(callsign: str) -> None:
    data = {"atis": [_atis(callsign)]}
    assert get_staffed_positions(data, AIRPORTS) == {}


def test_atis_three_letter_callsign_gets_the_k_prefix() -> None:
    airports = {"KSFO": {"name": "San Francisco", "country_code": "US"}}
    data = {"atis": [_atis("SFO_ATIS")]}
    assert get_staffed_positions(data, airports) == {"KSFO": ["ATIS"]}


def test_airport_type_is_not_consulted() -> None:
    airports = {"KLAX": {"name": "Los Angeles", "tower_type": "NON-ATCT"}}
    data = {"controllers": [_controller("KLAX_APP")]}
    assert get_staffed_positions(data, airports) == {"KLAX": ["APP"]}


def test_key_order_follows_first_appearance() -> None:
    data = {
        "controllers": [
            _controller("KLAX_TWR"),
            _controller("KJFK_TWR"),
            _controller("KLAX_GND"),
        ]
    }
    result = get_staffed_positions(data, AIRPORTS)
    assert result == {"KLAX": ["TWR", "GND"], "KJFK": ["TWR"]}
    assert list(result) == ["KLAX", "KJFK"]
