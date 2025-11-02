"""
Flight analysis and tracking for VATSIM aircraft.
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple

from backend.core.calculations import haversine_distance_nm, calculate_eta


@dataclass
class AirportInfo:
    """Structured airport information"""
    pretty_name: str
    icao_code: str


@dataclass
class DepartureInfo:
    """Structured departure flight information"""
    callsign: str
    destination: AirportInfo


@dataclass
class ArrivalInfo:
    """Structured arrival flight information"""
    callsign: str
    origin: AirportInfo
    eta_display: str
    eta_local_time: str


def get_nearest_airport_if_on_ground(
    flight: Dict[str, Any],
    airports: Dict[str, Dict[str, Any]],
    max_distance_nm: float = 6,
    max_groundspeed: float = 40
) -> Optional[str]:
    """
    Determine if a flight is on the ground at an airport.
    Based on distance from airport and groundspeed.
    
    Args:
        flight: Flight data dictionary
        airports: Dictionary of airport data
        max_distance_nm: Maximum distance in nautical miles to consider "on ground" (default: 6)
        max_groundspeed: Maximum groundspeed in knots to consider "on ground" (default: 40)
    
    Returns:
        Airport ICAO code if flight is on ground at that airport, None otherwise
    """
    if flight['groundspeed'] > max_groundspeed:
        return None
    
    # For flights with flight plans but not near departure or arrival
    # or flights without flight plans, find the nearest airport
    if flight['latitude'] is not None and flight['longitude'] is not None:
        nearest_icao = None
        min_distance = float('inf')
        for icao, airport_data in airports.items():
            # Calculate distance from airport
            distance = haversine_distance_nm(
                flight['latitude'],
                flight['longitude'],
                airport_data['latitude'],
                airport_data['longitude']
            )

            # Keep the minimum distance/airport
            if distance < min_distance:
                min_distance = distance
                nearest_icao = icao

        # If the nearest airport is within the threshold, consider the flight on ground there
        if nearest_icao is not None and min_distance <= max_distance_nm:
            return nearest_icao
    
    return None


def is_flight_flying_near_arrival(
    flight: Dict[str, Any],
    airports: Dict[str, Dict[str, Any]],
    max_eta_hours: float = 1.0,
    min_groundspeed: float = 40
) -> bool:
    """
    Determine if a flight is within specified hours of arriving at the arrival airport.
    Based on distance and groundspeed.
    
    Args:
        flight: Flight data dictionary
        airports: Dictionary of airport data
        max_eta_hours: Maximum ETA in hours to consider "near arrival" (default: 1.0)
        min_groundspeed: Minimum groundspeed in knots to consider "in flight" (default: 40)
    
    Returns:
        True if flight is within max_eta_hours of arrival, False otherwise
    """
    if flight['groundspeed'] < min_groundspeed:
        return False
    
    # For flights with arrival airport in flight plan
    if flight['arrival'] and flight['arrival'] in airports:
        arrival_airport = airports.get(flight['arrival'])
        if not arrival_airport:
            return False
        
        # Calculate distance to arrival airport
        distance = haversine_distance_nm(
            flight['latitude'],
            flight['longitude'],
            arrival_airport['latitude'],
            arrival_airport['longitude']
        )
        
        # Calculate estimated time of arrival (in hours)
        if flight['groundspeed'] > 0:
            eta_hours = distance / flight['groundspeed']
            return max_eta_hours == 0 or eta_hours <= max_eta_hours
    
    return False


def find_nearest_airport(
    flight: Dict[str, Any],
    airports: Dict[str, Dict[str, Any]]
) -> Optional[str]:
    """
    Find the nearest airport to a flight's current position.
    
    Args:
        flight: Flight data dictionary
        airports: Dictionary of airport data
    
    Returns:
        ICAO code of the nearest airport, or None if position not available
    """
    if flight['latitude'] is None or flight['longitude'] is None:
        return None
    
    nearest_airport = None
    min_distance = float('inf')
    
    for icao, airport_data in airports.items():
        distance = haversine_distance_nm(
            flight['latitude'],
            flight['longitude'],
            airport_data['latitude'],
            airport_data['longitude']
        )
        
        if distance < min_distance:
            min_distance = distance
            nearest_airport = icao
    
    return nearest_airport


def get_airport_flight_details(
    airport_icao_or_list,
    max_eta_hours: float = 1.0,
    disambiguator=None,
    all_airports_data: Optional[Dict[str, Dict[str, Any]]] = None,
    aircraft_approach_speeds: Optional[Dict[str, int]] = None,
    vatsim_data: Optional[Dict[str, Any]] = None
) -> Tuple[List[DepartureInfo], List[ArrivalInfo]]:
    """
    Get detailed flight information for a specific airport or list of airports.
    Returns separate lists for departures and arrivals with structured data.
    
    Args:
        airport_icao_or_list: Either a single ICAO code (str) or a list of ICAO codes
        max_eta_hours: Maximum ETA in hours for arrival filter (default: 1.0)
        disambiguator: An AirportDisambiguator instance for pretty names
        all_airports_data: Dictionary of all airport data (required)
        aircraft_approach_speeds: Dictionary of aircraft approach speeds (optional)
        vatsim_data: VATSIM data dictionary (required)
    
    Returns:
        Tuple of (departures_list, arrivals_list) with structured data:
        - departures_list: List[DepartureInfo]
        - arrivals_list: List[ArrivalInfo]
    """
    from backend.data.vatsim_api import filter_flights_by_airports
    import os
    
    # Set up debug logging to file
    DEBUG_LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "debug.log")
    
    def debug_log(message: str):
        """Write a debug message to the log file."""
        from datetime import datetime
        with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            f.write(f"[{timestamp}] {message}\n")
    
    debug_log(f"[BACKEND] get_airport_flight_details called with airport={airport_icao_or_list}, max_eta={max_eta_hours}")
    
    if all_airports_data is None or vatsim_data is None:
        debug_log(f"[BACKEND] ERROR: all_airports_data is None: {all_airports_data is None}, vatsim_data is None: {vatsim_data is None}")
        return [], []
    
    debug_log(f"[BACKEND] all_airports_data has {len(all_airports_data)} airports, vatsim_data keys: {list(vatsim_data.keys())}")
    
    # Normalize input to a list
    if isinstance(airport_icao_or_list, str):
        airport_icao_list = [airport_icao_or_list]
    else:
        airport_icao_list = list(airport_icao_or_list)
    
    debug_log(f"[BACKEND] airport_icao_list: {airport_icao_list}")
    
    # Create airports dict for the specified airports
    airports = {icao: data for icao, data in all_airports_data.items() if icao in airport_icao_list}
    debug_log(f"[BACKEND] Created airports dict with {len(airports)} airports")
    
    # Filter flights - we need all flights that involve our airports
    flights = filter_flights_by_airports(vatsim_data, all_airports_data, airport_icao_list)
    debug_log(f"[BACKEND] filter_flights_by_airports returned {len(flights)} flights")
    
    departures_list = []
    arrivals_list = []
    
    debug_log(f"[BACKEND] Processing {len(flights)} flights...")
    
    for flight in flights:
        callsign = flight['callsign']
        nearest_airport_if_on_ground = get_nearest_airport_if_on_ground(flight, airports)
        
        # Check if this is a local flight (departure == arrival)
        is_local_flight = (flight['departure'] and flight['arrival'] and
                          flight['departure'] == flight['arrival'])
        
        # Check if this is a departure (on ground at departure airport)
        if flight['departure'] and flight['departure'] in airport_icao_list:
            if nearest_airport_if_on_ground == flight['departure']:
                # Flight is on ground at one of our airports, preparing to depart
                if is_local_flight:
                    # Local flight - show LOCAL for name and ---- for ICAO
                    departures_list.append(DepartureInfo(
                        callsign=callsign,
                        destination=AirportInfo(pretty_name="LOCAL", icao_code="----")
                    ))
                else:
                    destination = flight['arrival'] if flight['arrival'] else "----"
                    debug_log(f"[BACKEND] Departure {callsign}: destination={destination}, disambiguator={disambiguator is not None}")
                    if disambiguator and destination != "----":
                        pretty_destination = disambiguator.get_pretty_name(destination)
                        debug_log(f"[BACKEND] Departure {callsign}: got pretty_destination={pretty_destination}")
                    else:
                        pretty_destination = destination
                    departures_list.append(DepartureInfo(
                        callsign=callsign,
                        destination=AirportInfo(pretty_name=pretty_destination, icao_code=destination)
                    ))
        # Also handle flights with only arrival filed, on ground at one of our airports
        # But NOT if they're already at the arrival airport (those are arrivals, not departures)
        elif not flight['departure'] and flight['arrival'] and nearest_airport_if_on_ground:
            if nearest_airport_if_on_ground in airport_icao_list and nearest_airport_if_on_ground != flight['arrival']:
                # Flight is on ground at one of our airports (not the arrival) with only arrival in flight plan
                destination = flight['arrival']
                pretty_destination = disambiguator.get_pretty_name(destination) if disambiguator else destination
                departures_list.append(DepartureInfo(
                    callsign=callsign,
                    destination=AirportInfo(pretty_name=pretty_destination, icao_code=destination)
                ))
        
        # Check if this is an arrival (either on ground at arrival or flying nearby)
        if flight['arrival'] and flight['arrival'] in airport_icao_list:
            # Skip if departure == arrival and aircraft is on ground (already added as departure)
            if is_local_flight and nearest_airport_if_on_ground == flight['arrival']:
                pass  # Already handled as departure above
            elif nearest_airport_if_on_ground == flight['arrival']:
                # Flight is on ground at arrival airport
                if is_local_flight:
                    # Local flight - show LOCAL for name and ---- for ICAO
                    arrivals_list.append(ArrivalInfo(
                        callsign=callsign,
                        origin=AirportInfo(pretty_name="LOCAL", icao_code="----"),
                        eta_display="LANDED",
                        eta_local_time="----"
                    ))
                else:
                    origin = flight['departure'] if flight['departure'] else "----"
                    pretty_origin = disambiguator.get_pretty_name(origin) if disambiguator else origin
                    debug_log(f"[BACKEND] Arrival {callsign} LANDED: origin={origin}, pretty_origin={pretty_origin}")
                    arrivals_list.append(ArrivalInfo(
                        callsign=callsign,
                        origin=AirportInfo(pretty_name=pretty_origin, icao_code=origin),
                        eta_display="LANDED",
                        eta_local_time="----"
                    ))
            # For in-flight arrivals, check if it's an arrival first, then calculate ETA
            # is_flight_flying_near_arrival uses max_eta_hours=0 to check ALL arrivals
            elif is_flight_flying_near_arrival(flight, all_airports_data, max_eta_hours=0):
                if is_local_flight:
                    # Local flight in the air - show LOCAL for name and ---- for ICAO
                    eta_display, eta_local_time, eta_hours = calculate_eta(flight, all_airports_data, aircraft_approach_speeds)
                    # Add to list if it meets the original max_eta_hours criteria
                    if max_eta_hours == 0 or eta_hours <= max_eta_hours:
                        arrivals_list.append(ArrivalInfo(
                            callsign=callsign,
                            origin=AirportInfo(pretty_name="LOCAL", icao_code="----"),
                            eta_display=eta_display,
                            eta_local_time=eta_local_time
                        ))
                else:
                    origin = flight['departure'] if flight['departure'] else "----"
                    pretty_origin = disambiguator.get_pretty_name(origin) if disambiguator else origin
                    eta_display, eta_local_time, eta_hours = calculate_eta(flight, all_airports_data, aircraft_approach_speeds)
                    # Add to list if it meets the original max_eta_hours criteria
                    if max_eta_hours == 0 or eta_hours <= max_eta_hours:
                        arrivals_list.append(ArrivalInfo(
                            callsign=callsign,
                            origin=AirportInfo(pretty_name=pretty_origin, icao_code=origin),
                            eta_display=eta_display,
                            eta_local_time=eta_local_time
                        ))
            else:
                # Flight has arrival filed but is on ground (not at arrival airport, likely at departure)
                # Show with ETA="----" to indicate they haven't departed yet
                if is_local_flight:
                    # Local flight not yet departed - show LOCAL for name and ---- for ICAO
                    arrivals_list.append(ArrivalInfo(
                        callsign=callsign,
                        origin=AirportInfo(pretty_name="LOCAL", icao_code="----"),
                        eta_display="----",
                        eta_local_time="----"
                    ))
                else:
                    origin = flight['departure'] if flight['departure'] else "----"
                    pretty_origin = disambiguator.get_pretty_name(origin) if disambiguator else origin
                    arrivals_list.append(ArrivalInfo(
                        callsign=callsign,
                        origin=AirportInfo(pretty_name=pretty_origin, icao_code=origin),
                        eta_display="----",
                        eta_local_time="----"
                    ))
        
        # Handle flights on ground without flight plans
        if not flight['departure'] and not flight['arrival'] and nearest_airport_if_on_ground:
            if nearest_airport_if_on_ground in airport_icao_list:
                # Count as departure with unknown destination
                departures_list.append(DepartureInfo(
                    callsign=callsign,
                    destination=AirportInfo(pretty_name="----", icao_code="----")
                ))
    
    debug_log(f"[BACKEND] Final counts - departures: {len(departures_list)}, arrivals: {len(arrivals_list)}")
    
    # Sort departures by callsign
    departures_list.sort(key=lambda x: x.callsign)
    
    # Sort arrivals by ETA (convert eta_display to sortable value)
    def eta_sort_key(arrival):
        eta_str = arrival.eta_display
        if eta_str == "LANDED":
            return -1  # Already landed flights first
        elif eta_str == "----" or eta_str == "":
            return float('inf')  # Unknown ETA last
        else:
            # Parse ETA strings like "45m", "1h30m", "2h", etc.
            try:
                if 'h' in eta_str and 'm' in eta_str:
                    parts = eta_str.replace('h', ' ').replace('m', '').split()
                    return int(parts[0]) * 60 + int(parts[1])
                elif 'h' in eta_str:
                    return int(eta_str.replace('h', '')) * 60
                elif 'm' in eta_str:
                    return int(eta_str.replace('m', '').replace('<', ''))
                else:
                    return float('inf')
            except (ValueError, IndexError):
                return float('inf')
    
    arrivals_list.sort(key=eta_sort_key)
    
    return departures_list, arrivals_list