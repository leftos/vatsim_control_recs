"""The route parser expands an airway only between two fixes that are both on it.

Runs against the AWY.txt fixture of tests/test_mea_check.py (real records of
the 2026-10-01 cycle), so neither the network nor the NASR cache is touched.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.data import navaids
from tests.test_mea_check import install_awy_fixture


@pytest.fixture(autouse=True)
def awy_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the airway loaders at the fixture AWY.txt instead of the NASR cache."""
    yield from install_awy_fixture(tmp_path, monkeypatch)


def _identifiers(airway: str, entry_fix: str, exit_fix: str) -> list[str]:
    return [
        fix.identifier for fix in navaids.get_airway_fixes(airway, entry_fix, exit_fix)
    ]


def test_missing_endpoint_does_not_expand_whole_airway() -> None:
    """ONM is not on V12: ABQ V12 ONM expands to nothing, not to V12 from ABQ to its end."""
    assert _identifiers("V12", "ABQ", "ACH") == ["ABQ", "OTO", "ACH"]
    assert navaids.get_airway_fixes("V12", "ABQ", "ONM") == []


def test_reverse_direction_airway_fixes_follow_the_flight() -> None:
    """MLF J107 BLD flies J107 against its sequence order, so its fixes run MLF to BLD."""
    assert _identifiers("J107", "MLF", "BLD") == [
        "MLF",
        "BERYL",
        "NORRA",
        "OVETO",
        "BLD",
    ]


def test_border_points_never_become_route_fixes() -> None:
    """J70's two border points have no position: CFGFT J70 BEWEL expands to its two fixes only.

    Leaving the US to DAFLU, the expansion stops at the last US fix before the border.
    """
    assert _identifiers("J70", "CFGFT", "BEWEL") == ["CFGFT", "BEWEL"]
    assert _identifiers("J70", "SVM", "DAFLU") == ["SVM", "CFGFT"]
    assert all(
        isinstance(fix, navaids.AirwayFix)
        for fix in navaids.get_airway_fixes("J70", "SVM", "JHW")
    )


def test_parse_route_string_expands_adjacent_airways() -> None:
    """REDIN V165 V208 VISTA runs V165 to OCN, where it meets V208, then V208 to VISTA."""
    airports = {"REDIN": (32.9511, -117.3997), "VISTA": (33.2191, -117.2345)}

    parsed = navaids.parse_route_string("REDIN V165 V208 VISTA", airports=airports)

    assert [waypoint.identifier for waypoint in parsed] == ["REDIN", "OCN", "VISTA"]


def test_parse_route_string_takes_the_junction_on_the_entry_fixs_airway_type() -> None:
    """KATHS is on V12 H, which meets V904 H at LEANE, not federal V12's PMD."""
    airports = {"KATHS": (21.075, -157.8333), "HENDX": (21.4, -157.8)}

    parsed = navaids.parse_route_string("KATHS V12 V904 HENDX", airports=airports)

    assert [waypoint.identifier for waypoint in parsed] == [
        "KATHS",
        "NONNI",
        "LEANE",
        "HENDX",
    ]


def test_parse_route_string_without_a_junction_expands_each_airway_as_before() -> None:
    """V12 and J107 share no fix: V12 still expands from ABQ to ACH, J107 adds nothing."""
    airports = {"ABQ": (35.0438, -106.8163), "ACH": (35.1117, -105.0400)}

    parsed = navaids.parse_route_string("ABQ V12 J107 ACH", airports=airports)

    assert [waypoint.identifier for waypoint in parsed] == ["ABQ", "OTO", "ACH"]


def test_parse_route_string_expands_three_adjacent_airways() -> None:
    """V165 meets V208 at OCN and V208 meets V903 at VISTA."""
    airports = {"REDIN": (32.9511, -117.3997), "ENDCC": (33.4, -116.9)}

    parsed = navaids.parse_route_string("REDIN V165 V208 V903 ENDCC", airports=airports)

    assert [waypoint.identifier for waypoint in parsed] == [
        "REDIN",
        "OCN",
        "VISTA",
        "ENDCC",
    ]


def test_parse_route_string_keeps_the_junction_when_the_first_airway_expands_nothing() -> (
    None
):
    """ABQ is not on V165, so V165 adds nothing; V208 still starts at OCN, its junction with V165."""
    airports = {"ABQ": (35.0438, -106.8163), "VISTA": (33.2191, -117.2345)}

    parsed = navaids.parse_route_string("ABQ V165 V208 VISTA", airports=airports)

    assert [waypoint.identifier for waypoint in parsed] == ["ABQ", "OCN", "VISTA"]
