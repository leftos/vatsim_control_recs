"""FAA NASR Navaid and Fix database for route parsing.

This module downloads and parses FAA NASR (National Airspace System Resources)
data to extract navaid and fix coordinates for parsing filed routes.

NASR data is downloaded once per 28-day cycle and cached locally.
"""

import io
import math
import re
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from common import logger as debug_logger
from common.paths import get_nasr_cache_dir

# NASR download settings
# The NASR subscription is at a static URL that updates each cycle
NASR_BASE_URL = "https://nfdc.faa.gov/webContent/28DaySub/"
NASR_TIMEOUT = 120  # seconds for download (larger file)

# Cache directory (uses user data directory)
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


@dataclass(frozen=True)
class AirwayBorderPoint:
    """A point where an airway crosses the US border; AWY.txt gives it no position."""

    identifier: str  # e.g., "U.S. CANADIAN BORDER", "U.S. MEXICAN BORDER"
    sequence: int  # Order along the airway


AirwayPoint = AirwayFix | AirwayBorderPoint


@dataclass
class AirwaySegmentRestriction:
    """MEA/MOCA altitude restrictions for an airway segment.

    Each segment is identified by its sequence number: the record at sequence
    N describes the segment from point N to the next point of the airway.
    """

    airway: str  # e.g., "V23", "J80"
    sequence: int  # Links to AirwayFix sequence (segment leaves this point)
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
    # AIRAC cycles change on a UTC boundary, not the machine's local one
    today = datetime.now(timezone.utc).date()
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
            url, headers={"User-Agent": "VATSIM-Control-Recs/1.0"}
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


def ensure_nasr_data(quiet: bool = False) -> Path | None:
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
        # Airways are optional - don't fail if we can't get them
        if not _download_nasr_file(awy_url, awy_file, quiet) and not quiet:
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


def _parse_dms_to_decimal(dms_str: str) -> float | None:
    """Parse DMS (degrees-minutes-seconds) string to decimal degrees.

    Args:
        dms_str: String like "37-46-46.110N" or "122-32-17.380W"

    Returns:
        Decimal degrees, or None if parsing fails
    """
    # Pattern: DD-MM-SS.sss[NSEW]
    match = re.match(r"(\d+)-(\d+)-(\d+\.?\d*)\s*([NSEW])", dms_str.strip())
    if not match:
        return None

    degrees = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    direction = match.group(4)

    decimal = degrees + minutes / 60 + seconds / 3600

    if direction in ("S", "W"):
        decimal = -decimal

    return decimal


def _parse_nav_record(line: str) -> Navaid | None:
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


def _parse_fix_record(line: str) -> Fix | None:
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
def load_navaids() -> dict[str, Navaid]:
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

    navaids: dict[str, Navaid] = {}

    try:
        with open(nav_file, encoding="latin-1") as f:
            for line in f:
                navaid = _parse_nav_record(line)
                # Use identifier as key (may have duplicates - use first)
                if navaid and navaid.identifier not in navaids:
                    navaids[navaid.identifier] = navaid
    except OSError:
        return {}

    return navaids


@lru_cache(maxsize=1)
def load_fixes() -> dict[str, Fix]:
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

    fixes: dict[str, Fix] = {}

    try:
        with open(fix_file, encoding="latin-1") as f:
            for line in f:
                fix = _parse_fix_record(line)
                # Use identifier as key
                if fix and fix.identifier not in fixes:
                    fixes[fix.identifier] = fix
    except OSError:
        return {}

    return fixes


