"""Core disambiguation logic for generating unique airport names."""

from typing import Dict, List, Optional

from .config import DisambiguatorConfig
from .entity_extractor import EntityExtractor
from .name_processor import NameProcessor


class DisambiguationEngine:
    """Handles the core logic for disambiguating airport names."""
    
    def __init__(self, config: DisambiguatorConfig, name_processor: NameProcessor, entity_extractor: EntityExtractor):
        """Initialize with configuration and helper components."""
        self.config = config
        self.name_processor = name_processor
        self.entity_extractor = entity_extractor
    
    def disambiguate_single_airport(self, icao: str, airport_details: Dict, location: str) -> str:
        """
        Generate a pretty name for a single airport in a location.
        
        Returns the appropriate name format:
        - Military format: "Beale AFB"
        - Location only if name contains location
        - Location - Entity: "San Francisco - John"
        - Location only as fallback
        """
        airport_name = airport_details.get('name', '')
        city = airport_details.get('city', '')
        state = airport_details.get('state', '')
        
        # Check for military airports first - they get special formatting
        military_name = self.name_processor.get_military_name(airport_name, location)
        if military_name:
            return military_name
        
        # If airport name contains the location, just use location
        if self.name_processor.name_contains_location(airport_name, city, state):
            return location
        
        # Name doesn't contain location - try NER first, then fall back
        entity = self.entity_extractor.extract_distinguishing_entity(airport_name, city, state)
        if entity:
            return f"{location} - {entity}"
        
        # Fall back to non-high-priority word method
        non_high_priority_words = self.name_processor.get_non_high_priority_prefix(airport_name, location)
        if non_high_priority_words:
            return f"{location} - {non_high_priority_words}"
        
        return location
    
    def disambiguate_multiple_airports(self, icaos: List[str], airports_data: Dict, location: str) -> Dict[str, str]:
        """
        Generate pretty names for multiple airports in the same location.
        
        Splits airports into two groups:
        1. Those whose names start with the location
        2. Those whose names don't start with the location
        
        Returns a dictionary mapping ICAO codes to pretty names.
        """
        result = {}
        
        # Get shortened names for all airports
        airport_names = {
            icao: self.name_processor.shorten_name(airports_data[icao].get('name', icao))
            for icao in icaos
        }
        
        # Split into two groups
        starts_with_location = []
        doesnt_start_with_location = []
        
        for icao in icaos:
            full_name = airports_data[icao].get('name', icao)
            if full_name.lower().startswith(location.lower()):
                starts_with_location.append(icao)
            else:
                doesnt_start_with_location.append(icao)
        
        # Process airports that DON'T start with location
        for icao in doesnt_start_with_location:
            result[icao] = self._disambiguate_non_location_start(
                icao, airports_data[icao], location, airport_names[icao]
            )
        
        # Process airports that START with location
        if len(starts_with_location) == 1:
            # Only one starts with location - it just gets the location name
            result[starts_with_location[0]] = location
        elif len(starts_with_location) > 1:
            # Multiple start with location - need to disambiguate them
            location_starts = self._disambiguate_location_starts(
                starts_with_location, airport_names, location
            )
            result.update(location_starts)
        
        return result
    
    def _disambiguate_non_location_start(self, icao: str, airport_details: Dict, location: str, shortened_name: str) -> str:
        """Disambiguate an airport whose name doesn't start with the location."""
        full_name = airport_details.get('name', '')
        city = airport_details.get('city', '')
        state = airport_details.get('state', '')
        
        # Check if it's a military airport
        military_name = self.name_processor.get_military_name(full_name, location)
        if military_name:
            return military_name
        
        # Check if name contains location
        if not self.name_processor.name_contains_location(full_name, city, state):
            # Try NER first
            entity = self.entity_extractor.extract_distinguishing_entity(full_name, city, state)
            if entity:
                return f"{location} - {entity}"
            
            # Fall back to non-high-priority words
            non_high_priority = self.name_processor.get_non_high_priority_prefix(full_name, location)
            if non_high_priority:
                return f"{location} - {non_high_priority}"
            
            return location
        
        # Name contains location - use location + high-priority word or first distinguishing word
        distinguishing_parts = self.name_processor.extract_distinguishing_words(shortened_name, location)
        high_priority_word = self.name_processor.find_first_high_priority_word(distinguishing_parts)
        
        if high_priority_word:
            return f"{location} {high_priority_word}"
        elif distinguishing_parts:
            return f"{location} {distinguishing_parts[0]}"
        else:
            return location
    
    def _disambiguate_location_starts(self, icaos: List[str], airport_names: Dict[str, str], location: str) -> Dict[str, str]:
        """
        Disambiguate multiple airports that all start with the location name.
        Uses a progressive approach to find unique suffixes.
        """
        resolved_names = {}

        # Pre-compute location words and distinguishing words for all airports (O(n) instead of O(n²))
        location_words = self.name_processor.extract_location_words(location)

        # Pre-compute distinguishing words and their lowercase set for each airport
        all_distinguishing_parts: Dict[str, List[str]] = {}
        all_distinguishing_sets: Dict[str, set] = {}
        for icao in icaos:
            name = airport_names[icao]
            parts = self.name_processor.extract_distinguishing_words(name, location)
            all_distinguishing_parts[icao] = parts
            all_distinguishing_sets[icao] = set(w.lower() for w in parts)

        for icao in icaos:
            distinguishing_parts = all_distinguishing_parts[icao]

            if not distinguishing_parts:
                resolved_names[icao] = location
                continue

            # Score and sort words by priority
            scored_words = []
            for word in distinguishing_parts:
                is_high_priority = self.name_processor._is_high_priority_word(word)
                priority = 0 if is_high_priority else 1
                scored_words.append((priority, word))

            scored_words.sort(key=lambda x: (x[0], distinguishing_parts.index(x[1])))

            # Try to find unique name
            found = False

            # First, try single high-priority words
            for priority, word in scored_words:
                if priority == 0:  # High priority
                    candidate = f"{location} {word}".strip()
                    if self._is_unique_in_group_optimized(candidate, icao, icaos, location_words, all_distinguishing_sets):
                        resolved_names[icao] = candidate
                        found = True
                        break

            # Try combinations with high-priority words
            if not found:
                found = self._try_combinations_with_priority_optimized(
                    icao, distinguishing_parts, location, icaos, location_words, all_distinguishing_sets, resolved_names
                )

            # Last resort: try sequential combinations
            if not found:
                found = self._try_sequential_combinations_optimized(
                    icao, distinguishing_parts, location, icaos, location_words, all_distinguishing_sets, resolved_names
                )

            # Fallback if nothing works
            if not found:
                resolved_names[icao] = airport_names[icao]

        return resolved_names
    
    def _try_combinations_with_priority(self, icao: str, distinguishing_parts: List[str], 
                                       location: str, all_icaos: List[str], 
                                       airport_names: Dict[str, str], 
                                       resolved_names: Dict[str, str]) -> bool:
        """Try progressively longer combinations that include high-priority words."""
        for num_words in range(2, len(distinguishing_parts) + 1):
            for start_idx in range(len(distinguishing_parts) - num_words + 1):
                candidate_words = distinguishing_parts[start_idx:start_idx + num_words]
                
                # Check if this combination includes at least one high-priority word
                has_high_priority = any(
                    self.name_processor._is_high_priority_word(word)
                    for word in candidate_words
                )
                
                if has_high_priority:
                    candidate = f"{location} {' '.join(candidate_words)}".strip()
                    if self._is_unique_in_group(candidate, icao, all_icaos, location, airport_names):
                        resolved_names[icao] = candidate
                        return True
        
        return False
    
    def _try_sequential_combinations(self, icao: str, distinguishing_parts: List[str], 
                                    location: str, all_icaos: List[str], 
                                    airport_names: Dict[str, str], 
                                    resolved_names: Dict[str, str]) -> bool:
        """Try sequential combinations without requiring high-priority words."""
        for i in range(1, len(distinguishing_parts) + 1):
            candidate = f"{location} {' '.join(distinguishing_parts[:i])}".strip()
            if self._is_unique_in_group(candidate, icao, all_icaos, location, airport_names):
                resolved_names[icao] = candidate
                return True
        
        return False
    
    def _is_unique_in_group(self, candidate_name: str, current_icao: str,
                           all_icaos: List[str], location: str,
                           airport_names: Dict[str, str]) -> bool:
        """Check if a candidate name is unique among all airports in the group."""
        # Extract just the distinguishing part from the candidate
        location_words = self.name_processor.extract_location_words(location)
        candidate_words = [word for word in candidate_name.split() if word.lower() not in location_words]
        candidate_suffix = " ".join(candidate_words).lower()

        for other_icao in all_icaos:
            if current_icao == other_icao:
                continue

            # Get the other airport's distinguishing words
            other_name = airport_names[other_icao]
            other_dist_words = [
                word for word in other_name.split()
                if word.lower() not in location_words
            ]
            other_dist_suffix = " ".join(other_dist_words).lower()

            # Check if the other airport's distinguishing parts start with our candidate
            if other_dist_suffix.startswith(candidate_suffix):
                return False

        return True

    def _is_unique_in_group_optimized(self, candidate_name: str, current_icao: str,
                                      all_icaos: List[str], location_words: set,
                                      all_distinguishing_sets: Dict[str, set]) -> bool:
        """Optimized uniqueness check using pre-computed data."""
        # Extract candidate words (excluding location)
        candidate_words = set(
            word.lower() for word in candidate_name.split()
            if word.lower() not in location_words
        )

        for other_icao in all_icaos:
            if current_icao == other_icao:
                continue

            other_words = all_distinguishing_sets[other_icao]

            # Check if candidate would conflict - if other's words contain all candidate words
            if candidate_words and candidate_words.issubset(other_words):
                return False

        return True

    def _try_combinations_with_priority_optimized(self, icao: str, distinguishing_parts: List[str],
                                                  location: str, all_icaos: List[str],
                                                  location_words: set,
                                                  all_distinguishing_sets: Dict[str, set],
                                                  resolved_names: Dict[str, str]) -> bool:
        """Optimized version using pre-computed data."""
        for num_words in range(2, len(distinguishing_parts) + 1):
            for start_idx in range(len(distinguishing_parts) - num_words + 1):
                candidate_words = distinguishing_parts[start_idx:start_idx + num_words]

                # Check if this combination includes at least one high-priority word
                has_high_priority = any(
                    self.name_processor._is_high_priority_word(word)
                    for word in candidate_words
                )

                if has_high_priority:
                    candidate = f"{location} {' '.join(candidate_words)}".strip()
                    if self._is_unique_in_group_optimized(candidate, icao, all_icaos, location_words, all_distinguishing_sets):
                        resolved_names[icao] = candidate
                        return True

        return False

    def _try_sequential_combinations_optimized(self, icao: str, distinguishing_parts: List[str],
                                               location: str, all_icaos: List[str],
                                               location_words: set,
                                               all_distinguishing_sets: Dict[str, set],
                                               resolved_names: Dict[str, str]) -> bool:
        """Optimized version using pre-computed data."""
        for i in range(1, len(distinguishing_parts) + 1):
            candidate = f"{location} {' '.join(distinguishing_parts[:i])}".strip()
            if self._is_unique_in_group_optimized(candidate, icao, all_icaos, location_words, all_distinguishing_sets):
                resolved_names[icao] = candidate
                return True

        return False