import requests
import json
import csv
import math
import argparse
from collections import defaultdict

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

def load_airport_data(filename):
    """Load airport data from CSV file"""
    airports = {}
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

            allowed_positions = {"DEL", "GND", "TWR", "DEP", "APP"}

            if position_suffix in allowed_positions:
                valid_icao = _get_valid_icao_from_callsign(icao_candidate_prefix, airports_data)
                
                if valid_icao:
                    staffed_positions[valid_icao].add(position_suffix)
    
    return {icao: sorted(list(positions)) for icao, positions in staffed_positions.items()}

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

def is_flight_on_ground(flight, airports, max_distance_nm=6, max_groundspeed=40):
    """
    Determine if a flight is on the ground at the departure airport
    Based on distance from airport and groundspeed
    """
    if flight['groundspeed'] > max_groundspeed:
        return False
    
    # For flights with departure airport in flight plan
    if flight['departure'] and flight['departure'] in airports:
        departure_airport = airports.get(flight['departure'])
        if not departure_airport:
            return False
        
        # Calculate distance from departure airport
        distance = haversine_distance_nm(
            flight['latitude'],
            flight['longitude'],
            departure_airport['latitude'],
            departure_airport['longitude']
        )
        
        # Check if within distance threshold and low groundspeed
        if distance <= max_distance_nm:
            return True
    
    # For flights with flight plans but not near departure or arrival
    # or flights without flight plans, check all airports
    if flight['latitude'] is not None and flight['longitude'] is not None:
        for icao, airport_data in airports.items():
            # Calculate distance from airport
            distance = haversine_distance_nm(
                flight['latitude'],
                flight['longitude'],
                airport_data['latitude'],
                airport_data['longitude']
            )
            
            # Check if within distance threshold and low groundspeed
            if distance <= max_distance_nm:
                # Update the flight with the found airport
                flight['departure'] = icao
                return True
    
    return False

def is_flight_near_arrival(flight, airports, max_eta_hours=1):
    """
    Determine if a flight is within an hour of arriving at the arrival airport
    Based on distance and groundspeed
    """
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
            return eta_hours <= max_eta_hours
        else:
            # If groundspeed is 0, we can't calculate ETA
            return False
    
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

