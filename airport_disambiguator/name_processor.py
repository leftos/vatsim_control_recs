"""Name processing utilities for airport disambiguation."""

import re
from typing import List, Optional, Set

from .config import DisambiguatorConfig


class NameProcessor:
    """Handles airport name processing including shortening and word extraction."""
    
    def __init__(self, config: DisambiguatorConfig):
        """Initialize with configuration."""
        self.config = config
    
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
        Preserves original casing.
        """
        name = self.shorten_name(airport_name)
        location_words = self.extract_location_words(location)
        
        distinguishing_parts = []
        for word in name.split():
            word_lower = word.lower()
            # Skip if it's a location word
            if word_lower in location_words:
                continue
            # Check if it's a compound word containing location words
            if '/' in word or '-' in word:
                parts = word.replace('-', '/').split('/')
                if any(part.lower() in location_words for part in parts):
                    continue
            distinguishing_parts.append(word)
        
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
        if '/' in word or '-' in word:
            parts = word.replace('-', '/').split('/')
            for part in parts:
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
            if '-' in word or '/' in word:
                parts = word.replace('-', '/').split('/')
                if any(part.lower() in self.config.GENERIC_WORDS_FOR_MILITARY for part in parts):
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
        airport_name_lower = airport_name.lower()
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