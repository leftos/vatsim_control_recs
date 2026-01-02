"""
Weather Briefing Daemon

Headless weather briefing generator for scheduled execution.
Generates HTML weather briefings for all preset and custom groupings.
"""

from .generator import WeatherBriefingGenerator, generate_all_briefings
from .config import DaemonConfig

__all__ = ['WeatherBriefingGenerator', 'generate_all_briefings', 'DaemonConfig']
