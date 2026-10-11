"""Characterization of the weather index page builders.

Pins the current output of ``build_airport_markers``, ``generate_weather_regions``,
``build_sidebar_html`` and ``generate_html`` over inputs that reach each of their
branches. The marker lists are asserted exactly; the HTML and the region grids are
pinned by the sha256 of their output plus a few anchor substrings that say what a
changed hash broke.

The SimAware boundary lookup is the only boundary ``generate_html`` crosses (it reads
the boundary cache and may download); it is faked and its calls are recorded.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.weather_daemon import index_generator
from scripts.weather_daemon.index_generator import (
    build_airport_markers,
    build_sidebar_html,
    generate_html,
    generate_weather_regions,
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _point(
    icao: str | None, category: str | None, lat: Any, lon: Any, **extra: Any
) -> dict[str, Any]:
    point: dict[str, Any] = {"lat": lat, "lon": lon, **extra}
    if icao is not None:
        point["icao"] = icao
    if category is not None:
        point["category"] = category
    return point


UNIFIED: dict[str, Any] = {
    "KSFO": {
        "far139": "I E",
        "name": "SAN FRANCISCO INTL",
        "latitude": 37.6,
        "longitude": -122.4,
    },
    "KOAK": {"FAR_139_TYPE_CODE": "I D", "name": "", "lat": 37.7, "lon": -122.2},
    "KSJC": {"far139": "I C", "name": "san jose mineta"},
    "KSMF": {
        "far139": "",
        "FAR_139_TYPE_CODE": "I C",
        "name": "SACRAMENTO INTL",
        "latitude": 38.7,
        "longitude": -121.6,
    },
    "KPAO": {
        "far139": "I B",
        "name": "PALO ALTO",
        "latitude": 37.46,
        "longitude": -122.11,
    },
    "KHAF": {"name": "HALF MOON BAY", "latitude": 37.5, "longitude": -122.5},
    "KMRY": {"far139": "I C", "name": "MONTEREY", "latitude": 0, "longitude": -121.8},
    "KBUR": {"far139": "I D", "name": "BURBANK"},
    "KLAX": {"far139": "I E", "name": "LOS ANGELES INTL"},
    "KONT": {"far139": "I C", "name": "ONTARIO", "latitude": 34.06},
}

WEATHER_GROUPINGS: dict[str, list[dict[str, Any]]] = {
    "ZOA": [
        {
            "airport_weather_points": [
                _point(
                    "KSFO",
                    "VFR",
                    37.6,
                    -122.4,
                    visibility=10,
                    ceiling=None,
                    wind="27010KT",
                    phenomena=["BR"],
                    taf_changes=[{"type": "TEMPO"}],
                ),
                _point("KOAK", "MVFR", 37.7, -122.2),
                _point("KSJC", "IFR", 37.36, -121.93, visibility=2, ceiling=600),
                _point("KSMF", None, 38.7, -121.6),
                _point("KPAO", "VFR", 37.46, -122.11),
                _point("KHAF", "VFR", 37.5, -122.5),
                _point("KXXX", "VFR", 37.0, -122.0),
                _point(None, "VFR", 37.0, -122.0),
                _point("", "VFR", 37.0, -122.0),
            ]
        },
        {"name": "No points"},
    ],
    "ZLA": [
        {
            "airport_weather_points": [
                _point("KSFO", "IFR", 1.0, 1.0),
                _point("KBUR", "LIFR", 34.2, None),
                _point("KMRY", "XYZ", 36.59, -121.85),
                _point("KLAX", "VFR", 0, -118.4),
                _point("KONT", "VFR", None, -117.6),
            ]
        }
    ],
}


def _marker(
    icao: str,
    name: str,
    lat: Any,
    lon: Any,
    category: str,
    color: str,
    tier: str,
    **weather: Any,
) -> dict[str, Any]:
    return {
        "icao": icao,
        "name": name,
        "lat": lat,
        "lon": lon,
        "category": category,
        "color": color,
        "tier": tier,
        "visibility": weather.get("visibility"),
        "ceiling": weather.get("ceiling"),
        "wind": weather.get("wind"),
        "phenomena": weather.get("phenomena", []),
        "taf_changes": weather.get("taf_changes", []),
    }


# build_airport_markers


@pytest.mark.parametrize("unified", [None, {}])
def test_markers_need_airport_data(unified: dict[str, Any] | None) -> None:
    assert build_airport_markers(WEATHER_GROUPINGS, unified) == []


def test_markers_from_weather_points_keep_far139_airports_with_coordinates() -> None:
    assert build_airport_markers(WEATHER_GROUPINGS, UNIFIED) == [
        _marker(
            "KSFO",
            "San Francisco Intl",
            37.6,
            -122.4,
            "VFR",
            "#00cc00",
            "major",
            visibility=10,
            wind="27010KT",
            phenomena=["BR"],
            taf_changes=[{"type": "TEMPO"}],
        ),
        _marker("KOAK", "", 37.7, -122.2, "MVFR", "#0066ff", "major"),
        _marker(
            "KSJC",
            "San Jose Mineta",
            37.36,
            -121.93,
            "IFR",
            "#ff0000",
            "regional",
            visibility=2,
            ceiling=600,
        ),
        _marker("KSMF", "Sacramento Intl", 38.7, -121.6, "UNK", "#888888", "regional"),
        _marker("KMRY", "Monterey", 36.59, -121.85, "XYZ", "#888888", "regional"),
    ]


def test_markers_without_weather_points_come_from_far139_airport_data() -> None:
    groupings = {
        "ZOA": [{"name": "No points"}, {"airport_weather_points": []}],
        "ZLA": [],
    }
    assert build_airport_markers(groupings, UNIFIED) == [
        _marker("KSFO", "San Francisco Intl", 37.6, -122.4, "UNK", "#888888", "major"),
        _marker("KOAK", "", 37.7, -122.2, "UNK", "#888888", "major"),
        _marker("KSMF", "Sacramento Intl", 38.7, -121.6, "UNK", "#888888", "regional"),
    ]


def test_markers_with_no_groupings_use_airport_data() -> None:
    assert [m["icao"] for m in build_airport_markers({}, UNIFIED)] == [
        "KSFO",
        "KOAK",
        "KSMF",
    ]


def test_markers_lifr_point_gets_magenta() -> None:
    groupings = {
        "ZOA": [{"airport_weather_points": [_point("KBUR", "LIFR", 34.2, -118.36)]}]
    }
    assert build_airport_markers(groupings, UNIFIED) == [
        _marker("KBUR", "Burbank", 34.2, -118.36, "LIFR", "#ff00ff", "major"),
    ]


# generate_weather_regions

SQUARE = [(0.0, 0.0), (0.0, 2.0), (2.0, 2.0), (2.0, 0.0)]
TRIANGLE = [(0.0, 0.0), (0.0, 2.0), (2.0, 0.0)]
REGION_AIRPORTS = [
    {"icao": "KAAA", "lat": 0.25, "lon": 0.25, "category": "VFR"},
    {"icao": "KBBB", "lat": 1.75, "lon": 1.75},
]


@pytest.mark.parametrize(
    ("boundary", "airports"),
    [([], REGION_AIRPORTS), (SQUARE, []), ([], [])],
    ids=["no-boundary", "no-airports", "neither"],
)
def test_regions_need_a_boundary_and_airports(
    boundary: list[tuple[float, float]], airports: list[dict]
) -> None:
    assert generate_weather_regions(boundary, airports) == []


def test_regions_skip_cells_far_from_every_airport() -> None:
    far = [{"icao": "KFAR", "lat": 10.0, "lon": 10.0, "category": "IFR"}]
    assert generate_weather_regions(SQUARE, far, grid_resolution=0.5) == []


def test_regions_assign_cells_to_the_nearest_airport() -> None:
    regions = generate_weather_regions(SQUARE, REGION_AIRPORTS, grid_resolution=0.5)
    assert len(regions) == 8
    assert [r["category"] for r in regions] == [
        "VFR",
        "VFR",
        "VFR",
        "VFR",
        "UNK",
        "UNK",
        "UNK",
        "UNK",
    ]
    assert regions[0] == {
        "coords": [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5], [0.0, 0.0]],
        "category": "VFR",
    }
    assert _sha256(json.dumps(regions)) == REGION_HASHES["square"]


def test_regions_skip_cells_outside_the_boundary() -> None:
    regions = generate_weather_regions(TRIANGLE, REGION_AIRPORTS, grid_resolution=0.5)
    assert _sha256(json.dumps(regions)) == REGION_HASHES["triangle"]


def test_regions_default_grid_resolution() -> None:
    regions = generate_weather_regions(SQUARE, REGION_AIRPORTS)
    assert _sha256(json.dumps(regions)) == REGION_HASHES["default-grid"]


def test_regions_tie_goes_to_the_first_airport() -> None:
    tied = [
        {"icao": "KAAA", "lat": 1.0, "lon": 1.0, "category": "VFR"},
        {"icao": "KCCC", "lat": 1.0, "lon": 1.0, "category": "IFR"},
    ]
    regions = generate_weather_regions(SQUARE, tied, grid_resolution=0.5)
    assert len(regions) == 4
    assert {r["category"] for r in regions} == {"VFR"}


REGION_HASHES = {
    "square": "c223a950328da893c97e23e4524b0638c03e39a623a16cdee8a5d8795072b71a",
    "triangle": "a95ae4a8de43bd4ddcef9caa0fd52b7ed76242ee40048852858c29186788b453",
    "default-grid": "5be4889c7191d161626f57eee72b4fabb1352785ea04ba9bfd4091d1c69c92d3",
}


# build_sidebar_html


def _grouping(
    name: str, filename: str, categories: dict[str, int], **extra: Any
) -> dict[str, Any]:
    return {"name": name, "filename": filename, "categories": categories, **extra}


SIDEBAR_GROUPINGS: dict[str, list[dict[str, Any]]] = {
    "ZOA": [
        _grouping("International Pacific", "intl.html", {"VFR": 1}, airport_count=4),
        _grouping("Bay Area", "bay.html", {"VFR": 2, "IFR": 1}, airport_count=12),
        _grouping("Quiet", "quiet.html", {"UNK": 3}, airport_count=3),
        _grouping(
            "Mine", "mine.html", {"MVFR": 1}, path_prefix="custom", is_custom=True
        ),
    ],
    "ZHN": [_grouping("Oahu", "oahu.html", {"LIFR": 1}, airport_count=2)],
    "ZAN": [_grouping("Anchorage", "anc.html", {"IFR": 1}, airport_count=1)],
    "ZXX": [_grouping("Nowhere", "nowhere.html", {})],
    "ZAU": [_grouping("Chicago Metro", "chi.html", {"VFR": 1}, airport_count=5)],
    "ZBW": [_grouping("Boston Metro", "bos.html", {"MVFR": 1}, airport_count=3)],
    "custom": [
        _grouping("Zeta", "zeta.html", {"VFR": 1}, airport_count=2),
        _grouping("International Custom", "ic.html", {"MVFR": 2}),
        _grouping("Empty", "empty.html", {"UNK": 1}),
    ],
}

SIDEBAR_STATS: dict[str, dict[str, int]] = {
    "ZOA": {"LIFR": 1, "IFR": 2, "MVFR": 3, "VFR": 4, "UNK": 5, "total": 15},
    "ZHN": {"LIFR": 0, "IFR": 0, "MVFR": 0, "VFR": 1},
    "ZAN": {"IFR": 1},
    "custom": {"LIFR": 2, "VFR": 1},
}


def test_sidebar_with_every_kind_of_section() -> None:
    html = build_sidebar_html(SIDEBAR_GROUPINGS, SIDEBAR_STATS)
    assert '<a href="https://www.weather.gov/hfo/aviation" target="_blank"' in html
    assert '<a href="https://www.weather.gov/aawu" target="_blank"' in html
    assert 'href="https://aviationweather.gov/pdwb/?cwsu=zoa"' in html
    assert (
        '<div class="artcc-stats"><span class="stat stat-lifr">1</span><span class="stat stat-ifr">2</span>'
        in html
    )
    assert 'href="custom/mine.html" class="grouping-link"' in html
    assert '<span class="custom-marker">' in html
    assert "quiet.html" not in html
    assert "empty.html" not in html
    assert 'data-artcc="custom"' in html
    assert html.index("bay.html") < html.index("intl.html")
    assert html.index("zeta.html") < html.index("ic.html")
    assert html.index('data-artcc="ZBW"') < html.index('data-artcc="ZAU"')
    assert 'href="ZBW/bos.html" class="grouping-link" data-grouping-id="1"' in html
    assert _sha256(html) == SIDEBAR_HASHES["full"]


def test_sidebar_omits_a_custom_section_without_metars() -> None:
    groupings = {"custom": [_grouping("Empty", "empty.html", {"UNK": 1})], "ZOA": []}
    html = build_sidebar_html(groupings, {})
    assert 'data-artcc="custom"' not in html
    assert 'data-artcc="ZOA"' in html
    assert _sha256(html) == SIDEBAR_HASHES["custom-without-metars"]


def test_sidebar_is_empty_without_groupings() -> None:
    assert build_sidebar_html({}, SIDEBAR_STATS) == ""


SIDEBAR_HASHES = {
    "full": "5a04629344962b8ecd966b06d2fb2ecb728f95bb5b6c46d6587e0763c9af615c",
    "custom-without-metars": "128aa8493aa8dae956ffdf02ec673ea71f4df61de304d7ce4e135722696af973",
}


# generate_html

HTML_BOUNDARIES: dict[str, list[list[tuple]]] = {
    "ZOA": [
        [(37.0, -123.0), (38.0, -123.0), (38.0, -121.0), (37.0, -121.0)],
        [(36.0, -122.0), (36.5, -122.0), (36.0, -121.5), (36.0, -122.0)],
    ],
    "ZLA": [[]],
    "ZDV": [],
    "ZAN": [[(60.0, -150.0), (61.0, -150.0), (61.0, -149.0)]],
    "ZUA": [[(13.4, 144.7), (13.6, 144.9), (13.5, 145.0)]],
}

HTML_POINTS = [
    _point("KSFO", "VFR", 37.6, -122.4, visibility=10, wind="27010KT"),
    _point("KOAK", "MVFR", 37.7, -122.2),
    _point("KXXX", "IFR", 37.1, -122.1),
    _point("KUNK", "UNK", 37.2, -122.2),
    _point("KNOL", "VFR", None, -122.3),
    _point("KSFO", "IFR", 37.6, -122.4),
    _point(None, "VFR", 37.3, -122.3),
    _point("KNLN", "VFR", 37.3, None),
    _point("KCAT", None, 37.4, -122.0),
    _point("KODD", "XYZ", 37.45, -122.05),
]

HTML_GROUPINGS: dict[str, list[dict[str, Any]]] = {
    "ZOA": [
        _grouping(
            "Bay Area",
            "bay.html",
            {"VFR": 2, "MVFR": 1},
            airport_count=3,
            airport_coords=[[37.6, -122.4], [37.7, -122.2], [37.3, -121.9]],
            airport_weather_points=HTML_POINTS,
        ),
        _grouping(
            "International Pacific",
            "intl.html",
            {"VFR": 1},
            airport_coords=[
                [37.6, -122.4],
                [37.7, -122.2],
                [37.3, -121.9],
                [37.5, -122.0],
            ],
        ),
        _grouping("Quiet", "quiet.html", {"UNK": 2}, airport_coords=[[37.0, -122.0]]),
        _grouping(
            "Single",
            "single.html",
            {"IFR": 1},
            airport_coords=[[38.0, -122.0]],
            airport_weather_points=[_point("KSMF", "IFR", 38.0, -122.0)],
        ),
        _grouping(
            "Pair",
            "pair.html",
            {"LIFR": 1},
            airport_coords=[[38.0, -122.0], [38.5, -121.5]],
        ),
        _grouping(
            "Duplicate pair",
            "dup.html",
            {"VFR": 1},
            airport_coords=[[38.0, -122.0], [38.0, -122.0]],
        ),
        _grouping("No coords", "nocoords.html", {"VFR": 1}),
    ],
    "ZLA": [
        _grouping(
            "SoCal",
            "socal.html",
            {"MVFR": 1},
            path_prefix="custom",
            is_custom=True,
            airport_weather_points=[_point("KUNK", "UNK", 34.0, -118.0)],
        ),
    ],
    "ZAN": [
        _grouping("Anchorage", "anc.html", {"IFR": 1}, airport_coords=[[61.2, -149.9]])
    ],
    "ZAU": [
        _grouping(
            "Chicago Metro", "chi.html", {"VFR": 1}, airport_coords=[[41.98, -87.9]]
        )
    ],
    "ZBW": [
        _grouping(
            "Boston Metro", "bos.html", {"MVFR": 1}, airport_coords=[[42.36, -71.0]]
        )
    ],
    "custom": [
        _grouping(
            "Custom Boundary",
            "cb.html",
            {"VFR": 1},
            airport_weather_points=[_point("KSFO", "VFR", 37.6, -122.4)],
        ),
        _grouping(
            "Custom Hull",
            "ch.html",
            {"MVFR": 1},
            airport_coords=[[40.0, -100.0], [41.0, -100.0], [40.5, -99.0]],
        ),
        _grouping("Custom Quiet", "cq.html", {}),
    ],
}

HTML_STATS: dict[str, dict[str, int]] = {
    "ZOA": {"LIFR": 1, "IFR": 1, "MVFR": 1, "VFR": 4, "UNK": 2, "total": 9},
    "ZAN": {"IFR": 1},
}

HTML_UNIFIED: dict[str, Any] = {
    "KSFO": {"name": "SAN FRANCISCO INTL"},
    "KOAK": {"name": ""},
}

SIMAWARE: dict[str, list[list[tuple[float, float]]]] = {
    "Bay Area": [
        [(37.0, -122.0), (37.5, -122.0), (37.5, -121.5)],
        [(36.0, -121.0), (36.2, -121.0), (36.0, -121.0)],
        [],
    ],
    "Custom Boundary": [[(39.0, -120.0), (39.5, -120.0), (39.5, -119.5)]],
}

MARKERS = [
    _marker("KSFO", "San Francisco Intl", 37.6, -122.4, "VFR", "#00cc00", "major"),
]


class FakeSimAware:
    """Stands in for the SimAware boundary lookup and records its calls."""

    def __init__(self, boundaries: dict[str, list[list[tuple[float, float]]]]) -> None:
        self.boundaries = boundaries
        self.calls: list[tuple[list[str], Any]] = []

    def __call__(
        self, names: list[str], unified_airport_data: Any = None
    ) -> dict[str, list[list[tuple[float, float]]]]:
        self.calls.append((list(names), unified_airport_data))
        return self.boundaries


@pytest.fixture
def simaware(monkeypatch: pytest.MonkeyPatch) -> FakeSimAware:
    fake = FakeSimAware(SIMAWARE)
    monkeypatch.setattr(index_generator, "get_all_grouping_boundaries", fake)
    return fake


ALL_GROUPING_NAMES = [
    "Anchorage",
    "Boston Metro",
    "Chicago Metro",
    "SoCal",
    "Bay Area",
    "International Pacific",
    "Quiet",
    "Single",
    "Pair",
    "Duplicate pair",
    "No coords",
    "Custom Boundary",
    "Custom Hull",
    "Custom Quiet",
]


def test_html_with_simaware_boundaries(
    simaware: FakeSimAware, capsys: pytest.CaptureFixture[str]
) -> None:
    html = generate_html(
        boundaries=HTML_BOUNDARIES,
        artcc_groupings=HTML_GROUPINGS,
        artcc_stats=HTML_STATS,
        timestamp="2026-10-10 12:00:00Z",
        tile_version=1700000000,
        unified_airport_data=HTML_UNIFIED,
        simaware_cache_dir=Path("cache"),
        airport_markers=MARKERS,
        basemap_url="https://example.test/{z}/{x}/{y}.png?key=abc",
    )
    assert simaware.calls == [(ALL_GROUPING_NAMES, HTML_UNIFIED)]
    assert (
        capsys.readouterr().out
        == "    Fetching SimAware TRACON boundaries for groupings...\n"
    )
    assert "Updated: 2026-10-10 12:00:00Z" in html
    assert "L.tileLayer('https://example.test/{z}/{x}/{y}.png?key=abc', {" in html
    assert "tiles/{z}/{x}/{y}.png?v=1700000000" in html
    assert (
        'const artccBounds = {"ZOA": {"south": 36.0, "north": 38.0, "west": -123.0, "east": -121.0}, '
        '"ZAN": {"south": 60.0, "north": 61.0, "west": -150.0, "east": -149.0}};'
    ) in html
    artcc_data = html.split("const artccData = ", 1)[1].split("\n", 1)[0]
    assert '"artcc": "ZAN"' in artcc_data
    assert '"artcc": "ZUA"' not in artcc_data
    assert 'const airportMarkers = [{"icao": "KSFO"' in html
    assert '"name": "Custom Hull", "artcc": null' in html
    assert '"1": {"name": "Boston Metro", "artcc": "ZBW"' in html
    assert '"icao": "KNLN"' not in html
    assert (
        '{"icao": "KCAT", "name": "", "lat": 37.4, "lon": -122.0, "category": null, "color": "#888888"'
        in html
    )
    assert (
        '"icao": "KODD", "name": "", "lat": 37.45, "lon": -122.05, "category": "XYZ", "color": "#888888"'
        in html
    )
    assert _sha256(html) == HTML_HASHES["simaware"]


def test_html_without_simaware_cache_falls_back_to_hulls(
    simaware: FakeSimAware, capsys: pytest.CaptureFixture[str]
) -> None:
    html = generate_html(
        HTML_BOUNDARIES,
        HTML_GROUPINGS,
        HTML_STATS,
        "2026-01-01 00:00:00Z",
        unified_airport_data=None,
        use_tile_layer=False,
        basemap_url="https://example.test/basemap.png",
    )
    assert simaware.calls == []
    assert capsys.readouterr().out == ""
    assert "tiles/{z}/{x}/{y}.png?v=0" in html
    assert "const airportMarkers = [];" in html
    assert _sha256(html) == HTML_HASHES["hulls"]


def test_html_with_nothing_to_show(simaware: FakeSimAware) -> None:
    html = generate_html(
        {},
        {},
        {},
        "2026-01-01 00:00:00Z",
        tile_version=5,
        simaware_cache_dir=Path("cache"),
        airport_markers=[],
        basemap_url="u",
    )
    assert simaware.calls == [([], None)]
    assert 'const artccData = {"type": "FeatureCollection", "features": []};' in html
    assert "const groupingPolygons = {};" in html
    assert "const artccBounds = {};" in html
    assert "const artccAirportData = {};" in html
    assert _sha256(html) == HTML_HASHES["empty"]


HTML_HASHES = {
    "simaware": "fa99af1e5418d2faba62177b5400a890ad7eb738d18481dea40bb22318c0c007",
    "hulls": "d126f1ff729d85a0ed82e380b5f92b5b2a327b7c3cd2b2fb057a292b16438326",
    "empty": "9a34a1c591982b2870f4f927d2064354e8e4cb3c1333b718daa0e1ea5b36eb76",
}
