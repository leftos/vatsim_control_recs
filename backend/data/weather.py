"""
Weather data fetching for airports (METAR and wind information).
"""

import json
import re
import threading
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional, Any, Callable

# Rate limiting state
_rate_limit_lock = threading.Lock()
_rate_limit_backoff = 0.0  # Current backoff delay in seconds
_rate_limit_errors = 0  # Consecutive rate limit errors
_rate_limit_last_error_time: Optional[datetime] = None

# Rate limiting configuration
RATE_LIMIT_INITIAL_BACKOFF = 1.0  # Initial backoff delay in seconds
RATE_LIMIT_MAX_BACKOFF = 30.0  # Maximum backoff delay in seconds
RATE_LIMIT_BACKOFF_MULTIPLIER = 2.0  # Exponential backoff multiplier
RATE_LIMIT_RECOVERY_TIME = 60.0  # Seconds without errors before resetting backoff


def _check_rate_limit_error(status_code: int) -> bool:
    """Check if an HTTP status code indicates rate limiting."""
    return status_code == 429 or status_code >= 500


def _record_rate_limit_error() -> float:
    """
    Record a rate limit error and calculate new backoff delay.

    Returns:
        The current backoff delay in seconds
    """
    global _rate_limit_backoff, _rate_limit_errors, _rate_limit_last_error_time

    with _rate_limit_lock:
        _rate_limit_errors += 1
        _rate_limit_last_error_time = datetime.now(timezone.utc)

        if _rate_limit_backoff == 0:
            _rate_limit_backoff = RATE_LIMIT_INITIAL_BACKOFF
        else:
            _rate_limit_backoff = min(
                _rate_limit_backoff * RATE_LIMIT_BACKOFF_MULTIPLIER,
                RATE_LIMIT_MAX_BACKOFF
            )

        return _rate_limit_backoff


def _record_successful_request() -> None:
    """
    Record a successful request and potentially reset backoff.

    Resets backoff if enough time has passed since last error.
    """
    global _rate_limit_backoff, _rate_limit_errors, _rate_limit_last_error_time

    with _rate_limit_lock:
        if _rate_limit_last_error_time is not None:
            time_since_error = (datetime.now(timezone.utc) - _rate_limit_last_error_time).total_seconds()
            if time_since_error > RATE_LIMIT_RECOVERY_TIME:
                # Reset rate limiting state after recovery period
                _rate_limit_backoff = 0.0
                _rate_limit_errors = 0
                _rate_limit_last_error_time = None


def _get_current_backoff() -> float:
    """
    Get the current backoff delay.

    Returns:
        Current backoff delay in seconds, or 0 if no backoff needed
    """
    with _rate_limit_lock:
        if _rate_limit_last_error_time is None:
            return 0.0

        # Check if we've recovered
        time_since_error = (datetime.now(timezone.utc) - _rate_limit_last_error_time).total_seconds()
        if time_since_error > RATE_LIMIT_RECOVERY_TIME:
            return 0.0

        return _rate_limit_backoff


def _wait_for_backoff() -> None:
    """Wait for the current backoff delay if rate limiting is active."""
    backoff = _get_current_backoff()
    if backoff > 0:
        time.sleep(backoff)


from backend.cache.manager import (
    get_wind_cache, get_metar_cache, get_taf_cache,
    get_wind_cache_lock, get_metar_cache_lock, get_taf_cache_lock
)
from backend.config.constants import WIND_CACHE_DURATION, METAR_CACHE_DURATION
from backend.core.calculations import haversine_distance_nm, calculate_bearing


def _parse_wind_from_observation(properties: dict) -> Tuple[bool, str]:
    """
    Parse wind data from a single observation.
    
    Args:
        properties: Observation properties dictionary from weather.gov API
    
    Returns:
        (has_data, wind_string) tuple where has_data is True if valid wind data was found
    """
    wind_direction = properties.get('windDirection', {}).get('value')
    wind_speed_kmh = properties.get('windSpeed', {}).get('value')
    wind_gust_kmh = properties.get('windGust', {}).get('value')
    
    # Check if we have valid wind data
    if wind_direction is None or wind_speed_kmh is None:
        return (False, "")
    
    # Convert km/h to knots (1 knot = 1.852 km/h)
    wind_speed_knots = round(wind_speed_kmh / 1.852)
    
    # Handle calm winds (0 knots)
    if wind_speed_knots == 0:
        return (True, "00000KT")
    
    # Format base wind: "27005KT"
    wind_str = f"{int(wind_direction):03d}{wind_speed_knots:02d}"
    
    # Add gusts if present and greater than steady wind
    if wind_gust_kmh is not None and wind_gust_kmh > 0:
        wind_gust_knots = round(wind_gust_kmh / 1.852)
        if wind_gust_knots > wind_speed_knots:
            wind_str += f"G{wind_gust_knots:02d}"
    
    # Add KT suffix
    wind_str += "KT"
    
    return (True, wind_str)


