"""
Airport groupings management (custom groupings, preset groupings, and ARTCC-based groupings).
"""

import json
import os
import tempfile
from collections import defaultdict
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.cache.manager import get_artcc_groupings_cache, set_artcc_groupings_cache
from common.paths import get_user_favorites_file, load_merged_groupings

# Path to preset groupings directory
PRESET_GROUPINGS_DIR = Path(__file__).parent.parent.parent / "data" / "preset_groupings"

# The only per-airport filters a grouping stores.
_DEP_ARR_FILTERS = ("dep", "arr")


def _valid_filters(raw: Any) -> dict[str, str]:
    """Keep only the str-keyed "dep" and "arr" entries of a raw filters mapping.

    Args:
        raw: The value read from a favorites entry's "filters" key.

    Returns:
        The entries whose key is a string and whose value is "dep" or "arr".
    """
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key, value in raw.items()
        if isinstance(key, str) and value in _DEP_ARR_FILTERS
    }


def load_user_favorites() -> dict[str, list[str]]:
    """Load user-saved favorites from the favorites file.

    Supports both old format (value is a list) and enriched format
    (value is a dict with "airports" and optional "filters" keys).

    Returns:
        Dictionary mapping favorite names to lists of references
        (airport ICAOs and/or grouping names)
    """
    favorites_file = get_user_favorites_file()
    if not favorites_file.exists():
        return {}

    try:
        with open(favorites_file, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            result: dict[str, list[str]] = {}
            for k, v in data.items():
                if isinstance(v, list):
                    result[k] = v
                elif isinstance(v, dict) and "airports" in v:
                    airports = v["airports"]
                    if isinstance(airports, list):
                        result[k] = airports
            return result
    except (json.JSONDecodeError, OSError):
        pass

    return {}


def load_favorite_filters(name: str) -> dict[str, str]:
    """Load per-airport dep/arr filters for a named favorite.

    Args:
        name: The favorite name to look up

    Returns:
        Dictionary mapping airport ICAOs to "dep" or "arr".
        Empty dict if no filters are saved or the favorite doesn't exist.
    """
    favorites_file = get_user_favorites_file()
    if not favorites_file.exists():
        return {}

    try:
        with open(favorites_file, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            entry = data.get(name)
            if isinstance(entry, dict) and "filters" in entry:
                return _valid_filters(entry["filters"])
    except (json.JSONDecodeError, OSError):
        pass

    return {}


def get_user_favorite_names() -> set[str]:
    """Return the set of user-saved favorite grouping names.

    Returns:
        Set of favorite names from the user's favorites.json
    """
    return set(load_user_favorites().keys())


def find_grouping_case_insensitive(
    name: str, all_groupings: dict[str, list[str]]
) -> str | None:
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
    all_groupings: dict[str, list[str]],
    visited: set[str] | None = None,
) -> set[str]:
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

    airports: set[str] = set()
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


def load_artcc_groupings(
    unified_data: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
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
        artcc = airport_info.get("artcc", "").strip()
        if artcc:
            artcc_airports[artcc].append(airport_code)

    # Create groupings in the format "ARTCC All"
    artcc_groupings = {}
    for artcc, airports in artcc_airports.items():
        grouping_name = f"{artcc} All"
        artcc_groupings[grouping_name] = sorted(airports)  # Sort for consistency

    set_artcc_groupings_cache(artcc_groupings)
    # print(f"Created {len(artcc_groupings)} ARTCC groupings")
    return artcc_groupings


def load_custom_groupings(
    filename: str | None = None,
) -> dict[str, list[str]] | None:
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
        logger.error(
            f"Custom groupings must be a JSON object, got {type(data).__name__}"
        )
        return None

    validated: dict[str, list[str]] = {}
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
            logger.warning(
                f"Skipping grouping '{key}' with invalid value type: {type(value).__name__}"
            )
            continue

    return validated


def load_preset_groupings() -> dict[str, list[str]]:
    """
    Load all preset groupings from the preset_groupings directory.
    Each JSON file in the directory represents one ARTCC's groupings.

    Supports both old format (value is list of airports) and new format
    (value is dict with 'airports', 'position_prefixes', etc.).

    Note: Single-airport groupings are filtered out.

    Returns:
        Dictionary mapping grouping names to lists of airport codes
    """
    from common import logger

    all_preset_groupings: dict[str, list[str]] = {}

    if not PRESET_GROUPINGS_DIR.exists():
        return all_preset_groupings

    for json_file in PRESET_GROUPINGS_DIR.glob("*.json"):
        try:
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                logger.warning(
                    f"Preset groupings file '{json_file.name}' must contain a JSON object"
                )
                continue

            for key, value in data.items():
                if not isinstance(key, str):
                    continue
                airports = None
                if isinstance(value, list):
                    # Old format: value is list of airports
                    airports = [str(v) for v in value]
                elif (
                    isinstance(value, dict)
                    and "airports" in value
                    and isinstance(value["airports"], list)
                ):
                    # New format: value is dict with 'airports' key
                    airports = [str(v) for v in value["airports"]]

                # Filter out single-airport groupings
                if airports and len(airports) > 1:
                    all_preset_groupings[key] = airports

        except json.JSONDecodeError as e:
            logger.warning(
                f"Could not decode JSON from preset groupings file '{json_file.name}': {e}"
            )
        except Exception as e:
            logger.warning(
                f"Error loading preset groupings file '{json_file.name}': {e}"
            )

    return all_preset_groupings


def load_all_groupings(
    custom_groupings_filename: str | None = None,
    unified_data: dict[str, dict[str, Any]] | None = None,
) -> dict[str, list[str]]:
    """
    Load and merge all groupings sources in order of precedence:
    1. ARTCC groupings (lowest priority - from unified airport data)
    2. Preset groupings (from data/preset_groupings/*.json files)
    3. Custom groupings (community-contributed)
    4. User favorites (highest priority - per-user saved combinations)

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

    # Load custom groupings (community-contributed)
    custom_groupings = load_custom_groupings()

    # Load user favorites (highest priority)
    user_favorites = load_user_favorites()

    # Merge them in order of precedence (later entries override earlier)
    all_groupings = {**artcc_groupings, **preset_groupings}
    if custom_groupings:
        all_groupings.update(custom_groupings)
    if user_favorites:
        all_groupings.update(user_favorites)

    return all_groupings


# --- Strict I/O for the user's own groupings ---------------------------------
#
# The loaders above are lenient on purpose: the cache and analysis paths must
# keep working when the file is broken. The functions below are the I/O a
# grouping name is saved, deleted or renamed through. They raise GroupingError
# with a user-facing message (the UI shows str(e) in a toast) and never treat a
# corrupt file as empty, or the next save would erase every grouping in it.


class GroupingError(Exception):
    """A user grouping could not be read or written.

    The message is user-facing: the UI shows str(e) in a toast.
    """


class GroupingNotFoundError(GroupingError):
    """The named user grouping is not in the favorites file."""


class GroupingExistsError(GroupingError):
    """A user grouping with that name is already in the favorites file."""


class GroupingNameError(GroupingError):
    """The name is blank, a preset's, or an airport ident."""


@dataclass(frozen=True)
class UserGrouping:
    """A named user grouping: its members and their per-airport dep/arr filters.

    Attributes:
        name: The stored grouping name.
        items: Airport idents and/or grouping names, in the user's order.
        filters: Bare ICAO to "dep" or "arr"; "both" is never stored.
    """

    name: str
    items: tuple[str, ...]
    filters: dict[str, str]


def _read_strict() -> dict[str, Any]:
    """Read the whole favorites file, raising on a file that cannot be trusted.

    Returns:
        The file's top-level object, or an empty dict when the file is absent.

    Raises:
        GroupingError: The file cannot be read, is not valid JSON, or is not a
            JSON object.
    """
    favorites_file = get_user_favorites_file()
    if not favorites_file.exists():
        return {}

    try:
        with open(favorites_file, encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError) as e:
        raise GroupingError(f"couldn't read '{favorites_file}': {e}") from e

    if not isinstance(data, dict):
        raise GroupingError(
            f"couldn't read '{favorites_file}': the top level is not a JSON object"
        )

    return data


def _members(name: str, items: list[Any]) -> tuple[str, ...]:
    """Return a favorites entry's members, refusing any that is not a string.

    Args:
        name: The entry's name, used in the error message.
        items: The raw member list.

    Returns:
        The members in file order.

    Raises:
        GroupingError: A member is not a string.
    """
    if not all(isinstance(item, str) for item in items):
        raise GroupingError(
            f"couldn't read '{get_user_favorites_file()}': the entry '{name}' has a member that is not a string"
        )
    return tuple(items)


def _split_entry(name: str, entry: Any) -> tuple[tuple[str, ...], dict[str, str]]:
    """Split one favorites entry into its members and its dep/arr filters.

    Args:
        name: The entry's name, used in the error message.
        entry: The raw entry, in either the bare-list or the dict shape.

    Returns:
        The members in file order and the filters with a valid key and value.

    Raises:
        GroupingError: The entry is in neither accepted shape, or a member is
            not a string.
    """
    if isinstance(entry, list):
        return _members(name, entry), {}

    if isinstance(entry, dict) and isinstance(entry.get("airports"), list):
        return _members(name, entry["airports"]), _valid_filters(entry.get("filters"))

    favorites_file = get_user_favorites_file()
    raise GroupingError(
        f"couldn't read '{favorites_file}': the entry '{name}' is not a list of airports"
    )


def _entry_for(grouping: UserGrouping) -> list[str] | dict[str, Any]:
    """Build the file entry for a grouping: a bare list, or a dict when filters remain.

    Members keep the given order with duplicates dropped; filter values other
    than "dep" and "arr" are dropped.
    """
    items = list(dict.fromkeys(grouping.items))
    filters = _valid_filters(grouping.filters)
    if filters:
        return {"airports": items, "filters": filters}
    return items


def _check_name(name: str, preset_names: set[str], idents: set[str]) -> None:
    """Refuse a blank name, a preset's name, or an airport ident.

    Raises:
        GroupingNameError: The name is blank, equal case-insensitively to a
            preset grouping name, or equal to an airport ident (the idents are
            upper-cased by `airport_idents`).
    """
    if not name:
        raise GroupingNameError("a name is required")
    if any(preset.lower() == name.lower() for preset in preset_names):
        raise GroupingNameError("that name belongs to a preset")
    if name.upper() in idents:
        raise GroupingNameError("that name is an airport ident")


def _replace_key(data: dict[str, Any], old: str, new: str, value: Any) -> None:
    """Store `value` under `new` at `old`'s position, dropping `old`.

    Args:
        data: The whole favorites object, updated in place.
        old: The stored key to replace; it is expected to be there.
        new: The key to store in its place.
        value: The entry to store under `new`.
    """
    rebuilt = {}
    for key, entry in data.items():
        if key == old:
            rebuilt[new] = value
        else:
            rebuilt[key] = entry
    data.clear()
    data.update(rebuilt)


def _write_atomic(data: dict[str, Any]) -> None:
    """Write the whole favorites file atomically.

    The app is single-user and writes on the UI thread, so no lock is needed.
    The temp file is created in the favorites directory, so os.replace is atomic
    on the same filesystem, and it is removed again when anything fails.

    Raises:
        GroupingError: The file could not be written or moved into place.
    """
    favorites_file = get_user_favorites_file()

    temp_path: Path | None = None
    try:
        favorites_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=favorites_file.parent,
            prefix=f"{favorites_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(data, temp_file, indent=2, ensure_ascii=False)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, favorites_file)
    except Exception as error:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                from common import logger

                logger.warning(
                    f"Could not remove the temporary favorites file {temp_path}: {cleanup_error}"
                )
        raise GroupingError(f"couldn't write '{favorites_file}': {error}") from error


def get_user_grouping(name: str) -> UserGrouping | None:
    """Read one user grouping strictly.

    Args:
        name: The stored grouping name (an exact, case-sensitive key).

    Returns:
        The grouping, or None when the name is not in the file.

    Raises:
        GroupingError: The file cannot be read or the entry is malformed.
    """
    data = _read_strict()
    if name not in data:
        return None

    items, filters = _split_entry(name, data[name])
    return UserGrouping(name=name, items=items, filters=filters)


def save_user_grouping(
    grouping: UserGrouping,
    *,
    overwrite: bool,
    preset_names: AbstractSet[str],
    airport_idents: AbstractSet[str],
) -> None:
    """Write one user grouping to the favorites file.

    The stored name is matched case-insensitively, and the name rules apply only
    when no stored name matches it, so a legacy favorite that shadows a preset
    can be saved again under its own name. An overwrite keeps the entry's
    position and stores it under the given spelling. Every other top-level entry
    in the file is preserved.

    Args:
        grouping: The name, members and filters to write.
        overwrite: Replace an existing entry of the same name instead of raising.
        preset_names: Grouping names owned by a preset source.
        airport_idents: Airport idents a grouping name may not collide with.

    Raises:
        GroupingExistsError: The name exists and overwrite is false.
        GroupingNameError: The name is blank, a preset's, or an airport ident.
        GroupingError: The file cannot be read or written.
    """
    name = grouping.name.strip()
    data = _read_strict()

    stored = find_grouping_case_insensitive(name, data)
    if stored is None:
        _check_name(name, preset_names, airport_idents)
    elif not overwrite:
        raise GroupingExistsError(f"'{name}' is already one of my groupings")

    if stored is None:
        data[name] = _entry_for(grouping)
    else:
        _replace_key(data, stored, name, _entry_for(grouping))
    _write_atomic(data)


def delete_user_grouping(name: str) -> None:
    """Remove one user grouping from the favorites file.

    Args:
        name: The stored grouping name (an exact, case-sensitive key).

    Raises:
        GroupingNotFoundError: The name is not in the file, or the file is absent.
        GroupingError: The file cannot be read or written.
    """
    data = _read_strict()
    if name not in data:
        raise GroupingNotFoundError(f"'{name}' is not one of my groupings")

    del data[name]
    _write_atomic(data)


def rename_user_grouping(
    old: str,
    new_grouping: UserGrouping,
    *,
    overwrite: bool,
    preset_names: AbstractSet[str],
    airport_idents: AbstractSet[str],
) -> None:
    """Rename one user grouping and save its new contents in a single write.

    Both names are matched case-insensitively, so a case-only rename is a rename
    of the same entry, and the renamed entry keeps its position in the file. A
    rename onto another entry's name needs overwrite, as a save does, and the
    name rules apply to a name no stored name matches.

    Args:
        old: The stored name to remove.
        new_grouping: The new name and contents.
        overwrite: Replace an existing entry of the new name instead of raising.
        preset_names: Grouping names owned by a preset source.
        airport_idents: Airport idents a grouping name may not collide with.

    Raises:
        GroupingNotFoundError: old is not in the file.
        GroupingExistsError: The new name matches another entry and overwrite is false.
        GroupingNameError: The new name is blank, a preset's, or an airport ident.
        GroupingError: The file cannot be read or written.
    """
    new_name = new_grouping.name.strip()
    data = _read_strict()

    old_stored = find_grouping_case_insensitive(old, data)
    if old_stored is None:
        raise GroupingNotFoundError(f"'{old}' is not one of my groupings")

    new_stored = find_grouping_case_insensitive(new_name, data)
    if new_stored != old_stored:
        if new_stored is not None:
            if not overwrite:
                raise GroupingExistsError(
                    f"'{new_name}' is already one of my groupings"
                )
            del data[new_stored]
        else:
            _check_name(new_name, preset_names, airport_idents)

    _replace_key(data, old_stored, new_name, _entry_for(new_grouping))
    _write_atomic(data)


def preset_grouping_names(unified_data: dict[str, dict[str, Any]]) -> set[str]:
    """Return the grouping names owned by the non-favorite grouping sources.

    These are the names a user grouping may not take: the ARTCC groupings, the
    files in data/preset_groupings, and the community custom groupings. A
    favorite that shadows one of them does not remove it from this set.

    Args:
        unified_data: Unified airport data (needed for the ARTCC groupings).

    Returns:
        Every preset grouping name.
    """
    names: set[str] = set(load_artcc_groupings(unified_data) if unified_data else {})
    names.update(load_preset_groupings())
    names.update(load_custom_groupings() or {})
    return names


def airport_idents(unified_data: dict[str, dict[str, Any]]) -> set[str]:
    """Return every airport ident the unified data knows.

    Args:
        unified_data: Unified airport data keyed by ICAO (or FAA) code.

    Returns:
        Upper-cased keys plus each row's non-empty icao, faa and iata fields.
    """
    idents: set[str] = set()
    for key, info in unified_data.items():
        idents.add(str(key).strip().upper())
        if not isinstance(info, dict):
            continue
        for field in ("icao", "faa", "iata"):
            value = info.get(field)
            if isinstance(value, str) and value.strip():
                idents.add(value.strip().upper())
    return idents
