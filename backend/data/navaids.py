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

# Cache directory (uses user data directory)
from common.paths import get_nasr_cache_dir

NASR_CACHE_DIR = get_nasr_cache_dir()


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
    waypoint_type: str  # "navaid", "fix", "airport", "coordinate", "airway_fix"


@dataclass
class AirwayFix:
    """A fix along an airway with sequence number."""

    identifier: str  # e.g., "MZB", "SUNOL"
    sequence: int  # Order along the airway
    latitude: float
    longitude: float


@dataclass
class AirwaySegmentRestriction:
    """MEA/MOCA altitude restrictions for an airway segment.

    Each segment is identified by its sequence number, which corresponds
    to the point-to-point segment ending at that sequence in the airway.
    """

    airway: str  # e.g., "V23", "J80"
    sequence: int  # Links to AirwayFix sequence (segment ends at this point)
    mea: int | None  # Minimum Enroute Altitude in feet (e.g., 5000)
    mea_opposite: int | None  # MEA for opposite direction (may differ)
    moca: int | None  # Minimum Obstruction Clearance Altitude in feet


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


def _download_nasr_file(url: str, dest_file: Path, quiet: bool = False) -> bool:
    """Download a single NASR zip file and extract its .txt file.

    Args:
        url: URL to download from
        dest_file: Path to save the extracted .txt file
        quiet: If True, suppress print output

    Returns:
        True if successful, False otherwise
    """
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'VATSIM-Control-Recs/1.0'}
        )
        with urllib.request.urlopen(req, timeout=NASR_TIMEOUT) as response:
            zip_data = response.read()

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            # Find the .txt file in the zip
            txt_name = dest_file.stem + ".txt"
            for name in zf.namelist():
                if name.endswith(txt_name) or name == txt_name:
                    with zf.open(name) as src:
                        dest_file.write_bytes(src.read())
                    return True

        if not quiet:
            print(f"{txt_name} not found in {url}")
        return False

    except (urllib.error.URLError, TimeoutError, zipfile.BadZipFile) as e:
        if not quiet:
            print(f"Failed to download {url}: {e}")
        return False


def ensure_nasr_data(quiet: bool = False) -> Optional[Path]:
    """Download NASR data if missing or outdated.

    Auto-downloads new NASR data when a new cycle begins.
    Downloads NAV.zip, FIX.zip, and AWY.zip separately for faster downloads.

    Args:
        quiet: If True, suppress print output

    Returns:
        Path to the NASR data directory, or None if download failed
    """
    cache_path = get_nasr_cache_path()
    nav_file = cache_path / "NAV.txt"
    fix_file = cache_path / "FIX.txt"
    awy_file = cache_path / "AWY.txt"

    # Check if we already have all the data
    if nav_file.exists() and fix_file.exists() and awy_file.exists():
        return cache_path

    cache_path.mkdir(parents=True, exist_ok=True)
    cycle_date = get_current_nasr_cycle_date()

    # Download NAV.zip, FIX.zip, and AWY.zip separately (smaller, faster downloads)
    # URL format: https://nfdc.faa.gov/webContent/28DaySub/{date}/NAV.zip
    nav_url = f"{NASR_BASE_URL}{cycle_date}/NAV.zip"
    fix_url = f"{NASR_BASE_URL}{cycle_date}/FIX.zip"
    awy_url = f"{NASR_BASE_URL}{cycle_date}/AWY.zip"

    if not nav_file.exists():
        if not quiet:
            print(f"Downloading NASR NAV data from {nav_url}...")
        if not _download_nasr_file(nav_url, nav_file, quiet):
            return None

    if not fix_file.exists():
        if not quiet:
            print(f"Downloading NASR FIX data from {fix_url}...")
        if not _download_nasr_file(fix_url, fix_file, quiet):
            return None

    if not awy_file.exists():
        if not quiet:
            print(f"Downloading NASR AWY data from {awy_url}...")
        if not _download_nasr_file(awy_url, awy_file, quiet):
            # Airways are optional - don't fail if we can't get them
            if not quiet:
                print("Warning: Could not download airway data")

    if not quiet:
        print(f"NASR data cached to {cache_path}")
    return cache_path


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

    NAV.txt format is fixed-width. Key fields (as of 2025-12-25 cycle):
    - Positions 0-4: Record type (NAV1, NAV2, etc.)
    - NAV1 records contain the main navaid data
    - Positions 4-8: Identifier (e.g., "SFO ", "OAK ")
    - Positions 8-28: Navaid type (VOR, VORDME, NDB, TACAN, etc.)
    - Positions 42-72: Official name/city
    - Positions 142-144: State
    - Positions 371-385: Latitude (DMS format)
    - Positions 396-411: Longitude (DMS format)

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
        identifier = line[4:8].strip()
        navaid_type = line[8:28].strip()
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


