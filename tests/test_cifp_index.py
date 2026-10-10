"""CIFP lookups read the file once, and return what the old scan returned.

The index under test lives in backend/data/cifp.py: one pass over the CIFP file
indexes its SID, STAR and approach records by airport ident. Every test runs
against a small fixed-width fixture file, so neither the network nor the real
CIFP cache is touched.
"""

from __future__ import annotations

import builtins
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.core import route_distance
from backend.data import cifp


def record(
    ident: str,
    section: str,
    proc_id: str,
    transition: str,
    sequence: int,
    fix: str,
    area: str = "SUSAP",
    region: str = "K2",
    route_type: str = " ",
) -> str:
    """Build one ARINC 424 record line, padded to the CIFP line width.

    Positions: ident at 6-9, region at 10-11, subsection at 12, procedure id at
    13-18, route type at 19, transition at 20-24, sequence at 26-28, fix at
    29-33. Approaches only parse at length 50 or more.
    """
    line = (
        f"{area} {ident:<4}{region:<2}{section}"
        f"{proc_id:6}{route_type}{transition:<5} {sequence:03d}{fix:<5}"
    )
    return line.ljust(60)


# One airport per record block, as the real file has them: KOAK (SID, STAR,
# approach), KOAJ, KSFO (SID, STAR, approach), KDEN (SID), KMCI (STAR), a SCANP
# Alaska line, a SUSAP line with a non-K ident, and a line too short to parse.
# The KOAK SID carries the variant digit the real file writes past the
# 6-character id field, which is why its raw text reads "CNDEL54".
FIXTURE_LINES = [
    record("KOAK", "D", "CNDEL5", "ALL", 10, "SUNOL", route_type="4"),
    record("KOAK", "D", "CNDEL5", "ALL", 20, "TRACY", route_type="4"),
    record("KOAK", "D", "CNDEL5", "RW28B", 10, "OAK", route_type="4"),
    record("KOAK", "E", "SCOLA1", "RW30", 10, "SCOLA"),
    record("KOAK", "E", "SCOLA1", "RW28B", 10, "SCAG"),
    record("KOAK", "E", "SCOLA1", "RW28B", 20, "KOAK"),
    record("KOAK", "F", "H17LZ", "", 10, "LIBGE"),
    record("KOAK", "F", "H17LZ", "", 20, "FAF17"),
    record("KOAJ", "D", "ELLIS2", "ALL", 10, "ELLIS"),
    record("KSFO", "D", "SIDBE5", "ALL", 10, "SIDBE"),
    record("KSFO", "E", "BDEGA3", "RW01L", 10, "BDEGA"),
    record("KSFO", "F", "I28R", "", 10, "FAF28"),
    # A navaid named like its airport: the KDEN VOR's ident is "DEN", while the
    # airport's own ident "KDEN" and a runway reference appear as fixes in the
    # same common route, so the waypoint filter meets all three.
    record("KDEN", "D", "PLAIN2", "ALL", 10, "DEN"),
    record("KDEN", "D", "PLAIN2", "ALL", 20, "PLAIN"),
    record("KDEN", "D", "PLAIN2", "ALL", 30, "KDEN"),
    record("KDEN", "D", "PLAIN2", "ALL", 40, "RW16R"),
    record("KMCI", "E", "JHAWK5", "ALL", 10, "MCI"),
    record("KMCI", "E", "JHAWK5", "ALL", 20, "KMCI"),
    record("KMCI", "E", "JHAWK5", "ALL", 30, "RW01L"),
    record("PAFA", "D", "PADDR", "ALL", 10, "PAFA", area="SCANP"),
    record("05C", "E", "PROC1", "RW01", 10, "FIX01", region="K5"),
    "SUSAP KOAKK2DCNDEL5",
]

AIRPORTS = [
    "KOAK",
    "OAK",
    "KOAJ",
    "KSFO",
    "SFO",
    "OA",
    "O",
    "K",
    "",
    "PANC",
    "PAFA",
    "EGLL",
    "05C",
]
DP_NAMES = [
    "CNDEL5",
    "CNDEL54",
    "CNDEL",
    "CNDEL6",
    "SIDBE5",
    "ELLIS2",
    "PADDR",
    "ZZZZ",
    "",
]
STAR_NAMES = ["SCOLA1", "BDEGA3", "PROC1", "ZZZZ", ""]


