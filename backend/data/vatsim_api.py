"""
VATSIM API client for fetching flight and controller data.
"""

import json
import requests
from typing import Dict, Any, List, Optional

from backend.config.constants import VATSIM_DATA_URL


def download_vatsim_data(timeout: int = 10) -> Optional[Dict[str, Any]]:
    """
    Download VATSIM data from the API.

    Args:
        timeout: Request timeout in seconds (default: 10)

    Returns:
        Dictionary containing VATSIM data (pilots, controllers, atis, etc.)
        or None if the download failed
    """
    try:
        response = requests.get(VATSIM_DATA_URL, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.Timeout:
        print(f"Error downloading VATSIM data: Request timed out after {timeout} seconds")
        return None
    except requests.RequestException as e:
        print(f"Error downloading VATSIM data: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"Error decoding VATSIM data JSON: {e}")
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