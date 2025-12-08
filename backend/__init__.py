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
    bearing_to_compass
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
    'get_altimeter_setting',
    'find_nearest_airport_with_metar',
    'find_airports_near_position',
    'load_all_groupings',
    'load_unified_airport_data',
    'WIND_SOURCE',
    'haversine_distance_nm',
    'calculate_bearing',
    'bearing_to_compass',
]