"""
Airport Disambiguator - Refactored Version

This module provides a cleaner, more maintainable implementation of airport
name disambiguation while maintaining full backwards compatibility with the
original API.

Main Components:
- AirportDisambiguator: Main public interface (backwards compatible)
- DisambiguatorConfig: Configuration and constants
- NameProcessor: Name processing and word extraction
- EntityExtractor: NLP-based entity extraction
- DisambiguationEngine: Core disambiguation logic
- AirportDataManager: Data loading and management

Usage:
    from airport_disambiguator import AirportDisambiguator

    disambiguator = AirportDisambiguator('airports.json')
    pretty_name = disambiguator.get_pretty_name('KSFO')
"""

from .config import DEFAULT_CONFIG, DisambiguatorConfig
from .disambiguator import AirportDisambiguator

# Public API - maintain backwards compatibility
__all__ = [
    "AirportDisambiguator",
    "DisambiguatorConfig",
    "DEFAULT_CONFIG",
]

__version__ = "2.0.0"
