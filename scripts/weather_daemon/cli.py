#!/usr/bin/env python3
"""
Weather Briefing Daemon CLI

Command-line interface for the weather briefing generator.
Can be run manually or via systemd timer.
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Set

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.weather_daemon.config import DaemonConfig
from scripts.weather_daemon.generator import generate, acquire_lock

# Valid stages for --stages argument
VALID_STAGES = {'weather', 'briefings', 'tiles', 'index'}
ALL_STAGES = VALID_STAGES.copy()


def parse_stages(stages_str: str) -> Set[str]:
    """Parse comma-separated stages string into a set of valid stages."""
    stages = {s.strip().lower() for s in stages_str.split(',')}
    invalid = stages - VALID_STAGES
    if invalid:
        raise ValueError(f"Invalid stage(s): {', '.join(sorted(invalid))}. Valid stages: {', '.join(sorted(VALID_STAGES))}")
    return stages


def setup_logging(log_dir: Path, verbose: bool = False) -> None:
    """Configure logging for the daemon."""
    log_dir.mkdir(parents=True, exist_ok=True)

    # Create log filename with date
    log_file = log_dir / f"weather_daemon_{datetime.now().strftime('%Y%m%d')}.log"

    # Configure root logger for weather_daemon
    logger = logging.getLogger("weather_daemon")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    # File handler - always logs INFO and above
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Console handler for verbose mode (DEBUG level)
    if verbose:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_formatter = logging.Formatter('[%(levelname)s] %(message)s')
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)


def main():
    parser = argparse.ArgumentParser(
        description="Generate VATSIM weather briefings for all groupings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Stages:
  weather   - Fetch fresh weather data (METARs, TAFs, ATIS)
  briefings - Generate HTML briefing pages for each grouping
  tiles     - Generate weather overlay map tiles
  index     - Generate the index.html page

Examples:
  # Full generation (all stages)
  python -m scripts.weather_daemon.cli --output ./test_output

  # Tiles and index only (use cached weather)
  python -m scripts.weather_daemon.cli --stages tiles,index

  # Force regeneration even if weather unchanged
  python -m scripts.weather_daemon.cli --force

  # Run without lock file (allow concurrent runs)
  python -m scripts.weather_daemon.cli --no-lock

  # Generate only for specific ARTCCs
  python -m scripts.weather_daemon.cli --artccs ZOA ZLA ZSE
""",
    )

    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output directory for generated HTML files (default: /var/www/leftos.dev/weather)",
    )

    parser.add_argument(
        "--stages", "-s",
        type=str,
        default=None,
        help="Comma-separated list of stages to run: weather,briefings,tiles,index (default: all)",
    )

    parser.add_argument(
        "--artccs",
        nargs="+",
        type=str,
        default=None,
        help="Only generate for specific ARTCC(s) (e.g., ZOA ZLA)",
    )

    parser.add_argument(
        "--custom-only",
        action="store_true",
        help="Only generate custom groupings (skip presets)",
    )

    parser.add_argument(
        "--presets-only",
        action="store_true",
        help="Only generate preset groupings (skip custom)",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Maximum concurrent API requests (default: 10)",
    )

    parser.add_argument(
        "--tile-workers",
        type=int,
        default=None,
        help="Maximum concurrent tile generation workers (default: 2 for servers, increase for local machines with more RAM)",
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output",
    )

    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Path to data directory (default: project's data/ folder)",
    )

    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="Path to log directory (default: project's logs/ folder)",
    )

    parser.add_argument(
        "--no-lock",
        action="store_true",
        help="Disable lock file (allow concurrent runs)",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Force regeneration even if weather hasn't changed",
    )

    args = parser.parse_args()

    # Parse stages
    if args.stages:
        try:
            stages = parse_stages(args.stages)
        except ValueError as e:
            parser.error(str(e))
    else:
        stages = ALL_STAGES.copy()

    # Build configuration
    config = DaemonConfig()

    # Determine log directory
    project_root = Path(__file__).parent.parent.parent
    log_dir = args.log_dir or project_root / "logs"

    # Set up logging
    setup_logging(log_dir, verbose=args.verbose)

    if args.output:
        config.output_dir = args.output

    if args.artccs:
        config.artcc_filter = [a.upper() for a in args.artccs]

    if args.custom_only:
        config.include_presets = False

    if args.presets_only:
        config.include_custom = False

    # Configure stages
    config.fetch_fresh_weather = 'weather' in stages
    config.generate_briefings = 'briefings' in stages
    config.generate_tiles = 'tiles' in stages
    config.generate_index = 'index' in stages

    if args.workers:
        config.max_workers = args.workers

    if args.tile_workers:
        config.tile_max_workers = args.tile_workers

    if args.data_dir:
        config.data_dir = args.data_dir

    # Handle --force flag
    if args.force:
        config.skip_if_unchanged = False

    # Validate configuration
    if args.custom_only and args.presets_only:
        parser.error("Cannot use both --custom-only and --presets-only")

    # Print stage info
    if args.verbose:
        print(f"Stages: {', '.join(sorted(stages))}")
        if not config.fetch_fresh_weather:
            print("Using cached weather data")

    # Run generation (with optional lock)
    def do_generate():
        generated_files = generate(config)

        if args.verbose:
            print("\nGenerated files:")
            for path, name in generated_files.items():
                print(f"  {path}")

        if generated_files:
            print(f"\nSuccess: Generated {len(generated_files)} files to {config.output_dir}")
        else:
            print(f"\nNo files generated (weather unchanged or no groupings)")
        return 0

    try:
        if args.no_lock:
            # Run without lock
            return do_generate()
        else:
            # Use lock file to prevent concurrent runs
            with acquire_lock(config.lock_file) as acquired:
                if not acquired:
                    print("Another instance is already running, skipping this run")
                    logger = logging.getLogger("weather_daemon")
                    logger.info("Skipped: another instance is already running")
                    return 0  # Exit cleanly, systemd timer will retry later
                return do_generate()

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 130

    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
