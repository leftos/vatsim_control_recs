"""
Main analysis module for VATSIM flights and controller staffing.
"""

import os
from collections import defaultdict
from typing import Dict, Any, List, Optional, Tuple

from backend.cache.manager import load_aircraft_approach_speeds
from backend.data.loaders import load_unified_airport_data
from backend.data.vatsim_api import download_vatsim_data, filter_flights_by_airports
from backend.data.weather import get_wind_info, get_wind_info_batch
from backend.core.controllers import get_staffed_positions
from backend.core.calculations import format_eta_display, calculate_eta
from backend.core.groupings import load_all_groupings
from backend.core.flights import get_nearest_airport_if_on_ground, is_flight_flying_near_arrival
from backend.config.constants import WIND_SOURCE
from airport_disambiguator import AirportDisambiguator


# Module-level variables for data that needs to be accessible
_script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def load_airport_data(unified_data: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Convert unified airport data to the format expected by the rest of the application.
    
    Args:
        unified_data: Unified airport data dictionary
    
    Returns:
        Dictionary mapping ICAO codes to coordinate/country data
    """
    airports = {}
    for code, info in unified_data.items():
        if info.get('latitude') is not None and info.get('longitude') is not None:
            airports[code] = {
                'latitude': info['latitude'],
                'longitude': info['longitude'],
                'country_code': info.get('country', '')
            }
    return airports


def analyze_flights_data(
    max_eta_hours: float = 1.0,
    airport_allowlist: Optional[List[str]] = None,
    groupings_allowlist: Optional[List[str]] = None,
    supergroupings_allowlist: Optional[List[str]] = None,
    include_all_staffed: bool = True,
    unified_airport_data: Optional[Dict[str, Dict[str, Any]]] = None,
    disambiguator: Optional[AirportDisambiguator] = None
) -> Tuple[Optional[List[Tuple]], Optional[List[Tuple]], int, Dict[str, Dict[str, Any]], Optional[AirportDisambiguator]]:
    """
    Main function to analyze VATSIM flights and controller staffing - returns data structures.
    
    Args:
        max_eta_hours: Maximum ETA in hours for arrival filter (default: 1.0)
        airport_allowlist: Optional list of airport ICAOs to include
        groupings_allowlist: Optional list of custom grouping names to include
        supergroupings_allowlist: Optional list of supergrouping names to include
        include_all_staffed: Whether to include airports with zero planes if staffed (default: True)
        unified_airport_data: Optional pre-loaded unified airport data
        disambiguator: Optional pre-created disambiguator instance
    
    Returns:
        Tuple of (airport_data, grouped_data, total_flights, unified_airport_data, disambiguator):
        - airport_data: List of tuples with airport statistics
        - grouped_data: List of tuples with grouping statistics
        - total_flights: Total number of flights analyzed
        - unified_airport_data: The unified airport data (for reuse by caller)
        - disambiguator: The disambiguator instance (for reuse by caller)
    """
    # Load unified airport data if not provided
    if unified_airport_data is None:
        print("Loading airport database...")
        unified_airport_data = load_unified_airport_data(
            apt_base_path=os.path.join(_script_dir, 'data', 'APT_BASE.csv'),
            airports_json_path=os.path.join(_script_dir, 'data', 'airports.json'),
            iata_icao_path=os.path.join(_script_dir, 'data', 'iata-icao.csv')
        )
        if unified_airport_data is None:
            return None, None, 0, {}, None
    
    # Create disambiguator if not provided
    if disambiguator is None:
        disambiguator = AirportDisambiguator(
            os.path.join(_script_dir, 'data', 'airports.json'),
            unified_data=unified_airport_data
        )
    
    all_airports_data = load_airport_data(unified_airport_data)
    
    # Load all custom groupings and ARTCC groupings
    all_custom_groupings = load_all_groupings(
        os.path.join(_script_dir, 'data','custom_groupings.json'),
        unified_airport_data
    )
    
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
            
        airport_allowlist = list(final_airport_allowlist)  # Convert back to list

    else:
        display_custom_groupings = {}
        active_groupings_for_filter = {}

    if airport_allowlist:  # If there's an explicit airport_allowlist (from --airports or active groupings)
        airports = {icao: data for icao, data in all_airports_data.items() if icao in airport_allowlist}
    else:  # If no explicit airport_allowlist, use all airports
        airports = all_airports_data
    
    # Download VATSIM data
    print("Downloading live flight data...")
    data = download_vatsim_data()
    if not data:
        return None, None, 0, unified_airport_data, disambiguator
    
    # Extract staffed positions
    print("Analyzing controller positions...")
    staffed_positions = get_staffed_positions(data, all_airports_data)

    # Load aircraft approach speeds
    aircraft_approach_speeds = load_aircraft_approach_speeds(os.path.join(_script_dir, 'data', 'aircraft_data.csv'))

    # Filter flights
    print(f"Processing flights for {len(airports)} airports...")
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
            _, _, eta_hours = calculate_eta(flight, airports, aircraft_approach_speeds)
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

    # First pass: determine which airports will be displayed
    # (those with flights or that are staffed when include_all_staffed is True)
    airports_to_display = []
    for airport in airports:
        departing = departure_counts.get(airport, 0)
        arriving = arrival_counts.get(airport, 0)
        arriving_all = arrival_counts_all.get(airport, 0)
        
        current_staffed_positions = staffed_positions.get(airport, [])
        staffed_pos_display = ""
        
        # Check if airport has no tower (NON-ATCT)
        airport_info = unified_airport_data.get(airport, {})
        tower_type = airport_info.get('tower_type', '')
        
        if tower_type == 'NON-ATCT':
            # For non-towered airports, show "N/A" instead of staffed positions
            staffed_pos_display = "N/A"
        elif "ATIS" in current_staffed_positions and len(current_staffed_positions) == 1:
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
        # Note: "N/A" doesn't count as staffing (it means the airport has no tower)
        if total_flights > 0 or (staffed_pos_display and staffed_pos_display != "N/A" and include_all_staffed):
            airports_to_display.append((
                airport, departing, arriving, arriving_all,
                total_flights, eta_display, staffed_pos_display
            ))
    
    # Batch fetch wind information only for airports that will be displayed (parallelized)
    airports_to_fetch = [apt[0] for apt in airports_to_display]
    if airports_to_fetch:
        print(f"Fetching weather data for {len(airports_to_fetch)} active airports...")
    wind_info_batch = get_wind_info_batch(airports_to_fetch, source=WIND_SOURCE) if airports_to_fetch else {}
    
    if airports_to_fetch:
        print(f"Processing airport names for {len(airports_to_fetch)} airports...")
    # Batch fetch pretty names only for airports that will be displayed (processes locations efficiently)
    pretty_names_batch = disambiguator.get_pretty_names_batch(airports_to_fetch) if disambiguator and airports_to_fetch else {}
    
    # Second pass: build airport_data with fetched information
    airport_data = []
    for airport, departing, arriving, arriving_all, total_flights, eta_display, staffed_pos_display in airports_to_display:
        # Get the pretty name from batch results
        pretty_name = pretty_names_batch.get(airport, airport)
        # Get wind information from batch results
        wind_info = wind_info_batch.get(airport, "")
        
        # Pad numeric columns to consistent width (3 characters, right-aligned)
        dep_str = str(departing).rjust(3)
        arr_str = str(arriving).rjust(3)
        arr_all_str = str(arriving_all).rjust(3)
        # Column order: ICAO, NAME, WIND, TOTAL, DEP, ARR, [ARR(all)], NEXT ETA, STAFFED
        # Include arriving_all in the tuple when max_eta_hours is specified
        if max_eta_hours != 0:
            airport_data.append((airport, pretty_name, wind_info, str(total_flights), dep_str, arr_str, arr_all_str, eta_display, staffed_pos_display))
        else:
            airport_data.append((airport, pretty_name, wind_info, str(total_flights), dep_str, arr_str, eta_display, staffed_pos_display))
    # Sort by total count descending (TOTAL is now at index 3 due to WIND column at index 2)
    airport_data.sort(key=lambda x: int(x[3]), reverse=True)
    
    # Process custom groupings data
    grouped_data = []
    if display_custom_groupings:
        for group_name, group_airports in display_custom_groupings.items():
            group_departing = sum(departure_counts.get(ap_icao, 0) for ap_icao in group_airports)
            group_arriving = sum(arrival_counts.get(ap_icao, 0) for ap_icao in group_airports)
            group_arriving_all = sum(arrival_counts_all.get(ap_icao, 0) for ap_icao in group_airports)
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
            
            # Collect staffed airports in this grouping (exclude airports with only ATIS)
            staffed_airports = [ap_icao for ap_icao in group_airports if ap_icao in staffed_positions and any(pos != "ATIS" for pos in staffed_positions[ap_icao])]
            staffed_display = ", ".join(staffed_airports) if staffed_airports else ""
            
            if group_total > 0:  # Only include groupings with activity
                grouped_data.append((group_name, str(group_total), str(group_departing), str(group_arriving), str(group_arriving_all), group_eta_display, staffed_display))
        
        grouped_data.sort(key=lambda x: int(x[1]), reverse=True)
    
    return airport_data, grouped_data, len(flights), unified_airport_data, disambiguator