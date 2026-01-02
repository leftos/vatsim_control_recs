"""FAA NASR Navaid and Fix database for route parsing.

This module downloads and parses FAA NASR (National Airspace System Resources)
data to extract navaid and fix coordinates for parsing filed routes.

NASR data is downloaded once per 28-day cycle and cached locally.
"""

import io
import os
import re
import urllib.request
import urllib.error
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# NASR download settings
# The NASR subscription is at a static URL that updates each cycle
NASR_BASE_URL = "https://nfdc.faa.gov/webContent/28DaySub/"
NASR_TIMEOUT = 120  # seconds for download (larger file)

# Cache directory within the project's data folder
_script_dir = os.path.dirname(os.path.abspath(__file__))
NASR_CACHE_DIR = Path(os.path.join(_script_dir, '..', '..', 'data', 'navaids'))


# --- Data Classes ---


@dataclass
class Navaid:
    """A navaid (VOR, NDB, TACAN) with coordinates."""

    identifier: str  # e.g., "SFO", "OAK", "MZB"
    name: str  # e.g., "San Francisco", "Oakland"
    navaid_type: str  # "VOR", "VORDME", "VORTAC", "NDB", "TACAN"
    latitude: float
    longitude: float
    state: str = ""  # Two-letter state code


@dataclass
class Fix:
    """A named fix/intersection with coordinates."""

    identifier: str  # e.g., "SUNOL", "PORTE", "DAHJY"
    latitude: float
    longitude: float
    state: str = ""  # Two-letter state code


@dataclass
class Waypoint:
    """A waypoint along a route with coordinates."""

    identifier: str
    latitude: float
    longitude: float
    waypoint_type: str  # "navaid", "fix", "airport", "coordinate"


# --- NASR Cycle Calculation ---

# NASR uses the same AIRAC cycle dates
AIRAC_EPOCH = date(2025, 1, 23)
CYCLE_DAYS = 28


def get_current_nasr_cycle_date() -> str:
    """Get the effective date string for current NASR cycle.

    Returns:
        Date string in YYYY-MM-DD format
    """
    today = date.today()
    days_since_epoch = (today - AIRAC_EPOCH).days
    cycle_number = days_since_epoch // CYCLE_DAYS

    effective_date = AIRAC_EPOCH + timedelta(days=cycle_number * CYCLE_DAYS)
    return effective_date.strftime("%Y-%m-%d")


# --- NASR Download and Management ---


def get_nasr_cache_path() -> Path:
    """Get the cache directory for NASR data.

    Returns:
        Path to the cached NASR data directory
    """
    cycle_date = get_current_nasr_cycle_date()
    return NASR_CACHE_DIR / cycle_date


def ensure_nasr_data(quiet: bool = False) -> Optional[Path]:
    """Download NASR data if missing or outdated.

    Auto-downloads new NASR data when a new cycle begins.

    Args:
        quiet: If True, suppress print output

    Returns:
        Path to the NASR data directory, or None if download failed
    """
    cache_path = get_nasr_cache_path()
    nav_file = cache_path / "NAV.txt"
    fix_file = cache_path / "FIX.txt"

    # Check if we already have the data
    if nav_file.exists() and fix_file.exists():
        return cache_path

    # Try to download NASR subscription
    # The URL format is: 28DaySub/28DaySub_YYYY-MM-DD.zip
    cycle_date = get_current_nasr_cycle_date()
    url = f"{NASR_BASE_URL}28DaySub_{cycle_date}.zip"

    if not quiet:
        print(f"Downloading NASR data from {url}...")

    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'VATSIM-Control-Recs/1.0'}
        )
        with urllib.request.urlopen(req, timeout=NASR_TIMEOUT) as response:
            zip_data = response.read()
    except (urllib.error.URLError, TimeoutError) as e:
        if not quiet:
            print(f"Failed to download NASR: {e}")
        return None

    # Extract NAV.txt and FIX.txt from the zip
    try:
        cache_path.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            # Find and extract NAV.txt
            nav_found = False
            fix_found = False

            for name in zf.namelist():
                # NAV.txt is in NAV/NAV.txt
                if name.endswith("NAV.txt") or name == "NAV.txt":
                    with zf.open(name) as src:
                        nav_file.write_bytes(src.read())
                    nav_found = True
                # FIX.txt is in FIX/FIX.txt
                elif name.endswith("FIX.txt") or name == "FIX.txt":
                    with zf.open(name) as src:
                        fix_file.write_bytes(src.read())
                    fix_found = True

            if not nav_found or not fix_found:
                if not quiet:
                    print(f"NAV.txt or FIX.txt not found in NASR zip (found NAV: {nav_found}, FIX: {fix_found})")
                return None

            if not quiet:
                print(f"NASR data cached to {cache_path}")
            return cache_path

    except zipfile.BadZipFile as e:
        if not quiet:
            print(f"Invalid NASR zip file: {e}")
        return None