def analyze_flights(max_eta_hours=1, airport_allowlist=None, groupings_allowlist=None, supergrouping_names=None):
    """Main function to analyze VATSIM flights and controller staffing"""
    # Load airport data
    print("Loading airport data...")
    all_airports_data = load_airport_data('iata-icao.csv')
    
    # Load all custom groupings
    print("Loading custom groupings...")
    all_custom_groupings = load_custom_groupings('custom_groupings.json')
    
    # Determine which groupings to display and which to use for filtering
    display_custom_groupings = {}
    active_groupings_for_filter = {}
    
    if all_custom_groupings:
        if supergrouping_names:
            supergroup_airports_set = set()
            included_group_names = set()
            
            for supergroup_name in supergrouping_names:
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
                
            print(f"Selected {len(included_group_names)} groupings based on supergrouping(s).")

        elif groupings_allowlist:
            # Existing logic for groupings_allowlist
            for group_name in groupings_allowlist:
                if group_name in all_custom_groupings:
                    display_custom_groupings[group_name] = all_custom_groupings[group_name]
                    active_groupings_for_filter[group_name] = all_custom_groupings[group_name]
                else:
                    print(f"Warning: Custom grouping '{group_name}' not found in custom_groupings.json.")
            print(f"Selected {len(active_groupings_for_filter)} custom groupings for analysis and display.")
        else:
            # If no groupings_allowlist and no supergrouping, display all groupings
            display_custom_groupings = all_custom_groupings
            print(f"Loaded {len(all_custom_groupings)} custom groupings for display (not used as allowlist unless specified).")

        # Prepare main airport_allowlist based on provided --airports and/or active groupings
        final_airport_allowlist = set()
        if airport_allowlist:
            final_airport_allowlist.update(airport_allowlist)
        
        # Add airports from groupings to the filter if --groupings or --supergrouping was explicitly used
        if groupings_allowlist or supergrouping_names:
            for group_name, airports_in_group in active_groupings_for_filter.items():
                final_airport_allowlist.update(airports_in_group)
            
        airport_allowlist = list(final_airport_allowlist) # Convert back to list

    else:
        print("No custom groupings loaded from custom_groupings.json.")
        display_custom_groupings = {}
        active_groupings_for_filter = {}


    if airport_allowlist: # If there's an explicit airport_allowlist (from --airports or active groupings)
        airports = {icao: data for icao, data in all_airports_data.items() if icao in airport_allowlist}
        print(f"Filtering flights based on {len(airports)} unique airports from allowlist.")
    else: # If no explicit airport_allowlist, use all airports
        airports = all_airports_data
        print(f"Analyzing all {len(airports)} airports globally.")
    
    # Download VATSIM data
    print("Downloading VATSIM data...")
    data = download_vatsim_data()
    if not data:
        print("Failed to download VATSIM data")
        return
    
    # Extract staffed positions
    staffed_positions = get_staffed_positions(data, all_airports_data)


    # Filter flights
    print("Filtering flights...")
    flights = filter_flights_by_airports(data, airports, airport_allowlist)
    print(f"Found {len(flights)} flights with valid departure/arrival airports")
    
    # Count flights on ground at departure and near arrival
    departure_counts = defaultdict(int)
    arrival_counts = defaultdict(int)
    
    print("Analyzing flights...")
    for flight in flights:
        # Check if flight is on ground at departure airport
        if is_flight_on_ground(flight, airports):
            departure_counts[flight['departure']] += 1
        # Check if flight is near arrival airport
        elif is_flight_near_arrival(flight, airports, max_eta_hours):
            arrival_counts[flight['arrival']] += 1
    
    # Display results in a combined table
    # Get all unique airports that have flights (departing or arriving)
    all_airports_with_flights = set(departure_counts.keys()) | set(arrival_counts.keys())
    
    # Create a list of airports with their counts and staffed positions
    airport_data = []
    for airport in all_airports_with_flights:
        departing = departure_counts.get(airport, 0)
        arriving = arrival_counts.get(airport, 0)
        staffed_pos = ", ".join(staffed_positions.get(airport, [])) if staffed_positions.get(airport, []) else "N/A"
        total_flights = departing + arriving
        if total_flights > 0 or staffed_pos != "N/A": # Only include if there's flight activity or staffing
            airport_data.append((airport, total_flights, departing, arriving, staffed_pos))
    
    # Sort by total count descending
    airport_data.sort(key=lambda x: x[1], reverse=True)
    
    # Print table header for individual airports
    print("\nIndividual Airport Summary:")
    print("{:<8} {:<10} {:<10} {:<10} {:<20}".format("ICAO", "TOTAL", "DEPARTING", "ARRIVING", "STAFFED POSITIONS"))
    print("-" * 65)
    
    # Print table rows for individual airports
    for icao, total_flights, departing, arriving, staffed_pos in airport_data:
        print(f"{icao:<8} {total_flights:<10} {departing:<10} {arriving:<10} {staffed_pos:<20}")

    # Print table for custom groupings
    if display_custom_groupings: # Use display_custom_groupings here
        print("\nCustom Groupings Summary:")
        print("{:<20} {:<6} {:<12} {:<12}".format("GROUPING", "TOTAL", "DEPARTING", "ARRIVING"))
        print("-" * 50)
        
        grouped_data = []
        for group_name, group_airports in display_custom_groupings.items():
            group_departing = sum(departure_counts.get(ap_icao, 0) for ap_icao in group_airports)
            group_arriving = sum(arrival_counts.get(ap_icao, 0) for ap_icao in group_airports)
            group_total = group_departing + group_arriving
            if group_total > 0: # Only include groupings with activity
                grouped_data.append((group_name, group_total, group_departing, group_arriving))
        
        if grouped_data: # Only print header if there's data to show
            grouped_data.sort(key=lambda x: x[1], reverse=True)
            
            for group_name, group_total, group_departing, group_arriving in grouped_data:
                print(f"{group_name:<20} {group_total:<6} {group_departing:<12} {group_arriving:<12}")
        else:
            print("No custom groupings with flight activity to display.")

if __name__ == "__main__":
    # Set up argument parser
    parser = argparse.ArgumentParser(description="Analyze VATSIM flight data and controller staffing")
    parser.add_argument("--max-eta-hours", type=float, default=1.0,
                        help="Maximum ETA in hours for arrival filter (default: 1.0)")
    parser.add_argument("--airports", nargs="+",
                        help="List of airport ICAO codes to include in analysis (default: all). Custom groupings are always included.")
    parser.add_argument("--groupings", nargs="+",
                        help="List of custom grouping names to include in analysis (default: all custom groupings).")
    parser.add_argument("--supergroupings", nargs="+",
                        help="List of custom grouping names to use as supergroupings. This will include all airports in these supergroupings and any detected sub-groupings.")
    
    # Parse arguments
    args = parser.parse_args()
    
    # Run analysis with provided arguments
    analyze_flights(max_eta_hours=args.max_eta_hours, airport_allowlist=args.airports,
                    groupings_allowlist=args.groupings, supergrouping_names=args.supergroupings)