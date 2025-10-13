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
                    'longitude': float(row['longitude'])
                }
    return airports

def download_vatsim_data():
    """Download VATSIM data from the API"""
    try:
        response = requests.get(VATSIM_DATA_URL)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Error downloading VATSIM data: {e}")
        return None

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

def analyze_flights(max_eta_hours=1, airport_allowlist=None):
    """Main function to analyze VATSIM flights"""
    # Load airport data
    print("Loading airport data...")
    airports = load_airport_data('iata-icao.csv')
    
    # If an allowlist is provided, filter the airports
    if airport_allowlist:
        airports = {icao: data for icao, data in airports.items() if icao in airport_allowlist}
        print(f"Using {len(airports)} airports from allowlist")
    else:
        print(f"Loaded {len(airports)} airports")
    
    # Download VATSIM data
    print("Downloading VATSIM data...")
    data = download_vatsim_data()
    if not data:
        print("Failed to download VATSIM data")
        return
    
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
    # Get all unique airports
    all_airports = set(departure_counts.keys()) | set(arrival_counts.keys())
    
    # Create a list of airports with their counts
    airport_data = []
    for airport in all_airports:
        departing = departure_counts.get(airport, 0)
        arriving = arrival_counts.get(airport, 0)
        total = departing + arriving
        airport_data.append((airport, total, departing, arriving))
    
    # Sort by total count descending
    airport_data.sort(key=lambda x: x[1], reverse=True)
    
    # Print table header
    print("\n{:<8} {:<6} {:<12} {:<12}".format("ICAO", "TOTAL", "DEPARTING", "ARRIVING"))
    print("-" * 42)
    
    # Print table rows
    for icao, total, departing, arriving in airport_data:
        print(f"{icao:<8} {total:<6} {departing:<12} {arriving:<12}")

if __name__ == "__main__":
    # Set up argument parser
    parser = argparse.ArgumentParser(description="Analyze VATSIM flight data")
    parser.add_argument("--max-eta-hours", type=float, default=1.0,
                        help="Maximum ETA in hours for arrival filter (default: 1.0)")
    parser.add_argument("--airports", nargs="+",
                        help="List of airport ICAO codes to include in analysis (default: all)")
    
    # Parse arguments
    args = parser.parse_args()
    
    # Run analysis with provided arguments
    analyze_flights(max_eta_hours=args.max_eta_hours, airport_allowlist=args.airports)