def _parse_awy1_record(
    line: str,
) -> tuple[tuple[str, str], AirwaySegmentRestriction] | None:
    """Parse an AWY.txt AWY1 record line for MEA/MOCA data.

    AWY.txt format is fixed-width. AWY1 records contain altitude restrictions:
    - Positions 0-4: Record type "AWY1"
    - Positions 4-9: Airway designator (e.g., "V27  ", "J1   ")
    - Position 9: Airway type (blank = federal, "A" = Alaska, "H" = Hawaii)
    - Positions 10-15: Sequence number of the point the segment leaves
    - Positions 74-79: MEA (5 digits, right-justified, e.g., "05000")
    - Positions 85-90: MEA opposite direction (may be blank)
    - Positions 101-106: MOCA (5 digits or blank)

    Args:
        line: Raw AWY.txt record line

    Returns:
        Tuple of ((airway_designator, airway_type), AirwaySegmentRestriction) if
        valid, None otherwise; the federal airway type is the empty string
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
        mea_opposite = (
            int(mea_opp_str) if mea_opp_str and mea_opp_str.isdigit() else None
        )

        # Extract MOCA (positions 101-106)
        moca_str = line[101:106].strip()
        moca = int(moca_str) if moca_str and moca_str.isdigit() else None

        # Skip if no altitude data at all
        if mea is None and mea_opposite is None and moca is None:
            return None

        return (
            (airway, line[9].strip()),
            AirwaySegmentRestriction(
                airway=airway,
                sequence=sequence,
                mea=mea,
                mea_opposite=mea_opposite,
                moca=moca,
            ),
        )

    except (IndexError, ValueError):
        return None


# Pattern: digits-digits-digits.decimals + N/S or E/W
# Some records have no space before lat (e.g., "CAK232-57-03.86N")
# so we look for 2-digit lat degrees followed by -mm-ss pattern
_AWY2_LATITUDE_RE = re.compile(r"(\d{2})-(\d{2})-(\d{2}\.?\d*)([NS])")
_AWY2_LONGITUDE_RE = re.compile(r"(\d{2,3})-(\d{2})-(\d{2}\.?\d*)([EW])")


def _dms_degrees(match: re.Match[str]) -> float:
    """Signed degrees from a degrees-minutes-seconds-hemisphere match."""
    degrees = (
        int(match.group(1)) + int(match.group(2)) / 60 + float(match.group(3)) / 3600
    )
    return -degrees if match.group(4) in "SW" else degrees


def _awy2_fix_identifier(remaining: str, airway: str) -> str | None:
    """The fix identifier written after an AWY2 record's position.

    Format is typically: "... V27  *REDIN*CA..." or "...MZB V27  *MZB*C...".
    """
    # Pattern 1: Look for *FIXID* pattern (most common for fixes)
    star_match = re.search(r"\*([A-Z]{2,5})\*", remaining)
    if star_match:
        return star_match.group(1)
    # Pattern 2: Look for 3-letter code followed by airway (for VORTACs)
    id_match = re.search(r"\s+([A-Z]{2,5})\s+" + re.escape(airway), remaining)
    return id_match.group(1) if id_match else None


def _parse_awy2_fix(line: str, airway: str, sequence: int) -> AirwayFix | None:
    """The positioned fix of an AWY2 record, or None when it has no position or id."""
    lat_match = _AWY2_LATITUDE_RE.search(line)
    lon_match = _AWY2_LONGITUDE_RE.search(line)
    if not lat_match or not lon_match:
        return None
    fix_id = _awy2_fix_identifier(line[lon_match.end() :], airway)
    if not fix_id:
        return None
    return AirwayFix(
        identifier=fix_id,
        sequence=sequence,
        latitude=_dms_degrees(lat_match),
        longitude=_dms_degrees(lon_match),
    )


def _parse_awy_record(line: str) -> tuple[tuple[str, str], AirwayPoint] | None:
    """Parse an AWY.txt record line (AWY2 records only).

    AWY.txt format is fixed-width. AWY2 records contain fix details:
    - Positions 0-4: Record type (AWY1, AWY2, etc.)
    - AWY2 records contain the fix location data
    - Positions 4:9: Airway designator (e.g., "V27  ")
    - Position 9: Airway type (blank = federal, "A" = Alaska, "H" = Hawaii)
    - Positions 10:15: Sequence number
    - Positions 15:45: Fix name (full name); "U.S. CANADIAN BORDER" or
      "U.S. MEXICAN BORDER" for a border point, which has no position
    - Latitude/longitude in DMS format (variable position, use regex)
    - Fix identifier is near the end of the line

    Args:
        line: Raw AWY.txt record line

    Returns:
        Tuple of ((airway_designator, airway_type), AirwayFix or
        AirwayBorderPoint) if valid, None otherwise; the federal airway type is
        the empty string
    """
    # Only process AWY2 records (fix location data)
    if len(line) < 120 or line[0:4] != "AWY2":
        return None

    airway = line[4:9].strip()
    sequence_text = line[10:15].strip()
    if not airway or not sequence_text.isdigit():
        return None
    key = (airway, line[9].strip())
    sequence = int(sequence_text)

    name = line[15:45].strip()
    if name.endswith(" BORDER"):
        return key, AirwayBorderPoint(identifier=name, sequence=sequence)

    fix = _parse_awy2_fix(line, airway, sequence)
    return None if fix is None else (key, fix)


@lru_cache(maxsize=1)
def load_airways() -> dict[tuple[str, str], list[AirwayPoint]]:
    """Load all airways from NASR data.

    Returns:
        Dict mapping (airway designator, airway type) to the airway's points in
        sequence order: AirwayFix objects, and AirwayBorderPoint objects (no
        position) where it crosses the US border. The airway type is "" for
        federal airways, "A" for Alaska and "H" for Hawaii, which reuse federal
        designations
    """
    cache_path = ensure_nasr_data(quiet=True)
    if not cache_path:
        return {}

    awy_file = cache_path / "AWY.txt"
    if not awy_file.exists():
        return {}

    airways: dict[tuple[str, str], list[AirwayPoint]] = {}

    try:
        with open(awy_file, encoding="latin-1") as f:
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

    except OSError:
        return {}

    return airways


@lru_cache(maxsize=1)
def load_airway_restrictions() -> dict[
    tuple[str, str], dict[int, AirwaySegmentRestriction]
]:
    """Load MEA/MOCA restrictions for all airways from NASR data.

    Returns:
        Dict mapping (airway designator, airway type) to dict of sequence ->
        restriction for the segment leaving the point at that sequence.
        Example: {("V23", ""): {20: AirwaySegmentRestriction(...), 30: ...}}
    """
    cache_path = ensure_nasr_data(quiet=True)
    if not cache_path:
        return {}

    awy_file = cache_path / "AWY.txt"
    if not awy_file.exists():
        return {}

    restrictions: dict[tuple[str, str], dict[int, AirwaySegmentRestriction]] = {}

    try:
        with open(awy_file, encoding="latin-1") as f:
            for line in f:
                result = _parse_awy1_record(line)
                if result:
                    airway, restriction = result
                    if airway not in restrictions:
                        restrictions[airway] = {}
                    restrictions[airway][restriction.sequence] = restriction

    except OSError:
        return {}

    return restrictions


# Airway types of AWY.txt column 10: federal (blank), Alaska and Hawaii
_AIRWAY_TYPES = ("", "A", "H")


@dataclass(frozen=True)
class SkippedAirway:
    """An airway a route names but whose flown stretch is unknown.

    One end of the route's stretch is on the airway and the other is neither
    on it nor placeable beyond one of its border points, so neither its MEAs
    are checked nor its fixes expanded.
    """

    airway: str  # e.g., "V12"
    missing_fix: str  # The entry or exit fix that is not on the airway


@dataclass(frozen=True)
class _AirwayStretch:
    """The points of one airway type and the span of them a route flies."""

    airway: str
    airway_type: str
    points: list[AirwayPoint]
    entry_index: int  # Index in points where the route joins the airway
    exit_index: int  # Index in points where the route leaves the airway

    @property
    def first(self) -> int:
        """Index in points of the lower-sequence end of the stretch."""
        return min(self.entry_index, self.exit_index)

    @property
    def last(self) -> int:
        """Index in points of the higher-sequence end of the stretch."""
        return max(self.entry_index, self.exit_index)


@dataclass(frozen=True)
class _OneEndMatch:
    """An airway type with only one of a route's entry and exit fixes on it."""

    airway_type: str
    points: list[AirwayPoint]
    entry_index: int | None  # Index of the entry fix, None when not on it
    exit_index: int | None  # Index of the exit fix, None when not on it


