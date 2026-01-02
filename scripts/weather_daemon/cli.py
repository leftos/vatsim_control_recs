#!/usr/bin/env python3
"""
Weather Briefing Daemon CLI

Command-line interface for the weather briefing generator.
Can be run manually or via systemd timer.
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.weather_daemon.config import DaemonConfig
from scripts.weather_daemon.generator import generate_all_briefings


def main():
    parser = argparse.ArgumentParser(
        description="Generate VATSIM weather briefings for all groupings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate to default output directory
  python -m scripts.weather_daemon.cli

  # Generate to specific directory
  python -m scripts.weather_daemon.cli --output /var/www/weather

  # Generate only for specific ARTCCs
  python -m scripts.weather_daemon.cli --artccs ZOA ZLA ZSE

  # Generate only custom groupings
  python -m scripts.weather_daemon.cli --custom-only

  # Test run with local output
  python -m scripts.weather_daemon.cli --output ./test_output --verbose
""",
    )

    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output directory for generated HTML files (default: /var/www/leftos.dev/weather)",
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
        "--no-index",
        action="store_true",
        help="Skip generating the index page",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Maximum concurrent API requests (default: 10)",
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

    args = parser.parse_args()

    # Build configuration
    config = DaemonConfig()

    if args.output:
        config.output_dir = args.output

    if args.artccs:
        config.artcc_filter = [a.upper() for a in args.artccs]

    if args.custom_only:
        config.include_presets = False

    if args.presets_only:
        config.include_custom = False

    if args.no_index:
        config.generate_index = False

    if args.workers:
        config.max_workers = args.workers

    if args.data_dir:
        config.data_dir = args.data_dir

    # Validate configuration
    if args.custom_only and args.presets_only:
        parser.error("Cannot use both --custom-only and --presets-only")

    # Run generation
    try:
        generated_files = generate_all_briefings(config)

        if args.verbose:
            print("\nGenerated files:")
            for path, name in generated_files.items():
                print(f"  {path}")

        print(f"\nSuccess: Generated {len(generated_files)} files to {config.output_dir}")
        return 0

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
