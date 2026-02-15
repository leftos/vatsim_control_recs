"""Route-based distance calculation for more accurate ETA.

Calculates remaining flying distance along a filed route instead of using
straight-line (great circle) distance. Falls back to None when the route
cannot be parsed or the aircraft cannot be located on the route.
"""

import math
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from backend.core.calculations import haversine_distance_nm
from backend.data.navaids import Waypoint, parse_route_string


# Earth radius in nautical miles (must match haversine_distance_nm)
_R_NM = 3440.065

# Maximum cross-track distance (nm) before we consider the aircraft off-route
_MAX_CROSS_TRACK_NM = 50.0


def get_route_remaining_distance(
    route_string: str,
    aircraft_lat: float,
    aircraft_lon: float,
    dest_lat: float,
    dest_lon: float,
    airports: Optional[Dict[str, Tuple[float, float]]] = None,
    departure: Optional[str] = None,
    arrival: Optional[str] = None,
) -> Optional[float]:
    """Calculate remaining route distance from current position to destination.

    Args:
        route_string: Filed route string (e.g., "SUNOL V27 BSR SAC")
        aircraft_lat: Aircraft current latitude
        aircraft_lon: Aircraft current longitude
        dest_lat: Destination airport latitude
        dest_lon: Destination airport longitude
        airports: Optional dict mapping ICAO codes to (lat, lon)
        departure: Departure airport ICAO (for SID expansion)
        arrival: Arrival airport ICAO (for STAR expansion)

    Returns:
        Remaining distance in NM, or None if route cannot be used
    """
    waypoints = _get_parsed_route(route_string, departure, arrival)
    if len(waypoints) < 2:
        return None

    return _calculate_route_remaining_distance(
        waypoints, aircraft_lat, aircraft_lon, dest_lat, dest_lon
    )


@lru_cache(maxsize=512)
def _get_parsed_route(
    route_string: str,
    departure: Optional[str] = None,
    arrival: Optional[str] = None,
) -> Tuple[Waypoint, ...]:
    """Parse and cache a route string into waypoints.

    Returns a tuple (hashable for lru_cache) instead of list.
    The airports dict is not passed here since it varies per call
    and would defeat caching. Airport lookups in parse_route_string
    are a secondary resolution path.
    """
    waypoints = parse_route_string(
        route_string, departure=departure, arrival=arrival
    )
    return tuple(waypoints)


def _calculate_route_remaining_distance(
    waypoints: Tuple[Waypoint, ...],
    aircraft_lat: float,
    aircraft_lon: float,
    dest_lat: float,
    dest_lon: float,
) -> Optional[float]:
    """Calculate remaining distance along route from aircraft to destination.

    Algorithm:
    1. Build coordinate list from waypoints + destination
    2. Find which segment the aircraft is currently on
    3. Sum distances from aircraft through remaining waypoints to destination
    """
    # Build coordinate list
    coords: List[Tuple[float, float]] = [
        (wp.latitude, wp.longitude) for wp in waypoints
    ]

    # Append destination if not already close to the last waypoint
    if coords:
        last = coords[-1]
        dist_to_dest = haversine_distance_nm(last[0], last[1], dest_lat, dest_lon)
        if dist_to_dest > 1.0:
            coords.append((dest_lat, dest_lon))

    if len(coords) < 2:
        return None

    seg_idx, remaining_in_seg = _find_current_segment(
        coords, aircraft_lat, aircraft_lon
    )

    if seg_idx < 0:
        return None

    # Sum: remaining in current segment + all subsequent segments
    total_remaining = remaining_in_seg

    for j in range(seg_idx + 1, len(coords) - 1):
        total_remaining += haversine_distance_nm(
            coords[j][0], coords[j][1],
            coords[j + 1][0], coords[j + 1][1],
        )

    return total_remaining


def _find_current_segment(
    coords: List[Tuple[float, float]],
    aircraft_lat: float,
    aircraft_lon: float,
) -> Tuple[int, float]:
    """Find which route segment the aircraft is currently on.

    Returns:
        (segment_index, remaining_distance_in_segment)
        segment_index is the index of the STARTING waypoint.
        Returns (-1, 0.0) if no segment matches.
    """
    best_segment_idx = -1
    best_cross_track = float("inf")
    best_remaining = 0.0

    for i in range(len(coords) - 1):
        seg_start = coords[i]
        seg_end = coords[i + 1]

        segment_length = haversine_distance_nm(
            seg_start[0], seg_start[1], seg_end[0], seg_end[1]
        )

        if segment_length < 0.1:
            continue

        cross_track, along_track = _cross_track_along_track(
            aircraft_lat, aircraft_lon,
            seg_start[0], seg_start[1],
            seg_end[0], seg_end[1],
        )

        # Check if projection falls within the segment (with tolerance)
        tolerance = max(20.0, segment_length * 0.15)
        if -tolerance <= along_track <= segment_length + tolerance:
            if cross_track < best_cross_track:
                best_cross_track = cross_track
                best_segment_idx = i
                best_remaining = max(0.0, segment_length - along_track)

    if best_cross_track > _MAX_CROSS_TRACK_NM:
        return (-1, 0.0)

    return (best_segment_idx, best_remaining)


def _cross_track_along_track(
    point_lat: float,
    point_lon: float,
    seg_start_lat: float,
    seg_start_lon: float,
    seg_end_lat: float,
    seg_end_lon: float,
) -> Tuple[float, float]:
    """Calculate cross-track and along-track distances on a sphere.

    Args:
        point_lat/lon: Aircraft position
        seg_start_lat/lon: Segment start point
        seg_end_lat/lon: Segment end point

    Returns:
        (cross_track_nm, along_track_nm) where:
        - cross_track_nm: Perpendicular distance from point to great circle
        - along_track_nm: Distance from segment start to the point's projection
    """
    lat1 = math.radians(seg_start_lat)
    lon1 = math.radians(seg_start_lon)
    lat2 = math.radians(seg_end_lat)
    lon2 = math.radians(seg_end_lon)
    lat_p = math.radians(point_lat)
    lon_p = math.radians(point_lon)

    # Angular distance from start to point
    d_sp = 2.0 * math.asin(
        math.sqrt(
            math.sin((lat_p - lat1) / 2.0) ** 2
            + math.cos(lat1) * math.cos(lat_p) * math.sin((lon_p - lon1) / 2.0) ** 2
        )
    )

    # Bearing from start to end
    theta_se = math.atan2(
        math.sin(lon2 - lon1) * math.cos(lat2),
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1),
    )

    # Bearing from start to point
    theta_sp = math.atan2(
        math.sin(lon_p - lon1) * math.cos(lat_p),
        math.cos(lat1) * math.sin(lat_p)
        - math.sin(lat1) * math.cos(lat_p) * math.cos(lon_p - lon1),
    )

    # Cross-track angular distance
    cross_track_rad = math.asin(
        math.sin(d_sp) * math.sin(theta_sp - theta_se)
    )

    # Along-track angular distance
    cos_cross = math.cos(cross_track_rad)
    if abs(cos_cross) < 1e-10:
        along_track_rad = 0.0
    else:
        # Clamp to avoid domain errors from floating point
        cos_ratio = math.cos(d_sp) / cos_cross
        cos_ratio = max(-1.0, min(1.0, cos_ratio))
        along_track_rad = math.acos(cos_ratio)

    return abs(cross_track_rad) * _R_NM, along_track_rad * _R_NM


def clear_route_cache() -> None:
    """Clear the parsed route cache."""
    _get_parsed_route.cache_clear()