def _parse_awy1_record(line: str) -> Optional[Tuple[str, AirwaySegmentRestriction]]:
    """Parse an AWY.txt AWY1 record line for MEA/MOCA data.

    AWY.txt format is fixed-width. AWY1 records contain altitude restrictions:
    - Positions 0-4: Record type "AWY1"
    - Positions 4-9: Airway designator (e.g., "V27  ", "J1   ")
    - Positions 10-15: Sequence number (identifies segment)
    - Positions 74-79: MEA (5 digits, right-justified, e.g., "05000")
    - Positions 85-90: MEA opposite direction (may be blank)
    - Positions 101-106: MOCA (5 digits or blank)

    Args:
        line: Raw AWY.txt record line

    Returns:
        Tuple of (airway_designator, AirwaySegmentRestriction) if valid, None otherwise
    """
    if len(line) < 110:
        return None

    # Only process AWY1 records
    record_type = line[0:4].strip()
    if record_type != "AWY1":
        return None

    try:
        # Extract airway designator (positions 4-9)
        airway = line[4:9].strip()
        if not airway:
            return None

        # Extract sequence number (positions 10-15)
        seq_str = line[10:15].strip()
        if not seq_str:
            return None
        sequence = int(seq_str)

        # Extract MEA (positions 74-79)
        mea_str = line[74:79].strip()
        mea = int(mea_str) if mea_str and mea_str.isdigit() else None

        # Extract MEA opposite direction (positions 85-90)
        mea_opp_str = line[85:90].strip()
        mea_opposite = int(mea_opp_str) if mea_opp_str and mea_opp_str.isdigit() else None

        # Extract MOCA (positions 101-106)
        moca_str = line[101:106].strip()
        moca = int(moca_str) if moca_str and moca_str.isdigit() else None

        # Skip if no altitude data at all
        if mea is None and mea_opposite is None and moca is None:
            return None

        return (airway, AirwaySegmentRestriction(
            airway=airway,
            sequence=sequence,
            mea=mea,
            mea_opposite=mea_opposite,
            moca=moca,
        ))

    except (IndexError, ValueError):
        return None


