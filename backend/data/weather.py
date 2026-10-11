"""
Weather data fetching for airports (METAR and wind information).
"""

import json
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Container
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, NamedTuple

from backend.cache.manager import (
    get_metar_cache,
    get_metar_cache_lock,
    get_metar_negative_cache,
    get_taf_cache,
    get_taf_cache_lock,
    get_wind_cache,
    get_wind_cache_lock,
)
from backend.config.constants import METAR_CACHE_DURATION, WIND_CACHE_DURATION
from backend.core.calculations import calculate_bearing, haversine_distance_nm
from common import logger as debug_logger

# Rate limiting state
_rate_limit_lock = threading.Lock()
_rate_limit_backoff = 0.0  # Current backoff delay in seconds
_rate_limit_errors = 0  # Consecutive rate limit errors
_rate_limit_last_error_time: datetime | None = None

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
                RATE_LIMIT_MAX_BACKOFF,
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
            time_since_error = (
                datetime.now(timezone.utc) - _rate_limit_last_error_time
            ).total_seconds()
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
        time_since_error = (
            datetime.now(timezone.utc) - _rate_limit_last_error_time
        ).total_seconds()
        if time_since_error > RATE_LIMIT_RECOVERY_TIME:
            return 0.0

        return _rate_limit_backoff


def _wait_for_backoff() -> None:
    """Wait for the current backoff delay if rate limiting is active."""
    backoff = _get_current_backoff()
    if backoff > 0:
        time.sleep(backoff)


def _parse_wind_from_observation(properties: dict) -> tuple[bool, str]:
    """
    Parse wind data from a single observation.

    Args:
        properties: Observation properties dictionary from weather.gov API

    Returns:
        (has_data, wind_string) tuple where has_data is True if valid wind data was found
    """
    wind_direction = properties.get("windDirection", {}).get("value")
    wind_speed_kmh = properties.get("windSpeed", {}).get("value")
    wind_gust_kmh = properties.get("windGust", {}).get("value")

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
    cached = _cached_minute_wind(airport_icao)
    if cached is not None:
        return cached

    # Cache miss or expired - fetch new data (outside lock to avoid blocking)
    try:
        wind_str = _fetch_minute_wind(airport_icao)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Station doesn't exist - blacklist it permanently
            _wind_data_cache, wind_blacklist = get_wind_cache()
            with get_wind_cache_lock():
                wind_blacklist[airport_icao] = True
            return ""
        return _last_minute_wind(airport_icao)
    except (
        urllib.error.URLError,
        json.JSONDecodeError,
        KeyError,
        ValueError,
        TimeoutError,
    ):
        return _last_minute_wind(airport_icao)

    # Cache the result (even if empty)
    wind_data_cache, _wind_blacklist = get_wind_cache()
    current_time = datetime.now(timezone.utc)
    with get_wind_cache_lock():
        wind_data_cache[airport_icao] = {
            "wind_info": wind_str,
            "timestamp": current_time,
        }

    return wind_str


def _cached_minute_wind(airport_icao: str) -> str | None:
    """
    Answer get_wind_info_minute from the wind caches when they can.

    Returns:
        "" for a blacklisted airport, the cached wind when younger than
        WIND_CACHE_DURATION, or None when the wind must be fetched.
    """
    wind_data_cache, wind_blacklist = get_wind_cache()
    with get_wind_cache_lock():
        if airport_icao in wind_blacklist:
            return ""

        current_time = datetime.now(timezone.utc)
        cache_entry = wind_data_cache.get(airport_icao)
        if cache_entry is None:
            return None
        time_since_cache = (current_time - cache_entry["timestamp"]).total_seconds()
        if time_since_cache < WIND_CACHE_DURATION:
            return cache_entry["wind_info"]
        return None


def _last_minute_wind(airport_icao: str) -> str:
    """The airport's cached minute wind even if expired, or "" without one."""
    wind_data_cache, _wind_blacklist = get_wind_cache()
    with get_wind_cache_lock():
        if airport_icao in wind_data_cache:
            return wind_data_cache[airport_icao]["wind_info"]
    return ""


def _read_weather_gov_json(url: str) -> dict:
    """GET a weather.gov API URL and decode its JSON body."""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "VATSIM-Control-Recs/1.0")

    with urllib.request.urlopen(req, timeout=3) as response:
        return json.loads(response.read().decode())


def _fetch_minute_wind(airport_icao: str) -> str:
    """
    Fetch the airport's current wind from weather.gov observations.

    Tries the latest observation first; when it has no wind, the last 30
    observations, taking the first one with wind.

    Returns:
        Formatted wind string, or "" when no observation has wind

    Raises:
        urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError or
        TimeoutError when a request or its parsing fails
    """
    stations_url = f"https://api.weather.gov/stations/{airport_icao}/observations"
    data = _read_weather_gov_json(f"{stations_url}/latest")
    has_data, wind_str = _parse_wind_from_observation(data.get("properties", {}))
    if has_data:
        return wind_str

    data = _read_weather_gov_json(f"{stations_url}?limit=30")
    for feature in data.get("features", []):
        has_data, wind_str = _parse_wind_from_observation(feature.get("properties", {}))
        if has_data:
            return wind_str
    return ""


def _fetch_metar_from_aviationweather(airport_icao: str) -> tuple[str | None, bool]:
    """
    Fetch METAR from aviationweather.gov API.

    Returns:
        (metar, answered): metar is the METAR string, empty string if no data, or None
        when the station should be blacklisted; answered is True when the server
        responded successfully, so an empty metar is a clean "no METAR" answer rather
        than a timeout, rate limit or other error.
    """
    try:
        # Wait for backoff if rate limiting is active
        _wait_for_backoff()

        url = (
            f"https://aviationweather.gov/api/data/metar?ids={airport_icao}&format=raw"
        )
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "VATSIM-Control-Recs/1.0")

        with urllib.request.urlopen(req, timeout=5) as response:
            metar_text = response.read().decode("utf-8").strip()

        # Record successful request (may reset backoff after recovery period)
        _record_successful_request()

        if not metar_text:
            # Empty response (e.g., HTTP 204) - return empty to trigger fallback
            return "", True

        if metar_text.startswith(("No METAR", "Error")):
            # Explicit error - return None to indicate blacklist
            return None, True

        return metar_text, True

    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, True  # Station doesn't exist - blacklist
        if _check_rate_limit_error(e.code):
            backoff = _record_rate_limit_error()

            debug_logger.debug(
                f"Rate limit detected (HTTP {e.code}) for METAR {airport_icao}, backoff: {backoff:.1f}s"
            )
        return "", False  # Other HTTP errors - try fallback
    except (urllib.error.URLError, TimeoutError):
        return "", False  # Network error - try fallback
    except Exception:
        return "", False  # Other errors - try fallback


def _fetch_metar_from_vatsim(airport_icao: str) -> tuple[str, bool]:
    """
    Fetch METAR from VATSIM METAR API (fallback).

    Returns:
        (metar, answered): metar is the METAR string or empty string if unavailable;
        answered is True when the server responded with HTTP 200, so an empty metar
        is a clean "no METAR" answer rather than an error.
    """
    url = f"https://metar.vatsim.net/{airport_icao}"
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "VATSIM-Control-Recs/1.0")

        with urllib.request.urlopen(req, timeout=5) as response:
            status = response.status
            metar_text = response.read().decode("utf-8").strip()

    except Exception as e:
        debug_logger.debug(f"VATSIM METAR fetch failed for {airport_icao} ({url}): {e}")
        return "", False

    if status != 200:
        return "", False

    if not metar_text or metar_text.startswith("No METAR"):
        return "", True

    return metar_text, True


