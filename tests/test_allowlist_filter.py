"""The allowlist filter keeps only listed airports and scales to a country list."""

import time

from backend.core.analysis import filter_airports_by_allowlist

ALL_AIRPORTS_DATA: dict[str, dict] = {
    "KSFO": {"latitude": 37.6189, "longitude": -122.3750, "country_code": "US"},
    "KOAK": {"latitude": 37.7213, "longitude": -122.2207, "country_code": "US"},
    "KSJC": {"latitude": 37.3626, "longitude": -121.9290, "country_code": "US"},
    "KLAX": {"latitude": 33.9425, "longitude": -118.4081, "country_code": "US"},
    "KSEA": {"latitude": 47.4502, "longitude": -122.3088, "country_code": "US"},
}


def test_filter_airports_by_allowlist_keeps_only_listed() -> None:
    allowlist = ["KSFO", "KOAK", "KLAX", "KZZZ"]

    result = filter_airports_by_allowlist(ALL_AIRPORTS_DATA, allowlist)

    assert list(result) == ["KSFO", "KOAK", "KLAX"]
    for icao in ("KSFO", "KOAK", "KLAX"):
        assert result[icao] == ALL_AIRPORTS_DATA[icao]

    assert filter_airports_by_allowlist(ALL_AIRPORTS_DATA, None) == ALL_AIRPORTS_DATA
    assert filter_airports_by_allowlist(ALL_AIRPORTS_DATA, []) == ALL_AIRPORTS_DATA


def test_filter_airports_by_allowlist_is_fast_for_a_country_list() -> None:
    all_airports_data = {
        f"A{i:05d}": {"latitude": float(i), "longitude": 0.0} for i in range(38_000)
    }
    allowlist = [f"A{i:05d}" for i in range(23_000)]

    start = time.perf_counter()
    result = filter_airports_by_allowlist(all_airports_data, allowlist)
    elapsed = time.perf_counter() - start

    assert len(result) == 23_000
    assert elapsed < 0.5, f"filtering 38,000 airports took {elapsed:.3f}s"
