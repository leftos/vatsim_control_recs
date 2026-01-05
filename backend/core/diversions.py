"""Diversion airport finder.

This module provides functionality to find suitable diversion airports
based on aircraft performance, runway requirements, available approaches,
weather conditions, and ATC staffing.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.core.calculations import calculate_bearing, bearing_to_compass
from backend.core.spatial import get_airport_spatial_index
from backend.core.aircraft_performance import get_required_runway_length
from backend.data.runways import get_longest_runway, get_runway_summary
from backend.data.cifp import get_approach_list_for_airport


@dataclass
class DiversionFilters:
    """Filters for diversion airport search."""

    require_runway_capability: bool = True  # Filter by aircraft runway requirement
    require_approaches: bool = False  # Only show airports with instrument approaches
    require_good_weather: bool = False  # Only show VFR/MVFR conditions
    require_staffed: bool = False  # Only show airports with ATC online


@dataclass
class DiversionOption:
    """A potential diversion airport with all relevant information."""

    icao: str
    name: str
    distance_nm: float
    bearing_deg: float
    bearing_compass: str  # e.g., "NE", "SW"
    longest_runway_ft: Optional[int]
    runway_summary: Optional[str]  # e.g., "10000ft (28L/10R)"
    approaches: List[str]  # e.g., ["ILS RWY 28R", "RNAV (GPS) Z RWY 28L"]
    has_approaches: bool
    weather_category: Optional[str] = None  # VFR/MVFR/IFR/LIFR
    weather_details: Optional[str] = None  # e.g., "10SM BKN050"
    staffed_positions: List[str] = field(default_factory=list)  # e.g., ["TWR", "APP"]
    is_staffed: bool = False

    @property
    def meets_runway_requirement(self) -> bool:
        """Check if runway data is available."""
        return self.longest_runway_ft is not None

    @property
    def approach_count(self) -> int:
        """Get number of available approaches."""
        return len(self.approaches)


def find_nearby_airports(
    lat: float,
    lon: float,
    airports_data: Dict[str, Dict[str, Any]],
    radius_nm: float = 100.0,
    max_results: int = 500,
) -> List[Tuple[str, float, float]]:
    """Find airports within a given radius.

    Args:
        lat: Search center latitude
        lon: Search center longitude
        airports_data: Dictionary of all airport data
        radius_nm: Search radius in nautical miles
        max_results: Maximum results to return

    Returns:
        List of (icao, distance_nm, bearing_deg) tuples, sorted by distance
    """
    spatial_index = get_airport_spatial_index(airports_data)
    nearby = spatial_index.find_within_distance(lat, lon, radius_nm)

    results = []
    for icao, distance in nearby[:max_results]:
        airport_data = airports_data.get(icao, {})
        apt_lat = airport_data.get("latitude")
        apt_lon = airport_data.get("longitude")

        if apt_lat is not None and apt_lon is not None:
            bearing = calculate_bearing(lat, lon, apt_lat, apt_lon)
            results.append((icao, distance, bearing))

    return results


def find_suitable_diversions(
    lat: float,
    lon: float,
    aircraft_type: str,
    airports_data: Dict[str, Dict[str, Any]],
    radius_nm: float = 100.0,
    filters: Optional[DiversionFilters] = None,
    weather_data: Optional[Dict[str, Tuple[str, str]]] = None,
    controller_data: Optional[Dict[str, List[str]]] = None,
    max_results: int = 50,
) -> List[DiversionOption]:
    """Find airports suitable for diversion.

    Args:
        lat: Current aircraft latitude
        lon: Current aircraft longitude
        aircraft_type: Aircraft ICAO type code (e.g., "B738")
        airports_data: Dictionary of all airport data
        radius_nm: Search radius in nautical miles (default: 100)
        filters: Optional filter settings
        weather_data: Optional dict mapping ICAO to (category, details) tuples
        controller_data: Optional dict mapping ICAO to list of staffed positions
        max_results: Maximum results to return

    Returns:
        List of DiversionOption objects, sorted by distance
    """
    if filters is None:
        filters = DiversionFilters()

    # Get required runway length for this aircraft
    required_runway = (
        get_required_runway_length(aircraft_type) if aircraft_type else None
    )

    # Find nearby airports
    nearby = find_nearby_airports(lat, lon, airports_data, radius_nm, max_results=500)

    diversions: List[DiversionOption] = []

    for icao, distance, bearing in nearby:
        airport_data = airports_data.get(icao, {})
        name = airport_data.get("name", icao)

        # Get runway information
        longest_runway = get_longest_runway(icao)
        runway_summary = get_runway_summary(icao)

        # Check runway capability filter
        if filters.require_runway_capability and required_runway:
            if longest_runway is None or longest_runway < required_runway:
                continue

        # Get approach information
        approaches = get_approach_list_for_airport(icao)
        has_approaches_flag = len(approaches) > 0

        # Check approaches filter
        if filters.require_approaches and not has_approaches_flag:
            continue

        # Get weather information
        weather_category = None
        weather_details = None
        if weather_data and icao in weather_data:
            weather_category, weather_details = weather_data[icao]

        # Check weather filter
        if filters.require_good_weather and weather_category:
            if weather_category not in ("VFR", "MVFR"):
                continue

        # Get controller information
        staffed_positions: List[str] = []
        is_staffed = False
        if controller_data and icao in controller_data:
            staffed_positions = controller_data[icao]
            is_staffed = len(staffed_positions) > 0

        # Check staffing filter
        if filters.require_staffed and not is_staffed:
            continue

        # Create diversion option
        diversion = DiversionOption(
            icao=icao,
            name=name,
            distance_nm=round(distance, 1),
            bearing_deg=bearing,
            bearing_compass=bearing_to_compass(bearing),
            longest_runway_ft=longest_runway,
            runway_summary=runway_summary,
            approaches=approaches,
            has_approaches=has_approaches_flag,
            weather_category=weather_category,
            weather_details=weather_details,
            staffed_positions=staffed_positions,
            is_staffed=is_staffed,
        )

        diversions.append(diversion)

        if len(diversions) >= max_results:
            break

    return diversions


def get_diversion_summary(diversion: DiversionOption) -> str:
    """Get a one-line summary of a diversion option.

    Args:
        diversion: DiversionOption object

    Returns:
        Summary string for display
    """
    parts = [f"{diversion.icao}"]

    # Distance and bearing
    parts.append(f"{diversion.distance_nm:.0f}nm {diversion.bearing_compass}")

    # Runway
    if diversion.longest_runway_ft:
        parts.append(f"{diversion.longest_runway_ft:,}ft")
    else:
        parts.append("No RWY")

    # Approaches
    if diversion.has_approaches:
        parts.append(f"{diversion.approach_count} APP")
    else:
        parts.append("No APP")

    # Weather
    if diversion.weather_category:
        parts.append(diversion.weather_category)

    # Staffing
    if diversion.is_staffed:
        parts.append(f"ATC: {','.join(diversion.staffed_positions)}")

    return " | ".join(parts)