def get_metar(airport_icao: str, *, as_of: datetime | None = None) -> str:
    """
    Fetch current METAR with caching.

    Uses aviationweather.gov as primary source, with VATSIM METAR API as fallback.
    METAR data is cached for 60 seconds to avoid excessive API calls.
    When fetching METAR, wind and altimeter are also parsed and cached
    to avoid redundant parsing when both values are needed.

    This function is thread-safe.

    Args:
        airport_icao: The ICAO code of the airport
        as_of: Judge cache freshness as of this moment (the refresh's box decision)
            instead of now

    Returns:
        Full METAR string or empty string if unavailable
    """
    _metar_data_cache, metar_blacklist = get_metar_cache()
    metar_lock = get_metar_cache_lock()

    with metar_lock:
        cached = _cached_metar(airport_icao, _resolve_now(as_of))
    if cached is not None:
        return cached

    # Cache miss or expired - fetch new data (outside lock to avoid blocking)
    # Try primary source first
    metar_text, answered = _fetch_metar_from_aviationweather(airport_icao)

    if metar_text is None:
        # Station doesn't exist - blacklist it permanently
        with metar_lock:
            metar_blacklist[airport_icao] = True
        return ""

    # A negative is cached only when both servers answered cleanly with no METAR
    clean_empty = False

    # If primary returned empty, try VATSIM fallback
    if metar_text == "":
        metar_text, vatsim_answered = _fetch_metar_from_vatsim(airport_icao)
        clean_empty = answered and vatsim_answered

    current_time = datetime.now(timezone.utc)
    if not metar_text:
        return _no_metar_result(airport_icao, clean_empty, current_time)

    with metar_lock:
        _store_metar(airport_icao, metar_text, current_time)

    return metar_text


def _is_fresh(timestamp: datetime, now: datetime) -> bool:
    """Whether a METAR cache timestamp is younger than METAR_CACHE_DURATION."""
    return (now - timestamp).total_seconds() < METAR_CACHE_DURATION


def _has_fresh_negative(airport_icao: str, now: datetime) -> bool:
    """Whether a clean "no METAR" answer for the airport is still fresh. Caller holds the METAR lock."""
    negative_time = get_metar_negative_cache().get(airport_icao)
    return negative_time is not None and _is_fresh(negative_time, now)


def _resolve_now(as_of: datetime | None) -> datetime:
    """The moment freshness is judged at: the given as_of, else the current time."""
    return as_of if as_of is not None else datetime.now(timezone.utc)


def _usable_entry(airport_icao: str, now: datetime) -> dict[str, Any] | None:
    """
    The airport's METAR cache entry when it can answer a read without a fetch:
    fresh, or stale while a fresh "no METAR" negative stands. Caller holds the
    METAR lock. None means the entry is missing or must be refetched.
    """
    metar_data_cache, _metar_blacklist = get_metar_cache()
    cache_entry = metar_data_cache.get(airport_icao)
    if cache_entry is None:
        return None
    if _is_fresh(cache_entry["timestamp"], now) or _has_fresh_negative(
        airport_icao, now
    ):
        return cache_entry
    return None


def _cached_metar(airport_icao: str, now: datetime) -> str | None:
    """
    Answer get_metar from the caches when they can. Caller holds the METAR lock.

    A fresh METAR entry is returned as is. A stale entry is returned without a
    fetch while a fresh "no METAR" negative stands (a stale METAR keeps showing);
    otherwise it is refetched. With no entry, a fresh negative answers "".

    Returns:
        The cached answer ("" for a blacklisted airport or a fresh negative), or
        None when the METAR must be fetched.
    """
    _metar_data_cache, metar_blacklist = get_metar_cache()
    if airport_icao in metar_blacklist:
        return ""

    entry = _usable_entry(airport_icao, now)
    if entry is not None:
        return entry["metar"]

    return "" if _has_fresh_negative(airport_icao, now) else None


def _store_metar(airport_icao: str, metar_text: str, now: datetime) -> None:
    """Cache a METAR with its parsed wind and altimeter. Caller holds the METAR lock."""
    metar_data_cache, _metar_blacklist = get_metar_cache()
    metar_data_cache[airport_icao] = {
        "metar": metar_text,
        "wind": _parse_wind_from_metar(metar_text),
        "altimeter": parse_altimeter_from_metar(metar_text),
        "timestamp": now,
    }
    get_metar_negative_cache().pop(airport_icao, None)


def _no_metar_result(airport_icao: str, clean_empty: bool, now: datetime) -> str:
    """
    Settle a fetch that found no METAR: record a clean empty as a negative and
    return the stale cached METAR if one exists, else "".
    """
    metar_data_cache, _metar_blacklist = get_metar_cache()
    with get_metar_cache_lock():
        if clean_empty:
            get_metar_negative_cache()[airport_icao] = now
        cache_entry = metar_data_cache.get(airport_icao)
        return cache_entry["metar"] if cache_entry is not None else ""


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
    cached = _cached_taf(airport_icao)
    if cached is not None:
        return cached

    # Cache miss or expired - fetch new data (outside lock to avoid blocking)
    try:
        taf_text = _fetch_taf_text(airport_icao)
    except urllib.error.HTTPError as e:
        return _taf_after_http_error(airport_icao, e)
    except (
        urllib.error.URLError,
        json.JSONDecodeError,
        KeyError,
        ValueError,
        TimeoutError,
    ) as e:
        # Expected network/parsing errors - temporary, don't blacklist
        debug_logger.debug(
            f"Expected error fetching TAF for {airport_icao}: {type(e).__name__}: {e}"
        )
        return _last_taf(airport_icao)
    except Exception as e:
        debug_logger.error(
            f"Unexpected error fetching TAF for {airport_icao}: {type(e).__name__}: {e}",
            exc_info=True,
        )
        return _last_taf(airport_icao)

    return _settle_taf(airport_icao, taf_text)


def _cached_taf(airport_icao: str) -> str | None:
    """
    Answer get_taf from the TAF caches when they can.

    Returns:
        "" for a blacklisted airport, the cached TAF when younger than
        METAR_CACHE_DURATION, or None when the TAF must be fetched.
    """
    taf_data_cache, taf_blacklist = get_taf_cache()
    with get_taf_cache_lock():
        if airport_icao in taf_blacklist:
            return ""

        current_time = datetime.now(timezone.utc)
        cache_entry = taf_data_cache.get(airport_icao)
        if cache_entry is None:
            return None
        time_since_cache = (current_time - cache_entry["timestamp"]).total_seconds()
        if time_since_cache < METAR_CACHE_DURATION:
            return cache_entry["taf"]
        return None


def _last_taf(airport_icao: str) -> str:
    """The airport's cached TAF even if expired, or "" without one."""
    taf_data_cache, _taf_blacklist = get_taf_cache()
    with get_taf_cache_lock():
        if airport_icao in taf_data_cache:
            return taf_data_cache[airport_icao]["taf"]
    return ""


def _fetch_taf_text(airport_icao: str) -> str:
    """Request the airport's raw TAF from aviationweather.gov, after any rate-limit backoff."""
    _wait_for_backoff()

    url = f"https://aviationweather.gov/api/data/taf?ids={airport_icao}&format=raw"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "VATSIM-Control-Recs/1.0")

    with urllib.request.urlopen(req, timeout=5) as response:
        taf_text = response.read().decode("utf-8").strip()

    # Record successful request (may reset backoff after recovery period)
    _record_successful_request()
    return taf_text


def _taf_after_http_error(airport_icao: str, error: urllib.error.HTTPError) -> str:
    """
    Settle a TAF request that failed with an HTTP error.

    A 404 blacklists the airport; a rate-limit status records the error and
    raises the backoff. Any status but 404 answers the cached TAF, even if expired.
    """
    if error.code == 404:
        # Station doesn't exist - blacklist it permanently
        _taf_data_cache, taf_blacklist = get_taf_cache()
        with get_taf_cache_lock():
            taf_blacklist[airport_icao] = True
        return ""
    if _check_rate_limit_error(error.code):
        backoff = _record_rate_limit_error()

        debug_logger.debug(
            f"Rate limit detected (HTTP {error.code}) for TAF {airport_icao}, backoff: {backoff:.1f}s"
        )
    return _last_taf(airport_icao)


