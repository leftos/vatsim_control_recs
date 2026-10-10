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


def awy1(
    designation: str, sequence: int, mea: int | None, airway_type: str = " "
) -> str:
    """One AWY1 (segment) record: designation 4-8, type 9, sequence 10-14, MEA at 74-78 (blank if None)."""
    head = f"AWY1{designation:<5}{airway_type}{sequence:>5}09/03/2026"
    mea_text = "" if mea is None else f"{mea:05d}"
    return f"{head:<74}{mea_text}".ljust(315)


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


def awy2_border(designation: str, sequence: int, name: str) -> str:
    """One AWY2 border point record: a name and no position, as AWY.txt writes them."""
    head = f"AWY2{designation:<5} {sequence:>5}{name:<30}"
    return f"{head:<117}{designation:<5}*{name}".ljust(315)


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
    # J70 crosses Canada over Lake Erie: two border points between CFGFT and BEWEL
    *airway(
        "J70",
        [
            (160, "SVM", ("42-24-31.106N", "083-35-38.046W"), 18000),
            (170, "CFGFT", ("42-23-01.45N", "082-49-04.29W"), 18000),
        ],
    ),
    awy1("J70", 180, None),
    awy2_border("J70", 180, "U.S. CANADIAN BORDER"),
    awy1("J70", 190, 18000),
    awy2_border("J70", 190, "U.S. CANADIAN BORDER"),
    *airway(
        "J70",
        [
            (200, "BEWEL", ("42-17-20.28N", "080-44-42.49W"), 18000),
            (210, "JHW", ("42-11-18.985N", "079-07-16.708W"), 18000),
        ],
    ),
    # J5 leaves the US at CFKFL over two border points and re-enters at CFJCC, then ends at a third
    *airway(
        "J5",
        [
            (160, "SEA", ("47-26-07.343N", "122-18-34.618W"), 18000),
            (170, "CFKFL", ("48-48-36.35N", "123-00-30.61W"), 18000),
        ],
    ),
    awy1("J5", 180, None),
    awy2_border("J5", 180, "U.S. CANADIAN BORDER"),
    awy1("J5", 190, 18000),
    awy2_border("J5", 190, "U.S. CANADIAN BORDER"),
    *airway(
        "J5",
        [
            (200, "CFJCC", ("48-50-22.99N", "123-01-26.42W"), 18000),
            (210, "CFDCT", ("49-00-07.44N", "123-06-33.57W"), 18000),
        ],
    ),
    awy1("J5", 220, 18000),
    awy2_border("J5", 220, "U.S. CANADIAN BORDER"),
]


def install_awy_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Point the NASR loaders at the fixture AWY.txt instead of the NASR cache.

    The body of a yield fixture: there is no NAV.txt or FIX.txt, so no fix off
    the fixture airways resolves. The loader caches are cleared on both sides.
    """
    awy_path = tmp_path / "AWY.txt"
    awy_path.write_text("\n".join(FIXTURE_LINES) + "\n", encoding="latin-1")
    monkeypatch.setattr(navaids, "ensure_nasr_data", lambda quiet=False: tmp_path)
    navaids.clear_navaid_cache()
    yield awy_path
    navaids.clear_navaid_cache()


@pytest.fixture(autouse=True)
def awy_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the airway loaders at the fixture AWY.txt instead of the NASR cache."""
    yield from install_awy_fixture(tmp_path, monkeypatch)


def _segments(route: str) -> dict[tuple[str, str], int]:
    violations = navaids.get_max_mea_for_route(route).violations
    return {(v.segment_start, v.segment_end): v.mea for v in violations}


def test_mea_is_read_for_the_segment_leaving_each_point() -> None:
    """J107 MLF to BLD flies BLD-OVETO-NORRA-BERYL-MLF at 18,000; the 33,000 belongs to MLF-URNUW."""
    max_mea = navaids.get_max_mea_for_route("MLF J107 BLD").max_mea

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
    max_mea = navaids.get_max_mea_for_route("GVO V12 PMD").max_mea

    assert max_mea == 9300


def test_exit_fix_not_on_the_airway_reports_no_segment_of_it() -> None:
    """ONM is not on V12, so the flown stretch is unknown and no V12 segment is reported."""
    result = navaids.get_max_mea_for_route("ABQ V12 ONM")

    assert (result.max_mea, result.violations) == (None, [])


def test_speed_and_level_group_on_an_endpoint_still_finds_the_fix() -> None:
    """MLF/N0490F400 is MLF; the stretch is the same as for a bare MLF."""
    assert _segments("MLF/N0490F400 J107 BLD") == _segments("MLF J107 BLD")


def test_missing_endpoint_reports_skipped_airway() -> None:
    """One end on V12 and the other not: V12 is skipped and named with the fix that is not on it."""
    exit_missing = navaids.get_max_mea_for_route("ABQ V12 ONM")
    entry_missing = navaids.get_max_mea_for_route("ONM V12 ABQ")

    assert exit_missing.violations == []
    assert exit_missing.skipped == [
        navaids.SkippedAirway(airway="V12", missing_fix="ONM")
    ]
    assert entry_missing.skipped == [
        navaids.SkippedAirway(airway="V12", missing_fix="ONM")
    ]