def _point_index(points: list[AirwayPoint], identifier: str) -> int | None:
    """Index of the first point with this identifier, None when there is none."""
    return next(
        (i for i, point in enumerate(points) if point.identifier == identifier), None
    )


def _rough_distance_sq(a: tuple[float, float], b: tuple[float, float]) -> float:
    """A squared distance in degrees that orders nearby points like the true distance."""
    longitude_scale = math.cos(math.radians((a[0] + b[0]) / 2))
    return (a[0] - b[0]) ** 2 + ((a[1] - b[1]) * longitude_scale) ** 2


def _border_distance_sq(
    points: list[AirwayPoint], border: int, step: int, position: tuple[float, float]
) -> float:
    """Squared distance from a position to the fix just past a border point.

    Past means away from the route's found end (step is +1 or -1). For the
    first border point of an adjacent pair (the airway leaves the US and
    re-enters it), the fix past it is the US re-entry fix across the gap; real
    gaps are short (J5 2 nm, J126 9 nm). A border point at the airway's end has
    no fix past it, so the fix just inside it is used.
    """
    past = [p for p in points[border::step] if isinstance(p, AirwayFix)]
    inside = [p for p in points[border::-step] if isinstance(p, AirwayFix)]
    nearest = (past or inside)[0]
    return _rough_distance_sq(position, (nearest.latitude, nearest.longitude))


