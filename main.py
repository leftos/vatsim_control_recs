#!/usr/bin/env python3
"""
VATSIM Control Recommendations - Main Entry Point
Analyzes VATSIM flight data and controller staffing recommendations
"""

import argparse
import os

from backend import analyze_flights_data, load_unified_airport_data
from backend.config import constants as backend_constants
from backend.core.groupings import load_all_groupings
from airport_disambiguator import AirportDisambiguator
from ui import VATSIMControlApp, expand_countries_to_airports
from ui import config as ui_config
from ui import debug_logger  # Import to trigger log cleanup on bootup


def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description="Analyze VATSIM flight data and controller staffing")
    parser.add_argument("--max-eta-hours", type=float, default=1.0,
                        help="Maximum ETA in hours for arrival filter (default: 1.0)")
    parser.add_argument("--refresh-interval", type=int, default=15,
                        help="Auto-refresh interval in seconds (default: 15)")
    parser.add_argument("--airports", nargs="+",
                        help="List of airport ICAO codes to include in analysis (default: all)")
    parser.add_argument("--countries", nargs="+",
                        help="List of country codes (e.g., US DE) to include all airports from those countries")
    parser.add_argument("--groupings", nargs="+",
                        help="List of custom grouping names to include in analysis (default: all)")
    parser.add_argument("--supergroupings", nargs="+",
                        help="List of custom grouping names to use as supergroupings. This will include all airports in these supergroupings and any detected sub-groupings.")
    parser.add_argument("--include-all-staffed", action="store_true",
                        help="Include airports with zero planes if they are staffed (default: False)")
    parser.add_argument("--disable-animations", action="store_true",
                        help="Disable split-flap animations for instant text updates (default: False)")
    parser.add_argument("--progressive-load", action="store_true",
                        help="Enable progressive loading for faster perceived startup (default: auto for 50+ airports)")
    parser.add_argument("--progressive-chunk-size", type=int, default=20,
                        help="Number of rows to load per chunk in progressive mode (default: 20)")
    parser.add_argument("--wind-source", choices=["metar", "minute"], default="metar",
                        help="Wind data source: 'metar' for METAR from aviationweather.gov (default), 'minute' for up-to-the-minute from weather.gov")
    parser.add_argument("--hide-wind", action="store_true",
                        help="Hide the wind column from the main view (default: False)")
    parser.add_argument("--include-all-arriving", action="store_true",
                        help="Include airports with any arrivals filed, regardless of max-eta-hours (default: False)")
    
    # Parse arguments
    args = parser.parse_args()
    
    # Set the global wind source from command-line argument
    backend_constants.WIND_SOURCE = args.wind_source
    
    # Log cleanup happens automatically when debug_logger is imported
    debug_logger.info("Application starting")
    
    print("Loading VATSIM data...")
    
    # Load unified airport data if we need to expand countries, groupings, or supergroupings
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if args.countries or args.groupings or args.supergroupings:
        ui_config.UNIFIED_AIRPORT_DATA = load_unified_airport_data(
            apt_base_path=os.path.join(script_dir, 'data', 'APT_BASE.csv'),
            airports_json_path=os.path.join(script_dir, 'data', 'airports.json'),
            iata_icao_path=os.path.join(script_dir, 'data', 'iata-icao.csv')
        )
        ui_config.DISAMBIGUATOR = AirportDisambiguator(
            os.path.join(script_dir, 'data', 'airports.json'),
            unified_data=ui_config.UNIFIED_AIRPORT_DATA
        )
    
    # Start with explicitly provided airports
    airport_allowlist = args.airports or []
    
    # Expand country codes to airport ICAO codes
    if args.countries and ui_config.UNIFIED_AIRPORT_DATA:
        country_airports = expand_countries_to_airports(args.countries, ui_config.UNIFIED_AIRPORT_DATA)
        print(f"Expanded {len(args.countries)} country code(s) to {len(country_airports)} airport(s)")
        airport_allowlist = list(set(airport_allowlist + country_airports))
    
    # Expand groupings and supergroupings to airport ICAO codes at bootup
    if (args.groupings or args.supergroupings) and ui_config.UNIFIED_AIRPORT_DATA:
        all_groupings = load_all_groupings(
            os.path.join(script_dir, 'data', 'custom_groupings.json'),
            ui_config.UNIFIED_AIRPORT_DATA
        )
        
        def resolve_grouping_recursively(grouping_name, visited=None):
            """
            Recursively resolve a grouping name to its individual airports.
            Handles nested groupings by looking up grouping names and resolving them.
            
            Args:
                grouping_name: Name of the grouping to resolve
                visited: Set of already-visited grouping names to prevent infinite loops
            
            Returns:
                Set of airport ICAO codes
            """
            if visited is None:
                visited = set()
            
            # Prevent infinite loops
            if grouping_name in visited:
                return set()
            visited.add(grouping_name)
            
            if grouping_name not in all_groupings:
                return set()
            
            airports = set()
            items = all_groupings[grouping_name]
            
            for item in items:
                # Check if this item is itself a grouping name
                if item in all_groupings:
                    # Recursively resolve the nested grouping
                    airports.update(resolve_grouping_recursively(item, visited))
                else:
                    # It's an airport code, add it directly
                    airports.add(item)
            
            return airports
        
        grouping_airports = set()
        
        # Handle supergroupings (includes sub-groupings)
        if args.supergroupings:
            for supergroup_name in args.supergroupings:
                if supergroup_name in all_groupings:
                    # Recursively resolve the supergrouping to all airports
                    resolved_airports = resolve_grouping_recursively(supergroup_name)
                    grouping_airports.update(resolved_airports)
                else:
                    print(f"Warning: Supergrouping '{supergroup_name}' not found in custom_groupings.json")
        
        # Handle regular groupings
        if args.groupings:
            for group_name in args.groupings:
                if group_name in all_groupings:
                    grouping_airports.update(all_groupings[group_name])
                else:
                    print(f"Warning: Grouping '{group_name}' not found in custom_groupings.json")
        
        if grouping_airports:
            # Filter out airports without valid coordinates
            valid_airports = [ap for ap in grouping_airports if ap in ui_config.UNIFIED_AIRPORT_DATA and
                            ui_config.UNIFIED_AIRPORT_DATA[ap].get('latitude') is not None and
                            ui_config.UNIFIED_AIRPORT_DATA[ap].get('longitude') is not None]
            print(f"Expanded groupings/supergroupings to {len(valid_airports)} airport(s) (filtered from {len(grouping_airports)})")
            airport_allowlist = list(set(airport_allowlist + valid_airports))
    
    # Get the data (groupings/supergroupings already expanded to airport_allowlist)
    airport_data, groupings_data, total_flights, ui_config.UNIFIED_AIRPORT_DATA, ui_config.DISAMBIGUATOR = analyze_flights_data(
        max_eta_hours=args.max_eta_hours,
        airport_allowlist=airport_allowlist if airport_allowlist else None,
        groupings_allowlist=args.groupings,  # Still used for display purposes only
        supergroupings_allowlist=args.supergroupings,  # Still used for display purposes only
        include_all_staffed=args.include_all_staffed,
        hide_wind=args.hide_wind,
        include_all_arriving=args.include_all_arriving,
        unified_airport_data=ui_config.UNIFIED_AIRPORT_DATA,
        disambiguator=ui_config.DISAMBIGUATOR
    )
    
    if airport_data is None:
        print("Failed to download VATSIM data")
        return
    
    # Run the Textual app
    app = VATSIMControlApp(airport_data, groupings_data, total_flights or 0, args, airport_allowlist if airport_allowlist else None)
    app.run()


if __name__ == "__main__":
    main()