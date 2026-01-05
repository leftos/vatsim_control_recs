"""
Benchmark: Bounding Box vs Per-Airport Weather Fetching

Compares the current per-airport METAR/TAF fetching approach against
a bounding box-based approach using aviationweather.gov's bbox parameter.
"""

import json
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .artcc_boundaries import get_artcc_boundaries


def get_artcc_bbox(
    artcc_code: str, cache_dir: Path
) -> Optional[Tuple[float, float, float, float]]:
    """
    Calculate bounding box for an ARTCC from its polygon boundaries.

    Returns:
        (min_lat, min_lon, max_lat, max_lon) or None if not found
    """
    boundaries = get_artcc_boundaries(cache_dir)
    if artcc_code not in boundaries:
        return None

    polygons = boundaries[artcc_code]
    all_points = []
    for polygon in polygons:
        all_points.extend(polygon)

    if not all_points:
        return None

    min_lat = min(p[0] for p in all_points)
    max_lat = max(p[0] for p in all_points)
    min_lon = min(p[1] for p in all_points)
    max_lon = max(p[1] for p in all_points)

    return (min_lat, min_lon, max_lat, max_lon)


def fetch_metar_single(icao: str) -> Tuple[str, str, float]:
    """
    Fetch METAR for a single airport (current approach).

    Returns:
        (icao, metar_text, elapsed_seconds)
    """
    start = time.perf_counter()
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={icao}&format=raw"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "VATSIM-Weather-Benchmark/1.0")

        with urllib.request.urlopen(req, timeout=10) as response:
            metar_text = response.read().decode("utf-8").strip()

        elapsed = time.perf_counter() - start
        return (icao, metar_text, elapsed)
    except Exception:
        elapsed = time.perf_counter() - start
        return (icao, "", elapsed)


def fetch_taf_single(icao: str) -> Tuple[str, str, float]:
    """
    Fetch TAF for a single airport (current approach).

    Returns:
        (icao, taf_text, elapsed_seconds)
    """
    start = time.perf_counter()
    try:
        url = f"https://aviationweather.gov/api/data/taf?ids={icao}&format=raw"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "VATSIM-Weather-Benchmark/1.0")

        with urllib.request.urlopen(req, timeout=10) as response:
            taf_text = response.read().decode("utf-8").strip()

        elapsed = time.perf_counter() - start
        return (icao, taf_text, elapsed)
    except Exception:
        elapsed = time.perf_counter() - start
        return (icao, "", elapsed)


def fetch_weather_per_airport(
    airports: List[str], max_workers: int = 10
) -> Tuple[Dict[str, str], Dict[str, str], float]:
    """
    Fetch METAR and TAF for airports using per-airport requests (current approach).

    Returns:
        (metars_dict, tafs_dict, total_elapsed_seconds)
    """
    metars = {}
    tafs = {}

    start = time.perf_counter()

    # Fetch METARs in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_metar_single, icao): icao for icao in airports}
        for future in as_completed(futures):
            icao, metar, _ = future.result()
            if metar:
                metars[icao] = metar

    # Fetch TAFs in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_taf_single, icao): icao for icao in airports}
        for future in as_completed(futures):
            icao, taf, _ = future.result()
            if taf:
                tafs[icao] = taf

    elapsed = time.perf_counter() - start
    return (metars, tafs, elapsed)


