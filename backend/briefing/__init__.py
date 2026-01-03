"""
Weather briefing utilities.

This package provides shared logic for generating weather briefings,
used by both the Textual UI and the headless HTML generator.
"""

from .area_clustering import (
    AreaClusterer,
    count_area_categories,
    build_area_summary,
)

from .taf_parsing import (
    parse_taf_forecast_details,
    calculate_trend,
    parse_taf_changes,
    format_taf_relative_time,
)

# Re-export parse_wind_from_metar from weather_parsing for convenience
from backend.data.weather_parsing import parse_wind_from_metar

__all__ = [
    # Area clustering
    "AreaClusterer",
    "count_area_categories",
    "build_area_summary",
    # TAF parsing
    "parse_wind_from_metar",
    "parse_taf_forecast_details",
    "calculate_trend",
    "parse_taf_changes",
    "format_taf_relative_time",
]
