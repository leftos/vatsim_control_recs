# Wave 3 route checks: measured causes, fixes and red tests

Two debugger runs (NASR cycle 2026-10-01, CIFP 2610, live VATSIM data) settled the causes behind the Wave 3 lines in [MAIN.md](./MAIN.md). Each section gives the minimal fix and a test that was red at a4955e6. Owner rulings are in the MAIN.md lines, not repeated here.

## MEA airway walk (`backend/data/navaids.py`)

Fix:

1. Loaders: `_parse_awy_record` reads designation `line[4:9]`, type `line[9]`, sequence `line[10:15]` by column instead of the regex; `_parse_awy1_record` also returns the type; `load_airways` and `load_airway_restrictions` key by `(designation, type)`. The MEA columns (`74:79`, `85:90`, `101:106`) match the FAA layout and stay.
2. `get_max_mea_for_route`: strip `/<speed><level>` from endpoint tokens and skip standalone level tokens (as `parse_route_string` does); use the airway type whose points contain both endpoints, else skip it per the owner ruling; with point indices `a < b` the flown segments are point `i` → `i+1` for `i` in `range(a, b)`, each using the AWY1 record at point `i`'s sequence and labelled with those two fixes. Delete the `in_range` whole-airway branch, the airway-name label fallback, and fix the docstrings that say the record at N ends at point N.
3. Step climbs (its own MAIN.md line): `MeaViolation` gets a required `level_ft: int | None`, the last speed/level group at or before the airway's entry (F and A ×100 ft; S and M tens of metres); `flight_info.py` compares each violation against it, falling back to the filed altitude.

Red test, `tests/test_mea_check.py` (6 failed, 1 passed at a4955e6; the step-climb test stays red after fixes 1-2 alone):

