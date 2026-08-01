"""
Weather Briefing Daemon

Headless weather briefing generator for scheduled execution.
Generates HTML weather briefings for all preset and custom groupings.
"""

from .config import DaemonConfig
from .generator import WeatherBriefingGenerator, generate_all_briefings

__all__ = ["DaemonConfig", "WeatherBriefingGenerator", "generate_all_briefings"]
