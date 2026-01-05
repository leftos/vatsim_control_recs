"""
Configuration constants and settings for VATSIM Control Recommendations.
"""

# Define the preferred order for control positions
CONTROL_POSITION_ORDER = [
    "APP",
    "DEP",
    "TWR",
    "GND",
    "DEL",
]  # ATIS is handled specially in display logic

# VATSIM data endpoint
VATSIM_DATA_URL = "https://data.vatsim.net/v3/vatsim-data.json"

# Cache duration settings (in seconds)
WIND_CACHE_DURATION = 60
METAR_CACHE_DURATION = 60

# Persistent cache TTL (in seconds) - how long cached data is valid across sessions
# METAR/TAF data is valid for 15 minutes for persistent cache
PERSISTENT_CACHE_TTL = 900  # 15 minutes

# Global wind source setting (can be "metar" or "minute")
WIND_SOURCE = "metar"  # Default to METAR
