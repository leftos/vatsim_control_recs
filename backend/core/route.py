"""
Route utilities for sampling points along flight paths.
"""

import math
import re
from typing import List, Tuple, Dict, Any, Optional

from backend.core.calculations import haversine_distance_nm


def parse_route_waypoints(route_string: str) -> List[str]:
    """
    Parse a flight plan route string to extract waypoint/fix identifiers.

    Filters out airways (like J1, V23, Q100), SID/STAR suffixes, and
    keeps only named fixes and waypoints.

    Args:
        route_string: Route string from flight plan (e.g., "SFOXX Q61 CEDES HADLY2")

    Returns:
        List of waypoint/fix identifiers in order
    """
    if not route_string:
        return []

    # Split into components
    parts = route_string.upper().split()

    waypoints = []
    # Airways pattern: letter(s) followed by numbers (J1, V23, Q100, UL9, etc.)
    airway_pattern = re.compile(r"^[A-Z]{1,2}\d+$")
    # DCT (direct) pattern
    dct_pattern = re.compile(r"^DCT$")

    for part in parts:
        # Skip airways
        if airway_pattern.match(part):
            continue
        # Skip DCT
        if dct_pattern.match(part):
            continue
        # Skip speed/altitude restrictions like N0450F350
        if re.match(r"^[NK]\d{4}[FA]\d{3}$", part):
            continue
        # Skip pure numbers
        if part.isdigit():
            continue
        # Skip very short items (likely not waypoints)
        if len(part) < 2:
            continue

        waypoints.append(part)

    return waypoints


def interpolate_great_circle(
    lat1: float, lon1: float, lat2: float, lon2: float, fraction: float
) -> Tuple[float, float]:
    """
    Interpolate a point along the great circle path between two points.

    Args:
        lat1, lon1: Start point coordinates (degrees)
        lat2, lon2: End point coordinates (degrees)
        fraction: Fraction along the path (0.0 = start, 1.0 = end)

    Returns:
        Tuple of (latitude, longitude) in degrees
    """
    # Convert to radians
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    # Calculate angular distance
    d_lat = lat2_rad - lat1_rad
    d_lon = lon2_rad - lon1_rad
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(d_lon / 2) ** 2
    )
    delta = 2 * math.asin(math.sqrt(a))

    if delta < 1e-10:
        return lat1, lon1

    # Interpolate along great circle
    A = math.sin((1 - fraction) * delta) / math.sin(delta)
    B = math.sin(fraction * delta) / math.sin(delta)

    x = A * math.cos(lat1_rad) * math.cos(lon1_rad) + B * math.cos(lat2_rad) * math.cos(
        lon2_rad
    )
    y = A * math.cos(lat1_rad) * math.sin(lon1_rad) + B * math.cos(lat2_rad) * math.sin(
        lon2_rad
    )
    z = A * math.sin(lat1_rad) + B * math.sin(lat2_rad)

    lat = math.atan2(z, math.sqrt(x**2 + y**2))
    lon = math.atan2(y, x)

    return math.degrees(lat), math.degrees(lon)


def sample_route_points(
    dep_lat: float,
    dep_lon: float,
    arr_lat: float,
    arr_lon: float,
    interval_nm: float = 150.0,
    min_points: int = 0,
    max_points: int = 10,
) -> List[Tuple[float, float, float]]:
    """
    Sample points along the great circle route at regular intervals.

    Args:
        dep_lat, dep_lon: Departure coordinates
        arr_lat, arr_lon: Arrival coordinates
        interval_nm: Approximate interval between sample points in nm
        min_points: Minimum number of enroute points (excluding dep/arr)
        max_points: Maximum number of enroute points

    Returns:
        List of (lat, lon, distance_from_dep_nm) tuples for enroute points
        Does NOT include departure or arrival points.
    """
    total_distance = haversine_distance_nm(dep_lat, dep_lon, arr_lat, arr_lon)

    if total_distance < interval_nm:
        return []

    # Calculate number of segments
    num_segments = max(1, int(total_distance / interval_nm))
    num_points = min(max_points, max(min_points, num_segments - 1))

    if num_points == 0:
        return []

    points = []
    for i in range(1, num_points + 1):
        fraction = i / (num_points + 1)
        lat, lon = interpolate_great_circle(
            dep_lat, dep_lon, arr_lat, arr_lon, fraction
        )
        distance = total_distance * fraction
        points.append((lat, lon, distance))

    return points


