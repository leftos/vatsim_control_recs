"""Runway data downloader and loader.

This module downloads runway data from OurAirports.com and provides
lookup functions for runway lengths and information.

The runway data is cached locally and updated on startup if the cached
data is older than a configurable threshold.
"""

import csv
import os
import threading
import urllib.request
import urllib.error
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional


# OurAirports runway data URL
RUNWAYS_URL = "https://davidmegginson.github.io/ourairports-data/runways.csv"
DOWNLOAD_TIMEOUT = 60  # seconds

# How often to check for updates (days)
UPDATE_INTERVAL_DAYS = 28  # Approximately one AIRAC cycle

# Cache file location
_script_dir = os.path.dirname(os.path.abspath(__file__))
RUNWAYS_CACHE_PATH = Path(os.path.join(_script_dir, '..', '..', 'data', 'runways.csv'))
RUNWAYS_METADATA_PATH = Path(os.path.join(_script_dir, '..', '..', 'data', 'runways_metadata.txt'))

# Thread-safe in-memory cache
_RUNWAY_DATA_LOCK = threading.Lock()
_RUNWAY_DATA: Optional[dict[str, list['RunwayInfo']]] = None


@dataclass
class RunwayInfo:
    """Information about a single runway."""

    airport_ident: str  # ICAO code, e.g., "KSFO"
    length_ft: int  # Runway length in feet
    width_ft: int  # Runway width in feet
    surface: str  # Surface type, e.g., "ASP" (asphalt), "CON" (concrete)
    lighted: bool  # Whether the runway is lighted
    closed: bool  # Whether the runway is closed
    le_ident: str  # Low-end identifier, e.g., "28L"
    he_ident: str  # High-end identifier, e.g., "10R"

    @property
    def identifiers(self) -> tuple[str, str]:
        """Get both runway identifiers."""
        return (self.le_ident, self.he_ident)

    @property
    def display_name(self) -> str:
        """Get display name for the runway pair."""
        return f"{self.le_ident}/{self.he_ident}"


def _needs_update() -> bool:
    """Check if runway data needs to be updated.

    Returns:
        True if data should be re-downloaded
    """
    if not RUNWAYS_CACHE_PATH.exists():
        return True

    if not RUNWAYS_METADATA_PATH.exists():
        return True

    try:
        with open(RUNWAYS_METADATA_PATH, 'r') as f:
            last_update_str = f.read().strip()
            last_update = datetime.fromisoformat(last_update_str)
            return datetime.now() - last_update > timedelta(days=UPDATE_INTERVAL_DAYS)
    except (ValueError, OSError):
        return True


def _save_metadata() -> None:
    """Save metadata about the last update."""
    try:
        RUNWAYS_METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RUNWAYS_METADATA_PATH, 'w') as f:
            f.write(datetime.now().isoformat())
    except OSError:
        pass


def download_runway_data(force: bool = False, quiet: bool = False) -> bool:
    """Download runway data from OurAirports.

    Args:
        force: If True, download even if cache is fresh
        quiet: If True, suppress print output

    Returns:
        True if download succeeded, False otherwise
    """
    if not force and not _needs_update():
        return True

    if not quiet:
        print("Downloading runway data from OurAirports...")

    try:
        with urllib.request.urlopen(RUNWAYS_URL, timeout=DOWNLOAD_TIMEOUT) as response:
            data = response.read()

        # Ensure directory exists
        RUNWAYS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Write to cache
        with open(RUNWAYS_CACHE_PATH, 'wb') as f:
            f.write(data)

        _save_metadata()

        if not quiet:
            print(f"Runway data saved to {RUNWAYS_CACHE_PATH}")

        # Clear in-memory cache (thread-safe)
        global _RUNWAY_DATA
        with _RUNWAY_DATA_LOCK:
            _RUNWAY_DATA = None
        get_longest_runway.cache_clear()

        return True

    except (urllib.error.URLError, TimeoutError, OSError) as e:
        if not quiet:
            print(f"Failed to download runway data: {e}")
        return False


