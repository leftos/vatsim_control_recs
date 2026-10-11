"""A short SID/STAR name in a filed route is a procedure, not a fix.

CIFP procedure ids are letters followed by a digit, so shape alone cannot tell a
four- or five-character name such as "MKK5" from a fix such as "KH03M". Every
test runs against a small CIFP fixture and a NASR loader pointed at a temp
directory, so neither the network nor the real caches are touched.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.core import route_distance
from backend.data import cifp, navaids
from tests.test_cifp_index import record

# Real procedure ids of the 2610 cycle: a PHNL SID with a common route, a
# runway-only PANC SID, a vectors-only PANC SID and a KBDL STAR.
CIFP_LINES = [
    record("PHNL", "D", "MKK5", "ALL", 10, "MKK", area="SPACP", region="PH"),
    record("PANC", "D", "TURN8", "RW15", 10, "EGKAJ", area="SCANP", region="PA"),
    record("PANC", "D", "TURN8", "RW15", 20, "TED", area="SCANP", region="PA"),
    record("PANC", "D", "TURN8", "RW15", 30, "NAPTO", area="SCANP", region="PA"),
    record("PANC", "D", "TURN8", "RW33", 10, "ISACC", area="SCANP", region="PA"),
    record("PANC", "D", "TURN8", "RW33", 20, "TED", area="SCANP", region="PA"),
    record("PANC", "D", "TURN8", "RW33", 30, "NAPTO", area="SCANP", region="PA"),
    record("PANC", "D", "ANCH1", "RW15", 10, "", area="SCANP", region="PA"),
    record("KBDL", "E", "DPK3", "ALL", 10, "MAD"),
    record("KBDL", "E", "DPK3", "ALL", 20, "BRISS"),
]

POSITIONS: dict[str, tuple[float, float]] = {
    "MKK": (21.1522, -157.1667),
    "APACK": (21.5, -157.5),
    "TED": (61.16, -149.98),
    "NAPTO": (61.5, -150.5),
    "MAD": (41.0, -72.0),
    "BRISS": (41.5, -72.5),
}

FIX_SHAPED_FIX = "ABC1"


class _FakeLoader:
    """A NASR loader returning a fixed mapping, with the cache_clear() to match."""

    def __init__(self, mapping: dict[str, object]) -> None:
        self._mapping = mapping

    def __call__(self) -> dict[str, object]:
        return self._mapping

    def cache_clear(self) -> None:
        """Match lru_cache's own API, which clear_navaid_cache() calls."""


@pytest.fixture
def cifp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CIFP lookups at the fixture file instead of the cache."""
    path = tmp_path / "FAACIFP18-2610"
    path.write_text("\n".join(CIFP_LINES) + "\n", encoding="latin-1")
    monkeypatch.setattr(cifp, "get_cifp_cache_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the NASR loaders at an empty directory and clear every cache."""
    monkeypatch.setattr(navaids, "ensure_nasr_data", lambda quiet=False: tmp_path)
    monkeypatch.setattr(navaids, "get_waypoint_coordinates", POSITIONS.get)
    cifp.clear_cifp_cache()
    navaids.clear_navaid_cache()
    route_distance.clear_route_cache()
    yield
    cifp.clear_cifp_cache()
    navaids.clear_navaid_cache()
    route_distance.clear_route_cache()


def test_short_sid_expands_from_the_departure() -> None:
    """MKK5 is the PHNL SID: its fixes come before the rest of the route."""
    parsed = navaids.parse_route_string("MKK5 APACK", departure="PHNL")

    assert [waypoint.identifier for waypoint in parsed] == ["MKK", "APACK"]


def test_short_star_expands_at_the_arrival() -> None:
    """DPK3 is the KBDL STAR: its fixes are added where the STAR is filed."""
    parsed = navaids.parse_route_string("DPK3 KBDL", arrival="KBDL")

    assert [waypoint.identifier for waypoint in parsed] == ["MAD", "BRISS"]


def test_runway_only_short_sid_uses_the_shared_tail() -> None:
    """TURN8 has runway transitions only: every runway flies its shared tail."""
    parsed = navaids.parse_route_string("TURN8 NAPTO", departure="PANC")

    assert [waypoint.identifier for waypoint in parsed] == ["TED", "NAPTO"]


def test_vectors_only_short_sid_is_dropped() -> None:
    """ANCH1 is coded with vectors and no fix: the name adds nothing, and raises nothing."""
    parsed = navaids.parse_route_string("ANCH1 NAPTO", departure="PANC")

    assert [waypoint.identifier for waypoint in parsed] == ["NAPTO"]


@pytest.mark.parametrize("name", ["OTR7", "RTE6", "SEA9"])
def test_unknown_short_name_is_dropped_and_not_an_endpoint(name: str) -> None:
    """A short name CIFP holds no procedure for: no waypoint, and no airway endpoint."""
    assert navaids.parse_route_string(name) == []

    assert navaids._is_route_endpoint(name, None) is False


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("MKK5", False),
        ("KH03M", True),
        ("POE1", True),
        ("G10", True),
        ("N644", True),
        ("V12", False),
        ("DCT", False),
        ("", False),
    ],
)
def test_short_procedure_name_is_not_an_airway_endpoint(
    token: str, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SID/STAR name is no endpoint; a fix, a navaid and a foreign airway all are."""
    monkeypatch.setattr(navaids, "load_navaids", _FakeLoader({"POE1": object()}))
    monkeypatch.setattr(navaids, "load_fixes", _FakeLoader({}))

    assert navaids._is_route_endpoint(token, None) is expected


def test_airway_run_skips_a_short_sid_before_the_next_fix() -> None:
    """PMD V12 MKK5 TCC: the airway run's exit is TCC, the fix after the SID name."""
    airports = {"TCC": (35.1822, -103.5985)}

    run, exit_fix = navaids._airway_run_ahead(
        ["PMD", "V12", "MKK5", "TCC"], 1, airports
    )

    assert [airway for _, airway in run] == ["V12"]
    assert exit_fix == "TCC"


def test_short_name_equal_to_a_fix_stays_a_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fix whose ident is procedure-shaped is resolved as the fix it is."""
    monkeypatch.setattr(navaids, "load_fixes", _FakeLoader({FIX_SHAPED_FIX: object()}))

    def coordinates(identifier: str) -> tuple[float, float] | None:
        if identifier == FIX_SHAPED_FIX:
            return (40.0, -100.0)
        return None

    monkeypatch.setattr(navaids, "get_waypoint_coordinates", coordinates)

    parsed = navaids.parse_route_string(FIX_SHAPED_FIX)

    assert [waypoint.identifier for waypoint in parsed] == [FIX_SHAPED_FIX]