```python
"""The MEA check reads the airway segments a route actually flies, at the level it flies them.

The FAA AWY.txt layout (awy_rf.txt) keys every record by airway designation,
airway type (blank = federal, A = Alaska, H = Hawaii) and point sequence, and
the AWY1 record at sequence N describes the segment from point N to the next
point. Every test runs against a small fixed-width fixture built from real
records of the 2026-10-01 cycle, so neither the network nor the NASR cache is
touched.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.data import navaids
from ui.modals.flight_info import FlightInfoScreen


def awy1(designation: str, sequence: int, mea: int, airway_type: str = " ") -> str:
    """One AWY1 (segment) record: designation 4-8, type 9, sequence 10-14, MEA at 74-78."""
    head = f"AWY1{designation:<5}{airway_type}{sequence:>5}09/03/2026"
    return f"{head:<74}{mea:05d}".ljust(315)


def awy2(
    designation: str,
    sequence: int,
    ident: str,
    position: tuple[str, str],
    airway_type: str = " ",
) -> str:
    """One AWY2 (point) record, with the point's id between asterisks as the real file writes it."""
    latitude, longitude = position
    head = f"AWY2{designation:<5}{airway_type}{sequence:>5}{ident:<30}"
    body = f"{'REP-PT':<19}{'FIX':<15}XX  {latitude} {longitude}     {designation}  *{ident}*XX"
    return f"{head}{body}".ljust(315)


def airway(
    designation: str,
    points: list[tuple[int, str, tuple[str, str], int]],
    airway_type: str = " ",
) -> list[str]:
    """AWY1 then AWY2 for each (sequence, ident, position, MEA of the segment leaving it)."""
    lines: list[str] = []
    for sequence, ident, position, mea in points:
        lines.append(awy1(designation, sequence, mea, airway_type))
        lines.append(awy2(designation, sequence, ident, position, airway_type))
    return lines


FIXTURE_LINES = [
    *airway(
        "V12",
        [
            (10, "GVO", ("34-31-52.753N", "120-05-27.918W"), 6400),
            (20, "RZS", ("34-30-34.315N", "119-46-15.557W"), 9300),
            (30, "EMUXY", ("34-31-24.9N", "119-35-24.97W"), 9300),
            (40, "PMD", ("34-37-53.034N", "118-03-49.761W"), 6000),
            (270, "HEGMA", ("35-02-19.76N", "107-01-09.12W"), 9000),
            (280, "ABQ", ("35-02-37.664N", "106-48-58.723W"), 12000),
            (290, "OTO", ("35-04-20.167N", "105-56-09.891W"), 10000),
            (300, "ACH", ("35-06-42.142N", "105-02-23.744W"), 7700),
            (310, "WASBI", ("35-09-57.29N", "103-57-50.17W"), 7700),
            (320, "TCC", ("35-10-55.941N", "103-35-54.891W"), 6100),
        ],
    ),
    *airway(
        "J107",
        [
            (60, "BLD", ("35-59-44.845N", "114-51-48.884W"), 18000),
            (70, "OVETO", ("36-27-37.91N", "114-30-28.44W"), 18000),
            (80, "NORRA", ("36-45-33.5N", "114-16-51.91W"), 18000),
            (90, "BERYL", ("37-54-00.17N", "113-23-08.58W"), 18000),
            (100, "MLF", ("38-21-37.28N", "113-00-47.638W"), 33000),
            (110, "URNUW", ("39-08-03.04N", "112-06-14.89W"), 33000),
            (120, "OCS", ("41-35-24.76N", "109-00-55.183W"), 18000),
        ],
    ),
    *airway(
        "V12",
        [
            (10, "KATHS", ("21-04-30.00N", "157-50-00.00W"), 29000),
            (20, "NONNI", ("21-13-40.00N", "157-55-00.00W"), 29000),
            (30, "LEANE", ("21-16-00.00N", "157-57-00.00W"), 16000),
            (40, "KRYST", ("21-16-40.00N", "157-58-00.00W"), 16000),
        ],
        airway_type="H",
    ),
]


def _clear_airway_caches() -> None:
    navaids.load_airways.cache_clear()
    navaids.load_airway_restrictions.cache_clear()


@pytest.fixture(autouse=True)
def awy_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the airway loaders at the fixture AWY.txt instead of the NASR cache."""
    (tmp_path / "AWY.txt").write_text("\n".join(FIXTURE_LINES) + "\n", encoding="latin-1")
    monkeypatch.setattr(navaids, "ensure_nasr_data", lambda quiet=False: tmp_path)
    _clear_airway_caches()
    yield tmp_path / "AWY.txt"
    _clear_airway_caches()


def _segments(route: str) -> dict[tuple[str, str], int]:
    _, violations = navaids.get_max_mea_for_route(route)
    return {(v.segment_start, v.segment_end): v.mea for v in violations}


def test_mea_is_read_for_the_segment_leaving_each_point() -> None:
    """J107 MLF to BLD flies BLD-OVETO-NORRA-BERYL-MLF at 18,000; the 33,000 belongs to MLF-URNUW."""
    max_mea, _ = navaids.get_max_mea_for_route("MLF J107 BLD")

    assert max_mea == 18000
    assert _segments("MLF J107 BLD") == {
        ("BLD", "OVETO"): 18000,
        ("OVETO", "NORRA"): 18000,
        ("NORRA", "BERYL"): 18000,
        ("BERYL", "MLF"): 18000,
    }


def test_first_segment_of_the_flown_stretch_is_checked() -> None:
    """ABQ to OTO is one segment with a 12,000 MEA; the check must not skip it."""
    assert _segments("ABQ V12 OTO") == {("ABQ", "OTO"): 12000}


def test_hawaii_airway_does_not_replace_the_federal_airway_meas() -> None:
    """V12 H shares V12's designation and sequence numbers; GVO to PMD is federal V12 at 9,300 at most."""
    max_mea, _ = navaids.get_max_mea_for_route("GVO V12 PMD")

    assert max_mea == 9300


def test_exit_fix_not_on_the_airway_reports_no_segment_of_it() -> None:
    """ONM is not on V12, so the flown stretch is unknown and no V12 segment is reported."""
    assert navaids.get_max_mea_for_route("ABQ V12 ONM") == (None, [])


def test_speed_and_level_group_on_an_endpoint_still_finds_the_fix() -> None:
    """MLF/N0490F400 is MLF; the stretch is the same as for a bare MLF."""
    assert _segments("MLF/N0490F400 J107 BLD") == _segments("MLF J107 BLD")


def _flight_info_text(route: str, altitude: str) -> str:
    """The flight info body for an IFR flight, with the MEA result the screen would load for it."""
    screen = FlightInfoScreen(
        {
            "callsign": "UAE82M",
            "flight_plan": {
                "departure": "OMDB",
                "arrival": "KLAX",
                "flight_rules": "I",
                "altitude": altitude,
                "route": route,
            },
        }
    )
    screen.mea_info = navaids.get_max_mea_for_route(route)
    return screen._format_flight_info()


def test_step_climb_before_the_airway_sets_the_level_checked_against_its_mea() -> None:
    """Filed FL300, climbing to FL400 at KP15Y before MLF J107 URNUW (MEA 33,000): no MEA warning."""
    text = _flight_info_text("KP15Y/N0490F400 DCT MLF J107 URNUW", "30000")

    assert "MEA WARNING" not in text


def test_step_climb_after_the_airway_leaves_the_filed_level_on_it() -> None:
    """Filed FL300 on MLF J107 URNUW (MEA 33,000), climbing to FL400 only afterwards: warned."""
    text = _flight_info_text("MLF J107 URNUW DCT KP15Y/N0490F400", "30000")

    assert "MEA WARNING" in text
    assert "33,000" in text
```

