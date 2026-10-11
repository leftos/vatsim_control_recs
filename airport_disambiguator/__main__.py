"""Command-line interface for airport disambiguator."""

import argparse
import json
import re
import sys

from backend.data.loaders import load_unified_airport_data
from common.logger import setup_file_logging

from .data_manager import AirportDataManager
from .disambiguator import AirportDisambiguator


def verbose_trace_disambiguation(
    icao: str, unified_data: dict, disambiguator: AirportDisambiguator
) -> str:
    """Trace through the disambiguation process step by step with verbose output."""
    print(f"\n{'=' * 80}")
    print(f"VERBOSE TRACE FOR: {icao}")
    print(f"{'=' * 80}")

    if icao not in unified_data:
        print(f"[STEP 1] ICAO {icao} NOT FOUND in unified_data")
        return icao

    _print_unified_data_step(icao, unified_data[icao])

    data_manager = disambiguator.data_manager
    data_manager_airport = _print_data_manager_step(data_manager, icao)
    _print_location_step(data_manager, icao)
    if data_manager_airport:
        _print_name_contains_location_step(data_manager_airport)

    _print_cache_step_before(disambiguator, icao)

    pretty_name = disambiguator.get_pretty_name(icao)
    full_name = disambiguator.get_full_name(icao)
    _print_final_results_step(icao, pretty_name, full_name)

    _print_cache_step_after(disambiguator, icao)

    print(f"{'=' * 80}\n")

    return pretty_name


def _print_unified_data_step(icao: str, airport_info: dict) -> None:
    """Print the unified-data fields recorded for an ICAO."""
    print(f"[STEP 1] Unified data for {icao}:")
    print(f"         name:    {airport_info.get('name')!r}")
    print(f"         city:    {airport_info.get('city')!r}")
    print(f"         state:   {airport_info.get('state')!r}")
    print(f"         country: {airport_info.get('country')!r}")


def _print_data_manager_step(
    data_manager: AirportDataManager, icao: str
) -> dict | None:
    """Print the data manager's record for an ICAO and return it."""
    airport = data_manager.get_airport_details(icao)
    print("\n[STEP 2] Data manager airport details:")
    if airport:
        print(f"         name:  {airport.get('name')!r}")
        print(f"         city:  {airport.get('city')!r}")
        print(f"         state: {airport.get('state')!r}")
    else:
        print("         NOT FOUND in data_manager")
    return airport


def _print_location_step(data_manager: AirportDataManager, icao: str) -> None:
    """Print the base location and the airports sharing it."""
    location = data_manager.get_location_for_airport(icao)
    print(f"\n[STEP 3] Base location for {icao}: {location!r}")
    if location:
        airports_in_location = data_manager.get_airports_in_location(location)
        print(f"\n[STEP 4] Airports in location {location!r}: {airports_in_location}")
        print(f"         Count: {len(airports_in_location)}")


def _print_name_contains_location_step(airport: dict) -> None:
    """Print the name and city words compared by the matching check."""
    airport_name = airport.get("name", "")
    city = airport.get("city", "")
    state = airport.get("state", "")

    print("\n[STEP 5] name_contains_location check:")
    print(f"         airport_name: {airport_name!r}")
    print(f"         city:         {city!r}")
    print(f"         state:        {state!r}")

    name_words = airport_name.split()
    print(f"         name_words:   {name_words}")

    if not city:
        return

    city_words = re.split(r"[\s\-/]+", city.lower())
    print(f"         city_words:   {city_words}")

    match = _first_name_city_match(name_words, city_words)
    if match is None:
        print("         NO MATCH found between name_words and city_words")
    else:
        name_word, city_word = match
        print(f"         MATCH: {name_word!r}.lower() == {city_word!r}")


def _first_name_city_match(
    name_words: list[str], city_words: list[str]
) -> tuple[str, str] | None:
    """Return the first name/city word pair that matches, or None."""
    for city_word in city_words:
        if not city_word:
            continue
        for name_word in name_words:
            if name_word.lower() == city_word:
                return name_word, city_word
    return None


def _print_cache_step_before(disambiguator: AirportDisambiguator, icao: str) -> None:
    """Print the caches and any name already stored for the ICAO."""
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


def _print_final_results_step(icao: str, pretty_name: str, full_name: str) -> None:
    """Print the disambiguator's pretty and full names for the ICAO."""
    print("\n[STEP 7] Final results:")
    print(f"         get_pretty_name({icao}): {pretty_name!r}")
    print(f"         get_full_name({icao}):   {full_name!r}")


def _print_cache_step_after(disambiguator: AirportDisambiguator, icao: str) -> None:
    """Print the cached names after the lookups have run."""
    print("\n[STEP 8] Disambiguation caches (after get_pretty_name):")
    print(
        f"         icao_to_pretty_name[{icao}]: {disambiguator.icao_to_pretty_name.get(icao)!r}"
    )
    print(
        f"         icao_to_full_name[{icao}]:   {disambiguator.icao_to_full_name.get(icao)!r}"
    )


def main():
    """Main entry point for command-line usage."""
    setup_file_logging()

    parser = argparse.ArgumentParser(
        description="Test airport name disambiguation with provided ICAO codes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m airport_disambiguator KMER KBAB KNZY
  python -m airport_disambiguator --verbose KLGB
  python -m airport_disambiguator --apt-base data/raw/APT_BASE.csv --airports data/raw/airports.json --iata-icao data/raw/iata-icao.csv KSFO KLAX
        """,
    )

    parser.add_argument(
        "icao_codes", nargs="+", help="One or more ICAO airport codes to disambiguate"
    )
    parser.add_argument(
        "--apt-base",
        default="data/raw/APT_BASE.csv",
        help="Path to APT_BASE.csv file (default: data/raw/APT_BASE.csv)",
    )
    parser.add_argument(
        "--airports",
        default="data/raw/airports.json",
        help="Path to airports.json file (default: data/raw/airports.json)",
    )
    parser.add_argument(
        "--iata-icao",
        default="data/raw/iata-icao.csv",
        help="Path to iata-icao.csv file (default: data/raw/iata-icao.csv)",
    )
    parser.add_argument(
        "--names-csv",
        default="data/airport_names.csv",
        help="Path to airport_names.csv file (default: data/airport_names.csv)",
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
        disambiguator = AirportDisambiguator(
            args.airports,
            unified_data=unified_data,
            names_csv_path=args.names_csv,
        )

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