def _inside_fix_is_nearer(
    points: list[AirwayPoint],
    found_index: int,
    border: int,
    position: tuple[float, float],
) -> bool:
    """Whether a position is no farther from the last fix before a border than from the found fix.

    The last fix before the border is the last positioned point walking from
    the found end toward it (the found fix itself when nothing lies between).
    """
    step = 1 if border > found_index else -1
    between = [p for p in points[found_index:border:step] if isinstance(p, AirwayFix)]
    found, inside = between[0], between[-1]
    return _rough_distance_sq(
        position, (inside.latitude, inside.longitude)
    ) <= _rough_distance_sq(position, (found.latitude, found.longitude))


def _border_index(
    points: list[AirwayPoint], found_index: int, missing_fix: str
) -> int | None:
    """The border point a route leaves or joins the airway at, beyond its found end.

    The border point nearest the found end on each side is a candidate. With
    one candidate it is used, unless the missing fix has a position that is
    farther from the last fix before the border than from the found fix (a
    misfiled fix on the other side). With two, the one whose fix past it lies
    nearer the missing fix is used when that fix has a position. None otherwise.
    """
    borders = [i for i, p in enumerate(points) if isinstance(p, AirwayBorderPoint)]
    below = max((i for i in borders if i < found_index), default=None)
    above = min((i for i in borders if i > found_index), default=None)
    if below is None and above is None:
        return None
    position = get_waypoint_coordinates(missing_fix)
    if below is None or above is None:
        border = above if below is None else below
        if position is None or _inside_fix_is_nearer(
            points, found_index, border, position
        ):
            return border
        return None
    if position is None:
        return None
    below_distance = _border_distance_sq(points, below, -1, position)
    above_distance = _border_distance_sq(points, above, 1, position)
    return below if below_distance < above_distance else above


def _cross_border_stretch(
    airway: str, match: _OneEndMatch, missing_fix: str
) -> _AirwayStretch | None:
    """The US stretch from the end found on the airway to its border point, if any."""
    entry_found = match.entry_index is not None
    found_index = match.entry_index if entry_found else match.exit_index
    if found_index is None:
        return None
    border = _border_index(match.points, found_index, missing_fix)
    if border is None:
        return None
    return _AirwayStretch(
        airway=airway,
        airway_type=match.airway_type,
        points=match.points,
        entry_index=found_index if entry_found else border,
        exit_index=border if entry_found else found_index,
    )