def fetch_metar_bbox(
    bbox: Tuple[float, float, float, float], include_taf: bool = True
) -> Tuple[Dict[str, str], Dict[str, str], float, int]:
    """
    Fetch METAR (and optionally TAF) using bounding box query.

    Args:
        bbox: (min_lat, min_lon, max_lat, max_lon)
        include_taf: Whether to include TAF data in the response

    Returns:
        (metars_dict, tafs_dict, elapsed_seconds, api_calls)
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    bbox_str = f"{min_lat},{min_lon},{max_lat},{max_lon}"

    metars = {}
    tafs = {}

    start = time.perf_counter()

    try:
        # Use taf=true to get both in one call
        taf_param = "&taf=true" if include_taf else ""
        url = f"https://aviationweather.gov/api/data/metar?bbox={bbox_str}&format=json{taf_param}"

        req = urllib.request.Request(url)
        req.add_header("User-Agent", "VATSIM-Weather-Benchmark/1.0")

        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))

        # Parse response - it's an array of METAR objects
        if isinstance(data, list):
            for entry in data:
                icao = entry.get("icaoId", "")
                raw_metar = entry.get("rawOb", "")
                raw_taf = entry.get("rawTaf", "")

                if icao and raw_metar:
                    metars[icao] = raw_metar
                if icao and raw_taf:
                    tafs[icao] = raw_taf

        elapsed = time.perf_counter() - start
        return (metars, tafs, elapsed, 1)

    except Exception as e:
        elapsed = time.perf_counter() - start
        print(f"  Error fetching bbox: {e}")
        return ({}, {}, elapsed, 1)


def fetch_weather_bbox(
    bbox: Tuple[float, float, float, float], target_airports: List[str]
) -> Tuple[Dict[str, str], Dict[str, str], float]:
    """
    Fetch METAR and TAF using bounding box, then filter to target airports.

    Returns:
        (metars_dict, tafs_dict, total_elapsed_seconds)
    """
    metars_all, tafs_all, elapsed, _ = fetch_metar_bbox(bbox, include_taf=True)

    # Filter to only target airports
    target_set = set(a.upper() for a in target_airports)
    metars = {k: v for k, v in metars_all.items() if k.upper() in target_set}
    tafs = {k: v for k, v in tafs_all.items() if k.upper() in target_set}

    return (metars, tafs, elapsed)


def run_benchmark(artcc: str = "ZOA"):
    """Run the benchmark comparing both approaches."""
    print(f"\n{'=' * 60}")
    print(f"Weather Fetching Benchmark: {artcc}")
    print(f"{'=' * 60}")
    print(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}")

    # Load airports for this ARTCC
    preset_file = (
        Path(__file__).parent.parent.parent
        / "data"
        / "preset_groupings"
        / f"{artcc}.json"
    )
    if not preset_file.exists():
        print(f"Error: No preset file found for {artcc}")
        return

    with open(preset_file, "r") as f:
        groupings = json.load(f)

    # Get all unique airports
    all_airports = set()
    for airports in groupings.values():
        all_airports.update(airports)

    airports_list = sorted(all_airports)
    print(f"\nAirports to fetch: {len(airports_list)}")
    print(f"Sample: {', '.join(airports_list[:10])}...")

    # Get ARTCC bounding box
    cache_dir = Path(__file__).parent / "cache"
    bbox = get_artcc_bbox(artcc, cache_dir)
    if not bbox:
        print(f"Error: Could not get bounding box for {artcc}")
        return

    print("\nARTCC Bounding Box:")
    print(f"  Lat: {bbox[0]:.2f} to {bbox[2]:.2f}")
    print(f"  Lon: {bbox[1]:.2f} to {bbox[3]:.2f}")

    # Run benchmark: Per-Airport approach
    print(f"\n{'-' * 60}")
    print("Approach 1: Per-Airport Requests (current)")
    print(f"{'-' * 60}")

    metars_1, tafs_1, elapsed_1 = fetch_weather_per_airport(
        airports_list, max_workers=10
    )

    print(f"  API calls: ~{len(airports_list) * 2} (METAR + TAF)")
    print(f"  Time: {elapsed_1:.2f}s")
    print(f"  METARs retrieved: {len(metars_1)}/{len(airports_list)}")
    print(f"  TAFs retrieved: {len(tafs_1)}/{len(airports_list)}")

    # Brief pause to avoid rate limiting
    print("\n  (Pausing 2s to avoid rate limiting...)")
    time.sleep(2)

    # Run benchmark: Bounding Box approach
    print(f"\n{'-' * 60}")
    print("Approach 2: Bounding Box Request (new)")
    print(f"{'-' * 60}")

    # First, let's see what the raw bbox returns
    metars_raw, tafs_raw, elapsed_raw, api_calls = fetch_metar_bbox(
        bbox, include_taf=True
    )

    print(f"  API calls: {api_calls}")
    print(f"  Time: {elapsed_raw:.2f}s")
    print(f"  Total METARs in bbox: {len(metars_raw)}")
    print(f"  Total TAFs in bbox: {len(tafs_raw)}")

    # Filter to target airports
    target_set = set(a.upper() for a in airports_list)
    metars_2 = {k: v for k, v in metars_raw.items() if k.upper() in target_set}
    tafs_2 = {k: v for k, v in tafs_raw.items() if k.upper() in target_set}

    print(f"  METARs for target airports: {len(metars_2)}/{len(airports_list)}")
    print(f"  TAFs for target airports: {len(tafs_2)}/{len(airports_list)}")

    # Compare results
    print(f"\n{'-' * 60}")
    print("Comparison")
    print(f"{'-' * 60}")

    speedup = elapsed_1 / elapsed_raw if elapsed_raw > 0 else float("inf")
    print(f"  Speedup: {speedup:.1f}x faster")
    print(f"  Time saved: {elapsed_1 - elapsed_raw:.2f}s")
    print(
        f"  API call reduction: {len(airports_list) * 2} -> {api_calls} ({(1 - api_calls / (len(airports_list) * 2)) * 100:.0f}% reduction)"
    )

    # Check for missing data
    missing_in_bbox = set(metars_1.keys()) - set(metars_2.keys())
    extra_in_bbox = set(metars_2.keys()) - set(metars_1.keys())

    if missing_in_bbox:
        print(
            f"\n  METARs found per-airport but not in bbox: {sorted(missing_in_bbox)}"
        )
    if extra_in_bbox:
        print(f"  METARs found in bbox but not per-airport: {sorted(extra_in_bbox)}")

    # Sample data comparison
    print(f"\n{'-' * 60}")
    print("Sample Data Verification")
    print(f"{'-' * 60}")

    common_airports = sorted(set(metars_1.keys()) & set(metars_2.keys()))[:3]
    for icao in common_airports:
        m1 = metars_1[icao][:60] + "..." if len(metars_1[icao]) > 60 else metars_1[icao]
        m2 = metars_2[icao][:60] + "..." if len(metars_2[icao]) > 60 else metars_2[icao]
        match = "MATCH" if metars_1[icao] == metars_2[icao] else "DIFFER"
        print(f"\n  {icao}: {match}")
        print(f"    Per-airport: {m1}")
        print(f"    Bbox:        {m2}")

    print(f"\n{'=' * 60}")
    print("Benchmark Complete")
    print(f"{'=' * 60}\n")

    return {
        "per_airport": {
            "time": elapsed_1,
            "api_calls": len(airports_list) * 2,
            "metars": len(metars_1),
            "tafs": len(tafs_1),
        },
        "bbox": {
            "time": elapsed_raw,
            "api_calls": api_calls,
            "metars": len(metars_2),
            "tafs": len(tafs_2),
            "total_in_bbox": len(metars_raw),
        },
        "speedup": speedup,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Benchmark bbox vs per-airport weather fetching"
    )
    parser.add_argument(
        "--artcc", default="ZOA", help="ARTCC code to benchmark (default: ZOA)"
    )
    args = parser.parse_args()

    run_benchmark(args.artcc)
