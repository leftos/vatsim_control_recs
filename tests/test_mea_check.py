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


def _bound_text(octant: str) -> str:
    """An AWY1 direction text as the real record pads it: "NE BND", "E BND " or blank."""
    return f"{octant + ' BND' if octant else '':<6}"


def awy1(
    designation: str,
    sequence: int,
    mea: int | None,
    airway_type: str = " ",
    *,
    direction: str = "",
    mea_opposite: int | None = None,
    opposite_direction: str = "",
) -> str:
    """One AWY1 (segment) record: designation 4-8, type 9, sequence 10-14.

    MEA at 74-78 and its direction at 79-84, the opposite MEA at 85-89 and its
    direction at 90-95; each blank when None or empty.
    """
    head = f"AWY1{designation:<5}{airway_type}{sequence:>5}09/03/2026"
    mea_text = "" if mea is None else f"{mea:05d}"
    opposite_text = "" if mea_opposite is None else f"{mea_opposite:05d}"
    return (
        f"{head:<74}{mea_text:<5}{_bound_text(direction)}"
        f"{opposite_text:<5}{_bound_text(opposite_direction)}"
    ).ljust(315)


def dms(latitude: float, longitude: float) -> tuple[str, str]:
    """A decimal position as AWY.txt's degrees-minutes-seconds texts."""

    def text(value: float, width: int, hemispheres: str) -> str:
        hundredths = round(abs(value) * 360000)
        degrees, rest = divmod(hundredths, 360000)
        minutes, seconds = divmod(rest, 6000)
        hemisphere = hemispheres[1] if value < 0 else hemispheres[0]
        return f"{degrees:0{width}d}-{minutes:02d}-{seconds / 100:05.2f}{hemisphere}"

    return text(latitude, 2, "NS"), text(longitude, 3, "EW")


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
    # V1 near Myrtle Beach: 2,100 northeast-bound and 5,000 southwest-bound
    awy1("V1", 160, 2100, direction="NE", mea_opposite=5000, opposite_direction="SW"),
    awy2("V1", 160, "INLET", dms(33.3927, -79.3318)),
    awy1("V1", 170, 2100, direction="NE", mea_opposite=5000, opposite_direction="SW"),
    awy2("V1", 170, "PLANN", dms(33.5584, -79.0941)),
    awy1("V1", 180, 5000, direction="NE", mea_opposite=2000, opposite_direction="SW"),
    awy2("V1", 180, "CRE", dms(33.8138, -78.7245)),
    # V2's first MEA is the westbound one, against its increasing sequence
    awy1("V2", 360, 9700, direction="W", mea_opposite=7000, opposite_direction="E"),
    awy2("V2", 360, "REEPO", dms(45.7596, -109.5269)),
    awy2("V2", 370, "COLUS", dms(45.7836, -109.0997)),
    awy1("V4", 310, 8000, direction="E", mea_opposite=9500, opposite_direction="W"),
    awy2("V4", 310, "ALKAL", dms(43.0162, -115.3281)),
    awy2("V4", 320, "GOODE", dms(42.8397, -114.7498)),
    # Synthetic: a southbound segment to the Mexican border, whose course is unknown
    awy1("V600", 10, 5000, direction="S", mea_opposite=7000, opposite_direction="N"),
    awy2("V600", 10, "SOUTH", dms(32.9, -115.0)),
    awy2_border("V600", 20, "U.S. MEXICAN BORDER"),
    # V165 and V208 meet at OCN only
    awy1("V165", 30, 2500),
    awy2("V165", 30, "REDIN", dms(32.9511, -117.3997)),
    awy2("V165", 40, "OCN", dms(33.2406, -117.4177)),
    awy1("V208", 70, 3100),
    awy2("V208", 70, "OCN", dms(33.2406, -117.4177)),
    awy1("V208", 80, 8000),
    awy2("V208", 80, "VISTA", dms(33.2191, -117.2345)),
    # Synthetic: V903 leaves V208 at VISTA
    awy1("V903", 10, 4000),
    awy2("V903", 10, "VISTA", dms(33.2191, -117.2345)),
    awy2("V903", 20, "ENDCC", dms(33.4, -116.9)),
    # Synthetic: V901 and V902 share two fixes, SHRAA and SHRBB
    *airway(
        "V901",
        [
            (10, "STRTA", dms(34.0, -118.0), 5000),
            (20, "SHRAA", dms(34.1, -117.9), 5000),
            (30, "SHRBB", dms(34.2, -117.8), 5000),
        ],
    ),
    *airway(
        "V902",
        [
            (10, "SHRAA", dms(34.1, -117.9), 6000),
            (20, "SHRBB", dms(34.2, -117.8), 6000),
            (30, "ENDBB", dms(34.3, -117.7), 6000),
        ],
    ),
    # Synthetic: V904 meets federal V12 at PMD and V12 H at LEANE
    *airway(
        "V904",
        [
            (10, "PMD", ("34-37-53.034N", "118-03-49.761W"), 5000),
            (20, "FEDXX", dms(34.8, -117.8), 5000),
        ],
    ),
    *airway(
        "V904",
        [
            (10, "LEANE", ("21-16-00.00N", "157-57-00.00W"), 7000),
            (20, "HENDX", dms(21.4, -157.8), 7000),
        ],
        airway_type="H",
    ),
    # Synthetic: NORTA to NORTB is a course of about 349, just west of north
    awy1("V905", 10, 3000, direction="N", mea_opposite=6000, opposite_direction="S"),
    awy2("V905", 10, "NORTA", dms(40.0, -100.0)),
    awy2("V905", 20, "NORTB", dms(40.5, -100.13)),
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
        ("OVETO", "BLD"): 18000,
        ("NORRA", "OVETO"): 18000,
        ("BERYL", "NORRA"): 18000,
        ("MLF", "BERYL"): 18000,
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