def get_wind_info_minute(airport_icao: str) -> str:
    """
    Fetch current wind information from weather.gov API with caching.

    Wind data is cached for 60 seconds to avoid excessive API calls.
    If the latest observation doesn't have wind data, fetches the last 10 observations
    and returns the most recent one with valid wind data.
    Airports returning 404 are blacklisted and never queried again.

    This function is thread-safe.

    Args:
        airport_icao: The ICAO code of the airport

    Returns:
        Formatted wind string like "27005G12KT" or "27005KT" or empty string if unavailable
    """
    wind_data_cache, wind_blacklist = get_wind_cache()
    wind_lock = get_wind_cache_lock()

    # Check if airport is blacklisted (doesn't have weather data available)
    with wind_lock:
        if airport_icao in wind_blacklist:
            return ""

        # Check if we have valid cached data
        current_time = datetime.now(timezone.utc)
        if airport_icao in wind_data_cache:
            cache_entry = wind_data_cache[airport_icao]
            time_since_cache = (current_time - cache_entry['timestamp']).total_seconds()
            if time_since_cache < WIND_CACHE_DURATION:
                return cache_entry['wind_info']

    # Cache miss or expired - fetch new data (outside lock to avoid blocking)
    try:
        # First, try the latest observation
        url = f"https://api.weather.gov/stations/{airport_icao}/observations/latest"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'VATSIM-Control-Recs/1.0')

        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode())

        properties = data.get('properties', {})
        has_data, wind_str = _parse_wind_from_observation(properties)

        # If latest observation doesn't have wind data, try the last 15 observations
        if not has_data:
            url = f"https://api.weather.gov/stations/{airport_icao}/observations?limit=30"
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'VATSIM-Control-Recs/1.0')

            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode())

            # Iterate through observations to find the first one with wind data
            features = data.get('features', [])
            for feature in features:
                properties = feature.get('properties', {})
                has_data, wind_str = _parse_wind_from_observation(properties)
                if has_data:
                    break

        # Cache the result (even if empty) - with lock
        current_time = datetime.now(timezone.utc)
        with wind_lock:
            wind_data_cache[airport_icao] = {
                'wind_info': wind_str,
                'timestamp': current_time
            }

        return wind_str

    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Station doesn't exist - blacklist it permanently
            with wind_lock:
                wind_blacklist[airport_icao] = True
            return ""
        # For other HTTP errors, return cached data if available
        with wind_lock:
            if airport_icao in wind_data_cache:
                return wind_data_cache[airport_icao]['wind_info']
        return ""
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError, TimeoutError):
        # On other errors, return cached data if available (even if expired), otherwise empty string
        with wind_lock:
            if airport_icao in wind_data_cache:
                return wind_data_cache[airport_icao]['wind_info']
        return ""


def _fetch_metar_from_aviationweather(airport_icao: str) -> Optional[str]:
    """
    Fetch METAR from aviationweather.gov API.

    Returns:
        METAR string, empty string if no data, or None on error
    """
    try:
        # Wait for backoff if rate limiting is active
        _wait_for_backoff()

        url = f"https://aviationweather.gov/api/data/metar?ids={airport_icao}&format=raw"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'VATSIM-Control-Recs/1.0')

        with urllib.request.urlopen(req, timeout=5) as response:
            metar_text = response.read().decode('utf-8').strip()

        # Record successful request (may reset backoff after recovery period)
        _record_successful_request()

        if not metar_text:
            # Empty response (e.g., HTTP 204) - return empty to trigger fallback
            return ""

        if metar_text.startswith('No METAR') or metar_text.startswith('Error'):
            # Explicit error - return None to indicate blacklist
            return None

        return metar_text

    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # Station doesn't exist - blacklist
        if _check_rate_limit_error(e.code):
            backoff = _record_rate_limit_error()
            from common import logger as debug_logger
            debug_logger.debug(f"Rate limit detected (HTTP {e.code}) for METAR {airport_icao}, backoff: {backoff:.1f}s")
        return ""  # Other HTTP errors - try fallback
    except (urllib.error.URLError, TimeoutError):
        return ""  # Network error - try fallback
    except Exception:
        return ""  # Other errors - try fallback


def _fetch_metar_from_vatsim(airport_icao: str) -> Optional[str]:
    """
    Fetch METAR from VATSIM METAR API (fallback).

    Returns:
        METAR string or empty string if unavailable
    """
    try:
        url = f"https://metar.vatsim.net/{airport_icao}"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'VATSIM-Control-Recs/1.0')

        with urllib.request.urlopen(req, timeout=5) as response:
            metar_text = response.read().decode('utf-8').strip()

        if not metar_text or metar_text.startswith('No METAR'):
            return ""

        return metar_text

    except Exception:
        return ""


def get_metar(airport_icao: str) -> str:
    """
    Fetch current METAR with caching.

    Uses aviationweather.gov as primary source, with VATSIM METAR API as fallback.
    METAR data is cached for 60 seconds to avoid excessive API calls.
    When fetching METAR, wind and altimeter are also parsed and cached
    to avoid redundant parsing when both values are needed.

    This function is thread-safe.

    Args:
        airport_icao: The ICAO code of the airport

    Returns:
        Full METAR string or empty string if unavailable
    """
    metar_data_cache, metar_blacklist = get_metar_cache()
    metar_lock = get_metar_cache_lock()

    # Check if airport is blacklisted (doesn't have METAR data available)
    with metar_lock:
        if airport_icao in metar_blacklist:
            return ""

        # Check if we have valid cached data
        current_time = datetime.now(timezone.utc)
        if airport_icao in metar_data_cache:
            cache_entry = metar_data_cache[airport_icao]
            time_since_cache = (current_time - cache_entry['timestamp']).total_seconds()
            if time_since_cache < METAR_CACHE_DURATION:
                return cache_entry['metar']

    # Cache miss or expired - fetch new data (outside lock to avoid blocking)
    # Try primary source first
    metar_text = _fetch_metar_from_aviationweather(airport_icao)

    if metar_text is None:
        # Station doesn't exist - blacklist it permanently
        with metar_lock:
            metar_blacklist[airport_icao] = True
        return ""

    # If primary returned empty, try VATSIM fallback
    if metar_text == "":
        metar_text = _fetch_metar_from_vatsim(airport_icao)

    # If still empty, return cached data if available or empty string
    if not metar_text:
        with metar_lock:
            if airport_icao in metar_data_cache:
                return metar_data_cache[airport_icao]['metar']
        return ""

    # Parse wind and altimeter from METAR for caching
    parsed_wind = _parse_wind_from_metar(metar_text)
    parsed_altimeter = parse_altimeter_from_metar(metar_text)

    # Cache the result with parsed values
    current_time = datetime.now(timezone.utc)
    with metar_lock:
        metar_data_cache[airport_icao] = {
            'metar': metar_text,
            'wind': parsed_wind,
            'altimeter': parsed_altimeter,
            'timestamp': current_time
        }

    return metar_text


