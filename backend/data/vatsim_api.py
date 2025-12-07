"""
VATSIM API client for fetching flight and controller data.
"""

import json
import threading
import time
from typing import Dict, Any, List, Optional

import requests

from backend.config.constants import VATSIM_DATA_URL
from common import logger as debug_logger

# Cache for VATSIM data to avoid redundant API calls within refresh window
_VATSIM_DATA_CACHE: Optional[Dict[str, Any]] = None
_VATSIM_DATA_CACHE_TIME: float = 0
_VATSIM_DATA_CACHE_LOCK = threading.Lock()
VATSIM_CACHE_DURATION = 15  # seconds - matches typical refresh interval


def download_vatsim_data(timeout: int = 10, max_retries: int = 3) -> Optional[Dict[str, Any]]:
    """
    Download VATSIM data from the API with retry logic and caching.

    Data is cached for 15 seconds to avoid redundant API calls when multiple
    components request data within the same refresh window.

    Args:
        timeout: Request timeout in seconds (default: 10)
        max_retries: Maximum number of retry attempts (default: 3)

    Returns:
        Dictionary containing VATSIM data (pilots, controllers, atis, etc.)
        or None if all retries failed
    """
    global _VATSIM_DATA_CACHE, _VATSIM_DATA_CACHE_TIME

    current_time = time.time()

    # Check cache first (with lock for thread safety)
    with _VATSIM_DATA_CACHE_LOCK:
        if _VATSIM_DATA_CACHE is not None:
            cache_age = current_time - _VATSIM_DATA_CACHE_TIME
            if cache_age < VATSIM_CACHE_DURATION:
                return _VATSIM_DATA_CACHE

    # Cache miss or expired - fetch fresh data
    last_exception: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            response = requests.get(VATSIM_DATA_URL, timeout=timeout)
            response.raise_for_status()
            data = response.json()

            # Update cache
            with _VATSIM_DATA_CACHE_LOCK:
                _VATSIM_DATA_CACHE = data
                _VATSIM_DATA_CACHE_TIME = time.time()

            return data
        except requests.Timeout as e:
            last_exception = e
            debug_logger.warning(f"VATSIM API timeout (attempt {attempt + 1}/{max_retries})")
        except requests.RequestException as e:
            last_exception = e
            debug_logger.warning(f"VATSIM API error (attempt {attempt + 1}/{max_retries}): {e}")
        except json.JSONDecodeError as e:
            last_exception = e
            debug_logger.warning(f"VATSIM API JSON decode error (attempt {attempt + 1}/{max_retries}): {e}")

        # Exponential backoff before next retry (0.5s, 1s, 2s)
        if attempt < max_retries - 1:
            sleep_time = (2 ** attempt) * 0.5
            time.sleep(sleep_time)

    debug_logger.error(f"VATSIM API failed after {max_retries} attempts: {last_exception}")

    # On failure, return stale cache if available
    with _VATSIM_DATA_CACHE_LOCK:
        if _VATSIM_DATA_CACHE is not None:
            debug_logger.info("Returning stale VATSIM cache after API failure")
            return _VATSIM_DATA_CACHE

    return None


def filter_flights_by_airports(
    data: Dict[str, Any],
    airports: Dict[str, Dict[str, Any]],
    airport_allowlist: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Filter flights by departure and arrival airports.
    
    Args:
        data: VATSIM data dictionary containing 'pilots' list
        airports: Dictionary of airport data
        airport_allowlist: Optional list of airport ICAOs to filter by
    
    Returns:
        List of filtered flight dictionaries
    """
    flights = data.get('pilots', [])
    filtered_flights = []
    
    for flight in flights:
        # For flights with flight plans
        # Check if flight has a valid flight plan with non-empty departure or arrival
        departure = None
        arrival = None
        has_valid_flight_plan = False
        
        if flight.get('flight_plan'):
            departure = flight['flight_plan'].get('departure')
            arrival = flight['flight_plan'].get('arrival')
            
            # Treat empty strings as None/null for departure and arrival
            if not departure:
                departure = None
            if not arrival:
                arrival = None
            
            # Only consider it a valid flight plan if at least one field is non-empty
            has_valid_flight_plan = departure is not None or arrival is not None
        
        if has_valid_flight_plan:
            # If allowlist is provided, check if either departure or arrival is in the allowlist
            # Otherwise, check if both departure and arrival airports are in our airport data
            if airport_allowlist:
                if (departure and departure in airports) or (arrival and arrival in airports):
                    filtered_flights.append({
                        'callsign': flight.get('callsign'),
                        'departure': departure,
                        'arrival': arrival,
                        'latitude': flight.get('latitude'),
                        'longitude': flight.get('longitude'),
                        'groundspeed': flight.get('groundspeed'),
                        'altitude': flight.get('altitude'),
                        'flight_plan': flight.get('flight_plan')
                    })
            elif departure and arrival and departure in airports and arrival in airports:
                filtered_flights.append({
                    'callsign': flight.get('callsign'),
                    'departure': departure,
                    'arrival': arrival,
                    'latitude': flight.get('latitude'),
                    'longitude': flight.get('longitude'),
                    'groundspeed': flight.get('groundspeed'),
                    'altitude': flight.get('altitude'),
                    'flight_plan': flight.get('flight_plan')
                })
        # For flights without valid flight plans, we'll still include them for ground analysis
        # but with None for departure/arrival
        elif flight.get('latitude') is not None and flight.get('longitude') is not None:
            filtered_flights.append({
                'callsign': flight.get('callsign'),
                'departure': None,
                'arrival': None,
                'latitude': flight.get('latitude'),
                'longitude': flight.get('longitude'),
                'groundspeed': flight.get('groundspeed'),
                'altitude': flight.get('altitude'),
                'flight_plan': flight.get('flight_plan')
            })
    
    return filtered_flights