def _clear_module_state() -> None:
    """Drop the CIFP index and any lookup cache left by an earlier test."""
    cifp.clear_cifp_cache()
    for lookup in (
        cifp.get_dp_data,
        cifp.get_star_data,
        cifp.get_approaches_for_airport,
    ):
        cache_clear = getattr(lookup, "cache_clear", None)
        if cache_clear is not None:
            cache_clear()


@pytest.fixture(autouse=True)
def _isolate_cifp_state() -> Iterator[None]:
    """Give every test a clean index, before and after it runs."""
    _clear_module_state()
    yield
    _clear_module_state()


@pytest.fixture
def cifp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CIFP lookups at the fixture file instead of the cache."""
    path = tmp_path / "FAACIFP18-2601"
    path.write_text("\n".join(FIXTURE_LINES) + "\n", encoding="latin-1")
    monkeypatch.setattr(cifp, "get_cifp_cache_path", lambda: path)
    return path


# The outlying areas carry their procedures under the airport's own P/T ident:
# SCANP has Alaska's (and Canada's, under a C ident), SPACP has Hawaii's and
# the Pacific territories', SLAMP has Puerto Rico's and the US Virgin Islands'.
# The SUSAP area carries P/T FAA LIDs of its own, which the index leaves out.
# This file is separate from the one above, whose records pin what a CONUS
# lookup answers.
AREA_LINES = [
    record("PANC", "D", "TED5", "ALL", 10, "TED", area="SCANP", region="PA"),
    record("PANC", "D", "TED5", "ALL", 20, "KNIK", area="SCANP", region="PA"),
    record(
        "PHNL",
        "E",
        "BOOKE8",
        "ALL",
        10,
        "CANON",
        area="SPACP",
        region="PH",
        route_type="5",
    ),
    record(
        "PHNL",
        "E",
        "BOOKE8",
        "ALL",
        20,
        "BOOKE",
        area="SPACP",
        region="PH",
        route_type="5",
    ),
    record("TJSJ", "F", "I08", "ADDP", 10, "DDP", area="SLAMP", region="TJ"),
    record("CYQG", "D", "GUSHE1", "ALL", 10, "GUSHE", area="SCANP", region="CZ"),
    record("05C", "D", "PROC1", "ALL", 10, "FIX01", region="K5"),
    record("P19", "D", "CCODEX", "ALL", 10, "CODEX", region="K2"),
    record("T41", "E", "NODE1", "RW26", 10, "CENODE", region="K4"),
]


@pytest.fixture
def areas_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CIFP lookups at a file of outlying-area records."""
    path = tmp_path / "FAACIFP18-2603"
    path.write_text("\n".join(AREA_LINES) + "\n", encoding="latin-1")
    monkeypatch.setattr(cifp, "get_cifp_cache_path", lambda: path)
    return path


