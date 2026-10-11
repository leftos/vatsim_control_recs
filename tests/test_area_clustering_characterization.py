"""Characterization of the weather-briefing area clustering.

Pins the current output of ``calculate_optimal_k``, ``kmeans_clustering``,
``generate_area_name``, ``create_fallback_area_groups`` and ``create_area_groups``
over inputs that reach each of their branches. The clustering is deterministic (the
first centroid is the most significant airport, then the farthest one), so every
result is asserted exactly.

Airports sit on the equator so that distances are proportional to longitude: one
degree is about 60 nm.
"""

from typing import Any

import pytest

from backend.briefing.area_clustering import AirportEntry, AreaClusterer

NM_PER_DEGREE = 60.0


def _info(
    lat: float | None = None,
    lon: float | None = None,
    *,
    far139: str = "",
    tower: str = "",
    city: str | None = "",
    state: str | None = "",
) -> dict[str, Any]:
    info: dict[str, Any] = {"far139": far139, "tower_type": tower}
    if city is not None:
        info["city"] = city
    if state is not None:
        info["state"] = state
    if lat is not None:
        info["latitude"] = lat
    if lon is not None:
        info["longitude"] = lon
    return info


def _entry(
    icao: str, size_priority: int, coords: tuple[float, float] | None
) -> AirportEntry:
    return (icao, {"category": "VFR"}, size_priority, coords)


def _icaos(clusters: list[list[AirportEntry]]) -> list[list[str]]:
    return [[entry[0] for entry in cluster] for cluster in clusters]


def _group(
    name: str,
    weather: dict[str, dict[str, Any]],
    members: list[tuple[str, int]],
    center: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "airports": [(icao, weather[icao], priority) for icao, priority in members],
        "center_icao": center,
    }


class _FakeDisambiguator:
    def __init__(self, names: dict[str, str | None]) -> None:
        self.names = names

    def get_pretty_name(self, icao: str) -> str | None:
        return self.names[icao]


# calculate_optimal_k


def _clusterer_with_extent(extent_nm: float) -> AreaClusterer:
    unified = {
        "KWST": _info(0.0, 0.0),
        "KEST": _info(0.0, extent_nm / NM_PER_DEGREE),
    }
    weather = {icao: {"category": "VFR"} for icao in unified}
    return AreaClusterer(weather, unified)


@pytest.mark.parametrize(
    ("num_towered", "extent_nm", "expected"),
    [
        (0, 100, 1),
        (1, 1200, 1),
        (2, 100, 2),
        (3, 1200, 2),
        (4, 100, 4),
        (6, 1200, 4),
        (7, 100, 5),
        (12, 100, 5),
        (7, 250, 6),
        (12, 400, 6),
        (7, 550, 7),
        (12, 550, 8),
        (13, 100, 6),
        (25, 100, 6),
        (13, 250, 8),
        (25, 400, 8),
        (13, 550, 10),
        (25, 1200, 10),
        (26, 100, 8),
        (40, 250, 8),
        (26, 400, 10),
        (40, 550, 10),
        (26, 800, 14),
        (40, 1200, 14),
        (41, 100, 10),
        (41, 250, 10),
        (41, 400, 12),
        (41, 550, 12),
        (41, 800, 15),
        (41, 1200, 18),
        (100, 1200, 18),
    ],
)
def test_optimal_k_by_towered_count_and_extent(
    num_towered: int, extent_nm: float, expected: int
) -> None:
    clusterer = _clusterer_with_extent(extent_nm)

    assert clusterer.calculate_optimal_k(num_towered, num_towered + 5) == expected


def test_optimal_k_uses_a_50_nm_extent_for_a_single_located_airport() -> None:
    unified = {"KONE": _info(0.0, 0.0), "KNOC": _info()}
    weather = {icao: {"category": "VFR"} for icao in unified}
    clusterer = AreaClusterer(weather, unified)

    assert clusterer.calculate_grouping_extent() == 50.0
    assert clusterer.calculate_optimal_k(8, 8) == 5


def test_optimal_k_ignores_airports_without_coordinates_in_the_extent() -> None:
    unified = {
        "KWST": _info(0.0, 0.0),
        "KNOC": _info(),
        "KEST": _info(0.0, 550 / NM_PER_DEGREE),
    }
    weather = {icao: {"category": "VFR"} for icao in unified}

    assert AreaClusterer(weather, unified).calculate_optimal_k(8, 8) == 8