def cleanup_old_nasr_caches(keep_cycles: int = 2) -> int:
    """Remove cache directories for old NASR cycles.

    Args:
        keep_cycles: Number of recent cycles to keep (default: 2)

    Returns:
        Number of directories removed
    """
    if not NASR_CACHE_DIR.exists():
        return 0

    current_date = get_current_nasr_cycle_date()
    current = date.fromisoformat(current_date)
    removed = 0

    for cache_dir in NASR_CACHE_DIR.iterdir():
        if not cache_dir.is_dir():
            continue

        try:
            # Parse directory name as date
            dir_date = date.fromisoformat(cache_dir.name)
            days_diff = (current - dir_date).days

            if days_diff > keep_cycles * CYCLE_DAYS:
                # Remove old cache directory
                for f in cache_dir.iterdir():
                    f.unlink()
                cache_dir.rmdir()
                removed += 1
        except (ValueError, OSError):
            continue

    return removed


# --- NAV.txt Parsing ---


def _parse_dms_to_decimal(dms_str: str) -> Optional[float]:
    """Parse DMS (degrees-minutes-seconds) string to decimal degrees.

    Args:
        dms_str: String like "37-46-46.110N" or "122-32-17.380W"

    Returns:
        Decimal degrees, or None if parsing fails
    """
    # Pattern: DD-MM-SS.sss[NSEW]
    match = re.match(r'(\d+)-(\d+)-(\d+\.?\d*)\s*([NSEW])', dms_str.strip())
    if not match:
        return None

    degrees = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    direction = match.group(4)

    decimal = degrees + minutes / 60 + seconds / 3600

    if direction in ('S', 'W'):
        decimal = -decimal

    return decimal


def _parse_nav_record(line: str) -> Optional[Navaid]:
    """Parse a NAV.txt record line.

    NAV.txt format is fixed-width. Key fields:
    - Positions 1-4: Record type (NAV1, NAV2, etc.)
    - NAV1 records contain the main navaid data
    - Positions 5-8: Navaid type (VOR, VORDME, NDB, TACAN, etc.)
    - Positions 9-12: Identifier (may have spaces)
    - Positions 43-72: Official name
    - Positions 372-385: Latitude (DMS format)
    - Positions 397-410: Longitude (DMS format)
    - Positions 143-144: State

    Args:
        line: Raw NAV.txt record line

    Returns:
        Navaid if valid NAV1 record, None otherwise
    """
    if len(line) < 420:
        return None

    # Only process NAV1 records (main navaid data)
    record_type = line[0:4].strip()
    if record_type != "NAV1":
        return None

    try:
        navaid_type = line[4:24].strip()
        identifier = line[24:28].strip()
        name = line[42:72].strip()
        state = line[142:144].strip()

        # Parse coordinates (fixed positions in NAV1 record)
        lat_str = line[371:385].strip()
        lon_str = line[396:411].strip()

        latitude = _parse_dms_to_decimal(lat_str)
        longitude = _parse_dms_to_decimal(lon_str)

        if latitude is None or longitude is None:
            return None

        # Skip navaids without valid coordinates
        if latitude == 0 and longitude == 0:
            return None

        return Navaid(
            identifier=identifier,
            name=name,
            navaid_type=navaid_type,
            latitude=latitude,
            longitude=longitude,
            state=state,
        )
    except (IndexError, ValueError):
        return None


def _parse_fix_record(line: str) -> Optional[Fix]:
    """Parse a FIX.txt record line.

    FIX.txt format is fixed-width. Key fields:
    - Positions 1-4: Record type (FIX1, FIX2, etc.)
    - FIX1 records contain the main fix data
    - Positions 5-34: Fix identifier
    - Positions 35-36: State
    - Positions 67-80: Latitude (DMS format)
    - Positions 81-94: Longitude (DMS format)

    Args:
        line: Raw FIX.txt record line

    Returns:
        Fix if valid FIX1 record, None otherwise
    """
    if len(line) < 100:
        return None

    # Only process FIX1 records
    record_type = line[0:4].strip()
    if record_type != "FIX1":
        return None

    try:
        identifier = line[4:34].strip()
        state = line[34:36].strip()

        # Parse coordinates
        lat_str = line[66:80].strip()
        lon_str = line[80:95].strip()

        latitude = _parse_dms_to_decimal(lat_str)
        longitude = _parse_dms_to_decimal(lon_str)

        if latitude is None or longitude is None:
            return None

        return Fix(
            identifier=identifier,
            latitude=latitude,
            longitude=longitude,
            state=state,
        )
    except (IndexError, ValueError):
        return None


# --- High-Level API ---


@lru_cache(maxsize=1)
def load_navaids() -> Dict[str, Navaid]:
    """Load all navaids from NASR data.

    Returns:
        Dict mapping identifier to Navaid objects
    """
    cache_path = ensure_nasr_data(quiet=True)
    if not cache_path:
        return {}

    nav_file = cache_path / "NAV.txt"
    if not nav_file.exists():
        return {}

    navaids: Dict[str, Navaid] = {}

    try:
        with open(nav_file, "r", encoding="latin-1") as f:
            for line in f:
                navaid = _parse_nav_record(line)
                if navaid:
                    # Use identifier as key (may have duplicates - use first)
                    if navaid.identifier not in navaids:
                        navaids[navaid.identifier] = navaid
    except (OSError, IOError):
        return {}

    return navaids


