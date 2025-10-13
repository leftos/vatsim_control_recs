import requests
import json
import csv
import math
import argparse
import time
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
    """Main function to analyze VATSIM flights and controller staffing, handling static data loading."""
    # Load airport data
    airports = load_airport_data('iata-icao.csv')
    all_custom_groupings = load_custom_groupings('custom_groupings.json')
    return _analyze_flights_logic(max_eta_hours, airport_allowlist, groupings_allowlist, supergrouping_names, airports, all_custom_groupings)

def _analyze_flights_logic(max_eta_hours, airport_allowlist, groupings_allowlist, supergrouping_names, all_airports_data, all_custom_groupings, buffer_mode=False):
    """Encapsulates the core logic for analyzing VATSIM flights and controller staffing."""
    
    if not buffer_mode:
        print("Loading data for analysis...")
    
    # For buffer mode, we'll collect all output and return it
    output_buffer = []
    
    def add_to_output(text=""):
        if buffer_mode:
            output_buffer.append(text)
        else:
            print(text)

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
                
            add_to_output(f"Selected {len(included_group_names)} groupings based on supergrouping(s).")

        elif groupings_allowlist:
            # Existing logic for groupings_allowlist
            for group_name in groupings_allowlist:
                if group_name in all_custom_groupings:
                    display_custom_groupings[group_name] = all_custom_groupings[group_name]
                    active_groupings_for_filter[group_name] = all_custom_groupings[group_name]
                else:
                    print(f"Warning: Custom grouping '{group_name}' not found in custom_groupings.json.")
            add_to_output(f"Selected {len(active_groupings_for_filter)} custom groupings for analysis and display.")
        else:
            # If no groupings_allowlist and no supergrouping, display all groupings
            display_custom_groupings = all_custom_groupings
            add_to_output(f"Loaded {len(all_custom_groupings)} custom groupings for display (not used as allowlist unless specified).")

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
        add_to_output("No custom groupings loaded from custom_groupings.json.")
        display_custom_groupings = {}
        active_groupings_for_filter = {}


    if airport_allowlist: # If there's an explicit airport_allowlist (from --airports or active groupings)
        airports = {icao: data for icao, data in all_airports_data.items() if icao in airport_allowlist}
        add_to_output(f"Filtering flights based on {len(airports)} unique airports from allowlist.")
    else: # If no explicit airport_allowlist, use all airports
        airports = all_airports_data
        add_to_output(f"Analyzing all {len(airports)} airports globally.")
    
    # Download VATSIM data
    add_to_output("Downloading VATSIM data...")
    data = download_vatsim_data()
    if not data:
        add_to_output("Failed to download VATSIM data")
        return output_buffer if buffer_mode else None
    
    # Extract staffed positions
    staffed_positions = get_staffed_positions(data, all_airports_data)

    # Filter flights
    add_to_output("Filtering flights...")
    flights = filter_flights_by_airports(data, airports, airport_allowlist)
    add_to_output(f"Found {len(flights)} flights with valid departure/arrival airports")
    
    # Count flights on ground at departure and near arrival
    departure_counts = defaultdict(int)
    arrival_counts = defaultdict(int)
    
    add_to_output("Analyzing flights...")
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
        if total_flights > 0 or staffed_pos_display: # Only include if there's flight activity or staffing
            airport_data.append((airport, total_flights, departing, arriving, staffed_pos_display))
    
    # Sort by total count descending
    airport_data.sort(key=lambda x: x[1], reverse=True)
    
    # Print table header for individual airports
    add_to_output("\nIndividual Airport Summary:")
    add_to_output("{:<8} {:<10} {:<10} {:<10} {:<20}".format("ICAO", "TOTAL", "DEPARTING", "ARRIVING", "STAFFED POSITIONS"))
    add_to_output("-" * 65)
    
    # Print table rows for individual airports
    for icao, total_flights, departing, arriving, staffed_pos in airport_data:
        add_to_output(f"{icao:<8} {total_flights:<10} {departing:<10} {arriving:<10} {staffed_pos:<20}")

    # Print table for custom groupings
    if display_custom_groupings: # Use display_custom_groupings here
        add_to_output("\nCustom Groupings Summary:")
        add_to_output("{:<20} {:<6} {:<12} {:<12}".format("GROUPING", "TOTAL", "DEPARTING", "ARRIVING"))
        add_to_output("-" * 50)
        
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
                add_to_output(f"{group_name:<20} {group_total:<6} {group_departing:<12} {group_arriving:<12}")
        else:
            add_to_output("No custom groupings with flight activity to display.")
    
    return output_buffer if buffer_mode else None

