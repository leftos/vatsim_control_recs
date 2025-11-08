"""
UI Module for VATSIM Control Recommendations
Provides Textual-based user interface components
"""

from .app import VATSIMControlApp
from .modals import WindInfoScreen, MetarInfoScreen, FlightBoardScreen
from .tables import TableManager, create_airports_table_config, create_groupings_table_config
from .config import (
    UNIFIED_AIRPORT_DATA,
    DISAMBIGUATOR,
    ColumnConfig,
    TableConfig,
    ETA_FLAP_CHARS,
    ICAO_FLAP_CHARS,
    CALLSIGN_FLAP_CHARS,
    POSITION_FLAP_CHARS,
    WIND_FLAP_CHARS,
)
from .utils import debug_log, eta_sort_key, expand_countries_to_airports

__all__ = [
    # Main app
    'VATSIMControlApp',
    
    # Modal screens
    'WindInfoScreen',
    'MetarInfoScreen',
    'FlightBoardScreen',
    
    # Table management
    'TableManager',
    'create_airports_table_config',
    'create_groupings_table_config',
    
    # Configuration
    'UNIFIED_AIRPORT_DATA',
    'DISAMBIGUATOR',
    'ColumnConfig',
    'TableConfig',
    'ETA_FLAP_CHARS',
    'ICAO_FLAP_CHARS',
    'CALLSIGN_FLAP_CHARS',
    'POSITION_FLAP_CHARS',
    'WIND_FLAP_CHARS',
    
    # Utilities
    'debug_log',
    'eta_sort_key',
    'expand_countries_to_airports',
]

__version__ = '1.0.0'