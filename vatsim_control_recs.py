import requests
import json
import csv
import math
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from airport_disambiguator import AirportDisambiguator # pyright: ignore[reportAttributeAccessIssue]

# Define the preferred order for control positions
CONTROL_POSITION_ORDER = ["APP", "DEP", "TWR", "GND", "DEL"] # ATIS is handled specially in display logic

# VATSIM data endpoint
VATSIM_DATA_URL = "https://data.vatsim.net/v3/vatsim-data.json"

# Cache for aircraft approach speeds
_AIRCRAFT_APPROACH_SPEEDS = None

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

def format_eta_display(eta_hours, arrivals_in_flight_count, arrivals_on_ground_count):
    """Format ETA hours into a readable string"""
    # If there are no in-flight arrivals but there are arrivals on ground
    if eta_hours == float('inf') and arrivals_on_ground_count > 0 and arrivals_in_flight_count == 0:
        return "LANDED"
    elif eta_hours == float('inf'):
        return ""  # No arrivals at all
    elif eta_hours < 1.0:
        minutes = int(eta_hours * 60)
        return f"{minutes}m" if minutes > 0 else "<1m"
    else:
        hours = int(eta_hours)
        minutes = int((eta_hours - hours) * 60)
        return f"{hours}h{minutes:02d}m"

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

