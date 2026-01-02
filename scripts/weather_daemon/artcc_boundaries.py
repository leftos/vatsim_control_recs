"""
ARTCC Boundary Data Fetcher

Fetches ARTCC boundary data from VATSIM vNAS API.
Caches data locally for performance.
"""

import json
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# VATSIM vNAS ARTCC boundaries GeoJSON
VNAS_BOUNDARIES_URL = "https://data-api.vnas.vatsim.net/Files/ArtccBoundaries.geojson"


def download_artcc_boundaries(
    cache_dir: Path,
) -> Optional[Dict[str, List[List[Tuple[float, float]]]]]:
    """
    Download and parse ARTCC boundary data from VATSIM vNAS API.

    Args:
        cache_dir: Directory to cache downloaded data

    Returns:
        Dict mapping ARTCC codes to lists of boundary polygons,
        where each polygon is a list of (lat, lon) tuples
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    cache_file = cache_dir / f"artcc_boundaries_{today}.json"

    # Check cache first (valid for today)
    if cache_file.exists():
        try:
            with open(cache_file, 'r') as f:
                return json.load(f)
        except Exception:
            pass  # Re-download if cache is corrupt

    print(f"  Downloading ARTCC boundaries from vNAS API...")

    try:
        req = Request(VNAS_BOUNDARIES_URL, headers={'User-Agent': 'VATSIM-Weather-Briefings/1.0'})
        with urlopen(req, timeout=30) as response:
            geojson = json.loads(response.read().decode('utf-8'))
    except (URLError, HTTPError) as e:
        print(f"  Error downloading vNAS boundaries: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"  Error parsing vNAS GeoJSON: {e}")
        return None

    # Parse GeoJSON features into boundary polygons
    boundaries: Dict[str, List[List[Tuple[float, float]]]] = {}

    features = geojson.get('features', [])
    for feature in features:
        properties = feature.get('properties', {})
        artcc_id = properties.get('id', '').upper()

        if not artcc_id:
            continue

        geometry = feature.get('geometry', {})
        geom_type = geometry.get('type', '')
        coordinates = geometry.get('coordinates', [])

        if geom_type == 'Polygon' and coordinates:
            # Polygon: coordinates is [ring, ...] where ring is [[lon, lat], ...]
            polygons = []
            for ring in coordinates:
                # Convert from GeoJSON [lon, lat] to our (lat, lon) format
                polygon = [(coord[1], coord[0]) for coord in ring]
                if len(polygon) >= 3:
                    polygons.append(polygon)
            if polygons:
                boundaries[artcc_id] = polygons

        elif geom_type == 'MultiPolygon' and coordinates:
            # MultiPolygon: coordinates is [polygon, ...] where polygon is [ring, ...]
            polygons = []
            for poly_coords in coordinates:
                for ring in poly_coords:
                    polygon = [(coord[1], coord[0]) for coord in ring]
                    if len(polygon) >= 3:
                        polygons.append(polygon)
            if polygons:
                boundaries[artcc_id] = polygons

    if not boundaries:
        print("  Warning: No boundaries parsed from vNAS GeoJSON")
        return get_embedded_boundaries()

    print(f"    Parsed boundaries for {len(boundaries)} ARTCCs")

    # Cache the parsed data
    try:
        with open(cache_file, 'w') as f:
            json.dump(boundaries, f)

        # Clean up old cache files
        for old_cache in cache_dir.glob("artcc_boundaries_*.json"):
            if old_cache != cache_file:
                old_cache.unlink()
    except Exception as e:
        print(f"  Warning: Could not cache boundaries: {e}")

    return boundaries


def get_embedded_boundaries() -> Dict[str, List[List[Tuple[float, float]]]]:
    """
    Return embedded ARTCC boundary approximations.

    These are simplified polygon approximations for each ARTCC.
    Used as a fallback when vNAS API is unavailable.
    """
    return {
        "ZAB": [[
            (36.5, -109.0), (36.5, -103.5), (32.0, -103.5), (31.0, -106.0),
            (31.0, -111.5), (33.0, -114.5), (36.5, -114.5), (36.5, -109.0)
        ]],
        "ZAN": [[
            (71.0, -180.0), (71.0, -130.0), (60.0, -130.0), (54.0, -135.0),
            (51.0, -170.0), (52.0, -180.0), (71.0, -180.0)
        ]],
        "ZAU": [[
            (44.0, -90.5), (44.0, -85.0), (39.5, -85.0), (39.5, -90.5), (44.0, -90.5)
        ]],
        "ZBW": [[
            (47.5, -74.0), (47.5, -67.0), (41.0, -67.0), (41.0, -74.0), (47.5, -74.0)
        ]],
        "ZDC": [[
            (41.0, -79.5), (41.0, -74.0), (36.5, -74.0), (36.5, -79.5), (41.0, -79.5)
        ]],
        "ZDV": [[
            (44.0, -111.0), (44.0, -102.0), (37.0, -102.0), (37.0, -111.0), (44.0, -111.0)
        ]],
        "ZFW": [[
            (36.5, -102.0), (36.5, -94.0), (29.5, -94.0), (29.5, -102.0), (36.5, -102.0)
        ]],
        "ZHU": [[
            (32.0, -97.0), (32.0, -89.0), (27.0, -89.0), (27.0, -97.0), (32.0, -97.0)
        ]],
        "ZID": [[
            (42.0, -87.0), (42.0, -81.0), (37.0, -81.0), (37.0, -87.0), (42.0, -87.0)
        ]],
        "ZJX": [[
            (32.0, -84.0), (32.0, -79.0), (27.0, -79.0), (27.0, -84.0), (32.0, -84.0)
        ]],
        "ZKC": [[
            (42.0, -97.0), (42.0, -90.5), (36.5, -90.5), (36.5, -97.0), (42.0, -97.0)
        ]],
        "ZLA": [[
            (36.5, -121.0), (36.5, -114.5), (32.0, -114.5), (32.0, -121.0), (36.5, -121.0)
        ]],
        "ZLC": [[
            (49.0, -117.0), (49.0, -111.0), (40.0, -111.0), (40.0, -117.0), (49.0, -117.0)
        ]],
        "ZMA": [[
            (27.0, -84.0), (27.0, -77.0), (23.0, -77.0), (23.0, -84.0), (27.0, -84.0)
        ]],
        "ZME": [[
            (37.0, -92.0), (37.0, -86.0), (32.5, -86.0), (32.5, -92.0), (37.0, -92.0)
        ]],
        "ZMP": [[
            (49.0, -97.0), (49.0, -89.0), (43.0, -89.0), (43.0, -97.0), (49.0, -97.0)
        ]],
        "ZNY": [[
            (43.5, -76.5), (43.5, -71.0), (40.0, -71.0), (40.0, -76.5), (43.5, -76.5)
        ]],
        "ZOA": [[
            (41.0, -125.0), (41.0, -118.0), (35.5, -118.0), (35.5, -125.0), (41.0, -125.0)
        ]],
        "ZOB": [[
            (43.5, -84.0), (43.5, -78.0), (39.5, -78.0), (39.5, -84.0), (43.5, -84.0)
        ]],
        "ZSE": [[
            (49.0, -125.0), (49.0, -117.0), (42.0, -117.0), (42.0, -125.0), (49.0, -125.0)
        ]],
        "ZSU": [[
            (19.5, -65.0), (19.5, -64.0), (17.5, -64.0), (17.5, -65.0), (19.5, -65.0)
        ]],
        "ZTL": [[
            (36.5, -87.0), (36.5, -81.0), (32.0, -81.0), (32.0, -87.0), (36.5, -87.0)
        ]],
        "ZHN": [[
            (26.0, -164.0), (26.0, -150.0), (17.0, -150.0), (17.0, -164.0), (26.0, -164.0)
        ]],
    }


def get_artcc_center(boundaries: List[List[Tuple[float, float]]]) -> Tuple[float, float]:
    """Calculate the center point of ARTCC boundaries."""
    all_points = []
    for polygon in boundaries:
        all_points.extend(polygon)

    if not all_points:
        return (39.0, -98.0)  # US center

    avg_lat = sum(p[0] for p in all_points) / len(all_points)
    avg_lon = sum(p[1] for p in all_points) / len(all_points)
    return (avg_lat, avg_lon)


def get_artcc_boundaries(cache_dir: Path) -> Dict[str, List[List[Tuple[float, float]]]]:
    """
    Get ARTCC boundaries, downloading from vNAS API if needed.

    Args:
        cache_dir: Directory to cache downloaded data

    Returns:
        Dict mapping ARTCC codes to boundary polygons
    """
    boundaries = download_artcc_boundaries(cache_dir)
    if boundaries:
        return boundaries

    # Fall back to embedded boundaries
    print("  Using embedded ARTCC boundaries")
    return get_embedded_boundaries()


if __name__ == "__main__":
    # Test the boundary fetcher
    from pathlib import Path
    cache_dir = Path("./test_cache")
    boundaries = get_artcc_boundaries(cache_dir)
    print(f"Loaded boundaries for {len(boundaries)} ARTCCs")
    for artcc, polys in boundaries.items():
        total_points = sum(len(p) for p in polys)
        print(f"  {artcc}: {len(polys)} polygon(s), {total_points} points")
