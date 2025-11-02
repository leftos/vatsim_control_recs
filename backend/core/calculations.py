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
        lat1: Latitude of first point in decimal degrees
        lon1: Longitude of first point in decimal degrees
        lat2: Latitude of second point in decimal degrees
        lon2: Longitude of second point in decimal degrees
    
    Returns:
        Distance in nautical miles
    """
    # Convert decimal degrees to radians
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    
    # Haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    r = 3440.065  # Radius of earth in nautical miles
    return c * r


def format_eta_display(eta_hours: float, arrivals_in_flight_count: int, arrivals_on_ground_count: int) -> str:
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
    if eta_hours == float('inf') and arrivals_on_ground_count > 0 and arrivals_in_flight_count == 0:
        return "LANDED"
    elif eta_hours == float('inf'):
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
    aircraft_approach_speeds: Optional[Dict[str, int]] = None
) -> Tuple[str, str, float]:
    """
    Calculate ETA for a flight and return display strings.
    Uses a two-phase calculation: current groundspeed for most of the journey,
    then approach speed for the final 5 nautical miles.
    
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
    if flight['arrival'] in airports_data and flight.get('groundspeed', 0) > 40:
        arrival_airport = airports_data[flight['arrival']]
        distance = haversine_distance_nm(
            flight['latitude'],
            flight['longitude'],
            arrival_airport['latitude'],
            arrival_airport['longitude']
        )
        
        # Default: use current groundspeed for entire distance
        groundspeed = flight['groundspeed']
        eta_hours = distance / groundspeed
        
        # If we have aircraft approach speeds, use more sophisticated calculation
        if aircraft_approach_speeds:
            # Extract aircraft type from flight plan
            aircraft_type = None
            if flight.get('flight_plan') and flight['flight_plan'].get('aircraft_short'):
                aircraft_type = flight['flight_plan']['aircraft_short']
            
            # If we have approach speed for this aircraft type, use two-phase calculation
            if aircraft_type and aircraft_type in aircraft_approach_speeds:
                approach_speed = aircraft_approach_speeds[aircraft_type]
                final_approach_distance = 5.0  # nautical miles
                
                if distance > final_approach_distance:
                    # Two-phase: current speed for most of journey, approach speed for final 5 nm
                    cruise_distance = distance - final_approach_distance
                    cruise_time = cruise_distance / groundspeed
                    approach_time = final_approach_distance / approach_speed
                    eta_hours = cruise_time + approach_time
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
    return "----", "----", float('inf')