def get_taf(airport_icao: str) -> str:
    """
    Fetch current TAF (Terminal Aerodrome Forecast) from aviationweather.gov API with caching.

    TAF data is cached for 60 seconds to avoid excessive API calls.
    Airports returning 404 or no data are blacklisted and never queried again in this session.

    This function is thread-safe.

    Args:
        airport_icao: The ICAO code of the airport

    Returns:
        Full TAF string or empty string if unavailable
    """
    taf_data_cache, taf_blacklist = get_taf_cache()
    taf_lock = get_taf_cache_lock()

    # Check if airport is blacklisted (doesn't have TAF data available)
    with taf_lock:
        if airport_icao in taf_blacklist:
            return ""

        # Check if we have valid cached data
        current_time = datetime.now(timezone.utc)
        if airport_icao in taf_data_cache:
            cache_entry = taf_data_cache[airport_icao]
            time_since_cache = (current_time - cache_entry['timestamp']).total_seconds()
            if time_since_cache < METAR_CACHE_DURATION:  # Use same cache duration as METAR
                return cache_entry['taf']

    # Cache miss or expired - fetch new data (outside lock to avoid blocking)
    try:
        # Wait for backoff if rate limiting is active
        _wait_for_backoff()

        url = f"https://aviationweather.gov/api/data/taf?ids={airport_icao}&format=raw"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'VATSIM-Control-Recs/1.0')

        with urllib.request.urlopen(req, timeout=5) as response:
            taf_text = response.read().decode('utf-8').strip()

        # Record successful request (may reset backoff after recovery period)
        _record_successful_request()

        # Check if we got valid data (not empty and not an error message)
        if not taf_text:
            # Empty response (e.g., HTTP 204 No Content) - temporary, don't blacklist
            return ""

        if taf_text.startswith('No TAF') or taf_text.startswith('Error'):
            # Explicit error message - station likely doesn't report TAF, blacklist it
            with taf_lock:
                taf_blacklist[airport_icao] = True
            return ""

        # Cache the result
        current_time = datetime.now(timezone.utc)
        with taf_lock:
            taf_data_cache[airport_icao] = {
                'taf': taf_text,
                'timestamp': current_time
            }

        return taf_text

    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Station doesn't exist - blacklist it permanently
            with taf_lock:
                taf_blacklist[airport_icao] = True
            return ""
        if _check_rate_limit_error(e.code):
            backoff = _record_rate_limit_error()
            from common import logger as debug_logger
            debug_logger.debug(f"Rate limit detected (HTTP {e.code}) for TAF {airport_icao}, backoff: {backoff:.1f}s")
        # For other HTTP errors - temporary, don't blacklist
        # Return cached data if available
        with taf_lock:
            if airport_icao in taf_data_cache:
                return taf_data_cache[airport_icao]['taf']
        return ""
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError, TimeoutError) as e:
        # Expected network/parsing errors - temporary, don't blacklist
        from common import logger as debug_logger
        debug_logger.debug(f"Expected error fetching TAF for {airport_icao}: {type(e).__name__}: {e}")
        with taf_lock:
            if airport_icao in taf_data_cache:
                return taf_data_cache[airport_icao]['taf']
        return ""
    except Exception as e:
        # Unexpected errors - log with traceback for debugging
        from common import logger as debug_logger
        debug_logger.error(f"Unexpected error fetching TAF for {airport_icao}: {type(e).__name__}: {e}", exc_info=True)
        with taf_lock:
            if airport_icao in taf_data_cache:
                return taf_data_cache[airport_icao]['taf']
        return ""


def _parse_wind_from_metar(metar: str) -> str:
    """
    Parse wind information from a METAR string.
    
    Args:
        metar: The METAR string
        
    Returns:
        Wind string in format like "27005KT" or "27005G12KT" or "00000KT" or empty string if unavailable
    """
    if not metar:
        return ""
    
    # Parse wind from METAR
    # Wind format in METAR: DDDSSGggKT or DDDSSKMHor DDDSSKPH (Direction Speed Gust)
    # Also handle VRB for variable, and 00000KT for calm
    
    # Look for wind pattern: direction (3 digits or VRB), speed (2-3 digits), optional gust (G + 2-3 digits), units (KT/KMH/KPH/MPS)
    wind_pattern = r'\b(\d{3}|VRB)(\d{2,3})(G\d{2,3})?(KT|KMH|KPH|MPS)\b'
    match = re.search(wind_pattern, metar)
    
    if match:
        direction = match.group(1)
        speed = match.group(2)
        gust = match.group(3)  # Includes 'G' prefix if present
        units = match.group(4)
        
        # Check for calm winds
        if direction != 'VRB' and int(speed) == 0:
            return "00000KT"
        
        # Build wind string (always convert to KT for consistency)
        wind_str = f"{direction}{speed}"
        if gust:
            wind_str += gust
        
        # Add KT suffix (convert if needed, but METAR is usually in KT)
        if units == 'KT':
            wind_str += 'KT'
        elif units in ['KMH', 'KPH']:
            # Convert km/h to knots (1 knot = 1.852 km/h)
            speed_kt = round(int(speed) / 1.852)
            wind_str = f"{direction}{speed_kt:02d}"
            if gust:
                gust_kt = round(int(gust[1:]) / 1.852)
                wind_str += f"G{gust_kt:02d}"
            wind_str += 'KT'
        elif units == 'MPS':
            # Convert m/s to knots (1 knot = 0.514444 m/s)
            speed_kt = round(int(speed) / 0.514444)
            wind_str = f"{direction}{speed_kt:02d}"
            if gust:
                gust_kt = round(int(gust[1:]) / 0.514444)
                wind_str += f"G{gust_kt:02d}"
            wind_str += 'KT'
        else:
            wind_str += 'KT'
        
        return wind_str
    
    return ""