def clear_terminal():
    """Clear terminal screen"""
    os.system('cls' if os.name == 'nt' else 'clear')

def move_cursor_to_top():
    """Move cursor to top of terminal without clearing"""
    print('\033[H', end='')

def clear_from_cursor():
    """Clear from cursor to end of screen"""
    print('\033[J', end='')

def build_display_buffer(max_eta_hours, airport_allowlist, groupings_allowlist, supergrouping_names, all_airports_data, all_custom_groupings, update_time=None, refresh_interval=5):
    """Build the complete display output in a buffer for smooth updates"""
    from datetime import datetime
    
    buffer = []
    
    # Header with timestamp and refresh info
    if update_time:
        buffer.append(f"--- Live VATSIM Monitoring (Last updated: {update_time.strftime('%H:%M:%S')}) ---")
        buffer.append(f"Refreshing every {refresh_interval} seconds... Press Ctrl+C to exit")
    else:
        buffer.append("--- VATSIM Flight Analysis ---")
    buffer.append("")
    
    # Get the analysis output
    analysis_output = _analyze_flights_logic(
        max_eta_hours=max_eta_hours,
        airport_allowlist=airport_allowlist,
        groupings_allowlist=groupings_allowlist,
        supergrouping_names=supergrouping_names,
        all_airports_data=all_airports_data,
        all_custom_groupings=all_custom_groupings,
        buffer_mode=True
    )
    
    if analysis_output:
        buffer.extend(analysis_output)
    
    return buffer

def display_buffer_smoothly(buffer):
    """Display the buffer content smoothly without jarring screen clears"""
    # Move cursor to top and clear from there
    move_cursor_to_top()
    clear_from_cursor()
    
    # Print all content at once
    for line in buffer:
        print(line)
    
    # Ensure we flush the output
    import sys
    sys.stdout.flush()

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
    parser.add_argument("--live", action="store_true",
                        help="Run in live monitoring mode, updating display every few seconds.")
    parser.add_argument("--interval", type=int, default=5,
                        help="Refresh interval in seconds for live monitoring mode (default: 5).")
    
    # Parse arguments
    args = parser.parse_args()

    if args.live:
        from datetime import datetime
        airports_data = load_airport_data('iata-icao.csv')
        custom_groupings = load_custom_groupings('custom_groupings.json')
        
        # Clear screen once at the start
        clear_terminal()
        
        try:
            while True:
                current_time = datetime.now()
                
                # Build the complete display buffer
                display_buffer = build_display_buffer(
                    max_eta_hours=args.max_eta_hours,
                    airport_allowlist=args.airports,
                    groupings_allowlist=args.groupings,
                    supergrouping_names=args.supergroupings,
                    all_airports_data=airports_data,
                    all_custom_groupings=custom_groupings,
                    update_time=current_time,
                    refresh_interval=args.interval
                )
                
                # Display the buffer smoothly
                display_buffer_smoothly(display_buffer)
                
                time.sleep(args.interval)
                
        except KeyboardInterrupt:
            print("\n\nLive monitoring stopped by user.")
            print("Thank you for using VATSIM Control Recommendations!")
    else:
        # Run analysis with provided arguments
        analyze_flights(max_eta_hours=args.max_eta_hours, airport_allowlist=args.airports,
                        groupings_allowlist=args.groupings, supergrouping_names=args.supergroupings)