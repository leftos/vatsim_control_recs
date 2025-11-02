"""Command-line interface for airport disambiguator."""

import argparse
import json
import os
import sys

from .disambiguator import AirportDisambiguator

# Import the airport_data_loader from parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from airport_data_loader import load_unified_airport_data
except ImportError:
    print("Error: airport_data_loader.py not found in parent directory")
    sys.exit(1)


def main():
    """Main entry point for command-line usage."""
    parser = argparse.ArgumentParser(
        description="Test airport name disambiguation with provided ICAO codes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m airport_disambiguator KMER KBAB KNZY
  python -m airport_disambiguator --apt-base APT_BASE.csv --airports airports.json --iata-icao iata-icao.csv KSFO KLAX
        """
    )
    
    parser.add_argument(
        "icao_codes",
        nargs="+",
        help="One or more ICAO airport codes to disambiguate"
    )
    parser.add_argument(
        "--apt-base",
        default="APT_BASE.csv",
        help="Path to APT_BASE.csv file (default: APT_BASE.csv)"
    )
    parser.add_argument(
        "--airports",
        default="airports.json",
        help="Path to airports.json file (default: airports.json)"
    )
    parser.add_argument(
        "--iata-icao",
        default="iata-icao.csv",
        help="Path to iata-icao.csv file (default: iata-icao.csv)"
    )
    
    args = parser.parse_args()
    
    try:
        # Load unified airport data from all three sources
        print(f"Loading airport data from:")
        print(f"  - {args.apt_base}")
        print(f"  - {args.airports}")
        print(f"  - {args.iata_icao}")
        
        unified_data = load_unified_airport_data(
            args.apt_base,
            args.airports,
            args.iata_icao
        )
        
        # Create disambiguator with unified data
        disambiguator = AirportDisambiguator(
            args.airports,
            unified_data=unified_data
        )
        
        print(f"\nAirport Pretty Names:")
        print("=" * 80)
        
        for icao in args.icao_codes:
            pretty_name = disambiguator.get_pretty_name(icao)
            print(f"{icao}: {pretty_name}")
        
        print()
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in file - {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()