def get_wind_from_metar(airport_icao: str) -> str:
    """
    Extract wind information from METAR.
    Uses cached parsed wind if available to avoid redundant parsing.

    This function is thread-safe.

    Args:
        airport_icao: The ICAO code of the airport

    Returns:
        Wind string in format like "27005KT" or "27005G12KT" or "00000KT" or empty string if unavailable
    """
    metar_data_cache, _metar_blacklist = get_metar_cache()
    metar_lock = get_metar_cache_lock()

    # Check if we have cached parsed wind data
    with metar_lock:
        current_time = datetime.now(timezone.utc)
        if airport_icao in metar_data_cache:
            cache_entry = metar_data_cache[airport_icao]
            time_since_cache = (current_time - cache_entry['timestamp']).total_seconds()
            if time_since_cache < METAR_CACHE_DURATION:
                # Return cached parsed wind if available
                return cache_entry.get('wind', '')

    # Cache miss or expired - fetch METAR (which will parse and cache wind)
    metar = get_metar(airport_icao)
    if not metar:
        return ""

    # After get_metar, check cache again for parsed wind
    with metar_lock:
        if airport_icao in metar_data_cache:
            return metar_data_cache[airport_icao].get('wind', '')

    # Fallback: parse directly if cache doesn't have it for some reason
    return _parse_wind_from_metar(metar)


def get_wind_info(airport_icao: str, source: str = "metar") -> str:
    """
    Fetch current wind information with caching.
    
    Wind data is cached for 60 seconds to avoid excessive API calls.
    Supports two sources:
    - "metar": Uses METAR from aviationweather.gov (default)
    - "minute": Uses up-to-the-minute observations from weather.gov
    
    Args:
        airport_icao: The ICAO code of the airport
        source: Wind data source - "metar" or "minute" (default: "metar")
        
    Returns:
        Formatted wind string like "27005G12KT" or "27005KT" or empty string if unavailable
    """
    if source.lower() == "metar":
        return get_wind_from_metar(airport_icao)
    elif source.lower() == "minute":
        return get_wind_info_minute(airport_icao)
    else:
        # Default to METAR for unknown sources
        return get_wind_from_metar(airport_icao)


def get_wind_info_batch(airport_icaos: List[str], source: str = "metar", max_workers: int = 10) -> Dict[str, str]:
    """
    Fetch wind information for multiple airports in parallel.

    This is much more efficient than calling get_wind_info() sequentially for many airports.
    Uses ThreadPoolExecutor to fetch wind data concurrently.

    Args:
        airport_icaos: List of ICAO codes to fetch wind info for
        source: Wind data source - "metar" or "minute" (default: "metar")
        max_workers: Maximum number of concurrent threads (default: 10)

    Returns:
        Dictionary mapping ICAO codes to wind strings (empty string if unavailable)
    """
    results = {}

    # Use ThreadPoolExecutor to parallelize network requests
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_icao = {
            executor.submit(get_wind_info, icao, source): icao
            for icao in airport_icaos
        }

        # Collect results as they complete
        for future in as_completed(future_to_icao):
            icao = future_to_icao[future]
            try:
                wind_info = future.result()
                results[icao] = wind_info
            except Exception:
                # If there's an error, just use empty string
                results[icao] = ""

    return results


def get_metar_batch(
    airport_icaos: List[str],
    max_workers: int = 10,
    progress_callback: Optional[Callable[[int, int], None]] = None
) -> Dict[str, str]:
    """
    Fetch METAR data for multiple airports in parallel.

    This is much more efficient than calling get_metar() sequentially for many airports.
    Uses ThreadPoolExecutor to fetch METAR data concurrently. Also warms the cache
    for altimeter and wind data since those are parsed from METAR.

    Args:
        airport_icaos: List of ICAO codes to fetch METAR for
        max_workers: Maximum number of concurrent threads (default: 10)
        progress_callback: Optional callback(completed, total) called as results arrive

    Returns:
        Dictionary mapping ICAO codes to METAR strings (empty string if unavailable)
    """
    results = {}

    if not airport_icaos:
        return results

    total = len(airport_icaos)
    completed = 0

    # Use ThreadPoolExecutor to parallelize network requests
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_icao = {
            executor.submit(get_metar, icao): icao
            for icao in airport_icaos
        }

        # Collect results as they complete
        for future in as_completed(future_to_icao):
            icao = future_to_icao[future]
            try:
                metar = future.result()
                results[icao] = metar
            except Exception:
                # If there's an error, just use empty string
                results[icao] = ""

            completed += 1
            if progress_callback:
                progress_callback(completed, total)

    return results


def get_taf_batch(
    airport_icaos: List[str],
    max_workers: int = 10,
    progress_callback: Optional[Callable[[int, int], None]] = None
) -> Dict[str, str]:
    """
    Fetch TAF data for multiple airports in parallel.

    This is much more efficient than calling get_taf() sequentially for many airports.
    Uses ThreadPoolExecutor to fetch TAF data concurrently.

    Args:
        airport_icaos: List of ICAO codes to fetch TAF for
        max_workers: Maximum number of concurrent threads (default: 10)
        progress_callback: Optional callback(completed, total) called as results arrive

    Returns:
        Dictionary mapping ICAO codes to TAF strings (empty string if unavailable)
    """
    results = {}

    if not airport_icaos:
        return results

    total = len(airport_icaos)
    completed = 0

    # Use ThreadPoolExecutor to parallelize network requests
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_icao = {
            executor.submit(get_taf, icao): icao
            for icao in airport_icaos
        }

        # Collect results as they complete
        for future in as_completed(future_to_icao):
            icao = future_to_icao[future]
            try:
                taf = future.result()
                results[icao] = taf
            except Exception:
                # If there's an error, just use empty string
                results[icao] = ""

            completed += 1
            if progress_callback:
                progress_callback(completed, total)

    return results


