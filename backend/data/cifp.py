"""CIFP (Coded Instrument Flight Procedures) downloader and parser.

This module downloads and parses FAA CIFP data in ARINC 424 format to extract
approach procedure data for diversion recommendations. CIFP provides authoritative,
structured data for instrument procedures.

CIFP data is downloaded once per AIRAC cycle (28 days) and cached locally.
"""

import io
import re
import urllib.request
import urllib.error
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional

from common.paths import get_cifp_cache_dir


# AIRAC epoch: Cycle 2501 effective date
# All AIRAC cycles can be calculated from this reference point
AIRAC_EPOCH = date(2025, 1, 23)
CYCLE_DAYS = 28

# CIFP download settings
CIFP_BASE_URL = "https://aeronav.faa.gov/Upload_313-d/cifp/"
CIFP_TIMEOUT = 60  # seconds for download

# Cache directory (uses user data directory)
CIFP_CACHE_DIR = get_cifp_cache_dir()


# --- Data Classes ---


@dataclass
class CifpApproachFix:
    """A fix/waypoint in an approach procedure."""

    approach_id: str  # e.g., "H17LZ" -> RNAV (GPS) Z RWY 17L
    transition: str  # e.g., "LIBGE" or "" for main route
    fix_identifier: str  # e.g., "LIBGE", "KLOCK", "FMG"
    fix_type: str  # "IAF", "IF", "FAF", or ""
    sequence: int  # Order in procedure
    path_terminator: str = ""  # e.g., "IF", "TF", "RF"


@dataclass
class CifpApproach:
    """An approach procedure with all its fixes."""

    airport: str
    approach_id: str  # e.g., "H17LZ"
    approach_type: str  # e.g., "RNAV (GPS)", "ILS", "LOC"
    runway: Optional[str]  # e.g., "17L", "35R"
    fixes: list[CifpApproachFix] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        """Get human-readable approach name.

        Returns:
            e.g., "ILS RWY 28R", "RNAV (GPS) Z RWY 17L"
        """
        variant = ""
        # Check for variant letter at end of approach_id
        if self.approach_id and len(self.approach_id) > 1:
            last_char = self.approach_id[-1]
            if last_char in "WXYZ":
                variant = f" {last_char}"

        if self.runway:
            return f"{self.approach_type}{variant} RWY {self.runway}"
        return f"{self.approach_type}{variant}"

    @property
    def iaf_fixes(self) -> list[str]:
        """Get all IAF (Initial Approach Fix) identifiers."""
        return [f.fix_identifier for f in self.fixes if f.fix_type == "IAF"]

    @property
    def if_fixes(self) -> list[str]:
        """Get all IF (Intermediate Fix) identifiers."""
        return [f.fix_identifier for f in self.fixes if f.fix_type == "IF"]


# --- AIRAC Cycle Calculation ---


