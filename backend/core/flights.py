"""
Flight analysis and tracking for VATSIM aircraft.
"""

from dataclasses import dataclass
from typing import Any, cast

from backend.core.calculations import calculate_eta, haversine_distance_nm
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
    departure: AirportInfo | None = (
        None  # For groupings: which airport they're departing from
    )


@dataclass
class ArrivalInfo:
    """Structured arrival flight information"""

    callsign: str
    origin: AirportInfo
    eta_display: str
    eta_local_time: str
    arrival: AirportInfo | None = (
        None  # For groupings: which airport they're arriving at
    )


def get_nearest_airport_if_on_ground(
    flight: dict[str, Any],
    airports: dict[str, dict[str, Any]],
    max_distance_nm: float = 6,
    max_groundspeed: float = 40,
) -> str | None:
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
    def filter_tracked(airport: dict[str, Any]) -> bool:
        return airport["icao"] in airports

    return spatial_index.find_nearest(
        flight_lat,
        flight_lon,
        max_distance_nm=max_distance_nm,
        filter_fn=filter_tracked,
    )


_MIN_GROUNDSPEED = 40
_APPROACH_DISTANCE_NM = 5.0


def _great_circle_to_arrival(
    flight: dict[str, Any], airports: dict[str, dict[str, Any]]
) -> float | None:
    """
    Return the remaining great-circle distance to the arrival airport, in nm.

    None unless the flight is an airborne inbound: groundspeed at or above the
    minimum, a filed arrival with coordinates among airports, and valid
    coordinates on both sides.

    Args:
        flight: Flight data dictionary
        airports: Dictionary of airport data

    Returns:
        Distance in nautical miles, or None when the flight is not an airborne inbound
    """
    # Use .get() for defensive access to potentially missing fields
    if flight.get("groundspeed", 0) < _MIN_GROUNDSPEED:
        return None

    arrival_airport = airports.get(flight.get("arrival"))
    if not arrival_airport:
        return None

    # Validate flight and airport coordinates
    flight_lat = flight.get("latitude")
    flight_lon = flight.get("longitude")
    airport_lat = arrival_airport.get("latitude")
    airport_lon = arrival_airport.get("longitude")

    if None in (flight_lat, flight_lon, airport_lat, airport_lon):
        return None

    try:
        return haversine_distance_nm(
            cast(float, flight_lat),
            cast(float, flight_lon),
            cast(float, airport_lat),
            cast(float, airport_lon),
        )
    except ValueError:
        # Invalid coordinates
        return None


def arrival_eta_in_window(
    flight: dict[str, Any],
    airports: dict[str, dict[str, Any]],
    aircraft_approach_speeds: dict[str, int] | None,
    max_eta_hours: float,
) -> tuple[str, str, float] | None:
    """
    Return an airborne inbound's calculate_eta result when it is inside the window.

    The overview's ARR column and the flight board share this one predicate, so
    both count a flight exactly when the ETA the board displays is within
    max_eta_hours. With max_eta_hours == 0 the window is off and every airborne
    inbound is returned, which is what the board lists.

    Args:
        flight: Flight data dictionary
        airports: Dictionary of airport data with coordinates and elevation
        aircraft_approach_speeds: Dictionary of aircraft approach speeds (optional)
        max_eta_hours: Maximum ETA in hours to count as an arrival (0 turns the window off)

    Returns:
        calculate_eta's (eta_display, eta_local_time, eta_hours) when the flight is an
        airborne inbound whose ETA is inside the window, None otherwise
    """
    distance = _great_circle_to_arrival(flight, airports)
    if distance is None:
        return None

    if max_eta_hours == 0:
        return calculate_eta(flight, airports, aircraft_approach_speeds)

    # (great circle - the final approach) / groundspeed is a sound lower bound on
    # calculate_eta, so a flight already past the window costs no call
    groundspeed = flight.get("groundspeed", 0)
    if (distance - _APPROACH_DISTANCE_NM) / groundspeed > max_eta_hours:
        return None

    eta = calculate_eta(flight, airports, aircraft_approach_speeds)
    if eta[2] <= max_eta_hours:
        return eta
    return None


def find_nearest_airport(
    flight: dict[str, Any], airports: dict[str, dict[str, Any]]
) -> str | None:
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
    def filter_tracked(airport: dict[str, Any]) -> bool:
        return airport["icao"] in airports

    return spatial_index.find_nearest(flight_lat, flight_lon, filter_fn=filter_tracked)


def get_airport_flight_details(
    airport_icao_or_list,
    disambiguator=None,
    all_airports_data: dict[str, dict[str, Any]] | None = None,
    aircraft_approach_speeds: dict[str, int] | None = None,
    vatsim_data: dict[str, Any] | None = None,
    airport_filters: dict[str, str] | None = None,
) -> tuple[list[DepartureInfo], list[ArrivalInfo]]:
    """
    Get detailed flight information for a specific airport or list of airports.
    Returns separate lists for departures and arrivals with structured data.

    Args:
        airport_icao_or_list: Either a single ICAO code (str) or a list of ICAO codes
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
        f"[BACKEND] get_airport_flight_details called with airport={airport_icao_or_list}"
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

    airport_icao_set = set(airport_icao_list)

    debug_logger.debug(f"[BACKEND] airport_icao_list: {airport_icao_list}")

    # Create airports dict for the specified airports
    airports = {
        icao: data
        for icao, data in all_airports_data.items()
        if icao in airport_icao_set
    }
    debug_logger.debug(f"[BACKEND] Created airports dict with {len(airports)} airports")

    def _should_show(icao: str, direction: str) -> bool:
        if not airport_filters:
            return True
        f = airport_filters.get(icao)
        return f is None or f == direction

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
        if departure and departure in airport_icao_set:
            if nearest_airport_if_on_ground == departure and _should_show(
                departure, "dep"
            ):
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
        elif (
            not departure
            and arrival
            and nearest_airport_if_on_ground
            and nearest_airport_if_on_ground in airport_icao_set
            and nearest_airport_if_on_ground != arrival
            and _should_show(nearest_airport_if_on_ground, "dep")
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
        if arrival and arrival in airport_icao_set and _should_show(arrival, "arr"):
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
            # For in-flight arrivals the window is off: the board lists every
            # airborne inbound, and the helper returns the ETA its row shows
            elif (
                eta := arrival_eta_in_window(
                    flight, all_airports_data, aircraft_approach_speeds, 0
                )
            ) is not None:
                arrival_airport_icao = arrival
                pretty_arrival_airport = (
                    disambiguator.get_pretty_name(arrival_airport_icao)
                    if disambiguator
                    else arrival_airport_icao
                )
                eta_display, eta_local_time, _ = eta

                if is_local_flight:
                    # Local flight in the air - show LOCAL for name and ---- for ICAO
                    arrivals_list.append(
                        ArrivalInfo(
                            callsign=callsign,
                            origin=AirportInfo(pretty_name="LOCAL", icao_code="----"),
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
        if (
            not departure
            and not arrival
            and nearest_airport_if_on_ground
            and nearest_airport_if_on_ground in airport_icao_set
            and _should_show(nearest_airport_if_on_ground, "dep")
        ):
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
