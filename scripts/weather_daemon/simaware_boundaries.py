"""
SimAware TRACON Boundary Loader

Loads TRACON boundary data from pre-downloaded SimAware files.
Maps grouping names to actual airspace polygons using position prefix data
from the preset groupings.

The boundary data is pre-downloaded by scripts/generate_simaware_boundaries.py
and stored in data/simaware_boundaries/*.json

Source: https://github.com/vatsimnetwork/simaware-tracon-project
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Default directories
SIMAWARE_BOUNDARIES_DIR = Path(__file__).parent.parent.parent / "data" / "simaware_boundaries"
PRESET_GROUPINGS_DIR = Path(__file__).parent.parent.parent / "data" / "preset_groupings"

# Sub-facility to parent SimAware folder mappings
# Some groupings reference sub-facilities that don't have their own SimAware folder
SUB_FACILITY_MAP = {
    "O90": "NCT",   # Bay TRACON is part of NCT
    "MC1": "NCT",   # Sacramento sector
    "SC1": "NCT",   # Stockton sector
    "MR1": "NCT",   # Monterey sector
}


def load_preset_grouping_data(preset_dir: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """
    Load all preset groupings with full metadata (including position_prefixes).

    Note: Single-airport groupings are filtered out.

    Returns:
        Dict mapping grouping names to their full data including:
        - airports: list of airport codes
        - position_prefixes: list of position prefixes (e.g., ['SFO', 'OAK'])
        - position_suffixes: list of position suffixes (e.g., ['APP', 'DEP'])
        - facility_id: the facility ID (e.g., 'NCT')
    """
    if preset_dir is None:
        preset_dir = PRESET_GROUPINGS_DIR

    all_data: Dict[str, Dict[str, Any]] = {}

    if not preset_dir.exists():
        return all_data

    for json_file in preset_dir.glob("*.json"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not isinstance(data, dict):
                continue

            for key, value in data.items():
                if not isinstance(key, str):
                    continue

                grouping_data = None
                if isinstance(value, dict):
                    # New format with metadata
                    grouping_data = value
                elif isinstance(value, list):
                    # Old format - just airport list, no metadata
                    grouping_data = {
                        'airports': value,
                        'position_prefixes': None,
                        'position_suffixes': None,
                        'facility_id': None,
                    }

                # Filter out single-airport groupings
                if grouping_data:
                    airports = grouping_data.get('airports', [])
                    if len(airports) > 1:
                        all_data[key] = grouping_data

        except Exception:
            pass

    return all_data


def load_simaware_boundaries(
    boundaries_dir: Optional[Path] = None
) -> Dict[str, Dict[str, Any]]:
    """
    Load all SimAware boundary data from local files.

    Returns:
        Dict mapping facility ID to dict of boundary name -> coordinates
    """
    if boundaries_dir is None:
        boundaries_dir = SIMAWARE_BOUNDARIES_DIR

    all_boundaries: Dict[str, Dict[str, Any]] = {}

    if not boundaries_dir.exists():
        return all_boundaries

    for json_file in boundaries_dir.glob("*.json"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            facility = json_file.stem
            all_boundaries[facility] = data
        except Exception:
            pass

    return all_boundaries


def resolve_simaware_folder(
    facility_id: str,
    available_facilities: set
) -> Optional[str]:
    """
    Resolve a facility ID to a SimAware folder name.

    Args:
        facility_id: The facility ID (e.g., 'NCT', 'O90')
        available_facilities: Set of available SimAware facility names

    Returns:
        The SimAware folder name, or None if not found
    """
    if facility_id in available_facilities:
        return facility_id
    if facility_id in SUB_FACILITY_MAP:
        parent = SUB_FACILITY_MAP[facility_id]
        if parent in available_facilities:
            return parent
    return None


def find_boundary_for_prefix(
    prefix: str,
    suffix: Optional[str],
    simaware_folder: str,
    boundaries_data: Dict[str, Dict[str, Any]],
) -> Optional[List[List[float]]]:
    """
    Find the best boundary for a position prefix and suffix.

    Tries in order:
    1. PREFIX_DEP (if suffix is DEP)
    2. PREFIX
    3. FOLDER (facility's main boundary as fallback)

    Args:
        prefix: Position prefix (e.g., 'SFO', 'OAK')
        suffix: Position suffix (e.g., 'APP', 'DEP') or None
        simaware_folder: The SimAware folder to search in
        boundaries_data: All loaded SimAware boundary data

    Returns:
        Boundary coordinates as list of [lat, lon] pairs, or None
    """
    facility_boundaries = boundaries_data.get(simaware_folder, {})
    if not facility_boundaries:
        return None

    # For DEP suffix, try PREFIX_DEP first
    if suffix == 'DEP':
        dep_key = f"{prefix}_DEP"
        if dep_key in facility_boundaries:
            return facility_boundaries[dep_key].get('coordinates')

    # Try exact prefix match
    if prefix in facility_boundaries:
        return facility_boundaries[prefix].get('coordinates')

    # Fall back to facility's main boundary (e.g., L30 only has L30.json)
    if simaware_folder in facility_boundaries:
        return facility_boundaries[simaware_folder].get('coordinates')

    return None


def map_grouping_to_boundaries(
    grouping_name: str,
    grouping_data: Optional[Dict[str, Any]],
    boundaries_data: Dict[str, Dict[str, Any]],
) -> List[List[List[float]]]:
    """
    Map a grouping to one or more SimAware boundary polygons.

    Args:
        grouping_name: Name of the grouping (e.g., 'NCT D', 'SCT Burbank')
        grouping_data: Full grouping data including position_prefixes
        boundaries_data: All loaded SimAware boundary data

    Returns:
        List of boundary polygons (each is list of [lat, lon] pairs).
        Returns empty list if no match found (will fall back to convex hull).
    """
    if not grouping_data:
        return []

    position_prefixes = grouping_data.get('position_prefixes')
    position_suffixes = grouping_data.get('position_suffixes')
    facility_id = grouping_data.get('facility_id')

    available_facilities = set(boundaries_data.keys())

    # Skip groupings without position prefix data
    if not position_prefixes:
        # Fall back to facility-level matching
        if facility_id:
            simaware_folder = resolve_simaware_folder(facility_id, available_facilities)
            if simaware_folder:
                facility_boundaries = boundaries_data.get(simaware_folder, {})
                # Try facility-level boundary
                if facility_id in facility_boundaries:
                    coords = facility_boundaries[facility_id].get('coordinates')
                    if coords:
                        return [coords]

        return []

    # Get the SimAware folder from the facility_id
    if not facility_id:
        return []

    simaware_folder = resolve_simaware_folder(facility_id, available_facilities)
    if not simaware_folder:
        return []

    # Determine the primary suffix (DEP gets special handling)
    primary_suffix = None
    if position_suffixes:
        if 'DEP' in position_suffixes:
            primary_suffix = 'DEP'
        elif 'APP' in position_suffixes:
            primary_suffix = 'APP'

    # Find boundaries for each position prefix
    # Collect ALL matching boundaries (e.g., NCT C with OAK and MOD prefixes)
    polygons: List[List[List[float]]] = []
    seen_coords: set = set()  # Avoid duplicates

    for prefix in position_prefixes:
        coords = find_boundary_for_prefix(
            prefix, primary_suffix, simaware_folder, boundaries_data
        )
        if coords:
            # Use first few points as a key to detect duplicates
            coords_key = tuple(tuple(p) for p in coords[:3]) if len(coords) >= 3 else None
            if coords_key and coords_key not in seen_coords:
                polygons.append(coords)
                seen_coords.add(coords_key)

    return polygons


def expand_plus_pattern(
    grouping_name: str,
    all_grouping_data: Dict[str, Dict[str, Any]],
) -> List[str]:
    """
    Expand a grouping name with '+' pattern into component groupings.

    Examples:
        "NCT E+R" -> ["NCT E", "NCT R"]
        "N90+B90" -> ["N90", "B90"]
        "NCT A+B+C" -> ["NCT A", "NCT B", "NCT C"]

    Args:
        grouping_name: The grouping name (may contain '+')
        all_grouping_data: All available grouping data

    Returns:
        List of expanded grouping names
    """
    if '+' not in grouping_name:
        return [grouping_name]

    # Check if it's a "Facility Letter+Letter" pattern (e.g., "NCT E+R")
    # or a "Facility+Facility" pattern (e.g., "N90+B90")
    parts = grouping_name.split()

    if len(parts) == 2:
        # Pattern: "NCT E+R" -> facility is "NCT", sectors are "E" and "R"
        facility = parts[0]
        sector_part = parts[1]

        if '+' in sector_part:
            sectors = sector_part.split('+')
            expanded = []
            for sector in sectors:
                candidate = f"{facility} {sector}"
                expanded.append(candidate)
            return expanded if expanded else [grouping_name]

    # Pattern: "N90+B90" or similar
    if '+' in grouping_name and ' ' not in grouping_name:
        parts = grouping_name.split('+')
        expanded = []
        for part in parts:
            # Try exact match first
            if part in all_grouping_data:
                expanded.append(part)
            else:
                # Try adding common suffix patterns
                for suffix in ['', ' Combined', f' {part}']:
                    candidate = f"{part}{suffix}"
                    if candidate in all_grouping_data:
                        expanded.append(candidate)
                        break
                else:
                    expanded.append(part)  # Add anyway, will be resolved later
        return expanded

    return [grouping_name]


def polygon_min_distance(poly1: List[List[float]], poly2: List[List[float]]) -> float:
    """
    Calculate the minimum distance between any two points of two polygons.

    Args:
        poly1, poly2: Polygons as lists of [lat, lon] points

    Returns:
        Minimum distance in degrees (approximate, for comparison only)
    """
    import math
    min_dist = float('inf')
    for p1 in poly1:
        for p2 in poly2:
            # Simple Euclidean distance in degrees
            dist = math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)
            if dist < min_dist:
                min_dist = dist
    return min_dist


def convex_hull(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """
    Compute the convex hull of a set of points.

    Args:
        points: List of (lat, lon) tuples

    Returns:
        Convex hull as list of (lat, lon) tuples
    """
    if len(points) < 3:
        return list(points)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    unique_points = list(set(points))
    sorted_points = sorted(unique_points)

    if len(sorted_points) < 3:
        return sorted_points

    # Build lower hull
    lower = []
    for p in sorted_points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    # Build upper hull
    upper = []
    for p in reversed(sorted_points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


def generate_circle_polygon(
    lat: float,
    lon: float,
    radius_nm: float = 5,
    num_points: int = 32
) -> List[Tuple[float, float]]:
    """
    Generate a circle polygon around a point.

    Used for Tower groupings that don't have specific airspace boundaries.

    Args:
        lat: Center latitude
        lon: Center longitude
        radius_nm: Radius in nautical miles (default 5nm)
        num_points: Number of points in the circle

    Returns:
        List of (lat, lon) tuples forming a circle
    """
    import math

    # Convert nm to degrees (approximate: 1 degree lat ≈ 60nm)
    radius_deg_lat = radius_nm / 60.0
    # Adjust longitude for latitude (longitude degrees are smaller at higher latitudes)
    radius_deg_lon = radius_nm / (60.0 * math.cos(math.radians(lat)))

    points = []
    for i in range(num_points):
        angle = 2 * math.pi * i / num_points
        pt_lat = lat + radius_deg_lat * math.sin(angle)
        pt_lon = lon + radius_deg_lon * math.cos(angle)
        points.append((pt_lat, pt_lon))

    # Close the polygon
    points.append(points[0])
    return points


def combine_polygons(
    polygons: List[List[List[float]]],
    neighbor_threshold: float = 0.05  # ~3nm at mid-latitudes
) -> List[List[Tuple[float, float]]]:
    """
    Combine multiple polygons into groups based on proximity.

    Neighboring polygons (within threshold distance) are merged using convex hull.
    Non-neighboring polygons are kept as separate polygons.

    Args:
        polygons: List of polygon coordinate lists (each is list of [lat, lon])
        neighbor_threshold: Maximum distance (degrees) to consider polygons as neighbors

    Returns:
        List of polygons, where each polygon is a list of (lat, lon) tuples.
        Returns multiple polygons if there are gaps between input polygons.
    """
    if not polygons:
        return []
    if len(polygons) == 1:
        return [[(p[0], p[1]) for p in polygons[0]]]

    # Convert all polygons to tuple format
    tuple_polygons = [[(p[0], p[1]) for p in poly] for poly in polygons]

    # Use Union-Find to group neighboring polygons
    n = len(tuple_polygons)
    parent = list(range(n))

    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # Check all pairs for proximity
    for i in range(n):
        for j in range(i + 1, n):
            dist = polygon_min_distance(polygons[i], polygons[j])
            if dist <= neighbor_threshold:
                union(i, j)

    # Group polygons by their root
    from collections import defaultdict
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    # Merge each group
    result = []
    for indices in groups.values():
        if len(indices) == 1:
            # Single polygon - keep as-is
            result.append(tuple_polygons[indices[0]])
        else:
            # Multiple polygons - merge with convex hull
            all_points = []
            for idx in indices:
                all_points.extend(tuple_polygons[idx])
            merged = convex_hull(all_points)
            result.append(merged)

    return result


def get_all_grouping_boundaries(
    grouping_names: List[str],
    cache_dir: Optional[Path] = None,  # Kept for API compatibility, not used
    preset_dir: Optional[Path] = None,
    boundaries_dir: Optional[Path] = None,
    max_workers: int = 8,  # Kept for API compatibility, not used
    unified_airport_data: Optional[Dict[str, Any]] = None,  # For Tower circle generation
) -> Dict[str, List[List[Tuple[float, float]]]]:
    """
    Get boundaries for multiple groupings from pre-downloaded SimAware data.

    Uses position prefix data from preset groupings to find the correct
    SimAware boundary files.

    Args:
        grouping_names: List of grouping names
        cache_dir: Deprecated, kept for API compatibility
        preset_dir: Optional path to preset groupings directory
        boundaries_dir: Optional path to SimAware boundaries directory
        max_workers: Deprecated, kept for API compatibility

    Returns:
        Dict mapping grouping names to list of boundary polygons.
        Each grouping can have multiple separate polygons if they're not neighbors.
        Each polygon is a list of (lat, lon) tuples.
    """
    # Load all preset grouping data with position prefixes
    all_grouping_data = load_preset_grouping_data(preset_dir)

    # Load all SimAware boundary data
    boundaries_data = load_simaware_boundaries(boundaries_dir)

    if not boundaries_data:
        print("  Warning: No SimAware boundary data found")
        print(f"  Run 'python scripts/generate_simaware_boundaries.py' to download")
        return {}

    # Map each grouping to its boundary polygons
    boundaries: Dict[str, List[List[Tuple[float, float]]]] = {}

    for name in grouping_names:
        # Check if this is a Tower grouping - use circle polygon
        if name.endswith(' Tower') and unified_airport_data:
            grouping_data = all_grouping_data.get(name)
            if grouping_data:
                airports = grouping_data.get('airports', [])
                if airports:
                    # Get coordinates of the first (usually only) airport
                    airport_code = airports[0]
                    airport_info = unified_airport_data.get(airport_code, {})
                    lat = airport_info.get('latitude')
                    lon = airport_info.get('longitude')
                    if lat and lon:
                        circle = generate_circle_polygon(lat, lon, radius_nm=5)
                        boundaries[name] = [circle]
                        continue  # Skip to next grouping

        # Check for '+' pattern
        expanded_names = expand_plus_pattern(name, all_grouping_data)

        if len(expanded_names) > 1:
            # Combined grouping - collect all polygons
            all_polygons = []
            for exp_name in expanded_names:
                grouping_data = all_grouping_data.get(exp_name)
                polygons = map_grouping_to_boundaries(
                    exp_name, grouping_data, boundaries_data
                )
                all_polygons.extend(polygons)

            if all_polygons:
                # combine_polygons now returns list of polygons (may be >1 if there are gaps)
                boundaries[name] = combine_polygons(all_polygons)
        else:
            # Single grouping
            grouping_data = all_grouping_data.get(name)
            polygons = map_grouping_to_boundaries(
                name, grouping_data, boundaries_data
            )

            if polygons:
                # combine_polygons handles all cases (1 or more input polygons)
                boundaries[name] = combine_polygons(polygons)

    matched = len(boundaries)
    if matched > 0:
        print(f"  Matched {matched}/{len(grouping_names)} groupings to SimAware boundaries")

    return boundaries


if __name__ == "__main__":
    # Test the boundary loader
    from pathlib import Path

    # Test groupings from preset_groupings with various ARTCCs
    test_groupings = [
        # ZOA (Oakland Center) - tests position prefix mapping
        "NCT A",        # Should map to SJC via prefix SJC/MRY
        "NCT B",        # Should map to SFO via prefix SFO
        "NCT C",        # Should map to OAK + MOD (combined) via prefixes
        "NCT D",        # Should map to SFO_DEP via DEP suffix
        "NCT E",        # Should map to SMF
        "NCT R",        # Should map to RNO
        "NCT Combined",
        "NCT E+R",      # Combined sectors
        "FAT",          # Was "FAT FAT", now simplified
        "O90",          # Was "O90 O90", now simplified
        "O90 SFO",

        # ZLA (Los Angeles Center)
        "SCT Burbank",
        "SCT Empire",
        "SCT Coast",
        "SCT Consolidated",
        "L30",          # Was "L30 L30", now simplified
        "L30 Las Vegas Tower",

        # ZNY (New York Center)
        "N90 Kennedy",
        "N90 LaGuardia",
        "N90 New York Combined",

        # ZDC (Washington Center)
        "PCT Consolidated",
        "PCT Mount Vernon",
        "PCT Shenandoah",

        # ZSE (Seattle Center)
        "S46 Seattle Area",

        # International (should fall back to convex hull)
        "International - Mexico",
    ]

    print("Testing SimAware boundary loader with position prefix data...")
    print("=" * 60)

    # Load and display grouping data
    grouping_data = load_preset_grouping_data()
    print(f"\nLoaded {len(grouping_data)} groupings with metadata")

    # Load boundary data
    boundaries_data = load_simaware_boundaries()
    print(f"Loaded {len(boundaries_data)} SimAware facility files")

    # Show which facilities have NCT prefixes
    nct_boundaries = boundaries_data.get('NCT', {})
    print(f"\nNCT facility has {len(nct_boundaries)} boundary files:")
    for key in sorted(nct_boundaries.keys()):
        print(f"  - {key}")

    # Show sample data
    print("\nSample grouping data:")
    for name in ["NCT D", "NCT C", "NCT E", "SCT Burbank", "FAT FAT"]:
        if name in grouping_data:
            data = grouping_data[name]
            print(f"\n{name}:")
            print(f"  prefixes: {data.get('position_prefixes')}")
            print(f"  suffixes: {data.get('position_suffixes')}")
            print(f"  facility: {data.get('facility_id')}")

    print("\n" + "=" * 60)
    print("Fetching boundaries...")

    boundaries = get_all_grouping_boundaries(test_groupings)

    print(f"\nResults for {len(test_groupings)} test groupings:")
    for name in test_groupings:
        if name in boundaries:
            polygons = boundaries[name]
            # Check how many prefixes this grouping has
            gdata = grouping_data.get(name, {})
            prefixes = gdata.get('position_prefixes', [])
            if len(polygons) == 1:
                if prefixes and len(prefixes) > 1:
                    print(f"  {name}: {len(polygons[0])} points (merged from prefixes: {prefixes})")
                else:
                    print(f"  {name}: {len(polygons[0])} points")
            else:
                # Multiple separate polygons (not neighbors)
                point_counts = [len(p) for p in polygons]
                print(f"  {name}: {len(polygons)} separate polygons ({point_counts} points)")
        else:
            print(f"  {name}: NO MATCH (will use convex hull)")
