# Auto-install dependencies if missing
try:
    import auto_setup
except ImportError:
    pass  # auto_setup.py not available, dependencies should be pre-installed

import json
import re
from collections import defaultdict
from typing import List, Tuple, Optional
import spacy

class AirportDisambiguator:
    # Multi-word phrases that should always be included (check these first)
    ALWAYS_INCLUDE_PHRASES = {
        'Coast Guard'
    }
    
    # Words that should always be included in the airport name (military terms)
    ALWAYS_INCLUDE_WORDS = {
        'AFB', 'Navy', 'Naval', 'Army', 'Marine', 'Marines'
    }
    
    # Base high-priority descriptor words
    BASE_HIGH_PRIORITY_WORDS = {
        'AFB', 'International', 'Intercontinental', 'Regional', 'Municipal',
        'Executive', 'Metropolitan', 'National', 'Memorial', 'Central',
        'East', 'West', 'North', 'South', 'Downtown', 'City', 'Base',
        'Airfield', 'Airpark', 'Commercial', 'Domestic', 'Civil', 'Military', 'Boeing'
    }
    
    # Shortening replacements - both keys and values will be considered high-priority
    SHORTENING_REPLACEMENTS = {
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
    
    def __init__(self, airports_file_path, lazy_load=True, unified_data=None):
        self.airports_file_path = airports_file_path
        self.lazy_load = lazy_load
        
        # Build complete HIGH_PRIORITY_WORDS set from base words + all shortening terms
        self.HIGH_PRIORITY_WORDS = self.BASE_HIGH_PRIORITY_WORDS.copy()
        # Add both the original and shortened forms from the replacements
        for original, shortened in self.SHORTENING_REPLACEMENTS.items():
            self.HIGH_PRIORITY_WORDS.add(original)
            self.HIGH_PRIORITY_WORDS.add(shortened)
        
        # Initialize spaCy model (lazy loading)
        self._nlp = None
        
        # Cache for processed entities to avoid reprocessing
        self._entity_cache = {}
        
        # Load airports data - prefer unified_data if provided
        if unified_data:
            # Convert unified data format to airports.json format for compatibility
            self.airports_data = {}
            for code, info in unified_data.items():
                self.airports_data[code] = {
                    'icao': info.get('icao', code),
                    'iata': info.get('iata', ''),
                    'name': info.get('name', ''),
                    'city': info.get('city', ''),
                    'state': info.get('state', ''),
                    'country': info.get('country', ''),
                    'lat': info.get('latitude'),
                    'lon': info.get('longitude'),
                    'elevation': info.get('elevation'),
                    'tz': info.get('tz', '')
                }
        else:
            # Fallback to loading from file
            try:
                with open(self.airports_file_path, 'r', encoding='utf-8') as f:
                    self.airports_data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                self.airports_data = {}
        
        # Build location mapping
        self.location_to_airports = defaultdict(list)
        self.icao_to_location = {}
        for icao, details in self.airports_data.items():
            base_location = self._get_base_location(details)
            if base_location:
                self.location_to_airports[base_location].append(icao)
                self.icao_to_location[icao] = base_location
        
        if lazy_load:
            # Lazy loading mode: process locations on-demand
            self.icao_to_pretty_name = {}
            self._processed_locations = set()
        else:
            # Eager loading mode: process all airports upfront
            import time
            import sys
            
            start_time = time.time()
            print("Generating airport disambiguation mappings...")
            sys.stdout.flush()
            
            gen_start = time.time()
            self.icao_to_pretty_name = self._generate_all_pretty_names()
            gen_time = time.time() - gen_start
            
            total_time = time.time() - start_time
            print(f"✓ Processed {len(self.icao_to_pretty_name)} airports in {gen_time:.2f}s")
            print(f"✓ Disambiguator ready! (total: {total_time:.2f}s)\n")
            sys.stdout.flush()

    @property
    def nlp(self):
        """Lazy load spaCy model, downloading it if necessary."""
        if self._nlp is None:
            import sys
            import subprocess
            
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

    def _extract_entities_from_name(self, airport_name: str, city: str = "", state: str = "") -> Tuple[List[str], List[str]]:
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
        clean_name = airport_name
        for suffix in ["Airport", "Field", "Airfield", "Airpark", "Station"]:
            clean_name = clean_name.replace(suffix, "").strip()
        
        # Process with spaCy
        doc = self.nlp(clean_name)
        
        persons = []
        locations = []
        
        # Generic airport descriptors that shouldn't be part of entities
        generic_descriptors = {"International", "Regional", "Municipal", "Executive",
                              "Metropolitan", "National", "Memorial", "Central"}
        
        for ent in doc.ents:
            entity_text = ent.text.strip()
            
            # Skip if entity is the city or state itself
            if entity_text.lower() == city.lower() or entity_text.lower() == state.lower():
                continue
            
            # Skip if entity is just a generic descriptor
            if entity_text in generic_descriptors:
                continue
                
            if ent.label_ == "PERSON":
                # Clean person names by removing trailing generic descriptors
                person_words = entity_text.split()
                cleaned_words = [w for w in person_words if w not in generic_descriptors]
                if cleaned_words:
                    persons.append(" ".join(cleaned_words))
            elif ent.label_ in ["GPE", "LOC", "FAC"]:
                # Clean location names by removing trailing generic descriptors
                location_words = entity_text.split()
                cleaned_words = [w for w in location_words if w not in generic_descriptors]
                if cleaned_words:
                    locations.append(" ".join(cleaned_words))
        
        # Also check for pattern-based location detection (County, City, etc.)
        location_patterns = ["County", "City", "Township", "Parish", "Borough"]
        words = clean_name.split()
        
        for i, word in enumerate(words):
            if word in location_patterns and i > 0:
                # Get preceding word(s) that might be part of the location name
                potential_location = []
                j = i - 1
                while j >= 0 and words[j] not in generic_descriptors:
                    potential_location.insert(0, words[j])
                    j -= 1
                if potential_location:
                    location_name = " ".join(potential_location + [word])
                    if location_name not in locations:
                        locations.append(location_name)
        
        # Cache the results
        self._entity_cache[cache_key] = (persons, locations)
        
        return persons, locations

    def _get_first_occurring_entity(self, airport_name: str, persons: List[str], locations: List[str]) -> Optional[str]:
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
                entity_positions.append((pos, person, "person"))
        
        for location in locations:
            pos = clean_name.find(location.lower())
            if pos != -1:
                entity_positions.append((pos, location, "location"))
        
        if not entity_positions:
            return None
        
        # Sort by position (first occurrence)
        entity_positions.sort(key=lambda x: x[0])
        
        # Return the first entity
        return entity_positions[0][1]

    def _extract_distinguishing_entity(self, airport_name: str, city: str, state: str) -> Optional[str]:
        """
        Extract the most relevant distinguishing entity using NER.
        This is the main method that will be called to get the entity for disambiguation.
        """
        # Get entities from the name
        persons, locations = self._extract_entities_from_name(airport_name, city, state)
        
        # Get the first occurring entity
        entity = self._get_first_occurring_entity(airport_name, persons, locations)
        
        return entity

    def _get_base_location(self, airport_details):
        """Determine whether to use state or city as the base location name.
        Prefers state if the airport name starts with it, otherwise uses city."""
        name = airport_details.get('name', '')
        state = airport_details.get('state', '')
        city = airport_details.get('city', '')
        
        # Prefer state if the airport name starts with it
        if state and name.lower().startswith(state.lower()):
            return state
        return city
    
    def _name_contains_location(self, airport_name, city, state):
        """Check if the airport name contains the city or state name."""
        airport_name_lower = airport_name.lower()
        
        # Check if any word from the city name appears in the airport name
        # Split on spaces, hyphens, and slashes to handle cities like "Frankfurt-am-Main"
        if city:
            city_words = re.split(r'[\s\-/]+', city.lower())
            for word in city_words:
                if word and word in airport_name_lower:  # Skip empty strings
                    return True
        
        # Check if state name appears in the airport name
        if state and state.lower() in airport_name_lower:
            return True
        
        return False
    
    def _get_military_name(self, airport_name, location):
        """Get the military airport name.
        Returns the base name + military term (e.g., "Beale AFB", "North Island Navy").
        Returns None if no military term is found."""
        name = self._shorten_name(airport_name)
        
        # First, check for multi-word phrases
        military_phrase = None
        for phrase in self.ALWAYS_INCLUDE_PHRASES:
            if phrase in name:
                military_phrase = phrase
                break
        
        # Then check for single-word military terms
        military_word = None
        if not military_phrase:
            for word in name.split():
                if word in self.ALWAYS_INCLUDE_WORDS or word.lower().capitalize() in self.ALWAYS_INCLUDE_WORDS:
                    military_word = word
                    break
        
        military_term = military_phrase or military_word
        if not military_term:
            return None
        
        # Build the military name: get all non-location, non-generic words + military term
        location_words = {word.lower() for word in location.split()}
        
        # Generic words to skip (including shortened forms)
        generic_words_set = {
            'air', 'apt', 'airport', 'station', 'sta', 'fld', 'field', 'airfield',
            'apk', 'airpark', 'base', 'force', 'nas', 'trapnell', 'aiport', 'halsey'
        }
        
        name_parts = []
        words = name.split()
        
        for word in words:
            # Clean word for comparison (remove special chars)
            clean_word = word.replace('-', '').replace('/', '').replace('(', '').replace(')', '').lower()
            
            # Skip location words (but only if they're short - keep meaningful location names in military bases)
            if word.lower() in location_words and len(word) <= 4:
                continue
            
            # Skip generic words or compound words containing generic words
            if clean_word in generic_words_set:
                continue
            
            # Check if it's a compound word (contains - or /) and any part is generic
            if '-' in word or '/' in word:
                parts = word.replace('-', '/').split('/')
                if any(part.lower() in generic_words_set for part in parts):
                    continue
            
            # Skip the military term itself (we'll add it at the end)
            if word == military_term or (military_phrase and word in military_phrase.split()):
                continue
                
            name_parts.append(word)
        
        # Add the military term at the end
        if military_phrase:
            result = ' '.join(name_parts + [military_phrase])
        else:
            result = ' '.join(name_parts + [military_term])
        
        return result if result else None
    
    def _get_non_high_priority_words(self, airport_name, location):
        """Get all consecutive non-high priority words from the start of the airport name
        until a high-priority word is encountered.
        If 4+ words are collected, only return the last one. If 3 or fewer, return all.
        Returns None if no such words are found."""
        name = self._shorten_name(airport_name)
        location_words = {word.lower() for word in location.split()}
        distinguishing_parts = [word for word in name.split() if word.lower() not in location_words]
        
        # Collect all non-high-priority words from the start until we hit a high-priority word
        non_high_priority_words = []
        for word in distinguishing_parts:
            # For hyphenated/compound words, check if ANY part is high-priority
            is_high_priority = False
            if '-' in word or '/' in word:
                # Split on hyphens and slashes to check each part
                parts = word.replace('-', '/').split('/')
                for part in parts:
                    if part in self.HIGH_PRIORITY_WORDS or part.lower().capitalize() in self.HIGH_PRIORITY_WORDS:
                        is_high_priority = True
                        break
            else:
                is_high_priority = word in self.HIGH_PRIORITY_WORDS or word.lower().capitalize() in self.HIGH_PRIORITY_WORDS
            
            if is_high_priority:
                break  # Stop at the first high-priority word or compound containing one
            non_high_priority_words.append(word)
        
        # If we have 4 or more words, only keep the last one
        # If 3 or fewer, keep all of them
        if len(non_high_priority_words) >= 4:
            return non_high_priority_words[-1]
        elif non_high_priority_words:
            return ' '.join(non_high_priority_words)
        
        # If no non-high priority words found, return the first word
        return distinguishing_parts[0] if distinguishing_parts else None

    def _generate_all_pretty_names(self):
        """Generate pretty names for all airports (eager loading)."""
        import sys
        
        icao_to_pretty_name = {}
        total_locations = len(self.location_to_airports)
        processed = 0
        last_progress = 0
        print(f"  Processing airports... 0% ({processed}/{total_locations} locations)")
        
        for location, icaos in self.location_to_airports.items():
            processed += 1
            # Update progress every 10%
            progress_pct = int((processed / total_locations) * 100)
            if progress_pct >= last_progress + 10:
                print(f"  Processing airports... {progress_pct}% ({processed}/{total_locations} locations)")
                sys.stdout.flush()
                last_progress = progress_pct
            if not icaos:
                continue
            
            if len(icaos) == 1:
                icao = icaos[0]
                airport_details = self.airports_data[icao]
                airport_name = airport_details.get('name', '')
                city = airport_details.get('city', '')
                state = airport_details.get('state', '')
                
                # Check for military airports first - they get special formatting
                military_name = self._get_military_name(airport_name, location)
                
                # If it's a military airport, use the military name format (e.g., "Beale AFB")
                if military_name:
                    icao_to_pretty_name[icao] = military_name
                # Otherwise, check if the airport name contains the city or state
                elif self._name_contains_location(airport_name, city, state):
                    icao_to_pretty_name[icao] = location
                else:
                    # Name doesn't contain location - try NER first, then fall back
                    entity = self._extract_distinguishing_entity(airport_name, city, state)
                    if entity:
                        icao_to_pretty_name[icao] = f"{location} - {entity}"
                    else:
                        # Fall back to old method
                        non_high_priority_words = self._get_non_high_priority_words(airport_name, location)
                        if non_high_priority_words:
                            icao_to_pretty_name[icao] = f"{location} - {non_high_priority_words}"
                        else:
                            icao_to_pretty_name[icao] = location
                continue

            # Multiple airports in this location - need to disambiguate them.
            # Split into two groups: those that start with location vs those that don't
            airport_names = {icao: self._shorten_name(self.airports_data[icao].get('name', icao)) for icao in icaos}
            
            icaos_start_with_location = []
            icaos_dont_start_with_location = []
            
            for icao in icaos:
                full_name = self.airports_data[icao].get('name', icao)
                if full_name.lower().startswith(location.lower()):
                    icaos_start_with_location.append(icao)
                else:
                    icaos_dont_start_with_location.append(icao)
            
            # Process airports that DON'T start with location
            for icao in icaos_dont_start_with_location:
                full_name = self.airports_data[icao].get('name', '')
                city = self.airports_data[icao].get('city', '')
                state = self.airports_data[icao].get('state', '')
                
                # Check if it's a military airport - use special formatting
                military_name = self._get_military_name(full_name, location)
                if military_name:
                    icao_to_pretty_name[icao] = military_name
                # Check if name contains location - if not, use hyphen format with NER
                elif not self._name_contains_location(full_name, city, state):
                    entity = self._extract_distinguishing_entity(full_name, city, state)
                    if entity:
                        icao_to_pretty_name[icao] = f"{location} - {entity}"
                    else:
                        # Fall back to old method
                        non_high_priority_words = self._get_non_high_priority_words(full_name, location)
                        if non_high_priority_words:
                            icao_to_pretty_name[icao] = f"{location} - {non_high_priority_words}"
                        else:
                            icao_to_pretty_name[icao] = location
                else:
                    # Regular logic: location + distinguishing word (no hyphen)
                    name = airport_names[icao]
                    location_words = {word.lower() for word in location.split()}
                    distinguishing_parts = [word for word in name.split() if word.lower() not in location_words]
                    
                    # Find the first high-priority word, or just use the first word
                    high_priority_word = None
                    for word in distinguishing_parts:
                        if word in self.HIGH_PRIORITY_WORDS or word.lower().capitalize() in self.HIGH_PRIORITY_WORDS:
                            high_priority_word = word
                            break
                    
                    if high_priority_word:
                        icao_to_pretty_name[icao] = f"{location} {high_priority_word}"
                    elif distinguishing_parts:
                        icao_to_pretty_name[icao] = f"{location} {distinguishing_parts[0]}"
                    else:
                        icao_to_pretty_name[icao] = location
            
            # If only one airport starts with location, it just gets the location name
            if len(icaos_start_with_location) == 1:
                icao_to_pretty_name[icaos_start_with_location[0]] = location
                continue
            
            # If no airports start with location, we're done (already handled above)
            if len(icaos_start_with_location) == 0:
                continue
            
            # Multiple airports start with location - need to disambiguate them
            # Only compare against each other, not against the ones that don't start with location
            resolved_names = {}
            for icao_to_resolve in icaos_start_with_location:
                # 1. Get the distinguishing parts of the name (remove location words from anywhere), preserving casing
                name = airport_names[icao_to_resolve]
                # Remove all words from the location name (e.g., both "San" and "Carlos" from "San Carlos")
                location_words = {word.lower() for word in location.split()}
                distinguishing_parts = [word for word in name.split() if word.lower() not in location_words]

                # 2. Score and sort words by priority (high-priority descriptors first)
                scored_words = []
                for word in distinguishing_parts:
                    is_high_priority = word in self.HIGH_PRIORITY_WORDS or word.lower().capitalize() in self.HIGH_PRIORITY_WORDS
                    priority = 0 if is_high_priority else 1  # Lower number = higher priority
                    scored_words.append((priority, word))
                
                # Sort by priority, then by original position to maintain some order
                scored_words.sort(key=lambda x: (x[0], distinguishing_parts.index(x[1])))
                
                # 3. Try to find the shortest unique combination, prioritizing high-value words
                found = False
                
                # First, try single high-priority words
                for priority, word in scored_words:
                    if priority == 0:  # High priority word
                        candidate_suffix = word
                        candidate_full_name = f"{location} {candidate_suffix}".strip()
                        
                        if self._is_unique_name(candidate_full_name, icao_to_resolve, icaos_start_with_location, location, airport_names):
                            resolved_names[icao_to_resolve] = candidate_full_name
                            found = True
                            break
                
                # If no single high-priority word works, try adding more words
                # But still prioritize combinations that include high-priority words
                if not found:
                    # Try progressively longer combinations, but prefer those with high-priority words
                    for num_words in range(2, len(distinguishing_parts) + 1):
                        # Try all combinations of this length
                        for start_idx in range(len(distinguishing_parts) - num_words + 1):
                            candidate_words = distinguishing_parts[start_idx:start_idx + num_words]
                            
                            # Check if this combination includes at least one high-priority word
                            has_high_priority = any(
                                (word in self.HIGH_PRIORITY_WORDS or word.lower().capitalize() in self.HIGH_PRIORITY_WORDS)
                                for word in candidate_words
                            )
                            
                            if has_high_priority:
                                candidate_suffix = " ".join(candidate_words)
                                candidate_full_name = f"{location} {candidate_suffix}".strip()
                                
                                if self._is_unique_name(candidate_full_name, icao_to_resolve, icaos_start_with_location, location, airport_names):
                                    resolved_names[icao_to_resolve] = candidate_full_name
                                    found = True
                                    break
                        
                        if found:
                            break
                
                # Last resort: try sequential combinations without requiring high-priority words
                if not found:
                    for i in range(1, len(distinguishing_parts) + 1):
                        candidate_suffix = " ".join(distinguishing_parts[:i])
                        candidate_full_name = f"{location} {candidate_suffix}".strip()
                        
                        if self._is_unique_name(candidate_full_name, icao_to_resolve, icaos_start_with_location, location, airport_names):
                            resolved_names[icao_to_resolve] = candidate_full_name
                            found = True
                            break
                
                # Fallback if nothing works
                if not found:
                    resolved_names[icao_to_resolve] = airport_names[icao_to_resolve]

            icao_to_pretty_name.update(resolved_names)
                    
        return icao_to_pretty_name
    
    def _is_unique_name(self, candidate_name, current_icao, all_icaos, location, airport_names):
        """Check if a candidate name is unique among all airports in the location."""
        # Extract just the distinguishing part from the candidate (remove all location words)
        location_words = {word.lower() for word in location.split()}
        candidate_words = [word for word in candidate_name.split() if word.lower() not in location_words]
        candidate_suffix = " ".join(candidate_words).lower()
        
        for other_icao in all_icaos:
            if current_icao == other_icao:
                continue
            
            # Get the other airport's distinguishing words
            other_name = airport_names[other_icao]
            other_dist_words = [word for word in other_name.split() if word.lower() not in location_words]
            other_dist_suffix = " ".join(other_dist_words).lower()
            
            # Check if the other airport's distinguishing parts start with our candidate
            if other_dist_suffix.startswith(candidate_suffix):
                return False
        
        return True
    
    def _shorten_name(self, name):
        """Shorten common airport name parts using the defined replacements."""
        for long, short in self.SHORTENING_REPLACEMENTS.items():
            name = name.replace(long, short)
        return name

    def _process_location(self, location):
        """Process all airports in a specific location on-demand."""
        if location in self._processed_locations:
            return  # Already processed
        
        self._processed_locations.add(location)
        icaos = self.location_to_airports.get(location, [])
        
        if not icaos:
            return
        
        # Process this location using the same logic as _generate_all_pretty_names
        # but only for this specific location
        if len(icaos) == 1:
            icao = icaos[0]
            airport_details = self.airports_data[icao]
            airport_name = airport_details.get('name', '')
            city = airport_details.get('city', '')
            state = airport_details.get('state', '')
            
            # Check for military airports first
            military_name = self._get_military_name(airport_name, location)
            
            if military_name:
                self.icao_to_pretty_name[icao] = military_name
            elif self._name_contains_location(airport_name, city, state):
                self.icao_to_pretty_name[icao] = location
            else:
                entity = self._extract_distinguishing_entity(airport_name, city, state)
                if entity:
                    self.icao_to_pretty_name[icao] = f"{location} - {entity}"
                else:
                    non_high_priority_words = self._get_non_high_priority_words(airport_name, location)
                    if non_high_priority_words:
                        self.icao_to_pretty_name[icao] = f"{location} - {non_high_priority_words}"
                    else:
                        self.icao_to_pretty_name[icao] = location
            return
        
        # Multiple airports - use the full disambiguation logic
        airport_names = {icao: self._shorten_name(self.airports_data[icao].get('name', icao)) for icao in icaos}
        
        icaos_start_with_location = []
        icaos_dont_start_with_location = []
        
        for icao in icaos:
            full_name = self.airports_data[icao].get('name', icao)
            if full_name.lower().startswith(location.lower()):
                icaos_start_with_location.append(icao)
            else:
                icaos_dont_start_with_location.append(icao)
        
        # Process airports that DON'T start with location
        for icao in icaos_dont_start_with_location:
            full_name = self.airports_data[icao].get('name', '')
            city = self.airports_data[icao].get('city', '')
            state = self.airports_data[icao].get('state', '')
            
            military_name = self._get_military_name(full_name, location)
            if military_name:
                self.icao_to_pretty_name[icao] = military_name
            elif not self._name_contains_location(full_name, city, state):
                entity = self._extract_distinguishing_entity(full_name, city, state)
                if entity:
                    self.icao_to_pretty_name[icao] = f"{location} - {entity}"
                else:
                    non_high_priority_words = self._get_non_high_priority_words(full_name, location)
                    if non_high_priority_words:
                        self.icao_to_pretty_name[icao] = f"{location} - {non_high_priority_words}"
                    else:
                        self.icao_to_pretty_name[icao] = location
            else:
                name = airport_names[icao]
                location_words = {word.lower() for word in location.split()}
                distinguishing_parts = [word for word in name.split() if word.lower() not in location_words]
                
                high_priority_word = None
                for word in distinguishing_parts:
                    if word in self.HIGH_PRIORITY_WORDS or word.lower().capitalize() in self.HIGH_PRIORITY_WORDS:
                        high_priority_word = word
                        break
                
                if high_priority_word:
                    self.icao_to_pretty_name[icao] = f"{location} {high_priority_word}"
                elif distinguishing_parts:
                    self.icao_to_pretty_name[icao] = f"{location} {distinguishing_parts[0]}"
                else:
                    self.icao_to_pretty_name[icao] = location
        
        # Handle airports that start with location
        if len(icaos_start_with_location) == 1:
            self.icao_to_pretty_name[icaos_start_with_location[0]] = location
        elif len(icaos_start_with_location) > 1:
            # Full disambiguation logic for multiple airports starting with location
            resolved_names = {}
            for icao_to_resolve in icaos_start_with_location:
                name = airport_names[icao_to_resolve]
                location_words = {word.lower() for word in location.split()}
                distinguishing_parts = [word for word in name.split() if word.lower() not in location_words]
                
                scored_words = []
                for word in distinguishing_parts:
                    is_high_priority = word in self.HIGH_PRIORITY_WORDS or word.lower().capitalize() in self.HIGH_PRIORITY_WORDS
                    priority = 0 if is_high_priority else 1
                    scored_words.append((priority, word))
                
                scored_words.sort(key=lambda x: (x[0], distinguishing_parts.index(x[1])))
                
                found = False
                for priority, word in scored_words:
                    if priority == 0:
                        candidate_suffix = word
                        candidate_full_name = f"{location} {candidate_suffix}".strip()
                        
                        if self._is_unique_name(candidate_full_name, icao_to_resolve, icaos_start_with_location, location, airport_names):
                            resolved_names[icao_to_resolve] = candidate_full_name
                            found = True
                            break
                
                if not found:
                    for num_words in range(2, len(distinguishing_parts) + 1):
                        for start_idx in range(len(distinguishing_parts) - num_words + 1):
                            candidate_words = distinguishing_parts[start_idx:start_idx + num_words]
                            
                            has_high_priority = any(
                                (word in self.HIGH_PRIORITY_WORDS or word.lower().capitalize() in self.HIGH_PRIORITY_WORDS)
                                for word in candidate_words
                            )
                            
                            if has_high_priority:
                                candidate_suffix = " ".join(candidate_words)
                                candidate_full_name = f"{location} {candidate_suffix}".strip()
                                
                                if self._is_unique_name(candidate_full_name, icao_to_resolve, icaos_start_with_location, location, airport_names):
                                    resolved_names[icao_to_resolve] = candidate_full_name
                                    found = True
                                    break
                        
                        if found:
                            break
                
                if not found:
                    for i in range(1, len(distinguishing_parts) + 1):
                        candidate_suffix = " ".join(distinguishing_parts[:i])
                        candidate_full_name = f"{location} {candidate_suffix}".strip()
                        
                        if self._is_unique_name(candidate_full_name, icao_to_resolve, icaos_start_with_location, location, airport_names):
                            resolved_names[icao_to_resolve] = candidate_full_name
                            found = True
                            break
                
                if not found:
                    resolved_names[icao_to_resolve] = airport_names[icao_to_resolve]
            
            self.icao_to_pretty_name.update(resolved_names)

    def get_pretty_name(self, icao):
        # If lazy loading is enabled and this location hasn't been processed yet
        if self.lazy_load and icao in self.icao_to_location:
            location = self.icao_to_location[icao]
            if location not in self._processed_locations:
                self._process_location(location)
        
        return self.icao_to_pretty_name.get(icao, icao)


if __name__ == "__main__":
    import argparse
    import os
    from airport_data_loader import load_unified_airport_data
    
    parser = argparse.ArgumentParser(
        description="Test airport name disambiguation with provided ICAO codes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python airport_disambiguator.py KMER KBAB KNZY
  python airport_disambiguator.py --apt-base APT_BASE.csv --airports airports.json --iata-icao iata-icao.csv KSFO KLAX
        """
    )
    
    parser.add_argument(
        "icao_codes",
        nargs="+",
        help="One or more ICAO airport codes to disambiguate"
    )
    parser.add_argument(
        "--apt-base",
        default="APT_BASE.csv",
        help="Path to APT_BASE.csv file (default: APT_BASE.csv)"
    )
    parser.add_argument(
        "--airports",
        default="airports.json",
        help="Path to airports.json file (default: airports.json)"
    )
    parser.add_argument(
        "--iata-icao",
        default="iata-icao.csv",
        help="Path to iata-icao.csv file (default: iata-icao.csv)"
    )
    
    args = parser.parse_args()
    
    try:
        # Load unified airport data from all three sources
        print(f"Loading airport data from:")
        print(f"  - {args.apt_base}")
        print(f"  - {args.airports}")
        print(f"  - {args.iata_icao}")
        
        unified_data = load_unified_airport_data(
            args.apt_base,
            args.airports,
            args.iata_icao
        )
        
        # Create disambiguator with unified data
        disambiguator = AirportDisambiguator(
            args.airports,
            unified_data=unified_data
        )
        
        print(f"\nAirport Pretty Names:")
        print("=" * 80)
        
        for icao in args.icao_codes:
            pretty_name = disambiguator.get_pretty_name(icao)
            print(f"{icao}: {pretty_name}")
        
        print()
    except FileNotFoundError as e:
        print(f"Error: {e}")
        import sys
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in file - {e}")
        import sys
        sys.exit(1)