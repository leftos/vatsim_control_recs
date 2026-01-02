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

# NASR subscription page (for finding current date)
NASR_INDEX_URL = "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/"
# Actual download base URL (different domain)
NASR_DOWNLOAD_URL = "https://nfdc.faa.gov/webContent/28DaySub/"


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
        req = Request(NASR_INDEX_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=30) as response:
            html = response.read().decode('utf-8')

        # Parse available subscription dates from directory listing
        # Pattern matches href="./../NASR_Subscription/YYYY-MM-DD" or similar paths
        date_pattern = r'NASR_Subscription/(\d{4}-\d{2}-\d{2})'
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

    # Download the ARB.zip file from nfdc.faa.gov
    zip_url = f"{NASR_DOWNLOAD_URL}{subscription_date}/ARB.zip"

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
            # List all files in the ZIP
            file_list = zf.namelist()
            print(f"    ZIP contains: {file_list}")

            # Look for ARB.txt file
            arb_file = None
            for name in file_list:
                if name.upper() == 'ARB.TXT' or name.upper().endswith('/ARB.TXT'):
                    arb_file = name
                    break

            if not arb_file:
                # Try any .txt file
                for name in file_list:
                    if name.endswith('.txt'):
                        arb_file = name
                        break

            if not arb_file:
                print("  Warning: Could not find ARB.txt in ZIP file")
                return get_embedded_boundaries()

            print(f"    Parsing {arb_file}...")
            with zf.open(arb_file) as f:
                content = f.read().decode('latin-1')
                boundaries = parse_arb_file(content)

    except zipfile.BadZipFile:
        print("  Error: Invalid ZIP file from NASR")
        return get_embedded_boundaries()

    if not boundaries:
        print("  Warning: No boundaries parsed, using embedded data")
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


def parse_arb_file(content: str) -> Dict[str, List[List[Tuple[float, float]]]]:
    """
    Parse FAA ARB (ARTCC Boundary) file format.

    Fixed-width format (397 chars per record):
    - Position 1-3: ARTCC ID (3 chars, part of 12-char identifier)
    - Position 4-12: Altitude structure code + point designator
    - Position 13-52: Center name (40 chars)
    - Position 53-62: Altitude structure decode (10 chars)
    - Position 63-76: Latitude (14 chars)
    - Position 77-90: Longitude (14 chars)
    - Position 91-390: Boundary description (300 chars)
    - Position 391-396: Sequence number (6 chars)
    - Position 397: NAS-only flag (1 char)

    Args:
        content: Raw file content

    Returns:
        Dict mapping ARTCC codes to boundary polygons
    """
    boundaries: Dict[str, List[List[Tuple[float, float]]]] = {}

    # Track points by ARTCC and altitude structure
    artcc_points: Dict[str, Dict[str, List[Tuple[int, float, float]]]] = {}

    # Use splitlines() to properly handle different line endings (CRLF, LF)
    for line in content.splitlines():
        # Skip empty lines and short lines
        if len(line) < 90:
            continue

        # Extract fields using fixed positions (0-indexed)
        artcc_id = line[0:3].strip().upper()

        # Skip if not a valid ARTCC ID (should be 3 uppercase letters starting with Z or K)
        if not artcc_id or len(artcc_id) != 3:
            continue

        # Only process continental US ARTCCs (start with Z)
        # Also include ZAN (Alaska), ZHN (Honolulu), ZSU (San Juan)
        if not artcc_id.startswith('Z'):
            continue

        # Extract airspace type: *H* = HIGH, *L* = LOW
        # Format is " *H*" starting at position 3, so H/L is at position 5
        airspace_type = line[5:6] if len(line) > 6 else 'H'  # Default to HIGH

        # Extract sequence number from positions 390-396 (end of record)
        # This is used to sort points in the correct order around the boundary
        try:
            seq_str = line[390:397].strip()
            seq_num = int(seq_str) if seq_str.isdigit() else 0
        except (ValueError, IndexError):
            seq_num = 0

        # Extract latitude (positions 63-76, 1-indexed = 62-75 0-indexed)
        lat_str = line[62:76].strip()

        # Extract longitude (positions 77-90, 1-indexed = 76-89 0-indexed)
        lon_str = line[76:90].strip()

        # Parse coordinates
        lat = parse_nasr_coordinate(lat_str)
        lon = parse_nasr_coordinate(lon_str)

        if lat is None or lon is None:
            continue

        # Store point by ARTCC and airspace type (H=HIGH, L=LOW)
        if artcc_id not in artcc_points:
            artcc_points[artcc_id] = {}

        if airspace_type not in artcc_points[artcc_id]:
            artcc_points[artcc_id][airspace_type] = []

        artcc_points[artcc_id][airspace_type].append((seq_num, lat, lon))

    # Convert to boundary polygons
    # Priority order for airspace types:
    # H = HIGH (main en-route boundary)
    # L = LOW (lower altitude boundary)
    # B = Base/Boundary (used by some facilities like ZHN, ZSU)
    # F = FIR (oceanic boundaries)
    for artcc_id, airspace_types in artcc_points.items():
        for airspace_type in ['H', 'L', 'B', 'F']:
            if airspace_type not in airspace_types:
                continue

            points = airspace_types[airspace_type]
            if not points:
                continue

            # Sort by sequence number
            points.sort(key=lambda p: p[0])

            # Extract just lat/lon
            polygon = [(lat, lon) for _, lat, lon in points]

            # Only include polygons with enough points
            if len(polygon) >= 3:
                # Close the polygon if not already closed
                if polygon[0] != polygon[-1]:
                    polygon.append(polygon[0])
                boundaries[artcc_id] = [polygon]
                break  # Use first valid airspace type

    return boundaries


def parse_nasr_coordinate(coord_str: str) -> Optional[float]:
    """
    Parse NASR coordinate format.

    NASR uses formatted coordinates like:
    - "40-25-30.000N" (DMS format with dashes)
    - "074-10-20.000W"

    Args:
        coord_str: Coordinate string from NASR file

    Returns:
        Decimal degrees or None if parsing fails
    """
    if not coord_str:
        return None

    # Pattern for DMS with dashes: DD-MM-SS.sssH
    match = re.match(r'(\d{2,3})-(\d{2})-(\d{2}(?:\.\d+)?)\s*([NSEW])', coord_str)
    if match:
        degrees = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))
        hemisphere = match.group(4)

        decimal = degrees + minutes / 60 + seconds / 3600

        if hemisphere in ('S', 'W'):
            decimal = -decimal

        return decimal

    # Try pattern without dashes: DDMMSS.sssH
    match = re.match(r'(\d{2,3})(\d{2})(\d{2}(?:\.\d+)?)\s*([NSEW])', coord_str)
    if match:
        degrees = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))
        hemisphere = match.group(4)

        decimal = degrees + minutes / 60 + seconds / 3600

        if hemisphere in ('S', 'W'):
            decimal = -decimal

        return decimal

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
