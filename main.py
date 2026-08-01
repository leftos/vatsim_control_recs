#!/usr/bin/env python3
"""
VATSIM Control Recommendations - Main Entry Point
Analyzes VATSIM flight data and controller staffing recommendations
"""

import argparse
import importlib
import os
import re
import subprocess
import sys

# Marks a run that was restarted after the bootstrap installed something, so the
# install is attempted at most once per invocation
BOOTSTRAP_RETRY_ENV = "VATSIM_BOOTSTRAP_RETRIED"


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the argument parser used for both help output and startup."""
    parser = argparse.ArgumentParser(
        description="Analyze VATSIM flight data and controller staffing"
    )
    parser.add_argument(
        "--max-eta-hours",
        type=float,
        default=1.0,
        help="Maximum ETA in hours for arrival filter (default: 1.0)",
    )
    parser.add_argument(
        "--refresh-interval",
        type=int,
        default=15,
        help="Auto-refresh interval in seconds (default: 15)",
    )
    parser.add_argument(
        "--airports",
        nargs="+",
        help="List of airport ICAO codes to include in analysis (default: all)",
    )
    parser.add_argument(
        "--countries",
        nargs="+",
        help="List of country codes (e.g., US DE) to include all airports from those countries",
    )
    parser.add_argument(
        "--groupings",
        nargs="+",
        help="List of custom grouping names to include in analysis. Groupings are recursively expanded to include all airports and sub-groupings. (default: all)",
    )
    parser.add_argument(
        "--include-all-staffed",
        action="store_true",
        help="Include airports with zero planes if they are staffed (default: False)",
    )
    parser.add_argument(
        "--disable-animations",
        action="store_true",
        help="Disable split-flap animations for instant text updates (default: False)",
    )
    parser.add_argument(
        "--progressive-load",
        action="store_true",
        help="Enable progressive loading for faster perceived startup (default: auto for 50+ airports)",
    )
    parser.add_argument(
        "--progressive-chunk-size",
        type=int,
        default=20,
        help="Number of rows to load per chunk in progressive mode (default: 20)",
    )
    parser.add_argument(
        "--wind-source",
        choices=["metar", "minute"],
        default="metar",
        help="Wind data source: 'metar' for METAR from aviationweather.gov (default), 'minute' for up-to-the-minute from weather.gov",
    )
    parser.add_argument(
        "--hide-wind",
        action="store_true",
        help="Hide the wind column from the main view (default: False)",
    )
    parser.add_argument(
        "--include-all-arriving",
        action="store_true",
        help="Include airports with any arrivals filed, regardless of max-eta-hours (default: False)",
    )
    return parser


def show_help_and_exit():
    """Show help message and exit immediately without any setup."""
    build_arg_parser().print_help()
    sys.exit(0)


# Check for --help or -h before any setup to provide instant help
if "--help" in sys.argv or "-h" in sys.argv:
    show_help_and_exit()


def is_running_in_venv():
    """Check if we're running inside a virtual environment."""
    # Check for venv/virtualenv (real_prefix is set by virtualenv, base_prefix by venv)
    return hasattr(sys, "real_prefix") or (
        hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
    )


def get_venv_python(venv_path):
    """Get the path to the Python executable in the venv."""
    if sys.platform == "win32":
        return os.path.join(venv_path, "Scripts", "python.exe")
    return os.path.join(venv_path, "bin", "python")


