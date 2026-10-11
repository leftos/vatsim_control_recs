"""CIFP (Coded Instrument Flight Procedures) downloader and parser.

This module downloads and parses FAA CIFP data in ARINC 424 format to extract
approach procedure data for diversion recommendations. CIFP provides authoritative,
structured data for instrument procedures.

CIFP data is downloaded once per AIRAC cycle (28 days) and cached locally.
"""

import io
import re
import threading
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from common import logger as debug_logger
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
    runway: str | None  # e.g., "17L", "35R"
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


@dataclass
class CifpDP:
    """A departure procedure (SID) with waypoints."""

    identifier: str  # e.g., "CNDEL5"
    waypoints: list[str]  # Common route fixes, or the tail runway transitions share


@dataclass
class CifpSTAR:
    """A STAR (Standard Terminal Arrival Route) with waypoints."""

    identifier: str  # e.g., "SCOLA1"
    waypoints: list[str]  # All waypoints in the common route


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
    # AIRAC cycles change on a UTC boundary, not the machine's local one
    today = datetime.now(UTC).date()
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


def ensure_cifp_data(quiet: bool = False) -> Path | None:
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


def _parse_runway_from_approach_id(approach_id: str) -> str | None:
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


def parse_approach_record(line: str) -> CifpApproachFix | None:
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

    # Check record type and subsection. The index reads the areas below, and
    # its own predicate keeps out the Canadian and non-K records they carry.
    if not line.startswith(_RECORD_AREA_PREFIXES):
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


def get_approaches_for_airport(airport: str) -> dict[str, CifpApproach]:
    """Get all approach procedures for an airport.

    Args:
        airport: Airport code (e.g., "RNO", "KRNO", "KSFO")

    Returns:
        Dict mapping approach_id to CifpApproach objects
    """
    if not _ensure_index():
        return {}

    approach_index = _approach_index
    airport_code = _airport_code(airport)

    approaches: dict[str, CifpApproach] = {}

    for fixes in _iter_airport_records(approach_index, _lookup_ident(airport_code)):
        for fix in fixes:
            # Create approach if not exists
            if fix.approach_id not in approaches:
                approaches[fix.approach_id] = CifpApproach(
                    airport=airport_code,
                    approach_id=fix.approach_id,
                    approach_type=_parse_approach_type(fix.approach_id),
                    runway=_parse_runway_from_approach_id(fix.approach_id),
                )

            approaches[fix.approach_id].fixes.append(fix)

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
    return sorted({approach.display_name for approach in approaches.values()})


def has_instrument_approaches(airport: str) -> bool:
    """Check if an airport has any instrument approaches.

    Args:
        airport: Airport ICAO code

    Returns:
        True if the airport has at least one approach in CIFP data
    """
    approaches = get_approaches_for_airport(airport)
    return len(approaches) > 0


def parse_dp_record(line: str) -> tuple[str, str, str, int] | None:
    """Parse a single CIFP DP (departure procedure) record.

    ARINC 424 DP records (subsection D):
    - Position 14-19: DP identifier (e.g., "CNDEL5")
    - Position 21-25: Transition name ("ALL"/blank = common route)
    - Position 27-29: Sequence number
    - Position 30-34: Fix identifier

    Returns:
        Tuple of (dp_id, transition, fix_identifier, sequence) or None
    """
    if len(line) < 35:
        return None

    if not line.startswith(_RECORD_AREA_PREFIXES):
        return None

    if len(line) < 13 or line[12] != "D":
        return None

    dp_id = line[13:19].strip()
    transition = line[20:25].strip()
    sequence_str = line[26:29].strip()
    fix_identifier = line[29:34].strip()

    try:
        sequence = int(sequence_str)
    except ValueError:
        sequence = 0

    if not fix_identifier or not dp_id:
        return None

    return (dp_id, transition, fix_identifier, sequence)


def parse_star_record(line: str) -> tuple[str, str, str, int] | None:
    """Parse a single CIFP STAR record.

    ARINC 424 STAR records (subsection E):
    - Position 14-19: STAR identifier (e.g., "SCOLA1")
    - Position 21-25: Transition name ("ALL"/blank = common route)
    - Position 27-29: Sequence number
    - Position 30-34: Fix identifier

    Returns:
        Tuple of (star_id, transition, fix_identifier, sequence) or None
    """
    if len(line) < 35:
        return None

    if not line.startswith(_RECORD_AREA_PREFIXES):
        return None

    if len(line) < 13 or line[12] != "E":
        return None

    star_id = line[13:19].strip()
    transition = line[20:25].strip()
    sequence_str = line[26:29].strip()
    fix_identifier = line[29:34].strip()

    try:
        sequence = int(sequence_str)
    except ValueError:
        sequence = 0

    if not fix_identifier or not star_id:
        return None

    return (star_id, transition, fix_identifier, sequence)