def get_current_airac_cycle() -> tuple[str, date, date]:
    """Calculate current AIRAC cycle and its date boundaries.

    AIRAC cycles follow a predictable 28-day schedule. This function
    calculates the current cycle ID and its exact start/end dates
    from a known epoch (cycle 2501 = January 23, 2025).

    Returns:
        Tuple of (cycle_id, start_date, end_date)
        Example: ("2512", date(2025, 11, 27), date(2025, 12, 24))
    """
    today = date.today()
    days_since_epoch = (today - AIRAC_EPOCH).days
    cycle_number = days_since_epoch // CYCLE_DAYS  # 0-indexed from 2501

    # Calculate year and cycle within year
    # Note: There are 13 cycles per year (28 * 13 = 364 days)
    year = 2025 + (cycle_number // 13)
    cycle_in_year = (cycle_number % 13) + 1
    cycle_id = f"{year % 100:02d}{cycle_in_year:02d}"

    start_date = AIRAC_EPOCH + timedelta(days=cycle_number * CYCLE_DAYS)
    end_date = start_date + timedelta(days=CYCLE_DAYS - 1)

    return cycle_id, start_date, end_date


def _get_effective_date_for_cycle(cycle_id: str) -> str:
    """Convert AIRAC cycle ID to YYMMDD effective date format.

    FAA CIFP files are named like CIFP_251127.zip for cycle 2512 (Nov 27, 2025).

    Args:
        cycle_id: AIRAC cycle ID (e.g., "2512")

    Returns:
        Date string in YYMMDD format (e.g., "251127")
    """
    # Parse cycle ID
    year = 2000 + int(cycle_id[:2])
    cycle_in_year = int(cycle_id[2:])

    # Calculate days since epoch
    # Cycle 2501 = epoch, so subtract 1 from cycle_in_year
    cycles_since_2501 = (year - 2025) * 13 + (cycle_in_year - 1)
    effective_date = AIRAC_EPOCH + timedelta(days=cycles_since_2501 * CYCLE_DAYS)

    return effective_date.strftime("%y%m%d")


# --- CIFP Download and Management ---


def get_cifp_url() -> str:
    """Get the CIFP download URL for the current AIRAC cycle.

    Returns:
        URL to the CIFP zip file
    """
    cycle_id, _, _ = get_current_airac_cycle()
    date_str = _get_effective_date_for_cycle(cycle_id)
    return f"{CIFP_BASE_URL}CIFP_{date_str}.zip"


def get_cifp_cache_path() -> Path:
    """Get the cache path for CIFP data.

    Returns:
        Path to the cached CIFP text file
    """
    cycle_id, _, _ = get_current_airac_cycle()
    return CIFP_CACHE_DIR / f"FAACIFP18-{cycle_id}"


def ensure_cifp_data(quiet: bool = False) -> Optional[Path]:
    """Download CIFP data if missing or outdated.

    Auto-downloads new CIFP data when a new AIRAC cycle begins.

    Args:
        quiet: If True, suppress print output

    Returns:
        Path to the CIFP data file, or None if download failed
    """
    cached_path = get_cifp_cache_path()

    if cached_path.exists():
        return cached_path

    # Download new CIFP
    url = get_cifp_url()
    if not quiet:
        print(f"Downloading CIFP data from {url}...")

    try:
        with urllib.request.urlopen(url, timeout=CIFP_TIMEOUT) as response:
            zip_data = response.read()
    except (urllib.error.URLError, TimeoutError) as e:
        if not quiet:
            print(f"Failed to download CIFP: {e}")
        return None

    # Extract the FAACIFP18 file from the zip
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            # Find the main CIFP file
            cifp_filename = None
            for name in zf.namelist():
                if name.startswith("FAACIFP"):
                    cifp_filename = name
                    break

            if not cifp_filename:
                if not quiet:
                    print("FAACIFP file not found in zip")
                return None

            # Extract to cache
            cached_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(cifp_filename) as src, open(cached_path, "wb") as dst:
                dst.write(src.read())

            if not quiet:
                print(f"CIFP data cached to {cached_path}")
            return cached_path

    except zipfile.BadZipFile as e:
        if not quiet:
            print(f"Invalid zip file: {e}")
        return None


def _cycle_to_absolute(cycle_id: str) -> int:
    """Convert AIRAC cycle ID (YYCC) to absolute cycle number.

    This handles year boundary correctly: 2513 -> 2601 is 1 cycle apart.

    Args:
        cycle_id: AIRAC cycle ID like "2513" or "2601"

    Returns:
        Absolute cycle number (cycles since year 2000)
    """
    year = int(cycle_id[:2])
    cycle_in_year = int(cycle_id[2:])
    return year * 13 + cycle_in_year


def cleanup_old_airac_caches(keep_cycles: int = 2) -> int:
    """Remove cache files for old AIRAC cycles.

    Keeps the most recent cycles and removes older ones to prevent
    cache bloat.

    Args:
        keep_cycles: Number of recent cycles to keep (default: 2)

    Returns:
        Number of files removed
    """
    current_cycle, _, _ = get_current_airac_cycle()
    current_absolute = _cycle_to_absolute(current_cycle)
    removed = 0

    if not CIFP_CACHE_DIR.exists():
        return 0

    for cifp_file in CIFP_CACHE_DIR.iterdir():
        if not cifp_file.is_file():
            continue

        # CIFP files are named like "FAACIFP18-2512"
        match = re.match(r"^FAACIFP\d+-(\d{4})$", cifp_file.name)
        if not match:
            continue

        try:
            file_cycle = match.group(1)
            file_absolute = _cycle_to_absolute(file_cycle)

            # Compare absolute cycle numbers (handles year boundary correctly)
            if current_absolute - file_absolute > keep_cycles:
                cifp_file.unlink()
                removed += 1
        except (ValueError, OSError):
            continue

    return removed


# --- ARINC 424 Record Parsing ---


# Waypoint Description Code 1 (position 43, 1-indexed) meanings
WAYPOINT_DESC_CODES = {
    "A": "IAF",  # Initial Approach Fix
    "B": "IF",  # Intermediate Fix
    "C": "IAF",  # IAF and IF combined (treat as IAF)
    "D": "IAF",  # IAF and FAF combined (treat as IAF)
    "E": "FAF",  # Final Approach Course Fix / FAF
    "F": "FAF",  # Final Approach Fix
    "G": "MAHP",  # Missed Approach Point
    "I": "IF",  # Initial Fix (IF in path terminator context)
    "M": "MAHP",  # Missed Approach Holding Fix
}

# Approach type codes (first character of approach ID)
APPROACH_TYPE_CODES = {
    "B": "LOC/DME BC",
    "D": "VOR/DME",
    "F": "FMS",
    "G": "IGS",
    "H": "RNAV (GPS)",  # Could be RNAV (RNP)
    "I": "ILS",
    "J": "GNSS",
    "L": "LOC",
    "N": "NDB",
    "P": "GPS",
    "Q": "NDB/DME",
    "R": "RNAV",
    "S": "VOR",  # VOR with DME required
    "T": "TACAN",
    "U": "SDF",
    "V": "VOR",
    "W": "MLS",
    "X": "LDA",
    "Y": "MLS",  # Type A/B/C
    "Z": "MLS",  # Type B/C
}


def _parse_runway_from_approach_id(approach_id: str) -> Optional[str]:
    """Extract runway from approach ID.

    Args:
        approach_id: e.g., "H17LZ", "I35L", "V07"

    Returns:
        Runway string (e.g., "17L", "35L", "07") or None
    """
    # Skip first character (approach type)
    rest = approach_id[1:]

    # Match runway pattern: 1-2 digits optionally followed by L/R/C
    # The last character might be a variant letter (X, Y, Z, W)
    match = re.match(r"(\d{1,2}[LRC]?)", rest)
    if match:
        return match.group(1)
    return None


def _parse_approach_type(approach_id: str) -> str:
    """Get approach type from approach ID.

    Args:
        approach_id: e.g., "H17LZ"

    Returns:
        Approach type string (e.g., "RNAV (GPS)")
    """
    if not approach_id:
        return "UNKNOWN"

    type_code = approach_id[0]
    return APPROACH_TYPE_CODES.get(type_code, "UNKNOWN")


def parse_approach_record(line: str) -> Optional[CifpApproachFix]:
    """Parse a single CIFP approach procedure record.

    ARINC 424 approach records have fixed column positions:
    - Position 7-10: Airport ICAO
    - Position 13: Subsection (F = Approach)
    - Position 14-19: Approach ID
    - Position 20: Route type (A = transition, H/I/L etc = main)
    - Position 21-25: Transition identifier
    - Position 27-29: Sequence number
    - Position 30-34: Fix identifier
    - Position 43: Waypoint Description Code 1
    - Position 48-49: Path terminator

    Args:
        line: Raw CIFP record line

    Returns:
        CifpApproachFix if valid approach record, None otherwise
    """
    if len(line) < 50:
        return None

    # Check record type and subsection
    if not line.startswith("SUSAP"):
        return None

    # Position 13 (0-indexed: 12) = Subsection
    if len(line) < 13 or line[12] != "F":
        return None

    # Extract fields (1-indexed positions converted to 0-indexed)
    approach_id = line[13:19].strip()  # Position 14-19
    route_type = line[19] if len(line) > 19 else ""  # Position 20
    transition = line[20:25].strip()  # Position 21-25
    sequence_str = line[26:29].strip()  # Position 27-29
    fix_identifier = line[29:34].strip()  # Position 30-34

    # Waypoint description code (position 43, 0-indexed: 42)
    waypoint_desc = line[42] if len(line) > 42 else " "

    # Path terminator (positions 48-49, 0-indexed: 47-48)
    path_terminator = line[47:49].strip() if len(line) > 48 else ""

    # Parse sequence number
    try:
        sequence = int(sequence_str)
    except ValueError:
        sequence = 0

    # Determine fix type from waypoint description code
    fix_type = WAYPOINT_DESC_CODES.get(waypoint_desc, "")

    # Skip if no fix identifier
    if not fix_identifier:
        return None

    # For transition records (route_type == 'A'), use transition name
    # For main route records, transition is empty
    if route_type != "A":
        transition = ""

    return CifpApproachFix(
        approach_id=approach_id,
        transition=transition,
        fix_identifier=fix_identifier,
        fix_type=fix_type,
        sequence=sequence,
        path_terminator=path_terminator,
    )


# --- High-Level API ---


@lru_cache(maxsize=100)
def get_approaches_for_airport(airport: str) -> dict[str, CifpApproach]:
    """Get all approach procedures for an airport.

    Args:
        airport: Airport code (e.g., "RNO", "KRNO", "KSFO")

    Returns:
        Dict mapping approach_id to CifpApproach objects
    """
    cifp_path = ensure_cifp_data(quiet=True)
    if not cifp_path:
        return {}

    # Normalize airport code - handle both "KSFO" and "SFO"
    airport = airport.upper()
    if airport.startswith("K") and len(airport) == 4:
        airport_code = airport[1:]  # Strip K for CONUS airports
    else:
        airport_code = airport

    search_prefix = f"SUSAP K{airport_code}"

    approaches: dict[str, CifpApproach] = {}

    try:
        with open(cifp_path, "r", encoding="latin-1") as f:
            for line in f:
                if not line.startswith(search_prefix):
                    continue

                fix = parse_approach_record(line)
                if not fix:
                    continue

                # Create approach if not exists
                if fix.approach_id not in approaches:
                    approaches[fix.approach_id] = CifpApproach(
                        airport=airport_code,
                        approach_id=fix.approach_id,
                        approach_type=_parse_approach_type(fix.approach_id),
                        runway=_parse_runway_from_approach_id(fix.approach_id),
                    )

                approaches[fix.approach_id].fixes.append(fix)
    except (OSError, IOError):
        return {}

    return approaches


def get_approach_list_for_airport(airport: str) -> list[str]:
    """Get a simple list of approach names for an airport.

    This is a convenience function that returns just the display names
    suitable for showing in the diversion modal.

    Args:
        airport: Airport ICAO code (e.g., "KSFO")

    Returns:
        List of approach display names, e.g., ["ILS RWY 28R", "RNAV (GPS) Z RWY 28L"]
    """
    approaches = get_approaches_for_airport(airport)
    return sorted(set(approach.display_name for approach in approaches.values()))


def has_instrument_approaches(airport: str) -> bool:
    """Check if an airport has any instrument approaches.

    Args:
        airport: Airport ICAO code

    Returns:
        True if the airport has at least one approach in CIFP data
    """
    approaches = get_approaches_for_airport(airport)
    return len(approaches) > 0


def clear_approach_cache() -> None:
    """Clear the LRU cache for approach lookups.

    Useful when CIFP data is updated.
    """
    get_approaches_for_airport.cache_clear()
