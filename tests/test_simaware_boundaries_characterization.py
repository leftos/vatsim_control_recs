"""Characterization of the SimAware boundary loader in the weather daemon.

Pins the current output of ``load_preset_grouping_data``, ``map_grouping_to_boundaries``,
``expand_plus_pattern``, ``combine_polygons`` and ``get_all_grouping_boundaries``. Preset
groupings and SimAware boundaries are fixture JSON files written under ``tmp_path``; the
module's default directories are patched to point there when a test leaves them out.
"""

import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.weather_daemon import simaware_boundaries as sb


def _write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    return path


# load_preset_grouping_data

NCT_D = {
    "airports": ["KSFO", "KOAK"],
    "position_prefixes": ["SFO"],
    "position_suffixes": ["DEP"],
    "facility_id": "NCT",
}
FAT_SINGLE = {"airports": ["KFAT"], "facility_id": "FAT"}
FACILITY_ONLY = {"facility_id": "L30"}


def _old_format(airports: list[str]) -> dict[str, Any]:
    return {
        "airports": airports,
        "position_prefixes": None,
        "position_suffixes": None,
        "facility_id": None,
    }


def test_load_preset_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert sb.load_preset_grouping_data(tmp_path / "missing") == {}


def test_load_preset_keeps_multi_airport_and_facility_groupings(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "ZOA.json",
        {
            "NCT D": NCT_D,
            "Old pair": ["KSFO", "KOAK"],
            "Old single": ["KSFO"],
            "Old empty": [],
            "FAT": FAT_SINGLE,
            "L30 only": FACILITY_ONLY,
            "Single no facility": {"airports": ["KSJC"]},
            "Single null facility": {"airports": ["KSJC"], "facility_id": None},
            "Empty dict": {},
            "Number": 5,
            "Text": "KSFO",
            "Null": None,
        },
    )

    result = sb.load_preset_grouping_data(tmp_path)

    assert result == {
        "NCT D": NCT_D,
        "Old pair": _old_format(["KSFO", "KOAK"]),
        "FAT": FAT_SINGLE,
        "L30 only": FACILITY_ONLY,
    }
    assert list(result) == ["NCT D", "Old pair", "FAT", "L30 only"]


def test_load_preset_skips_non_dict_file(tmp_path: Path) -> None:
    _write_json(tmp_path / "list.json", [["KSFO", "KOAK"]])
    _write_json(tmp_path / "string.json", "NCT")
    _write_json(tmp_path / "ZOA.json", {"NCT D": NCT_D})

    assert sb.load_preset_grouping_data(tmp_path) == {"NCT D": NCT_D}


def test_load_preset_ignores_non_json_files(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("not json", encoding="utf-8")
    _write_json(tmp_path / "ZOA.json", {"NCT D": NCT_D})

    assert sb.load_preset_grouping_data(tmp_path) == {"NCT D": NCT_D}


def test_load_preset_skips_non_string_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_json(tmp_path / "ZOA.json", {})
    fake_json = SimpleNamespace(
        load=lambda f: {1: ["KSFO", "KOAK"], "Pair": ["KSFO", "KOAK"]}
    )
    monkeypatch.setattr(sb, "json", fake_json)

    assert sb.load_preset_grouping_data(tmp_path) == {
        "Pair": _old_format(["KSFO", "KOAK"])
    }


def test_load_preset_warns_on_invalid_json_and_keeps_going(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    _write_json(tmp_path / "ZOA.json", {"NCT D": NCT_D})

    assert sb.load_preset_grouping_data(tmp_path) == {"NCT D": NCT_D}
    assert capsys.readouterr().out == (
        "  Warning: skipping unreadable grouping file bad.json: "
        "Expecting value: line 1 column 1 (char 0)\n"
    )


def test_load_preset_warns_on_undecodable_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "binary.json").write_bytes(b"\xff\xfe\x00")

    assert sb.load_preset_grouping_data(tmp_path) == {}
    assert capsys.readouterr().out == (
        "  Warning: skipping unreadable grouping file binary.json: "
        "'utf-8' codec can't decode byte 0xff in position 0: invalid start byte\n"
    )


def test_load_preset_warns_on_unopenable_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "folder.json").mkdir()
    _write_json(tmp_path / "ZOA.json", {"NCT D": NCT_D})

    assert sb.load_preset_grouping_data(tmp_path) == {"NCT D": NCT_D}
    out = capsys.readouterr().out
    # The OSError text differs by platform (EISDIR on Linux, EACCES on Windows).
    assert out.startswith("  Warning: skipping unreadable grouping file folder.json: ")
    assert out.count("\n") == 1