def _parse_awy_record(line: str) -> Optional[Tuple[str, AirwayFix]]:
    """Parse an AWY.txt record line (AWY2 records only).

    AWY.txt format is fixed-width. AWY2 records contain fix details:
    - Positions 0-4: Record type (AWY1, AWY2, etc.)
    - AWY2 records contain the fix location data
    - Positions 4:13: Airway designator (e.g., "V27      ")
    - Positions 13:16: Sequence number
    - Positions 16:46: Fix name (full name)
    - Latitude/longitude in DMS format (variable position, use regex)
    - Fix identifier is near the end of the line

    Args:
        line: Raw AWY.txt record line

    Returns:
        Tuple of (airway_designator, AirwayFix) if valid, None otherwise
    """
    if len(line) < 120:
        return None

    # Only process AWY2 records (fix location data)
    record_type = line[0:4].strip()
    if record_type != "AWY2":
        return None

    try:
        # Extract airway designator and sequence number
        # Format: "AWY2" + airway (variable length) + spaces + sequence + fix name
        # e.g., "AWY2V27      20REDIN..." or "AWY2J1      100AVENAL..."
        header_match = re.match(r'AWY2([A-Z][A-Z0-9]*)\s*(\d+)', line)
        if not header_match:
            return None

        airway = header_match.group(1)
        sequence = int(header_match.group(2))

        # Find latitude and longitude using regex
        # Pattern: digits-digits-digits.decimals + N/S or E/W
        # Some records have no space before lat (e.g., "CAK232-57-03.86N")
        # so we look for 2-digit lat degrees followed by -mm-ss pattern
        lat_match = re.search(r'(\d{2})-(\d{2})-(\d{2}\.?\d*)([NS])', line)
        lon_match = re.search(r'(\d{2,3})-(\d{2})-(\d{2}\.?\d*)([EW])', line)

        if not lat_match or not lon_match:
            return None

        # Parse latitude
        lat_deg = int(lat_match.group(1))
        lat_min = int(lat_match.group(2))
        lat_sec = float(lat_match.group(3)) if lat_match.group(3) else 0.0
        latitude = lat_deg + lat_min / 60 + lat_sec / 3600
        if lat_match.group(4) == 'S':
            latitude = -latitude

        # Parse longitude
        lon_deg = int(lon_match.group(1))
        lon_min = int(lon_match.group(2))
        lon_sec = float(lon_match.group(3)) if lon_match.group(3) else 0.0
        longitude = lon_deg + lon_min / 60 + lon_sec / 3600
        if lon_match.group(4) == 'W':
            longitude = -longitude

        # Extract fix identifier from the remaining part of the line
        # Format is typically: "... V27  *REDIN*CA..." or "...MZB V27  *MZB*C..."
        remaining = line[lon_match.end():]

        fix_id = None

        # Pattern 1: Look for *FIXID* pattern (most common for fixes)
        star_match = re.search(r'\*([A-Z]{2,5})\*', remaining)
        if star_match:
            fix_id = star_match.group(1)
        else:
            # Pattern 2: Look for 3-letter code followed by airway (for VORTACs)
            id_match = re.search(r'\s+([A-Z]{2,5})\s+' + re.escape(airway), remaining)
            if id_match:
                fix_id = id_match.group(1)

        if not fix_id:
            return None

        return (airway, AirwayFix(
            identifier=fix_id,
            sequence=sequence,
            latitude=latitude,
            longitude=longitude,
        ))

    except (IndexError, ValueError):
        return None


@lru_cache(maxsize=1)
def load_airways() -> Dict[str, List[AirwayFix]]:
    """Load all airways from NASR data.

    Returns:
        Dict mapping airway designator to ordered list of AirwayFix objects
    """
    cache_path = ensure_nasr_data(quiet=True)
    if not cache_path:
        return {}

    awy_file = cache_path / "AWY.txt"
    if not awy_file.exists():
        return {}

    airways: Dict[str, List[AirwayFix]] = {}

    try:
        with open(awy_file, "r", encoding="latin-1") as f:
            for line in f:
                result = _parse_awy_record(line)
                if result:
                    airway, fix = result
                    if airway not in airways:
                        airways[airway] = []
                    airways[airway].append(fix)

        # Sort each airway's fixes by sequence number
        for airway in airways:
            airways[airway].sort(key=lambda f: f.sequence)

    except (OSError, IOError):
        return {}

    return airways


@lru_cache(maxsize=1)
def load_airway_restrictions() -> Dict[str, Dict[int, AirwaySegmentRestriction]]:
    """Load MEA/MOCA restrictions for all airways from NASR data.

    Returns:
        Dict mapping airway designator to dict of sequence -> restriction.
        Example: {"V23": {20: AirwaySegmentRestriction(...), 30: ...}}
    """
    cache_path = ensure_nasr_data(quiet=True)
    if not cache_path:
        return {}

    awy_file = cache_path / "AWY.txt"
    if not awy_file.exists():
        return {}

    restrictions: Dict[str, Dict[int, AirwaySegmentRestriction]] = {}

    try:
        with open(awy_file, "r", encoding="latin-1") as f:
            for line in f:
                result = _parse_awy1_record(line)
                if result:
                    airway, restriction = result
                    if airway not in restrictions:
                        restrictions[airway] = {}
                    restrictions[airway][restriction.sequence] = restriction

    except (OSError, IOError):
        return {}

    return restrictions


def get_airway_fixes(
    airway: str,
    entry_fix: Optional[str] = None,
    exit_fix: Optional[str] = None
) -> List[AirwayFix]:
    """Get fixes along an airway, optionally between entry and exit points.

    Args:
        airway: Airway designator (e.g., "V27", "J1")
        entry_fix: Optional entry fix identifier (start of segment)
        exit_fix: Optional exit fix identifier (end of segment)

    Returns:
        List of AirwayFix objects along the airway segment
    """
    airways = load_airways()
    if airway not in airways:
        return []

    fixes = airways[airway]

    if not entry_fix and not exit_fix:
        return fixes

    # Find entry and exit indices
    entry_idx = 0
    exit_idx = len(fixes) - 1

    if entry_fix:
        for i, fix in enumerate(fixes):
            if fix.identifier == entry_fix:
                entry_idx = i
                break

    if exit_fix:
        for i, fix in enumerate(fixes):
            if fix.identifier == exit_fix:
                exit_idx = i
                break

    # Handle reversed direction (exit before entry)
    if entry_idx > exit_idx:
        entry_idx, exit_idx = exit_idx, entry_idx

    return fixes[entry_idx:exit_idx + 1]


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


