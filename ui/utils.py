"""
UI Utility Functions
Contains helper functions for sorting, logging, and data processing
"""

from datetime import datetime
from backend.core.flights import ArrivalInfo
from . import debug_logger


def debug_log(message: str):
    """Write a debug message to the log file."""
    debug_logger.debug(message)


def eta_sort_key(arrival_row):
    """Sort key for arrivals: LANDED at top, then by ETA (soonest first), then by flight callsign for stability"""
    # Handle both tuple format (for display) and DepartureInfo/ArrivalInfo objects
    if isinstance(arrival_row, ArrivalInfo):
        flight_str = arrival_row.callsign
        eta_str = arrival_row.eta_display.upper()
    else:
        flight, origin_icao, origin_name, eta, eta_local = arrival_row
        eta_str = str(eta).upper()
        flight_str = str(flight)
    
    # Put LANDED flights at the top
    if "LANDED" in eta_str:
        return (0, 0, flight_str)
    
    # Handle relative time formats with hours and/or minutes like "1H", "1H30M", "2H", "45M", "<1M"
    if "H" in eta_str or "M" in eta_str:
        try:
            total_minutes = 0
            
            # Check if it starts with '<' for "less than" times
            if eta_str.startswith("<"):
                # <1M means less than 1 minute, treat as 0.5 minutes for sorting
                minutes_str = eta_str.replace("<", "").replace("M", "").strip()
                total_minutes = float(minutes_str) - 0.5  # Subtract 0.5 to sort before the actual minute
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
            
            return (1, total_minutes, flight_str)
        except (ValueError, IndexError):
            return (2, 0, flight_str)
    
    # Handle absolute time formats like "13:04"
    if ":" in eta_str:
        try:
            # Parse HH:MM format
            parts = eta_str.split(":")
            hours = int(parts[0])
            minutes = int(parts[1])
            total_minutes = hours * 60 + minutes
            return (2, total_minutes, flight_str)
        except ValueError:
            return (2, 0, flight_str)
    
    # Default: treat as lowest priority
    return (3, 0, flight_str)


def expand_countries_to_airports(country_codes: list, unified_airport_data: dict) -> list:
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
        airport_country = airport_data.get('country', '').upper()
        if airport_country in country_codes_upper:
            matching_airports.append(icao)
    
    return sorted(matching_airports)