def _find_airway_stretch(
    airway: str, entry_fix: str, exit_fix: str
) -> _AirwayStretch | SkippedAirway | None:
    """Find the stretch of an airway between a route's entry and exit fixes.

    Uses the airway type whose points hold both fixes. When only one is on it
    and the airway crosses the US border, the stretch runs from that fix to the
    border point toward the other. Logs (at debug) every airway it cannot
    place whole.

    Returns:
        The stretch; a SkippedAirway naming the fix that is not on the airway
        when only one end is on it and no border point applies; None when
        neither end is on it
    """
    airways = load_airways()
    one_end: _OneEndMatch | None = None
    for airway_type in _AIRWAY_TYPES:
        points = airways.get((airway, airway_type), [])
        entry_index = _point_index(points, entry_fix)
        exit_index = _point_index(points, exit_fix)
        if entry_index is not None and exit_index is not None:
            return _AirwayStretch(airway, airway_type, points, entry_index, exit_index)
        if one_end is None and (entry_index is not None or exit_index is not None):
            one_end = _OneEndMatch(airway_type, points, entry_index, exit_index)

    route_text = f"{entry_fix} {airway} {exit_fix}"
    if one_end is None:
        debug_logger.debug(
            f"Airway {airway} skipped: neither end is on it ({route_text})"
        )
        return None
    missing_fix = exit_fix if one_end.entry_index is not None else entry_fix
    stretch = _cross_border_stretch(airway, one_end, missing_fix)
    if stretch is not None:
        debug_logger.debug(
            f"Airway {airway}: {missing_fix} not on it, taken to its border point ({route_text})"
        )
        return stretch
    debug_logger.debug(
        f"Airway {airway} skipped: {missing_fix} not on it ({route_text})"
    )
    return SkippedAirway(airway=airway, missing_fix=missing_fix)


def get_airway_fixes(airway: str, entry_fix: str, exit_fix: str) -> list[AirwayFix]:
    """Get the fixes along an airway between a route's entry and exit fixes.

    Uses the airway type (federal, Alaska or Hawaii) whose points hold both
    fixes. When one fix is beyond a border point of the airway, the fixes run
    from the other fix to that border. Otherwise, when either fix is not on the
    airway, nothing is expanded, so the route runs from the entry fix to the
    exit fix directly. Border points have no position and are never returned.

    Args:
        airway: Airway designator (e.g., "V27", "J1")
        entry_fix: Fix identifier where the route joins the airway
        exit_fix: Fix identifier where the route leaves the airway

    Returns:
        The AirwayFix objects in the order the route flies them, or an empty
        list when the airway's stretch is unknown
    """
    stretch = _find_airway_stretch(airway, entry_fix, exit_fix)
    if not isinstance(stretch, _AirwayStretch):
        return []
    flown = stretch.points[stretch.first : stretch.last + 1]
    if stretch.entry_index > stretch.exit_index:
        flown.reverse()
    return [point for point in flown if isinstance(point, AirwayFix)]


def get_waypoint_coordinates(identifier: str) -> tuple[float, float] | None:
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


def _parse_coordinate_fix(identifier: str) -> tuple[float, float] | None:
    """Parse a coordinate-based fix like "3530N/11500W" or "35N115W".

    Args:
        identifier: Coordinate string

    Returns:
        Tuple of (latitude, longitude) or None if not parseable
    """
    # Pattern 1: DDMMN/DDDMMW (e.g., "3530N/11500W")
    match = re.match(r"(\d{2})(\d{2})([NS])/(\d{3})(\d{2})([EW])", identifier)
    if match:
        lat = int(match.group(1)) + int(match.group(2)) / 60
        if match.group(3) == "S":
            lat = -lat
        lon = int(match.group(4)) + int(match.group(5)) / 60
        if match.group(6) == "W":
            lon = -lon
        return (lat, lon)

    # Pattern 2: DDN/DDDW (e.g., "35N/115W")
    match = re.match(r"(\d{2})([NS])/(\d{2,3})([EW])", identifier)
    if match:
        lat = float(match.group(1))
        if match.group(2) == "S":
            lat = -lat
        lon = float(match.group(3))
        if match.group(4) == "W":
            lon = -lon
        return (lat, lon)

    return None


def _get_fix_identifier(
    part: str, airports: dict[str, tuple[float, float]]
) -> str | None:
    """Try to resolve a route part to a known fix/navaid identifier.

    Returns the identifier if found, None otherwise.
    """
    part = part.upper()

    # Check airports
    if part in airports:
        return part
    if len(part) == 4 and part.startswith("K") and part[1:] in airports:
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