The owner's "MEA not checked" screen note and the `get_airway_fixes` rule need their own tests on top of these. Also: `_load_mea_info` in `flight_info.py` swallows every exception silently (~:364-366); log it.

## NEXT ETA window (`backend/core/analysis.py`)

Fix: delete the standalone `earliest_arrival_eta` update (~:239-247); inside the `elif is_flight_flying_near_arrival(flight, airports, max_eta_hours):` branch, after its counter increments, compute `calculate_eta` and take the minimum. Grouping rows follow, since they read the same dict. With `max_eta_hours=0` every airborne arrival still counts.

Red test, `tests/test_next_eta_window.py` (1 failed, 2 passed at a4955e6). The weather pass (beb750c) removed the weather calls from `analyze_flights_data`; drop the three weather `monkeypatch.setattr` lines if they no longer resolve.

```python
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
    "callsign": "AIB1", "latitude": -37.59, "longitude": 144.843, "groundspeed": 175, "altitude": 2500,
    "flight_plan": {"departure": "YMML", "arrival": "LFBO", "aircraft_short": "A35K", "route": ""},
}

ON_DESCENT_INTO_LFBO: dict[str, Any] = {
    "callsign": "AFR2", "latitude": 44.2291, "longitude": 1.3638, "groundspeed": 250, "altitude": 9000,
    "flight_plan": {"departure": "LFPO", "arrival": "LFBO", "aircraft_short": "A320", "route": ""},
}


class _NoDisplayNames:
    def get_full_names_batch(self, _icaos: list[str]) -> dict[str, str]:
        return {}


def _expected_eta_display(pilot: dict[str, Any]) -> str:
    flight = {**pilot, "departure": pilot["flight_plan"]["departure"], "arrival": "LFBO"}
    airports = analysis.load_airport_data(UNIFIED_AIRPORT_DATA)
    speeds = load_aircraft_approach_speeds("data/aircraft_data.csv")
    display, _, _ = calculate_eta(flight, airports, speeds)
    return display


def _lfbo_row(monkeypatch: pytest.MonkeyPatch, pilots: list[dict[str, Any]], max_eta_hours: float) -> Any:
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


def test_next_eta_is_blank_when_the_only_inbound_is_beyond_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _lfbo_row(monkeypatch, [CLIMBING_OUT_OF_YMML], max_eta_hours=1.0)
    assert (row.arrivals, row.arrivals_all) == (0, 1)
    assert row.next_eta == "", f"NEXT ETA {row.next_eta!r} comes from a flight the ARR column does not count"


def test_next_eta_comes_from_the_inbound_inside_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _lfbo_row(monkeypatch, [CLIMBING_OUT_OF_YMML, ON_DESCENT_INTO_LFBO], max_eta_hours=1.0)
    assert (row.arrivals, row.arrivals_all) == (1, 2)
    assert row.next_eta == _expected_eta_display(ON_DESCENT_INTO_LFBO)


def test_next_eta_shows_every_airborne_inbound_when_the_window_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _lfbo_row(monkeypatch, [CLIMBING_OUT_OF_YMML], max_eta_hours=0)
    assert (row.arrivals, row.arrivals_all) == (1, 1)
    assert row.next_eta == _expected_eta_display(CLIMBING_OUT_OF_YMML)
```
