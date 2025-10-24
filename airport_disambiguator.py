import json

from collections import defaultdict

class AirportDisambiguator:
    def __init__(self, airports_file_path):
        self.airports_file_path = airports_file_path
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
                # 1. Get the distinguishing parts of the name (remove location), preserving casing
                name = airport_names[icao_to_resolve]
                # Use regex for case-insensitive replacement to get the parts
                import re
                distinguishing_name = re.sub(f"^{re.escape(location)}", "", name, flags=re.IGNORECASE).strip()
                distinguishing_parts = distinguishing_name.split()

                # 2. Add words one-by-one until the name is unique
                for i in range(len(distinguishing_parts) + 1):
                    # Use original casing for the suffix
                    candidate_suffix = " ".join(distinguishing_parts[:i])
                    candidate_full_name = f"{location} {candidate_suffix}".strip()

                    # 3. Check for uniqueness against all other airports in this location (case-insensitively)
                    is_unique = True
                    for other_icao in icaos:
                        if icao_to_resolve == other_icao:
                            continue
                        
                        other_name = airport_names[other_icao]
                        other_dist_name = re.sub(f"^{re.escape(location)}", "", other_name, flags=re.IGNORECASE).strip()
                        other_dist_parts = other_dist_name.split()

                        other_suffix = " ".join(other_dist_parts[:i])
                        other_full_name = f"{location} {other_suffix}".strip()

                        if candidate_full_name.lower() == other_full_name.lower():
                            is_unique = False
                            break
                    
                    if is_unique:
                        resolved_names[icao_to_resolve] = candidate_full_name
                        break

                # Fallback if loop finishes (shouldn't happen with good data)
                if icao_to_resolve not in resolved_names:
                    resolved_names[icao_to_resolve] = airport_names[icao_to_resolve]

            icao_to_pretty_name.update(resolved_names)
                    
        return icao_to_pretty_name
    
    def _shorten_name(self, name):
        replacements = {
            "International": "Intl",
            "Executive": "Exec",
            "Regional": "Rgnl",
            "Airport": "Apt",
            "Field": "Fld",
            "Airpark": "Apk",
            "Station": "Sta",
        }
        
        for long, short in replacements.items():
            name = name.replace(long, short)
        return name

    def get_pretty_name(self, icao):
        return self.icao_to_pretty_name.get(icao, icao)