# --- Record Index ---

# One pass over the CIFP file indexes its SID, STAR and approach records by
# airport ident, so a lookup answers without rescanning the whole file. Each
# per-airport list stays in file order, and the index is rebuilt when the AIRAC
# cycle hands ensure_cifp_data a different file to read.
_cifp_index_lock = threading.Lock()
_cifp_index_path: Path | None = None
_dp_index: dict[str, list[tuple[str, str, str, int]]] = {}
_star_index: dict[str, list[tuple[str, str, str, int]]] = {}
_approach_index: dict[str, list[CifpApproachFix]] = {}


def _airport_code(airport: str) -> str:
    """Normalize an airport code the way the lookups always did.

    Args:
        airport: Airport code (e.g., "OAK", "KOAK")

    Returns:
        The code with the leading K of a 4-character CONUS ICAO stripped
    """
    airport = airport.upper()
    if airport.startswith("K") and len(airport) == 4:
        return airport[1:]
    return airport


def _lookup_ident(airport_code: str) -> str:
    """The index ident a normalized airport code is looked up under.

    An Alaska, Hawaii, Puerto Rico or Pacific-territory airport keeps its own
    four-character P/T ident ("PANC", "PHNL", "TJSJ"); every other code is a
    CONUS airport, whose records are keyed by the code with the K back on.

    Args:
        airport_code: Normalized code, as _airport_code returns it

    Returns:
        The 4-character ident the index is keyed by (e.g., "KOAK", "PANC")
    """
    if len(airport_code) == 4 and airport_code[0] in ("P", "T"):
        return airport_code
    return f"K{airport_code}"


def _iter_airport_records(index: dict, ident_prefix: str) -> Iterator[list]:
    """Yield the record lists of every airport whose ident matches a prefix.

    A 3-character code matches one airport; a shorter one matches every airport
    whose ident starts with it, as the old prefix scans did. Idents come back
    in the order they first appear in the file, which is the order the records
    of a prefix spanning several airports were scanned in.
    """
    for ident, records in index.items():
        if ident.startswith(ident_prefix):
            yield records


def _gather_records(
    index: dict[str, list[tuple[str, str, str, int]]],
    ident_prefix: str,
    name_prefix: str,
) -> dict[str, list[tuple[str, int]]]:
    """Collect a procedure's fixes by transition, in CIFP file order.

    Args:
        index: The SID or STAR index to read
        ident_prefix: The airport ident to match (e.g., "KOAK")
        name_prefix: Procedure id prefix a record's id must start with

    Returns:
        Dict mapping transition to its (fix, sequence) records
    """
    records: dict[str, list[tuple[str, int]]] = {}

    for entries in _iter_airport_records(index, ident_prefix):
        for proc_id, transition, fix_id, sequence in entries:
            if not proc_id.startswith(name_prefix):
                continue
            records.setdefault(transition, []).append((fix_id, sequence))

    return records


def _sequenced_fixes(records: list[tuple[str, int]]) -> list[str]:
    """A transition's fix identifiers in sequence order, duplicates removed.

    Args:
        records: The transition's (fix, sequence) records

    Returns:
        The non-empty fix identifiers, in sequence order
    """
    fixes: list[str] = []
    seen: set[str] = set()
    for fix, _ in sorted(records, key=lambda pair: pair[1]):
        if fix and fix not in seen:
            fixes.append(fix)
            seen.add(fix)
    return fixes


def _shared_runway_tail(
    dp_records: dict[str, list[tuple[str, int]]],
) -> list[str]:
    """The longest run of fixes every runway transition ends with.

    A SID with no common route and no enroute transition is coded as runway
    transitions only. The fixes those transitions share are flown off every
    runway, so they are the part of the SID known without the departure runway.

    Args:
        dp_records: A procedure's (fix, sequence) records by transition

    Returns:
        The shared tail's fixes in sequence order; empty when the procedure has
        no runway transition or the transitions end at different fixes
    """
    transitions = [
        _sequenced_fixes(records)
        for transition, records in dp_records.items()
        if transition.startswith("RW")
    ]
    if not transitions:
        return []

    shared_tail = transitions[0]
    for fixes in transitions[1:]:
        common = 0
        while (
            common < len(shared_tail)
            and common < len(fixes)
            and shared_tail[-1 - common] == fixes[-1 - common]
        ):
            common += 1
        shared_tail = shared_tail[len(shared_tail) - common :]
    return shared_tail