@pytest.mark.parametrize(
    ("num_towered", "extent_nm", "expected"),
    [
        (12, 200, 6),
        (12, 500, 8),
        (25, 200, 8),
        (25, 500, 10),
        (40, 300, 10),
        (40, 600, 14),
        (41, 300, 12),
        (41, 600, 15),
        (41, 1000, 18),
    ],
)
def test_optimal_k_at_an_extent_limit_takes_the_next_step(
    monkeypatch: pytest.MonkeyPatch, num_towered: int, extent_nm: float, expected: int
) -> None:
    clusterer = _bare_clusterer()
    monkeypatch.setattr(clusterer, "calculate_grouping_extent", lambda: extent_nm)

    assert clusterer.calculate_optimal_k(num_towered, num_towered) == expected


def test_optimal_k_measures_the_extent_even_for_one_towered_airport() -> None:
    weather: dict[str, dict[str, Any]] = {
        "KAAA": {"category": "VFR", "atis": [{"callsign": "KAAA_ATIS"}]},
        "KBAD": {"category": "UNK"},
    }
    unified = {
        "KAAA": {"latitude": 0.0, "longitude": 0.0},
        "KBAD": {"latitude": 95.0, "longitude": 0.0},
    }

    with pytest.raises(ValueError, match="lat2 must be between -90 and 90"):
        AreaClusterer(weather, unified).calculate_optimal_k(1, 2)


# kmeans_clustering


def _bare_clusterer() -> AreaClusterer:
    return AreaClusterer({}, {})


def test_kmeans_of_no_airports_is_empty() -> None:
    assert _bare_clusterer().kmeans_clustering([], 3) == []


def test_kmeans_of_airports_without_coordinates_is_empty() -> None:
    airports = [_entry("KAAA", 0, None), _entry("KBBB", 1, None)]

    assert _bare_clusterer().kmeans_clustering(airports, 1) == []


def test_kmeans_with_no_more_located_airports_than_k_gives_one_cluster_each() -> None:
    airports = [
        _entry("KAAA", 5, (0.0, 0.0)),
        _entry("KNOC", 0, None),
        _entry("KCCC", 0, (0.0, 3.0)),
    ]

    clusters = _bare_clusterer().kmeans_clustering(airports, 2)

    assert clusters == [[airports[0]], [airports[2]]]


def test_kmeans_with_k_one_keeps_every_located_airport_in_input_order() -> None:
    airports = [
        _entry("KCCC", 5, (0.0, 2.0)),
        _entry("KAAA", 0, (0.0, 0.0)),
        _entry("KNOC", 0, None),
        _entry("KBBB", 5, (0.0, 9.0)),
    ]

    clusters = _bare_clusterer().kmeans_clustering(airports, 1)

    assert clusters == [[airports[0], airports[1], airports[3]]]


def _shifting_airports() -> list[AirportEntry]:
    """KB55 starts with KA00 and moves to the eastern cluster on the second pass."""
    return [
        _entry("KA00", 0, (0.0, 0.0)),
        _entry("KB55", 5, (0.0, 5.5)),
        _entry("KC65", 5, (0.0, 6.5)),
        _entry("KD65", 5, (0.0, 6.5)),
        _entry("KE65", 5, (0.0, 6.5)),
        _entry("KF12", 5, (0.0, 12.0)),
    ]


def test_kmeans_iterates_until_the_assignment_is_stable() -> None:
    clusters = _bare_clusterer().kmeans_clustering(_shifting_airports(), 2)

    assert _icaos(clusters) == [["KA00"], ["KB55", "KC65", "KD65", "KE65", "KF12"]]


def test_kmeans_stops_at_max_iterations() -> None:
    clusters = _bare_clusterer().kmeans_clustering(
        _shifting_airports(), 2, max_iterations=1
    )

    assert _icaos(clusters) == [["KA00", "KB55"], ["KC65", "KD65", "KE65", "KF12"]]


def test_kmeans_seeds_from_the_most_significant_airport_then_the_farthest() -> None:
    airports = [
        _entry("KMID", 0, (0.0, 5.0)),
        _entry("KWST", 3, (0.0, 0.0)),
        _entry("KEST", 3, (0.0, 20.0)),
        _entry("KNXT", 3, (0.0, 6.0)),
    ]

    clusters = _bare_clusterer().kmeans_clustering(airports, 2)

    assert _icaos(clusters) == [["KMID", "KWST", "KNXT"], ["KEST"]]


def test_kmeans_drops_a_cluster_left_empty_by_duplicate_centroids() -> None:
    airports = [
        _entry("KAAA", 0, (0.0, 0.0)),
        _entry("KBBB", 5, (0.0, 0.0)),
        _entry("KCCC", 5, (0.0, 1.0)),
        _entry("KDDD", 5, (0.0, 1.0)),
    ]

    clusters = _bare_clusterer().kmeans_clustering(airports, 3)

    assert _icaos(clusters) == [["KAAA", "KBBB"], ["KCCC", "KDDD"]]