def test_mea_entry_skips_a_short_sid() -> None:
    """MKK5 is a SID name, not the fix before V12: V12 has no entry fix and nothing is reported."""
    assert navaids.get_max_mea_for_route("MKK5 V12 ACH") == navaids.MeaCheck(
        max_mea=None, violations=[], skipped=[]
    )


def test_mea_check_reaches_past_a_short_sid_to_the_airways_entry_fix() -> None:
    """ABQ is the fix before V12, so MKK5 earlier in the route leaves the check as it was."""
    assert _segments("MKK5 ABQ V12 ACH") == {
        ("ABQ", "OTO"): 12000,
        ("OTO", "ACH"): 10000,
    }


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
        ("CFJCC", "U.S. CANADIAN BORDER"),
        ("CFDCT", "CFJCC"),
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


def test_awy1_record_keeps_opposite_mea_and_directions() -> None:
    """V1 seq 160 is 2,100 northeast-bound and 5,000 southwest-bound; a bare "BND" is no direction."""
    line = awy1(
        "V1", 160, 2100, direction="NE", mea_opposite=5000, opposite_direction="SW"
    )
    no_opposite = awy1("V1", 10, 4000)
    no_opposite = f"{no_opposite[:90]}   BND{no_opposite[96:]}"

    parsed = navaids._parse_awy1_record(line)
    bare = navaids._parse_awy1_record(no_opposite)

    assert parsed is not None
    assert parsed[1] == navaids.AirwaySegmentRestriction(
        airway="V1",
        sequence=160,
        mea=2100,
        mea_direction="NE",
        mea_opposite=5000,
        mea_opposite_direction="SW",
        moca=None,
    )
    assert bare is not None
    assert (bare[1].mea_direction, bare[1].mea_opposite_direction) == (None, None)


def test_opposite_direction_mea_follows_the_course_flown() -> None:
    """INLET to PLANN flies northeast at 2,100; PLANN to INLET flies southwest at 5,000."""
    assert _segments("INLET V1 PLANN") == {("INLET", "PLANN"): 2100}
    assert _segments("PLANN V1 INLET") == {("PLANN", "INLET"): 5000}


def test_direction_text_not_increasing_sequence_picks_the_mea() -> None:
    """V2's first MEA (9,700) is westbound: REEPO to COLUS, east, is 7,000."""
    assert _segments("REEPO V2 COLUS") == {("REEPO", "COLUS"): 7000}
    assert _segments("COLUS V2 REEPO") == {("COLUS", "REEPO"): 9700}


def test_record_without_an_opposite_mea_is_unchanged() -> None:
    """ABQ to OTO has one MEA, 12,000, whichever way it is flown."""
    assert _segments("ABQ V12 OTO") == {("ABQ", "OTO"): 12000}
    assert _segments("OTO V12 ABQ") == {("OTO", "ABQ"): 12000}


