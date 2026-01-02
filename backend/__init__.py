"""
VATSIM Control Recommendations Backend
Main backend module providing data analysis and API access for VATSIM flight tracking.
"""

# Import main analysis function
from backend.core.analysis import analyze_flights_data

# Import flight details function
from backend.core.flights import get_airport_flight_details

# Import weather functions
from backend.data.weather import (
    get_wind_info,
    get_wind_info_batch,
    get_metar,
    get_metar_batch,
    get_taf,
    get_taf_batch,
    get_altimeter_setting,
    find_nearest_airport_with_metar,
    find_airports_near_position
)

# Import groupings functions
from backend.core.groupings import load_all_groupings

# Import data loaders
from backend.data.loaders import load_unified_airport_data

# Import configuration
from backend.config.constants import WIND_SOURCE

# Import calculation utilities
from backend.core.calculations import (
    haversine_distance_nm,
    calculate_bearing,
    bearing_to_compass,
    calculate_eta
)

# Import diversion-related functions
from backend.core.diversions import (
    find_suitable_diversions,
    DiversionOption,
    DiversionFilters
)
from backend.core.aircraft_performance import (
    get_required_runway_length,
    can_land_at_runway
)
from backend.data.cifp import (
    ensure_cifp_data,
    get_approaches_for_airport,
    get_approach_list_for_airport,
    has_instrument_approaches,
    get_current_airac_cycle,
    cleanup_old_airac_caches as cleanup_old_cifp_caches
)
from backend.data.runways import (
    ensure_runway_data,
    download_runway_data,
    get_longest_runway,
    get_runways,
    get_runway_summary
)

# Import cache functions
from backend.cache.manager import (
    save_weather_cache,
    load_weather_cache
)

__version__ = "1.0.0"

# Export public API
__all__ = [
    'analyze_flights_data',
    'get_airport_flight_details',
    'get_wind_info',
    'get_wind_info_batch',
    'get_metar',
    'get_metar_batch',
    'get_taf',
    'get_taf_batch',
    'get_altimeter_setting',
    'find_nearest_airport_with_metar',
    'find_airports_near_position',
    'load_all_groupings',
    'load_unified_airport_data',
    'WIND_SOURCE',
    'haversine_distance_nm',
    'calculate_bearing',
    'bearing_to_compass',
    'calculate_eta',
    # Diversion-related
    'find_suitable_diversions',
    'DiversionOption',
    'DiversionFilters',
    'get_required_runway_length',
    'can_land_at_runway',
    # CIFP data
    'ensure_cifp_data',
    'get_approaches_for_airport',
    'get_approach_list_for_airport',
    'has_instrument_approaches',
    'get_current_airac_cycle',
    'cleanup_old_cifp_caches',
    # Runway data
    'ensure_runway_data',
    'download_runway_data',
    'get_longest_runway',
    'get_runways',
    'get_runway_summary',
    # Cache functions
    'save_weather_cache',
    'load_weather_cache',
]