import requests
import json
import csv
import math
import os
from collections import defaultdict

# Define the preferred order for control positions
CONTROL_POSITION_ORDER = ["TWR", "GND", "DEL"] # ATIS is handled specially in display logic

# VATSIM data endpoint
VATSIM_DATA_URL = "https://data.vatsim.net/v3/vatsim-data.json"

def haversine_distance_nm(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance between two points
    on the earth (specified in decimal degrees)
    Returns distance in nautical miles
    """
    # Convert decimal degrees to radians
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    
    # Haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    r = 3440.065 # Radius of earth in nautical miles
    return c * r

def format_eta_display(eta_hours):
    """Format ETA hours into a readable string"""
    if eta_hours == float('inf'):
        return ""  # No arrivals
    elif eta_hours < 1.0:
        minutes = int(eta_hours * 60)
        return f"{minutes}m"
    else:
        hours = int(eta_hours)
        minutes = int((eta_hours - hours) * 60)
        if minutes == 0:
            return f"{hours}h"
        else:
            return f"{hours}h{minutes}m"

def load_airport_data(filename):
    """Load airport data from CSV file"""
    airports = {}
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                icao = row['icao']
                if icao:  # Only include airports with ICAO codes
                    airports[icao] = {
                        'latitude': float(row['latitude']),
                        'longitude': float(row['longitude']),
                        'country_code': row['country_code']
                    }
    except FileNotFoundError:
        print(f"Error: Airport data file '{filename}' not found.")
    return airports

def load_custom_groupings(filename):
    """Load custom airport groupings from JSON file"""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: Custom groupings file '{filename}' not found.")
        return None
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from '{filename}'. Check file format.")
        return None

def download_vatsim_data():
    """Download VATSIM data from the API"""
    try:
        response = requests.get(VATSIM_DATA_URL)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Error downloading VATSIM data: {e}")
        return None

def _get_valid_icao_from_callsign(icao_candidate, airports_data):
    """
    Attempts to resolve an ICAO candidate from a callsign, considering implied 'K' for US airports.
    Returns a valid ICAO or None if not found in airports_data.
    """
    # 1. Check if the icao_candidate itself is a valid ICAO in our data
    if icao_candidate in airports_data:
        return icao_candidate

    # 2. If not found, try prepending 'K' for 3-letter US airport candidates
    if len(icao_candidate) == 3 and icao_candidate.isalpha():
        k_prefixed_icao = 'K' + icao_candidate
        if k_prefixed_icao in airports_data and airports_data[k_prefixed_icao]['country_code'] == 'US':
            return k_prefixed_icao
            
    return None

def get_staffed_positions(data, airports_data, excluded_frequency="199.998"):
    """
    Extracts staffed positions at each airport from VATSIM data.
    Excludes positions with a specific frequency.
    """
    staffed_positions = defaultdict(set)
    controllers = data.get('controllers', [])
    for controller in controllers:
        callsign = controller.get('callsign', '')
        frequency = controller.get('frequency', '')

        # Exclude specific frequency
        if frequency == excluded_frequency:
            continue

        parts = callsign.split('_')
        if len(parts) > 0:
            icao_candidate_prefix = parts[0]
            position_suffix = parts[-1]

            # Only consider non-ATIS positions for the 'controllers' array
            allowed_positions = {"DEL", "GND", "TWR"}

            if position_suffix in allowed_positions:
                valid_icao = _get_valid_icao_from_callsign(icao_candidate_prefix, airports_data)
                
                if valid_icao:
                    staffed_positions[valid_icao].add(position_suffix)

    # Process ATIS
    atis_list = data.get('atis', [])
    for atis_station in atis_list:
        callsign = atis_station.get('callsign', '')
        
        parts = callsign.split('_')
        if len(parts) > 0:
            icao_candidate_prefix = parts[0]
            # The position suffix for ATIS is generally "ATIS"
            position_suffix = parts[-1]

            if position_suffix == "ATIS":
                valid_icao = _get_valid_icao_from_callsign(icao_candidate_prefix, airports_data)
                
                if valid_icao:
                    staffed_positions[valid_icao].add("ATIS")
    
    # Sort non-ATIS positions based on CONTROL_POSITION_ORDER for consistent display.
    # ATIS is handled separately in the display logic for TOP-DOWN.
    ordered_staffed_positions = {}
    for icao, positions in staffed_positions.items():
        sorted_positions = [pos for pos in CONTROL_POSITION_ORDER if pos in positions]
        if "ATIS" in positions:
            sorted_positions.append("ATIS") # Always append ATIS at the end if present
        ordered_staffed_positions[icao] = sorted_positions
    
    return ordered_staffed_positions

def filter_flights_by_airports(data, airports, airport_allowlist=None):
    """Filter flights by departure and arrival airports"""
    flights = data.get('pilots', [])
    filtered_flights = []
    
    for flight in flights:
        # For flights with flight plans
        if flight.get('flight_plan'):
            departure = flight['flight_plan'].get('departure')
            arrival = flight['flight_plan'].get('arrival')
            
            # If allowlist is provided, check if either departure or arrival is in the allowlist
            # Otherwise, check if both departure and arrival airports are in our airport data
            if airport_allowlist:
                if departure in airports or arrival in airports:
                    filtered_flights.append({
                        'callsign': flight.get('callsign'),
                        'departure': departure,
                        'arrival': arrival,
                        'latitude': flight.get('latitude'),
                        'longitude': flight.get('longitude'),
                        'groundspeed': flight.get('groundspeed'),
                        'altitude': flight.get('altitude')
                    })
            elif departure in airports and arrival in airports:
                filtered_flights.append({
                    'callsign': flight.get('callsign'),
                    'departure': departure,
                    'arrival': arrival,
                    'latitude': flight.get('latitude'),
                    'longitude': flight.get('longitude'),
                    'groundspeed': flight.get('groundspeed'),
                    'altitude': flight.get('altitude')
                })
        # For flights without flight plans, we'll still include them for ground analysis
        # but with None for departure/arrival
        elif flight.get('latitude') is not None and flight.get('longitude') is not None:
            filtered_flights.append({
                'callsign': flight.get('callsign'),
                'departure': None,
                'arrival': None,
                'latitude': flight.get('latitude'),
                'longitude': flight.get('longitude'),
                'groundspeed': flight.get('groundspeed'),
                'altitude': flight.get('altitude')
            })
    
    return filtered_flights

def get_nearest_airport_if_on_ground(flight, airports, max_distance_nm=6, max_groundspeed=40):
    """
    Determine if a flight is on the ground at the departure airport
    Based on distance from airport and groundspeed
    """
    if flight['groundspeed'] > max_groundspeed:
        return None
    
    # For flights with flight plans but not near departure or arrival
    # or flights without flight plans, find the nearest airport
    if flight['latitude'] is not None and flight['longitude'] is not None:
        nearest_icao = None
        min_distance = float('inf')
        for icao, airport_data in airports.items():
            # Calculate distance from airport
            distance = haversine_distance_nm(
                flight['latitude'],
                flight['longitude'],
                airport_data['latitude'],
                airport_data['longitude']
            )

            # Keep the minimum distance/airport
            if distance < min_distance:
                min_distance = distance
                nearest_icao = icao

        # If the nearest airport is within the threshold, consider the flight on ground there
        if nearest_icao is not None and min_distance <= max_distance_nm:
            return nearest_icao
    
    return None

def is_flight_flying_near_arrival(flight, airports, max_eta_hours=1.0, min_groundspeed=40):
    """
    Determine if a flight is within an hour of arriving at the arrival airport
    Based on distance and groundspeed
    """
    if flight['groundspeed'] < min_groundspeed:
        return False
    
    # For flights with arrival airport in flight plan
    if flight['arrival'] and flight['arrival'] in airports:
        arrival_airport = airports.get(flight['arrival'])
        if not arrival_airport:
            return False
        
        # Calculate distance to arrival airport
        distance = haversine_distance_nm(
            flight['latitude'],
            flight['longitude'],
            arrival_airport['latitude'],
            arrival_airport['longitude']
        )
        
        # Calculate estimated time of arrival (in hours)
        if flight['groundspeed'] > 0:
            eta_hours = distance / flight['groundspeed']
            return eta_hours <= max_eta_hours if max_eta_hours > 0 else True
        else:
            # If groundspeed is 0, we can't calculate ETA
            return max_eta_hours == 0 # If we don't care about ETA, consider stationary aircraft at departure airport as well
    
    return False

def find_nearest_airport(flight, airports):
    """
    Find the nearest airport to a flight's current position
    Returns the ICAO code of the nearest airport
    """
    if flight['latitude'] is None or flight['longitude'] is None:
        return None
    
    nearest_airport = None
    min_distance = float('inf')
    
    for icao, airport_data in airports.items():
        distance = haversine_distance_nm(
            flight['latitude'],
            flight['longitude'],
            airport_data['latitude'],
            airport_data['longitude']
        )
        
        if distance < min_distance:
            min_distance = distance
            nearest_airport = icao
    
    return nearest_airport

def analyze_flights_data(max_eta_hours=1.0, airport_allowlist=None, groupings_allowlist=None, supergroupings_allowlist=None, include_all_staffed=True):
    """Main function to analyze VATSIM flights and controller staffing - returns data structures"""
    # Get the directory where this script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Load airport data
    all_airports_data = load_airport_data(os.path.join(script_dir, 'iata-icao.csv'))
    
    # Load all custom groupings
    all_custom_groupings = load_custom_groupings(os.path.join(script_dir, 'custom_groupings.json'))
    
    # Determine which groupings to display and which to use for filtering
    display_custom_groupings = {}
    active_groupings_for_filter = {}
    
    if all_custom_groupings:
        if supergroupings_allowlist:
            supergroup_airports_set = set()
            included_group_names = set()
            
            for supergroup_name in supergroupings_allowlist:
                if supergroup_name in all_custom_groupings:
                    # Add airports from the supergrouping itself
                    current_supergroup_airports = set(all_custom_groupings[supergroup_name])
                    supergroup_airports_set.update(current_supergroup_airports)
                    included_group_names.add(supergroup_name)
                    
                    # Find all sub-groupings
                    for other_group_name, other_group_airports in all_custom_groupings.items():
                        if other_group_name != supergroup_name:
                            other_group_airports_set = set(other_group_airports)
                            # If the other grouping is a subset of the current supergroup, include it
                            if other_group_airports_set.issubset(current_supergroup_airports):
                                included_group_names.add(other_group_name)
                                supergroup_airports_set.update(other_group_airports_set)
                else:
                    print(f"Warning: Supergrouping '{supergroup_name}' not found in custom_groupings.json.")
            
            # Populate display and active groupings based on supergrouping logic
            for name in included_group_names:
                display_custom_groupings[name] = all_custom_groupings[name]
                active_groupings_for_filter[name] = all_custom_groupings[name]

        elif groupings_allowlist:
            # Existing logic for groupings_allowlist
            for group_name in groupings_allowlist:
                if group_name in all_custom_groupings:
                    display_custom_groupings[group_name] = all_custom_groupings[group_name]
                    active_groupings_for_filter[group_name] = all_custom_groupings[group_name]
                else:
                    print(f"Warning: Custom grouping '{group_name}' not found in custom_groupings.json.")
        else:
            # If no groupings_allowlist and no supergrouping, display all groupings
            display_custom_groupings = all_custom_groupings

        # Prepare main airport_allowlist based on provided --airports and/or active groupings
        final_airport_allowlist = set()
        if airport_allowlist:
            final_airport_allowlist.update(airport_allowlist)
        
        # Add airports from groupings to the filter if --groupings or --supergrouping was explicitly used
        if groupings_allowlist or supergroupings_allowlist:
            for group_name, airports_in_group in active_groupings_for_filter.items():
                final_airport_allowlist.update(airports_in_group)
            
        airport_allowlist = list(final_airport_allowlist) # Convert back to list

    else:
        display_custom_groupings = {}
        active_groupings_for_filter = {}


    if airport_allowlist: # If there's an explicit airport_allowlist (from --airports or active groupings)
        airports = {icao: data for icao, data in all_airports_data.items() if icao in airport_allowlist}
    else: # If no explicit airport_allowlist, use all airports
        airports = all_airports_data
    
    # Download VATSIM data
    data = download_vatsim_data()
    if not data:
        return None, None, None
    
    # Extract staffed positions
    staffed_positions = get_staffed_positions(data, all_airports_data)

    # Filter flights
    flights = filter_flights_by_airports(data, airports, airport_allowlist)
    
    # Count flights on ground at departure and near arrival
    departure_counts = defaultdict(int)
    arrival_counts = defaultdict(int)
    earliest_arrival_eta = defaultdict(lambda: float('inf'))  # Track earliest ETA per airport
    
    for flight in flights:
        nearest_airport_if_on_ground = get_nearest_airport_if_on_ground(flight, airports)
        if flight['departure'] and nearest_airport_if_on_ground == flight['departure']:
            # Count as departure if on ground at departure airport
            departure_counts[flight['departure']] += 1
        elif flight['arrival'] and nearest_airport_if_on_ground == flight['arrival']:
            # Count as arrival if on ground at arrival airport
            arrival_counts[flight['arrival']] += 1
        elif not flight['departure'] and not flight['arrival'] and nearest_airport_if_on_ground:
            # For flights on ground without flight plans, count them as a departure at the nearest airport
            departure_counts[nearest_airport_if_on_ground] += 1
        elif is_flight_flying_near_arrival(flight, airports, max_eta_hours):
            # Count as arrival if within the specified ETA hours of arrival airport
            arrival_counts[flight['arrival']] += 1
            
            # Calculate ETA for this flight and track the earliest one per airport
            if flight['arrival'] in airports and flight['groundspeed'] > 0:
                arrival_airport = airports[flight['arrival']]
                distance = haversine_distance_nm(
                    flight['latitude'],
                    flight['longitude'],
                    arrival_airport['latitude'],
                    arrival_airport['longitude']
                )
                eta_hours = distance / flight['groundspeed']
                if eta_hours < earliest_arrival_eta[flight['arrival']]:
                    earliest_arrival_eta[flight['arrival']] = eta_hours
    
    # Create a list of airports with their counts and staffed positions
    airport_data = []
    for airport in airports:
        departing = departure_counts.get(airport, 0)
        arriving = arrival_counts.get(airport, 0)
        
        current_staffed_positions = staffed_positions.get(airport, [])
        staffed_pos_display = ""

        if "ATIS" in current_staffed_positions and len(current_staffed_positions) == 1:
            staffed_pos_display = "TOP-DOWN"
        elif current_staffed_positions:
            # Remove ATIS from display if other positions are present
            if "ATIS" in current_staffed_positions:
                current_staffed_positions.remove("ATIS")
            # Join the already sorted list of positions
            staffed_pos_display = ", ".join(current_staffed_positions)
        
        total_flights = departing + arriving
        eta_display = format_eta_display(earliest_arrival_eta.get(airport, float('inf')))
        
        # Include airport if it has flights, or if it's staffed and we want to include staffed zero-plane airports
        if total_flights > 0 or (staffed_pos_display and include_all_staffed):
            airport_data.append((airport, str(total_flights), str(departing), str(arriving), eta_display, staffed_pos_display))
    
    # Sort by total count descending
    airport_data.sort(key=lambda x: int(x[1]), reverse=True)
    
    # Process custom groupings data
    grouped_data = []
    if display_custom_groupings:
        for group_name, group_airports in display_custom_groupings.items():
            group_departing = sum(departure_counts.get(ap_icao, 0) for ap_icao in group_airports)
            group_arriving = sum(arrival_counts.get(ap_icao, 0) for ap_icao in group_airports)
            group_total = group_departing + group_arriving
            
            # Find the earliest ETA among all airports in this grouping
            group_earliest_eta = float('inf')
            for ap_icao in group_airports:
                if ap_icao in earliest_arrival_eta:
                    if earliest_arrival_eta[ap_icao] < group_earliest_eta:
                        group_earliest_eta = earliest_arrival_eta[ap_icao]
            
            group_eta_display = format_eta_display(group_earliest_eta)
            
            if group_total > 0: # Only include groupings with activity
                grouped_data.append((group_name, str(group_total), str(group_departing), str(group_arriving), group_eta_display))
        
        grouped_data.sort(key=lambda x: int(x[1]), reverse=True)
    
    return airport_data, grouped_data, len(flights)