def fetch_weather_bbox(
    bbox: Tuple[float, float, float, float],
    include_taf: bool = True,
    timeout: int = 30
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Fetch METAR and TAF data for all stations within a bounding box.

    Uses aviationweather.gov's bbox parameter to fetch all weather data
    in a single API call, which is much more efficient than per-airport fetching.

    Args:
        bbox: Bounding box as (min_lat, min_lon, max_lat, max_lon)
        include_taf: Whether to include TAF data (uses taf=true parameter)
        timeout: Request timeout in seconds

    Returns:
        Tuple of (metars_dict, tafs_dict) where keys are ICAO codes
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    bbox_str = f"{min_lat},{min_lon},{max_lat},{max_lon}"

    metars: Dict[str, str] = {}
    tafs: Dict[str, str] = {}

    try:
        # Wait for backoff if rate limiting is active
        _wait_for_backoff()

        # Use taf=true to get both METAR and TAF in one call
        taf_param = "&taf=true" if include_taf else ""
        url = f"https://aviationweather.gov/api/data/metar?bbox={bbox_str}&format=json{taf_param}"

        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'VATSIM-Control-Recs/1.0')

        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))

        # Record successful request
        _record_successful_request()

        # Parse response - it's an array of METAR objects
        if isinstance(data, list):
            for entry in data:
                icao = entry.get('icaoId', '')
                raw_metar = entry.get('rawOb', '')
                raw_taf = entry.get('rawTaf', '')

                if icao and raw_metar:
                    metars[icao] = raw_metar
                if icao and raw_taf:
                    tafs[icao] = raw_taf

        return (metars, tafs)

    except urllib.error.HTTPError as e:
        if _check_rate_limit_error(e.code):
            _record_rate_limit_error()
        return ({}, {})
    except Exception:
        return ({}, {})


