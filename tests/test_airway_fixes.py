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
