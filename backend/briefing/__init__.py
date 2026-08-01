"""
Weather briefing utilities.

This package provides shared logic for generating weather briefings,
used by both the Textual UI and the headless HTML generator.
"""

# Re-export parse_wind_from_metar from weather_parsing for convenience
from backend.data.weather_parsing import parse_wind_from_metar

from .area_clustering import (
    AreaClusterer,
    build_area_summary,
    count_area_categories,
)
from .taf_parsing import (
    calculate_trend,
    format_taf_relative_time,
    parse_taf_changes,
    parse_taf_forecast_details,
)

__all__ = [
    # Area clustering
    "AreaClusterer",
    "build_area_summary",
    "calculate_trend",
    "count_area_categories",
    "format_taf_relative_time",
    "parse_taf_changes",
    "parse_taf_forecast_details",
    # TAF parsing
    "parse_wind_from_metar",
]