def find_enroute_airports(
    sample_points: List[Tuple[float, float, float]],
    airports_data: Dict[str, Dict[str, Any]],
    search_radius_nm: float = 100.0,
    prefer_metar: bool = True,
) -> List[Dict[str, Any]]:
    """
    Find airports near each sample point along the route.

    Args:
        sample_points: List of (lat, lon, distance_nm) from sample_route_points
        airports_data: Dictionary of airport data with coordinates
        search_radius_nm: Radius to search for airports at each point
        prefer_metar: Prefer airports likely to have METAR (larger airports)

    Returns:
        List of dicts with 'icao', 'distance_nm', 'lat', 'lon' for each enroute point.
        Returns at most one airport per sample point.
    """
    from backend.core.spatial import get_airport_spatial_index

    enroute = []
    used_icaos = set()

    index = get_airport_spatial_index(airports_data)

    for lat, lon, route_distance in sample_points:
        # Find airports near this point
        nearby = index.find_within_distance(lat, lon, max_distance_nm=search_radius_nm)

        # Pick the best airport (prefer larger airports with likely METAR)
        best = None
        best_score = -1

        for icao, distance in nearby:
            if icao in used_icaos:
                continue

            data = airports_data.get(icao, {})

            # Score based on indicators of airport size/importance
            score = 0

            # FAR 139 certification indicates commercial service airports
            far139 = data.get("far139", "")
            if far139:
                score += 80

            # Towered airports are generally larger
            tower_type = data.get("tower_type", "")
            if tower_type == "ATCT":
                score += 50
            elif tower_type == "NON-ATCT":
                score += 5

            # Check name for indicators of major airports
            name = data.get("name", "").upper()
            if "INTL" in name or "INTERNATIONAL" in name:
                score += 100
            elif "REGIONAL" in name or "RGNL" in name:
                score += 40
            elif "MUNICIPAL" in name or "MUNI" in name:
                score += 20

            # ICAO codes starting with K (US) that are 4 chars are more likely major
            if icao.startswith("K") and len(icao) == 4:
                score += 10

            # Penalize airports with very short names (often private)
            if len(name) < 5:
                score -= 20

            # Prefer closer airports, but not too strongly
            score -= distance * 0.2

            if score > best_score:
                best_score = score
                best = {
                    "icao": icao,
                    "distance_nm": route_distance,
                    "lat": data.get("latitude", lat),
                    "lon": data.get("longitude", lon),
                    "name": data.get("name", icao),
                }

        if best:
            enroute.append(best)
            used_icaos.add(best["icao"])

    return enroute


def determine_runway_from_wind(
    wind_str: str, runways: List[Dict[str, Any]]
) -> Optional[str]:
    """
    Determine the most likely runway based on wind direction.

    Args:
        wind_str: Wind string from METAR (e.g., "28012KT", "VRB05KT")
        runways: List of runway dicts with 'le_ident', 'he_ident', 'le_heading_degT', 'he_heading_degT'

    Returns:
        Runway identifier string (e.g., "28L/R") or None if can't determine
    """
    import re

    if not wind_str or not runways:
        return None

    # Parse wind direction
    match = re.match(r"(\d{3})(\d{2,3})(G\d{2,3})?(KT|MPS)", wind_str)
    if not match:
        # Variable wind - can't determine runway
        if "VRB" in wind_str:
            return None
        return None

    wind_dir = int(match.group(1))

    # Find runway most aligned with wind (into the wind)
    best_runway = None
    best_headwind = -999

    for rwy in runways:
        # Check low-end runway
        le_hdg = rwy.get("le_heading_degT")
        le_ident = rwy.get("le_ident", "")
        if le_hdg is not None and le_ident:
            try:
                le_hdg = float(le_hdg)
                # Calculate headwind component (higher = more headwind)
                angle_diff = abs((wind_dir - le_hdg + 180) % 360 - 180)
                headwind = math.cos(math.radians(angle_diff))
                if headwind > best_headwind:
                    best_headwind = headwind
                    best_runway = le_ident
            except (ValueError, TypeError):
                pass

        # Check high-end runway
        he_hdg = rwy.get("he_heading_degT")
        he_ident = rwy.get("he_ident", "")
        if he_hdg is not None and he_ident:
            try:
                he_hdg = float(he_hdg)
                angle_diff = abs((wind_dir - he_hdg + 180) % 360 - 180)
                headwind = math.cos(math.radians(angle_diff))
                if headwind > best_headwind:
                    best_headwind = headwind
                    best_runway = he_ident
            except (ValueError, TypeError):
                pass

    return best_runway


def format_ete(distance_nm: float, groundspeed: float) -> str:
    """
    Format estimated time enroute.

    Args:
        distance_nm: Distance in nautical miles
        groundspeed: Ground speed in knots

    Returns:
        Formatted ETE string like "+1:30" or "+0:45"
    """
    if groundspeed <= 0:
        return "---"

    hours = distance_nm / groundspeed
    total_minutes = int(hours * 60)
    h = total_minutes // 60
    m = total_minutes % 60

    return f"+{h}:{m:02d}"