def test_load_preset_default_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_json(tmp_path / "ZOA.json", {"NCT D": NCT_D})
    monkeypatch.setattr(sb, "PRESET_GROUPINGS_DIR", tmp_path)

    assert sb.load_preset_grouping_data() == {"NCT D": NCT_D}


def test_load_preset_default_dir_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sb, "PRESET_GROUPINGS_DIR", tmp_path / "missing")

    assert sb.load_preset_grouping_data() == {}


# map_grouping_to_boundaries

SFO = [[37.6, -122.4], [37.7, -122.4], [37.7, -122.3], [37.6, -122.3]]
SFO_DEP = [[37.5, -122.6], [37.8, -122.6], [37.8, -122.2], [37.5, -122.2]]
OAK = [[37.7, -122.2], [37.8, -122.2], [37.8, -122.1], [37.7, -122.1]]
MOD_SAME_START = [*SFO[:3], [37.65, -122.35]]
NCT_MAIN = [[37.0, -123.0], [39.0, -123.0], [39.0, -121.0], [37.0, -121.0]]
O90_MAIN = [[37.4, -122.5], [38.0, -122.5], [38.0, -122.0], [37.4, -122.0]]
L30_MAIN = [[35.5, -115.6], [36.5, -115.6], [36.5, -114.6], [35.5, -114.6]]
TWO_POINTS = [[1.0, 1.0], [2.0, 2.0]]

BOUNDARIES = {
    "NCT": {
        "SFO": {"coordinates": SFO},
        "SFO_DEP": {"coordinates": SFO_DEP},
        "OAK": {"coordinates": OAK},
        "MOD": {"coordinates": MOD_SAME_START},
        "SHORT": {"coordinates": TWO_POINTS},
        "EMPTY": {"coordinates": []},
        "NOCOORDS": {},
        "NCT": {"coordinates": NCT_MAIN},
        "O90": {"coordinates": O90_MAIN},
    },
    "L30": {"L30": {"coordinates": L30_MAIN}},
    "ZZZ": {},
    "EMPTYFAC": {"EMPTYFAC": {"coordinates": []}},
}


def _grouping(
    prefixes: list[str] | None,
    facility: str | None,
    suffixes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "airports": ["KAAA", "KBBB"],
        "position_prefixes": prefixes,
        "position_suffixes": suffixes,
        "facility_id": facility,
    }


@pytest.mark.parametrize(
    ("grouping_data", "expected"),
    [
        pytest.param(None, [], id="no-data"),
        pytest.param({}, [], id="empty-data"),
        pytest.param(_grouping(None, None), [], id="no-prefixes-no-facility"),
        pytest.param(_grouping(None, "XYZ"), [], id="no-prefixes-unknown-facility"),
        pytest.param(_grouping(None, "NCT"), [NCT_MAIN], id="no-prefixes-facility"),
        pytest.param(_grouping([], "NCT"), [NCT_MAIN], id="empty-prefixes-facility"),
        pytest.param(_grouping(None, "O90"), [O90_MAIN], id="no-prefixes-subfacility"),
        pytest.param(_grouping(None, "MC1"), [], id="no-prefixes-subfacility-no-key"),
        pytest.param(_grouping(None, "ZZZ"), [], id="no-prefixes-empty-folder"),
        pytest.param(_grouping(None, "EMPTYFAC"), [], id="no-prefixes-empty-coords"),
        pytest.param(_grouping(["SFO"], None), [], id="prefixes-no-facility"),
        pytest.param(_grouping(["SFO"], "XYZ"), [], id="prefixes-unknown-facility"),
        pytest.param(_grouping(["SFO"], "NCT", ["DEP"]), [SFO_DEP], id="dep-suffix"),
        pytest.param(
            _grouping(["SFO"], "NCT", ["APP", "DEP"]), [SFO_DEP], id="dep-beats-app"
        ),
        pytest.param(_grouping(["SFO"], "NCT", ["APP"]), [SFO], id="app-suffix"),
        pytest.param(_grouping(["SFO"], "NCT", None), [SFO], id="no-suffix"),
        pytest.param(_grouping(["SFO"], "NCT", []), [SFO], id="empty-suffixes"),
        pytest.param(_grouping(["SFO"], "NCT", ["TWR"]), [SFO], id="other-suffix"),
        pytest.param(_grouping(["OAK"], "NCT", ["DEP"]), [OAK], id="dep-without-key"),
        pytest.param(_grouping(["SFO", "OAK"], "NCT"), [SFO, OAK], id="two-prefixes"),
        pytest.param(_grouping(["SFO", "MOD"], "NCT"), [SFO], id="same-first-points"),
        pytest.param(_grouping(["SFO", "SFO"], "NCT"), [SFO], id="repeated-prefix"),
        pytest.param(_grouping(["UNKNOWN"], "NCT"), [NCT_MAIN], id="folder-fallback"),
        pytest.param(
            _grouping(["UNKNOWN", "OTHER"], "NCT"), [NCT_MAIN], id="fallback-deduped"
        ),
        pytest.param(_grouping(["SHORT"], "NCT"), [], id="under-three-points"),
        pytest.param(_grouping(["EMPTY", "NOCOORDS"], "NCT"), [], id="no-coords"),
        pytest.param(_grouping(["SFO"], "O90"), [SFO], id="subfacility-prefix"),
        pytest.param(_grouping(["X"], "ZZZ"), [], id="empty-folder"),
        pytest.param(_grouping(["LAS"], "L30"), [L30_MAIN], id="l30-fallback"),
    ],
)
def test_map_grouping_to_boundaries(
    grouping_data: dict[str, Any] | None, expected: list[list[list[float]]]
) -> None:
    assert sb.map_grouping_to_boundaries(grouping_data, BOUNDARIES) == expected


