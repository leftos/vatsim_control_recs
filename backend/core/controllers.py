"""
Controller staffing analysis for VATSIM airports.
"""

from collections import defaultdict
from typing import Any

from backend.config.constants import CONTROL_POSITION_ORDER


def _get_valid_icao_from_callsign(
    icao_candidate: str, airports_data: dict[str, dict[str, Any]]
) -> str | None:
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


def _split_callsign(callsign: str) -> tuple[str, str] | None:
    """
    Split a VATSIM callsign into its ICAO prefix and position suffix.

    Args:
        callsign: Controller or ATIS callsign such as "KJFK_APP" or "SFO_TWR"

    Returns:
        (prefix, suffix) when the prefix could be an ICAO code, else None. The
        suffix is empty for a callsign with no underscore.
    """
    parts = callsign.split("_")
    prefix = parts[0]
    if not 2 <= len(prefix) <= 5:
        return None
    suffix = parts[-1] if len(parts) > 1 else ""
    return prefix, suffix


def _collect_controller_positions(
    data: dict[str, Any],
    airports_data: dict[str, dict[str, Any]],
    excluded_frequency: str,
) -> dict[str, set[str]]:
    """
    Collect the non-ATIS positions each airport has a controller online for.

    Args:
        data: VATSIM data dictionary containing a 'controllers' list
        airports_data: Dictionary of airport data
        excluded_frequency: Frequency whose controllers are ignored

    Returns:
        Dictionary mapping airport ICAO codes to the sets of positions found
    """
    positions: defaultdict[str, set[str]] = defaultdict(set)
    for controller in data.get("controllers", []):
        if controller.get("frequency", "") == excluded_frequency:
            continue
        parsed = _split_callsign(controller.get("callsign", ""))
        if parsed is None:
            continue
        prefix, suffix = parsed
        if suffix not in CONTROL_POSITION_ORDER:
            continue
        valid_icao = _get_valid_icao_from_callsign(prefix, airports_data)
        if valid_icao:
            positions[valid_icao].add(suffix)
    return positions


def _collect_atis_positions(
    data: dict[str, Any], airports_data: dict[str, dict[str, Any]]
) -> dict[str, set[str]]:
    """
    Collect the airports that have an ATIS station online.

    Args:
        data: VATSIM data dictionary containing an 'atis' list
        airports_data: Dictionary of airport data

    Returns:
        Dictionary mapping airport ICAO codes to a set holding "ATIS"
    """
    positions: defaultdict[str, set[str]] = defaultdict(set)
    for atis_station in data.get("atis", []):
        parsed = _split_callsign(atis_station.get("callsign", ""))
        if parsed is None:
            continue
        prefix, suffix = parsed
        if suffix != "ATIS":
            continue
        valid_icao = _get_valid_icao_from_callsign(prefix, airports_data)
        if valid_icao:
            positions[valid_icao].add("ATIS")
    return positions


def _order_positions(positions: dict[str, set[str]]) -> dict[str, list[str]]:
    """
    Order each airport's positions for display.

    Non-ATIS positions follow CONTROL_POSITION_ORDER; ATIS is handled separately
    in the display logic for TOP-DOWN and is always appended last when present.

    Args:
        positions: Dictionary mapping airport ICAO codes to the sets of positions found

    Returns:
        Dictionary mapping airport ICAO codes to ordered position suffixes
    """
    ordered: dict[str, list[str]] = {}
    for icao, found in positions.items():
        sorted_positions = [pos for pos in CONTROL_POSITION_ORDER if pos in found]
        if "ATIS" in found:
            sorted_positions.append("ATIS")
        ordered[icao] = sorted_positions
    return ordered


def get_staffed_positions(
    data: dict[str, Any],
    airports_data: dict[str, dict[str, Any]],
    excluded_frequency: str = "199.998",
) -> dict[str, list[str]]:
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
    positions = _collect_controller_positions(data, airports_data, excluded_frequency)
    for icao, atis in _collect_atis_positions(data, airports_data).items():
        positions.setdefault(icao, set()).update(atis)
    return _order_positions(positions)