def _expand_sid_star(
    part: str,
    position: int,
    total_parts: int,
    departure: str | None,
    arrival: str | None,
    waypoints: list[Waypoint],
    seen_identifiers: set,
) -> None:
    """Try to expand a SID/STAR name into waypoints via CIFP lookup.

    Modifies waypoints and seen_identifiers in-place.
    If at the start of route, tries as SID using departure airport.
    If at the end of route, tries as STAR using arrival airport.
    Falls back to trying both if position is ambiguous.
    """
    try:
        from backend.data.cifp import get_dp_data, get_star_data
    except ImportError:
        return

    procedure_waypoints: list[str] = []

    # Near start of route -> likely a SID
    if position <= 1 and departure:
        dp = get_dp_data(departure, part)
        if dp:
            procedure_waypoints = dp.waypoints

    # Near end of route -> likely a STAR
    if not procedure_waypoints and position >= total_parts - 2 and arrival:
        star = get_star_data(arrival, part)
        if star:
            procedure_waypoints = star.waypoints

    # If position is ambiguous, try both
    if not procedure_waypoints:
        if departure:
            dp = get_dp_data(departure, part)
            if dp:
                procedure_waypoints = dp.waypoints
        if not procedure_waypoints and arrival:
            star = get_star_data(arrival, part)
            if star:
                procedure_waypoints = star.waypoints

    # Resolve each procedure waypoint to coordinates and add
    for wp_name in procedure_waypoints:
        if wp_name in seen_identifiers:
            continue
        coords = get_waypoint_coordinates(wp_name)
        if coords:
            waypoints.append(
                Waypoint(
                    identifier=wp_name,
                    latitude=coords[0],
                    longitude=coords[1],
                    waypoint_type="procedure_fix",
                )
            )
            seen_identifiers.add(wp_name)


def parse_route_string(
    route: str,
    airports: dict[str, tuple[float, float]] | None = None,
    departure: str | None = None,
    arrival: str | None = None,
) -> list[Waypoint]:
    """Parse a filed route string into waypoints with coordinates.

    Expands airways (V, J, T, Q routes) into their constituent fixes.
    Expands SID/STAR procedure names into their common route waypoints
    when departure/arrival airport context is provided.

    Args:
        route: Filed route string (e.g., "SUNOL V27 BSR")
        airports: Optional dict mapping ICAO codes to (lat, lon) tuples
        departure: Optional departure airport ICAO code (for SID expansion)
        arrival: Optional arrival airport ICAO code (for STAR expansion)

    Returns:
        List of Waypoint objects with coordinates (unknown waypoints omitted)
    """
    if not route:
        return []

    airports = airports or {}
    waypoints: list[Waypoint] = []
    seen_identifiers: set = set()  # Avoid duplicates

    # Split route on whitespace
    parts = route.upper().split()

    i = 0
    while i < len(parts):
        part = parts[i]

        # Strip speed/level groups from fix names ("OVSUN/N0491F320" -> "OVSUN")
        # and skip standalone ones ("N0491F320" -> ""); coordinate fixes like
        # "3530N/11500W" stay whole
        part, _ = _split_route_token(part)
        if not part:
            i += 1
            continue

        # Check if this is an airway (V##, J##, T##, Q##)
        if re.match(r"^[VJTQ]\d+$", part):
            # Find entry fix (previous waypoint) and exit fix (next non-airway part)
            entry_fix = waypoints[-1].identifier if waypoints else None

            # Look ahead to find exit fix
            exit_fix = None
            j = i + 1
            while j < len(parts):
                # Strip speed/level groups and skip standalone ones here too
                next_part, _ = _split_route_token(parts[j])
                if not next_part:
                    j += 1
                    continue
                # Skip consecutive airways
                if re.match(r"^[VJTQ]\d+$", next_part):
                    j += 1
                    continue
                # Skip DCT
                if next_part == "DCT":
                    j += 1
                    continue
                # Skip SID/STAR names
                if re.match(r"^[A-Z]+\d+[A-Z]*$", next_part) and len(next_part) > 5:
                    j += 1
                    continue
                # Found a potential exit fix
                exit_fix = _get_fix_identifier(next_part, airports)
                break

            # Get airway fixes between entry and exit
            if entry_fix and exit_fix:
                airway_fixes = get_airway_fixes(part, entry_fix, exit_fix)

                # Add intermediate fixes (skip entry since it's already added)
                for awy_fix in airway_fixes:
                    if awy_fix.identifier not in seen_identifiers:
                        # Skip if this is the entry fix (already in waypoints)
                        if entry_fix and awy_fix.identifier == entry_fix:
                            continue
                        waypoints.append(
                            Waypoint(
                                identifier=awy_fix.identifier,
                                latitude=awy_fix.latitude,
                                longitude=awy_fix.longitude,
                                waypoint_type="airway_fix",
                            )
                        )
                        seen_identifiers.add(awy_fix.identifier)

            i += 1
            continue

        # Expand SID/STAR names via CIFP if airport context is available
        if re.match(r"^[A-Z]+\d+[A-Z]*$", part) and len(part) > 5:
            _expand_sid_star(
                part,
                i,
                len(parts),
                departure,
                arrival,
                waypoints,
                seen_identifiers,
            )
            i += 1
            continue

        # Skip DCT (direct)
        if part == "DCT":
            i += 1
            continue

        # Try to get coordinates for this waypoint
        coords = None
        waypoint_type = ""

        # Check airports first
        if part in airports:
            coords = airports[part]
            waypoint_type = "airport"
        elif len(part) == 4 and part.startswith("K"):
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
                waypoint_type = "navaid" if part in navaids else "fix"

        if coords and part not in seen_identifiers:
            waypoints.append(
                Waypoint(
                    identifier=part,
                    latitude=coords[0],
                    longitude=coords[1],
                    waypoint_type=waypoint_type,
                )
            )
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