# The customer areas that carry the P/T-ident records the lookups answer from:
# SCANP is Alaska's, SPACP Hawaii's and the Pacific territories', SLAMP Puerto
# Rico's and the US Virgin Islands'. The Canadian records that share SCANP keep
# their C ident, and the SUSAP area keeps its non-K FAA LIDs; neither is
# indexed.
_P_T_AREA_PREFIXES = ("SCANP ", "SPACP ", "SLAMP ")
_RECORD_AREA_PREFIXES = ("SUSAP", *_P_T_AREA_PREFIXES)


def _indexed_record_ident(line: str) -> str | None:
    """The airport ident a CIFP record line is indexed under, or None.

    Indexed are the SUSAP records with a K ident, the CONUS airports, and the
    P/T-ident records of the areas that carry them: SCANP for Alaska, SPACP for
    Hawaii and the Pacific territories, SLAMP for Puerto Rico and the US Virgin
    Islands.

    Args:
        line: Raw CIFP record line

    Returns:
        The record's 4-character airport ident, or None when the line is not
        one the lookups answer from
    """
    if line.startswith("SUSAP K"):
        return line[6:10]
    if line.startswith(_P_T_AREA_PREFIXES) and len(line) > 6 and line[6] in ("P", "T"):
        return line[6:10]
    return None


def _build_cifp_index(path: Path) -> None:
    """Index the SID, STAR and approach records of a CIFP file by ident.

    Indexed are the SUSAP records with a K ident, the CONUS airports, and the
    records whose ident starts with P or T in the SCANP, SPACP and SLAMP areas:
    Alaska, Hawaii, Puerto Rico, the US Virgin Islands and the Pacific
    territories. The Canadian records that share SCANP, and the SUSAP records
    with a non-K ident, stay out.

    Args:
        path: Path to the CIFP file
    """
    dp_index: dict[str, list[tuple[str, str, str, int]]] = {}
    star_index: dict[str, list[tuple[str, str, str, int]]] = {}
    approach_index: dict[str, list[CifpApproachFix]] = {}

    with open(path, encoding="latin-1") as f:
        for line in f:
            ident = _indexed_record_ident(line)
            if ident is None:
                continue
            dp_record = parse_dp_record(line)
            if dp_record:
                dp_index.setdefault(ident, []).append(dp_record)
                continue
            star_record = parse_star_record(line)
            if star_record:
                star_index.setdefault(ident, []).append(star_record)
                continue
            fix = parse_approach_record(line)
            if fix:
                approach_index.setdefault(ident, []).append(fix)

    # Publish the new index at once, so a lookup never sees a partial one
    global _cifp_index_path, _dp_index, _star_index, _approach_index
    _cifp_index_path = path
    _dp_index = dp_index
    _star_index = star_index
    _approach_index = approach_index


def _clear_route_cache() -> None:
    """Drop the parsed-route cache, whose entries embedded CIFP lookups."""
    from backend.core.route_distance import clear_route_cache

    clear_route_cache()


def _load_cifp_index(path: Path) -> bool:
    """Index path when it is not the indexed file yet.

    Args:
        path: Path to the CIFP file

    Returns:
        True when the index for path is ready, False when the file cannot be
        read, which leaves the lookups returning an empty result as before
    """
    with _cifp_index_lock:
        if _cifp_index_path == path:
            return True
        try:
            _build_cifp_index(path)
        except OSError as e:
            debug_logger.warning(f"Cannot read CIFP data at {path}: {e}")
            return False
        _clear_route_cache()
        return True


def _ensure_index() -> bool:
    """Make the index match the file ensure_cifp_data provides.

    Returns:
        True when a lookup can answer from the index, False when there is no
        CIFP file to read
    """
    cifp_path = ensure_cifp_data(quiet=True)
    if cifp_path is None:
        return False
    return _load_cifp_index(cifp_path)


