"""
Weather Daemon Configuration
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class DaemonConfig:
    """Configuration for the weather briefing daemon."""

    # Output directory for generated HTML files
    output_dir: Path = field(default_factory=lambda: Path("/var/www/leftos.dev/weather"))

    # Path to custom groupings JSON file
    custom_groupings_path: Path = field(default_factory=lambda: Path(__file__).parent.parent.parent / "data" / "custom_groupings.json")

    # Path to preset groupings directory
    preset_groupings_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent.parent / "data" / "preset_groupings")

    # Path to data files
    data_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent.parent / "data")

    # ARTCCs to include (None = all)
    artcc_filter: Optional[List[str]] = None

    # Maximum concurrent weather API requests
    max_workers: int = 20

    # Weather cache TTL in seconds (use cached data if fresher than this)
    weather_cache_ttl: int = 600  # 10 minutes

    # Include custom groupings
    include_custom: bool = True

    # Include preset groupings
    include_presets: bool = True

    # ARTCC boundary cache directory
    artcc_cache_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent.parent / "cache" / "artcc_boundaries")

    # Weather cache directory (for --use-cached mode)
    weather_cache_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent.parent / "cache" / "weather")

    # Fetch fresh weather data (False = use cached)
    fetch_fresh_weather: bool = True

    # Generate briefing HTML pages
    generate_briefings: bool = True

    # Generate index page
    generate_index: bool = True

    # Generate weather overlay tiles
    generate_tiles: bool = True

    # Maximum concurrent tile generation workers
    # Keep low (1-2) for memory-constrained servers, can increase locally
    tile_max_workers: int = 2

    # Server timezone for display (e.g., 'America/Los_Angeles')
    # If None, uses UTC
    display_timezone: Optional[str] = None

    def __post_init__(self):
        """Ensure paths are Path objects and create directories."""
        if isinstance(self.output_dir, str):
            self.output_dir = Path(self.output_dir)
        if isinstance(self.custom_groupings_path, str):
            self.custom_groupings_path = Path(self.custom_groupings_path)
        if isinstance(self.preset_groupings_dir, str):
            self.preset_groupings_dir = Path(self.preset_groupings_dir)
        if isinstance(self.data_dir, str):
            self.data_dir = Path(self.data_dir)
        if isinstance(self.artcc_cache_dir, str):
            self.artcc_cache_dir = Path(self.artcc_cache_dir)


# ARTCC display names for index page
ARTCC_NAMES = {
    'ZAB': 'Albuquerque',
    'ZAN': 'Anchorage',
    'ZAU': 'Chicago',
    'ZBW': 'Boston',
    'ZDC': 'Washington',
    'ZDV': 'Denver',
    'ZFW': 'Fort Worth',
    'ZHN': 'Honolulu',
    'ZHU': 'Houston',
    'ZID': 'Indianapolis',
    'ZJX': 'Jacksonville',
    'ZKC': 'Kansas City',
    'ZLA': 'Los Angeles',
    'ZLC': 'Salt Lake City',
    'ZMA': 'Miami',
    'ZME': 'Memphis',
    'ZMP': 'Minneapolis',
    'ZNY': 'New York',
    'ZOA': 'Oakland',
    'ZOB': 'Cleveland',
    'ZSE': 'Seattle',
    'ZSU': 'San Juan',
    'ZTL': 'Atlanta',
    'ZUA': 'Guam',
}

# Category colors (Rich markup colors - bright versions for dark backgrounds)
CATEGORY_COLORS = {
    'LIFR': '#ffaaff',  # Bright magenta
    'IFR': '#ff9999',   # Bright red
    'MVFR': '#77bbff',  # Bright blue
    'VFR': '#66ff66',   # Bright green
    'UNK': 'white',
}