def _count_opens(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Wrap the module's ``open`` so a test can count the file reads."""
    opened: list[str] = []
    real_open = builtins.open

    def counting_open(file: object, *args: object, **kwargs: object):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(cifp, "open", counting_open, raising=False)
    return opened


def test_lookups_read_the_file_once(
    cifp_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_calls = _count_opens(monkeypatch)

    assert cifp.get_dp_data("KOAK", "CNDEL5") is not None
    assert cifp.get_dp_data("KSFO", "SIDBE5") is not None
    assert cifp.get_star_data("KOAK", "SCOLA1") is not None
    assert cifp.get_star_data("KSFO", "BDEGA3") is not None
    assert cifp.get_approaches_for_airport("KOAK") != {}
    assert cifp.get_approaches_for_airport("KSFO") != {}

    assert open_calls == [str(cifp_file)]


def test_airport_missing_from_the_index_never_opens_the_file(
    cifp_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An airport the index has no records for answers without a file read."""
    assert cifp.get_dp_data("KOAK", "CNDEL5") is not None

    open_calls = _count_opens(monkeypatch)

    assert cifp.get_dp_data("PANC", "PADDR") is None
    assert cifp.get_dp_data("EGLL", "BIG1A") is None
    assert cifp.get_dp_data("PAFA", "PADDR") is None
    assert cifp.get_star_data("EGLL", "BIG1A") is None
    assert cifp.get_approaches_for_airport("EGLL") == {}
    assert open_calls == []


def test_unreadable_cifp_file_logs_and_returns_empty(
    cifp_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A CIFP file that cannot be read is reported, not swallowed."""

    def unreadable_open(file: object, *args: object, **kwargs: object):
        raise OSError(13, "Permission denied", str(file))

    monkeypatch.setattr(cifp, "open", unreadable_open, raising=False)

    with caplog.at_level(logging.WARNING):
        assert cifp.get_dp_data("KOAK", "CNDEL5") is None
        assert cifp.get_star_data("KOAK", "SCOLA1") is None
        assert cifp.get_approaches_for_airport("KOAK") == {}

    messages = [record.getMessage() for record in caplog.records]
    assert any(str(cifp_file) in message for message in messages)


def test_cycle_switch_rebuilds_the_index(
    cifp_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    next_cycle = tmp_path / "FAACIFP18-2602"
    next_cycle.write_text(
        "\n".join([record("KOAK", "D", "CNDEL5", "ALL", 10, "NEWFX", route_type="4")])
        + "\n",
        encoding="latin-1",
    )
    route_clears: list[str] = []
    monkeypatch.setattr(
        route_distance,
        "clear_route_cache",
        lambda: route_clears.append("cleared"),
    )

    assert cifp.get_dp_data("KOAK", "CNDEL5").waypoints == ["SUNOL", "TRACY"]

    route_clears.clear()
    monkeypatch.setattr(cifp, "get_cifp_cache_path", lambda: next_cycle)

    assert cifp.get_dp_data("KOAK", "CNDEL5").waypoints == ["NEWFX"]
    assert route_clears == ["cleared"]


def test_prefix_match_keeps_variant_records(cifp_file: Path) -> None:
    """A filed CNDEL5 answers from the record whose raw id reads CNDEL54."""
    dp = cifp.get_dp_data("KOAK", "CNDEL5")

    assert dp is not None
    assert dp.identifier == "CNDEL5"
    assert dp.waypoints == ["SUNOL", "TRACY"]
    # The variant digit sits past the 6-character id field, so the old scan
    # never matched it, and neither does the index.
    assert cifp.get_dp_data("KOAK", "CNDEL54") is None
    # A name that is a prefix of the record's id still matches it.
    assert cifp.get_dp_data("KOAK", "CNDEL").waypoints == ["SUNOL", "TRACY"]


def test_star_without_a_common_route_uses_its_runway_transitions(
    cifp_file: Path,
) -> None:
    star = cifp.get_star_data("KOAK", "SCOLA1")

    assert star is not None
    assert star.waypoints == ["SCAG"]


def test_dp_keeps_navaid_named_like_its_airport(cifp_file: Path) -> None:
    """KDEN PLAIN2 flies its DEN VOR, which the old suffix filter dropped."""
    dp = cifp.get_dp_data("KDEN", "PLAIN2")

    assert dp is not None
    assert dp.waypoints == ["DEN", "PLAIN"]
    assert "KDEN" not in dp.waypoints
    assert not any(w.startswith("RW") for w in dp.waypoints)


def test_star_keeps_navaid_named_like_its_airport(cifp_file: Path) -> None:
    """KMCI JHAWK5 flies its MCI VOR, which the old suffix filter dropped."""
    star = cifp.get_star_data("KMCI", "JHAWK5")

    assert star is not None
    assert star.waypoints == ["MCI"]
    assert "KMCI" not in star.waypoints
    assert not any(w.startswith("RW") for w in star.waypoints)


def test_parsed_route_cache_holds_a_full_refresh() -> None:
    """The route cache is sized for a refresh's ~950 distinct routes."""
    assert route_distance._get_parsed_route.cache_info().maxsize == 4096


# --- The outlying areas: Alaska, Hawaii, Puerto Rico and the territories ---


def test_scanp_alaska_sid_is_found(areas_file: Path) -> None:
    dp = cifp.get_dp_data("PANC", "TED5")

    assert dp is not None
    assert dp.identifier == "TED5"
    assert dp.waypoints == ["TED", "KNIK"]


def test_spacp_hawaii_star_is_found(areas_file: Path) -> None:
    star = cifp.get_star_data("PHNL", "BOOKE8")

    assert star is not None
    assert star.waypoints == ["CANON", "BOOKE"]


def test_slamp_territory_approach_is_found(areas_file: Path) -> None:
    approaches = cifp.get_approaches_for_airport("TJSJ")

    assert set(approaches) == {"I08"}
    assert [fix.fix_identifier for fix in approaches["I08"].fixes] == ["DDP"]


def test_scanp_canadian_records_stay_out(areas_file: Path) -> None:
    assert cifp.get_dp_data("CYQG", "GUSHE1") is None


def test_susap_non_k_records_stay_out(areas_file: Path) -> None:
    assert cifp.get_dp_data("05C", "PROC1") is None


def test_susap_p_and_t_lids_stay_out(areas_file: Path) -> None:
    assert cifp.get_dp_data("P19", "CCODEX") is None
    assert cifp.get_star_data("T41", "NODE1") is None


def test_conus_lookups_unchanged(cifp_file: Path) -> None:
    """A CONUS airport answers under its K ident, with or without the K."""
    expected = cifp.get_dp_data("KOAK", "CNDEL5")

    assert expected is not None
    assert expected.waypoints == ["SUNOL", "TRACY"]
    assert cifp.get_dp_data("OAK", "CNDEL5") == expected
    assert cifp.get_star_data("OAK", "SCOLA1") == cifp.get_star_data("KOAK", "SCOLA1")


# --- Reference scans, as HEAD's cifp.py ran them per lookup ---


def _reference_dp_data(path: Path, airport: str, dp_name: str) -> cifp.CifpDP | None:
    """Copy of the per-lookup file scan get_dp_data used before the index.

    The waypoint filter at the end is the current one: a fix equal to the
    airport's own ident is dropped, a fix merely ending with its code is kept.
    """
    airport = airport.upper()
    if airport.startswith("K") and len(airport) == 4:
        airport_code = airport[1:]
    else:
        airport_code = airport

    dp_name = dp_name.upper().strip()
    dp_id_prefix = dp_name
    search_prefix = f"SUSAP K{airport_code}"

    dp_records: dict[str, list[tuple[str, int]]] = {}

    with open(path, encoding="latin-1") as f:
        for line in f:
            if not line.startswith(search_prefix):
                continue

            result = cifp.parse_dp_record(line)
            if not result:
                continue

            dp_id, transition, fix_id, sequence = result

            if not dp_id.startswith(dp_id_prefix):
                continue

            if transition not in dp_records:
                dp_records[transition] = []
            dp_records[transition].append((fix_id, sequence))

    if not dp_records:
        return None

    all_waypoints: list[str] = []
    seen_waypoints: set[str] = set()

    common_route_key = (
        "ALL" if "ALL" in dp_records else "" if "" in dp_records else None
    )
    if common_route_key is not None:
        for fix, _ in sorted(dp_records[common_route_key], key=lambda x: x[1]):
            if fix and fix not in seen_waypoints:
                all_waypoints.append(fix)
                seen_waypoints.add(fix)

    if not all_waypoints:
        enroute_transitions = sorted(
            t for t in dp_records if t and t != "ALL" and not t.startswith("RW")
        )
        if enroute_transitions:
            for fix, _ in sorted(
                dp_records[enroute_transitions[0]], key=lambda x: x[1]
            ):
                if fix and fix not in seen_waypoints:
                    all_waypoints.append(fix)
                    seen_waypoints.add(fix)

    airport_ident = (
        airport_code
        if len(airport_code) == 4 and airport_code[0] in ("P", "T")
        else f"K{airport_code}"
    )
    all_waypoints = [
        w for w in all_waypoints if w and not w.startswith("RW") and w != airport_ident
    ]

    if not all_waypoints:
        return None

    return cifp.CifpDP(identifier=dp_id_prefix, waypoints=all_waypoints)


def _reference_star_data(
    path: Path, airport: str, star_name: str
) -> cifp.CifpSTAR | None:
    """Copy of the per-lookup file scan get_star_data used before the index.

    The waypoint filter at the end is the current one: a fix equal to the
    airport's own ident is dropped, a fix merely ending with its code is kept.
    """
    airport = airport.upper()
    if airport.startswith("K") and len(airport) == 4:
        airport_code = airport[1:]
    else:
        airport_code = airport

    star_name = star_name.upper().strip()
    star_id_prefix = star_name
    search_prefix = f"SUSAP K{airport_code}"

    star_records: dict[str, list[tuple[str, int]]] = {}

    with open(path, encoding="latin-1") as f:
        for line in f:
            if not line.startswith(search_prefix):
                continue

            result = cifp.parse_star_record(line)
            if not result:
                continue

            star_id, transition, fix_id, sequence = result

            if not star_id.startswith(star_id_prefix):
                continue

            if transition not in star_records:
                star_records[transition] = []
            star_records[transition].append((fix_id, sequence))

    if not star_records:
        return None

    all_waypoints: list[str] = []
    seen_waypoints: set[str] = set()

    common_route_key = (
        "ALL" if "ALL" in star_records else "" if "" in star_records else None
    )
    if common_route_key is not None:
        for fix, _ in sorted(star_records[common_route_key], key=lambda x: x[1]):
            if fix and fix not in seen_waypoints:
                all_waypoints.append(fix)
                seen_waypoints.add(fix)

    if not all_waypoints:
        rw_transitions = sorted(t for t in star_records if t.startswith("RW"))
        if rw_transitions:
            for fix, _ in sorted(star_records[rw_transitions[0]], key=lambda x: x[1]):
                if fix and fix not in seen_waypoints:
                    all_waypoints.append(fix)
                    seen_waypoints.add(fix)

    airport_ident = (
        airport_code
        if len(airport_code) == 4 and airport_code[0] in ("P", "T")
        else f"K{airport_code}"
    )
    all_waypoints = [
        w for w in all_waypoints if w and not w.startswith("RW") and w != airport_ident
    ]

    if not all_waypoints:
        return None

    return cifp.CifpSTAR(identifier=star_id_prefix, waypoints=all_waypoints)


def _reference_approaches(path: Path, airport: str) -> dict[str, cifp.CifpApproach]:
    """Copy of the per-lookup file scan get_approaches_for_airport used."""
    airport = airport.upper()
    if airport.startswith("K") and len(airport) == 4:
        airport_code = airport[1:]
    else:
        airport_code = airport

    search_prefix = f"SUSAP K{airport_code}"

    approaches: dict[str, cifp.CifpApproach] = {}

    with open(path, encoding="latin-1") as f:
        for line in f:
            if not line.startswith(search_prefix):
                continue

            fix = cifp.parse_approach_record(line)
            if not fix:
                continue

            if fix.approach_id not in approaches:
                approaches[fix.approach_id] = cifp.CifpApproach(
                    airport=airport_code,
                    approach_id=fix.approach_id,
                    approach_type=cifp._parse_approach_type(fix.approach_id),
                    runway=cifp._parse_runway_from_approach_id(fix.approach_id),
                )

            approaches[fix.approach_id].fixes.append(fix)

    return approaches


@pytest.mark.parametrize(
    ("airport", "name"), [(a, n) for a in AIRPORTS for n in DP_NAMES]
)
def test_index_matches_the_old_scan_for_dps(
    cifp_file: Path, airport: str, name: str
) -> None:
    assert cifp.get_dp_data(airport, name) == _reference_dp_data(
        cifp_file, airport, name
    )


@pytest.mark.parametrize(
    ("airport", "name"), [(a, n) for a in AIRPORTS for n in STAR_NAMES]
)
def test_index_matches_the_old_scan_for_stars(
    cifp_file: Path, airport: str, name: str
) -> None:
    assert cifp.get_star_data(airport, name) == _reference_star_data(
        cifp_file, airport, name
    )


@pytest.mark.parametrize("airport", AIRPORTS)
def test_index_matches_the_old_scan_for_approaches(
    cifp_file: Path, airport: str
) -> None:
    assert cifp.get_approaches_for_airport(airport) == _reference_approaches(
        cifp_file, airport
    )