def get_dp_data(airport: str, dp_name: str) -> CifpDP | None:
    """Get DP (departure procedure) waypoints from CIFP.

    The waypoints are the common route's when the procedure carries one, else
    the first enroute transition's, else the tail every runway transition
    shares.

    Args:
        airport: Airport code (e.g., "OAK", "KOAK")
        dp_name: DP name (e.g., "CNDEL5")

    Returns:
        CifpDP with the procedure's waypoints, or None if not found
    """
    if not _ensure_index():
        return None

    dp_index = _dp_index
    airport_code = _airport_code(airport)

    # A CIFP record can carry a variant digit past its 6-character procedure
    # id, so the filed name is matched against a record's id by prefix
    dp_id_prefix = dp_name.upper().strip()

    dp_records = _gather_records(dp_index, _lookup_ident(airport_code), dp_id_prefix)
    if not dp_records:
        return None

    # Extract waypoints from procedure
    all_waypoints: list[str] = []
    seen_waypoints: set[str] = set()

    # Try common route first: transition is "ALL" or ""
    common_route_key = (
        "ALL" if "ALL" in dp_records else "" if "" in dp_records else None
    )
    if common_route_key is not None:
        sorted_fixes = sorted(dp_records[common_route_key], key=lambda x: x[1])
        for fix, _ in sorted_fixes:
            if fix and fix not in seen_waypoints:
                all_waypoints.append(fix)
                seen_waypoints.add(fix)

    # If no common route, use the first enroute transition's waypoints
    # (enroute transitions = not "RW*", not "ALL", not "")
    if not all_waypoints:
        enroute_transitions = sorted(
            t for t in dp_records if t and t != "ALL" and not t.startswith("RW")
        )
        if enroute_transitions:
            first_trans = enroute_transitions[0]
            sorted_fixes = sorted(dp_records[first_trans], key=lambda x: x[1])
            for fix, _ in sorted_fixes:
                if fix and fix not in seen_waypoints:
                    all_waypoints.append(fix)
                    seen_waypoints.add(fix)

    # A procedure coded with runway transitions only has no common route: every
    # aircraft flies the fixes all its runway transitions share
    if not all_waypoints:
        all_waypoints = _shared_runway_tail(dp_records)

    # A fix that names the airport itself (e.g. "KDEN") or one of its runways
    # is not a waypoint; a fix merely named like the airport, such as KDEN's
    # "DEN" VOR, is. Empty strings are dropped too.
    airport_ident = _lookup_ident(airport_code)
    all_waypoints = [
        w for w in all_waypoints if w and not w.startswith("RW") and w != airport_ident
    ]

    if not all_waypoints:
        return None

    return CifpDP(identifier=dp_id_prefix, waypoints=all_waypoints)


def get_star_data(airport: str, star_name: str) -> CifpSTAR | None:
    """Get STAR common route waypoints from CIFP.

    Args:
        airport: Airport code (e.g., "RNO", "KRNO")
        star_name: STAR name (e.g., "SCOLA1")

    Returns:
        CifpSTAR with common route waypoints, or None if not found
    """
    if not _ensure_index():
        return None

    star_index = _star_index
    airport_code = _airport_code(airport)

    star_id_prefix = star_name.upper().strip()

    star_records = _gather_records(
        star_index, _lookup_ident(airport_code), star_id_prefix
    )
    if not star_records:
        return None

    # Extract waypoints from procedure
    all_waypoints: list[str] = []
    seen_waypoints: set[str] = set()

    # Try common route first: transition is "ALL" or ""
    common_route_key = (
        "ALL" if "ALL" in star_records else "" if "" in star_records else None
    )
    if common_route_key is not None:
        sorted_fixes = sorted(star_records[common_route_key], key=lambda x: x[1])
        for fix, _ in sorted_fixes:
            if fix and fix not in seen_waypoints:
                all_waypoints.append(fix)
                seen_waypoints.add(fix)

    # If no common route, use the first runway transition's waypoints
    # (STARs end at the runway, so runway transitions contain the main path)
    if not all_waypoints:
        rw_transitions = sorted(t for t in star_records if t.startswith("RW"))
        if rw_transitions:
            first_rw = rw_transitions[0]
            sorted_fixes = sorted(star_records[first_rw], key=lambda x: x[1])
            for fix, _ in sorted_fixes:
                if fix and fix not in seen_waypoints:
                    all_waypoints.append(fix)
                    seen_waypoints.add(fix)

    # A fix that names the airport itself (e.g. "KMCI") or one of its runways
    # is not a waypoint; a fix merely named like the airport, such as KMCI's
    # "MCI" VOR, is. Empty strings are dropped too.
    airport_ident = _lookup_ident(airport_code)
    all_waypoints = [
        w for w in all_waypoints if w and not w.startswith("RW") and w != airport_ident
    ]

    if not all_waypoints:
        return None

    return CifpSTAR(identifier=star_id_prefix, waypoints=all_waypoints)


def clear_cifp_cache() -> None:
    """Drop the record index, so the next lookup reads the file again.

    Useful when CIFP data is updated.
    """
    global _cifp_index_path, _dp_index, _star_index, _approach_index

    with _cifp_index_lock:
        _cifp_index_path = None
        _dp_index = {}
        _star_index = {}
        _approach_index = {}
