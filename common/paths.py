"""Centralized path management for VATSIM Control Recommendations.

This module defines all paths for writable data, ensuring the UI app
writes to a proper user data directory rather than the script directory.

On Windows: %LOCALAPPDATA%/VATSIMControlRecs/
On macOS:   ~/Library/Application Support/VATSIMControlRecs/
On Linux:   ~/.local/share/VATSIMControlRecs/

Read-only data (e.g., data/airports.json) remains in the project directory.
"""

import os
import sys
from pathlib import Path

# Application name for user data directory
APP_NAME = "VATSIMControlRecs"

# Project root directory (where main.py lives) - for read-only data
_PROJECT_ROOT = Path(__file__).parent.parent.resolve()


def get_project_root() -> Path:
    """Get the project root directory (for read-only data files).

    Returns:
        Path to the project root directory
    """
    return _PROJECT_ROOT


def get_data_dir() -> Path:
    """Get the project's data directory (for read-only data files).

    This is where static data like airports.json, APT_BASE.csv live.

    Returns:
        Path to the project's data directory
    """
    return _PROJECT_ROOT / "data"


def get_user_data_dir() -> Path:
    """Get the user data directory for writable files.

    This is where cache, logs, and user-generated data are stored.

    Returns:
        Path to the user data directory
    """
    if sys.platform == "win32":
        # Windows: %LOCALAPPDATA%/VATSIMControlRecs
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = os.path.expanduser("~\\AppData\\Local")
        path = Path(base) / APP_NAME
    elif sys.platform == "darwin":
        # macOS: ~/Library/Application Support/VATSIMControlRecs
        path = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        # Linux/Unix: ~/.local/share/VATSIMControlRecs
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            path = Path(xdg_data) / APP_NAME
        else:
            path = Path.home() / ".local" / "share" / APP_NAME

    return path


def get_user_cache_dir() -> Path:
    """Get the user cache directory.

    Returns:
        Path to the cache directory within user data
    """
    return get_user_data_dir() / "cache"


def get_user_logs_dir() -> Path:
    """Get the user logs directory.

    Returns:
        Path to the logs directory within user data
    """
    return get_user_data_dir() / "logs"


def get_weather_cache_file() -> Path:
    """Get the path to the weather cache file.

    Returns:
        Path to weather_cache.json
    """
    return get_user_cache_dir() / "weather_cache.json"


def get_runways_cache_path() -> Path:
    """Get the path to the cached runways data.

    Returns:
        Path to runways.csv
    """
    return get_user_cache_dir() / "runways.csv"


def get_runways_metadata_path() -> Path:
    """Get the path to the runways metadata file.

    Returns:
        Path to runways_metadata.txt
    """
    return get_user_cache_dir() / "runways_metadata.txt"


def get_cifp_cache_dir() -> Path:
    """Get the CIFP cache directory.

    Returns:
        Path to the CIFP cache directory
    """
    return get_user_cache_dir() / "cifp"


def get_nasr_cache_dir() -> Path:
    """Get the NASR (navaids) cache directory.

    Returns:
        Path to the NASR cache directory
    """
    return get_user_cache_dir() / "navaids"


def get_custom_groupings_file() -> Path:
    """Get the path to the user's custom groupings file.

    The custom_groupings.json is user data (editable), so it goes in user data dir.

    Returns:
        Path to custom_groupings.json in user data directory
    """
    return get_user_data_dir() / "custom_groupings.json"


def get_project_groupings_file() -> Path:
    """Get the path to the project's custom groupings file (read-only default).

    Returns:
        Path to data/custom_groupings.json in project directory
    """
    return get_data_dir() / "custom_groupings.json"


def load_merged_groupings() -> dict:
    """Load and merge groupings from both project and user directories.

    Project groupings serve as defaults, user groupings override.

    Returns:
        Merged dictionary of groupings
    """
    import json

    merged = {}

    # Load project defaults first
    project_file = get_project_groupings_file()
    if project_file.exists():
        try:
            with open(project_file, 'r', encoding='utf-8') as f:
                merged = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    # Load and merge user groupings (override project)
    user_file = get_custom_groupings_file()
    if user_file.exists():
        try:
            with open(user_file, 'r', encoding='utf-8') as f:
                user_groupings = json.load(f)
                merged.update(user_groupings)
        except (json.JSONDecodeError, OSError):
            pass

    return merged


def ensure_user_directories() -> None:
    """Create all required user data directories if they don't exist.

    Call this at application startup to ensure directories are ready.
    """
    dirs = [
        get_user_data_dir(),
        get_user_cache_dir(),
        get_user_logs_dir(),
        get_cifp_cache_dir(),
        get_nasr_cache_dir(),
    ]

    for directory in dirs:
        directory.mkdir(parents=True, exist_ok=True)