@dataclass(frozen=True)
class MeaViolation:
    """The MEA of one airway segment a route flies."""

    airway: str  # e.g., "V23"
    segment_start: str  # Fix identifier of the point the segment leaves
    segment_end: str  # Fix identifier of the next point along the airway
    mea: int  # Required MEA in feet
    # Level in feet in force where the route joins the airway, from the last
    # FIX/<speed><level> group at or before the entry fix; None when there is none
    level_ft: int | None


@dataclass(frozen=True)
class MeaCheck:
    """The MEA check of a route: the segments it reads and the airways it skips."""

    max_mea: int | None  # Highest MEA across the checked segments, None if none
    violations: list[MeaViolation]  # Every checked segment with an MEA
    skipped: list[SkippedAirway]  # Airways with only one end of the stretch on them


_AIRWAY_RE = re.compile(r"^[VJTQ]\d+$")
_PROCEDURE_RE = re.compile(r"^[A-Z]+\d+[A-Z]*$")
# An Item 15 speed (N or K and four digits, M and three) followed by a level
# (F or A and three digits in hundreds of feet, S or M and four in tens of metres)
_SPEED_LEVEL_RE = re.compile(r"^[NKM]\d{3,4}(?P<level>[FA]\d{3}|[SM]\d{4})$")
_FEET_PER_METRE = 3.28084


def _level_feet(level: str) -> int:
    """Feet for an Item 15 level such as "F400", "A045", "S1130" or "M0840"."""
    value = int(level[1:])
    if level[0] in "FA":
        return value * 100
    return round(value * 10 * _FEET_PER_METRE)


def _split_route_token(token: str) -> tuple[str, int | None]:
    """Split a route token into its fix and the level its speed/level group sets.

    "MLF/N0490F400" gives ("MLF", 40000). A standalone group such as
    "N0490F400" gives ("", None), so it is never taken for a fix. Any other
    token, a coordinate fix such as "3530N/11500W" included, comes back whole
    with no level.
    """
    if _SPEED_LEVEL_RE.match(token):
        return "", None
    fix, _, annotation = token.partition("/")
    match = _SPEED_LEVEL_RE.match(annotation)
    if match is None:
        return token, None
    return fix, _level_feet(match.group("level"))


