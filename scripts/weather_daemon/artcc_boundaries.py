"""
ARTCC Boundary Data Fetcher

Fetches ARTCC boundary data from FAA NASR subscription.
Caches data locally and only re-downloads when a new subscription is available.
"""

import json
import os
import re
import zipfile
from datetime import datetime, date
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# NASR subscription base URL
NASR_BASE_URL = "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/"


def get_current_subscription_date() -> Optional[str]:
    """
    Determine the current NASR subscription date.

    NASR subscriptions are released every 28 days. This function calculates
    which subscription should currently be active.

    Returns:
        Date string in YYYY-MM-DD format, or None if unable to determine
    """
    try:
        # Fetch the main subscription page to find available dates
        req = Request(NASR_BASE_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=30) as response:
            html = response.read().decode('utf-8')

        # Parse available subscription dates from directory listing
        # Pattern matches href="YYYY-MM-DD/"
        date_pattern = r'href="(\d{4}-\d{2}-\d{2})/"'
        dates = re.findall(date_pattern, html)

        if not dates:
            print("  Warning: No subscription dates found on NASR page")
            return None

        # Sort dates and find the most recent one that is not in the future
        today = date.today()
        valid_dates = []

        for date_str in dates:
            try:
                sub_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                if sub_date <= today:
                    valid_dates.append(date_str)
            except ValueError:
                continue

        if not valid_dates:
            # If all dates are in the future, use the earliest one
            return sorted(dates)[0]

        # Return the most recent valid date
        return sorted(valid_dates, reverse=True)[0]

    except (URLError, HTTPError) as e:
        print(f"  Warning: Could not fetch NASR subscription page: {e}")
        return None


