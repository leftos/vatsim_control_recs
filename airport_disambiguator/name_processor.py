"""Name processing utilities for airport disambiguation."""

import re
from typing import List, Optional, Set

from .config import DisambiguatorConfig


class NameProcessor:
    """Handles airport name processing including shortening and word extraction."""

    def __init__(self, config: DisambiguatorConfig):
        """Initialize with configuration."""
        self.config = config

    def _split_compound_word(self, word: str) -> List[str]:
        """Split compound words on / and - delimiters."""
        if '/' in word or '-' in word:
            return [p for p in word.replace('-', '/').split('/') if p]
        return [word]

    def _word_matches_location(self, word: str, location_words: Set[str]) -> bool:
        """Check if word or any compound part matches location words."""
        parts = self._split_compound_word(word)
        return any(part.lower() in location_words for part in parts)

    def shorten_name(self, name: str) -> str:
        """Shorten common airport name parts using the defined replacements."""
        for long, short in self.config.SHORTENING_REPLACEMENTS.items():
            name = name.replace(long, short)
        return name
    
    def extract_location_words(self, location: str) -> Set[str]:
        """Extract and normalize location words for comparison."""
        return {word.lower() for word in location.split()}
    
    def extract_distinguishing_words(self, airport_name: str, location: str) -> List[str]:
        """
        Extract distinguishing words from airport name by removing location words.
        Preserves original casing. For compound words (e.g., "RENO/STEAD"),
        extracts non-location parts (e.g., "STEAD").
        Also handles parenthetical annotations like "(DAUGHERTY FLD)" by stripping
        parentheses and treating contents as regular words.
        """
        name = self.shorten_name(airport_name)
        location_words = self.extract_location_words(location)

        distinguishing_parts = []
        for word in name.split():
            # Strip leading/trailing parentheses from words
            # e.g., "(DAUGHERTY" -> "DAUGHERTY", "FLD)" -> "FLD"
            cleaned_word = word.strip('()')
            if not cleaned_word:
                continue

            word_lower = cleaned_word.lower()
            # Skip if it's exactly a location word
            if word_lower in location_words:
                continue

            # Check if it's a compound word
            if '/' in cleaned_word or '-' in cleaned_word:
                # Extract non-location parts from the compound word
                parts = self._split_compound_word(cleaned_word)
                non_location_parts = [p for p in parts if p.lower() not in location_words]
                if non_location_parts:
                    # Join back with original delimiter and add as distinguishing
                    delimiter = '/' if '/' in cleaned_word else '-'
                    distinguishing_parts.append(delimiter.join(non_location_parts))
            else:
                distinguishing_parts.append(cleaned_word)

        return distinguishing_parts
    
    def find_first_high_priority_word(self, words: List[str]) -> Optional[str]:
        """
        Find the first high-priority word in the list.
        Checks both the word itself and parts of compound words.
        """
        for word in words:
            if self._is_high_priority_word(word):
                return word
        return None
    
    def _is_high_priority_word(self, word: str) -> bool:
        """Check if a word is high-priority, including checking compound parts."""
        # Check if word itself is high-priority
        if word in self.config.HIGH_PRIORITY_WORDS:
            return True
        if word.lower().capitalize() in self.config.HIGH_PRIORITY_WORDS:
            return True

        # Check if word is a compound and any part is high-priority
        for part in self._split_compound_word(word):
            if part in self.config.HIGH_PRIORITY_WORDS:
                return True
            if part.lower().capitalize() in self.config.HIGH_PRIORITY_WORDS:
                return True

        return False
    
    def get_non_high_priority_prefix(self, airport_name: str, location: str) -> Optional[str]:
        """
        Get consecutive non-high-priority words from start of airport name.
        If 4+ words collected, return only the last one. If 3 or fewer, return all.
        Returns None if no such words are found.
        """
        distinguishing_parts = self.extract_distinguishing_words(airport_name, location)
        
        # Collect all non-high-priority words from the start
        non_high_priority_words = []
        for word in distinguishing_parts:
            if self._is_high_priority_word(word):
                break  # Stop at first high-priority word
            non_high_priority_words.append(word)
        
        # If we have 4 or more words, only keep the last one
        if len(non_high_priority_words) >= 4:
            return non_high_priority_words[-1]
        elif non_high_priority_words:
            return ' '.join(non_high_priority_words)
        
        # If no non-high priority words found, return the first word
        return distinguishing_parts[0] if distinguishing_parts else None
    
    def get_military_name(self, airport_name: str, location: str) -> Optional[str]:
        """
        Get the military airport name.
        Returns the base name + military term (e.g., "Beale AFB", "North Island Navy").
        Returns None if no military term is found.
        """
        name = self.shorten_name(airport_name)
        
        # First, check for multi-word phrases
        military_phrase = None
        for phrase in self.config.ALWAYS_INCLUDE_PHRASES:
            if phrase in name:
                military_phrase = phrase
                break
        
        # Then check for single-word military terms
        military_word = None
        if not military_phrase:
            for word in name.split():
                if (word in self.config.ALWAYS_INCLUDE_WORDS or 
                    word.lower().capitalize() in self.config.ALWAYS_INCLUDE_WORDS):
                    military_word = word
                    break
        
        military_term = military_phrase or military_word
        if not military_term:
            return None
        
        # Build the military name: get all non-location, non-generic words + military term
        location_words = self.extract_location_words(location)
        
        name_parts = []
        words = name.split()
        
        for word in words:
            # Clean word for comparison (remove special chars)
            clean_word = word.replace('-', '').replace('/', '').replace('(', '').replace(')', '').lower()
            
            # Skip short location words
            if word.lower() in location_words and len(word) <= 4:
                continue
            
            # Skip generic words
            if clean_word in self.config.GENERIC_WORDS_FOR_MILITARY:
                continue

            # Check if it's a compound word containing generic words
            if any(part.lower() in self.config.GENERIC_WORDS_FOR_MILITARY
                   for part in self._split_compound_word(word)):
                continue

            # Skip the military term itself (we'll add it at the end)
            if word == military_term:
                continue
            if military_phrase and word in military_phrase.split():
                continue
            
            name_parts.append(word)
        
        # Add the military term at the end
        if military_phrase:
            result = ' '.join(name_parts + [military_phrase])
        else:
            result = ' '.join(name_parts + [military_term])
        
        return result if result else None
    
    def name_contains_location(self, airport_name: str, city: str, state: str) -> bool:
        """
        Check if airport name contains city or state as a standalone word.
        Location words within compound words (e.g., 'MEDFORD' in 'INTL/MEDFORD') 
        are not counted if they're the trailing part.
        """
        name_words = airport_name.split()
        
        # Check if any word from the city name appears as a standalone word
        if city:
            city_words = re.split(r'[\s\-/]+', city.lower())
            for city_word in city_words:
                if not city_word:  # Skip empty strings
                    continue
                # Check each word in the airport name
                for name_word in name_words:
                    name_word_lower = name_word.lower()
                    # Check if it's the complete word
                    if name_word_lower == city_word:
                        return True
                    # Check within compound words
                    if '/' in name_word or '-' in name_word:
                        parts = name_word.replace('-', '/').split('/')
                        # Don't count if it's the last part of a compound (trailing context)
                        for i, part in enumerate(parts):
                            if part.lower() == city_word and i < len(parts) - 1:
                                return True
        
        # Check if state name appears as a standalone word
        if state:
            state_lower = state.lower()
            for name_word in name_words:
                name_word_lower = name_word.lower()
                # Check if it's the complete word
                if name_word_lower == state_lower:
                    return True
                # Check within compounds, but not if it's the trailing part
                if '/' in name_word or '-' in name_word:
                    parts = name_word.replace('-', '/').split('/')
                    for i, part in enumerate(parts):
                        if part.lower() == state_lower and i < len(parts) - 1:
                            return True

        return False

    def abbreviate_long_name(self, name: str) -> str:
        """
        Abbreviate a name if it exceeds the configured maximum length.

        For names with " - " separator (e.g., "Mexico City - Licenciado Benito Juarez"),
        abbreviates the part after the dash by converting all but the last word to initials.
        Example: "Mexico City - Licenciado Benito Juarez" → "Mexico City - L. B. Juarez"

        Args:
            name: The pretty name to potentially abbreviate

        Returns:
            The abbreviated name if it was too long, otherwise the original name
        """
        if len(name) <= self.config.MAX_NAME_LENGTH:
            return name

        # Check if name has a " - " separator
        if " - " not in name:
            return name

        parts = name.split(" - ", 1)
        if len(parts) != 2:
            return name

        location_part = parts[0]
        entity_part = parts[1]

        # Split the entity part into words
        entity_words = entity_part.split()
        if len(entity_words) <= 1:
            return name

        # Abbreviate all words except the last one
        abbreviated_words = []
        for i, word in enumerate(entity_words):
            if i < len(entity_words) - 1:
                # Abbreviate to first letter + period
                if word and word[0].isalpha():
                    abbreviated_words.append(f"{word[0].upper()}.")
                else:
                    abbreviated_words.append(word)
            else:
                # Keep the last word intact
                abbreviated_words.append(word)

        abbreviated_entity = " ".join(abbreviated_words)
        abbreviated_name = f"{location_part} - {abbreviated_entity}"

        # Only use abbreviated form if it's actually shorter
        if len(abbreviated_name) < len(name):
            return abbreviated_name

        return name