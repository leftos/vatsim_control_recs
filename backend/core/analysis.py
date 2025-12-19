"""
Main analysis module for VATSIM flights and controller staffing.
"""

import os
from collections import defaultdict
from typing import Dict, Any, List, Optional, Tuple

from backend.cache.manager import load_aircraft_approach_speeds
from backend.data.loaders import load_unified_airport_data
from backend.data.vatsim_api import download_vatsim_data, filter_flights_by_airports
from backend.data.weather import get_wind_info_batch, get_altimeter_setting
from backend.core.controllers import get_staffed_positions
from backend.core.calculations import format_eta_display, calculate_eta
from backend.core.groupings import load_all_groupings, resolve_grouping_recursively
from backend.core.flights import get_nearest_airport_if_on_ground, is_flight_flying_near_arrival
from backend.core.models import AirportStats, GroupingStats
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
    hide_wind: bool = False,
    include_all_arriving: bool = False,
    unified_airport_data: Optional[Dict[str, Dict[str, Any]]] = None,
    disambiguator: Optional[AirportDisambiguator] = None
) -> Tuple[Optional[List[AirportStats]], Optional[List[GroupingStats]], int, Dict[str, Dict[str, Any]], Optional[AirportDisambiguator]]:
    """
    Main function to analyze VATSIM flights and controller staffing - returns data structures.
    
    Args:
        max_eta_hours: Maximum ETA in hours for arrival filter (default: 1.0)
        airport_allowlist: Optional list of airport ICAOs to include (already expanded from groupings/supergroupings)
        groupings_allowlist: Optional list of custom grouping names to display (for display purposes only)
        supergroupings_allowlist: Optional list of supergrouping names to display (for display purposes only)
        include_all_staffed: Whether to include airports with zero planes if staffed (default: True)
        hide_wind: Whether to hide the wind column from the main view (default: False)
        include_all_arriving: Whether to include airports with any arrivals filed, regardless of max_eta_hours (default: False)
        unified_airport_data: Optional pre-loaded unified airport data
        disambiguator: Optional pre-created disambiguator instance
    
    Returns:
        Tuple of (airport_data, grouped_data, total_flights, unified_airport_data, disambiguator):
        - airport_data: List of AirportStats objects with airport statistics
        - grouped_data: List of GroupingStats objects with grouping statistics
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
    
    # Load all custom groupings and ARTCC groupings for display purposes
    all_custom_groupings = load_all_groupings(
        os.path.join(_script_dir, 'data','custom_groupings.json'),
        unified_airport_data
    )
    
    # Determine which groupings to display (for the groupings tab)
    # Note: The airport_allowlist already contains all airports from groupings/supergroupings
    display_custom_groupings = {}

    if all_custom_groupings:
        if supergroupings_allowlist:
            # Display supergroupings and their sub-groupings
            included_group_names = set()
            resolved_supergroup_airports = set()

            # Cache for resolved groupings to avoid redundant resolution
            resolved_cache: dict = {}

            def get_resolved(name: str) -> set:
                if name not in resolved_cache:
                    resolved_cache[name] = resolve_grouping_recursively(name, all_custom_groupings)
                return resolved_cache[name]

            for supergroup_name in supergroupings_allowlist:
                if supergroup_name in all_custom_groupings:
                    included_group_names.add(supergroup_name)
                    # Recursively resolve the supergrouping to all airports (using cache)
                    supergroup_airports = get_resolved(supergroup_name)
                    resolved_supergroup_airports.update(supergroup_airports)
                else:
                    print(f"Warning: Supergrouping '{supergroup_name}' not found in custom_groupings.json.")

            # Find all sub-groupings that are subsets of the resolved supergrouping airports
            for other_group_name in all_custom_groupings:
                if other_group_name not in included_group_names:
                    # Resolve this grouping using cache
                    resolved_other_airports = get_resolved(other_group_name)
                    if resolved_other_airports and resolved_other_airports.issubset(resolved_supergroup_airports):
                        included_group_names.add(other_group_name)

            # Populate display groupings
            for name in included_group_names:
                display_custom_groupings[name] = all_custom_groupings[name]

        elif groupings_allowlist:
            # Display only the specified groupings
            for group_name in groupings_allowlist:
                if group_name in all_custom_groupings:
                    display_custom_groupings[group_name] = all_custom_groupings[group_name]
                else:
                    print(f"Warning: Custom grouping '{group_name}' not found in custom_groupings.json.")
        else:
            # If no groupings_allowlist and no supergrouping, display all groupings
            display_custom_groupings = all_custom_groupings
    else:
        display_custom_groupings = {}

    # Filter airports based on allowlist - simple and clean
    if airport_allowlist:
        airports = {icao: data for icao, data in all_airports_data.items() if icao in airport_allowlist}
    else:
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
        # Use .get() for defensive access to flight dictionary fields
        flight_arrival = flight.get('arrival')
        flight_departure = flight.get('departure')

        # First, calculate the true earliest ETA for all in-flight arrivals
        if flight_arrival and flight_arrival in airports and flight.get('groundspeed', 0) > 40:
            _, _, eta_hours = calculate_eta(flight, airports, aircraft_approach_speeds)
            if eta_hours < earliest_arrival_eta[flight_arrival]:
                earliest_arrival_eta[flight_arrival] = eta_hours

        nearest_airport_if_on_ground = get_nearest_airport_if_on_ground(flight, airports)
        if flight_departure and nearest_airport_if_on_ground == flight_departure:
            # Count as departure if on ground at departure airport
            departure_counts[flight_departure] += 1
        elif flight_arrival and nearest_airport_if_on_ground == flight_arrival:
            # Count as arrival if on ground at arrival airport
            arrival_counts[flight_arrival] += 1
            arrival_counts_all[flight_arrival] += 1
            arrivals_on_ground[flight_arrival] += 1
        elif not flight_departure and not flight_arrival and nearest_airport_if_on_ground:
            # For flights on ground without flight plans, count them as a departure at the nearest airport
            departure_counts[nearest_airport_if_on_ground] += 1
        elif is_flight_flying_near_arrival(flight, airports, max_eta_hours):
            # Count as arrival if within the specified ETA hours of arrival airport
            arrival_counts[flight_arrival] += 1
            arrival_counts_all[flight_arrival] += 1
            arrivals_in_flight[flight_arrival] += 1
        elif flight_arrival and flight_arrival in airports:
            # Flight has arrival filed but isn't on ground at arrival and isn't flying nearby
            # This catches flights on ground at departure that haven't departed yet, or in-flight beyond max_eta_hours
            arrival_counts_all[flight_arrival] += 1

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
        
        # Include airport if it has flights, or if it's staffed and we want to include staffed zero-plane airports,
        # or if it has any arrivals and include_all_arriving is enabled
        # Note: "N/A" doesn't count as staffing (it means the airport has no tower)
        if (total_flights > 0 or
            (staffed_pos_display and staffed_pos_display != "N/A" and include_all_staffed) or
            (arriving_all > 0 and include_all_arriving)):
            airports_to_display.append({
                'icao': airport,
                'departing': departing,
                'arriving': arriving,
                'arriving_all': arriving_all,
                'total_flights': total_flights,
                'eta_display': eta_display,
                'staffed_pos_display': staffed_pos_display
            })
    
    # Batch fetch wind information only for airports that will be displayed (parallelized)
    # Skip fetching wind if hide_wind is enabled
    airports_to_fetch = [apt['icao'] for apt in airports_to_display]
    if airports_to_fetch and not hide_wind:
        print(f"Fetching weather data for {len(airports_to_fetch)} active airports...")
    wind_info_batch = get_wind_info_batch(airports_to_fetch, source=WIND_SOURCE) if (airports_to_fetch and not hide_wind) else {}
    
    # Batch fetch altimeter settings for all airports that will be displayed
    altimeter_batch = {}
    if airports_to_fetch:
        print(f"Fetching altimeter settings for {len(airports_to_fetch)} active airports...")
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_icao = {executor.submit(get_altimeter_setting, icao): icao for icao in airports_to_fetch}
            for future in as_completed(future_to_icao):
                icao = future_to_icao[future]
                try:
                    result = future.result()
                    # Use raw format (A2992 or Q1013)
                    altimeter_batch[icao] = result if result else ""
                except Exception:
                    altimeter_batch[icao] = ""
    
    if airports_to_fetch:
        print(f"Processing airport names for {len(airports_to_fetch)} airports...")
    # Batch fetch full names (no length limit) for airports that will be displayed (processes locations efficiently)
    pretty_names_batch = disambiguator.get_full_names_batch(airports_to_fetch) if disambiguator and airports_to_fetch else {}
    
    # Second pass: build airport_data with fetched information
    airport_data = []
    for apt_dict in airports_to_display:
        airport = apt_dict['icao']
        departing = apt_dict['departing']
        arriving = apt_dict['arriving']
        arriving_all = apt_dict['arriving_all']
        total_flights = apt_dict['total_flights']
        eta_display = apt_dict['eta_display']
        staffed_pos_display = apt_dict['staffed_pos_display']
        
        # Get the pretty name from batch results (with fallback to ICAO)
        pretty_name = pretty_names_batch.get(airport) or airport
        # Get wind information from batch results (only if not hidden)
        wind_info = wind_info_batch.get(airport) or "" if not hide_wind else ""
        # Get altimeter from batch results
        altimeter_info = altimeter_batch.get(airport) or ""
        
        # Create AirportStats object
        stats = AirportStats(
            icao=airport,
            name=pretty_name,
            wind=wind_info,
            altimeter=altimeter_info,
            total=total_flights,
            departures=departing,
            arrivals=arriving,
            arrivals_all=arriving_all,
            next_eta=eta_display,
            staffed=staffed_pos_display
        )
        airport_data.append(stats)
    
    # Sort by total count descending, with arrivals (independent of ETA) as tie-breaker, then alphabetically by ICAO
    airport_data.sort(key=lambda x: (-x.total, -x.arrivals_all, x.icao))
    
    # Process custom groupings data
    grouped_data = []
    if display_custom_groupings:
        for group_name, _group_airports in display_custom_groupings.items():
            # Resolve the grouping to actual airports (handles nested groupings)
            resolved_airports = resolve_grouping_recursively(group_name, all_custom_groupings)
            
            group_departing = sum(departure_counts.get(ap_icao, 0) for ap_icao in resolved_airports)
            group_arriving = sum(arrival_counts.get(ap_icao, 0) for ap_icao in resolved_airports)
            group_arriving_all = sum(arrival_counts_all.get(ap_icao, 0) for ap_icao in resolved_airports)
            group_total = group_departing + group_arriving
            
            # Find the earliest ETA among all airports in this grouping
            group_earliest_eta = float('inf')
            group_arrivals_in_flight = sum(arrivals_in_flight.get(ap_icao, 0) for ap_icao in resolved_airports)
            group_arrivals_on_ground = sum(arrivals_on_ground.get(ap_icao, 0) for ap_icao in resolved_airports)
            
            for ap_icao in resolved_airports:
                if ap_icao in earliest_arrival_eta:
                    if earliest_arrival_eta[ap_icao] < group_earliest_eta:
                        group_earliest_eta = earliest_arrival_eta[ap_icao]
            
            group_eta_display = format_eta_display(group_earliest_eta, group_arrivals_in_flight, group_arrivals_on_ground)
            
            # Collect staffed airports in this grouping (exclude airports with only ATIS)
            staffed_airports = [ap_icao for ap_icao in resolved_airports if ap_icao in staffed_positions and any(pos != "ATIS" for pos in staffed_positions[ap_icao])]
            staffed_display = ", ".join(staffed_airports) if staffed_airports else ""
            
            # Include groupings with activity, or with any arrivals when include_all_arriving is set
            if (group_total > 0 or
                (group_arriving_all > 0 and include_all_arriving)):
                stats = GroupingStats(
                    name=group_name,
                    total=group_total,
                    departures=group_departing,
                    arrivals=group_arriving,
                    arrivals_all=group_arriving_all,
                    next_eta=group_eta_display,
                    staffed=staffed_display
                )
                grouped_data.append(stats)
        
        grouped_data.sort(key=lambda x: x.total, reverse=True)
    
    return airport_data, grouped_data, len(flights), unified_airport_data, disambiguator