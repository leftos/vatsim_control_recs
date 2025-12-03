"""
Airport groupings management (custom groupings and ARTCC-based groupings).
"""

import json
from collections import defaultdict
from typing import Dict, List, Any, Optional, Set

from backend.cache.manager import get_artcc_groupings_cache, set_artcc_groupings_cache


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


def load_custom_groupings(filename: str) -> Optional[Dict[str, List[str]]]:
    """
    Load custom airport groupings from JSON file.

    Args:
        filename: Path to the custom groupings JSON file

    Returns:
        Dictionary mapping grouping names to lists of airport ICAOs
        or None if file not found or invalid
    """
    from common import logger

    try:
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Validate structure
        if not isinstance(data, dict):
            logger.error(f"Custom groupings file must contain a JSON object, got {type(data).__name__}")
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

    except FileNotFoundError:
        logger.error(f"Custom groupings file '{filename}' not found.")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Could not decode JSON from '{filename}': {e}")
        return None


def load_all_groupings(
    custom_groupings_filename: str,
    unified_data: Dict[str, Dict[str, Any]]
) -> Dict[str, List[str]]:
    """
    Load and merge custom groupings and ARTCC groupings.
    Custom groupings take precedence over ARTCC groupings if there's a name conflict.
    
    Args:
        custom_groupings_filename: Path to the custom groupings JSON file
        unified_data: Unified airport data dictionary
    
    Returns:
        Merged dictionary of all groupings
    """
    # Load ARTCC groupings first
    artcc_groupings = load_artcc_groupings(unified_data)
    
    # Load custom groupings
    custom_groupings = load_custom_groupings(custom_groupings_filename)
    
    # Merge them (custom groupings override ARTCC groupings if there's a conflict)
    if custom_groupings:
        all_groupings = {**artcc_groupings, **custom_groupings}
    else:
        all_groupings = artcc_groupings
    
    return all_groupings