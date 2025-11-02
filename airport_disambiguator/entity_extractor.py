"""Entity extraction using spaCy NER for airport disambiguation."""

import re
import subprocess
import sys
from typing import List, Optional, Tuple

import spacy

from .config import DisambiguatorConfig


class EntityExtractor:
    """Handles NLP-based entity extraction from airport names."""
    
    def __init__(self, config: DisambiguatorConfig):
        """Initialize with configuration."""
        self.config = config
        self._nlp = None
        self._entity_cache = {}
    
    @property
    def nlp(self):
        """Lazy load spaCy model, downloading it if necessary."""
        if self._nlp is None:
            try:
                self._nlp = spacy.load("en_core_web_sm")
                sys.stdout.flush()
            except OSError:
                # Model not found, download it
                print("\n" + "=" * 80)
                print("FIRST-TIME SETUP: Downloading spaCy Language Model")
                print("=" * 80)
                print("Model: en_core_web_sm (~12 MB)")
                print("This is a one-time download and will only take a moment...")
                print("=" * 80 + "\n")
                subprocess.check_call([
                    sys.executable, "-m", "spacy", "download", "en_core_web_sm"
                ])
                print("\n" + "=" * 80)
                print("✓ Model downloaded successfully!")
                print("=" * 80 + "\n")
                # Try loading again after download
                print("Loading model...")
                self._nlp = spacy.load("en_core_web_sm")
                print("✓ Ready!\n")
                sys.stdout.flush()
        return self._nlp
    
    def extract_entities(self, airport_name: str, city: str = "", state: str = "") -> Tuple[List[str], List[str]]:
        """
        Extract person and location entities from airport name using spaCy NER.
        
        Returns:
            Tuple of (person_entities, location_entities)
        """
        # Check cache first
        cache_key = f"{airport_name}|{city}|{state}"
        if cache_key in self._entity_cache:
            return self._entity_cache[cache_key]
        
        # Clean the airport name for better NER
        clean_name = self._clean_name_for_ner(airport_name)
        
        # Process with spaCy
        doc = self.nlp(clean_name)
        
        persons = []
        locations = []
        
        for ent in doc.ents:
            entity_text = ent.text.strip()
            
            # Skip if entity is the city or state itself
            if entity_text.lower() in [city.lower(), state.lower()]:
                continue
            
            # Skip if entity is just a generic descriptor
            if entity_text in self.config.GENERIC_DESCRIPTORS:
                continue
            
            if ent.label_ == "PERSON":
                # Clean person names by removing trailing generic descriptors
                cleaned = self._clean_entity_text(entity_text)
                if cleaned:
                    persons.append(cleaned)
            elif ent.label_ in ["GPE", "LOC", "FAC"]:
                # Clean location names by removing trailing generic descriptors
                cleaned = self._clean_entity_text(entity_text)
                if cleaned:
                    locations.append(cleaned)
        
        # Also check for pattern-based location detection
        locations.extend(self._extract_pattern_locations(clean_name))
        
        # Remove duplicates while preserving order
        persons = list(dict.fromkeys(persons))
        locations = list(dict.fromkeys(locations))
        
        # Cache the results
        self._entity_cache[cache_key] = (persons, locations)
        
        return persons, locations
    
    def _clean_name_for_ner(self, airport_name: str) -> str:
        """Clean the airport name for better NER by removing suffixes."""
        clean_name = airport_name
        for suffix in self.config.NAME_SUFFIXES:
            clean_name = clean_name.replace(suffix, "").strip()
        return clean_name
    
    def _clean_entity_text(self, entity_text: str) -> str:
        """Clean entity text by removing generic descriptors."""
        words = entity_text.split()
        cleaned_words = [w for w in words if w not in self.config.GENERIC_DESCRIPTORS]
        return " ".join(cleaned_words)
    
    def _extract_pattern_locations(self, clean_name: str) -> List[str]:
        """Extract locations based on patterns like 'County', 'City', etc."""
        locations = []
        words = clean_name.split()
        
        for i, word in enumerate(words):
            if word in self.config.LOCATION_PATTERNS and i > 0:
                # Get preceding word(s) that might be part of the location name
                potential_location = []
                j = i - 1
                while j >= 0 and words[j] not in self.config.GENERIC_DESCRIPTORS:
                    potential_location.insert(0, words[j])
                    j -= 1
                if potential_location:
                    location_name = " ".join(potential_location + [word])
                    locations.append(location_name)
        
        return locations
    
    def get_first_occurring_entity(self, airport_name: str, persons: List[str], locations: List[str]) -> Optional[str]:
        """
        Determine which entity appears first in the airport name.
        Prioritizes the first complete entity found.
        """
        if not persons and not locations:
            return None
        
        # Clean name for searching
        clean_name = airport_name.lower()
        
        # Find positions of all entities
        entity_positions = []
        
        for person in persons:
            pos = clean_name.find(person.lower())
            if pos != -1:
                entity_positions.append((pos, person))
        
        for location in locations:
            pos = clean_name.find(location.lower())
            if pos != -1:
                entity_positions.append((pos, location))
        
        if not entity_positions:
            return None
        
        # Sort by position (first occurrence) and return the first entity
        entity_positions.sort(key=lambda x: x[0])
        return entity_positions[0][1]
    
    def extract_distinguishing_entity(self, airport_name: str, city: str, state: str) -> Optional[str]:
        """
        Extract the most relevant distinguishing entity using NER.
        Entities are truncated to a maximum of 3 words.
        """
        # Get entities from the name
        persons, locations = self.extract_entities(airport_name, city, state)
        
        # Get the first occurring entity
        entity = self.get_first_occurring_entity(airport_name, persons, locations)
        
        # Truncate to 3 words maximum, treating /, -, and spaces as word separators
        if entity:
            words = re.split(r'[\s\-/]+', entity)
            words = [w for w in words if w]  # Remove empty strings
            if len(words) > 3:
                entity = ' '.join(words[:3])
        
        return entity