def test_kmeans_seeds_the_first_of_equally_far_airports() -> None:
    airports = [
        _entry("KAAA", 0, (0.0, 0.0)),
        _entry("KBBB", 5, (0.0, -5.0)),
        _entry("KCCC", 5, (0.0, 5.0)),
    ]

    clusters = _bare_clusterer().kmeans_clustering(airports, 2)

    assert _icaos(clusters) == [["KAAA", "KCCC"], ["KBBB"]]


# generate_area_name


def _named_clusterer(
    cities: dict[str, str | None], disambiguator: Any = None
) -> AreaClusterer:
    unified = {icao: _info(0.0, 0.0, city=city) for icao, city in cities.items()}
    return AreaClusterer({}, unified, disambiguator)


def _centers(*icaos: str) -> list[AirportEntry]:
    return [_entry(icao, 0, (0.0, 0.0)) for icao in icaos]


@pytest.mark.parametrize(
    ("cities", "expected"),
    [
        (["Sacramento"], "Sacramento Area"),
        (["Sacramento", "sacramento "], "Sacramento Area"),
        (["Sacramento", "Sacramento Metro"], "Sacramento Area"),
        (["Bay Region", "Bay Metropolitan", "bay area"], "Bay Region Area"),
        (["Dallas Metro Area", "Dallas"], "Dallas Metro Area Area"),
        (["Oakland", "San Jose"], "Oakland / San Jose Area"),
        (["Oakland", "San Jose", "Oakland Area"], "Oakland / San Jose Area"),
        (["Oakland", "San Jose", "Fresno"], "Oakland / San Jose+ Area"),
        (["Oakland", "San Jose", "Fresno", "Reno"], "Oakland / San Jose+ Area"),
    ],
)
def test_area_name_from_center_cities(cities: list[str], expected: str) -> None:
    icaos = [f"K{index:03d}" for index in range(len(cities))]
    clusterer = _named_clusterer(dict(zip(icaos, cities, strict=True)))

    assert clusterer.generate_area_name(_centers(*icaos)) == expected


def test_area_name_of_no_centers_is_unknown() -> None:
    assert _bare_clusterer().generate_area_name([]) == "Unknown Area"


def test_area_name_falls_back_to_the_display_name_then_the_icao() -> None:
    disambiguator = _FakeDisambiguator({"KDIS": "Pretty Field", "KBLK": ""})
    clusterer = _named_clusterer(
        {"KDIS": "", "KBLK": None, "KCTY": "Citytown"}, disambiguator
    )

    assert (
        clusterer.generate_area_name(_centers("KDIS", "KBLK"))
        == "Pretty Field / KBLK Area"
    )
    assert clusterer.generate_area_name(_centers("KCTY")) == "Citytown Area"


def test_area_name_without_a_disambiguator_uses_the_icao() -> None:
    clusterer = _named_clusterer({"KNOC": ""})

    assert clusterer.generate_area_name(_centers("KNOC", "KMIS")) == "KNOC / KMIS Area"


def test_area_name_uses_the_icao_when_the_display_name_is_none() -> None:
    clusterer = _named_clusterer({"KNON": ""}, _FakeDisambiguator({"KNON": None}))

    assert clusterer.generate_area_name(_centers("KNON")) == "KNON Area"


# create_fallback_area_groups


def test_fallback_groups_of_no_weather_are_empty() -> None:
    assert _bare_clusterer().create_fallback_area_groups() == []


def test_fallback_groups_skip_unknown_categories() -> None:
    unified = {"KAAA": _info(city="Alpha")}
    weather = {"KAAA": {"category": "UNK"}}

    assert AreaClusterer(weather, unified).create_fallback_area_groups() == []


def test_fallback_groups_by_city_then_state_then_other() -> None:
    unified = {
        "KZED": _info(tower="ATCT", city="Zed", state="CA"),
        "KBB2": _info(tower="NON-ATCT", city="Bravo", state="CA"),
        "KBB1": _info(tower="NON-ATCT", city="Bravo", state="CA"),
        "KBB0": _info(far139="I C", city="Bravo", state="CA"),
        "KST2": _info(city=None, state="Oregon"),
        "KST1": _info(tower="ATCT", city="", state="Oregon"),
        "KNV1": _info(city="", state="Nevada"),
        "KOT2": _info(city=None, state=None),
        "KOT1": _info(tower="ATCT", city="", state=""),
        "KUNK": _info(city="Bravo"),
    }
    weather: dict[str, dict[str, Any]] = {icao: {"category": "VFR"} for icao in unified}
    weather["KUNK"] = {"category": "UNK"}
    weather["KMIS"] = {"category": "IFR"}

    groups = AreaClusterer(weather, unified).create_fallback_area_groups()

    assert groups == [
        _group("Bravo Area", weather, [("KBB0", 2), ("KBB1", 8), ("KBB2", 8)]),
        _group("Zed Area", weather, [("KZED", 7)]),
        _group("Nevada Region", weather, [("KNV1", 9)]),
        _group("Oregon Region", weather, [("KST1", 7), ("KST2", 9)]),
        _group("Other Airports", weather, [("KOT1", 7), ("KMIS", 9), ("KOT2", 9)]),
    ]


