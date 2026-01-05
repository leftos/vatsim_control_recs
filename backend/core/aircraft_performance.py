"""Aircraft performance data for diversion calculations.

This module provides runway length requirements based on ADG (Airplane Design Group)
classifications. ADG is an FAA classification system that groups aircraft by wingspan
and tail height, which correlates with landing distance requirements.

Reference: FAA Advisory Circular 150/5300-13A
"""

import csv
import os
import threading
from functools import lru_cache
from typing import Dict, Optional

from common import logger as debug_logger


# ADG (Airplane Design Group) to minimum runway length mapping (in feet)
# These are conservative estimates based on typical aircraft in each group
ADG_RUNWAY_REQUIREMENTS: Dict[str, int] = {
    "I": 3000,  # Small single-engine (C172, PA28)
    "II": 4500,  # Small twin, light business jets (C500, BE20)
    "III": 6000,  # Regional jets, narrow-body (CRJ, B737, A320)
    "IV": 8000,  # Wide-body (B767, A300)
    "V": 9500,  # Large wide-body (B777, A350)
    "VI": 11000,  # Largest aircraft (B747, A380)
}

# Aircraft class fallback mapping (if ADG not available)
CLASS_RUNWAY_REQUIREMENTS: Dict[str, int] = {
    "Light": 3000,
    "Medium": 5500,
    "Large": 6500,
    "Heavy": 9000,
    "Super": 11000,
}

# Thread-safe cache for aircraft ADG data
_ADG_DATA_LOCK = threading.Lock()
_AIRCRAFT_ADG_DATA: Optional[Dict[str, str]] = None
_AIRCRAFT_CLASS_DATA: Optional[Dict[str, str]] = None

# Default runway requirement for unknown aircraft
DEFAULT_RUNWAY_REQUIREMENT = 6000


def _load_aircraft_data(filename: str) -> tuple[Dict[str, str], Dict[str, str]]:
    """Load aircraft ADG and class data from CSV file.

    Args:
        filename: Path to aircraft_data.csv

    Returns:
        Tuple of (adg_dict, class_dict) mapping aircraft codes to ADG/class values
    """
    global _AIRCRAFT_ADG_DATA, _AIRCRAFT_CLASS_DATA

    with _ADG_DATA_LOCK:
        if _AIRCRAFT_ADG_DATA is not None and _AIRCRAFT_CLASS_DATA is not None:
            return _AIRCRAFT_ADG_DATA, _AIRCRAFT_CLASS_DATA

        adg_data: Dict[str, str] = {}
        class_data: Dict[str, str] = {}

        try:
            with open(filename, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    icao_code = row.get("ICAO_Code", "").strip()
                    adg = row.get("ADG", "").strip()
                    aircraft_class = row.get("Class", "").strip()

                    if icao_code:
                        if adg:
                            adg_data[icao_code] = adg
                        if aircraft_class:
                            class_data[icao_code] = aircraft_class

            _AIRCRAFT_ADG_DATA = adg_data
            _AIRCRAFT_CLASS_DATA = class_data
            debug_logger.info(f"Loaded ADG data for {len(adg_data)} aircraft types")
            return adg_data, class_data

        except FileNotFoundError:
            debug_logger.warning(f"Aircraft data file '{filename}' not found")
            _AIRCRAFT_ADG_DATA = {}
            _AIRCRAFT_CLASS_DATA = {}
            return {}, {}
        except Exception as e:
            debug_logger.error(f"Error loading aircraft data: {e}")
            _AIRCRAFT_ADG_DATA = {}
            _AIRCRAFT_CLASS_DATA = {}
            return {}, {}


def get_aircraft_data_path() -> str:
    """Get the path to the aircraft data CSV file."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, "..", "..", "data", "aircraft_data.csv")


@lru_cache(maxsize=500)
def get_required_runway_length(aircraft_type: str) -> int:
    """Get the minimum runway length required for an aircraft type.

    Uses ADG (Airplane Design Group) as the primary classification,
    with aircraft class as a fallback.

    Args:
        aircraft_type: Aircraft ICAO type code (e.g., "B738", "A320")

    Returns:
        Minimum runway length in feet
    """
    if not aircraft_type:
        return DEFAULT_RUNWAY_REQUIREMENT

    aircraft_type = aircraft_type.upper().strip()

    # Load data
    filename = get_aircraft_data_path()
    adg_data, class_data = _load_aircraft_data(filename)

    # Try ADG first
    adg = adg_data.get(aircraft_type)
    if adg and adg in ADG_RUNWAY_REQUIREMENTS:
        return ADG_RUNWAY_REQUIREMENTS[adg]

    # Fall back to class
    aircraft_class = class_data.get(aircraft_type)
    if aircraft_class and aircraft_class in CLASS_RUNWAY_REQUIREMENTS:
        return CLASS_RUNWAY_REQUIREMENTS[aircraft_class]

    # Default for unknown aircraft
    return DEFAULT_RUNWAY_REQUIREMENT


def get_adg_for_aircraft(aircraft_type: str) -> Optional[str]:
    """Get the ADG classification for an aircraft type.

    Args:
        aircraft_type: Aircraft ICAO type code

    Returns:
        ADG classification (I-VI) or None if unknown
    """
    if not aircraft_type:
        return None

    aircraft_type = aircraft_type.upper().strip()
    filename = get_aircraft_data_path()
    adg_data, _ = _load_aircraft_data(filename)
    return adg_data.get(aircraft_type)


def can_land_at_runway(aircraft_type: str, runway_length_ft: int) -> bool:
    """Check if an aircraft can land on a runway of given length.

    Args:
        aircraft_type: Aircraft ICAO type code
        runway_length_ft: Runway length in feet

    Returns:
        True if the aircraft can land on the runway
    """
    required = get_required_runway_length(aircraft_type)
    return runway_length_ft >= required


def clear_aircraft_performance_cache() -> None:
    """Clear cached aircraft performance data."""
    global _AIRCRAFT_ADG_DATA, _AIRCRAFT_CLASS_DATA

    with _ADG_DATA_LOCK:
        _AIRCRAFT_ADG_DATA = None
        _AIRCRAFT_CLASS_DATA = None

    get_required_runway_length.cache_clear()
