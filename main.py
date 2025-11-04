#!/usr/bin/env python3
"""
VATSIM Control Recommendations - Main Entry Point
Analyzes VATSIM flight data and controller staffing recommendations
"""

import argparse
import os

from backend import analyze_flights_data, load_unified_airport_data
from backend.config import constants as backend_constants
from airport_disambiguator import AirportDisambiguator
from ui import VATSIMControlApp, init_debug_log, expand_countries_to_airports
from ui import config as ui_config


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
    
    # Initialize debug log
    init_debug_log()
    
    print("Loading VATSIM data...")
    
    # Expand country codes to airport ICAO codes if --countries is provided
    airport_allowlist = args.airports or []
    if args.countries:
        # Load unified airport data to expand countries
        script_dir = os.path.dirname(os.path.abspath(__file__))
        ui_config.UNIFIED_AIRPORT_DATA = load_unified_airport_data(
            apt_base_path=os.path.join(script_dir, 'data', 'APT_BASE.csv'),
            airports_json_path=os.path.join(script_dir, 'data', 'airports.json'),
            iata_icao_path=os.path.join(script_dir, 'data', 'iata-icao.csv')
        )
        ui_config.DISAMBIGUATOR = AirportDisambiguator(
            os.path.join(script_dir, 'data', 'airports.json'),
            unified_data=ui_config.UNIFIED_AIRPORT_DATA
        )
        country_airports = expand_countries_to_airports(args.countries, ui_config.UNIFIED_AIRPORT_DATA)
        print(f"Expanded {len(args.countries)} country code(s) to {len(country_airports)} airport(s)")
        # Combine with any explicitly provided airports
        airport_allowlist = list(set(airport_allowlist + country_airports))
    
    # Get the data
    airport_data, groupings_data, total_flights, ui_config.UNIFIED_AIRPORT_DATA, ui_config.DISAMBIGUATOR = analyze_flights_data(
        max_eta_hours=args.max_eta_hours,
        airport_allowlist=airport_allowlist if airport_allowlist else None,
        groupings_allowlist=args.groupings,
        supergroupings_allowlist=args.supergroupings,
        include_all_staffed=args.include_all_staffed,
        hide_wind=args.hide_wind,
        include_all_arriving=args.include_all_arriving,
        unified_airport_data=ui_config.UNIFIED_AIRPORT_DATA,
        disambiguator=ui_config.DISAMBIGUATOR,
        airport_blocklist=[]  # Empty at startup, only used for dynamic tracking
    )
    
    if airport_data is None:
        print("Failed to download VATSIM data")
        return
    
    # Run the Textual app
    app = VATSIMControlApp(airport_data, groupings_data, total_flights or 0, args, airport_allowlist if airport_allowlist else None)
    app.run()


if __name__ == "__main__":
    main()