def test_fallback_groups_without_unlocated_airports_have_no_other_group() -> None:
    unified = {"KAAA": _info(city="Alpha"), "KBBB": _info(state="Beta")}
    weather = {icao: {"category": "VFR"} for icao in unified}

    groups = AreaClusterer(weather, unified).create_fallback_area_groups()

    assert groups == [
        _group("Alpha Area", weather, [("KAAA", 9)]),
        _group("Beta Region", weather, [("KBBB", 9)]),
    ]


# create_area_groups


def test_area_groups_of_no_weather_are_empty() -> None:
    assert _bare_clusterer().create_area_groups() == []


def test_area_groups_without_towered_airports_fall_back_to_city_groups() -> None:
    unified = {
        "KAAA": _info(0.0, 0.0, tower="ATCT", city="Alpha"),
        "KBBB": _info(0.0, 1.0, far139="I B", city="Alpha"),
        "KCCC": _info(0.0, 2.0, state="Gamma"),
    }
    weather = {icao: {"category": "VFR"} for icao in unified}

    groups = AreaClusterer(weather, unified).create_area_groups()

    assert groups == [
        _group("Alpha Area", weather, [("KBBB", 3), ("KAAA", 7)]),
        _group("Gamma Region", weather, [("KCCC", 9)]),
    ]


def test_area_groups_with_unlocated_towered_airports_fall_back_to_city_groups() -> None:
    unified = {
        "KBIG": _info(far139="I E", city="Bigtown"),
        "KATS": _info(tower="ATCT", city="Atisville"),
        "KSML": _info(0.0, 1.0, tower="NON-ATCT", city="Bigtown"),
    }
    weather: dict[str, dict[str, Any]] = {icao: {"category": "VFR"} for icao in unified}
    weather["KATS"] = {"category": "VFR", "atis": [{"callsign": "KATS_ATIS"}]}

    groups = AreaClusterer(weather, unified).create_area_groups()

    assert groups == [
        _group("Atisville Area", weather, [("KATS", 7)]),
        _group("Bigtown Area", weather, [("KBIG", 0), ("KSML", 8)]),
    ]


def test_area_groups_cluster_towered_airports_and_attach_the_rest() -> None:
    unified = {
        "KWWW": _info(0.0, 0.0, far139="I E", city="Westville"),
        "KWAT": _info(0.0, 0.5, tower="ATCT", city="Atisburg"),
        "KWNT": _info(0.0, 0.3, tower="NON-ATCT", city="Westville"),
        "KEEE": _info(0.0, 10.0, far139="I C", city="Eastham"),
        "KENT": _info(0.0, 10.2, city="Eastham"),
        "KNOC": _info(city="Nowhere"),
        "KUNK": _info(0.0, 5.0, far139="I E", city="Middle"),
    }
    weather: dict[str, dict[str, Any]] = {icao: {"category": "VFR"} for icao in unified}
    weather["KWAT"] = {"category": "MVFR", "atis": [{"callsign": "KWAT_ATIS"}]}
    weather["KUNK"] = {"category": "UNK"}

    groups = AreaClusterer(weather, unified).create_area_groups()

    assert groups == [
        _group("Eastham Area", weather, [("KEEE", 2), ("KENT", 9)], center="KEEE"),
        _group(
            "Atisburg / Westville Area",
            weather,
            [("KWAT", 7), ("KWWW", 0), ("KWNT", 8)],
            center="KWAT",
        ),
    ]


def test_area_groups_with_equal_centre_size_sort_by_name_after_renaming() -> None:
    unified = {
        "EGLL": _info(51.4706, -0.4619, city="London"),
        "EGKK": _info(51.1481, -0.1903, city="London"),
    }
    weather = {
        icao: {"category": "VFR", "atis": [{"callsign": f"{icao}_ATIS"}]}
        for icao in unified
    }
    disambiguator = _FakeDisambiguator({"EGLL": "London", "EGKK": "London"})

    groups = AreaClusterer(weather, unified, disambiguator).create_area_groups()

    assert groups == [
        _group("London (EGKK) Area", weather, [("EGKK", 9)], center="EGKK"),
        _group("London (EGLL) Area", weather, [("EGLL", 9)], center="EGLL"),
    ]
