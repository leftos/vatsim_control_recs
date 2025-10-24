import json
import re
from collections import defaultdict

class AirportDisambiguator:
    # Base high-priority descriptor words
    BASE_HIGH_PRIORITY_WORDS = {
        'International', 'Intercontinental', 'Regional', 'Municipal', 'County',
        'Executive', 'Metropolitan', 'National', 'Memorial', 'Central',
        'East', 'West', 'North', 'South', 'Downtown', 'City', 'Base',
        'Airfield', 'Airpark', 'General', 'Private', 'Public',
        'Commercial', 'Domestic', 'Civil', 'Military'
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
                icao_to_pretty_name[icaos[0]] = location
                continue

            # This location has multiple airports, so all names must start with the location.
            airport_names = {icao: self._shorten_name(airports_data[icao].get('name', icao)) for icao in icaos}
            
            resolved_names = {}
            for icao_to_resolve in icaos:
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
                        
                        if self._is_unique_name(candidate_full_name, icao_to_resolve, icaos, location, airport_names):
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
                                
                                if self._is_unique_name(candidate_full_name, icao_to_resolve, icaos, location, airport_names):
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
                        
                        if self._is_unique_name(candidate_full_name, icao_to_resolve, icaos, location, airport_names):
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