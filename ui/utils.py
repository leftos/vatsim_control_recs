"""
UI Utility Functions
Contains helper functions for sorting, logging, and data processing
"""

from backend.core.flights import ArrivalInfo
from . import debug_logger


def debug_log(message: str):
    """Write a debug message to the log file."""
    debug_logger.debug(message)


def eta_sort_key(row):
    """
    Sort key for airport/grouping tables: LANDED at top, then by ETA (soonest first),
    then by non-ETA total (descending), then by flight callsign for stability.

    For rows with TOTAL column in format "X/Y", sorts by:
    1. ETA category (LANDED first, then relative times, then absolute times, then unknown)
    2. ETA value (ascending - soonest first)
    3. Non-ETA total value (descending - bigger totals first)
    4. Flight callsign (for stability)
    """
    # Handle both tuple format (for display) and DepartureInfo/ArrivalInfo objects
    if isinstance(row, ArrivalInfo):
        flight_str = row.callsign
        eta_str = row.eta_display.upper()
        non_eta_total = 0  # Not available for ArrivalInfo objects
    else:
        # Determine format and extract fields
        if len(row) == 7:
            # Grouping format: (callsign, origin_icao, origin_name, arrival_icao, arrival_name, eta, eta_local)
            (
                flight,
                _origin_icao,
                _origin_name,
                _arrival_icao,
                _arrival_name,
                eta,
                _eta_local,
            ) = row
        else:
            # Single airport format: (callsign, origin_icao, origin_name, eta, eta_local)
            flight, _origin_icao, _origin_name, eta, _eta_local = row
        eta_str = str(eta).upper()
        flight_str = str(flight)
        non_eta_total = 0  # Not available in arrival rows

    # Put LANDED flights at the top
    if "LANDED" in eta_str:
        return (0, 0, -non_eta_total, flight_str)

    # Handle relative time formats with hours and/or minutes like "1H", "1H30M", "2H", "45M", "<1M"
    if "H" in eta_str or "M" in eta_str:
        try:
            total_minutes = 0

            # Check if it starts with '<' for "less than" times
            if eta_str.startswith("<"):
                # <1M means less than 1 minute, treat as 0.5 minutes for sorting
                minutes_str = eta_str.replace("<", "").replace("M", "").strip()
                total_minutes = (
                    float(minutes_str) - 0.5
                )  # Subtract 0.5 to sort before the actual minute
            elif "H" in eta_str and "M" in eta_str:
                # Format like "1H30M" or "2H15M"
                parts = eta_str.replace("H", " ").replace("M", "").split()
                hours = int(parts[0])
                minutes = int(parts[1])
                total_minutes = hours * 60 + minutes
            elif "H" in eta_str:
                # Format like "1H" or "2H"
                hours = int(eta_str.replace("H", "").strip())
                total_minutes = hours * 60
            elif "M" in eta_str:
                # Format like "45M" or "30M"
                total_minutes = float(eta_str.replace("M", "").strip())

            return (1, total_minutes, -non_eta_total, flight_str)
        except (ValueError, IndexError):
            return (2, 0, -non_eta_total, flight_str)

    # Handle absolute time formats like "13:04"
    if ":" in eta_str:
        try:
            # Parse HH:MM format
            parts = eta_str.split(":")
            hours = int(parts[0])
            minutes = int(parts[1])
            total_minutes = hours * 60 + minutes
            return (2, total_minutes, -non_eta_total, flight_str)
        except ValueError:
            return (2, 0, -non_eta_total, flight_str)

    # Default: treat as lowest priority
    return (3, 0, -non_eta_total, flight_str)


def airport_grouping_sort_key(row):
    """
    Sort key for airport/grouping tables: by TOTAL column (descending).

    For rows with TOTAL column in format "X/Y", sorts by:
    1. Non-ETA total (Y value, descending - bigger totals first)
    2. ETA-dependent total (X value, descending - bigger totals first)

    This ensures rows with bigger non-eta totals appear above rows with
    the same eta-dependent total but smaller non-eta totals.
    """
    # Handle both AirportStats and GroupingStats objects
    if hasattr(row, "total") and hasattr(row, "arrivals_all"):
        # Object format (AirportStats or GroupingStats)
        eta_total = row.total
        non_eta_total = row.departures + row.arrivals_all
    else:
        # Tuple format - need to extract TOTAL column
        # For AirportStats with wind: (ICAO, NAME, WIND, ALT, TOTAL, DEP, ARR, NEXT ETA, STAFFED)
        # For AirportStats without wind: (ICAO, NAME, ALT, TOTAL, DEP, ARR, NEXT ETA, STAFFED)
        # For GroupingStats: (GROUPING, TOTAL, DEP, ARR, NEXT ETA, STAFFED)

        # Find the TOTAL column (it's typically at index 4 or 3 depending on format)
        # We'll look for it by checking if it contains '/' or is numeric
        total_str = None

        # Try to find TOTAL column by checking different positions
        if len(row) >= 5:
            # Check position 4 (with wind) or position 3 (without wind) or position 1 (grouping)
            for idx in [4, 3, 1]:
                if idx < len(row):
                    val = str(row[idx])
                    if "/" in val or val.isdigit():
                        total_str = val
                        break

        if total_str is None:
            # Fallback: couldn't find TOTAL, return neutral sort key
            return (0, 0)

        # Parse the TOTAL column
        if "/" in total_str:
            # Format: "X/Y"
            try:
                parts = total_str.split("/")
                eta_total = int(parts[0].strip())
                non_eta_total = int(parts[1].strip())
            except (ValueError, IndexError):
                return (0, 0)
        else:
            # Format: just "X"
            try:
                eta_total = int(total_str.strip())
                non_eta_total = eta_total
            except ValueError:
                return (0, 0)

    # Sort by non-eta total (descending), then by eta total (descending)
    return (-non_eta_total, -eta_total)


def expand_countries_to_airports(
    country_codes: list, unified_airport_data: dict
) -> list:
    """
    Expand country codes to a list of airport ICAO codes.

    Args:
        country_codes: List of country codes (e.g., ['US', 'DE'])
        unified_airport_data: Dictionary of all airport data

    Returns:
        List of airport ICAO codes matching the given country codes
    """
    if not country_codes or not unified_airport_data:
        return []

    # Normalize country codes to uppercase
    country_codes_upper = [code.upper() for code in country_codes]

    # Find all airports matching the country codes
    matching_airports = []
    for icao, airport_data in unified_airport_data.items():
        airport_country = airport_data.get("country", "").upper()
        if airport_country in country_codes_upper:
            matching_airports.append(icao)

    return sorted(matching_airports)