def _get_fix_identifier(
    part: str,
    airports: Dict[str, Tuple[float, float]]
) -> Optional[str]:
    """Try to resolve a route part to a known fix/navaid identifier.

    Returns the identifier if found, None otherwise.
    """
    part = part.upper()

    # Check airports
    if part in airports:
        return part
    if len(part) == 4 and part.startswith('K') and part[1:] in airports:
        return part

    # Check navaids
    navaids = load_navaids()
    if part in navaids:
        return part

    # Check fixes
    fixes = load_fixes()
    if part in fixes:
        return part

    return None


def parse_route_string(
    route: str,
    airports: Optional[Dict[str, Tuple[float, float]]] = None
) -> List[Waypoint]:
    """Parse a filed route string into waypoints with coordinates.

    Expands airways (V, J, T, Q routes) into their constituent fixes.

    Args:
        route: Filed route string (e.g., "SUNOL V27 BSR")
        airports: Optional dict mapping ICAO codes to (lat, lon) tuples

    Returns:
        List of Waypoint objects with coordinates (unknown waypoints omitted)
    """
    if not route:
        return []

    airports = airports or {}
    waypoints: List[Waypoint] = []
    seen_identifiers: set = set()  # Avoid duplicates

    # Split route on whitespace
    parts = route.upper().split()

    i = 0
    while i < len(parts):
        part = parts[i]

        # Check if this is an airway (V##, J##, T##, Q##)
        if re.match(r'^[VJTQ]\d+$', part):
            # Find entry fix (previous waypoint) and exit fix (next non-airway part)
            entry_fix = waypoints[-1].identifier if waypoints else None

            # Look ahead to find exit fix
            exit_fix = None
            j = i + 1
            while j < len(parts):
                next_part = parts[j]
                # Skip consecutive airways
                if re.match(r'^[VJTQ]\d+$', next_part):
                    j += 1
                    continue
                # Skip DCT
                if next_part == 'DCT':
                    j += 1
                    continue
                # Skip SID/STAR names
                if re.match(r'^[A-Z]+\d+[A-Z]*$', next_part) and len(next_part) > 5:
                    j += 1
                    continue
                # Found a potential exit fix
                exit_fix = _get_fix_identifier(next_part, airports)
                break

            # Get airway fixes between entry and exit
            if entry_fix or exit_fix:
                airway_fixes = get_airway_fixes(part, entry_fix, exit_fix)

                # Add intermediate fixes (skip entry since it's already added)
                for awy_fix in airway_fixes:
                    if awy_fix.identifier not in seen_identifiers:
                        # Skip if this is the entry fix (already in waypoints)
                        if entry_fix and awy_fix.identifier == entry_fix:
                            continue
                        waypoints.append(Waypoint(
                            identifier=awy_fix.identifier,
                            latitude=awy_fix.latitude,
                            longitude=awy_fix.longitude,
                            waypoint_type="airway_fix",
                        ))
                        seen_identifiers.add(awy_fix.identifier)

            i += 1
            continue

        # Skip SID/STAR names with digits (often at start/end)
        if re.match(r'^[A-Z]+\d+[A-Z]*$', part) and len(part) > 5:
            i += 1
            continue

        # Skip DCT (direct)
        if part == 'DCT':
            i += 1
            continue

        # Try to get coordinates for this waypoint
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

        if coords and part not in seen_identifiers:
            waypoints.append(Waypoint(
                identifier=part,
                latitude=coords[0],
                longitude=coords[1],
                waypoint_type=waypoint_type,
            ))
            seen_identifiers.add(part)

        i += 1

    return waypoints


def clear_navaid_cache() -> None:
    """Clear the LRU caches for navaid lookups.

    Useful when NASR data is updated.
    """
    load_navaids.cache_clear()
    load_fixes.cache_clear()
    load_airways.cache_clear()
    load_airway_restrictions.cache_clear()


