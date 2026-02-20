"""Generate airport_names.csv from the disambiguator.

Runs the full disambiguator over all airports and writes the results
to data/airport_names.csv. Existing manual edits are preserved unless
--force is used.

Usage:
    python scripts/generate_airport_names.py
    python scripts/generate_airport_names.py --force
"""

import argparse
import csv
import io
import os
import sys

# Add project root to path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
sys.path.insert(0, project_root)

from backend.data.loaders import load_unified_airport_data
from airport_disambiguator import AirportDisambiguator


def title_case_name(name: str) -> str:
    """Title-case an airport name, preserving " - " separators."""
    if " - " in name:
        parts = name.split(" - ", 1)
        return f"{parts[0].title()} - {parts[1].title()}"
    return name.title()


def load_existing_csv(path: str) -> dict[str, str]:
    """Load existing airport_names.csv, returning {icao: name}."""
    if not os.path.exists(path):
        return {}

    existing = {}
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            icao = row.get("icao", "").strip()
            name = row.get("name", "").strip()
            if icao and name:
                existing[icao] = name
    return existing


def main():
    parser = argparse.ArgumentParser(
        description="Generate data/airport_names.csv from the disambiguator."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate all entries, discarding manual edits",
    )
    args = parser.parse_args()

    raw_dir = os.path.join(project_root, "data", "raw")
    output_path = os.path.join(project_root, "data", "airport_names.csv")

    # Load unified airport data
    print("Loading unified airport data...")
    unified_data = load_unified_airport_data(
        apt_base_path=os.path.join(raw_dir, "APT_BASE.csv"),
        airports_json_path=os.path.join(raw_dir, "airports.json"),
        iata_icao_path=os.path.join(raw_dir, "iata-icao.csv"),
    )
    print(f"  Loaded {len(unified_data)} airports")

    # Load existing CSV to preserve manual edits
    existing = {}
    if not args.force:
        existing = load_existing_csv(output_path)
        if existing:
            print(f"  Loaded {len(existing)} existing names (will preserve)")

    # Run disambiguator on all airports
    print("Running disambiguator (this takes ~60s)...")
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    disambiguator = AirportDisambiguator(
        os.path.join(raw_dir, "airports.json"),
        lazy_load=False,
        unified_data=unified_data,
    )

    # Build final name mapping
    names = {}
    preserved = 0
    generated = 0

    for icao in sorted(unified_data.keys()):
        if icao in existing:
            names[icao] = existing[icao]
            preserved += 1
        else:
            full_name = disambiguator.icao_to_full_name.get(icao, icao)
            names[icao] = title_case_name(full_name)
            generated += 1

    # Write CSV
    print(f"\nWriting {len(names)} entries to {output_path}")
    print(f"  Preserved: {preserved}, Generated: {generated}")

    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["icao", "name"])
        for icao in sorted(names.keys()):
            writer.writerow([icao, names[icao]])

    print("Done.")


if __name__ == "__main__":
    main()
