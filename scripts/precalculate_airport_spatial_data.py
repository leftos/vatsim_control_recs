#!/usr/bin/env python3
"""
Precalculate and persist airport spatial data for faster lookups.

This script generates:
1. A spatial grid index mapping 1-degree cells to airports
2. A list of airports known to have METAR data (fetched from aviationweather.gov)

Run this script periodically (e.g., weekly) or when airport data changes.

Usage:
    python scripts/precalculate_airport_spatial_data.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.data.loaders import load_unified_airport_data


def build_spatial_grid(airports_data: dict) -> dict:
    """
    Build a spatial grid mapping 1-degree cells to airports.

    Args:
        airports_data: Dictionary of airport data from load_unified_airport_data()

    Returns:
        Dictionary mapping "(lat, lon)" cell keys to lists of airport info
    """
    spatial_grid = {}

    for icao, data in airports_data.items():
        lat = data.get("latitude")
        lon = data.get("longitude")

        if lat is None or lon is None:
            continue

        # 1-degree cell
        lat_cell = int(lat)
        lon_cell = int(lon)
        cell_key = f"{lat_cell},{lon_cell}"

        if cell_key not in spatial_grid:
            spatial_grid[cell_key] = []

        spatial_grid[cell_key].append({"icao": icao, "lat": lat, "lon": lon})

    return spatial_grid


def build_heuristic_metar_candidates(airports_data: dict) -> set:
    """
    Build a set of airports likely to have METAR based on heuristics.

    Heuristics:
    - 4-letter ICAO code with all letters (no digits)
    - This filters out private strips (27CL, CA22, L36, etc.)

    Args:
        airports_data: Dictionary of airport data

    Returns:
        Set of ICAO codes likely to have METAR
    """
    print("Building heuristic METAR candidate list...")

    candidates = set()
    for icao in airports_data.keys():
        # 4-letter codes with all letters are standard ICAO
        if len(icao) == 4 and icao.isalpha():
            candidates.add(icao)

    print(f"  Found {len(candidates)} candidate METAR stations (heuristic)")
    return candidates


def main():
    # Paths
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    data_dir = project_root / "data"
    cache_file = data_dir / "airport_spatial_cache.json"

    print("Loading unified airport data...")
    airports_data = load_unified_airport_data(
        apt_base_path=str(data_dir / "APT_BASE.csv"),
        airports_json_path=str(data_dir / "airports.json"),
        iata_icao_path=str(data_dir / "iata-icao.csv"),
    )
    print(f"  Loaded {len(airports_data)} airports")

    # Build spatial grid
    print("\nBuilding spatial grid...")
    spatial_grid = build_spatial_grid(airports_data)
    print(f"  Created {len(spatial_grid)} grid cells")

    # Count airports in grid
    total_in_grid = sum(len(airports) for airports in spatial_grid.values())
    print(f"  Total airports indexed: {total_in_grid}")

    # Build heuristic METAR candidate list
    metar_stations = build_heuristic_metar_candidates(airports_data)

    # Build cache data
    cache_data = {
        "version": 1,
        "generated": datetime.now(timezone.utc).isoformat(),
        "airport_count": len(airports_data),
        "grid_cell_count": len(spatial_grid),
        "spatial_grid": spatial_grid,
        "metar_stations": sorted(list(metar_stations)) if metar_stations else None,
    }

    # Write cache file
    print(f"\nWriting cache to {cache_file}...")
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, separators=(",", ":"))  # Compact JSON

    # Report file size
    file_size = cache_file.stat().st_size
    print(f"  Cache file size: {file_size / 1024:.1f} KB")

    print("\nDone!")


if __name__ == "__main__":
    main()
