"""
Caching utilities for backend data.
"""

import csv
from datetime import datetime, timezone
from typing import Dict, Any, Set, Optional

# Cache for wind data
_WIND_DATA_CACHE: Dict[str, Dict[str, Any]] = {}  # {airport_icao: {'wind_info': str, 'timestamp': datetime}}
_WIND_BLACKLIST: Set[str] = set()  # Set of airport ICAOs that don't have weather data available (404)

# Cache for METAR data
_METAR_DATA_CACHE: Dict[str, Dict[str, Any]] = {}  # {airport_icao: {'metar': str, 'timestamp': datetime}}
_METAR_BLACKLIST: Set[str] = set()  # Set of airport ICAOs that don't have METAR data available

# Cache for TAF data
_TAF_DATA_CACHE: Dict[str, Dict[str, Any]] = {}  # {airport_icao: {'taf': str, 'timestamp': datetime}}
_TAF_BLACKLIST: Set[str] = set()  # Set of airport ICAOs that don't have TAF data available

# Cache for aircraft approach speeds
_AIRCRAFT_APPROACH_SPEEDS: Optional[Dict[str, int]] = None

# Cache for ARTCC groupings (loaded once on startup)
_ARTCC_GROUPINGS: Optional[Dict[str, list]] = None


def get_wind_cache() -> tuple[Dict[str, Dict[str, Any]], Set[str]]:
    """Get wind data cache and blacklist."""
    return _WIND_DATA_CACHE, _WIND_BLACKLIST


def get_metar_cache() -> tuple[Dict[str, Dict[str, Any]], Set[str]]:
    """Get METAR data cache and blacklist."""
    return _METAR_DATA_CACHE, _METAR_BLACKLIST


def get_taf_cache() -> tuple[Dict[str, Dict[str, Any]], Set[str]]:
    """Get TAF data cache and blacklist."""
    return _TAF_DATA_CACHE, _TAF_BLACKLIST


def get_aircraft_speeds_cache() -> Optional[Dict[str, int]]:
    """Get aircraft approach speeds cache."""
    return _AIRCRAFT_APPROACH_SPEEDS


def set_aircraft_speeds_cache(speeds: Dict[str, int]) -> None:
    """Set aircraft approach speeds cache."""
    global _AIRCRAFT_APPROACH_SPEEDS
    _AIRCRAFT_APPROACH_SPEEDS = speeds


def get_artcc_groupings_cache() -> Optional[Dict[str, list]]:
    """Get ARTCC groupings cache."""
    return _ARTCC_GROUPINGS


def set_artcc_groupings_cache(groupings: Dict[str, list]) -> None:
    """Set ARTCC groupings cache."""
    global _ARTCC_GROUPINGS
    _ARTCC_GROUPINGS = groupings


def clear_wind_cache() -> None:
    """Clear wind data cache."""
    global _WIND_DATA_CACHE, _WIND_BLACKLIST
    _WIND_DATA_CACHE = {}
    _WIND_BLACKLIST = set()


def clear_metar_cache() -> None:
    """Clear METAR data cache."""
    global _METAR_DATA_CACHE, _METAR_BLACKLIST
    _METAR_DATA_CACHE = {}
    _METAR_BLACKLIST = set()


def clear_taf_cache() -> None:
    """Clear TAF data cache."""
    global _TAF_DATA_CACHE, _TAF_BLACKLIST
    _TAF_DATA_CACHE = {}
    _TAF_BLACKLIST = set()


def clear_all_caches() -> None:
    """Clear all caches."""
    global _WIND_DATA_CACHE, _WIND_BLACKLIST, _METAR_DATA_CACHE, _METAR_BLACKLIST
    global _TAF_DATA_CACHE, _TAF_BLACKLIST
    global _AIRCRAFT_APPROACH_SPEEDS, _ARTCC_GROUPINGS
    
    _WIND_DATA_CACHE = {}
    _WIND_BLACKLIST = set()
    _METAR_DATA_CACHE = {}
    _METAR_BLACKLIST = set()
    _TAF_DATA_CACHE = {}
    _TAF_BLACKLIST = set()
    _AIRCRAFT_APPROACH_SPEEDS = None
    _ARTCC_GROUPINGS = None


def load_aircraft_approach_speeds(filename: str) -> Dict[str, int]:
    """
    Load aircraft approach speeds from CSV file.
    Returns a dictionary mapping ICAO aircraft codes to approach speeds (in knots).
    Uses caching to avoid reloading on every call.

    Args:
        filename: Path to the aircraft data CSV file

    Returns:
        Dictionary mapping aircraft ICAO codes to approach speeds in knots
    """
    from backend.cache.manager import get_aircraft_speeds_cache, set_aircraft_speeds_cache

    cached = get_aircraft_speeds_cache()
    if cached is not None:
        return cached

    approach_speeds = {}
    try:
        with open(filename, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                icao_code = row.get('ICAO_Code', '').strip()
                approach_speed_str = row.get('Approach_Speed_knot', '').strip()

                # Only add entries with valid ICAO codes and approach speeds
                if icao_code and approach_speed_str and approach_speed_str != 'N/A':
                    try:
                        approach_speed = int(approach_speed_str)
                        approach_speeds[icao_code] = approach_speed
                    except ValueError:
                        # Skip entries with invalid approach speed values
                        continue

        set_aircraft_speeds_cache(approach_speeds)
        return approach_speeds
    except FileNotFoundError:
        print(f"Warning: Aircraft data file '{filename}' not found. ETA calculations will not use approach speeds.")
        set_aircraft_speeds_cache({})
        return {}
    except Exception as e:
        print(f"Warning: Error loading aircraft data from '{filename}': {e}. ETA calculations will not use approach speeds.")
        set_aircraft_speeds_cache({})
        return {}