def load_aircraft_approach_speeds(filename):
    """
    Load aircraft approach speeds from CSV file.
    Returns a dictionary mapping ICAO aircraft codes to approach speeds (in knots).
    Uses caching to avoid reloading on every call.
    """
    global _AIRCRAFT_APPROACH_SPEEDS
    
    if _AIRCRAFT_APPROACH_SPEEDS is not None:
        return _AIRCRAFT_APPROACH_SPEEDS
    
    approach_speeds = {}
    try:
        with open(filename, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                icao_code = row.get('ICAO_Code', '').strip()
                approach_speed_str = row.get('Approach_Speed_knot', '').strip()
                
                # Only add entries with valid ICAO codes and approach speeds
                if icao_code and approach_speed_str and approach_speed_str != 'N/A':
                    try:
                        approach_speed = int(approach_speed_str)
                        approach_speeds[icao_code] = approach_speed
                    except ValueError:
                        # Skip entries with invalid approach speed values
                        continue
        
        _AIRCRAFT_APPROACH_SPEEDS = approach_speeds
        return approach_speeds
    except FileNotFoundError:
        print(f"Warning: Aircraft data file '{filename}' not found. ETA calculations will not use approach speeds.")
        _AIRCRAFT_APPROACH_SPEEDS = {}
        return {}
    except Exception as e:
        print(f"Warning: Error loading aircraft data from '{filename}': {e}. ETA calculations will not use approach speeds.")
        _AIRCRAFT_APPROACH_SPEEDS = {}
        return {}

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
    except json.JSONDecodeError as e:
        print(f"Error decoding VATSIM data JSON: {e}")
        return None

script_dir = os.path.dirname(os.path.abspath(__file__))
DISAMBIGUATOR = AirportDisambiguator(os.path.join(script_dir, 'airports.json'))

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
            allowed_positions = CONTROL_POSITION_ORDER.copy()

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
        # Check if flight has a valid flight plan with non-empty departure or arrival
        departure = None
        arrival = None
        has_valid_flight_plan = False
        
        if flight.get('flight_plan'):
            departure = flight['flight_plan'].get('departure')
            arrival = flight['flight_plan'].get('arrival')
            
            # Treat empty strings as None/null for departure and arrival
            if not departure:
                departure = None
            if not arrival:
                arrival = None
            
            # Only consider it a valid flight plan if at least one field is non-empty
            has_valid_flight_plan = departure is not None or arrival is not None
        
        if has_valid_flight_plan:
            # If allowlist is provided, check if either departure or arrival is in the allowlist
            # Otherwise, check if both departure and arrival airports are in our airport data
            if airport_allowlist:
                if (departure and departure in airports) or (arrival and arrival in airports):
                    filtered_flights.append({
                        'callsign': flight.get('callsign'),
                        'departure': departure,
                        'arrival': arrival,
                        'latitude': flight.get('latitude'),
                        'longitude': flight.get('longitude'),
                        'groundspeed': flight.get('groundspeed'),
                        'altitude': flight.get('altitude'),
                        'flight_plan': flight.get('flight_plan')
                    })
            elif departure and arrival and departure in airports and arrival in airports:
                filtered_flights.append({
                    'callsign': flight.get('callsign'),
                    'departure': departure,
                    'arrival': arrival,
                    'latitude': flight.get('latitude'),
                    'longitude': flight.get('longitude'),
                    'groundspeed': flight.get('groundspeed'),
                    'altitude': flight.get('altitude'),
                    'flight_plan': flight.get('flight_plan')
                })
        # For flights without valid flight plans, we'll still include them for ground analysis
        # but with None for departure/arrival
        elif flight.get('latitude') is not None and flight.get('longitude') is not None:
            filtered_flights.append({
                'callsign': flight.get('callsign'),
                'departure': None,
                'arrival': None,
                'latitude': flight.get('latitude'),
                'longitude': flight.get('longitude'),
                'groundspeed': flight.get('groundspeed'),
                'altitude': flight.get('altitude'),
                'flight_plan': flight.get('flight_plan')
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
            return max_eta_hours == 0 or eta_hours <= max_eta_hours
    
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

    # Load aircraft approach speeds
    aircraft_approach_speeds = load_aircraft_approach_speeds(os.path.join(script_dir, 'aircraft_data.csv'))

    # Filter flights
    flights = filter_flights_by_airports(data, airports, airport_allowlist)
    
    # Count flights on ground at departure and near arrival
    departure_counts = defaultdict(int)
    arrival_counts = defaultdict(int)
    arrival_counts_all = defaultdict(int)  # Track all arrivals regardless of max_eta_hours
    earliest_arrival_eta = defaultdict(lambda: float('inf'))  # Track earliest ETA per airport
    arrivals_on_ground = defaultdict(int)  # Track arrivals already on ground
    arrivals_in_flight = defaultdict(int)  # Track arrivals still in flight
    
    for flight in flights:
        # First, calculate the true earliest ETA for all in-flight arrivals
        if flight['arrival'] in airports and flight.get('groundspeed', 0) > 40:
            _, _, eta_hours = _calculate_eta(flight, airports, aircraft_approach_speeds)
            if eta_hours < earliest_arrival_eta[flight['arrival']]:
                earliest_arrival_eta[flight['arrival']] = eta_hours

        nearest_airport_if_on_ground = get_nearest_airport_if_on_ground(flight, airports)
        if flight['departure'] and nearest_airport_if_on_ground == flight['departure']:
            # Count as departure if on ground at departure airport
            departure_counts[flight['departure']] += 1
        elif flight['arrival'] and nearest_airport_if_on_ground == flight['arrival']:
            # Count as arrival if on ground at arrival airport
            arrival_counts[flight['arrival']] += 1
            arrival_counts_all[flight['arrival']] += 1
            arrivals_on_ground[flight['arrival']] += 1
        elif not flight['departure'] and not flight['arrival'] and nearest_airport_if_on_ground:
            # For flights on ground without flight plans, count them as a departure at the nearest airport
            departure_counts[nearest_airport_if_on_ground] += 1
        elif is_flight_flying_near_arrival(flight, airports, max_eta_hours):
            # Count as arrival if within the specified ETA hours of arrival airport
            arrival_counts[flight['arrival']] += 1
            arrival_counts_all[flight['arrival']] += 1
            arrivals_in_flight[flight['arrival']] += 1
        elif flight['arrival'] and flight['arrival'] in airports:
            # Flight has arrival filed but isn't on ground at arrival and isn't flying nearby
            # This catches flights on ground at departure that haven't departed yet, or in-flight beyond max_eta_hours
            arrival_counts_all[flight['arrival']] += 1

    # Create a list of airports with their counts and staffed positions
    airport_data = []
    for airport in airports:
        departing = departure_counts.get(airport, 0)
        arriving = arrival_counts.get(airport, 0)
        arriving_all = arrival_counts_all.get(airport, 0)
        
        current_staffed_positions = staffed_positions.get(airport, [])
        staffed_pos_display = ""

        if "ATIS" in current_staffed_positions and len(current_staffed_positions) == 1:
            staffed_pos_display = "TOP-DOWN"
        elif current_staffed_positions:
            # Remove ATIS from display if other positions are present
            # Make a copy to avoid mutating the original list
            display_positions = [pos for pos in current_staffed_positions if pos != "ATIS"]
            # Join the already sorted list of positions
            staffed_pos_display = ", ".join(display_positions)
        
        total_flights = departing + arriving
        eta_display = format_eta_display(
            earliest_arrival_eta.get(airport, float('inf')),
            arrivals_in_flight.get(airport, 0),
            arrivals_on_ground.get(airport, 0)
        )
        
        # Include airport if it has flights, or if it's staffed and we want to include staffed zero-plane airports
        if total_flights > 0 or (staffed_pos_display and include_all_staffed):
            # Get the pretty name for the airport
            pretty_name = DISAMBIGUATOR.get_pretty_name(airport) if DISAMBIGUATOR else airport
            # Pad numeric columns to consistent width (3 characters, right-aligned)
            dep_str = str(departing).rjust(3)
            arr_str = str(arriving).rjust(3)
            arr_all_str = str(arriving_all).rjust(3)
            # Include arriving_all in the tuple when max_eta_hours is specified
            if max_eta_hours != 0:
                airport_data.append((airport, pretty_name, str(total_flights), dep_str, arr_str, arr_all_str, eta_display, staffed_pos_display))
            else:
                airport_data.append((airport, pretty_name, str(total_flights), dep_str, arr_str, eta_display, staffed_pos_display))
    
    # Sort by total count descending
    airport_data.sort(key=lambda x: int(x[2]), reverse=True)
    
    # Process custom groupings data
    grouped_data = []
    if display_custom_groupings:
        for group_name, group_airports in display_custom_groupings.items():
            group_departing = sum(departure_counts.get(ap_icao, 0) for ap_icao in group_airports)
            group_arriving = sum(arrival_counts.get(ap_icao, 0) for ap_icao in group_airports)
            group_total = group_departing + group_arriving
            
            # Find the earliest ETA among all airports in this grouping
            group_earliest_eta = float('inf')
            group_arrivals_in_flight = sum(arrivals_in_flight.get(ap_icao, 0) for ap_icao in group_airports)
            group_arrivals_on_ground = sum(arrivals_on_ground.get(ap_icao, 0) for ap_icao in group_airports)
            
            for ap_icao in group_airports:
                if ap_icao in earliest_arrival_eta:
                    if earliest_arrival_eta[ap_icao] < group_earliest_eta:
                        group_earliest_eta = earliest_arrival_eta[ap_icao]
            
            group_eta_display = format_eta_display(group_earliest_eta, group_arrivals_in_flight, group_arrivals_on_ground)
            
            if group_total > 0: # Only include groupings with activity
                grouped_data.append((group_name, str(group_total), str(group_departing), str(group_arriving), group_eta_display))
        
        grouped_data.sort(key=lambda x: int(x[1]), reverse=True)
    
    return airport_data, grouped_data, len(flights)

def _calculate_eta(flight, airports_data, aircraft_approach_speeds=None):
    """
    Calculate ETA for a flight and return display strings.
    Uses a two-phase calculation: current groundspeed for most of the journey,
    then approach speed for the final 5 nautical miles.
    """
    if flight['arrival'] in airports_data and flight.get('groundspeed', 0) > 40:
        arrival_airport = airports_data[flight['arrival']]
        distance = haversine_distance_nm(
            flight['latitude'],
            flight['longitude'],
            arrival_airport['latitude'],
            arrival_airport['longitude']
        )
        
        # Default: use current groundspeed for entire distance
        groundspeed = flight['groundspeed']
        eta_hours = distance / groundspeed
        
        # If we have aircraft approach speeds, use more sophisticated calculation
        if aircraft_approach_speeds:
            # Extract aircraft type from flight plan
            aircraft_type = None
            if flight.get('flight_plan') and flight['flight_plan'].get('aircraft_short'):
                aircraft_type = flight['flight_plan']['aircraft_short']
            
            # If we have approach speed for this aircraft type, use two-phase calculation
            if aircraft_type and aircraft_type in aircraft_approach_speeds:
                approach_speed = aircraft_approach_speeds[aircraft_type]
                final_approach_distance = 5.0  # nautical miles
                
                if distance > final_approach_distance:
                    # Two-phase: current speed for most of journey, approach speed for final 5 nm
                    cruise_distance = distance - final_approach_distance
                    cruise_time = cruise_distance / groundspeed
                    approach_time = final_approach_distance / approach_speed
                    eta_hours = cruise_time + approach_time
                else:
                    # Already within final approach distance, use minimum of current speed or approach speed
                    # (aircraft may already be slower than approach speed)
                    effective_speed = min(groundspeed, approach_speed)
                    eta_hours = distance / effective_speed
        
        eta_display = format_eta_display(eta_hours, 1, 0)
        
        current_time_utc = datetime.now(timezone.utc)
        arrival_time_utc = current_time_utc + timedelta(hours=eta_hours)
        arrival_time_local = arrival_time_utc.astimezone()
        eta_local_time = arrival_time_local.strftime("%H:%M")
        
        return eta_display, eta_local_time, eta_hours
    return "----", "----", float('inf')


def get_airport_flight_details(airport_icao_or_list, max_eta_hours=1.0, disambiguator=None):
    """
    Get detailed flight information for a specific airport or list of airports.
    Returns separate lists for departures and arrivals with full details.
    
    Args:
        airport_icao_or_list: Either a single ICAO code (str) or a list of ICAO codes
        max_eta_hours: Maximum ETA in hours for arrival filter
        disambiguator: An AirportDisambiguator instance
    
    Returns:
        (departures_list, arrivals_list) where each is a list of tuples:
        - departures: (callsign, (destination_pretty_name, destination_icao))
        - arrivals: (callsign, (origin_pretty_name, origin_icao), eta_display, eta_local_time)
    """
    # Get the directory where this script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Load airport data
    all_airports_data = load_airport_data(os.path.join(script_dir, 'iata-icao.csv'))
    
    # Normalize input to a list
    if isinstance(airport_icao_or_list, str):
        airport_icao_list = [airport_icao_or_list]
    else:
        airport_icao_list = list(airport_icao_or_list)
    
    # Create airports dict for the specified airports
    airports = {icao: data for icao, data in all_airports_data.items() if icao in airport_icao_list}
    
    # Download VATSIM data
    data = download_vatsim_data()
    if not data:
        return [], []
    
    # Load aircraft approach speeds
    aircraft_approach_speeds = load_aircraft_approach_speeds(os.path.join(script_dir, 'aircraft_data.csv'))
    
    # Filter flights - we need all flights that involve our airports
    flights = filter_flights_by_airports(data, all_airports_data, airport_icao_list)
    
    departures_list = []
    arrivals_list = []
    
    for flight in flights:
        callsign = flight['callsign']
        nearest_airport_if_on_ground = get_nearest_airport_if_on_ground(flight, airports)
        
        # Check if this is a local flight (departure == arrival)
        is_local_flight = (flight['departure'] and flight['arrival'] and
                          flight['departure'] == flight['arrival'])
        
        # Check if this is a departure (on ground at departure airport)
        if flight['departure'] and flight['departure'] in airport_icao_list:
            if nearest_airport_if_on_ground == flight['departure']:
                # Flight is on ground at one of our airports, preparing to depart
                if is_local_flight:
                    # Local flight - show LOCAL for name and ---- for ICAO
                    departures_list.append((callsign, ("LOCAL", "----")))
                else:
                    destination = flight['arrival'] if flight['arrival'] else "----"
                    pretty_destination = disambiguator.get_pretty_name(destination) if disambiguator else destination
                    departures_list.append((callsign, (pretty_destination, destination)))
        # Also handle flights with only arrival filed, on ground at one of our airports
        # But NOT if they're already at the arrival airport (those are arrivals, not departures)
        elif not flight['departure'] and flight['arrival'] and nearest_airport_if_on_ground:
            if nearest_airport_if_on_ground in airport_icao_list and nearest_airport_if_on_ground != flight['arrival']:
                # Flight is on ground at one of our airports (not the arrival) with only arrival in flight plan
                destination = flight['arrival']
                pretty_destination = disambiguator.get_pretty_name(destination) if disambiguator else destination
                departures_list.append((callsign, (pretty_destination, destination)))
        
        # Check if this is an arrival (either on ground at arrival or flying nearby)
        if flight['arrival'] and flight['arrival'] in airport_icao_list:
            # Skip if departure == arrival and aircraft is on ground (already added as departure)
            if is_local_flight and nearest_airport_if_on_ground == flight['arrival']:
                pass  # Already handled as departure above
            elif nearest_airport_if_on_ground == flight['arrival']:
                # Flight is on ground at arrival airport
                if is_local_flight:
                    # Local flight - show LOCAL for name and ---- for ICAO
                    arrivals_list.append((callsign, ("LOCAL", "----"), "LANDED", "----"))
                else:
                    origin = flight['departure'] if flight['departure'] else "----"
                    pretty_origin = disambiguator.get_pretty_name(origin) if disambiguator else origin
                    arrivals_list.append((callsign, (pretty_origin, origin), "LANDED", "----"))
            # For in-flight arrivals, check if it's an arrival first, then calculate ETA
            # is_flight_flying_near_arrival uses max_eta_hours=0 to check ALL arrivals
            elif is_flight_flying_near_arrival(flight, all_airports_data, max_eta_hours=0):
                if is_local_flight:
                    # Local flight in the air - show LOCAL for name and ---- for ICAO
                    eta_display, eta_local_time, _ = _calculate_eta(flight, all_airports_data, aircraft_approach_speeds)
                    # Add to list if it meets the original max_eta_hours criteria
                    if max_eta_hours == 0 or _ <= max_eta_hours:
                        arrivals_list.append((callsign, ("LOCAL", "----"), eta_display, eta_local_time))
                else:
                    origin = flight['departure'] if flight['departure'] else "----"
                    pretty_origin = disambiguator.get_pretty_name(origin) if disambiguator else origin
                    eta_display, eta_local_time, _ = _calculate_eta(flight, all_airports_data, aircraft_approach_speeds)
                    # Add to list if it meets the original max_eta_hours criteria
                    if max_eta_hours == 0 or _ <= max_eta_hours:
                        arrivals_list.append((callsign, (pretty_origin, origin), eta_display, eta_local_time))
            else:
                # Flight has arrival filed but is on ground (not at arrival airport, likely at departure)
                # Show with ETA="----" to indicate they haven't departed yet
                if is_local_flight:
                    # Local flight not yet departed - show LOCAL for name and ---- for ICAO
                    arrivals_list.append((callsign, ("LOCAL", "----"), "----", "----"))
                else:
                    origin = flight['departure'] if flight['departure'] else "----"
                    pretty_origin = disambiguator.get_pretty_name(origin) if disambiguator else origin
                    arrivals_list.append((callsign, (pretty_origin, origin), "----", "----"))
        
        # Handle flights on ground without flight plans
        if not flight['departure'] and not flight['arrival'] and nearest_airport_if_on_ground:
            if nearest_airport_if_on_ground in airport_icao_list:
                # Count as departure with unknown destination
                departures_list.append((callsign, ("----", "----")))
    
    # Sort departures by callsign
    departures_list.sort(key=lambda x: x[0])
    
    # Sort arrivals by ETA (convert eta_display to sortable value)
    def eta_sort_key(arrival):
        eta_str = arrival[2]
        if eta_str == "LANDED":
            return -1  # Already landed flights first
        elif eta_str == "----" or eta_str == "":
            return float('inf')  # Unknown ETA last
        else:
            # Parse ETA strings like "45m", "1h30m", "2h", etc.
            try:
                if 'h' in eta_str and 'm' in eta_str:
                    parts = eta_str.replace('h', ' ').replace('m', '').split()
                    return int(parts[0]) * 60 + int(parts[1])
                elif 'h' in eta_str:
                    return int(eta_str.replace('h', '')) * 60
                elif 'm' in eta_str:
                    return int(eta_str.replace('m', '').replace('<', ''))
                else:
                    return float('inf')
            except (ValueError, IndexError):
                return float('inf')
    
    arrivals_list.sort(key=eta_sort_key)
    
    return departures_list, arrivals_list