def download_artcc_boundaries(
    subscription_date: str,
    cache_dir: Path,
) -> Optional[Dict[str, List[List[Tuple[float, float]]]]]:
    """
    Download and parse ARTCC boundary data from NASR subscription.

    Args:
        subscription_date: Subscription date in YYYY-MM-DD format
        cache_dir: Directory to cache downloaded data

    Returns:
        Dict mapping ARTCC codes to lists of boundary polygons,
        where each polygon is a list of (lat, lon) tuples
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"artcc_boundaries_{subscription_date}.json"

    # Check cache first
    if cache_file.exists():
        try:
            with open(cache_file, 'r') as f:
                return json.load(f)
        except Exception:
            pass  # Re-download if cache is corrupt

    # Download the subscription ZIP file
    # ARTCC boundary data is in the "Additional_Data" directory
    zip_url = f"{NASR_BASE_URL}{subscription_date}/Additional_Data.zip"

    print(f"  Downloading ARTCC boundaries from {zip_url}...")

    try:
        req = Request(zip_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=120) as response:
            zip_data = BytesIO(response.read())
    except (URLError, HTTPError) as e:
        print(f"  Error downloading NASR data: {e}")
        return None

    # Extract and parse the ARTCC boundary file
    boundaries: Dict[str, List[List[Tuple[float, float]]]] = {}

    try:
        with zipfile.ZipFile(zip_data) as zf:
            # Look for ARTCC boundary files
            # The file is typically named "ARB.txt" or similar
            artcc_files = [
                name for name in zf.namelist()
                if 'ARB' in name.upper() and name.endswith('.txt')
            ]

            if not artcc_files:
                # Try looking for the boundary data in other common locations
                artcc_files = [
                    name for name in zf.namelist()
                    if 'BOUNDARY' in name.upper() or 'ARTCC' in name.upper()
                ]

            if not artcc_files:
                print("  Warning: Could not find ARTCC boundary file in NASR data")
                # Fall back to embedded boundaries
                return get_embedded_boundaries()

            for artcc_file in artcc_files:
                print(f"    Parsing {artcc_file}...")
                with zf.open(artcc_file) as f:
                    content = f.read().decode('latin-1')
                    parsed = parse_arb_file(content)
                    boundaries.update(parsed)

    except zipfile.BadZipFile:
        print("  Error: Invalid ZIP file from NASR")
        return get_embedded_boundaries()

    if not boundaries:
        print("  Warning: No boundaries parsed, using embedded data")
        return get_embedded_boundaries()

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


def parse_arb_file(content: str) -> Dict[str, List[List[Tuple[float, float]]]]:
    """
    Parse FAA ARB (ARTCC Boundary) file format.

    The ARB file contains records with boundary point data.
    Format varies but typically includes:
    - ARTCC identifier
    - Latitude/Longitude coordinates
    - Sequence numbers

    Args:
        content: Raw file content

    Returns:
        Dict mapping ARTCC codes to boundary polygons
    """
    boundaries: Dict[str, List[List[Tuple[float, float]]]] = {}
    current_artcc: Optional[str] = None
    current_polygon: List[Tuple[float, float]] = []

    for line in content.split('\n'):
        line = line.strip()
        if not line:
            continue

        # Try to parse as fixed-width record (common in NASR data)
        # ARB record format (approximate):
        # Columns 1-4: Record type
        # Columns 5-8: ARTCC ID
        # Various position data follows

        if len(line) >= 8:
            record_type = line[0:4].strip()

            if record_type == 'ARB1' or record_type == 'ARB':
                # Header record - extract ARTCC ID
                artcc_id = line[4:8].strip().upper()
                if artcc_id and len(artcc_id) == 3:
                    # Save previous polygon if exists
                    if current_artcc and current_polygon:
                        if current_artcc not in boundaries:
                            boundaries[current_artcc] = []
                        boundaries[current_artcc].append(current_polygon)

                    current_artcc = artcc_id
                    current_polygon = []

            elif record_type == 'ARB2' or 'ARB' in record_type:
                # Boundary point record
                # Try to extract lat/lon
                coords = extract_coordinates(line)
                if coords and current_artcc:
                    current_polygon.append(coords)

    # Save last polygon
    if current_artcc and current_polygon:
        if current_artcc not in boundaries:
            boundaries[current_artcc] = []
        boundaries[current_artcc].append(current_polygon)

    return boundaries


def extract_coordinates(line: str) -> Optional[Tuple[float, float]]:
    """
    Extract latitude/longitude from a boundary record line.

    Handles various NASR coordinate formats:
    - Decimal degrees
    - Degrees-Minutes-Seconds
    - DDMMSS.SS format

    Returns:
        (latitude, longitude) tuple or None
    """
    # Pattern for DMS format: DDMMSSH (H = hemisphere)
    dms_pattern = r'(\d{2})(\d{2})(\d{2}(?:\.\d+)?)\s*([NS])\s+(\d{2,3})(\d{2})(\d{2}(?:\.\d+)?)\s*([EW])'
    match = re.search(dms_pattern, line)

    if match:
        lat_deg = int(match.group(1))
        lat_min = int(match.group(2))
        lat_sec = float(match.group(3))
        lat_hem = match.group(4)

        lon_deg = int(match.group(5))
        lon_min = int(match.group(6))
        lon_sec = float(match.group(7))
        lon_hem = match.group(8)

        lat = lat_deg + lat_min / 60 + lat_sec / 3600
        if lat_hem == 'S':
            lat = -lat

        lon = lon_deg + lon_min / 60 + lon_sec / 3600
        if lon_hem == 'W':
            lon = -lon

        return (lat, lon)

    # Try decimal degrees pattern
    decimal_pattern = r'(-?\d+\.\d+)\s+(-?\d+\.\d+)'
    match = re.search(decimal_pattern, line)
    if match:
        lat = float(match.group(1))
        lon = float(match.group(2))
        # Validate reasonable lat/lon range
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return (lat, lon)

    return None


def get_embedded_boundaries() -> Dict[str, List[List[Tuple[float, float]]]]:
    """
    Return embedded ARTCC boundary approximations.

    These are simplified polygon approximations for each ARTCC.
    Used as a fallback when NASR data is unavailable.
    """
    # Simplified center points for each ARTCC
    # In production, these would be full boundary polygons
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
    Get ARTCC boundaries, downloading from NASR if needed.

    Args:
        cache_dir: Directory to cache downloaded data

    Returns:
        Dict mapping ARTCC codes to boundary polygons
    """
    # Try to get current subscription date
    sub_date = get_current_subscription_date()

    if sub_date:
        boundaries = download_artcc_boundaries(sub_date, cache_dir)
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
