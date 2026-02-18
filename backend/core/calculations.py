"""
Calculation utilities for distance and ETA computations.
"""

import math
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple


def haversine_distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points
    on the earth (specified in decimal degrees).

    Args:
        lat1: Latitude of first point in decimal degrees (-90 to 90)
        lon1: Longitude of first point in decimal degrees (-180 to 180)
        lat2: Latitude of second point in decimal degrees (-90 to 90)
        lon2: Longitude of second point in decimal degrees (-180 to 180)

    Returns:
        Distance in nautical miles

    Raises:
        ValueError: If coordinates are outside valid ranges
    """
    # Validate coordinate ranges to prevent invalid math operations
    if not (-90 <= lat1 <= 90):
        raise ValueError(f"lat1 must be between -90 and 90, got {lat1}")
    if not (-90 <= lat2 <= 90):
        raise ValueError(f"lat2 must be between -90 and 90, got {lat2}")
    if not (-180 <= lon1 <= 180):
        raise ValueError(f"lon1 must be between -180 and 180, got {lon1}")
    if not (-180 <= lon2 <= 180):
        raise ValueError(f"lon2 must be between -180 and 180, got {lon2}")

    # Convert decimal degrees to radians
    lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(math.radians, [lat1, lon1, lat2, lon2])

    # Haversine formula
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    r = 3440.065  # Radius of earth in nautical miles
    return c * r


def format_eta_display(
    eta_hours: float, arrivals_in_flight_count: int, arrivals_on_ground_count: int
) -> str:
    """
    Format ETA hours into a readable string.

    Args:
        eta_hours: ETA in hours
        arrivals_in_flight_count: Number of arrivals still in flight
        arrivals_on_ground_count: Number of arrivals on ground

    Returns:
        Formatted ETA string (e.g., "45m", "1h30m", "LANDED", or "")
    """
    # If there are no in-flight arrivals but there are arrivals on ground
    if (
        eta_hours == float("inf")
        and arrivals_on_ground_count > 0
        and arrivals_in_flight_count == 0
    ):
        return "LANDED"
    elif eta_hours == float("inf"):
        return ""  # No arrivals at all
    elif eta_hours < 1.0:
        minutes = int(eta_hours * 60)
        return f"{minutes}m" if minutes > 0 else "<1m"
    else:
        hours = int(eta_hours)
        minutes = int((eta_hours - hours) * 60)
        return f"{hours}h{minutes:02d}m"


def calculate_eta(
    flight: Dict[str, Any],
    airports_data: Dict[str, Dict[str, Any]],
    aircraft_approach_speeds: Optional[Dict[str, int]] = None,
) -> Tuple[str, str, float]:
    """
    Calculate ETA for a flight and return display strings.
    Uses a multi-phase calculation: current groundspeed above 10,000ft,
    clamped to 250kts below 10,000ft (using a 3-degree glide path to
    estimate the segment distance), then approach speed for the final 5nm.

    Args:
        flight: Flight data dictionary with position, speed, and destination
        airports_data: Dictionary of airport data with coordinates
        aircraft_approach_speeds: Optional dict mapping aircraft types to approach speeds

    Returns:
        Tuple of (eta_display, eta_local_time, eta_hours)
        - eta_display: Formatted ETA string (e.g., "45m")
        - eta_local_time: Local time of arrival (e.g., "14:30")
        - eta_hours: Raw ETA in hours
    """
    if flight["arrival"] in airports_data and flight.get("groundspeed", 0) > 40:
        arrival_airport = airports_data[flight["arrival"]]
        lateral_distance = haversine_distance_nm(
            flight["latitude"],
            flight["longitude"],
            arrival_airport["latitude"],
            arrival_airport["longitude"],
        )

        # Attempt route-based distance calculation for more accurate ETA
        flight_plan = flight.get("flight_plan")
        if flight_plan and flight_plan.get("route"):
            from backend.core.route_distance import get_route_remaining_distance

            route_distance = get_route_remaining_distance(
                flight_plan["route"],
                flight["latitude"],
                flight["longitude"],
                arrival_airport["latitude"],
                arrival_airport["longitude"],
                departure=flight_plan.get("departure"),
                arrival=flight_plan.get("arrival"),
            )
            # Use route distance if valid (must be >= straight-line to be plausible)
            if route_distance is not None and route_distance >= lateral_distance:
                lateral_distance = route_distance

        # Account for altitude: aircraft need distance to descend
        # Using the 3:1 rule: 3nm per 1000ft of altitude to lose
        APPROACH_AGL = 2500  # Target altitude AGL for approach (feet)
        DESCENT_GRADIENT = 3.0  # nm per 1000 feet of altitude loss

        current_altitude = flight.get("altitude", 0) or 0
        airport_elevation = arrival_airport.get("elevation", 0) or 0

        target_altitude = airport_elevation + APPROACH_AGL
        altitude_to_lose = max(0, current_altitude - target_altitude)
        descent_distance_required = (altitude_to_lose / 1000) * DESCENT_GRADIENT

        # Effective distance is the greater of lateral or descent requirement
        distance = max(lateral_distance, descent_distance_required)

        groundspeed = flight["groundspeed"]

        # Calculate below-10,000ft speed restriction segment
        # Below 10,000ft MSL, speed is clamped to 250kts using a naive
        # 3-degree glide path to estimate the distance of that segment
        BELOW_10K_SPEED_LIMIT = 250  # kts

        if current_altitude <= 10000:
            # Aircraft is already below 10k; entire distance is speed-restricted
            below_10k_distance = distance
        else:
            # Aircraft is above 10k; estimate below-10k segment from glide path
            if airport_elevation < 10000:
                descent_below_10k = (10000 - airport_elevation) / 1000 * DESCENT_GRADIENT
                below_10k_distance = min(descent_below_10k, distance)
            else:
                below_10k_distance = 0

        above_10k_distance = distance - below_10k_distance
        below_10k_speed = min(groundspeed, BELOW_10K_SPEED_LIMIT)

        # Default: two-phase (above 10k at groundspeed, below 10k at clamped speed)
        eta_hours = 0
        if above_10k_distance > 0:
            eta_hours += above_10k_distance / groundspeed
        if below_10k_distance > 0:
            eta_hours += below_10k_distance / below_10k_speed

        # If we have aircraft approach speeds, refine with a final approach phase
        if aircraft_approach_speeds:
            # Extract aircraft type from flight plan
            aircraft_type = None
            if flight.get("flight_plan") and flight["flight_plan"].get(
                "aircraft_short"
            ):
                aircraft_type = flight["flight_plan"]["aircraft_short"]

            if aircraft_type and aircraft_type in aircraft_approach_speeds:
                approach_speed = aircraft_approach_speeds[aircraft_type]
                final_approach_distance = 5.0  # nautical miles

                if distance > final_approach_distance:
                    # Three-phase: above 10k at groundspeed, below 10k at clamped speed,
                    # final approach at approach speed
                    # Final approach is closest to the airport (within the below-10k zone)
                    below_10k_cruise = max(0, below_10k_distance - final_approach_distance)
                    # If approach extends above 10k (high-elevation airports), reduce above_10k
                    approach_above_10k = max(0, final_approach_distance - below_10k_distance)
                    above_10k_cruise = max(0, above_10k_distance - approach_above_10k)

                    eta_hours = 0
                    if above_10k_cruise > 0:
                        eta_hours += above_10k_cruise / groundspeed
                    if below_10k_cruise > 0:
                        eta_hours += below_10k_cruise / below_10k_speed
                    eta_hours += final_approach_distance / approach_speed
                else:
                    # Already within final approach distance, use minimum of current speed or approach speed
                    # (aircraft may already be slower than approach speed)
                    effective_speed = min(groundspeed, approach_speed)
                    eta_hours = distance / effective_speed

        eta_display = format_eta_display(eta_hours, 1, 0)

        current_time_utc = datetime.now(timezone.utc)
        arrival_time_utc = current_time_utc + timedelta(hours=eta_hours)
        arrival_time_local = arrival_time_utc.astimezone()
        eta_local_time = arrival_time_local.strftime("%H:%M")

        return eta_display, eta_local_time, eta_hours
    return "----", "----", float("inf")


def calculate_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate initial bearing from point 1 to point 2.

    Args:
        lat1: Latitude of first point in decimal degrees
        lon1: Longitude of first point in decimal degrees
        lat2: Latitude of second point in decimal degrees
        lon2: Longitude of second point in decimal degrees

    Returns:
        Bearing in degrees (0-360, where 0=N, 90=E, 180=S, 270=W)
    """
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lon = math.radians(lon2 - lon1)

    x = math.sin(delta_lon) * math.cos(lat2_rad)
    y = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(
        lat2_rad
    ) * math.cos(delta_lon)

    bearing = math.atan2(x, y)
    return (math.degrees(bearing) + 360) % 360


def bearing_to_compass(bearing: float) -> str:
    """
    Convert bearing in degrees to compass direction.

    Args:
        bearing: Bearing in degrees (0-360)

    Returns:
        Compass direction string (N, NE, E, SE, S, SW, W, NW)
    """
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    index = round(bearing / 45) % 8
    return directions[index]