def _settle_taf(airport_icao: str, taf_text: str) -> str:
    """
    Settle a TAF answer: cache a TAF, blacklist an explicit "No TAF"/"Error"
    reply, and leave an empty reply (e.g. HTTP 204) uncached.
    """
    if not taf_text:
        return ""

    taf_data_cache, taf_blacklist = get_taf_cache()
    if taf_text.startswith(("No TAF", "Error")):
        # Explicit error message - station likely doesn't report TAF, blacklist it
        with get_taf_cache_lock():
            taf_blacklist[airport_icao] = True
        return ""

    current_time = datetime.now(timezone.utc)
    with get_taf_cache_lock():
        taf_data_cache[airport_icao] = {"taf": taf_text, "timestamp": current_time}

    return taf_text


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

    match = re.search(_METAR_WIND_PATTERN, metar)
    if not match:
        return ""

    direction, speed, gust, units = match.groups()
    if direction != "VRB" and int(speed) == 0:
        return "00000KT"

    units_per_knot = _WIND_UNITS_PER_KNOT.get(units)
    if units_per_knot is None:
        return f"{direction}{speed}{gust or ''}KT"
    return _wind_in_knots(direction, speed, gust, units_per_knot)


# Direction (3 digits or VRB), speed (2-3 digits), optional gust (G + 2-3 digits), units
_METAR_WIND_PATTERN = r"\b(\d{3}|VRB)(\d{2,3})(G\d{2,3})?(KT|KMH|KPH|MPS)\b"

# How many of each non-knot METAR wind unit make one knot
_WIND_UNITS_PER_KNOT = {"KMH": 1.852, "KPH": 1.852, "MPS": 0.514444}


def _wind_in_knots(
    direction: str, speed: str, gust: str | None, units_per_knot: float
) -> str:
    """
    Format a METAR wind group given in another unit as knots.

    Args:
        direction: Three-digit direction or "VRB"
        speed: Speed digits in the original unit
        gust: Gust group with its "G" prefix, or None
        units_per_knot: How many of the original unit make one knot

    Returns:
        Wind string like "27011G22KT"
    """
    speed_kt = round(int(speed) / units_per_knot)
    wind_str = f"{direction}{speed_kt:02d}"
    if gust:
        gust_kt = round(int(gust[1:]) / units_per_knot)
        wind_str += f"G{gust_kt:02d}"
    return wind_str + "KT"


def get_wind_from_metar(airport_icao: str, *, as_of: datetime | None = None) -> str:
    """
    Extract wind information from METAR.
    Uses cached parsed wind if available to avoid redundant parsing.

    This function is thread-safe.

    Args:
        airport_icao: The ICAO code of the airport
        as_of: Judge cache freshness as of this moment (the refresh's box decision)
            instead of now

    Returns:
        Wind string in format like "27005KT" or "27005G12KT" or "00000KT" or empty string if unavailable
    """
    metar_data_cache, _metar_blacklist = get_metar_cache()
    metar_lock = get_metar_cache_lock()

    # Check if we have cached parsed wind data
    with metar_lock:
        entry = _usable_entry(airport_icao, _resolve_now(as_of))
        if entry is not None:
            return entry.get("wind", "")

    # Cache miss or expired - fetch METAR (which will parse and cache wind)
    metar = get_metar(airport_icao, as_of=as_of)
    if not metar:
        return ""

    # After get_metar, check cache again for parsed wind
    with metar_lock:
        if airport_icao in metar_data_cache:
            return metar_data_cache[airport_icao].get("wind", "")

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


def get_wind_info_batch(
    airport_icaos: list[str], source: str = "metar", max_workers: int = 10
) -> dict[str, str]:
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
            executor.submit(get_wind_info, icao, source): icao for icao in airport_icaos
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
    airport_icaos: list[str],
    max_workers: int = 10,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, str]:
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
            executor.submit(get_metar, icao): icao for icao in airport_icaos
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
    airport_icaos: list[str],
    max_workers: int = 10,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, str]:
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
            executor.submit(get_taf, icao): icao for icao in airport_icaos
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
    bbox: tuple[float, float, float, float], include_taf: bool = True, timeout: int = 30
) -> tuple[dict[str, str], dict[str, str]]:
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
    result = _fetch_weather_bbox_result(bbox, include_taf, timeout)
    return (result.metars, result.tafs)


# aviationweather.gov returns at most 400 entries per response (aviationweather.gov/data/api,
# Restrictions), so a response that size may be truncated.
BBOX_MAX_ENTRIES = 400


class BboxFetchResult(NamedTuple):
    """One bbox request's METARs and TAFs, whether it succeeded, and its raw entry count."""

    metars: dict[str, str]
    tafs: dict[str, str]
    succeeded: bool
    entry_count: int


_FAILED_BBOX_FETCH = BboxFetchResult({}, {}, False, 0)


def _parse_bbox_entries(data: list) -> tuple[dict[str, str], dict[str, str]]:
    """Split a bbox JSON response into (metars, tafs) keyed by ICAO."""
    metars: dict[str, str] = {}
    tafs: dict[str, str] = {}
    for entry in data:
        icao = entry.get("icaoId", "")
        raw_metar = entry.get("rawOb", "")
        raw_taf = entry.get("rawTaf", "")

        if icao and raw_metar:
            metars[icao] = raw_metar
        if icao and raw_taf:
            tafs[icao] = raw_taf
    return metars, tafs


def _fetch_weather_bbox_result(
    bbox: tuple[float, float, float, float], include_taf: bool, timeout: int
) -> BboxFetchResult:
    """
    Fetch a bbox like fetch_weather_bbox, also reporting success and the entry count.

    An empty body (HTTP 204, no stations in the box) is a success with no entries;
    an HTTP error, network error or a response that is not a JSON array is a failure.
    """
    min_lat, min_lon, max_lat, max_lon = bbox
    bbox_str = f"{min_lat},{min_lon},{max_lat},{max_lon}"

    # Use taf=true to get both METAR and TAF in one call
    taf_param = "&taf=true" if include_taf else ""
    url = f"https://aviationweather.gov/api/data/metar?bbox={bbox_str}&format=json{taf_param}"

    try:
        # Wait for backoff if rate limiting is active
        _wait_for_backoff()

        req = urllib.request.Request(url)
        req.add_header("User-Agent", "VATSIM-Control-Recs/1.0")

        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8").strip()

        # Record successful request
        _record_successful_request()

        if not body:
            return BboxFetchResult({}, {}, True, 0)

        # Parse response - it's an array of METAR objects
        data = json.loads(body)
        if not isinstance(data, list):
            debug_logger.warning(
                f"Bbox weather response for {bbox_str} is not a JSON array"
            )
            return _FAILED_BBOX_FETCH

        metars, tafs = _parse_bbox_entries(data)
        return BboxFetchResult(metars, tafs, True, len(data))

    except urllib.error.HTTPError as e:
        if _check_rate_limit_error(e.code):
            _record_rate_limit_error()
        debug_logger.warning(
            f"Bbox weather fetch failed for {bbox_str} ({url}): HTTP {e.code}"
        )
        return _FAILED_BBOX_FETCH
    except Exception as e:
        debug_logger.warning(f"Bbox weather fetch failed for {bbox_str} ({url}): {e}")
        return _FAILED_BBOX_FETCH


