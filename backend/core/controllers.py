"""
Controller staffing analysis for VATSIM airports.
"""

from collections import defaultdict
from typing import Dict, Any, List, Optional

from backend.config.constants import CONTROL_POSITION_ORDER


def _get_valid_icao_from_callsign(
    icao_candidate: str, airports_data: Dict[str, Dict[str, Any]]
) -> Optional[str]:
    """
    Attempts to resolve an ICAO candidate from a callsign, considering implied 'K' for US airports.

    Resolution order:
    1. Check if icao_candidate is a valid 4-letter ICAO code in airports_data
    2. For 3-letter codes, try prepending 'K' for US airports (e.g., SFO -> KSFO)

    The K-prefix is only applied if:
    - The original 3-letter code is not found in airports_data
    - The K-prefixed code exists in airports_data
    - The K-prefixed airport has country_code == 'US'

    This prevents incorrectly converting non-US 3-letter codes like airport identifiers
    from other regions.

    Args:
        icao_candidate: Potential ICAO code from callsign (e.g., "KJFK" or "JFK")
        airports_data: Dictionary of airport data with 'country_code' field

    Returns:
        Valid ICAO code or None if not found in airports_data
    """
    # Normalize to uppercase for consistent comparison
    icao_candidate = icao_candidate.upper()

    # 1. Check if the icao_candidate itself is a valid ICAO in our data
    if icao_candidate in airports_data:
        return icao_candidate

    # 2. If not found, try prepending 'K' for 3-letter US airport candidates
    # Only do this if:
    #    - The candidate is exactly 3 alphabetic characters
    #    - The K-prefixed version exists in our data
    #    - The K-prefixed airport is in the US
    if len(icao_candidate) == 3 and icao_candidate.isalpha():
        k_prefixed_icao = "K" + icao_candidate
        if k_prefixed_icao in airports_data:
            airport_data = airports_data[k_prefixed_icao]
            # Verify it's actually a US airport before assuming the K prefix
            if airport_data.get("country_code") == "US":
                return k_prefixed_icao

    return None


def get_staffed_positions(
    data: Dict[str, Any],
    airports_data: Dict[str, Dict[str, Any]],
    excluded_frequency: str = "199.998",
) -> Dict[str, List[str]]:
    """
    Extracts staffed positions at each airport from VATSIM data.
    Excludes positions with a specific frequency.

    Args:
        data: VATSIM data dictionary containing 'controllers' and 'atis' lists
        airports_data: Dictionary of airport data
        excluded_frequency: Frequency to exclude (default: "199.998")

    Returns:
        Dictionary mapping airport ICAO codes to lists of staffed position suffixes
        (e.g., {'KJFK': ['APP', 'TWR', 'GND']})
    """
    staffed_positions = defaultdict(set)
    controllers = data.get("controllers", [])
    for controller in controllers:
        callsign = controller.get("callsign", "")
        frequency = controller.get("frequency", "")

        # Exclude specific frequency
        if frequency == excluded_frequency:
            continue

        parts = callsign.split("_")
        # Validate we have non-empty parts (split always returns at least [''])
        if not parts or not parts[0]:
            continue  # Skip empty or invalid callsigns

        icao_candidate_prefix = parts[0]

        # Validate ICAO prefix has reasonable length (ICAO codes are 3-4 chars)
        if len(icao_candidate_prefix) < 2 or len(icao_candidate_prefix) > 5:
            continue  # Not a valid ICAO prefix

        position_suffix = parts[-1] if len(parts) > 1 else ""

        # Only consider non-ATIS positions for the 'controllers' array
        allowed_positions = CONTROL_POSITION_ORDER.copy()

        if position_suffix in allowed_positions:
            valid_icao = _get_valid_icao_from_callsign(
                icao_candidate_prefix, airports_data
            )

            if valid_icao:
                staffed_positions[valid_icao].add(position_suffix)

    # Process ATIS
    atis_list = data.get("atis", [])
    for atis_station in atis_list:
        callsign = atis_station.get("callsign", "")

        parts = callsign.split("_")
        # Validate we have non-empty parts
        if not parts or not parts[0]:
            continue  # Skip empty or invalid callsigns

        icao_candidate_prefix = parts[0]

        # Validate ICAO prefix has reasonable length
        if len(icao_candidate_prefix) < 2 or len(icao_candidate_prefix) > 5:
            continue  # Not a valid ICAO prefix

        # The position suffix for ATIS is generally "ATIS"
        position_suffix = parts[-1] if len(parts) > 1 else ""

        if position_suffix == "ATIS":
            valid_icao = _get_valid_icao_from_callsign(
                icao_candidate_prefix, airports_data
            )

            if valid_icao:
                staffed_positions[valid_icao].add("ATIS")

    # Sort non-ATIS positions based on CONTROL_POSITION_ORDER for consistent display.
    # ATIS is handled separately in the display logic for TOP-DOWN.
    ordered_staffed_positions = {}
    for icao, positions in staffed_positions.items():
        sorted_positions = [pos for pos in CONTROL_POSITION_ORDER if pos in positions]
        if "ATIS" in positions:
            sorted_positions.append("ATIS")  # Always append ATIS at the end if present
        ordered_staffed_positions[icao] = sorted_positions

    return ordered_staffed_positions
