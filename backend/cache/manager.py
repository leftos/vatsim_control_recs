"""
Caching utilities for backend data.

This module provides thread-safe caching for weather data, METAR, TAF,
aircraft approach speeds, and ARTCC groupings.

Caches use LRU eviction with size limits to prevent unbounded memory growth.
"""

import csv
import threading
from datetime import datetime, timezone
from typing import Dict, Any, Set, Optional

from cachetools import LRUCache

from common import logger as debug_logger

# Cache size limits
MAX_WEATHER_CACHE_SIZE = 1000  # Max entries in weather data caches
MAX_BLACKLIST_SIZE = 5000  # Max entries in blacklists (404 airports)

# Thread locks for cache synchronization
_WIND_CACHE_LOCK = threading.Lock()
_METAR_CACHE_LOCK = threading.Lock()
_TAF_CACHE_LOCK = threading.Lock()
_AIRCRAFT_SPEEDS_LOCK = threading.Lock()
_ARTCC_GROUPINGS_LOCK = threading.Lock()

# Cache for wind data - LRU with size limit
# {airport_icao: {'wind_info': str, 'timestamp': datetime}}
_WIND_DATA_CACHE: LRUCache = LRUCache(maxsize=MAX_WEATHER_CACHE_SIZE)
_WIND_BLACKLIST: LRUCache = LRUCache(maxsize=MAX_BLACKLIST_SIZE)  # Keys are ICAOs, values are True

# Cache for METAR data - LRU with size limit
# {airport_icao: {'metar': str, 'timestamp': datetime}}
_METAR_DATA_CACHE: LRUCache = LRUCache(maxsize=MAX_WEATHER_CACHE_SIZE)
_METAR_BLACKLIST: LRUCache = LRUCache(maxsize=MAX_BLACKLIST_SIZE)  # Keys are ICAOs, values are True

# Cache for TAF data - LRU with size limit
# {airport_icao: {'taf': str, 'timestamp': datetime}}
_TAF_DATA_CACHE: LRUCache = LRUCache(maxsize=MAX_WEATHER_CACHE_SIZE)
_TAF_BLACKLIST: LRUCache = LRUCache(maxsize=MAX_BLACKLIST_SIZE)  # Keys are ICAOs, values are True

# Cache for aircraft approach speeds
_AIRCRAFT_APPROACH_SPEEDS: Optional[Dict[str, int]] = None

# Cache for ARTCC groupings (loaded once on startup)
_ARTCC_GROUPINGS: Optional[Dict[str, list]] = None


def get_wind_cache_lock() -> threading.Lock:
    """Get the lock for wind cache operations."""
    return _WIND_CACHE_LOCK


def get_metar_cache_lock() -> threading.Lock:
    """Get the lock for METAR cache operations."""
    return _METAR_CACHE_LOCK


def get_taf_cache_lock() -> threading.Lock:
    """Get the lock for TAF cache operations."""
    return _TAF_CACHE_LOCK


def get_wind_cache() -> tuple[LRUCache, LRUCache]:
    """Get wind data cache and blacklist.

    Both caches are LRU with size limits to prevent unbounded memory growth.
    Note: Callers should use get_wind_cache_lock() to synchronize access
    when modifying the cache from multiple threads.
    """
    return _WIND_DATA_CACHE, _WIND_BLACKLIST


def get_metar_cache() -> tuple[LRUCache, LRUCache]:
    """Get METAR data cache and blacklist.

    Both caches are LRU with size limits to prevent unbounded memory growth.
    Note: Callers should use get_metar_cache_lock() to synchronize access
    when modifying the cache from multiple threads.
    """
    return _METAR_DATA_CACHE, _METAR_BLACKLIST


def get_taf_cache() -> tuple[LRUCache, LRUCache]:
    """Get TAF data cache and blacklist.

    Both caches are LRU with size limits to prevent unbounded memory growth.
    Note: Callers should use get_taf_cache_lock() to synchronize access
    when modifying the cache from multiple threads.
    """
    return _TAF_DATA_CACHE, _TAF_BLACKLIST


def get_aircraft_speeds_cache() -> Optional[Dict[str, int]]:
    """Get aircraft approach speeds cache (thread-safe read)."""
    with _AIRCRAFT_SPEEDS_LOCK:
        return _AIRCRAFT_APPROACH_SPEEDS


def set_aircraft_speeds_cache(speeds: Dict[str, int]) -> None:
    """Set aircraft approach speeds cache (thread-safe write)."""
    global _AIRCRAFT_APPROACH_SPEEDS
    with _AIRCRAFT_SPEEDS_LOCK:
        _AIRCRAFT_APPROACH_SPEEDS = speeds


def get_artcc_groupings_cache() -> Optional[Dict[str, list]]:
    """Get ARTCC groupings cache (thread-safe read)."""
    with _ARTCC_GROUPINGS_LOCK:
        return _ARTCC_GROUPINGS


def set_artcc_groupings_cache(groupings: Dict[str, list]) -> None:
    """Set ARTCC groupings cache (thread-safe write)."""
    global _ARTCC_GROUPINGS
    with _ARTCC_GROUPINGS_LOCK:
        _ARTCC_GROUPINGS = groupings


def clear_wind_cache() -> None:
    """Clear wind data cache (thread-safe)."""
    with _WIND_CACHE_LOCK:
        _WIND_DATA_CACHE.clear()
        _WIND_BLACKLIST.clear()


def clear_metar_cache() -> None:
    """Clear METAR data cache (thread-safe)."""
    with _METAR_CACHE_LOCK:
        _METAR_DATA_CACHE.clear()
        _METAR_BLACKLIST.clear()


def clear_taf_cache() -> None:
    """Clear TAF data cache (thread-safe)."""
    with _TAF_CACHE_LOCK:
        _TAF_DATA_CACHE.clear()
        _TAF_BLACKLIST.clear()


def clear_all_caches() -> None:
    """Clear all caches (thread-safe)."""
    global _AIRCRAFT_APPROACH_SPEEDS, _ARTCC_GROUPINGS

    with _WIND_CACHE_LOCK:
        _WIND_DATA_CACHE.clear()
        _WIND_BLACKLIST.clear()

    with _METAR_CACHE_LOCK:
        _METAR_DATA_CACHE.clear()
        _METAR_BLACKLIST.clear()

    with _TAF_CACHE_LOCK:
        _TAF_DATA_CACHE.clear()
        _TAF_BLACKLIST.clear()

    with _AIRCRAFT_SPEEDS_LOCK:
        _AIRCRAFT_APPROACH_SPEEDS = None

    with _ARTCC_GROUPINGS_LOCK:
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
        debug_logger.warning(f"Aircraft data file '{filename}' not found. ETA calculations will not use approach speeds.")
        set_aircraft_speeds_cache({})
        return {}
    except Exception as e:
        debug_logger.error(f"Error loading aircraft data from '{filename}': {e}. ETA calculations will not use approach speeds.")
        set_aircraft_speeds_cache({})
        return {}