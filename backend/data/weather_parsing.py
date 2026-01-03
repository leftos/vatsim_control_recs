"""
Shared weather parsing constants and utilities.

Used by both the UI weather briefing modal and the weather daemon generator.
"""

from typing import Dict, Any

# Category priority for trend comparison (lower = worse conditions)
CATEGORY_PRIORITY = {"LIFR": 0, "IFR": 1, "MVFR": 2, "VFR": 3}

# FAR 139 certification priority (lower = larger/more significant)
# "I E" = Index E (wide-body scheduled service) = Class B airports
# "I D" = Index D (large jets) = major Class C airports
# "I C" = Index C (medium jets) = Class C airports
FAR139_PRIORITY = {
    'I E': 0,  # Class B (KSFO, KLAX, KJFK, etc.)
    'I D': 1,  # Major Class C (KOAK, KSJC, KSAN, etc.)
    'I C': 2,  # Class C
    'I B': 3,  # Smaller scheduled service
    'I A': 4,  # Smallest scheduled service
}

# Tower type priority for sorting (lower = larger/more significant airport)
# Used as fallback when FAR 139 data is not available
# Values start at 5 to sort after FAR 139 airports
TOWER_TYPE_PRIORITY = {
    'ATCT-TRACON': 5,
    'ATCT-RAPCON': 5,
    'ATCT-RATCF': 5,
    'ATCT-A/C': 6,
    'ATCT': 7,
    'NON-ATCT': 8,
    '': 9,
}


def get_airport_size_priority(airport_info: Dict[str, Any]) -> int:
    """Get airport size priority for sorting (lower = more significant).

    Uses FAR 139 certification as primary indicator, falls back to tower type.
    FAR 139 Class I airports (major hubs) sort first.

    Args:
        airport_info: Airport data dict with 'far139' and 'tower_type' keys

    Returns:
        Priority value (lower = more significant airport)
    """
    far139 = airport_info.get('far139', '')
    # Check FAR 139 first (most accurate for major airports)
    if far139 in FAR139_PRIORITY:
        return FAR139_PRIORITY[far139]
    # Fall back to tower type
    tower_type = airport_info.get('tower_type', '')
    return TOWER_TYPE_PRIORITY.get(tower_type, 9)
