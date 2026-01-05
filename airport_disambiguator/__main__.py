"""Command-line interface for airport disambiguator."""

import argparse
import json
import re
import sys

from backend.data.loaders import load_unified_airport_data
from .disambiguator import AirportDisambiguator


def verbose_trace_disambiguation(
    icao: str, unified_data: dict, disambiguator: AirportDisambiguator
) -> str:
    """Trace through the disambiguation process step by step with verbose output."""
    print(f"\n{'=' * 80}")
    print(f"VERBOSE TRACE FOR: {icao}")
    print(f"{'=' * 80}")

    # Step 1: Check if ICAO exists in unified data
    if icao not in unified_data:
        print(f"[STEP 1] ICAO {icao} NOT FOUND in unified_data")
        return icao

    airport_info = unified_data[icao]
    print(f"[STEP 1] Unified data for {icao}:")
    print(f"         name:    {airport_info.get('name')!r}")
    print(f"         city:    {airport_info.get('city')!r}")
    print(f"         state:   {airport_info.get('state')!r}")
    print(f"         country: {airport_info.get('country')!r}")

    # Step 2: Check what's in the disambiguator's data_manager
    dm = disambiguator.data_manager
    dm_airport = dm.get_airport_details(icao)
    print("\n[STEP 2] Data manager airport details:")
    if dm_airport:
        print(f"         name:  {dm_airport.get('name')!r}")
        print(f"         city:  {dm_airport.get('city')!r}")
        print(f"         state: {dm_airport.get('state')!r}")
    else:
        print("         NOT FOUND in data_manager")

    # Step 3: Get base location
    location = dm.get_location_for_airport(icao)
    print(f"\n[STEP 3] Base location for {icao}: {location!r}")

    # Step 4: Find other airports in the same location
    if location:
        airports_in_location = dm.get_airports_in_location(location)
        print(f"\n[STEP 4] Airports in location {location!r}: {airports_in_location}")
        print(f"         Count: {len(airports_in_location)}")

    # Step 5: Trace name_contains_location logic
    if dm_airport:
        airport_name = dm_airport.get("name", "")
        city = dm_airport.get("city", "")
        state = dm_airport.get("state", "")

        print("\n[STEP 5] name_contains_location check:")
        print(f"         airport_name: {airport_name!r}")
        print(f"         city:         {city!r}")
        print(f"         state:        {state!r}")

        name_words = airport_name.split()
        print(f"         name_words:   {name_words}")

        if city:
            city_words = re.split(r"[\s\-/]+", city.lower())
            print(f"         city_words:   {city_words}")

            # Check for matches
            found_match = False
            for city_word in city_words:
                if not city_word:
                    continue
                for name_word in name_words:
                    name_word_lower = name_word.lower()
                    if name_word_lower == city_word:
                        print(f"         MATCH: {name_word!r}.lower() == {city_word!r}")
                        found_match = True
                        break
                if found_match:
                    break

            if not found_match:
                print("         NO MATCH found between name_words and city_words")

    # Step 6: Check what's in the caches
    print("\n[STEP 6] Disambiguation caches (before get_pretty_name):")
    print(
        f"         icao_to_pretty_name has {icao}: {icao in disambiguator.icao_to_pretty_name}"
    )
    print(
        f"         icao_to_full_name has {icao}:   {icao in disambiguator.icao_to_full_name}"
    )
    if icao in disambiguator.icao_to_pretty_name:
        print(
            f"         cached pretty_name: {disambiguator.icao_to_pretty_name[icao]!r}"
        )
    if icao in disambiguator.icao_to_full_name:
        print(f"         cached full_name:   {disambiguator.icao_to_full_name[icao]!r}")

    # Step 7: Get the actual result
    pretty_name = disambiguator.get_pretty_name(icao)
    full_name = disambiguator.get_full_name(icao)

    print("\n[STEP 7] Final results:")
    print(f"         get_pretty_name({icao}): {pretty_name!r}")
    print(f"         get_full_name({icao}):   {full_name!r}")

    # Step 8: Check caches after
    print("\n[STEP 8] Disambiguation caches (after get_pretty_name):")
    print(
        f"         icao_to_pretty_name[{icao}]: {disambiguator.icao_to_pretty_name.get(icao)!r}"
    )
    print(
        f"         icao_to_full_name[{icao}]:   {disambiguator.icao_to_full_name.get(icao)!r}"
    )

    print(f"{'=' * 80}\n")

    return pretty_name


def main():
    """Main entry point for command-line usage."""
    parser = argparse.ArgumentParser(
        description="Test airport name disambiguation with provided ICAO codes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m airport_disambiguator KMER KBAB KNZY
  python -m airport_disambiguator --verbose KLGB
  python -m airport_disambiguator --apt-base data/APT_BASE.csv --airports data/airports.json --iata-icao data/iata-icao.csv KSFO KLAX
        """,
    )

    parser.add_argument(
        "icao_codes", nargs="+", help="One or more ICAO airport codes to disambiguate"
    )
    parser.add_argument(
        "--apt-base",
        default="data/APT_BASE.csv",
        help="Path to APT_BASE.csv file (default: data/APT_BASE.csv)",
    )
    parser.add_argument(
        "--airports",
        default="data/airports.json",
        help="Path to airports.json file (default: data/airports.json)",
    )
    parser.add_argument(
        "--iata-icao",
        default="data/iata-icao.csv",
        help="Path to iata-icao.csv file (default: data/iata-icao.csv)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose tracing of the disambiguation process",
    )

    args = parser.parse_args()

    try:
        # Load unified airport data from all three sources
        print("Loading airport data from:")
        print(f"  - {args.apt_base}")
        print(f"  - {args.airports}")
        print(f"  - {args.iata_icao}")

        unified_data = load_unified_airport_data(
            args.apt_base, args.airports, args.iata_icao
        )

        if args.verbose:
            print(f"\nLoaded {len(unified_data)} airports from unified data")

        # Create disambiguator with unified data
        disambiguator = AirportDisambiguator(args.airports, unified_data=unified_data)

        if args.verbose:
            print(
                f"Disambiguator data_manager has {len(disambiguator.data_manager.airports_data)} airports"
            )
            print(
                f"Disambiguator has {len(disambiguator.data_manager.location_to_airports)} unique locations"
            )

        print("\nAirport Pretty Names:")
        print("=" * 80)

        for icao in args.icao_codes:
            if args.verbose:
                pretty_name = verbose_trace_disambiguation(
                    icao, unified_data, disambiguator
                )
            else:
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
