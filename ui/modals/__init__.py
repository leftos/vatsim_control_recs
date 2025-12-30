"""
Modal Screens Package
Contains all modal dialog screens (Wind, METAR, FlightBoard, Airport Tracking, Flight Info,
Flight Lookup, Go To, VFR Alternatives, Diversions, Historical Stats, Help, Command Palette)
"""

from .wind_info import WindInfoScreen
from .metar_info import MetarInfoScreen
from .airport_tracking import AirportTrackingModal
from .save_grouping import SaveGroupingModal
from .tracked_airports import TrackedAirportsModal
from .flight_board import FlightBoardScreen
from .flight_info import FlightInfoScreen
from .flight_lookup import FlightLookupScreen
from .goto_modal import GoToScreen
from .vfr_alternatives import VfrAlternativesScreen
from .diversion_modal import DiversionModal
from .historical_stats import HistoricalStatsScreen
from .help_modal import HelpScreen
from .command_palette import CommandPaletteScreen

__all__ = [
    'WindInfoScreen',
    'MetarInfoScreen',
    'AirportTrackingModal',
    'SaveGroupingModal',
    'TrackedAirportsModal',
    'FlightBoardScreen',
    'FlightInfoScreen',
    'FlightLookupScreen',
    'GoToScreen',
    'VfrAlternativesScreen',
    'DiversionModal',
    'HistoricalStatsScreen',
    'HelpScreen',
    'CommandPaletteScreen',
]