# expand_plus_pattern

KNOWN_GROUPINGS: dict[str, dict[str, Any]] = {
    "N90": {},
    "B90 Combined": {},
    "A80 A80": {},
    "D10 Combined": {},
    "D10 D10": {},
    "NCT E": {},
}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("NCT E", ["NCT E"]),
        ("N90", ["N90"]),
        ("", [""]),
        ("NCT E+R", ["NCT E", "NCT R"]),
        ("NCT A+B+C", ["NCT A", "NCT B", "NCT C"]),
        ("NCT E+", ["NCT E", "NCT "]),
        ("NCT\tE+R", ["NCT E", "NCT R"]),
        ("N90+B90", ["N90", "B90 Combined"]),
        ("N90+A80", ["N90", "A80 A80"]),
        ("N90+D10", ["N90", "D10 Combined"]),
        ("N90+C90", ["N90", "C90"]),
        ("+N90", ["", "N90"]),
        ("N90+", ["N90", ""]),
        ("N90+B90 Extra", ["N90+B90 Extra"]),
        ("NCT A + B", ["NCT A + B"]),
        ("NCT A+B C", ["NCT A+B C"]),
        (" N90+B90", [" N90+B90"]),
    ],
)
def test_expand_plus_pattern(name: str, expected: list[str]) -> None:
    assert sb.expand_plus_pattern(name, KNOWN_GROUPINGS) == expected


def test_expand_plus_pattern_with_no_groupings() -> None:
    assert sb.expand_plus_pattern("A+B", {}) == ["A", "B"]


# combine_polygons

S0 = [[0, 0], [0, 1], [1, 1], [1, 0]]
S1 = [[3, 0], [3, 1], [4, 1], [4, 0]]
S2 = [[6, 0], [6, 1], [7, 1], [7, 0]]
FAR = [[50, 50], [50, 51], [51, 51]]
NEAR_S0 = [[1.03, 0], [1.03, 1], [2, 1], [2, 0]]
JUST_OUT_S0 = [[1.06, 0], [1.06, 1], [2, 1], [2, 0]]


def _tuples(poly: list[list[float]]) -> list[tuple[float, float]]:
    return [(p[0], p[1]) for p in poly]


def test_combine_polygons_empty() -> None:
    assert sb.combine_polygons([]) == []


def test_combine_polygons_single_converts_to_tuples() -> None:
    result = sb.combine_polygons([[[1.0, 2.0, 99.0], [3.0, 4.0]]])

    assert result == [[(1.0, 2.0), (3.0, 4.0)]]
    assert all(isinstance(point, tuple) for point in result[0])


def test_combine_polygons_far_apart_kept_separate() -> None:
    assert sb.combine_polygons([S0, FAR]) == [_tuples(S0), _tuples(FAR)]


