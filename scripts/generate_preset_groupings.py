#!/usr/bin/env python3
"""
Generate preset groupings from vNAS API.

Fetches ARTCC data from https://data-api.vnas.vatsim.net/api/artccs
and generates preset grouping JSON files for each ARTCC.

Includes:
- TRACON sector areas from starsConfiguration.areas (underlyingAirports)
- Tower groupings from childFacilities
- International airports from nonNasFacilityIds
"""

import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

VNAS_API_BASE = "https://data-api.vnas.vatsim.net/api/artccs"
OUTPUT_DIR = PROJECT_ROOT / "data" / "preset_groupings"

# FIR/ARTCC codes to exclude (not airports)
EXCLUDED_FACILITY_IDS = {
    'ZAK',  # Oakland Oceanic
    'ZWY',  # Caribbean virtual ARTCC
}

# Country names for international airport groupings
COUNTRY_NAMES = {
    'MY': 'Bahamas',
    'MB': 'Turks & Caicos',
    'MD': 'Dominican Republic',
    'MU': 'Cuba',
    'MT': 'Cuba',
    'MM': 'Mexico',
    'CY': 'Canada',
    'CZ': 'Canada',
    'TN': 'Netherlands Antilles',
    'TT': 'Trinidad & Tobago',
    'TF': 'French Antilles',
    'TA': 'Antigua',
    'TK': 'St. Kitts',
    'TU': 'British Virgin Islands',
    'SV': 'Venezuela',
    'TX': 'Bermuda',
    'UH': 'Russia',
}


def fetch_artcc_data(artcc: str) -> Optional[Dict[str, Any]]:
    """Fetch ARTCC data from vNAS API."""
    try:
        url = f"{VNAS_API_BASE}/{artcc}"
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'VATSIM-Control-Recs/1.0')
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"  ERROR fetching {artcc}: {e}")
        return None


def get_all_artccs() -> List[str]:
    """Get list of all ARTCCs from vNAS API."""
    try:
        req = urllib.request.Request(VNAS_API_BASE)
        req.add_header('User-Agent', 'VATSIM-Control-Recs/1.0')
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        return [item.get('id') for item in data if item.get('id')]
    except Exception as e:
        print(f"ERROR fetching ARTCC list: {e}")
        return []


def normalize_icao(code: str) -> str:
    """Normalize airport code to ICAO format."""
    code = code.upper().strip()
    # US 3-letter FAA codes - add K prefix
    if len(code) == 3 and code.isalpha():
        return f"K{code}"
    # Already 4+ letters or alphanumeric (like E16, C83) - keep as-is
    return code


def is_airport_code(code: str) -> bool:
    """Check if code is likely an airport (not a FIR/ARTCC)."""
    if not code or len(code) < 2:
        return False
    if code in EXCLUDED_FACILITY_IDS:
        return False
    # 3-letter codes starting with Z are usually ARTCCs/FIRs
    if len(code) == 3 and code.startswith('Z'):
        return False
    return True


def extract_areas_from_facility(
    facility: Dict[str, Any],
    facility_id: str,
    groupings: Dict[str, List[str]]
) -> None:
    """
    Extract sector areas from starsConfiguration.areas.

    Each area has underlyingAirports which maps to a sector grouping.
    """
    stars = facility.get('starsConfiguration', {})
    areas = stars.get('areas', [])

    for area in areas:
        area_name = area.get('name', '')
        underlying = area.get('underlyingAirports', [])

        if not area_name or not underlying:
            continue

        # Skip generic/system areas
        if area_name.lower() in ('default', 'all', 'none'):
            continue

        # Normalize airport codes
        airports = [normalize_icao(code) for code in underlying if is_airport_code(code)]

        if airports:
            group_name = f"{facility_id} {area_name}"
            groupings[group_name] = sorted(set(airports))


def extract_airports_from_facility(facility: Dict[str, Any], collected: Set[str]) -> None:
    """Recursively extract airport IDs from a facility and its children."""
    facility_id = facility.get('id', '')

    if is_airport_code(facility_id):
        collected.add(normalize_icao(facility_id))

    # Recurse into child facilities
    for child in facility.get('childFacilities', []):
        extract_airports_from_facility(child, collected)


