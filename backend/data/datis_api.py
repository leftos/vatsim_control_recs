"""
Real-world D-ATIS API client using atis.info.

Fetches FAA Digital ATIS data for US airports.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from common import logger as debug_logger

# Cache for D-ATIS data
_DATIS_CACHE: dict[str, list[dict[str, Any]]] = {}
_DATIS_CACHE_TIME: dict[str, float] = {}
_DATIS_CACHE_LOCK = threading.Lock()
DATIS_CACHE_DURATION = (
    60  # 60 seconds - D-ATIS updates less frequently than VATSIM ATIS
)

# API configuration
DATIS_API_BASE_URL = "https://atis.info/api"
DATIS_API_TIMEOUT = 10  # seconds


def get_datis_for_airport(
    icao: str, timeout: int = DATIS_API_TIMEOUT
) -> list[dict[str, Any]]:
    """
    Fetch real-world D-ATIS for a single airport from atis.info.

    Args:
        icao: Airport ICAO code (e.g., "KSFO")
        timeout: Request timeout in seconds

    Returns:
        List of D-ATIS entries for the airport:
        [
            {
                'type': 'combined' | 'departure' | 'arrival',
                'atis_code': 'K',
                'text_atis': 'Full ATIS text',
                'time': '1856',  # HHMM format
                'updated_at': '2026-01-30T19:49:49Z',
                'source': 'rw'  # Real-world indicator
            },
            ...
        ]
        Empty list if no D-ATIS available or on error.
    """
    current_time = time.time()

    # Check cache first
    with _DATIS_CACHE_LOCK:
        if icao in _DATIS_CACHE:
            cache_age = current_time - _DATIS_CACHE_TIME.get(icao, 0)
            if cache_age < DATIS_CACHE_DURATION:
                return _DATIS_CACHE[icao]

    # Fetch from API
    url = f"{DATIS_API_BASE_URL}/{icao}"
    try:
        response = requests.get(url, timeout=timeout)
        if response.status_code == 404:
            # No D-ATIS available for this airport
            with _DATIS_CACHE_LOCK:
                _DATIS_CACHE[icao] = []
                _DATIS_CACHE_TIME[icao] = time.time()
            return []

        response.raise_for_status()
        raw_data = response.json()

        # Parse response - API returns array of ATIS entries
        result = []
        if isinstance(raw_data, list):
            for entry in raw_data:
                # Map atis.info type values to our standard types
                raw_type = entry.get("type", "combined")
                if raw_type == "arr":
                    atis_type = "arrival"
                elif raw_type == "dep":
                    atis_type = "departure"
                else:
                    atis_type = "combined"

                result.append(
                    {
                        "type": atis_type,
                        "atis_code": entry.get("code", ""),
                        "text_atis": entry.get("datis", ""),
                        "time": entry.get("time", ""),
                        "updated_at": entry.get("updatedAt", ""),
                        "source": "rw",  # Real-world indicator
                    }
                )

        # Sort: departure first, then arrival, then combined
        type_order = {"departure": 0, "arrival": 1, "combined": 2}
        result.sort(key=lambda x: type_order.get(x.get("type", "combined"), 2))

        # Update cache
        with _DATIS_CACHE_LOCK:
            _DATIS_CACHE[icao] = result
            _DATIS_CACHE_TIME[icao] = time.time()

        return result

    except requests.Timeout:
        debug_logger.warning(f"D-ATIS API timeout for {icao}")
        return []
    except requests.RequestException as e:
        debug_logger.warning(f"D-ATIS API error for {icao}: {e}")
        return []
    except json.JSONDecodeError as e:
        debug_logger.warning(f"D-ATIS API JSON decode error for {icao}: {e}")
        return []


def get_datis_for_airports(
    airports: list[str],
    timeout: int = DATIS_API_TIMEOUT,
    max_workers: int = 10,
) -> dict[str, list[dict[str, Any]]]:
    """
    Fetch real-world D-ATIS for multiple airports in parallel.

    Args:
        airports: List of airport ICAO codes
        timeout: Request timeout per airport in seconds
        max_workers: Maximum concurrent requests

    Returns:
        Dict mapping ICAO to list of D-ATIS entries:
        {
            'KSFO': [{
                'type': 'combined',
                'atis_code': 'K',
                'text_atis': 'Full ATIS text',
                'time': '1856',
                'updated_at': '2026-01-30T19:49:49Z',
                'source': 'rw'
            }],
            'KATL': [{
                'type': 'departure',
                'atis_code': 'N',
                'text_atis': 'Departure ATIS text',
                'time': '1852',
                'updated_at': '2026-01-30T19:50:45Z',
                'source': 'rw'
            }, {
                'type': 'arrival',
                'atis_code': 'A',
                'text_atis': 'Arrival ATIS text',
                'time': '1852',
                'updated_at': '2026-01-30T19:37:44Z',
                'source': 'rw'
            }],
            ...
        }
    """
    if not airports:
        return {}

    result: dict[str, list[dict[str, Any]]] = {}

    # Filter to US airports only (D-ATIS is FAA service)
    # K-prefix for continental US, P for Pacific (Hawaii, etc.)
    us_airports = [icao for icao in airports if icao.startswith(("K", "P"))]

    if not us_airports:
        return result

    # Use thread pool for parallel fetching
    with ThreadPoolExecutor(max_workers=min(max_workers, len(us_airports))) as executor:
        future_to_icao = {
            executor.submit(get_datis_for_airport, icao, timeout): icao
            for icao in us_airports
        }

        for future in as_completed(future_to_icao):
            icao = future_to_icao[future]
            try:
                atis_list = future.result()
                if atis_list:  # Only include airports with D-ATIS
                    result[icao] = atis_list
            except Exception as e:
                debug_logger.warning(f"Error fetching D-ATIS for {icao}: {e}")

    return result


def clear_datis_cache() -> None:
    """Clear the D-ATIS cache."""
    with _DATIS_CACHE_LOCK:
        _DATIS_CACHE.clear()
        _DATIS_CACHE_TIME.clear()