def test_combine_polygons_default_threshold_merges_neighbors() -> None:
    assert sb.combine_polygons([S0, NEAR_S0]) == [[(0, 0), (2, 0), (2, 1), (0, 1)]]


def test_combine_polygons_default_threshold_keeps_gap() -> None:
    assert sb.combine_polygons([S0, JUST_OUT_S0]) == [
        _tuples(S0),
        _tuples(JUST_OUT_S0),
    ]


def test_combine_polygons_distance_equal_to_threshold_merges() -> None:
    assert sb.combine_polygons([S0, S1], neighbor_threshold=2.0) == [
        [(0, 0), (4, 0), (4, 1), (0, 1)]
    ]


def test_combine_polygons_distance_over_threshold_separate() -> None:
    assert sb.combine_polygons([S0, S1], neighbor_threshold=1.9) == [
        _tuples(S0),
        _tuples(S1),
    ]


def test_combine_polygons_nan_threshold_never_merges() -> None:
    assert sb.combine_polygons([S0, FAR], neighbor_threshold=float("nan")) == [
        _tuples(S0),
        _tuples(FAR),
    ]


def test_combine_polygons_merges_transitively_in_first_index_order() -> None:
    result = sb.combine_polygons([FAR, S0, S2, S1], neighbor_threshold=2.0)

    assert result == [_tuples(FAR), [(0, 0), (7, 0), (7, 1), (0, 1)]]


def test_combine_polygons_merged_group_before_single() -> None:
    result = sb.combine_polygons([S0, NEAR_S0, FAR])

    assert result == [[(0, 0), (2, 0), (2, 1), (0, 1)], _tuples(FAR)]


def test_combine_polygons_merged_points_under_three() -> None:
    assert sb.combine_polygons([[[5, 5]], [[5, 5]]]) == [[(5, 5), (5, 5)]]


# get_all_grouping_boundaries

SQ_A = [[37.0, -122.0], [37.5, -122.0], [37.5, -121.5], [37.0, -121.5]]
SQ_B = [[37.0, -121.48], [37.5, -121.48], [37.5, -121.0], [37.0, -121.0]]
SQ_C = [[39.0, -120.0], [39.5, -120.0], [39.5, -119.5], [39.0, -119.5]]
HULL_AB = [(37.0, -122.0), (37.5, -122.0), (37.5, -121.0), (37.0, -121.0)]

PRESETS = {
    "NCT B": _grouping(["SFO"], "NCT"),
    "NCT C": _grouping(["SFO", "OAK"], "NCT"),
    "NCT E": _grouping(["RNO"], "NCT"),
    "NCT S": _grouping(["SFO", "RNO"], "NCT"),
    "KFAT Tower": {"airports": ["KFAT"], "facility_id": "FAT"},
    "KZZZ Tower": {
        "airports": ["KZZZ"],
        "facility_id": "NCT",
        "position_prefixes": ["SFO"],
    },
    "KZER Tower": {
        "airports": ["KZER"],
        "facility_id": "NCT",
        "position_prefixes": ["RNO"],
    },
    "KNOLON Tower": {
        "airports": ["KNOLON"],
        "facility_id": "NCT",
        "position_prefixes": ["OAK"],
    },
    "Noairports Tower": {
        "airports": [],
        "facility_id": "NCT",
        "position_prefixes": ["SFO"],
    },
}
NCT_BOUNDARIES = {
    "SFO": {"coordinates": SQ_A},
    "OAK": {"coordinates": SQ_B},
    "RNO": {"coordinates": SQ_C},
}
UNIFIED = {
    "KFAT": {"latitude": 36.776, "longitude": -119.718},
    "KZER": {"latitude": 0.0, "longitude": -119.0},
    "KNOLON": {"latitude": 36.0},
}


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    preset_dir = tmp_path / "presets"
    boundaries_dir = tmp_path / "boundaries"
    _write_json(preset_dir / "ZOA.json", PRESETS)
    _write_json(boundaries_dir / "NCT.json", NCT_BOUNDARIES)
    return preset_dir, boundaries_dir


NO_BOUNDARIES_OUT = (
    "  Warning: No SimAware boundary data found\n"
    "  Run 'python scripts/generate_simaware_boundaries.py' to download\n"
)