def get_weather_batch_bbox(
    artcc_bboxes: dict[str, tuple[float, float, float, float]],
    target_airports: list[str] | None = None,
    max_workers: int = 5,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
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
    all_metars: dict[str, str] = {}
    all_tafs: dict[str, str] = {}

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
            try:
                metars, tafs = future.result()
                all_metars.update(metars)
                all_tafs.update(tafs)
            except Exception as e:
                # Individual ARTCC failure doesn't stop others
                debug_logger.warning(f"ARTCC weather fetch failed: {e}")

            completed += 1
            if progress_callback:
                progress_callback(completed, total)

    # Filter to target airports if specified
    if target_airports is not None:
        target_set = {a.upper() for a in target_airports}
        all_metars = {k: v for k, v in all_metars.items() if k.upper() in target_set}
        all_tafs = {k: v for k, v in all_tafs.items() if k.upper() in target_set}

    return (all_metars, all_tafs)


def parse_altimeter_from_metar(metar: str) -> str | None:
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
    altimeter_pattern = r"\b([AQ]\d{4})\b"
    match = re.search(altimeter_pattern, metar)

    if match:
        return match.group(1)

    return None


def get_altimeter_setting(
    airport_icao: str, *, as_of: datetime | None = None
) -> str | None:
    """
    Get altimeter setting for an airport from its METAR.
    Uses cached parsed altimeter if available to avoid redundant parsing.

    This function is thread-safe.

    Args:
        airport_icao: The ICAO code of the airport
        as_of: Judge cache freshness as of this moment (the refresh's box decision)
            instead of now

    Returns:
        Altimeter string (e.g., "A2992" or "Q1013") or None if unavailable
    """
    metar_data_cache, _metar_blacklist = get_metar_cache()
    metar_lock = get_metar_cache_lock()

    # Check if we have cached parsed altimeter data
    with metar_lock:
        entry = _usable_entry(airport_icao, _resolve_now(as_of))
        if entry is not None:
            return entry.get("altimeter")

    # Cache miss or expired - fetch METAR (which will parse and cache altimeter)
    metar = get_metar(airport_icao, as_of=as_of)
    if not metar:
        return None

    # After get_metar, check cache again for parsed altimeter
    with metar_lock:
        if airport_icao in metar_data_cache:
            return metar_data_cache[airport_icao].get("altimeter")

    # Fallback: parse directly if cache doesn't have it for some reason
    return parse_altimeter_from_metar(metar)


def cached_wind_altimeter(
    airport_icao: str, *, as_of: datetime | None = None
) -> tuple[str, str] | None:
    """
    The airport's (wind, altimeter) when the caches can answer without a fetch.

    Answers exactly what get_wind_from_metar and get_altimeter_setting would answer
    from the caches: a usable METAR entry's parsed values, or ("", "") for a
    blacklisted airport or a fresh "no METAR" negative. Never fetches.

    This function is thread-safe.

    Args:
        airport_icao: The ICAO code of the airport
        as_of: Judge cache freshness as of this moment instead of now

    Returns:
        (wind, altimeter), "" where the METAR has none, or None when only a fetch
        could answer
    """
    now = _resolve_now(as_of)
    with get_metar_cache_lock():
        entry = _usable_entry(airport_icao, now)
        if entry is not None:
            return entry.get("wind") or "", entry.get("altimeter") or ""
        if _cached_metar(airport_icao, now) == "":
            return "", ""
    return None


# Global cache for spatial index of airports with METAR
_METAR_AIRPORT_SPATIAL_INDEX: dict[str, Any] | None = None
_METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP: datetime | None = None
_METAR_SPATIAL_INDEX_DURATION = 300  # 5 minutes cache
_METAR_SPATIAL_INDEX_LOCK = (
    threading.Lock()
)  # Lock for synchronizing spatial index rebuild

# Position-based result cache for find_nearest_airport_with_metar
# Key: (rounded_lat, rounded_lon) tuple, Value: {'result': tuple or None, 'timestamp': datetime}
_NEAREST_METAR_RESULT_CACHE: dict[tuple[float, float], dict[str, Any]] = {}
_NEAREST_METAR_RESULT_CACHE_DURATION = (
    60  # 1 minute cache (matches METAR cache duration)
)
_NEAREST_METAR_RESULT_CACHE_LOCK = threading.Lock()
_NEAREST_METAR_POSITION_GRID_SIZE = 0.1  # ~6nm grid for position rounding

# Persisted spatial cache (loaded from disk once at startup)
_PERSISTED_SPATIAL_CACHE: dict[str, Any] | None = None
_PERSISTED_SPATIAL_CACHE_LOADED = False
_KNOWN_METAR_STATIONS: set | None = None  # Whitelist of airports known to have METAR


def _load_persisted_spatial_cache() -> dict[str, Any] | None:
    """
    Load persisted spatial cache from disk if available.

    The cache file is generated by scripts/precalculate_airport_spatial_data.py
    and contains the spatial grid and known METAR stations.

    Returns:
        Cache data dictionary or None if not available
    """
    global \
        _PERSISTED_SPATIAL_CACHE, \
        _PERSISTED_SPATIAL_CACHE_LOADED, \
        _KNOWN_METAR_STATIONS

    if _PERSISTED_SPATIAL_CACHE_LOADED:
        return _PERSISTED_SPATIAL_CACHE

    _PERSISTED_SPATIAL_CACHE_LOADED = True

    try:
        # Find the cache file relative to this module
        import os

        module_dir = os.path.dirname(os.path.abspath(__file__))
        cache_file = os.path.join(
            module_dir, "..", "..", "data", "airport_spatial_cache.json"
        )
        cache_file = os.path.normpath(cache_file)

        if not os.path.exists(cache_file):
            return None

        with open(cache_file, encoding="utf-8") as f:
            cache_data = json.load(f)

        # Validate version
        if cache_data.get("version") != 1:
            return None

        _PERSISTED_SPATIAL_CACHE = cache_data

        # Load known METAR stations as a set for fast lookup
        metar_stations = cache_data.get("metar_stations")
        if metar_stations:
            _KNOWN_METAR_STATIONS = set(metar_stations)

        return cache_data

    except Exception:
        return None


def _build_metar_airport_spatial_index(
    airports_data: dict[str, dict[str, Any]],
) -> dict[str, Any]:
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

    if persisted_cache and "spatial_grid" in persisted_cache:
        valid_airports, spatial_grid = _metar_grid_from_persisted(
            persisted_cache["spatial_grid"], metar_blacklist
        )
    else:
        valid_airports = _metar_airports_from_data(airports_data, metar_blacklist)
        spatial_grid = _one_degree_grid(valid_airports)

    return {
        "grid": spatial_grid,
        "airports": valid_airports,
        "cell_size": 1.0,  # degrees
    }


def _may_have_metar(icao: str, metar_blacklist: Container[str]) -> bool:
    """Whether the airport is neither blacklisted at runtime nor outside a known-METAR whitelist."""
    if icao in metar_blacklist:
        return False
    return not (_KNOWN_METAR_STATIONS and icao not in _KNOWN_METAR_STATIONS)


def _metar_grid_from_persisted(
    persisted_grid: dict[str, list[dict[str, Any]]], metar_blacklist: Container[str]
) -> tuple[list[dict[str, Any]], dict[tuple[int, int], list[dict[str, Any]]]]:
    """
    Filter a persisted spatial grid ("lat,lon" cell keys) to the airports that may have a METAR.

    Returns:
        (airports, grid): the kept airports in grid order, and the grid keyed by
        (lat_cell, lon_cell) holding only cells with a kept airport
    """
    valid_airports = []
    spatial_grid: dict[tuple[int, int], list[dict[str, Any]]] = {}

    for cell_key_str, airports in persisted_grid.items():
        parts = cell_key_str.split(",")
        cell_key = (int(parts[0]), int(parts[1]))

        for airport in airports:
            if not _may_have_metar(airport["icao"], metar_blacklist):
                continue
            valid_airports.append(airport)
            spatial_grid.setdefault(cell_key, []).append(airport)

    return valid_airports, spatial_grid


def _metar_airports_from_data(
    airports_data: dict[str, dict[str, Any]], metar_blacklist: Container[str]
) -> list[dict[str, Any]]:
    """The airports with coordinates that may have a METAR, as {"icao", "lat", "lon"} dicts."""
    valid_airports = []
    for icao, data in airports_data.items():
        if data.get("latitude") is None or data.get("longitude") is None:
            continue
        if not _may_have_metar(icao, metar_blacklist):
            continue
        valid_airports.append(
            {"icao": icao, "lat": data["latitude"], "lon": data["longitude"]}
        )
    return valid_airports


def _one_degree_grid(
    airports: list[dict[str, Any]],
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    """Group airports into 1-degree cells keyed by (int(lat), int(lon))."""
    spatial_grid: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for airport in airports:
        cell_key = (int(airport["lat"]), int(airport["lon"]))
        spatial_grid.setdefault(cell_key, []).append(airport)
    return spatial_grid


def find_airports_near_position(
    latitude: float,
    longitude: float,
    airports_data: dict[str, dict[str, Any]],
    radius_nm: float = 50.0,
    max_results: int = 5,
) -> list[str]:
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
        _METAR_AIRPORT_SPATIAL_INDEX is None
        or _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP is None
        or (current_time - _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP).total_seconds()
        > _METAR_SPATIAL_INDEX_DURATION
    )

    if needs_rebuild:
        with _METAR_SPATIAL_INDEX_LOCK:
            current_time = datetime.now(timezone.utc)
            if (
                _METAR_AIRPORT_SPATIAL_INDEX is None
                or _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP is None
                or (
                    current_time - _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP
                ).total_seconds()
                > _METAR_SPATIAL_INDEX_DURATION
            ):
                _METAR_AIRPORT_SPATIAL_INDEX = _build_metar_airport_spatial_index(
                    airports_data
                )
                _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP = current_time

    spatial_index = _METAR_AIRPORT_SPATIAL_INDEX
    assert spatial_index is not None, "Spatial index should have been built"
    grid = spatial_index["grid"]

    # Determine which cells to search
    lat_cell = int(latitude)
    lon_cell = int(longitude)

    # Search current cell and adjacent cells (3x3 grid)
    search_cells = [
        (lat_cell + dlat, lon_cell + dlon) for dlat in [-1, 0, 1] for dlon in [-1, 0, 1]
    ]

    # Find airports within radius
    candidates = []

    for cell_key in search_cells:
        if cell_key not in grid:
            continue

        for airport in grid[cell_key]:
            distance = haversine_distance_nm(
                latitude, longitude, airport["lat"], airport["lon"]
            )

            if distance <= radius_nm:
                candidates.append((distance, airport["icao"]))

    # Sort by distance and return top results
    candidates.sort(key=lambda x: x[0])
    return [icao for _, icao in candidates[:max_results]]


def find_nearest_airport_with_metar(
    latitude: float,
    longitude: float,
    airports_data: dict[str, dict[str, Any]],
    max_distance_nm: float = 100.0,
    aircraft_heading: float | None = None,
    aircraft_groundspeed: float | None = None,
) -> tuple[str, str, float] | None:
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
    current_time = datetime.now(timezone.utc)
    cache_key = _nearest_metar_cache_key(latitude, longitude)

    # Check result cache first (fast path)
    found, cached_result = _cached_nearest_metar(cache_key, current_time)
    if found:
        return cached_result

    grid = _current_metar_spatial_index(airports_data, current_time)["grid"]

    # Only apply heading bias if in flight (groundspeed > 40kt) and heading is provided
    use_heading_bias = aircraft_heading is not None and (
        aircraft_groundspeed is None or aircraft_groundspeed > 40
    )
    candidates = _nearest_metar_candidates(
        (latitude, longitude),
        grid,
        max_distance_nm,
        aircraft_heading if use_heading_bias else None,
    )
    result = _first_with_altimeter(candidates)

    # Cache the result (even if None) for this position
    with _NEAREST_METAR_RESULT_CACHE_LOCK:
        _NEAREST_METAR_RESULT_CACHE[cache_key] = {
            "result": result,
            "timestamp": datetime.now(timezone.utc),
        }

    return result


# Limit on airports tried for a METAR per nearest-airport search, to bound API calls
_NEAREST_METAR_MAX_ATTEMPTS = 20


def _nearest_metar_cache_key(latitude: float, longitude: float) -> tuple[float, float]:
    """The position rounded to the ~6nm result-cache grid."""
    rounded_lat = (
        round(latitude / _NEAREST_METAR_POSITION_GRID_SIZE)
        * _NEAREST_METAR_POSITION_GRID_SIZE
    )
    rounded_lon = (
        round(longitude / _NEAREST_METAR_POSITION_GRID_SIZE)
        * _NEAREST_METAR_POSITION_GRID_SIZE
    )
    return (rounded_lat, rounded_lon)


def _cached_nearest_metar(
    cache_key: tuple[float, float], now: datetime
) -> tuple[bool, tuple[str, str, float] | None]:
    """
    Look up a nearest-METAR result younger than its cache duration.

    Returns:
        (found, result): found is False on a miss; result may be None on a hit,
        a cached "no airport found"
    """
    with _NEAREST_METAR_RESULT_CACHE_LOCK:
        cache_entry = _NEAREST_METAR_RESULT_CACHE.get(cache_key)
        if cache_entry is None:
            return False, None
        time_since_cache = (now - cache_entry["timestamp"]).total_seconds()
        if time_since_cache < _NEAREST_METAR_RESULT_CACHE_DURATION:
            return True, cache_entry["result"]
    return False, None


def _metar_spatial_index_expired(now: datetime) -> bool:
    """Whether the METAR airport spatial index is missing or older than its duration."""
    return (
        _METAR_AIRPORT_SPATIAL_INDEX is None
        or _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP is None
        or (now - _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP).total_seconds()
        > _METAR_SPATIAL_INDEX_DURATION
    )


def _current_metar_spatial_index(
    airports_data: dict[str, dict[str, Any]], now: datetime
) -> dict[str, Any]:
    """The METAR airport spatial index, rebuilt first when expired (double-checked locking)."""
    global _METAR_AIRPORT_SPATIAL_INDEX, _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP

    if _metar_spatial_index_expired(now):
        with _METAR_SPATIAL_INDEX_LOCK:
            # Re-check inside lock (another thread may have rebuilt)
            current_time = datetime.now(timezone.utc)
            if _metar_spatial_index_expired(current_time):
                _METAR_AIRPORT_SPATIAL_INDEX = _build_metar_airport_spatial_index(
                    airports_data
                )
                _METAR_AIRPORT_SPATIAL_INDEX_TIMESTAMP = current_time

    spatial_index = _METAR_AIRPORT_SPATIAL_INDEX
    assert spatial_index is not None, "Spatial index should have been built"
    return spatial_index


def _nearest_metar_search_cells(
    latitude: float, longitude: float, max_distance_nm: float
) -> set[tuple[int, int]]:
    """
    The 1-degree cells around a position that can hold an airport within max_distance_nm.

    1 degree of latitude is about 60nm and, at mid-latitudes, 1 degree of longitude
    about 45nm, so the radius in cells uses the conservative 45nm plus one cell.
    """
    cell_radius = int(max_distance_nm / 45) + 1

    lat_cell = int(latitude)
    lon_cell = int(longitude)

    search_cells = set()
    for dlat in range(-cell_radius, cell_radius + 1):
        for dlon in range(-cell_radius, cell_radius + 1):
            search_cells.add((lat_cell + dlat, lon_cell + dlon))
    return search_cells


def _heading_biased_score(
    position: tuple[float, float],
    airport: dict[str, Any],
    distance: float,
    bias_heading: float | None,
) -> float:
    """
    The airport's distance, scaled by a heading penalty when bias_heading is given.

    The penalty runs from 0.7 for an airport dead ahead to 1.5 for one dead behind:
    0.7 + 0.8 * (heading_diff / 180). An airport at distance 0 is never penalised.
    """
    if bias_heading is None or distance <= 0:
        return distance

    latitude, longitude = position
    bearing_to_airport = calculate_bearing(
        latitude, longitude, airport["lat"], airport["lon"]
    )
    heading_diff = abs(bearing_to_airport - bias_heading)
    if heading_diff > 180:
        heading_diff = 360 - heading_diff
    heading_penalty = 0.7 + 0.8 * (heading_diff / 180.0)
    return distance * heading_penalty


def _nearest_metar_candidates(
    position: tuple[float, float],
    grid: dict[tuple[int, int], list[dict[str, Any]]],
    max_distance_nm: float,
    bias_heading: float | None,
) -> list[tuple[float, float, str]]:
    """
    The grid's airports within max_distance_nm of the position, best score first.

    Returns:
        (score, distance_nm, icao) tuples sorted by score, lowest (closest or
        most ahead) first
    """
    latitude, longitude = position
    candidates = []
    tried_icaos = set()

    for cell_key in _nearest_metar_search_cells(latitude, longitude, max_distance_nm):
        for airport in grid.get(cell_key, ()):
            if airport["icao"] in tried_icaos:
                continue
            tried_icaos.add(airport["icao"])

            distance = haversine_distance_nm(
                latitude, longitude, airport["lat"], airport["lon"]
            )
            if distance <= max_distance_nm:
                score = _heading_biased_score(position, airport, distance, bias_heading)
                candidates.append((score, distance, airport["icao"]))

    candidates.sort(key=lambda x: x[0])
    return candidates


def _first_with_altimeter(
    candidates: list[tuple[float, float, str]],
) -> tuple[str, str, float] | None:
    """The first of the leading candidates with an altimeter setting, as (icao, altimeter, distance_nm)."""
    for _score, distance, icao in candidates[:_NEAREST_METAR_MAX_ATTEMPTS]:
        altimeter = get_altimeter_setting(icao)
        if altimeter:
            return (icao, altimeter, distance)
    return None


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
        get_metar_negative_cache().clear()
        # Don't clear blacklist - those are permanent 404s

    taf_data_cache, _taf_blacklist = get_taf_cache()
    taf_lock = get_taf_cache_lock()
    with taf_lock:
        taf_data_cache.clear()
        # Don't clear blacklist - those are permanent 404s


def get_rate_limit_status() -> dict[str, Any]:
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
        is_rate_limited = (
            _rate_limit_backoff > 0 and _rate_limit_last_error_time is not None
        )

        # Check if we've recovered
        if _rate_limit_last_error_time is not None:
            time_since_error = (
                datetime.now(timezone.utc) - _rate_limit_last_error_time
            ).total_seconds()
            if time_since_error > RATE_LIMIT_RECOVERY_TIME:
                is_rate_limited = False

        return {
            "is_rate_limited": is_rate_limited,
            "backoff_seconds": _rate_limit_backoff if is_rate_limited else 0.0,
            "error_count": _rate_limit_errors,
            "last_error_time": _rate_limit_last_error_time.isoformat()
            if _rate_limit_last_error_time
            else None,
        }


def reset_rate_limit_state() -> None:
    """Reset rate limiting state (e.g., after a long pause between fetches)."""
    global _rate_limit_backoff, _rate_limit_errors, _rate_limit_last_error_time

    with _rate_limit_lock:
        _rate_limit_backoff = 0.0
        _rate_limit_errors = 0
        _rate_limit_last_error_time = None


# Maximum bbox size in degrees (approximately 600nm at mid-latitudes)
MAX_BBOX_SIZE_DEGREES = 10.0
# Padding to add around airport coordinates to ensure they're included
BBOX_PADDING_DEGREES = 0.5


def _pack_airport_coords(
    coords: list[tuple[float, float]], max_size_degrees: float
) -> list[tuple[float, float, float, float]]:
    """
    Pack coordinates into first-fit boxes no bigger than max_size_degrees per axis.

    The coordinates are visited in (longitude, latitude) order, so the packing
    depends only on the coordinates and not on the order they were given in. Each
    one joins the first box that still fits within max_size_degrees on both axes
    once it is included; when no box fits, it starts a new one.

    Args:
        coords: (latitude, longitude) pairs to pack.
        max_size_degrees: Maximum box extent in degrees, on both axes.

    Returns:
        One (min_lat, min_lon, max_lat, max_lon) tuple per box, in creation order.
    """
    boxes: list[list[float]] = []
    for lat, lon in sorted(coords, key=lambda coord: (coord[1], coord[0])):
        for box in boxes:
            if (
                max(box[2], lat) - min(box[0], lat) <= max_size_degrees
                and max(box[3], lon) - min(box[1], lon) <= max_size_degrees
            ):
                box[0] = min(box[0], lat)
                box[1] = min(box[1], lon)
                box[2] = max(box[2], lat)
                box[3] = max(box[3], lon)
                break
        else:
            boxes.append([lat, lon, lat, lon])
    return [(box[0], box[1], box[2], box[3]) for box in boxes]


def _pad_and_clamp_bbox(
    bbox: tuple[float, float, float, float], padding_degrees: float
) -> tuple[float, float, float, float]:
    """Grow a (min_lat, min_lon, max_lat, max_lon) box by the padding, clamped to the globe."""
    return (
        max(-90.0, bbox[0] - padding_degrees),
        max(-180.0, bbox[1] - padding_degrees),
        min(90.0, bbox[2] + padding_degrees),
        min(180.0, bbox[3] + padding_degrees),
    )


def calculate_airport_bboxes(
    airport_icaos: list[str],
    airports_data: dict[str, dict[str, Any]],
    max_size_degrees: float = MAX_BBOX_SIZE_DEGREES,
    padding_degrees: float = BBOX_PADDING_DEGREES,
) -> dict[str, tuple[float, float, float, float]]:
    """
    Calculate bounding boxes for a set of airports, splitting if needed.

    If all airports fit within max_size_degrees, returns a single bbox.
    Otherwise, packs them into as few boxes as fit that limit: the airports are
    visited in (longitude, latitude) order and each joins the first box its
    coordinates still fit in, so the result does not depend on the input order.

    Args:
        airport_icaos: List of airport ICAO codes
        airports_data: Dictionary mapping ICAO codes to airport data with lat/lon
        max_size_degrees: Maximum bbox size in degrees (default: 10.0)
        padding_degrees: Padding to add around coordinates (default: 0.5)

    Returns:
        Dictionary mapping bbox ID (e.g., "bbox_0", "bbox_1") to
        (min_lat, min_lon, max_lat, max_lon) tuples
    """
    # Collect valid airport coordinates
    airport_coords = []
    for icao in airport_icaos:
        airport = airports_data.get(icao)
        if (
            airport
            and airport.get("latitude") is not None
            and airport.get("longitude") is not None
        ):
            airport_coords.append(
                {"icao": icao, "lat": airport["latitude"], "lon": airport["longitude"]}
            )

    if not airport_coords:
        return {}

    # Calculate overall bounding box
    min_lat = min(a["lat"] for a in airport_coords)
    max_lat = max(a["lat"] for a in airport_coords)
    min_lon = min(a["lon"] for a in airport_coords)
    max_lon = max(a["lon"] for a in airport_coords)

    lat_span = max_lat - min_lat
    lon_span = max_lon - min_lon

    # If small enough, return single bbox
    if lat_span <= max_size_degrees and lon_span <= max_size_degrees:
        return {
            "bbox_0": _pad_and_clamp_bbox(
                (min_lat, min_lon, max_lat, max_lon), padding_degrees
            )
        }

    # Too wide for one box, so pack the airports into as few boxes as fit the limit
    packed = _pack_airport_coords(
        [(airport["lat"], airport["lon"]) for airport in airport_coords],
        max_size_degrees,
    )
    return {
        f"bbox_{idx}": _pad_and_clamp_bbox(box, padding_degrees)
        for idx, box in enumerate(packed)
    }


def get_weather_for_airports_bbox(
    airport_icaos: list[str],
    airports_data: dict[str, dict[str, Any]],
    max_workers: int = 5,
    progress_callback: Callable[[int, int], None] | None = None,
    *,
    as_of: datetime | None = None,
    on_box: Callable[[set[str], bool], None] | None = None,
    stop: threading.Event | None = None,
) -> dict[str, str]:
    """
    Fetch METAR data for airports using bbox queries.

    This is more efficient than per-airport fetching when dealing with many airports.
    Calculates bounding boxes from airport coordinates and fetches weather in bulk,
    then populates the METAR cache for subsequent wind/altimeter lookups.

    A box is requested only when at least one of its airports has neither a METAR
    entry younger than METAR_CACHE_DURATION nor a fresh "no METAR" negative. A box
    that succeeded with fewer than BBOX_MAX_ENTRIES entries records a negative for
    each of its airports it did not return, so get_metar skips their per-station fetch.

    Args:
        airport_icaos: List of airport ICAO codes to fetch weather for
        airports_data: Dictionary mapping ICAO codes to airport data with lat/lon
        max_workers: Maximum concurrent bbox fetches (default: 5)
        progress_callback: Optional callback(completed, total) called as requested bboxes complete
        as_of: Judge cache freshness as of this moment (the refresh's box decision)
            instead of now; also used for the returned METARs
        on_box: Optional callback(members, ok) with the upper-cased ICAOs of the
            requested airports in a box once their METARs are readable from the cache.
            The members of every box skipped as fresh come first, in one call, before
            any request; each fetched box follows as soon as its result is stored, with
            ``ok`` False when its request failed. Runs on the calling thread.
        stop: Optional signal to give up: once set, box requests not yet started
            are never made and no further box is stored or reported. Requests
            already in flight may still finish.

    Returns:
        Dictionary mapping ICAO codes to METAR strings (empty string if unavailable)
    """
    if not airport_icaos:
        return {}

    # Calculate bboxes for the airports
    bboxes = calculate_airport_bboxes(airport_icaos, airports_data)

    if not bboxes:
        return {}

    members = {
        bbox_id: _airports_in_bbox(bbox, airport_icaos, airports_data)
        for bbox_id, bbox in bboxes.items()
    }
    decision_time = _resolve_now(as_of)
    to_fetch = _bboxes_needing_fetch(bboxes, members, decision_time)
    if on_box is not None:
        fresh = {
            icao
            for bbox_id in bboxes.keys() - to_fetch.keys()
            for icao in members[bbox_id]
        }
        if fresh:
            on_box(fresh, True)

    # Populate the METAR cache box by box, so get_wind_from_metar/get_altimeter_setting
    # can read a box's airports without additional API calls as soon as it is stored
    target_set = {a.upper() for a in airport_icaos}

    def store(bbox_id: str, fetch: BboxFetchResult) -> None:
        with get_metar_cache_lock():
            _store_bbox_result(
                fetch, members[bbox_id], target_set, datetime.now(timezone.utc)
            )
        if on_box is not None:
            on_box(members[bbox_id], fetch.succeeded)

    _fetch_bboxes(to_fetch, max_workers, progress_callback, store, stop)

    with get_metar_cache_lock():
        # Answer each airport as of the decision time: a box skipped as fresh and a
        # stale entry under a fresh negative both keep their cached METAR.
        return {
            icao: _cached_metar(icao.upper(), decision_time) or ""
            for icao in airport_icaos
        }


def _airports_in_bbox(
    bbox: tuple[float, float, float, float],
    airport_icaos: list[str],
    airports_data: dict[str, dict[str, Any]],
) -> set[str]:
    """Upper-cased ICAOs of the requested airports whose coordinates lie inside the bbox."""
    min_lat, min_lon, max_lat, max_lon = bbox
    inside: set[str] = set()
    for icao in airport_icaos:
        airport = airports_data.get(icao) or {}
        lat, lon = airport.get("latitude"), airport.get("longitude")
        if lat is None or lon is None:
            continue
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            inside.add(icao.upper())
    return inside


def _has_fresh_answer(airport_icao: str, now: datetime) -> bool:
    """Whether the airport has a fresh METAR entry or a fresh negative. Caller holds the METAR lock."""
    metar_data_cache, _metar_blacklist = get_metar_cache()
    cache_entry = metar_data_cache.get(airport_icao)
    if cache_entry is not None and _is_fresh(cache_entry["timestamp"], now):
        return True
    return _has_fresh_negative(airport_icao, now)


def _bboxes_needing_fetch(
    bboxes: dict[str, tuple[float, float, float, float]],
    members: dict[str, set[str]],
    now: datetime,
) -> dict[str, tuple[float, float, float, float]]:
    """The bboxes with at least one airport lacking a fresh METAR entry or negative."""
    with get_metar_cache_lock():
        return {
            bbox_id: bbox
            for bbox_id, bbox in bboxes.items()
            if not all(_has_fresh_answer(icao, now) for icao in members[bbox_id])
        }


def _fetch_bbox_unless_stopped(
    bbox: tuple[float, float, float, float], stop: threading.Event | None
) -> BboxFetchResult:
    """A bbox request, skipped as failed when ``stop`` was set while it waited in the queue."""
    if stop is not None and stop.is_set():
        return _FAILED_BBOX_FETCH
    return _fetch_weather_bbox_result(bbox, False, 30)


def _fetch_bboxes(
    bboxes: dict[str, tuple[float, float, float, float]],
    max_workers: int,
    progress_callback: Callable[[int, int], None] | None,
    on_result: Callable[[str, BboxFetchResult], None],
    stop: threading.Event | None,
) -> None:
    """Fetch the bboxes' METARs in parallel, handing each (bbox_id, result) to on_result as it completes.

    on_result runs on the calling thread; a request that raised is handed over as failed.
    Once ``stop`` is set, queued requests are cancelled or skipped and nothing more is handed over.
    """
    if not bboxes:
        return

    total = len(bboxes)
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_bbox = {
            executor.submit(_fetch_bbox_unless_stopped, bbox, stop): bbox_id
            for bbox_id, bbox in bboxes.items()
        }

        for future in as_completed(future_to_bbox):
            if stop is not None and stop.is_set():
                executor.shutdown(wait=False, cancel_futures=True)
                return
            try:
                fetch = future.result()
            except Exception as e:
                # Individual bbox failure doesn't stop others
                debug_logger.warning(f"Bbox weather fetch failed: {e}")
                fetch = _FAILED_BBOX_FETCH
            on_result(future_to_bbox[future], fetch)

            completed += 1
            if progress_callback:
                progress_callback(completed, total)


def _store_bbox_result(
    fetch: BboxFetchResult, members: set[str], target_set: set[str], now: datetime
) -> None:
    """
    Cache a bbox result's METARs for the requested airports and, when the request
    succeeded and was not truncated, a negative for each member airport it omitted.
    Caller holds the METAR lock.
    """
    for icao, metar in fetch.metars.items():
        if metar and icao.upper() in target_set:
            _store_metar(icao, metar, now)

    if not fetch.succeeded or fetch.entry_count >= BBOX_MAX_ENTRIES:
        return

    returned = {icao.upper() for icao in fetch.metars}
    negatives = get_metar_negative_cache()
    for icao in members - returned:
        negatives[icao] = now


def get_weather_smart(
    airports: list[str],
    airport_coords: dict[str, tuple[float, float]],
    include_taf: bool = True,
    cluster_radius_nm: float = 300.0,
    min_airports_for_bbox: int = 5,
    max_bbox_area_per_airport: float = 50000.0,
    max_workers: int = 10,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """
    Intelligently fetch weather data using a hybrid bbox/individual approach.

    Clusters airports geographically and decides whether to use efficient bbox
    queries (for dense clusters) or individual station queries (for sparse airports).

    Args:
        airports: List of ICAO codes to fetch weather for
        airport_coords: Dict mapping ICAO to (lat, lon) tuples
        include_taf: Whether to include TAF data
        cluster_radius_nm: Max distance for airports to be in same cluster (~300nm default)
        min_airports_for_bbox: Minimum airports in cluster to use bbox query (default: 5)
        max_bbox_area_per_airport: Max bbox area (sq degrees) per airport before
            falling back to individual queries (default: 50000)
        max_workers: Maximum concurrent threads for parallel fetching
        progress_callback: Optional callback(completed, total) for progress updates

    Returns:
        Tuple of (metars_dict, tafs_dict) where keys are ICAO codes
    """
    if not airports:
        return ({}, {})

    # Filter to airports with known coordinates
    airports_with_coords = [a for a in airports if a in airport_coords]
    airports_without_coords = [a for a in airports if a not in airport_coords]

    clusters = _cluster_airports(
        airports_with_coords, airport_coords, cluster_radius_nm
    )
    bbox_clusters, sparse_airports = _split_clusters_by_density(
        clusters, airport_coords, min_airports_for_bbox, max_bbox_area_per_airport
    )

    # Progress tracking: count airports processed, not work units
    progress = _AirportProgress(len(airports), progress_callback)

    all_metars, all_tafs, fallback_airports = _fetch_cluster_bboxes(
        bbox_clusters, include_taf, max_workers, progress
    )

    # Individual airports, then any fallback airports from failed bbox queries
    all_individual = airports_without_coords + sparse_airports + fallback_airports

    if all_individual:
        all_metars.update(
            _fetch_each_airport(
                all_individual, get_metar, max_workers, "METAR", progress.advance_one
            )
        )

        # Fetch TAFs separately if requested (no progress update - already counted)
        if include_taf:
            all_tafs.update(
                _fetch_each_airport(all_individual, get_taf, max_workers, "TAF", None)
            )

    # Ensure all requested airports have an entry
    result_metars = {icao: all_metars.get(icao, "") for icao in airports}
    result_tafs = (
        {icao: all_tafs.get(icao, "") for icao in airports} if include_taf else {}
    )

    return (result_metars, result_tafs)


class _AirportProgress:
    """Counts airports processed and reports (processed, total) to an optional callback."""

    def __init__(self, total: int, callback: Callable[[int, int], None] | None) -> None:
        self.total = total
        self.processed = 0
        self.callback = callback

    def advance(self, count: int) -> None:
        self.processed += count
        if self.callback:
            self.callback(self.processed, self.total)

    def advance_one(self) -> None:
        self.advance(1)


_BboxCluster = tuple[tuple[float, float, float, float], list[str]]

# Padding around a cluster's airports in its bbox (0.5 degrees ~30nm)
_CLUSTER_BBOX_PADDING_DEGREES = 0.5


def _cluster_airports(
    icaos: list[str],
    airport_coords: dict[str, tuple[float, float]],
    cluster_radius_nm: float,
) -> list[list[str]]:
    """
    Cluster airports greedily: each cluster starts from an arbitrary unclustered
    airport and takes every unclustered airport within cluster_radius_nm of it.
    """
    clusters: list[list[str]] = []
    unclustered = set(icaos)

    while unclustered:
        seed = next(iter(unclustered))
        seed_lat, seed_lon = airport_coords[seed]
        cluster = [seed]
        unclustered.remove(seed)

        for icao in list(unclustered):
            lat, lon = airport_coords[icao]
            dist = haversine_distance_nm(seed_lat, seed_lon, lat, lon)
            if dist <= cluster_radius_nm:
                cluster.append(icao)
                unclustered.remove(icao)

        clusters.append(cluster)

    return clusters


def _padded_cluster_bbox(
    cluster: list[str], airport_coords: dict[str, tuple[float, float]]
) -> tuple[float, float, float, float]:
    """The cluster's (min_lat, min_lon, max_lat, max_lon), grown by the cluster padding."""
    lats = [airport_coords[a][0] for a in cluster]
    lons = [airport_coords[a][1] for a in cluster]
    padding = _CLUSTER_BBOX_PADDING_DEGREES
    return (
        min(lats) - padding,
        min(lons) - padding,
        max(lats) + padding,
        max(lons) + padding,
    )


def _split_clusters_by_density(
    clusters: list[list[str]],
    airport_coords: dict[str, tuple[float, float]],
    min_airports_for_bbox: int,
    max_bbox_area_per_airport: float,
) -> tuple[list[_BboxCluster], list[str]]:
    """
    Decide bbox or individual queries per cluster.

    A cluster is fetched by bbox when it has at least min_airports_for_bbox
    airports and its padded bbox area (square degrees) per airport is at most
    max_bbox_area_per_airport.

    Returns:
        (bbox_clusters, individual_airports): (bbox, airports) per dense cluster,
        and the airports of every other cluster in cluster order
    """
    bbox_clusters: list[_BboxCluster] = []
    individual_airports: list[str] = []

    for cluster in clusters:
        if len(cluster) < min_airports_for_bbox:
            individual_airports.extend(cluster)
            continue

        bbox = _padded_cluster_bbox(cluster, airport_coords)
        min_lat, min_lon, max_lat, max_lon = bbox
        bbox_area = (max_lat - min_lat) * (max_lon - min_lon)
        area_per_airport = bbox_area / len(cluster)

        if area_per_airport > max_bbox_area_per_airport:
            individual_airports.extend(cluster)
        else:
            bbox_clusters.append((bbox, cluster))

    return bbox_clusters, individual_airports


def _only_airports(values: dict[str, str], wanted: set[str]) -> dict[str, str]:
    """The entries whose upper-cased ICAO is in the wanted set."""
    return {icao: value for icao, value in values.items() if icao.upper() in wanted}


def _fetch_cluster_bboxes(
    bbox_clusters: list[_BboxCluster],
    include_taf: bool,
    max_workers: int,
    progress: _AirportProgress,
) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """
    Fetch each dense cluster's bbox in parallel, keeping only the cluster's airports.

    A cluster's airports count as processed when its bbox returns. A bbox request
    that raises sends its cluster's airports to the individual fallback instead.

    Returns:
        (metars, tafs, fallback_airports)
    """
    metars: dict[str, str] = {}
    tafs: dict[str, str] = {}
    fallback_airports: list[str] = []
    if not bbox_clusters:
        return metars, tafs, fallback_airports

    with ThreadPoolExecutor(
        max_workers=min(max_workers, len(bbox_clusters))
    ) as executor:
        future_to_cluster = {
            executor.submit(fetch_weather_bbox, bbox, include_taf): airports_in_cluster
            for bbox, airports_in_cluster in bbox_clusters
        }

        for future in as_completed(future_to_cluster):
            airports_in_cluster = future_to_cluster[future]
            try:
                bbox_metars, bbox_tafs = future.result()
                cluster_set = {a.upper() for a in airports_in_cluster}
                metars.update(_only_airports(bbox_metars, cluster_set))
                tafs.update(_only_airports(bbox_tafs, cluster_set))
                progress.advance(len(airports_in_cluster))
            except Exception:
                fallback_airports.extend(airports_in_cluster)

    return metars, tafs, fallback_airports


def _fetch_each_airport(
    icaos: list[str],
    fetch: Callable[[str], str],
    max_workers: int,
    kind: str,
    on_done: Callable[[], None] | None,
) -> dict[str, str]:
    """
    Fetch one value per airport in parallel, keeping the non-empty ones.

    A fetch that raises is logged at debug level as "<kind> fetch failed for <icao>".
    on_done, when given, runs on the calling thread after each airport, failed or not.
    """
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_icao = {executor.submit(fetch, icao): icao for icao in icaos}

        for future in as_completed(future_to_icao):
            icao = future_to_icao[future]
            try:
                value = future.result()
                if value:
                    results[icao] = value
            except Exception as e:
                debug_logger.debug(f"{kind} fetch failed for {icao}: {e}")

            if on_done:
                on_done()

    return results
