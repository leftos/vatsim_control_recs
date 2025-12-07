"""Command-line interface for airport disambiguator."""

import argparse
import json
import sys

from backend.data.loaders import load_unified_airport_data
from .disambiguator import AirportDisambiguator


def main():
    """Main entry point for command-line usage."""
    parser = argparse.ArgumentParser(
        description="Test airport name disambiguation with provided ICAO codes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m airport_disambiguator KMER KBAB KNZY
  python -m airport_disambiguator --apt-base data/APT_BASE.csv --airports data/airports.json --iata-icao data/iata-icao.csv KSFO KLAX
        """
    )
    
    parser.add_argument(
        "icao_codes",
        nargs="+",
        help="One or more ICAO airport codes to disambiguate"
    )
    parser.add_argument(
        "--apt-base",
        default="data/APT_BASE.csv",
        help="Path to APT_BASE.csv file (default: data/APT_BASE.csv)"
    )
    parser.add_argument(
        "--airports",
        default="data/airports.json",
        help="Path to airports.json file (default: data/airports.json)"
    )
    parser.add_argument(
        "--iata-icao",
        default="data/iata-icao.csv",
        help="Path to iata-icao.csv file (default: data/iata-icao.csv)"
    )
    
    args = parser.parse_args()
    
    try:
        # Load unified airport data from all three sources
        print("Loading airport data from:")
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
        
        print("\nAirport Pretty Names:")
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