def process_facility_hierarchy(
    facility: Dict[str, Any],
    groupings: Dict[str, List[str]],
    depth: int = 0
) -> None:
    """
    Process facility hierarchy to extract groupings.

    - Extracts sector areas from starsConfiguration
    - Creates groupings for TRACONs with their underlying airports
    """
    facility_id = facility.get('id', '')
    facility_type = facility.get('type', '')
    facility_name = facility.get('name', facility_id)
    children = facility.get('childFacilities', [])

    # Extract sector areas from STARS configuration
    extract_areas_from_facility(facility, facility_id, groupings)

    # For facilities with children, create a combined grouping
    if children:
        airports: Set[str] = set()
        for child in children:
            extract_airports_from_facility(child, airports)

        # Add the facility itself if it's an airport
        if is_airport_code(facility_id):
            airports.add(normalize_icao(facility_id))

        if airports and facility_type in ('AtctTracon', 'Tracon', 'AtctRapcon', 'Rapcon'):
            # Create combined grouping for the facility
            group_name = f"{facility_id} {facility_name}"
            group_name = group_name.replace('  ', ' ').strip()

            # Only add if we don't already have sector-level groupings
            sector_prefix = f"{facility_id} "
            has_sectors = any(k.startswith(sector_prefix) and k != group_name
                            for k in groupings)

            if not has_sectors:
                groupings[group_name] = sorted(airports)

    # Recurse into children
    for child in children:
        process_facility_hierarchy(child, groupings, depth + 1)


def add_international_airports(
    facility: Dict[str, Any],
    groupings: Dict[str, List[str]]
) -> int:
    """Add international airports from nonNasFacilityIds."""
    non_nas = facility.get('nonNasFacilityIds', [])
    intl_airports = [code.upper() for code in non_nas
                     if is_airport_code(code) and len(code) == 4]

    if not intl_airports:
        return 0

    # Group by country prefix
    by_country: Dict[str, List[str]] = {}

    for code in intl_airports:
        prefix = code[:2]
        country = COUNTRY_NAMES.get(prefix, f'{prefix} Region')
        if country not in by_country:
            by_country[country] = []
        by_country[country].append(code)

    # Add groupings for each country
    count = 0
    for country, airports in sorted(by_country.items()):
        group_name = f"International - {country}"
        groupings[group_name] = sorted(airports)
        count += 1

    return count


def generate_artcc_groupings(artcc: str, data: Dict[str, Any]) -> Dict[str, List[str]]:
    """Generate groupings for an ARTCC from vNAS data."""
    groupings: Dict[str, List[str]] = {}

    facility = data.get('facility', {})

    # Process each child facility (TRACONs, RAPCONs, etc.)
    for child in facility.get('childFacilities', []):
        process_facility_hierarchy(child, groupings)

    # Add international airports
    add_international_airports(facility, groupings)

    return groupings


def main():
    """Main entry point."""
    print("Fetching ARTCC list from vNAS API...")
    artccs = get_all_artccs()

    if not artccs:
        print("Failed to fetch ARTCC list")
        return 1

    print(f"Found {len(artccs)} ARTCCs: {', '.join(sorted(artccs))}")

    # Fetch all ARTCC data in parallel
    print("\nFetching ARTCC data...")
    artcc_data: Dict[str, Dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_artcc_data, artcc): artcc for artcc in artccs}
        for future in futures:
            artcc = futures[future]
            data = future.result()
            if data:
                artcc_data[artcc] = data
                print(f"  {artcc}: OK")
            else:
                print(f"  {artcc}: FAILED")

    # Generate groupings for each ARTCC
    print("\nGenerating groupings...")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    total_groupings = 0
    total_intl = 0

    for artcc in sorted(artcc_data.keys()):
        data = artcc_data[artcc]
        groupings = generate_artcc_groupings(artcc, data)

        if groupings:
            output_file = OUTPUT_DIR / f"{artcc}.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(groupings, f, indent=2, ensure_ascii=False)
                f.write('\n')  # Trailing newline

            intl_count = sum(1 for name in groupings if name.startswith('International'))
            total_groupings += len(groupings)
            total_intl += intl_count
            print(f"  {artcc}: {len(groupings)} groupings ({intl_count} international)")
        else:
            print(f"  {artcc}: No groupings generated")

    print(f"\nDone! Generated {total_groupings} groupings ({total_intl} international)")
    print(f"Output: {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