def ensure_runway_data(quiet: bool = False) -> bool:
    """Ensure runway data is available, downloading if necessary.

    Args:
        quiet: If True, suppress print output

    Returns:
        True if runway data is available
    """
    if RUNWAYS_CACHE_PATH.exists():
        # Check if update needed
        if _needs_update():
            return download_runway_data(quiet=quiet)
        return True
    else:
        return download_runway_data(quiet=quiet)


def load_runway_data() -> dict[str, list[RunwayInfo]]:
    """Load runway data from cache.

    Thread-safe: uses lock to prevent race conditions during lazy loading.

    Returns:
        Dict mapping airport ICAO codes to list of RunwayInfo objects
    """
    global _RUNWAY_DATA

    # Fast path: already loaded
    with _RUNWAY_DATA_LOCK:
        if _RUNWAY_DATA is not None:
            return _RUNWAY_DATA

    if not RUNWAYS_CACHE_PATH.exists():
        with _RUNWAY_DATA_LOCK:
            _RUNWAY_DATA = {}
            return _RUNWAY_DATA

    runways: dict[str, list[RunwayInfo]] = defaultdict(list)

    try:
        with open(RUNWAYS_CACHE_PATH, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    # Skip if missing critical data
                    airport_ident = row.get('airport_ident', '').strip()
                    length_str = row.get('length_ft', '').strip()

                    if not airport_ident or not length_str:
                        continue

                    # Parse numeric fields
                    try:
                        length_ft = int(float(length_str))
                    except (ValueError, TypeError):
                        continue

                    try:
                        width_ft = int(float(row.get('width_ft', '0').strip() or '0'))
                    except (ValueError, TypeError):
                        width_ft = 0

                    # Parse boolean fields
                    lighted = row.get('lighted', '0').strip() == '1'
                    closed = row.get('closed', '0').strip() == '1'

                    # Get identifiers
                    le_ident = row.get('le_ident', '').strip()
                    he_ident = row.get('he_ident', '').strip()
                    surface = row.get('surface', '').strip()

                    runway = RunwayInfo(
                        airport_ident=airport_ident.upper(),
                        length_ft=length_ft,
                        width_ft=width_ft,
                        surface=surface,
                        lighted=lighted,
                        closed=closed,
                        le_ident=le_ident,
                        he_ident=he_ident,
                    )

                    runways[airport_ident.upper()].append(runway)

                except (KeyError, ValueError):
                    continue

    except (OSError, csv.Error):
        pass

    with _RUNWAY_DATA_LOCK:
        _RUNWAY_DATA = dict(runways)
        return _RUNWAY_DATA


def get_runways(airport_icao: str) -> list[RunwayInfo]:
    """Get all runways for an airport.

    Args:
        airport_icao: Airport ICAO code (e.g., "KSFO")

    Returns:
        List of RunwayInfo objects, empty if airport not found
    """
    data = load_runway_data()
    return data.get(airport_icao.upper(), [])


@lru_cache(maxsize=1000)
def get_longest_runway(airport_icao: str, open_only: bool = True) -> Optional[int]:
    """Get the longest runway length at an airport.

    Args:
        airport_icao: Airport ICAO code (e.g., "KSFO")
        open_only: If True, only consider non-closed runways

    Returns:
        Longest runway length in feet, or None if airport not found
    """
    runways = get_runways(airport_icao)
    if not runways:
        return None

    if open_only:
        runways = [r for r in runways if not r.closed]

    if not runways:
        return None

    return max(r.length_ft for r in runways)


def get_runway_summary(airport_icao: str) -> Optional[str]:
    """Get a brief summary of runways at an airport.

    Args:
        airport_icao: Airport ICAO code

    Returns:
        Summary string like "10000ft (28L/10R)", or None if not found
    """
    runways = get_runways(airport_icao)
    if not runways:
        return None

    # Find longest open runway
    open_runways = [r for r in runways if not r.closed]
    if not open_runways:
        return "Closed"

    longest = max(open_runways, key=lambda r: r.length_ft)
    return f"{longest.length_ft:,}ft ({longest.display_name})"


def clear_runway_cache() -> None:
    """Clear all runway caches (thread-safe).

    Useful when data is updated.
    """
    global _RUNWAY_DATA
    with _RUNWAY_DATA_LOCK:
        _RUNWAY_DATA = None
    get_longest_runway.cache_clear()
