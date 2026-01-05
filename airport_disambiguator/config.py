"""Configuration and constants for airport disambiguation."""

from dataclasses import dataclass, field
from typing import Dict, FrozenSet


@dataclass(frozen=True)
class DisambiguatorConfig:
    """Configuration settings for airport disambiguation."""

    # Maximum length for pretty names before abbreviation is applied
    MAX_NAME_LENGTH: int = 25

    # Multi-word phrases that should always be included (check these first)
    ALWAYS_INCLUDE_PHRASES: FrozenSet[str] = frozenset({"Coast Guard"})

    # Words that should always be included in the airport name (military terms)
    ALWAYS_INCLUDE_WORDS: FrozenSet[str] = frozenset(
        {"AFB", "Navy", "Naval", "Army", "Marine", "Marines"}
    )

    # Base high-priority descriptor words
    BASE_HIGH_PRIORITY_WORDS: FrozenSet[str] = frozenset(
        {
            "AFB",
            "International",
            "Intercontinental",
            "Regional",
            "Municipal",
            "Executive",
            "Metropolitan",
            "National",
            "Memorial",
            "Central",
            "East",
            "West",
            "North",
            "South",
            "Downtown",
            "City",
            "Base",
            "Airfield",
            "Airpark",
            "Commercial",
            "Domestic",
            "Civil",
            "Military",
            "Boeing",
        }
    )

    # Shortening replacements - both keys and values will be considered high-priority
    SHORTENING_REPLACEMENTS: Dict[str, str] = field(
        default_factory=lambda: {
            "International": "Intl",
            "Intercontinental": "Intercont",
            "Executive": "Exec",
            "Regional": "Rgnl",
            "Municipal": "Muni",
            "Metropolitan": "Metro",
            "National": "Natl",
            "Memorial": "Mem",
            "Airport": "Apt",
            "Field": "Fld",
            "Airpark": "Apk",
            "Station": "Sta",
            "Air Force Base": "AFB",
            "Naval": "Navy",
        }
    )

    # Generic airport descriptors that shouldn't be part of entities
    GENERIC_DESCRIPTORS: FrozenSet[str] = frozenset(
        {
            "International",
            "Regional",
            "Municipal",
            "Executive",
            "Metropolitan",
            "National",
            "Memorial",
            "Central",
        }
    )

    # Generic words to skip in military name formatting
    GENERIC_WORDS_FOR_MILITARY: FrozenSet[str] = frozenset(
        {
            "air",
            "apt",
            "airport",
            "station",
            "sta",
            "fld",
            "field",
            "airfield",
            "apk",
            "airpark",
            "base",
            "force",
            "nas",
            "trapnell",
            "aiport",
            "halsey",
        }
    )

    # Location pattern keywords
    LOCATION_PATTERNS: FrozenSet[str] = frozenset(
        {"County", "City", "Township", "Parish", "Borough"}
    )

    # Airport name suffixes to remove for better NER
    NAME_SUFFIXES: FrozenSet[str] = frozenset(
        {"Airport", "Field", "Airfield", "Airpark", "Station"}
    )

    # Built dynamically in __post_init__
    HIGH_PRIORITY_WORDS: FrozenSet[str] = field(default_factory=frozenset, init=False)

    def __post_init__(self):
        """Build the complete HIGH_PRIORITY_WORDS set."""
        # Build complete HIGH_PRIORITY_WORDS set
        high_priority = set(self.BASE_HIGH_PRIORITY_WORDS)
        for original, shortened in self.SHORTENING_REPLACEMENTS.items():
            high_priority.add(original)
            high_priority.add(shortened)
        object.__setattr__(self, "HIGH_PRIORITY_WORDS", frozenset(high_priority))


# Default configuration instance
DEFAULT_CONFIG = DisambiguatorConfig()
