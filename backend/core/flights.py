"""
Flight analysis and tracking for VATSIM aircraft.
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple, cast

from backend.core.calculations import haversine_distance_nm, calculate_eta
from backend.core.spatial import get_airport_spatial_index


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
    departure: Optional[AirportInfo] = (
        None  # For groupings: which airport they're departing from
    )


@dataclass
class ArrivalInfo:
    """Structured arrival flight information"""

    callsign: str
    origin: AirportInfo
    eta_display: str
    eta_local_time: str
    arrival: Optional[AirportInfo] = (
        None  # For groupings: which airport they're arriving at
    )


def get_nearest_airport_if_on_ground(
    flight: Dict[str, Any],
    airports: Dict[str, Dict[str, Any]],
    max_distance_nm: float = 6,
    max_groundspeed: float = 40,
) -> Optional[str]:
    """
    Determine if a flight is on the ground at an airport.
    Based on distance from airport and groundspeed.

    Uses spatial indexing for O(1) average case lookup instead of O(n) linear scan.

    Args:
        flight: Flight data dictionary
        airports: Dictionary of airport data
        max_distance_nm: Maximum distance in nautical miles to consider "on ground" (default: 6)
        max_groundspeed: Maximum groundspeed in knots to consider "on ground" (default: 40)

    Returns:
        Airport ICAO code if flight is on ground at that airport, None otherwise
    """
    # Use .get() for defensive access to potentially missing fields
    groundspeed = flight.get("groundspeed")
    if groundspeed is None or groundspeed > max_groundspeed:
        return None

    flight_lat = flight.get("latitude")
    flight_lon = flight.get("longitude")
    if flight_lat is None or flight_lon is None:
        return None

    # Use spatial index for efficient nearest airport lookup
    spatial_index = get_airport_spatial_index(airports)

    # Filter to only airports in our tracked set
    def filter_tracked(airport: Dict[str, Any]) -> bool:
        return airport["icao"] in airports

    return spatial_index.find_nearest(
        flight_lat,
        flight_lon,
        max_distance_nm=max_distance_nm,
        filter_fn=filter_tracked,
    )


def is_flight_flying_near_arrival(
    flight: Dict[str, Any],
    airports: Dict[str, Dict[str, Any]],
    max_eta_hours: float = 1.0,
    min_groundspeed: float = 40,
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
    # Use .get() for defensive access to potentially missing fields
    groundspeed = flight.get("groundspeed", 0)
    if groundspeed < min_groundspeed:
        return False

    # For flights with arrival airport in flight plan
    flight_arrival = flight.get("arrival")
    if flight_arrival and flight_arrival in airports:
        arrival_airport = airports.get(flight_arrival)
        if not arrival_airport:
            return False

        # Validate flight and airport coordinates
        flight_lat = flight.get("latitude")
        flight_lon = flight.get("longitude")
        airport_lat = arrival_airport.get("latitude")
        airport_lon = arrival_airport.get("longitude")

        if None in (flight_lat, flight_lon, airport_lat, airport_lon):
            return False

        # Calculate distance to arrival airport
        try:
            distance = haversine_distance_nm(
                cast(float, flight_lat),
                cast(float, flight_lon),
                cast(float, airport_lat),
                cast(float, airport_lon),
            )
        except ValueError:
            # Invalid coordinates
            return False

        # Calculate estimated time of arrival (in hours)
        if groundspeed > 0:
            eta_hours = distance / groundspeed
            return max_eta_hours == 0 or eta_hours <= max_eta_hours

    return False


def find_nearest_airport(
    flight: Dict[str, Any], airports: Dict[str, Dict[str, Any]]
) -> Optional[str]:
    """
    Find the nearest airport to a flight's current position.

    Uses spatial indexing for O(1) average case lookup instead of O(n) linear scan.

    Args:
        flight: Flight data dictionary
        airports: Dictionary of airport data

    Returns:
        ICAO code of the nearest airport, or None if position not available
    """
    # Use .get() for defensive access
    flight_lat = flight.get("latitude")
    flight_lon = flight.get("longitude")
    if flight_lat is None or flight_lon is None:
        return None

    # Use spatial index for efficient lookup
    spatial_index = get_airport_spatial_index(airports)

    # Filter to only airports in our tracked set
    def filter_tracked(airport: Dict[str, Any]) -> bool:
        return airport["icao"] in airports

    return spatial_index.find_nearest(flight_lat, flight_lon, filter_fn=filter_tracked)


def get_airport_flight_details(
    airport_icao_or_list,
    max_eta_hours: float = 1.0,
    disambiguator=None,
    all_airports_data: Optional[Dict[str, Dict[str, Any]]] = None,
    aircraft_approach_speeds: Optional[Dict[str, int]] = None,
    vatsim_data: Optional[Dict[str, Any]] = None,
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
    from ui import debug_logger

    debug_logger.debug(
        f"[BACKEND] get_airport_flight_details called with airport={airport_icao_or_list}, max_eta={max_eta_hours}"
    )

    if all_airports_data is None or vatsim_data is None:
        debug_logger.error(
            f"[BACKEND] all_airports_data is None: {all_airports_data is None}, vatsim_data is None: {vatsim_data is None}"
        )
        return [], []

    debug_logger.debug(
        f"[BACKEND] all_airports_data has {len(all_airports_data)} airports, vatsim_data keys: {list(vatsim_data.keys())}"
    )

    # Normalize input to a list
    if isinstance(airport_icao_or_list, str):
        airport_icao_list = [airport_icao_or_list]
    else:
        airport_icao_list = list(airport_icao_or_list)

    debug_logger.debug(f"[BACKEND] airport_icao_list: {airport_icao_list}")

    # Create airports dict for the specified airports
    airports = {
        icao: data
        for icao, data in all_airports_data.items()
        if icao in airport_icao_list
    }
    debug_logger.debug(f"[BACKEND] Created airports dict with {len(airports)} airports")

    # Filter flights - we need all flights that involve our airports
    flights = filter_flights_by_airports(
        vatsim_data, all_airports_data, airport_icao_list
    )
    debug_logger.debug(
        f"[BACKEND] filter_flights_by_airports returned {len(flights)} flights"
    )

    departures_list = []
    arrivals_list = []

    debug_logger.debug(f"[BACKEND] Processing {len(flights)} flights...")

    for flight in flights:
        # Safely extract required fields with defensive access
        callsign = flight.get("callsign")
        departure = flight.get("departure")
        arrival = flight.get("arrival")

        # Skip malformed flights missing callsign
        if not callsign:
            debug_logger.debug(
                f"[BACKEND] Skipping flight with missing callsign: {flight}"
            )
            continue

        nearest_airport_if_on_ground = get_nearest_airport_if_on_ground(
            flight, airports
        )

        # Check if this is a local flight (departure == arrival)
        is_local_flight = departure and arrival and departure == arrival

        # Check if this is a departure (on ground at departure airport)
        if departure and departure in airport_icao_list:
            if nearest_airport_if_on_ground == departure:
                # Flight is on ground at one of our airports, preparing to depart
                if is_local_flight:
                    # Local flight - show LOCAL for name and ---- for ICAO
                    departure_airport_icao = departure
                    pretty_departure_airport = (
                        disambiguator.get_pretty_name(departure_airport_icao)
                        if disambiguator
                        else departure_airport_icao
                    )
                    departures_list.append(
                        DepartureInfo(
                            callsign=callsign,
                            destination=AirportInfo(
                                pretty_name="LOCAL", icao_code="----"
                            ),
                            departure=AirportInfo(
                                pretty_name=pretty_departure_airport,
                                icao_code=departure_airport_icao,
                            ),
                        )
                    )
                else:
                    destination = arrival if arrival else "----"
                    debug_logger.debug(
                        f"[BACKEND] Departure {callsign}: destination={destination}, disambiguator={disambiguator is not None}"
                    )
                    if disambiguator and destination != "----":
                        pretty_destination = disambiguator.get_pretty_name(destination)
                        debug_logger.debug(
                            f"[BACKEND] Departure {callsign}: got pretty_destination={pretty_destination}"
                        )
                    else:
                        pretty_destination = destination

                    # Add departure airport info for groupings
                    departure_airport_icao = departure
                    pretty_departure_airport = (
                        disambiguator.get_pretty_name(departure_airport_icao)
                        if disambiguator
                        else departure_airport_icao
                    )

                    departures_list.append(
                        DepartureInfo(
                            callsign=callsign,
                            destination=AirportInfo(
                                pretty_name=pretty_destination, icao_code=destination
                            ),
                            departure=AirportInfo(
                                pretty_name=pretty_departure_airport,
                                icao_code=departure_airport_icao,
                            ),
                        )
                    )
        # Also handle flights with only arrival filed, on ground at one of our airports
        # But NOT if they're already at the arrival airport (those are arrivals, not departures)
        elif not departure and arrival and nearest_airport_if_on_ground:
            if (
                nearest_airport_if_on_ground in airport_icao_list
                and nearest_airport_if_on_ground != arrival
            ):
                # Flight is on ground at one of our airports (not the arrival) with only arrival in flight plan
                destination = arrival
                pretty_destination = (
                    disambiguator.get_pretty_name(destination)
                    if disambiguator
                    else destination
                )

                # Use nearest_airport as departure airport
                pretty_departure_airport = (
                    disambiguator.get_pretty_name(nearest_airport_if_on_ground)
                    if disambiguator
                    else nearest_airport_if_on_ground
                )

                departures_list.append(
                    DepartureInfo(
                        callsign=callsign,
                        destination=AirportInfo(
                            pretty_name=pretty_destination, icao_code=destination
                        ),
                        departure=AirportInfo(
                            pretty_name=pretty_departure_airport,
                            icao_code=nearest_airport_if_on_ground,
                        ),
                    )
                )

        # Check if this is an arrival (either on ground at arrival or flying nearby)
        if arrival and arrival in airport_icao_list:
            # Skip if departure == arrival and aircraft is on ground (already added as departure)
            if is_local_flight and nearest_airport_if_on_ground == arrival:
                pass  # Already handled as departure above
            elif nearest_airport_if_on_ground == arrival:
                # Flight is on ground at arrival airport
                arrival_airport_icao = arrival
                pretty_arrival_airport = (
                    disambiguator.get_pretty_name(arrival_airport_icao)
                    if disambiguator
                    else arrival_airport_icao
                )

                if is_local_flight:
                    # Local flight - show LOCAL for name and ---- for ICAO
                    arrivals_list.append(
                        ArrivalInfo(
                            callsign=callsign,
                            origin=AirportInfo(pretty_name="LOCAL", icao_code="----"),
                            eta_display="LANDED",
                            eta_local_time="----",
                            arrival=AirportInfo(
                                pretty_name=pretty_arrival_airport,
                                icao_code=arrival_airport_icao,
                            ),
                        )
                    )
                else:
                    origin = departure if departure else "----"
                    pretty_origin = (
                        disambiguator.get_pretty_name(origin)
                        if disambiguator
                        else origin
                    )
                    debug_logger.debug(
                        f"[BACKEND] Arrival {callsign} LANDED: origin={origin}, pretty_origin={pretty_origin}"
                    )
                    arrivals_list.append(
                        ArrivalInfo(
                            callsign=callsign,
                            origin=AirportInfo(
                                pretty_name=pretty_origin, icao_code=origin
                            ),
                            eta_display="LANDED",
                            eta_local_time="----",
                            arrival=AirportInfo(
                                pretty_name=pretty_arrival_airport,
                                icao_code=arrival_airport_icao,
                            ),
                        )
                    )
            # For in-flight arrivals, check if it's an arrival first, then calculate ETA
            # is_flight_flying_near_arrival uses max_eta_hours=0 to check ALL arrivals
            elif is_flight_flying_near_arrival(
                flight, all_airports_data, max_eta_hours=0
            ):
                arrival_airport_icao = arrival
                pretty_arrival_airport = (
                    disambiguator.get_pretty_name(arrival_airport_icao)
                    if disambiguator
                    else arrival_airport_icao
                )

                if is_local_flight:
                    # Local flight in the air - show LOCAL for name and ---- for ICAO
                    eta_display, eta_local_time, eta_hours = calculate_eta(
                        flight, all_airports_data, aircraft_approach_speeds
                    )
                    # Add to list if it meets the original max_eta_hours criteria
                    if max_eta_hours == 0 or eta_hours <= max_eta_hours:
                        arrivals_list.append(
                            ArrivalInfo(
                                callsign=callsign,
                                origin=AirportInfo(
                                    pretty_name="LOCAL", icao_code="----"
                                ),
                                eta_display=eta_display,
                                eta_local_time=eta_local_time,
                                arrival=AirportInfo(
                                    pretty_name=pretty_arrival_airport,
                                    icao_code=arrival_airport_icao,
                                ),
                            )
                        )
                else:
                    origin = departure if departure else "----"
                    pretty_origin = (
                        disambiguator.get_pretty_name(origin)
                        if disambiguator
                        else origin
                    )
                    eta_display, eta_local_time, eta_hours = calculate_eta(
                        flight, all_airports_data, aircraft_approach_speeds
                    )
                    # Add to list if it meets the original max_eta_hours criteria
                    if max_eta_hours == 0 or eta_hours <= max_eta_hours:
                        arrivals_list.append(
                            ArrivalInfo(
                                callsign=callsign,
                                origin=AirportInfo(
                                    pretty_name=pretty_origin, icao_code=origin
                                ),
                                eta_display=eta_display,
                                eta_local_time=eta_local_time,
                                arrival=AirportInfo(
                                    pretty_name=pretty_arrival_airport,
                                    icao_code=arrival_airport_icao,
                                ),
                            )
                        )
            else:
                # Flight has arrival filed but is on ground (not at arrival airport, likely at departure)
                # Show with ETA="----" to indicate they haven't departed yet
                arrival_airport_icao = arrival
                pretty_arrival_airport = (
                    disambiguator.get_pretty_name(arrival_airport_icao)
                    if disambiguator
                    else arrival_airport_icao
                )

                if is_local_flight:
                    # Local flight not yet departed - show LOCAL for name and ---- for ICAO
                    arrivals_list.append(
                        ArrivalInfo(
                            callsign=callsign,
                            origin=AirportInfo(pretty_name="LOCAL", icao_code="----"),
                            eta_display="----",
                            eta_local_time="----",
                            arrival=AirportInfo(
                                pretty_name=pretty_arrival_airport,
                                icao_code=arrival_airport_icao,
                            ),
                        )
                    )
                else:
                    origin = departure if departure else "----"
                    pretty_origin = (
                        disambiguator.get_pretty_name(origin)
                        if disambiguator
                        else origin
                    )
                    arrivals_list.append(
                        ArrivalInfo(
                            callsign=callsign,
                            origin=AirportInfo(
                                pretty_name=pretty_origin, icao_code=origin
                            ),
                            eta_display="----",
                            eta_local_time="----",
                            arrival=AirportInfo(
                                pretty_name=pretty_arrival_airport,
                                icao_code=arrival_airport_icao,
                            ),
                        )
                    )

        # Handle flights on ground without flight plans
        if not departure and not arrival and nearest_airport_if_on_ground:
            if nearest_airport_if_on_ground in airport_icao_list:
                # Count as departure with unknown destination
                pretty_departure_airport = (
                    disambiguator.get_pretty_name(nearest_airport_if_on_ground)
                    if disambiguator
                    else nearest_airport_if_on_ground
                )
                departures_list.append(
                    DepartureInfo(
                        callsign=callsign,
                        destination=AirportInfo(pretty_name="----", icao_code="----"),
                        departure=AirportInfo(
                            pretty_name=pretty_departure_airport,
                            icao_code=nearest_airport_if_on_ground,
                        ),
                    )
                )

    debug_logger.debug(
        f"[BACKEND] Final counts - departures: {len(departures_list)}, arrivals: {len(arrivals_list)}"
    )

    # Sort departures by callsign
    departures_list.sort(key=lambda x: x.callsign)

    # Sort arrivals by ETA (convert eta_display to sortable value)
    def eta_sort_key(arrival):
        eta_str = arrival.eta_display
        if eta_str == "LANDED":
            return -1  # Already landed flights first
        elif eta_str == "----" or eta_str == "":
            return float("inf")  # Unknown ETA last
        else:
            # Parse ETA strings like "45m", "1h30m", "2h", etc.
            try:
                if "h" in eta_str and "m" in eta_str:
                    parts = eta_str.replace("h", " ").replace("m", "").split()
                    return int(parts[0]) * 60 + int(parts[1])
                elif "h" in eta_str:
                    return int(eta_str.replace("h", "")) * 60
                elif "m" in eta_str:
                    return int(eta_str.replace("m", "").replace("<", ""))
                else:
                    return float("inf")
            except (ValueError, IndexError):
                return float("inf")

    arrivals_list.sort(key=eta_sort_key)

    return departures_list, arrivals_list