def test_get_all_without_boundaries_dir(
    dirs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    preset_dir, boundaries_dir = dirs

    result = sb.get_all_grouping_boundaries(
        ["NCT B"], preset_dir=preset_dir, boundaries_dir=boundaries_dir / "missing"
    )

    assert result == {}
    assert capsys.readouterr().out == NO_BOUNDARIES_OUT


def test_get_all_with_empty_boundaries_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    result = sb.get_all_grouping_boundaries(
        ["NCT B"], preset_dir=tmp_path / "none", boundaries_dir=empty
    )

    assert result == {}
    assert capsys.readouterr().out == NO_BOUNDARIES_OUT


def test_get_all_maps_every_kind_of_grouping(
    dirs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    preset_dir, boundaries_dir = dirs
    names = [
        "NCT B",
        "NCT C",
        "NCT S",
        "NCT E+B",
        "NCT B+C",
        "NCT B+Q",
        "NCT Q+Z",
        "Missing",
        "KFAT Tower",
        "KZZZ Tower",
        "KZER Tower",
        "KNOLON Tower",
        "Noairports Tower",
        "Unknown Tower",
    ]

    result = sb.get_all_grouping_boundaries(
        names,
        preset_dir=preset_dir,
        boundaries_dir=boundaries_dir,
        unified_airport_data=UNIFIED,
    )

    assert result == {
        "NCT B": [_tuples(SQ_A)],
        "NCT C": [HULL_AB],
        "NCT S": [_tuples(SQ_A), _tuples(SQ_C)],
        "NCT E+B": [_tuples(SQ_C), _tuples(SQ_A)],
        "NCT B+C": [HULL_AB],
        "NCT B+Q": [_tuples(SQ_A)],
        "KFAT Tower": [sb.generate_circle_polygon(36.776, -119.718, radius_nm=5)],
        "KZZZ Tower": [_tuples(SQ_A)],
        "KZER Tower": [_tuples(SQ_C)],
        "KNOLON Tower": [_tuples(SQ_B)],
        "Noairports Tower": [_tuples(SQ_A)],
    }
    assert list(result) == [
        "NCT B",
        "NCT C",
        "NCT S",
        "NCT E+B",
        "NCT B+C",
        "NCT B+Q",
        "KFAT Tower",
        "KZZZ Tower",
        "KZER Tower",
        "KNOLON Tower",
        "Noairports Tower",
    ]
    circle = result["KFAT Tower"][0]
    assert len(circle) == 33
    assert circle[0] == circle[-1]
    # The first point lies due east of the airport: 5 nm of longitude at its latitude.
    east_lon = -119.718 + 5 / (60.0 * math.cos(math.radians(36.776)))
    assert circle[0] == pytest.approx((36.776, east_lon))
    assert capsys.readouterr().out == (
        "  Matched 11/14 groupings to SimAware boundaries\n"
    )


@pytest.mark.parametrize("unified", [None, {}])
def test_get_all_tower_without_airport_data_uses_boundaries(
    dirs: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    unified: dict[str, Any] | None,
) -> None:
    preset_dir, boundaries_dir = dirs

    result = sb.get_all_grouping_boundaries(
        ["KFAT Tower", "KZZZ Tower"],
        preset_dir=preset_dir,
        boundaries_dir=boundaries_dir,
        unified_airport_data=unified,
    )

    assert result == {"KZZZ Tower": [_tuples(SQ_A)]}
    assert capsys.readouterr().out == (
        "  Matched 1/2 groupings to SimAware boundaries\n"
    )


def test_get_all_no_matches_prints_nothing(
    dirs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    preset_dir, boundaries_dir = dirs

    result = sb.get_all_grouping_boundaries(
        ["Missing", "NCT Q+Z"], preset_dir=preset_dir, boundaries_dir=boundaries_dir
    )

    assert result == {}
    assert capsys.readouterr().out == ""


def test_get_all_empty_names(
    dirs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    preset_dir, boundaries_dir = dirs

    assert (
        sb.get_all_grouping_boundaries(
            [], preset_dir=preset_dir, boundaries_dir=boundaries_dir
        )
        == {}
    )
    assert capsys.readouterr().out == ""


def test_get_all_default_dirs(
    dirs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    preset_dir, boundaries_dir = dirs
    monkeypatch.setattr(sb, "PRESET_GROUPINGS_DIR", preset_dir)
    monkeypatch.setattr(sb, "SIMAWARE_BOUNDARIES_DIR", boundaries_dir)

    assert sb.get_all_grouping_boundaries(["NCT C"]) == {"NCT C": [HULL_AB]}
    assert capsys.readouterr().out == (
        "  Matched 1/1 groupings to SimAware boundaries\n"
    )
