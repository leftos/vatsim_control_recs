"""
UI Module for VATSIM Control Recommendations
Provides Textual-based user interface components
"""

from .app import VATSIMControlApp
from .config import (
    CALLSIGN_FLAP_CHARS,
    DISAMBIGUATOR,
    ETA_FLAP_CHARS,
    ICAO_FLAP_CHARS,
    POSITION_FLAP_CHARS,
    UNIFIED_AIRPORT_DATA,
    WIND_FLAP_CHARS,
    ColumnConfig,
    TableConfig,
)
from .modals import FlightBoardScreen, MetarInfoScreen, WindInfoScreen
from .tables import (
    TableManager,
    create_airports_table_config,
    create_groupings_table_config,
)
from .utils import debug_log, eta_sort_key, expand_countries_to_airports

__all__ = [
    "CALLSIGN_FLAP_CHARS",
    "DISAMBIGUATOR",
    "ETA_FLAP_CHARS",
    "ICAO_FLAP_CHARS",
    "POSITION_FLAP_CHARS",
    # Configuration
    "UNIFIED_AIRPORT_DATA",
    "WIND_FLAP_CHARS",
    "ColumnConfig",
    "FlightBoardScreen",
    "MetarInfoScreen",
    "TableConfig",
    # Table management
    "TableManager",
    # Main app
    "VATSIMControlApp",
    # Modal screens
    "WindInfoScreen",
    "create_airports_table_config",
    "create_groupings_table_config",
    # Utilities
    "debug_log",
    "eta_sort_key",
    "expand_countries_to_airports",
]

__version__ = "1.0.0"
