"""
UI Configuration and Constants
Contains flap character sets, data classes, and module-level instances
"""

from dataclasses import dataclass
from typing import Optional, Callable, Literal

# Custom flap character sets for specific column types
ETA_FLAP_CHARS = "9876543210hm:ADELN <-"  # For NEXT ETA columns: numbers in descending order for countdown effect
ICAO_FLAP_CHARS = "-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"  # For ICAO codes
CALLSIGN_FLAP_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789- "  # For flight callsigns
POSITION_FLAP_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ- "  # For controller positions
WIND_FLAP_CHARS = "0123456789GKT "  # For wind data: numbers for direction/speed/gusts
ALTIMETER_FLAP_CHARS = "0123456789AQ "  # For altimeter: A/Q prefix, digits

# Module-level instances for the UI - initialized when data is first loaded
UNIFIED_AIRPORT_DATA = None
DISAMBIGUATOR = None
AIRCRAFT_APPROACH_SPEEDS = None


@dataclass
class ColumnConfig:
    """Configuration for a single table column"""
    name: str
    flap_chars: Optional[str] = None
    content_align: Literal["left", "center", "right"] = "left"
    update_width: bool = False


@dataclass
class TableConfig:
    """Configuration for a complete table"""
    columns: list[ColumnConfig]
    sort_function: Optional[Callable] = None