def ensure_venv_and_restart():
    """
    Ensure we're running in a virtual environment.
    If not, create one, ensure pip is installed, and restart the script within it.
    Returns True if already in venv, otherwise restarts and never returns.
    """
    if is_running_in_venv():
        return True

    script_dir = os.path.dirname(os.path.abspath(__file__))
    venv_path = os.path.join(script_dir, ".venv")
    venv_python = get_venv_python(venv_path)

    # Create venv if it doesn't exist
    if not os.path.exists(venv_python):
        print("Virtual environment not found. Creating one...")
        try:
            # Use venv module to create virtual environment
            subprocess.check_call([sys.executable, "-m", "venv", venv_path])
            print(f"Virtual environment created at: {venv_path}")
        except subprocess.CalledProcessError:
            print("\nError: Failed to create virtual environment.")
            print("You may need to install the venv module:")
            print("  - On Ubuntu/Debian: sudo apt install python3-venv")
            print("  - On other systems: ensure Python was installed with venv support")
            sys.exit(1)

    # Ensure pip is available in the venv
    print("Ensuring pip is available in virtual environment...")
    try:
        # First try ensurepip to bootstrap pip if missing
        subprocess.call(
            [venv_python, "-m", "ensurepip", "--upgrade"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        # Not fatal; the pip upgrade below reports if pip is genuinely missing
        print(f"Warning: Could not run ensurepip ({e}), continuing anyway...")

    # Upgrade pip to latest version
    try:
        subprocess.check_call(
            [venv_python, "-m", "pip", "install", "--upgrade", "pip"],
            stdout=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        print("Warning: Could not upgrade pip, continuing anyway...")

    # Re-execute this script using the venv Python, passing all arguments
    print("Restarting in virtual environment...\n")

    # Use subprocess.call and sys.exit for cross-platform compatibility
    result = subprocess.call([venv_python, *sys.argv])
    sys.exit(result)


def parse_requirements(requirements_path: str) -> list[str]:
    """Parse requirements.txt and return list of package names."""
    packages: list[str] = []
    with open(requirements_path) as f:
        for line in f:
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith("#"):
                continue
            # Extract package name (before any version specifier)
            match = re.match(r"^([a-zA-Z0-9_-]+)", line)
            if match:
                packages.append(match.group(1))
    return packages


def find_unimportable(packages: list[str]) -> list[tuple[str, str]]:
    """Return (package name, import error) for every package that fails to import.

    Args:
        packages: Distribution names as they appear in requirements.txt.

    Returns:
        One entry per package that could not be imported, in input order.
    """
    # Mapping for packages where pip name differs from import name
    import_name_map: dict[str, str] = {
        "Pillow": "PIL",
    }

    failures: list[tuple[str, str]] = []
    for package in packages:
        import_name = import_name_map.get(package, package)
        try:
            importlib.import_module(import_name)
        except ImportError as e:
            failures.append((package, str(e)))
    return failures


def report_broken_environment(failures: list[tuple[str, str]]) -> None:
    """Print an actionable message for packages that are installed but unusable."""
    print("\nError: these packages are installed but still fail to import:")
    for package, error in failures:
        print(f"  {package}: {error}")
    print("\nThe virtual environment is inconsistent: something these packages")
    print("depend on is missing. Delete the .venv directory and re-run this")
    print("script to rebuild the environment from scratch.")


def restart_after_install():
    """Re-execute this script in a fresh interpreter, then exit.

    A failed import leaves partially initialized modules behind, so packages
    installed during this run cannot be imported reliably in the same process.
    The child is marked so it installs at most once and cannot loop.
    """
    env = os.environ.copy()
    env[BOOTSTRAP_RETRY_ENV] = "1"
    result = subprocess.call([sys.executable, *sys.argv], env=env)
    sys.exit(result)


def ensure_requirements_installed():
    """Check if requirements are installed, and install them if not."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    requirements_path = os.path.join(script_dir, "requirements.txt")

    if not os.path.exists(requirements_path):
        print("Error: requirements.txt not found")
        return False

    failures = find_unimportable(parse_requirements(requirements_path))
    if not failures:
        return True

    # A restarted run has already installed once, so installing again cannot help
    if os.environ.get(BOOTSTRAP_RETRY_ENV):
        report_broken_environment(failures)
        return False

    # Requirements not installed, try to install them
    missing = [package for package, _ in failures]
    print(f"Missing dependencies: {', '.join(missing)}")
    print("Installing required dependencies...")
    try:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--prefer-binary",  # Prefer pre-built wheels to avoid compilation issues
                "-r",
                requirements_path,
            ]
        )
    except subprocess.CalledProcessError:
        print("\nError: Failed to install dependencies automatically.")
        print("\nThis often happens because spaCy requires native code compilation.")
        print("Try one of these solutions:\n")
        print("1. Upgrade to Python 3.10+ (recommended - has pre-built wheels):")
        print("   https://www.python.org/downloads/\n")
        print("2. Or install manually with upgraded pip:")
        print("   pip install --upgrade pip")
        print(f"   pip install -r {requirements_path}\n")
        print("3. On Windows, you may need Visual Studio Build Tools:")
        print("   https://visualstudio.microsoft.com/visual-cpp-build-tools/")
        return False

    print("Dependencies installed successfully.")
    restart_after_install()


def ensure_spacy_model_installed():
    """Check if the spaCy language model is installed, and download if not."""
    try:
        import spacy
    except ImportError as e:
        report_broken_environment([("spacy", str(e))])
        return False

    try:
        spacy.load("en_core_web_sm")
        return True
    except OSError:
        pass

    # Model not installed, try to download it
    print("Downloading spaCy language model (en_core_web_sm)...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "spacy", "download", "en_core_web_sm"]
        )
        print("Language model downloaded successfully.")
        return True
    except subprocess.CalledProcessError:
        print("\nError: Failed to download spaCy language model automatically.")
        print("Please download it manually by running:")
        print("  python -m spacy download en_core_web_sm")
        return False


# Skip bootstrap when running as a frozen PyInstaller executable
if not getattr(sys, "frozen", False):
    # Ensure we're running in a virtual environment (creates one if needed)
    ensure_venv_and_restart()

    # Ensure dependencies are installed before importing them
    if not ensure_requirements_installed():
        sys.exit(1)

    if not ensure_spacy_model_installed():
        sys.exit(1)

from airport_disambiguator import AirportDisambiguator
from backend import (
    analyze_flights_data,
    cleanup_old_cifp_caches,
    ensure_cifp_data,
    ensure_runway_data,
    load_unified_airport_data,
    load_weather_cache,
    save_weather_cache,
)
from backend.cache.manager import load_aircraft_approach_speeds
from backend.config import constants as backend_constants
from backend.core.groupings import (
    find_grouping_case_insensitive,
    load_all_groupings,
    resolve_grouping_recursively,
)
from common.paths import ensure_user_directories
from ui import (
    VATSIMControlApp,
    debug_logger,  # Import to trigger log cleanup on bootup
    expand_countries_to_airports,
)
from ui import config as ui_config


def load_reference_data():
    """Load the cached weather data and ensure CIFP and runway data are present."""
    # Load persistent weather cache from disk (if available and not expired)
    metar_count, taf_count = load_weather_cache()
    if metar_count > 0 or taf_count > 0:
        print(f"Loaded cached weather data: {metar_count} METARs, {taf_count} TAFs")

    # Ensure CIFP data is available (downloads from FAA if needed)
    # This happens once per AIRAC cycle (28 days)
    cifp_result = ensure_cifp_data(quiet=False)
    if cifp_result:
        debug_logger.info(f"CIFP data ready: {cifp_result}")
        # Cleanup old CIFP caches (keep current + 1 previous)
        cleanup_old_cifp_caches(keep_cycles=2)
    else:
        debug_logger.warning("CIFP data unavailable - approach data will not be shown")

    # Ensure runway data is available (downloads from OurAirports if needed/outdated)
    if ensure_runway_data(quiet=False):
        debug_logger.info("Runway data ready")
    else:
        debug_logger.warning(
            "Runway data unavailable - runway lengths will not be shown"
        )


def has_coordinates(icao: str) -> bool:
    """Check whether an airport has a usable latitude and longitude."""
    airport = ui_config.UNIFIED_AIRPORT_DATA.get(icao)
    return bool(
        airport
        and airport.get("latitude") is not None
        and airport.get("longitude") is not None
    )


def expand_grouping_airports(grouping_names: list[str], script_dir: str) -> list[str]:
    """Resolve grouping names to the airports they contain.

    Args:
        grouping_names: Grouping names as given on the command line.
        script_dir: Repository root, used to locate custom_groupings.json.

    Returns:
        ICAO codes of every airport in those groupings that has coordinates.
    """
    all_groupings = load_all_groupings(
        os.path.join(script_dir, "data", "custom_groupings.json"),
        ui_config.UNIFIED_AIRPORT_DATA,
    )

    grouping_airports = set()
    for group_name in grouping_names:
        actual_name = find_grouping_case_insensitive(group_name, all_groupings)
        if actual_name:
            # Recursively resolve the grouping to all airports
            grouping_airports.update(
                resolve_grouping_recursively(actual_name, all_groupings)
            )
        else:
            print(
                f"Warning: Grouping '{group_name}' not found in custom_groupings.json"
            )

    if not grouping_airports:
        return []

    valid_airports = [ap for ap in grouping_airports if has_coordinates(ap)]
    print(
        f"Expanded groupings to {len(valid_airports)} airport(s) (filtered from {len(grouping_airports)})"
    )
    return valid_airports


def resolve_airport_allowlist(args, script_dir: str) -> list[str]:
    """Build the tracked-airport list from explicit codes, countries, and groupings.

    Args:
        args: Parsed command-line arguments.
        script_dir: Repository root, used to locate the airport data files.

    Returns:
        ICAO codes to track, which is empty when no filter was requested.
    """
    # Load unified airport data if we need to expand countries or groupings
    if args.countries or args.groupings:
        ui_config.UNIFIED_AIRPORT_DATA = load_unified_airport_data(
            apt_base_path=os.path.join(script_dir, "data", "raw", "APT_BASE.csv"),
            airports_json_path=os.path.join(script_dir, "data", "raw", "airports.json"),
            iata_icao_path=os.path.join(script_dir, "data", "raw", "iata-icao.csv"),
        )
        ui_config.DISAMBIGUATOR = AirportDisambiguator(
            os.path.join(script_dir, "data", "raw", "airports.json"),
            unified_data=ui_config.UNIFIED_AIRPORT_DATA,
            names_csv_path=os.path.join(script_dir, "data", "airport_names.csv"),
        )

    # Start with explicitly provided airports
    airport_allowlist = args.airports or []

    # Expand country codes to airport ICAO codes
    if args.countries and ui_config.UNIFIED_AIRPORT_DATA:
        country_airports = expand_countries_to_airports(
            args.countries, ui_config.UNIFIED_AIRPORT_DATA
        )
        print(
            f"Expanded {len(args.countries)} country code(s) to {len(country_airports)} airport(s)"
        )
        airport_allowlist = list(set(airport_allowlist + country_airports))

    # Expand groupings to airport ICAO codes at bootup (recursively resolves nested groupings)
    if args.groupings and ui_config.UNIFIED_AIRPORT_DATA:
        valid_airports = expand_grouping_airports(args.groupings, script_dir)
        if valid_airports:
            airport_allowlist = list(set(airport_allowlist + valid_airports))

    return airport_allowlist


def set_terminal_title():
    """Set the terminal title before Textual takes over."""
    try:
        # Write to stderr to avoid buffering issues
        sys.stderr.write("\033]0;VATSIM Control Recommendations\007")
        sys.stderr.flush()
    except (OSError, AttributeError) as e:
        debug_logger.debug(f"Could not set terminal title: {e}")


def main():
    # Ensure user data directories exist
    ensure_user_directories()

    args = build_arg_parser().parse_args()

    # Set the global wind source from command-line argument
    backend_constants.WIND_SOURCE = args.wind_source

    # Log cleanup happens automatically when debug_logger is imported
    debug_logger.info("Application starting")

    load_reference_data()

    print("Loading VATSIM data...")

    # Load aircraft approach speeds for ETA calculations
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ui_config.AIRCRAFT_APPROACH_SPEEDS = load_aircraft_approach_speeds(
        os.path.join(script_dir, "data", "aircraft_data.csv")
    )

    airport_allowlist = resolve_airport_allowlist(args, script_dir)

    # Get the data (groupings already expanded to airport_allowlist)
    (
        airport_data,
        groupings_data,
        total_flights,
        ui_config.UNIFIED_AIRPORT_DATA,
        ui_config.DISAMBIGUATOR,
    ) = analyze_flights_data(
        max_eta_hours=args.max_eta_hours,
        airport_allowlist=airport_allowlist or None,
        groupings_allowlist=args.groupings,  # Still used for display purposes only
        include_all_staffed=args.include_all_staffed,
        hide_wind=args.hide_wind,
        include_all_arriving=args.include_all_arriving,
        unified_airport_data=ui_config.UNIFIED_AIRPORT_DATA,
        disambiguator=ui_config.DISAMBIGUATOR,
    )

    if airport_data is None:
        print("Failed to download VATSIM data")
        return

    set_terminal_title()

    # Run the Textual app
    app = VATSIMControlApp(
        airport_data,
        groupings_data,
        total_flights or 0,
        args,
        airport_allowlist or None,
    )
    app.run()

    # Save weather cache to disk on exit
    save_weather_cache()
    debug_logger.info("Application exiting - weather cache saved")


if __name__ == "__main__":
    main()