def _is_route_endpoint(token: str) -> bool:
    """Whether a route token can be an airway's entry or exit fix.

    Empty tokens (speed/level groups), DCT, airways and SID/STAR names cannot.
    """
    is_procedure = bool(_PROCEDURE_RE.match(token)) and len(token) > 5
    return (
        bool(token)
        and token != "DCT"
        and not _AIRWAY_RE.match(token)
        and not is_procedure
    )


def _find_endpoint(fixes: list[str], indices: range) -> int | None:
    """The first index in indices whose route token can be an airway endpoint."""
    return next((index for index in indices if _is_route_endpoint(fixes[index])), None)


def _level_at(levels: list[int | None], index: int) -> int | None:
    """The level set by the last speed/level group at or before a route position."""
    return next(
        (level for level in reversed(levels[: index + 1]) if level is not None), None
    )


def _stretch_segments(
    stretch: _AirwayStretch,
    restrictions: dict[tuple[str, str], dict[int, AirwaySegmentRestriction]],
    level_ft: int | None,
) -> list[MeaViolation]:
    """The segments of a stretch that have an MEA, each from the AWY1 record of the point it leaves."""
    segment_restrictions = restrictions.get((stretch.airway, stretch.airway_type), {})
    segments: list[MeaViolation] = []
    for index in range(stretch.first, stretch.last):
        start = stretch.points[index]
        restriction = segment_restrictions.get(start.sequence)
        if restriction is None or restriction.mea is None:
            continue
        segments.append(
            MeaViolation(
                airway=stretch.airway,
                segment_start=start.identifier,
                segment_end=stretch.points[index + 1].identifier,
                mea=restriction.mea,
                level_ft=level_ft,
            )
        )
    return segments


def get_max_mea_for_route(route: str) -> MeaCheck:
    """Get the MEAs of the airway segments a route flies.

    For each airway in the route, the entry and exit are the nearest fixes
    before and after it, with any speed/level group stripped. The airway type
    (federal, Alaska or Hawaii) whose points hold both is walked from one to
    the other, and each segment's MEA comes from the AWY1 record of the point
    the segment leaves. Each segment carries the level in force at the
    airway's entry, from the last FIX/<speed><level> group at or before the
    entry fix (Item 15). An airway with only one end on it is skipped and
    reported; one with neither end on it, or with no fix before or after it in
    the route, is skipped and only logged.

    Args:
        route: Filed route string (e.g., "KSFO V25 SAC J80 RNO KRNO")

    Returns:
        The MeaCheck of the route: the highest MEA (None when no segment has
        one), every segment with an MEA, and the airways skipped with one end
        not on them
    """
    restrictions = load_airway_restrictions()
    if not route or not restrictions:
        return MeaCheck(max_mea=None, violations=[], skipped=[])

    tokens = [_split_route_token(token) for token in route.upper().split()]
    fixes = [fix for fix, _ in tokens]
    levels = [level for _, level in tokens]
    violations: list[MeaViolation] = []
    skipped: list[SkippedAirway] = []

    for index, airway in enumerate(fixes):
        if not _AIRWAY_RE.match(airway):
            continue
        entry = _find_endpoint(fixes, range(index - 1, -1, -1))
        exit_ = _find_endpoint(fixes, range(index + 1, len(fixes)))
        if entry is None or exit_ is None:
            side = "before" if entry is None else "after"
            debug_logger.debug(f"MEA check skipped {airway}: no fix {side} it")
            continue
        stretch = _find_airway_stretch(airway, fixes[entry], fixes[exit_])
        if isinstance(stretch, _AirwayStretch):
            level_ft = _level_at(levels, entry)
            violations.extend(_stretch_segments(stretch, restrictions, level_ft))
        elif stretch is not None:
            skipped.append(stretch)

    # An airway listed twice in the route yields the same segments twice
    violations = list(dict.fromkeys(violations))
    return MeaCheck(
        max_mea=max((v.mea for v in violations), default=None),
        violations=violations,
        skipped=list(dict.fromkeys(skipped)),
    )