def test_unplaceable_course_takes_the_higher_mea() -> None:
    """The border point has no position, so SOUTH to it takes the higher of 5,000 and 7,000."""
    assert _segments("SOUTH V600 NOWHERE") == {("SOUTH", "U.S. MEXICAN BORDER"): 7000}


def test_false_warning_not_raised_for_the_low_direction() -> None:
    """V4 ALKAL-GOODE is 8,000 eastbound and 9,500 westbound; filed 9,000 warns only westbound."""
    eastbound = _flight_info_text("ALKAL V4 GOODE", "9000")
    westbound = _flight_info_text("GOODE V4 ALKAL", "9000")

    assert "MEA WARNING" not in eastbound
    assert "MEA WARNING" in westbound
    assert "9,500" in westbound


def test_adjacent_airways_use_their_shared_fix_as_the_junction() -> None:
    """REDIN V165 V208 VISTA turns from V165 to V208 at OCN, the one fix both hold."""
    check = navaids.get_max_mea_for_route("REDIN V165 V208 VISTA")

    assert _segments("REDIN V165 V208 VISTA") == {
        ("REDIN", "OCN"): 2500,
        ("OCN", "VISTA"): 3100,
    }
    assert check.skipped == []


def test_adjacent_airways_with_no_shared_fix_are_only_logged() -> None:
    """V12 and J107 share no fix: neither is checked and neither is reported skipped."""
    assert navaids.get_max_mea_for_route("ABQ V12 J107 BLD") == navaids.MeaCheck(
        max_mea=None, violations=[], skipped=[]
    )


def test_adjacent_airways_sharing_two_fixes_are_only_logged() -> None:
    """V901 and V902 share SHRAA and SHRBB, so the junction is unknown: nothing is shown."""
    assert navaids.get_max_mea_for_route("STRTA V901 V902 ENDBB") == navaids.MeaCheck(
        max_mea=None, violations=[], skipped=[]
    )


def test_three_adjacent_airways_pair_junction_by_junction() -> None:
    """V165 meets V208 at OCN and V208 meets V903 at VISTA."""
    check = navaids.get_max_mea_for_route("REDIN V165 V208 V903 ENDCC")

    assert _segments("REDIN V165 V208 V903 ENDCC") == {
        ("REDIN", "OCN"): 2500,
        ("OCN", "VISTA"): 3100,
        ("VISTA", "ENDCC"): 4000,
    }
    assert check.skipped == []


def test_adjacent_airways_pick_the_junction_on_the_entry_fixs_airway_type() -> None:
    """KATHS is on V12 H, which meets V904 H at LEANE; federal V12 and V904 meet at PMD."""
    check = navaids.get_max_mea_for_route("KATHS V12 V904 HENDX")

    assert _segments("KATHS V12 V904 HENDX") == {
        ("KATHS", "NONNI"): 29000,
        ("NONNI", "LEANE"): 29000,
        ("LEANE", "HENDX"): 7000,
    }
    assert check.skipped == []


def test_repeated_airway_is_checked_once() -> None:
    """ABQ V12 V12 OTO flies V12 from ABQ to OTO."""
    check = navaids.get_max_mea_for_route("ABQ V12 V12 OTO")

    assert check == navaids.MeaCheck(
        max_mea=12000,
        violations=[
            navaids.MeaViolation(
                airway="V12",
                segment_start="ABQ",
                segment_end="OTO",
                mea=12000,
                level_ft=None,
            )
        ],
        skipped=[],
    )


def test_course_just_west_of_north_takes_the_northbound_mea() -> None:
    """NORTA to NORTB, about 349, is 10 degrees off N and 170 off S: 3,000; back south, 6,000."""
    assert _segments("NORTA V905 NORTB") == {("NORTA", "NORTB"): 3000}
    assert _segments("NORTB V905 NORTA") == {("NORTB", "NORTA"): 6000}


def test_airways_meeting_only_at_border_points_have_no_junction() -> None:
    """J5 and J70 both name a "U.S. CANADIAN BORDER" point; a border point is never a junction."""
    assert navaids.get_max_mea_for_route("SEA J5 J70 JHW") == navaids.MeaCheck(
        max_mea=None, violations=[], skipped=[]
    )
