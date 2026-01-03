#!/usr/bin/env python3
"""
Download and transform SimAware TRACON boundaries.

Clones the SimAware TRACON Project repo and transforms the boundary data
into a local format for the weather daemon.

Output: data/simaware_boundaries/*.json
Each file contains {facility_id}.json with all boundaries for that facility.

Source: https://github.com/vatsimnetwork/simaware-tracon-project
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

REPO_URL = "https://github.com/vatsimnetwork/simaware-tracon-project.git"
OUTPUT_DIR = PROJECT_ROOT / "data" / "simaware_boundaries"


def parse_geojson_coordinates(feature: dict) -> Optional[List[List[float]]]:
    """
    Extract polygon coordinates from a GeoJSON feature.

    Returns coordinates in [lat, lon] format (converted from GeoJSON [lon, lat]).
    """
    geometry = feature.get('geometry', {})
    geom_type = geometry.get('type', '')
    coordinates = geometry.get('coordinates', [])

    if not coordinates:
        return None

    points = []

    if geom_type == 'Polygon':
        if coordinates and len(coordinates) > 0:
            ring = coordinates[0]
            for coord in ring:
                if len(coord) >= 2:
                    # GeoJSON uses [lon, lat], convert to [lat, lon]
                    points.append([coord[1], coord[0]])

    elif geom_type == 'MultiPolygon':
        if coordinates and len(coordinates) > 0:
            polygon = coordinates[0]
            if polygon and len(polygon) > 0:
                ring = polygon[0]
                for coord in ring:
                    if len(coord) >= 2:
                        points.append([coord[1], coord[0]])

    return points if len(points) >= 3 else None


def process_facility_dir(facility_dir: Path) -> Dict[str, Any]:
    """Process all boundary files in a facility directory."""
    boundaries: Dict[str, Any] = {}

    for json_file in sorted(facility_dir.glob("*.json")):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                feature = json.load(f)

            coords = parse_geojson_coordinates(feature)
            if coords:
                # Use filename without .json as key
                key = json_file.stem
                boundaries[key] = {
                    'coordinates': coords,
                    'name': feature.get('properties', {}).get('name', key),
                }
        except (json.JSONDecodeError, Exception) as e:
            print(f"    Warning: Could not parse {json_file.name}: {e}")

    return boundaries


def main():
    """Main entry point."""
    print("SimAware TRACON Boundary Downloader")
    print("=" * 50)

    # Create temp directory for the clone
    with tempfile.TemporaryDirectory() as tmpdir:
        clone_dir = Path(tmpdir) / "simaware-tracon-project"

        # Shallow clone to get just the latest files
        print("Cloning SimAware TRACON Project repo (shallow clone)...")
        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", REPO_URL, str(clone_dir)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                print(f"  Error cloning repo: {result.stderr}")
                return 1
        except subprocess.TimeoutExpired:
            print("  Clone timed out")
            return 1
        except FileNotFoundError:
            print("  Error: git not found. Please install git.")
            return 1

        print("  Clone successful")

        # Find the Boundaries directory
        boundaries_dir = clone_dir / "Boundaries"
        if not boundaries_dir.exists():
            print(f"  Error: Boundaries directory not found at {boundaries_dir}")
            return 1

        # Get list of facility directories
        facility_dirs = [d for d in sorted(boundaries_dir.iterdir()) if d.is_dir()]
        print(f"\nFound {len(facility_dirs)} TRACON facilities")

        # Create output directory
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # Process each facility
        print("\nProcessing boundaries...")
        total_boundaries = 0
        successful_facilities = 0

        for i, facility_dir in enumerate(facility_dirs):
            facility = facility_dir.name
            print(f"  [{i+1}/{len(facility_dirs)}] {facility}...", end=" ", flush=True)

            boundaries = process_facility_dir(facility_dir)

            if boundaries:
                output_file = OUTPUT_DIR / f"{facility}.json"
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(boundaries, f, indent=2, ensure_ascii=False)
                    f.write('\n')

                print(f"{len(boundaries)} boundaries")
                total_boundaries += len(boundaries)
                successful_facilities += 1
            else:
                print("no boundaries")

    print(f"\nDone! Processed {total_boundaries} boundaries from {successful_facilities} facilities")
    print(f"Output: {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
