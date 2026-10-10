"""SpatialIndex.find_nearest refuses the full-scan fallback for radius queries.

The grid holds 1-degree cells (about 60nm at the equator), so a query with a
radius smaller than that cannot match any airport outside the 3x3 neighbourhood
of the query cell. Scanning every airport in that case can only cost time.
"""

import pytest

import backend.core.spatial as spatial
from backend.core.spatial import SpatialIndex

AIRPORTS = {
    "KAAA": {"latitude": 37.0, "longitude": -122.0},
    "KBBB": {"latitude": 37.5, "longitude": -122.5},
}


def _build_index() -> SpatialIndex:
    index = SpatialIndex()
    index.build(AIRPORTS)
    return index


def test_radius_query_with_empty_neighbourhood_scans_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    real_haversine = spatial.haversine_distance_nm

    def counting_haversine(*args: float, **kwargs: float) -> float:
        nonlocal calls
        calls += 1
        return real_haversine(*args, **kwargs)

    monkeypatch.setattr(spatial, "haversine_distance_nm", counting_haversine)

    index = _build_index()
    result = index.find_nearest(0.0, 0.0, max_distance_nm=6)

    assert result is None
    assert calls == 0


def test_radius_query_finds_nearby_airport() -> None:
    index = _build_index()

    # about 2nm north of KAAA, still in its cell
    result = index.find_nearest(37.033, -122.0, max_distance_nm=6)

    assert result == "KAAA"


def test_radius_query_beyond_radius_returns_none() -> None:
    index = _build_index()

    # about 20nm north of KAAA, still in the same 1-degree cell
    result = index.find_nearest(37.34, -122.0, max_distance_nm=6)

    assert result is None


def test_radiusless_query_still_falls_back_to_full_scan() -> None:
    index = _build_index()

    # far from every airport, so the neighbourhood is empty
    result = index.find_nearest(0.0, 0.0)

    assert result == "KAAA"
