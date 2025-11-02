"""
VATSIM Control Recommendations Backend
Main backend module providing data analysis and API access for VATSIM flight tracking.
"""

# Import main analysis function
from backend.core.analysis import analyze_flights_data, UNIFIED_AIRPORT_DATA, DISAMBIGUATOR

# Import flight details function
from backend.core.flights import get_airport_flight_details

# Import weather functions
from backend.data.weather import get_wind_info, get_metar

# Import groupings functions  
from backend.core.groupings import load_all_groupings

# Import configuration
from backend.config.constants import WIND_SOURCE

__version__ = "1.0.0"

# Export public API
__all__ = [
    'analyze_flights_data',
    'get_airport_flight_details',
    'get_wind_info',
    'get_metar',
    'load_all_groupings',
    'UNIFIED_AIRPORT_DATA',
    'DISAMBIGUATOR',
    'WIND_SOURCE',
]