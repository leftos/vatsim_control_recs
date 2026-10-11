"""
Weather Briefing Daemon

Headless weather briefing generator for scheduled execution.
Generates HTML weather briefings for all preset and custom groupings.
"""

from .config import DaemonConfig
from .generator import WeatherBriefingGenerator

__all__ = ["DaemonConfig", "WeatherBriefingGenerator"]