@dataclass
class MeaViolation:
    """Information about an MEA violation on a route segment."""

    airway: str  # e.g., "V23"
    segment_start: str  # Fix identifier where segment starts
    segment_end: str  # Fix identifier where segment ends
    mea: int  # Required MEA in feet


def get_max_mea_for_route(
    route: str,
    airports: Optional[Dict[str, Tuple[float, float]]] = None
) -> Tuple[int | None, List[MeaViolation]]:
    """Get the maximum MEA required for airways in a route.

    Parses the route string, identifies airways used, and looks up
    MEA requirements for each airway segment.

    Args:
        route: Filed route string (e.g., "KSFO V25 SAC J80 RNO KRNO")
        airports: Optional dict mapping ICAO codes to (lat, lon) tuples

    Returns:
        Tuple of (max_mea, violations) where:
        - max_mea: Maximum MEA required across all airways (None if no airways)
        - violations: List of MeaViolation objects for segments with MEA data
    """
    if not route:
        return (None, [])

    airports = airports or {}
    restrictions = load_airway_restrictions()
    airways_data = load_airways()

    if not restrictions:
        return (None, [])

    # Parse route to find airways and their entry/exit points
    parts = route.upper().split()
    violations: List[MeaViolation] = []
    max_mea: int | None = None

    i = 0
    while i < len(parts):
        part = parts[i]

        # Check if this is an airway (V##, J##, T##, Q##)
        if re.match(r'^[VJTQ]\d+$', part):
            airway = part

            # Find entry fix (previous non-airway, non-DCT part)
            entry_fix = None
            for j in range(i - 1, -1, -1):
                prev = parts[j]
                if prev != 'DCT' and not re.match(r'^[VJTQ]\d+$', prev):
                    # Skip SID/STAR names
                    if not (re.match(r'^[A-Z]+\d+[A-Z]*$', prev) and len(prev) > 5):
                        entry_fix = prev
                        break

            # Find exit fix (next non-airway, non-DCT part)
            exit_fix = None
            for j in range(i + 1, len(parts)):
                next_part = parts[j]
                if next_part != 'DCT' and not re.match(r'^[VJTQ]\d+$', next_part):
                    # Skip SID/STAR names
                    if not (re.match(r'^[A-Z]+\d+[A-Z]*$', next_part) and len(next_part) > 5):
                        exit_fix = next_part
                        break

            # Look up MEA for this airway
            if airway in restrictions and airway in airways_data:
                airway_restrictions = restrictions[airway]
                airway_fixes = airways_data[airway]

                # Find the sequence range for entry/exit fixes
                entry_seq = None
                exit_seq = None

                for fix in airway_fixes:
                    if entry_fix and fix.identifier == entry_fix:
                        entry_seq = fix.sequence
                    if exit_fix and fix.identifier == exit_fix:
                        exit_seq = fix.sequence

                # Get MEA for segments in the used portion
                # If we found both entry and exit, only check those segments
                # Otherwise, check all segments on the airway
                for seq, restr in airway_restrictions.items():
                    # Determine if this segment is in our used portion
                    in_range = True
                    if entry_seq is not None and exit_seq is not None:
                        min_seq = min(entry_seq, exit_seq)
                        max_seq = max(entry_seq, exit_seq)
                        # Segment at sequence N is between fix N-1 and fix N
                        in_range = min_seq < seq <= max_seq

                    if in_range and restr.mea is not None:
                        # Find the fix identifiers for this segment
                        segment_end = None
                        segment_start = None
                        for fix in airway_fixes:
                            if fix.sequence == seq:
                                segment_end = fix.identifier
                            elif fix.sequence == seq - 10:  # Typical spacing is 10
                                segment_start = fix.identifier

                        # If we can't find exact fixes, use generic labels
                        if not segment_start:
                            segment_start = f"seq{seq - 10}"
                        if not segment_end:
                            segment_end = f"seq{seq}"

                        violations.append(MeaViolation(
                            airway=airway,
                            segment_start=segment_start,
                            segment_end=segment_end,
                            mea=restr.mea,
                        ))

                        if max_mea is None or restr.mea > max_mea:
                            max_mea = restr.mea

        i += 1

    return (max_mea, violations)
