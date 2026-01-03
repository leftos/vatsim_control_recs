"""
Airport groupings management (custom groupings, preset groupings, and ARTCC-based groupings).
"""

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Any, Optional, Set

from backend.cache.manager import get_artcc_groupings_cache, set_artcc_groupings_cache
from common.paths import load_merged_groupings

# Path to preset groupings directory
PRESET_GROUPINGS_DIR = Path(__file__).parent.parent.parent / "data" / "preset_groupings"


def find_grouping_case_insensitive(
    name: str,
    all_groupings: Dict[str, List[str]]
) -> Optional[str]:
    """
    Find a grouping name case-insensitively.

    Args:
        name: The grouping name to find (case-insensitive)
        all_groupings: Dictionary of all available groupings

    Returns:
        The actual grouping name from the dictionary if found, None otherwise
    """
    # First try exact match
    if name in all_groupings:
        return name

    # Try case-insensitive match
    name_lower = name.lower()
    for key in all_groupings:
        if key.lower() == name_lower:
            return key

    return None


def resolve_grouping_recursively(
    grouping_name: str,
    all_groupings: Dict[str, List[str]],
    visited: Optional[Set[str]] = None
) -> Set[str]:
    """
    Recursively resolve a grouping name to its individual airports.
    Handles nested groupings by looking up grouping names and resolving them.

    This function prevents infinite loops through cycle detection using the
    visited set parameter.

    Args:
        grouping_name: Name of the grouping to resolve
        all_groupings: Dictionary of all available groupings
        visited: Set of already-visited grouping names to prevent infinite loops

    Returns:
        Set of airport ICAO codes
    """
    if visited is None:
        visited = set()

    # Prevent infinite loops
    if grouping_name in visited:
        return set()
    visited.add(grouping_name)

    if grouping_name not in all_groupings:
        return set()

    airports: Set[str] = set()
    items = all_groupings[grouping_name]

    for item in items:
        # Check if this item is itself a grouping name
        if item in all_groupings:
            # Recursively resolve the nested grouping
            airports.update(resolve_grouping_recursively(item, all_groupings, visited))
        else:
            # It's an airport code, add it directly
            airports.add(item)

    return airports


def load_artcc_groupings(unified_data: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    """
    Load ARTCC groupings from unified airport data.
    Creates groupings like "ZOA All", "ZMP All", etc. containing all airports under each ARTCC.
    Uses caching to avoid reloading on every call.
    
    Args:
        unified_data: Unified airport data dictionary
    
    Returns:
        Dictionary mapping ARTCC grouping names to lists of airport ICAOs
        (e.g., {'ZOA All': ['KSFO', 'KOAK', ...], ...})
    """
    cached = get_artcc_groupings_cache()
    if cached is not None:
        return cached
    
    if not unified_data:
        set_artcc_groupings_cache({})
        return {}
    
    # Group airports by ARTCC
    artcc_airports = defaultdict(list)
    
    for airport_code, airport_info in unified_data.items():
        artcc = airport_info.get('artcc', '').strip()
        if artcc:
            artcc_airports[artcc].append(airport_code)
    
    # Create groupings in the format "ARTCC All"
    artcc_groupings = {}
    for artcc, airports in artcc_airports.items():
        grouping_name = f"{artcc} All"
        artcc_groupings[grouping_name] = sorted(airports)  # Sort for consistency
    
    set_artcc_groupings_cache(artcc_groupings)
    #print(f"Created {len(artcc_groupings)} ARTCC groupings")
    return artcc_groupings


def load_custom_groupings(filename: Optional[str] = None) -> Optional[Dict[str, List[str]]]:
    """
    Load custom airport groupings from JSON file(s).

    Uses merged groupings from both project directory (defaults) and
    user data directory (user additions).

    Args:
        filename: Deprecated, ignored. Kept for backwards compatibility.

    Returns:
        Dictionary mapping grouping names to lists of airport ICAOs
        or None if no groupings found
    """
    from common import logger

    # Use the merged groupings from paths module
    data = load_merged_groupings()
    if not data:
        return None

    # Validate structure
    if not isinstance(data, dict):
        logger.error(f"Custom groupings must be a JSON object, got {type(data).__name__}")
        return None

    validated: Dict[str, List[str]] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            logger.warning(f"Skipping non-string grouping key: {key}")
            continue
        if isinstance(value, str):
            # Auto-convert single string to list
            validated[key] = [value]
            logger.warning(f"Grouping '{key}' has string value, converting to list")
        elif isinstance(value, list):
            # Ensure all elements are strings
            validated[key] = [str(v) for v in value]
        else:
            logger.warning(f"Skipping grouping '{key}' with invalid value type: {type(value).__name__}")
            continue

    return validated


def load_preset_groupings() -> Dict[str, List[str]]:
    """
    Load all preset groupings from the preset_groupings directory.
    Each JSON file in the directory represents one ARTCC's groupings.

    Returns:
        Dictionary mapping grouping names to lists of airport codes
    """
    from common import logger

    all_preset_groupings: Dict[str, List[str]] = {}

    if not PRESET_GROUPINGS_DIR.exists():
        return all_preset_groupings

    for json_file in PRESET_GROUPINGS_DIR.glob("*.json"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not isinstance(data, dict):
                logger.warning(f"Preset groupings file '{json_file.name}' must contain a JSON object")
                continue

            for key, value in data.items():
                if not isinstance(key, str):
                    continue
                if isinstance(value, list):
                    # Ensure all elements are strings
                    all_preset_groupings[key] = [str(v) for v in value]

        except json.JSONDecodeError as e:
            logger.warning(f"Could not decode JSON from preset groupings file '{json_file.name}': {e}")
        except Exception as e:
            logger.warning(f"Error loading preset groupings file '{json_file.name}': {e}")

    return all_preset_groupings


def load_all_groupings(
    custom_groupings_filename: Optional[str] = None,
    unified_data: Optional[Dict[str, Dict[str, Any]]] = None
) -> Dict[str, List[str]]:
    """
    Load and merge all groupings sources in order of precedence:
    1. ARTCC groupings (lowest priority - from unified airport data)
    2. Preset groupings (from data/preset_groupings/*.json files)
    3. Custom groupings (highest priority - user-defined)

    Args:
        custom_groupings_filename: Deprecated, ignored. Kept for backwards compatibility.
        unified_data: Unified airport data dictionary (optional, needed for ARTCC groupings)

    Returns:
        Merged dictionary of all groupings
    """
    # Load ARTCC groupings first (lowest priority)
    artcc_groupings = load_artcc_groupings(unified_data) if unified_data else {}

    # Load preset groupings (medium priority)
    preset_groupings = load_preset_groupings()

    # Load custom groupings (highest priority)
    custom_groupings = load_custom_groupings()

    # Merge them in order of precedence (later entries override earlier)
    all_groupings = {**artcc_groupings, **preset_groupings}
    if custom_groupings:
        all_groupings.update(custom_groupings)

    return all_groupings