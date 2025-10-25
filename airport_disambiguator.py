import json
import re
from collections import defaultdict

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
        'AFB', 'International', 'Intercontinental', 'Regional', 'Municipal', 'County',
        'Executive', 'Metropolitan', 'National', 'Memorial', 'Central',
        'East', 'West', 'North', 'South', 'Downtown', 'City', 'Base',
        'Airfield', 'Airpark', 'General', 'Private', 'Public',
        'Commercial', 'Domestic', 'Civil', 'Military', 'Boeing'
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
    def __init__(self, airports_file_path):
        self.airports_file_path = airports_file_path
        
        # Build complete HIGH_PRIORITY_WORDS set from base words + all shortening terms
        self.HIGH_PRIORITY_WORDS = self.BASE_HIGH_PRIORITY_WORDS.copy()
        # Add both the original and shortened forms from the replacements
        for original, shortened in self.SHORTENING_REPLACEMENTS.items():
            self.HIGH_PRIORITY_WORDS.add(original)
            self.HIGH_PRIORITY_WORDS.add(shortened)
        
        self.icao_to_pretty_name = self._generate_pretty_names()

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
        if city:
            city_words = city.lower().split()
            for word in city_words:
                if word in airport_name_lower:
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
    
    def _get_non_high_priority_word(self, airport_name, location):
        """Get the first non-high priority word from the airport name.
        Returns None if no such word is found."""
        name = self._shorten_name(airport_name)
        location_words = {word.lower() for word in location.split()}
        distinguishing_parts = [word for word in name.split() if word.lower() not in location_words]
        
        # Find the first non-high-priority word
        for word in distinguishing_parts:
            is_high_priority = word in self.HIGH_PRIORITY_WORDS or word.lower().capitalize() in self.HIGH_PRIORITY_WORDS
            if not is_high_priority:
                return word
        
        # If no non-high priority word found, return the first word
        return distinguishing_parts[0] if distinguishing_parts else None

    def _generate_pretty_names(self):
        location_to_airports = defaultdict(list)
        try:
            with open(self.airports_file_path, 'r', encoding='utf-8') as f:
                airports_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

        # Map each airport to its base location (state or city)
        icao_to_location = {}
        for icao, details in airports_data.items():
            base_location = self._get_base_location(details)
            if base_location:
                location_to_airports[base_location].append(icao)
                icao_to_location[icao] = base_location

        icao_to_pretty_name = {}
        for location, icaos in location_to_airports.items():
            if not icaos:
                continue
            
            if len(icaos) == 1:
                icao = icaos[0]
                airport_details = airports_data[icao]
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
                    # Name doesn't contain location - add a non-high priority word with hyphen
                    non_high_priority_word = self._get_non_high_priority_word(airport_name, location)
                    if non_high_priority_word:
                        icao_to_pretty_name[icao] = f"{location} - {non_high_priority_word}"
                    else:
                        icao_to_pretty_name[icao] = location
                continue

            # Multiple airports in this location - need to disambiguate them.
            # Split into two groups: those that start with location vs those that don't
            airport_names = {icao: self._shorten_name(airports_data[icao].get('name', icao)) for icao in icaos}
            
            icaos_start_with_location = []
            icaos_dont_start_with_location = []
            
            for icao in icaos:
                full_name = airports_data[icao].get('name', icao)
                if full_name.lower().startswith(location.lower()):
                    icaos_start_with_location.append(icao)
                else:
                    icaos_dont_start_with_location.append(icao)
            
            # Process airports that DON'T start with location
            for icao in icaos_dont_start_with_location:
                full_name = airports_data[icao].get('name', '')
                city = airports_data[icao].get('city', '')
                state = airports_data[icao].get('state', '')
                
                # Check if it's a military airport - use special formatting
                military_name = self._get_military_name(full_name, location)
                if military_name:
                    icao_to_pretty_name[icao] = military_name
                # Check if name contains location - if not, use hyphen format
                elif not self._name_contains_location(full_name, city, state):
                    non_high_priority_word = self._get_non_high_priority_word(full_name, location)
                    if non_high_priority_word:
                        icao_to_pretty_name[icao] = f"{location} - {non_high_priority_word}"
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

    def get_pretty_name(self, icao):
        return self.icao_to_pretty_name.get(icao, icao)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python airport_disambiguator.py <airports.json> <ICAO1> [ICAO2] [ICAO3] ...")
        print("\nExample: python airport_disambiguator.py airports.json KMER KBAB KNZY")
        sys.exit(1)
    
    airports_file = sys.argv[1]
    icaos = sys.argv[2:]
    
    try:
        disambiguator = AirportDisambiguator(airports_file)
        
        print(f"\nAirport Pretty Names from {airports_file}:")
        print("=" * 80)
        
        for icao in icaos:
            pretty_name = disambiguator.get_pretty_name(icao)
            print(f"{icao}: {pretty_name}")
        
        print()
    except FileNotFoundError:
        print(f"Error: File '{airports_file}' not found.")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Error: File '{airports_file}' is not valid JSON.")
        sys.exit(1)