@lru_cache(maxsize=1)
def load_fixes() -> Dict[str, Fix]:
    """Load all fixes from NASR data.

    Returns:
        Dict mapping identifier to Fix objects
    """
    cache_path = ensure_nasr_data(quiet=True)
    if not cache_path:
        return {}

    fix_file = cache_path / "FIX.txt"
    if not fix_file.exists():
        return {}

    fixes: Dict[str, Fix] = {}

    try:
        with open(fix_file, "r", encoding="latin-1") as f:
            for line in f:
                fix = _parse_fix_record(line)
                if fix:
                    # Use identifier as key
                    if fix.identifier not in fixes:
                        fixes[fix.identifier] = fix
    except (OSError, IOError):
        return {}

    return fixes


def get_waypoint_coordinates(identifier: str) -> Optional[Tuple[float, float]]:
    """Get coordinates for a waypoint identifier.

    Searches navaids first, then fixes.

    Args:
        identifier: Waypoint identifier (e.g., "SFO", "SUNOL")

    Returns:
        Tuple of (latitude, longitude) or None if not found
    """
    identifier = identifier.upper().strip()

    # Try navaids first
    navaids = load_navaids()
    if identifier in navaids:
        nav = navaids[identifier]
        return (nav.latitude, nav.longitude)

    # Try fixes
    fixes = load_fixes()
    if identifier in fixes:
        fix = fixes[identifier]
        return (fix.latitude, fix.longitude)

    return None


def _parse_coordinate_fix(identifier: str) -> Optional[Tuple[float, float]]:
    """Parse a coordinate-based fix like "3530N/11500W" or "35N115W".

    Args:
        identifier: Coordinate string

    Returns:
        Tuple of (latitude, longitude) or None if not parseable
    """
    # Pattern 1: DDMMN/DDDMMW (e.g., "3530N/11500W")
    match = re.match(r'(\d{2})(\d{2})([NS])/(\d{3})(\d{2})([EW])', identifier)
    if match:
        lat = int(match.group(1)) + int(match.group(2)) / 60
        if match.group(3) == 'S':
            lat = -lat
        lon = int(match.group(4)) + int(match.group(5)) / 60
        if match.group(6) == 'W':
            lon = -lon
        return (lat, lon)

    # Pattern 2: DDN/DDDW (e.g., "35N/115W")
    match = re.match(r'(\d{2})([NS])/(\d{2,3})([EW])', identifier)
    if match:
        lat = float(match.group(1))
        if match.group(2) == 'S':
            lat = -lat
        lon = float(match.group(3))
        if match.group(4) == 'W':
            lon = -lon
        return (lat, lon)

    return None


def parse_route_string(
    route: str,
    airports: Optional[Dict[str, Tuple[float, float]]] = None
) -> List[Waypoint]:
    """Parse a filed route string into waypoints with coordinates.

    Args:
        route: Filed route string (e.g., "SUNOL V27 HES V23 DAHJY")
        airports: Optional dict mapping ICAO codes to (lat, lon) tuples

    Returns:
        List of Waypoint objects with coordinates (unknown waypoints omitted)
    """
    if not route:
        return []

    airports = airports or {}
    waypoints: List[Waypoint] = []

    # Split route on whitespace
    parts = route.upper().split()

    for part in parts:
        # Skip airways (V##, J##, T##, Q##)
        if re.match(r'^[VJTQ]\d+$', part):
            continue

        # Skip SID/STAR names with digits (often at start/end)
        if re.match(r'^[A-Z]+\d+[A-Z]*$', part) and len(part) > 5:
            continue

        # Skip DCT (direct)
        if part == 'DCT':
            continue

        # Try to get coordinates
        coords = None
        waypoint_type = ""

        # Check airports first
        if part in airports:
            coords = airports[part]
            waypoint_type = "airport"
        elif len(part) == 4 and part.startswith('K'):
            # Try without K prefix for US airports
            short = part[1:]
            if short in airports:
                coords = airports[short]
                waypoint_type = "airport"

        # Try coordinate fix
        if not coords:
            coords = _parse_coordinate_fix(part)
            if coords:
                waypoint_type = "coordinate"

        # Try navaid/fix database
        if not coords:
            coords = get_waypoint_coordinates(part)
            if coords:
                # Determine type
                navaids = load_navaids()
                if part in navaids:
                    waypoint_type = "navaid"
                else:
                    waypoint_type = "fix"

        if coords:
            waypoints.append(Waypoint(
                identifier=part,
                latitude=coords[0],
                longitude=coords[1],
                waypoint_type=waypoint_type,
            ))

    return waypoints


def clear_navaid_cache() -> None:
    """Clear the LRU caches for navaid lookups.

    Useful when NASR data is updated.
    """
    load_navaids.cache_clear()
    load_fixes.cache_clear()