def test_neither_endpoint_on_airway_is_skipped_silently() -> None:
    """Neither ONM nor SAF is on V12: nothing is checked and nothing is left for flight info to show."""
    assert navaids.get_max_mea_for_route("ONM V12 SAF") == navaids.MeaCheck(
        max_mea=None, violations=[], skipped=[]
    )


def test_cross_border_route_checks_the_us_portion() -> None:
    """DAFLU is off J70 in Canada: J70 is checked from the US end to its border point, both ways."""
    eastbound = navaids.get_max_mea_for_route("SVM J70 DAFLU")
    westbound = navaids.get_max_mea_for_route("DAFLU J70 BEWEL")

    assert eastbound.skipped == []
    assert _segments("SVM J70 DAFLU") == {
        ("SVM", "CFGFT"): 18000,
        ("CFGFT", "U.S. CANADIAN BORDER"): 18000,
    }
    assert westbound.skipped == []
    assert _segments("DAFLU J70 BEWEL") == {("U.S. CANADIAN BORDER", "BEWEL"): 18000}


def test_single_border_is_not_taken_toward_a_fix_away_from_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WESTX lies west of SVM, away from J70's border: J70 is skipped, not walked to Canada.

    DAFLU lies next to CFGFT, the last US fix before the border, so it still is.
    """
    positions = {"WESTX": (42.40, -84.00), "DAFLU": (42.30, -82.70)}
    monkeypatch.setattr(navaids, "get_waypoint_coordinates", positions.get)

    off_airway = navaids.get_max_mea_for_route("SVM J70 WESTX")

    assert off_airway.violations == []
    assert off_airway.skipped == [
        navaids.SkippedAirway(airway="J70", missing_fix="WESTX")
    ]
    assert navaids.get_max_mea_for_route("SVM J70 DAFLU").skipped == []
    assert _segments("SVM J70 DAFLU") == {
        ("SVM", "CFGFT"): 18000,
        ("CFGFT", "U.S. CANADIAN BORDER"): 18000,
    }


POSITIONS = {
    "EPSOP": (48.8322, -123.0102),  # in J5's Canadian gap, between CFKFL and CFJCC
    "NORTHX": (49.15, -123.15),  # past J5's last border point
}


def _segment_order(route: str) -> list[tuple[str, str]]:
    """The checked segments in the order they are reported."""
    return [
        (v.segment_start, v.segment_end)
        for v in navaids.get_max_mea_for_route(route).violations
    ]


def test_two_border_candidates_pick_the_one_toward_the_missing_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J5 has two candidate borders on each side of CFJCC: the one nearer EPSOP or NORTHX wins."""
    monkeypatch.setattr(navaids, "get_waypoint_coordinates", POSITIONS.get)

    assert _segment_order("CFDCT J5 EPSOP") == [
        ("U.S. CANADIAN BORDER", "CFJCC"),
        ("CFJCC", "CFDCT"),
    ]
    assert _segment_order("CFJCC J5 NORTHX") == [
        ("CFJCC", "CFDCT"),
        ("CFDCT", "U.S. CANADIAN BORDER"),
    ]
    assert [
        fix.identifier for fix in navaids.get_airway_fixes("J5", "CFDCT", "EPSOP")
    ] == ["CFDCT", "CFJCC"]


def test_two_border_candidates_without_a_position_are_not_checked() -> None:
    """NOWHERE has no position, so neither of the two candidate borders can be chosen."""
    check = navaids.get_max_mea_for_route("CFJCC J5 NOWHERE")

    assert check.violations == []
    assert check.skipped == [navaids.SkippedAirway(airway="J5", missing_fix="NOWHERE")]
    assert navaids.get_airway_fixes("J5", "CFJCC", "NOWHERE") == []


def _entry_level(route: str) -> int | None:
    return navaids.get_max_mea_for_route(route).violations[0].level_ft


def test_metric_and_altitude_levels_convert_to_feet() -> None:
    """F and A are hundreds of feet; S and M are tens of metres."""
    assert _entry_level("MLF/N0490F400 J107 URNUW") == 40000
    assert _entry_level("MLF/N0490A045 J107 URNUW") == 4500
    assert _entry_level("MLF/N0490S1130 J107 URNUW") == round(11300 * 3.28084)
    assert _entry_level("MLF/N0490M0840 J107 URNUW") == round(8400 * 3.28084)
    assert _entry_level("MLF J107 URNUW") is None


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


@pytest.mark.parametrize("group", ["N0490F400", "N0490S1130", "M082M0840", "K0900A045"])
def test_route_parser_strips_every_speed_level_group(group: str) -> None:
    """The group leaves MLF and BLD on the route, whichever speed and level spelling it uses."""
    airports = {"MLF": (38.3604, -113.0132), "BLD": (35.9958, -114.8636)}
    parsed = navaids.parse_route_string(
        f"MLF/{group} {group} J107 BLD", airports=airports
    )

    assert [waypoint.identifier for waypoint in parsed] == [
        "MLF",
        "BERYL",
        "NORRA",
        "OVETO",
        "BLD",
    ]