def get_weather_batch_bbox(
    artcc_bboxes: Dict[str, Tuple[float, float, float, float]],
    target_airports: Optional[List[str]] = None,
    max_workers: int = 5,
    progress_callback: Optional[Callable[[int, int], None]] = None
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Fetch METAR and TAF data for multiple ARTCCs using bounding box queries.

    This is much more efficient than per-airport fetching when dealing with
    many airports across multiple ARTCCs. Makes one API call per ARTCC instead
    of one per airport.

    Args:
        artcc_bboxes: Dict mapping ARTCC codes to bounding boxes (min_lat, min_lon, max_lat, max_lon)
        target_airports: Optional list of airports to filter results to (if None, returns all)
        max_workers: Maximum concurrent ARTCC fetches
        progress_callback: Optional callback(completed, total) called as ARTCCs complete

    Returns:
        Tuple of (metars_dict, tafs_dict) where keys are ICAO codes
    """
    all_metars: Dict[str, str] = {}
    all_tafs: Dict[str, str] = {}

    if not artcc_bboxes:
        return (all_metars, all_tafs)

    total = len(artcc_bboxes)
    completed = 0

    # Fetch weather for each ARTCC in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_artcc = {
            executor.submit(fetch_weather_bbox, bbox, True): artcc
            for artcc, bbox in artcc_bboxes.items()
        }

        for future in as_completed(future_to_artcc):
            artcc = future_to_artcc[future]
            try:
                metars, tafs = future.result()
                all_metars.update(metars)
                all_tafs.update(tafs)
            except Exception:
                pass  # Individual ARTCC failure doesn't stop others

            completed += 1
            if progress_callback:
                progress_callback(completed, total)

    # Filter to target airports if specified
    if target_airports is not None:
        target_set = set(a.upper() for a in target_airports)
        all_metars = {k: v for k, v in all_metars.items() if k.upper() in target_set}
        all_tafs = {k: v for k, v in all_tafs.items() if k.upper() in target_set}

    return (all_metars, all_tafs)


def parse_altimeter_from_metar(metar: str) -> Optional[str]:
    """
    Extract altimeter setting from METAR.
    
    Args:
        metar: The METAR string
        
    Returns:
        Altimeter string in format "A2992" or "Q1013" or None if not found
    """
    if not metar:
        return None
    
    # Altimeter format in METAR:
    # A#### for inches of mercury (e.g., A2992 = 29.92 inHg)
    # Q#### for hectopascals/millibars (e.g., Q1013 = 1013 hPa)
    altimeter_pattern = r'\b([AQ]\d{4})\b'
    match = re.search(altimeter_pattern, metar)
    
    if match:
        return match.group(1)
    
    return None


def get_altimeter_setting(airport_icao: str) -> Optional[str]:
    """
    Get altimeter setting for an airport from its METAR.
    Uses cached parsed altimeter if available to avoid redundant parsing.

    This function is thread-safe.

    Args:
        airport_icao: The ICAO code of the airport

    Returns:
        Altimeter string (e.g., "A2992" or "Q1013") or None if unavailable
    """
    metar_data_cache, _metar_blacklist = get_metar_cache()
    metar_lock = get_metar_cache_lock()

    # Check if we have cached parsed altimeter data
    with metar_lock:
        current_time = datetime.now(timezone.utc)
        if airport_icao in metar_data_cache:
            cache_entry = metar_data_cache[airport_icao]
            time_since_cache = (current_time - cache_entry['timestamp']).total_seconds()
            if time_since_cache < METAR_CACHE_DURATION:
                # Return cached parsed altimeter if available
                return cache_entry.get('altimeter')

    # Cache miss or expired - fetch METAR (which will parse and cache altimeter)
    metar = get_metar(airport_icao)
    if not metar:
        return None

    # After get_metar, check cache again for parsed altimeter
    with metar_lock:
        if airport_icao in metar_data_cache:
            return metar_data_cache[airport_icao].get('altimeter')

    # Fallback: parse directly if cache doesn't have it for some reason
    return parse_altimeter_from_metar(metar)


# Global cache for spatial index of airports with METAR
_METAR_AIRPORT_SPATIAL_INDEX: Optional[Dict[str, Any]] = None
_METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP: Optional[datetime] = None
_METAR_SPATIAL_INDEX_DURATION = 300  # 5 minutes cache
_METAR_SPATIAL_INDEX_LOCK = threading.Lock()  # Lock for synchronizing spatial index rebuild

# Position-based result cache for find_nearest_airport_with_metar
# Key: (rounded_lat, rounded_lon) tuple, Value: {'result': tuple or None, 'timestamp': datetime}
_NEAREST_METAR_RESULT_CACHE: Dict[Tuple[float, float], Dict[str, Any]] = {}
_NEAREST_METAR_RESULT_CACHE_DURATION = 60  # 1 minute cache (matches METAR cache duration)
_NEAREST_METAR_RESULT_CACHE_LOCK = threading.Lock()
_NEAREST_METAR_POSITION_GRID_SIZE = 0.1  # ~6nm grid for position rounding

# Persisted spatial cache (loaded from disk once at startup)
_PERSISTED_SPATIAL_CACHE: Optional[Dict[str, Any]] = None
_PERSISTED_SPATIAL_CACHE_LOADED = False
_KNOWN_METAR_STATIONS: Optional[set] = None  # Whitelist of airports known to have METAR


def _load_persisted_spatial_cache() -> Optional[Dict[str, Any]]:
    """
    Load persisted spatial cache from disk if available.

    The cache file is generated by scripts/precalculate_airport_spatial_data.py
    and contains the spatial grid and known METAR stations.

    Returns:
        Cache data dictionary or None if not available
    """
    global _PERSISTED_SPATIAL_CACHE, _PERSISTED_SPATIAL_CACHE_LOADED, _KNOWN_METAR_STATIONS

    if _PERSISTED_SPATIAL_CACHE_LOADED:
        return _PERSISTED_SPATIAL_CACHE

    _PERSISTED_SPATIAL_CACHE_LOADED = True

    try:
        # Find the cache file relative to this module
        import os
        module_dir = os.path.dirname(os.path.abspath(__file__))
        cache_file = os.path.join(module_dir, '..', '..', 'data', 'airport_spatial_cache.json')
        cache_file = os.path.normpath(cache_file)

        if not os.path.exists(cache_file):
            return None

        with open(cache_file, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)

        # Validate version
        if cache_data.get('version') != 1:
            return None

        _PERSISTED_SPATIAL_CACHE = cache_data

        # Load known METAR stations as a set for fast lookup
        metar_stations = cache_data.get('metar_stations')
        if metar_stations:
            _KNOWN_METAR_STATIONS = set(metar_stations)

        return cache_data

    except Exception:
        return None


def _build_metar_airport_spatial_index(airports_data: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build a spatial index of airports that have METAR data for efficient nearest-airport lookups.

    First tries to load from persisted cache (generated by precalculate script).
    Falls back to building from scratch if cache is unavailable.

    For optimization, we use a simple grid-based spatial hash:
    - Divide the world into 1-degree grid cells
    - Store airports in each cell for quick regional lookup
    - This avoids expensive distance calculations for all airports

    Args:
        airports_data: Dictionary of all airport data

    Returns:
        Dictionary with spatial index structure
    """
    _metar_data_cache, metar_blacklist = get_metar_cache()

    # Try to load from persisted cache first
    persisted_cache = _load_persisted_spatial_cache()

    if persisted_cache and 'spatial_grid' in persisted_cache:
        # Use persisted spatial grid, but filter by runtime blacklist and METAR whitelist
        persisted_grid = persisted_cache['spatial_grid']

        valid_airports = []
        spatial_grid = {}

        for cell_key_str, airports in persisted_grid.items():
            # Convert string key back to tuple
            parts = cell_key_str.split(',')
            cell_key = (int(parts[0]), int(parts[1]))

            for airport in airports:
                icao = airport['icao']

                # Skip if blacklisted at runtime (404 errors)
                if icao in metar_blacklist:
                    continue

                # If we have a whitelist, only include known METAR stations
                if _KNOWN_METAR_STATIONS and icao not in _KNOWN_METAR_STATIONS:
                    continue

                valid_airports.append(airport)

                if cell_key not in spatial_grid:
                    spatial_grid[cell_key] = []
                spatial_grid[cell_key].append(airport)

        return {
            'grid': spatial_grid,
            'airports': valid_airports,
            'cell_size': 1.0  # degrees
        }

    # No persisted cache - build from scratch
    valid_airports = []
    for icao, data in airports_data.items():
        # Skip if blacklisted or missing coordinates
        if icao in metar_blacklist or data.get('latitude') is None or data.get('longitude') is None:
            continue

        # If we have a whitelist, only include known METAR stations
        if _KNOWN_METAR_STATIONS and icao not in _KNOWN_METAR_STATIONS:
            continue

        valid_airports.append({
            'icao': icao,
            'lat': data['latitude'],
            'lon': data['longitude']
        })

    # Build spatial grid (1-degree cells)
    spatial_grid = {}
    for airport in valid_airports:
        lat_cell = int(airport['lat'])
        lon_cell = int(airport['lon'])
        cell_key = (lat_cell, lon_cell)

        if cell_key not in spatial_grid:
            spatial_grid[cell_key] = []
        spatial_grid[cell_key].append(airport)

    return {
        'grid': spatial_grid,
        'airports': valid_airports,
        'cell_size': 1.0  # degrees
    }


def find_airports_near_position(
    latitude: float,
    longitude: float,
    airports_data: Dict[str, Dict[str, Any]],
    radius_nm: float = 50.0,
    max_results: int = 5
) -> List[str]:
    """
    Find airports near a given position for METAR precaching.

    Uses the spatial index for efficient lookup. Returns airport ICAO codes
    sorted by distance, limited to max_results.

    Args:
        latitude: Latitude in decimal degrees
        longitude: Longitude in decimal degrees
        airports_data: Dictionary of all airport data
        radius_nm: Search radius in nautical miles (default: 50)
        max_results: Maximum number of airports to return (default: 5)

    Returns:
        List of airport ICAO codes sorted by distance (closest first)
    """
    global _METAR_AIRPORT_SPATIAL_INDEX, _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP

    # Ensure spatial index is built
    current_time = datetime.now(timezone.utc)

    needs_rebuild = (
        _METAR_AIRPORT_SPATIAL_INDEX is None or
        _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP is None or
        (current_time - _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP).total_seconds() > _METAR_SPATIAL_INDEX_DURATION
    )

    if needs_rebuild:
        with _METAR_SPATIAL_INDEX_LOCK:
            current_time = datetime.now(timezone.utc)
            if (_METAR_AIRPORT_SPATIAL_INDEX is None or
                _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP is None or
                (current_time - _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP).total_seconds() > _METAR_SPATIAL_INDEX_DURATION):
                _METAR_AIRPORT_SPATIAL_INDEX = _build_metar_airport_spatial_index(airports_data)
                _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP = current_time

    spatial_index = _METAR_AIRPORT_SPATIAL_INDEX
    assert spatial_index is not None, "Spatial index should have been built"
    grid = spatial_index['grid']

    # Determine which cells to search
    lat_cell = int(latitude)
    lon_cell = int(longitude)

    # Search current cell and adjacent cells (3x3 grid)
    search_cells = []
    for dlat in [-1, 0, 1]:
        for dlon in [-1, 0, 1]:
            search_cells.append((lat_cell + dlat, lon_cell + dlon))

    # Find airports within radius
    candidates = []

    for cell_key in search_cells:
        if cell_key not in grid:
            continue

        for airport in grid[cell_key]:
            distance = haversine_distance_nm(
                latitude, longitude,
                airport['lat'], airport['lon']
            )

            if distance <= radius_nm:
                candidates.append((distance, airport['icao']))

    # Sort by distance and return top results
    candidates.sort(key=lambda x: x[0])
    return [icao for _, icao in candidates[:max_results]]


def find_nearest_airport_with_metar(
    latitude: float,
    longitude: float,
    airports_data: Dict[str, Dict[str, Any]],
    max_distance_nm: float = 100.0,
    aircraft_heading: Optional[float] = None,
    aircraft_groundspeed: Optional[float] = None
) -> Optional[Tuple[str, str, float]]:
    """
    Find the nearest airport with METAR data to given coordinates.

    Uses a spatial index for efficient lookup. The index is cached and rebuilt
    every 5 minutes to account for new METAR blacklists. Results are also cached
    based on rounded position (~6nm grid) for 1 minute.

    When aircraft_heading is provided and groundspeed > 40kt, airports ahead
    of the aircraft are preferred over those behind. The scoring uses a heading
    penalty that makes airports behind appear farther than they actually are.

    Args:
        latitude: Latitude in decimal degrees
        longitude: Longitude in decimal degrees
        airports_data: Dictionary of all airport data
        max_distance_nm: Maximum search radius in nautical miles (default: 100)
        aircraft_heading: Optional aircraft heading in degrees (0-360)
        aircraft_groundspeed: Optional aircraft groundspeed in knots

    Returns:
        Tuple of (icao_code, altimeter_setting, distance_nm) or None if no airport found
        Distance is in nautical miles
    """
    global _METAR_AIRPORT_SPATIAL_INDEX, _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP

    current_time = datetime.now(timezone.utc)

    # Round position to grid for cache lookup (~6nm grid)
    rounded_lat = round(latitude / _NEAREST_METAR_POSITION_GRID_SIZE) * _NEAREST_METAR_POSITION_GRID_SIZE
    rounded_lon = round(longitude / _NEAREST_METAR_POSITION_GRID_SIZE) * _NEAREST_METAR_POSITION_GRID_SIZE
    cache_key = (rounded_lat, rounded_lon)

    # Check result cache first (fast path)
    with _NEAREST_METAR_RESULT_CACHE_LOCK:
        if cache_key in _NEAREST_METAR_RESULT_CACHE:
            cache_entry = _NEAREST_METAR_RESULT_CACHE[cache_key]
            time_since_cache = (current_time - cache_entry['timestamp']).total_seconds()
            if time_since_cache < _NEAREST_METAR_RESULT_CACHE_DURATION:
                return cache_entry['result']

    # Check if we need to build or rebuild the spatial index (double-checked locking)
    needs_rebuild = (
        _METAR_AIRPORT_SPATIAL_INDEX is None or
        _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP is None or
        (current_time - _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP).total_seconds() > _METAR_SPATIAL_INDEX_DURATION
    )

    if needs_rebuild:
        with _METAR_SPATIAL_INDEX_LOCK:
            # Re-check inside lock (another thread may have rebuilt)
            current_time = datetime.now(timezone.utc)
            if (_METAR_AIRPORT_SPATIAL_INDEX is None or
                _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP is None or
                (current_time - _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP).total_seconds() > _METAR_SPATIAL_INDEX_DURATION):
                _METAR_AIRPORT_SPATIAL_INDEX = _build_metar_airport_spatial_index(airports_data)
                _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP = current_time

    spatial_index = _METAR_AIRPORT_SPATIAL_INDEX
    assert spatial_index is not None, "Spatial index should have been built"
    grid = spatial_index['grid']

    # Determine which cells to search
    # 1 degree of latitude ≈ 60nm, so we need to search enough cells to cover max_distance_nm
    # At mid-latitudes, 1 degree of longitude ≈ 45nm, so use conservative estimate
    # Add 1 to ensure we cover the edges
    cell_radius = int(max_distance_nm / 45) + 1

    lat_cell = int(latitude)
    lon_cell = int(longitude)

    # Build list of cells to search based on max_distance_nm
    search_cells = set()
    for dlat in range(-cell_radius, cell_radius + 1):
        for dlon in range(-cell_radius, cell_radius + 1):
            search_cells.add((lat_cell + dlat, lon_cell + dlon))

    # Determine if we should apply heading bias
    # Only apply if in flight (groundspeed > 40kt) and heading is provided
    use_heading_bias = (
        aircraft_heading is not None and
        (aircraft_groundspeed is None or aircraft_groundspeed > 40)
    )

    # Find all airports within search radius, scored by distance and heading
    candidates = []
    tried_icaos = set()

    for cell_key in search_cells:
        if cell_key not in grid:
            continue

        for airport in grid[cell_key]:
            if airport['icao'] in tried_icaos:
                continue
            tried_icaos.add(airport['icao'])

            distance = haversine_distance_nm(
                latitude, longitude,
                airport['lat'], airport['lon']
            )

            if distance <= max_distance_nm:
                # Calculate score with optional heading bias
                if use_heading_bias and distance > 0 and aircraft_heading is not None:
                    # Calculate bearing from aircraft to airport
                    bearing_to_airport = calculate_bearing(
                        latitude, longitude,
                        airport['lat'], airport['lon']
                    )
                    # Calculate angular difference (0-180 degrees)
                    heading_diff = abs(bearing_to_airport - aircraft_heading)
                    if heading_diff > 180:
                        heading_diff = 360 - heading_diff
                    # Calculate penalty: 0.7 (ahead) to 1.5 (behind)
                    # Formula: 0.7 + 0.8 * (heading_diff / 180)
                    heading_penalty = 0.7 + 0.8 * (heading_diff / 180.0)
                    score = distance * heading_penalty
                else:
                    score = distance

                candidates.append((score, distance, airport['icao']))

    # Sort by score (lowest first - closest/most ahead)
    candidates.sort(key=lambda x: x[0])

    # Try airports in order of score until we find one with METAR
    MAX_ATTEMPTS = 20  # Limit attempts to prevent excessive API calls
    result = None

    for _score, distance, icao in candidates[:MAX_ATTEMPTS]:
        altimeter = get_altimeter_setting(icao)
        if altimeter:
            result = (icao, altimeter, distance)
            break

    # Cache the result (even if None) for this position
    with _NEAREST_METAR_RESULT_CACHE_LOCK:
        _NEAREST_METAR_RESULT_CACHE[cache_key] = {
            'result': result,
            'timestamp': datetime.now(timezone.utc)
        }

    return result


def clear_weather_caches() -> None:
    """Clear all weather-related caches.

    Call this when tracked airports change to ensure fresh data is fetched.
    """
    global _METAR_AIRPORT_SPATIAL_INDEX, _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP

    # Clear module-level spatial index cache
    with _METAR_SPATIAL_INDEX_LOCK:
        _METAR_AIRPORT_SPATIAL_INDEX = None
        _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP = None

    # Clear position-based result cache
    with _NEAREST_METAR_RESULT_CACHE_LOCK:
        _NEAREST_METAR_RESULT_CACHE.clear()

    # Clear the shared caches from cache manager
    wind_data_cache, _wind_blacklist = get_wind_cache()
    wind_lock = get_wind_cache_lock()
    with wind_lock:
        wind_data_cache.clear()
        # Don't clear blacklist - those are permanent 404s

    metar_data_cache, _metar_blacklist = get_metar_cache()
    metar_lock = get_metar_cache_lock()
    with metar_lock:
        metar_data_cache.clear()
        # Don't clear blacklist - those are permanent 404s

    taf_data_cache, _taf_blacklist = get_taf_cache()
    taf_lock = get_taf_cache_lock()
    with taf_lock:
        taf_data_cache.clear()
        # Don't clear blacklist - those are permanent 404s


def get_rate_limit_status() -> Dict[str, Any]:
    """
    Get current rate limiting status for monitoring/logging.

    Returns:
        Dictionary with rate limit state:
        - is_rate_limited: True if currently backing off
        - backoff_seconds: Current backoff delay
        - error_count: Consecutive error count
        - last_error_time: ISO timestamp of last error (or None)
    """
    with _rate_limit_lock:
        is_rate_limited = _rate_limit_backoff > 0 and _rate_limit_last_error_time is not None

        # Check if we've recovered
        if _rate_limit_last_error_time is not None:
            time_since_error = (datetime.now(timezone.utc) - _rate_limit_last_error_time).total_seconds()
            if time_since_error > RATE_LIMIT_RECOVERY_TIME:
                is_rate_limited = False

        return {
            'is_rate_limited': is_rate_limited,
            'backoff_seconds': _rate_limit_backoff if is_rate_limited else 0.0,
            'error_count': _rate_limit_errors,
            'last_error_time': _rate_limit_last_error_time.isoformat() if _rate_limit_last_error_time else None
        }


def reset_rate_limit_state() -> None:
    """Reset rate limiting state (e.g., after a long pause between fetches)."""
    global _rate_limit_backoff, _rate_limit_errors, _rate_limit_last_error_time

    with _rate_limit_lock:
        _rate_limit_backoff = 0.0
        _rate_limit_errors = 0
        _rate_limit_last_error_time = None