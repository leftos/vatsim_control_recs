"""
Statsim.net API client for fetching historical flight statistics.

This module provides functions to query the statsim.net API for historical
VATSIM flight data, useful for analyzing traffic patterns between airports.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Set, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from common import logger as debug_logger


# API configuration
STATSIM_API_BASE_URL = "https://api.statsim.net"
STATSIM_API_TIMEOUT = 15  # seconds per request
STATSIM_MAX_DAYS_PER_QUERY = 30  # API has ~30 day limit per query
STATSIM_DEFAULT_DAYS_BACK = (
    90  # Total days to query (in chunks of STATSIM_MAX_DAYS_PER_QUERY)
)


def _format_datetime_for_api(dt: datetime) -> str:
    """
    Format a datetime object for the statsim API.

    Args:
        dt: Datetime object to format

    Returns:
        ISO 8601 formatted string
    """
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_flights_from_origin(
    icao: str,
    days_back: int = STATSIM_MAX_DAYS_PER_QUERY,
    days_offset: int = 0,
    timeout: int = STATSIM_API_TIMEOUT,
) -> List[Dict[str, Any]]:
    """
    Fetch all flights departing from a given airport in a date range.

    Args:
        icao: Airport ICAO code (e.g., "KLAX")
        days_back: Number of days in the query window (default: STATSIM_MAX_DAYS_PER_QUERY)
        days_offset: Number of days ago to end the query window (default: 0 = now)
        timeout: Request timeout in seconds

    Returns:
        List of flight dictionaries, or empty list on error

    Example:
        days_back=30, days_offset=0  -> last 30 days (now-30 to now)
        days_back=30, days_offset=30 -> 30-60 days ago (now-60 to now-30)
    """
    now = datetime.now(timezone.utc)
    to_date = now - timedelta(days=days_offset)
    from_date = to_date - timedelta(days=days_back)

    url = f"{STATSIM_API_BASE_URL}/api/Flights/IcaoOrigin"
    params = {
        "icao": icao.upper(),
        "from": _format_datetime_for_api(from_date),
        "to": _format_datetime_for_api(to_date),
    }

    try:
        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.Timeout:
        debug_logger.warning(f"Statsim API timeout fetching flights from origin {icao}")
        return []
    except requests.RequestException as e:
        debug_logger.warning(
            f"Statsim API error fetching flights from origin {icao}: {e}"
        )
        return []
    except Exception as e:
        debug_logger.warning(
            f"Unexpected error fetching flights from origin {icao}: {e}"
        )
        return []


def fetch_flights_to_destination(
    icao: str,
    days_back: int = STATSIM_MAX_DAYS_PER_QUERY,
    days_offset: int = 0,
    timeout: int = STATSIM_API_TIMEOUT,
) -> List[Dict[str, Any]]:
    """
    Fetch all flights arriving at a given airport in a date range.

    Args:
        icao: Airport ICAO code (e.g., "KLAX")
        days_back: Number of days in the query window (default: STATSIM_MAX_DAYS_PER_QUERY)
        days_offset: Number of days ago to end the query window (default: 0 = now)
        timeout: Request timeout in seconds

    Returns:
        List of flight dictionaries, or empty list on error

    Example:
        days_back=30, days_offset=0  -> last 30 days (now-30 to now)
        days_back=30, days_offset=30 -> 30-60 days ago (now-60 to now-30)
    """
    now = datetime.now(timezone.utc)
    to_date = now - timedelta(days=days_offset)
    from_date = to_date - timedelta(days=days_back)

    url = f"{STATSIM_API_BASE_URL}/api/Flights/IcaoDestination"
    params = {
        "icao": icao.upper(),
        "from": _format_datetime_for_api(from_date),
        "to": _format_datetime_for_api(to_date),
    }

    try:
        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.Timeout:
        debug_logger.warning(
            f"Statsim API timeout fetching flights to destination {icao}"
        )
        return []
    except requests.RequestException as e:
        debug_logger.warning(
            f"Statsim API error fetching flights to destination {icao}: {e}"
        )
        return []
    except Exception as e:
        debug_logger.warning(
            f"Unexpected error fetching flights to destination {icao}: {e}"
        )
        return []


def get_historical_stats_for_airports(
    query_airports: List[str],
    tracked_airports: Set[str],
    days_back: int = STATSIM_DEFAULT_DAYS_BACK,
    progress_callback: Optional[
        Callable[[int, int, Dict[str, Dict[str, int]]], None]
    ] = None,
) -> Dict[str, Dict[str, int]]:
    """
    Get historical flight statistics between query airports and tracked airports.

    For each query airport, fetches flights departing from and arriving at that airport,
    then counts how many of those flights involve the tracked airports.

    Args:
        query_airports: List of airport ICAOs to query (user-entered airports)
        tracked_airports: Set of currently tracked airport ICAOs
        days_back: Number of days to look back (default: STATSIM_DEFAULT_DAYS_BACK)
        progress_callback: Optional callback called with (completed_queries, total_queries, current_results)
                          after each query completes

    Returns:
        Dictionary mapping tracked airport ICAOs to stats:
        {
            "KOAK": {"departures": 45, "arrivals": 52, "total": 97},
            "KSFO": {"departures": 120, "arrivals": 105, "total": 225},
            ...
        }
    """
    # Results aggregated per tracked airport
    results: Dict[str, Dict[str, int]] = {}

    # Total queries = 2 per query airport (origin + destination)
    total_queries = len(query_airports) * 2
    completed_queries = 0

    # Normalize tracked airports to uppercase
    tracked_upper = {icao.upper() for icao in tracked_airports}

    for query_icao in query_airports:
        query_icao_upper = query_icao.upper()

        # Fetch flights departing FROM the query airport
        # These flights' destinations might be in our tracked airports
        origin_flights = fetch_flights_from_origin(query_icao_upper, days_back)

        for flight in origin_flights:
            # Get destination - statsim API uses 'arrival' field
            destination = (
                flight.get("destination", "").upper()
                if flight.get("destination")
                else ""
            )
            if destination and destination in tracked_upper:
                if destination not in results:
                    results[destination] = {"departures": 0, "arrivals": 0, "total": 0}
                # Flight from query airport TO tracked airport = arrival at tracked airport
                results[destination]["arrivals"] += 1
                results[destination]["total"] += 1

        completed_queries += 1
        if progress_callback:
            progress_callback(completed_queries, total_queries, results)

        # Fetch flights arriving AT the query airport
        # These flights' origins might be in our tracked airports
        destination_flights = fetch_flights_to_destination(query_icao_upper, days_back)

        for flight in destination_flights:
            # Get origin - statsim API uses 'departure' field
            origin = (
                flight.get("departure", "").upper() if flight.get("departure") else ""
            )
            if origin and origin in tracked_upper:
                if origin not in results:
                    results[origin] = {"departures": 0, "arrivals": 0, "total": 0}
                # Flight from tracked airport TO query airport = departure from tracked airport
                results[origin]["departures"] += 1
                results[origin]["total"] += 1

        completed_queries += 1
        if progress_callback:
            progress_callback(completed_queries, total_queries, results)

    return results


def get_historical_stats_concurrent(
    query_airports: List[str],
    tracked_airports: Set[str],
    days_back: int = STATSIM_DEFAULT_DAYS_BACK,
    max_workers: int = 4,
    progress_callback: Optional[
        Callable[[int, int, Dict[str, Dict[str, int]]], None]
    ] = None,
) -> Dict[str, Dict[str, int]]:
    """
    Get historical flight statistics using concurrent API calls for better performance.

    Same as get_historical_stats_for_airports but executes API calls in parallel.

    Args:
        query_airports: List of airport ICAOs to query (user-entered airports)
        tracked_airports: Set of currently tracked airport ICAOs
        days_back: Number of days to look back (default: STATSIM_DEFAULT_DAYS_BACK)
        max_workers: Maximum number of concurrent requests (default: 4)
        progress_callback: Optional callback called after each query completes

    Returns:
        Dictionary mapping tracked airport ICAOs to stats
    """
    # Results aggregated per tracked airport
    results: Dict[str, Dict[str, int]] = {}

    # Normalize tracked airports to uppercase
    tracked_upper = {icao.upper() for icao in tracked_airports}

    # Build list of all API calls to make
    # Each item: (query_type, icao) where query_type is "origin" or "destination"
    queries = []
    for icao in query_airports:
        queries.append(("origin", icao.upper()))
        queries.append(("destination", icao.upper()))

    total_queries = len(queries)
    completed_queries = 0

    def execute_query(query_info):
        """Execute a single API query."""
        query_type, icao = query_info
        if query_type == "origin":
            flights = fetch_flights_from_origin(icao, days_back)
            return ("origin", icao, flights)
        else:
            flights = fetch_flights_to_destination(icao, days_back)
            return ("destination", icao, flights)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_query = {
            executor.submit(execute_query, query): query for query in queries
        }

        for future in as_completed(future_to_query):
            try:
                query_type, icao, flights = future.result()

                for flight in flights:
                    if query_type == "origin":
                        # Flights departing FROM query airport - check destination
                        destination = (
                            flight.get("destination", "").upper()
                            if flight.get("destination")
                            else ""
                        )
                        if destination and destination in tracked_upper:
                            if destination not in results:
                                results[destination] = {
                                    "departures": 0,
                                    "arrivals": 0,
                                    "total": 0,
                                }
                            results[destination]["arrivals"] += 1
                            results[destination]["total"] += 1
                    else:
                        # Flights arriving AT query airport - check origin
                        origin = (
                            flight.get("departure", "").upper()
                            if flight.get("departure")
                            else ""
                        )
                        if origin and origin in tracked_upper:
                            if origin not in results:
                                results[origin] = {
                                    "departures": 0,
                                    "arrivals": 0,
                                    "total": 0,
                                }
                            results[origin]["departures"] += 1
                            results[origin]["total"] += 1

                completed_queries += 1
                if progress_callback:
                    progress_callback(completed_queries, total_queries, results)

            except Exception as e:
                debug_logger.warning(f"Error processing statsim query result: {e}")
                completed_queries += 1
                if progress_callback:
                    progress_callback(completed_queries, total_queries, results)

    return results
