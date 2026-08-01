"""
VATSIM Control Recommendations Backend
Main backend module providing data analysis and API access for VATSIM flight tracking.
"""

# Import main analysis function
# Import cache functions
from backend.cache.manager import load_weather_cache, save_weather_cache

# Import configuration
from backend.config.constants import WIND_SOURCE
from backend.core.aircraft_performance import (
    can_land_at_runway,
    get_required_runway_length,
)
from backend.core.analysis import analyze_flights_data

# Import calculation utilities
from backend.core.calculations import (
    bearing_to_compass,
    calculate_bearing,
    calculate_eta,
    haversine_distance_nm,
)

# Import diversion-related functions
from backend.core.diversions import (
    DiversionFilters,
    DiversionOption,
    find_suitable_diversions,
)

# Import flight details function
from backend.core.flights import get_airport_flight_details

# Import groupings functions
from backend.core.groupings import load_all_groupings

# Import route utilities
from backend.core.route import (
    find_enroute_airports,
    format_ete,
    interpolate_great_circle,
    parse_route_waypoints,
    sample_route_points,
)
from backend.data.cifp import (
    cleanup_old_airac_caches as cleanup_old_cifp_caches,
)
from backend.data.cifp import (
    ensure_cifp_data,
    get_approach_list_for_airport,
    get_approaches_for_airport,
    get_current_airac_cycle,
    has_instrument_approaches,
)

# Import data loaders
from backend.data.loaders import load_unified_airport_data
from backend.data.runways import (
    download_runway_data,
    ensure_runway_data,
    get_longest_runway,
    get_runway_summary,
    get_runways,
)

# Import weather functions
from backend.data.weather import (
    fetch_weather_bbox,
    find_airports_near_position,
    find_nearest_airport_with_metar,
    get_altimeter_setting,
    get_metar,
    get_metar_batch,
    get_rate_limit_status,
    get_taf,
    get_taf_batch,
    get_weather_batch_bbox,
    get_weather_smart,
    get_wind_info,
    get_wind_info_batch,
    reset_rate_limit_state,
)

__version__ = "1.0.0"

# Export public API
__all__ = [
    "WIND_SOURCE",
    "DiversionFilters",
    "DiversionOption",
    "analyze_flights_data",
    "bearing_to_compass",
    "calculate_bearing",
    "calculate_eta",
    "can_land_at_runway",
    "cleanup_old_cifp_caches",
    "download_runway_data",
    # CIFP data
    "ensure_cifp_data",
    # Runway data
    "ensure_runway_data",
    "fetch_weather_bbox",
    "find_airports_near_position",
    "find_enroute_airports",
    "find_nearest_airport_with_metar",
    # Diversion-related
    "find_suitable_diversions",
    "format_ete",
    "get_airport_flight_details",
    "get_altimeter_setting",
    "get_approach_list_for_airport",
    "get_approaches_for_airport",
    "get_current_airac_cycle",
    "get_longest_runway",
    "get_metar",
    "get_metar_batch",
    "get_rate_limit_status",
    "get_required_runway_length",
    "get_runway_summary",
    "get_runways",
    "get_taf",
    "get_taf_batch",
    "get_weather_batch_bbox",
    "get_weather_smart",
    "get_wind_info",
    "get_wind_info_batch",
    "has_instrument_approaches",
    "haversine_distance_nm",
    "interpolate_great_circle",
    "load_all_groupings",
    "load_unified_airport_data",
    "load_weather_cache",
    "parse_route_waypoints",
    "reset_rate_limit_state",
    # Route utilities
    "sample_route_points",
    # Cache functions
    "save_weather_cache",
]
