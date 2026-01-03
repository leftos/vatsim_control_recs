"""
Interactive ARTCC Map Index Generator

Generates an index.html page with an interactive Leaflet.js map
showing ARTCC boundaries that link to weather briefings.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import DaemonConfig, ARTCC_NAMES, CATEGORY_COLORS
from .artcc_boundaries import get_artcc_boundaries, get_artcc_center

# Continental US ARTCCs to display on the map (excludes oceanic/remote)
CONUS_ARTCCS = {
    'ZAB', 'ZAU', 'ZBW', 'ZDC', 'ZDV', 'ZFW', 'ZHU', 'ZID', 'ZJX',
    'ZKC', 'ZLA', 'ZLC', 'ZMA', 'ZME', 'ZMP', 'ZNY', 'ZOA', 'ZOB',
    'ZSE', 'ZTL',
}


def compute_convex_hull(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """
    Compute the convex hull of a set of 2D points using Graham scan.

    Args:
        points: List of (lat, lon) tuples

    Returns:
        List of points forming the convex hull in counter-clockwise order
    """
    if len(points) < 3:
        return points

    # Remove duplicates
    points = list(set(points))
    if len(points) < 3:
        return points

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    # Sort points lexicographically
    points = sorted(points)

    # Build lower hull
    lower = []
    for p in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    # Build upper hull
    upper = []
    for p in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    # Concatenate (remove last point of each half because it's repeated)
    return lower[:-1] + upper[:-1]


def point_in_polygon(point: Tuple[float, float], polygon: List[Tuple[float, float]]) -> bool:
    """
    Check if a point is inside a polygon using ray casting algorithm.

    Args:
        point: (lat, lon) tuple
        polygon: List of (lat, lon) tuples forming the polygon

    Returns:
        True if point is inside polygon
    """
    x, y = point
    n = len(polygon)
    inside = False

    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]

        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i

    return inside


def generate_weather_regions(
    artcc_boundary: List[Tuple[float, float]],
    airport_points: List[Dict],
    grid_resolution: float = 0.25,  # degrees
) -> List[Dict]:
    """
    Generate Voronoi-style weather regions within an ARTCC boundary.

    Uses a grid-based approach: divides the ARTCC into small cells,
    assigns each cell to the nearest airport, then outputs colored regions.

    Args:
        artcc_boundary: List of (lat, lon) tuples forming the ARTCC polygon
        airport_points: List of {icao, lat, lon, category} dicts
        grid_resolution: Size of grid cells in degrees

    Returns:
        List of {coords: [[lon, lat], ...], category: str} for each region
    """
    if not airport_points or not artcc_boundary:
        return []

    # Get bounding box of ARTCC
    lats = [p[0] for p in artcc_boundary]
    lons = [p[1] for p in artcc_boundary]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    # Create grid and assign each cell to nearest airport
    # Max distance squared for interpolation (~0.7 degrees, about 40-50nm)
    # Cells farther than this from any airport won't be colored (avoids ocean fill)
    max_dist_sq = 0.5  # 0.7^2 ≈ 0.5

    grid_cells = []  # [(center_lat, center_lon, nearest_airport_idx)]

    lat = min_lat + grid_resolution / 2
    while lat < max_lat:
        lon = min_lon + grid_resolution / 2
        while lon < max_lon:
            # Check if cell center is inside ARTCC
            if point_in_polygon((lat, lon), artcc_boundary):
                # Find nearest airport
                min_dist = float('inf')
                nearest_idx = 0
                for idx, ap in enumerate(airport_points):
                    dist = (lat - ap['lat'])**2 + (lon - ap['lon'])**2
                    if dist < min_dist:
                        min_dist = dist
                        nearest_idx = idx
                # Only include cell if it's close enough to an airport
                if min_dist <= max_dist_sq:
                    grid_cells.append((lat, lon, nearest_idx))
            lon += grid_resolution
        lat += grid_resolution

    if not grid_cells:
        return []

    # Group cells by airport and create region polygons
    # For simplicity, output individual cell polygons (they'll merge visually)
    half_res = grid_resolution / 2
    regions = []

    for lat, lon, airport_idx in grid_cells:
        category = airport_points[airport_idx].get('category', 'UNK')
        # Create cell polygon (GeoJSON format: [lon, lat])
        cell_coords = [
            [lon - half_res, lat - half_res],
            [lon + half_res, lat - half_res],
            [lon + half_res, lat + half_res],
            [lon - half_res, lat + half_res],
            [lon - half_res, lat - half_res],  # Close polygon
        ]
        regions.append({
            'coords': cell_coords,
            'category': category,
        })

    return regions


def add_buffer_to_polygon(points: List[Tuple[float, float]], buffer_nm: float = 20) -> List[Tuple[float, float]]:
    """
    Add a buffer around a polygon by expanding it outward.

    Args:
        points: List of (lat, lon) tuples forming the convex hull
        buffer_nm: Buffer distance in nautical miles

    Returns:
        Expanded polygon points
    """
    if len(points) < 3:
        # For single point or line, create a circle/rectangle
        if len(points) == 1:
            lat, lon = points[0]
            # Convert nm to approximate degrees (1 degree lat ≈ 60 nm)
            buffer_deg = buffer_nm / 60
            # Create octagon around single point
            return [
                (lat + buffer_deg, lon),
                (lat + buffer_deg * 0.7, lon + buffer_deg * 0.7),
                (lat, lon + buffer_deg),
                (lat - buffer_deg * 0.7, lon + buffer_deg * 0.7),
                (lat - buffer_deg, lon),
                (lat - buffer_deg * 0.7, lon - buffer_deg * 0.7),
                (lat, lon - buffer_deg),
                (lat + buffer_deg * 0.7, lon - buffer_deg * 0.7),
            ]
        elif len(points) == 2:
            # For two points, create a rounded rectangle
            lat1, lon1 = points[0]
            lat2, lon2 = points[1]
            buffer_deg = buffer_nm / 60
            # Simple bounding box with buffer
            min_lat = min(lat1, lat2) - buffer_deg
            max_lat = max(lat1, lat2) + buffer_deg
            min_lon = min(lon1, lon2) - buffer_deg
            max_lon = max(lon1, lon2) + buffer_deg
            return [
                (max_lat, min_lon),
                (max_lat, max_lon),
                (min_lat, max_lon),
                (min_lat, min_lon),
            ]

    # Calculate centroid
    centroid_lat = sum(p[0] for p in points) / len(points)
    centroid_lon = sum(p[1] for p in points) / len(points)

    # Expand each point away from centroid
    buffer_deg = buffer_nm / 60  # Approximate conversion
    expanded = []

    for lat, lon in points:
        # Vector from centroid to point
        dlat = lat - centroid_lat
        dlon = lon - centroid_lon

        # Normalize and scale
        dist = math.sqrt(dlat * dlat + dlon * dlon)
        if dist > 0:
            scale = (dist + buffer_deg) / dist
            new_lat = centroid_lat + dlat * scale
            new_lon = centroid_lon + dlon * scale
        else:
            new_lat, new_lon = lat, lon

        expanded.append((new_lat, new_lon))

    return expanded


def generate_index_page(
    config: DaemonConfig,
    artcc_groupings: Dict[str, List[Dict[str, Any]]],
    unified_airport_data: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    """
    Generate the interactive index page with ARTCC map.

    Args:
        config: Daemon configuration
        artcc_groupings: Dict mapping ARTCC codes to lists of grouping info dicts
        unified_airport_data: Optional airport data for augmenting weather coverage

    Returns:
        Path to generated index file, or None on error
    """
    print("  Generating interactive index page...")

    # Get ARTCC boundaries
    boundaries = get_artcc_boundaries(config.artcc_cache_dir)

    # Calculate overall category stats per ARTCC
    artcc_stats: Dict[str, Dict[str, int]] = {}
    for artcc, groupings in artcc_groupings.items():
        artcc_stats[artcc] = {"LIFR": 0, "IFR": 0, "MVFR": 0, "VFR": 0, "UNK": 0, "total": 0}
        for g in groupings:
            cats = g.get('categories', {})
            for cat, count in cats.items():
                artcc_stats[artcc][cat] = artcc_stats[artcc].get(cat, 0) + count
                artcc_stats[artcc]['total'] += count

    # Generate timestamp
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%SZ")

    # Build the HTML
    html_content = generate_html(
        boundaries=boundaries,
        artcc_groupings=artcc_groupings,
        artcc_stats=artcc_stats,
        timestamp=timestamp,
        unified_airport_data=unified_airport_data,
    )

    # Write index file
    index_path = config.output_dir / "index.html"
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"    Created {index_path}")
    return index_path


def get_artcc_color(stats: Dict[str, int]) -> str:
    """
    Determine ARTCC color based on worst conditions present.

    Returns hex color for map polygon fill.
    """
    if stats.get("LIFR", 0) > 0:
        return "#ff00ff"  # Magenta
    elif stats.get("IFR", 0) > 0:
        return "#ff0000"  # Red
    elif stats.get("MVFR", 0) > 0:
        return "#5599ff"  # Blue
    elif stats.get("VFR", 0) > 0:
        return "#00ff00"  # Green
    return "#888888"  # Gray for no data


def generate_html(
    boundaries: Dict[str, List[List[tuple]]],
    artcc_groupings: Dict[str, List[Dict[str, Any]]],
    artcc_stats: Dict[str, Dict[str, int]],
    timestamp: str,
    unified_airport_data: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate the complete HTML content."""

    # Collect all airport weather points per ARTCC (de-duplicated by ICAO)
    artcc_airport_points: Dict[str, Dict[str, Dict]] = {}  # artcc -> icao -> point
    for artcc, groupings in artcc_groupings.items():
        if artcc not in CONUS_ARTCCS and artcc != "custom":
            continue
        if artcc not in artcc_airport_points:
            artcc_airport_points[artcc] = {}
        for g in groupings:
            for point in g.get('airport_weather_points', []):
                icao = point.get('icao')
                if icao:
                    artcc_airport_points[artcc][icao] = point

    # Augment with ALL airports from unified_airport_data for each ARTCC
    # This fills in coverage gaps by using all known airports, not just those in groupings
    if unified_airport_data:
        for icao, airport_info in unified_airport_data.items():
            artcc = airport_info.get('artcc', '')
            if not artcc or artcc not in CONUS_ARTCCS:
                continue
            # Skip if we already have this airport with weather data
            if artcc in artcc_airport_points and icao in artcc_airport_points[artcc]:
                continue
            # Get coordinates
            lat = airport_info.get('latitude')
            lon = airport_info.get('longitude')
            if lat is None or lon is None:
                continue
            # Add airport without weather data (will be interpolated)
            if artcc not in artcc_airport_points:
                artcc_airport_points[artcc] = {}
            artcc_airport_points[artcc][icao] = {
                'icao': icao,
                'lat': lat,
                'lon': lon,
                'category': None,  # No weather data - will use interpolation
            }

        # Now interpolate categories for airports without valid weather data
        # Only use VFR/MVFR/IFR/LIFR as sources - exclude UNK since it means no weather
        valid_categories = {'VFR', 'MVFR', 'IFR', 'LIFR'}
        for artcc, airports in artcc_airport_points.items():
            # Get airports with known VALID weather (not UNK)
            known_weather = [(icao, ap) for icao, ap in airports.items()
                             if ap.get('category') in valid_categories]
            if not known_weather:
                continue  # No valid weather data at all for this ARTCC

            # For airports without valid weather, find nearest with valid weather
            for icao, ap in airports.items():
                if ap.get('category') in valid_categories:
                    continue  # Already has valid weather
                # Find nearest airport with valid weather
                min_dist = float('inf')
                nearest_category = 'VFR'  # Default to VFR if no weather found nearby
                for known_icao, known_ap in known_weather:
                    dist = (ap['lat'] - known_ap['lat'])**2 + (ap['lon'] - known_ap['lon'])**2
                    if dist < min_dist:
                        min_dist = dist
                        nearest_category = known_ap.get('category', 'VFR')
                ap['category'] = nearest_category

    # Generate weather region GeoJSON features (Voronoi-style grid cells)
    # Each ARTCC is divided into a grid, with each cell colored by nearest airport's weather
    weather_region_features = []
    for artcc, polys in boundaries.items():
        if artcc not in CONUS_ARTCCS:
            continue
        airport_points = list(artcc_airport_points.get(artcc, {}).values())
        if not airport_points:
            continue

        # Process ALL boundary polygons for this ARTCC (some have multiple)
        for artcc_boundary in polys:
            regions = generate_weather_regions(artcc_boundary, airport_points, grid_resolution=0.15)

            for region in regions:
                feature = {
                    "type": "Feature",
                    "properties": {
                        "category": region['category'],
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [region['coords']],
                    }
                }
                weather_region_features.append(feature)

    weather_regions_geojson = {
        "type": "FeatureCollection",
        "features": weather_region_features,
    }

    # Convert boundaries to GeoJSON for Leaflet (CONUS only)
    # Now using neutral styling - borders only
    geojson_features = []
    for artcc, polys in boundaries.items():
        # Skip non-CONUS ARTCCs (oceanic, Alaska, Hawaii, etc.)
        if artcc not in CONUS_ARTCCS:
            continue
        stats = artcc_stats.get(artcc, {})
        display_name = ARTCC_NAMES.get(artcc, artcc)
        grouping_count = len(artcc_groupings.get(artcc, []))

        for poly in polys:
            # GeoJSON uses [lon, lat] order
            coords = [[p[1], p[0]] for p in poly]
            # Close the polygon if not already closed
            if coords and coords[0] != coords[-1]:
                coords.append(coords[0])

            feature = {
                "type": "Feature",
                "properties": {
                    "artcc": artcc,
                    "name": display_name,
                    "groupings": grouping_count,
                    "lifr": stats.get("LIFR", 0),
                    "ifr": stats.get("IFR", 0),
                    "mvfr": stats.get("MVFR", 0),
                    "vfr": stats.get("VFR", 0),
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [coords],
                }
            }
            geojson_features.append(feature)

    geojson = {
        "type": "FeatureCollection",
        "features": geojson_features,
    }

    # Build ARTCC bounds for zoom reference (CONUS only)
    artcc_bounds = {}
    for artcc, polys in boundaries.items():
        if artcc not in CONUS_ARTCCS:
            continue
        all_coords = []
        for poly in polys:
            all_coords.extend(poly)
        if all_coords:
            lats = [c[0] for c in all_coords]
            lons = [c[1] for c in all_coords]
            artcc_bounds[artcc] = {
                'south': min(lats),
                'north': max(lats),
                'west': min(lons),
                'east': max(lons),
            }

    # Build grouping polygons data for hover effect
    # Use same iteration order as sidebar builder (sorted ARTCCs, then custom)
    grouping_polygons = {}
    grouping_id = 0

    sorted_artccs = sorted(
        [a for a in artcc_groupings.keys() if a != "custom"],
        key=lambda x: ARTCC_NAMES.get(x, x)
    )

    for artcc in sorted_artccs:
        groupings = artcc_groupings[artcc]
        for g in sorted(groupings, key=lambda x: x['name']):
            coords = g.get('airport_coords', [])
            if coords:
                # Convert to tuples for convex hull
                coord_tuples = [(c[0], c[1]) for c in coords]
                # Compute convex hull and add buffer
                hull = compute_convex_hull(coord_tuples)
                buffered = add_buffer_to_polygon(hull, buffer_nm=15)
                # Convert to GeoJSON format [lon, lat]
                polygon_coords = [[p[1], p[0]] for p in buffered]
                # Close the polygon
                if polygon_coords and polygon_coords[0] != polygon_coords[-1]:
                    polygon_coords.append(polygon_coords[0])

                grouping_polygons[str(grouping_id)] = {
                    'name': g['name'],
                    'artcc': artcc,
                    'coords': polygon_coords,
                }
            grouping_id += 1

    # Add custom groupings at the end
    if "custom" in artcc_groupings:
        for g in sorted(artcc_groupings["custom"], key=lambda x: x['name']):
            coords = g.get('airport_coords', [])
            if coords:
                coord_tuples = [(c[0], c[1]) for c in coords]
                hull = compute_convex_hull(coord_tuples)
                buffered = add_buffer_to_polygon(hull, buffer_nm=15)
                polygon_coords = [[p[1], p[0]] for p in buffered]
                if polygon_coords and polygon_coords[0] != polygon_coords[-1]:
                    polygon_coords.append(polygon_coords[0])
                grouping_polygons[str(grouping_id)] = {
                    'name': g['name'],
                    'artcc': None,  # No ARTCC for unmapped custom groupings
                    'coords': polygon_coords,
                }
            grouping_id += 1

    # Build groupings sidebar data
    sidebar_html = build_sidebar_html(artcc_groupings, artcc_stats)

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VATSIM Weather Briefings</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <style>
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #1a1a2e;
            color: #e0e0e0;
            height: 100vh;
            overflow: hidden;
        }}

        .container {{
            display: flex;
            height: 100vh;
        }}

        #map {{
            flex: 1;
            height: 100%;
        }}

        .sidebar {{
            width: 350px;
            background: #16213e;
            overflow-y: auto;
            border-left: 2px solid #0f3460;
        }}

        .sidebar-header {{
            padding: 20px;
            background: #0f3460;
            position: sticky;
            top: 0;
            z-index: 100;
        }}

        .sidebar-header h1 {{
            font-size: 1.2rem;
            margin-bottom: 5px;
        }}

        .sidebar-header .timestamp {{
            font-size: 0.85rem;
            color: #888;
        }}

        .legend {{
            display: flex;
            gap: 10px;
            margin-top: 10px;
            flex-wrap: wrap;
        }}

        .legend-item {{
            display: flex;
            align-items: center;
            gap: 5px;
            font-size: 0.8rem;
        }}

        .legend-color {{
            width: 16px;
            height: 16px;
            border-radius: 3px;
        }}

        .artcc-section {{
            border-bottom: 1px solid #0f3460;
        }}

        .artcc-header {{
            padding: 12px 20px;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: #1a1a2e;
            transition: background 0.2s;
        }}

        .artcc-header:hover {{
            background: #252545;
        }}

        .artcc-header.active {{
            background: #0f3460;
        }}

        .artcc-name {{
            font-weight: 600;
        }}

        .artcc-code {{
            color: #888;
            font-size: 0.85rem;
            margin-left: 8px;
        }}

        .artcc-stats {{
            display: flex;
            gap: 8px;
            font-size: 0.8rem;
        }}

        .stat {{
            padding: 2px 6px;
            border-radius: 3px;
            font-weight: 600;
        }}

        .stat-lifr {{ background: rgba(255, 0, 255, 0.3); color: #ff77ff; }}
        .stat-ifr {{ background: rgba(255, 0, 0, 0.3); color: #ff6666; }}
        .stat-mvfr {{ background: rgba(85, 153, 255, 0.3); color: #77aaff; }}
        .stat-vfr {{ background: rgba(0, 255, 0, 0.3); color: #66ff66; }}

        .groupings-list {{
            display: none;
            padding: 0 20px 15px 20px;
            background: #1a1a2e;
        }}

        .groupings-list.open {{
            display: block;
        }}

        .grouping-link {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 12px;
            margin: 4px 0;
            background: #252545;
            border-radius: 4px;
            text-decoration: none;
            color: #e0e0e0;
            transition: background 0.2s, transform 0.1s;
        }}

        .grouping-link:hover {{
            background: #353565;
            transform: translateX(3px);
        }}

        .grouping-name {{
            font-size: 0.9rem;
        }}

        .grouping-airports {{
            font-size: 0.75rem;
            color: #888;
        }}

        .custom-marker {{
            color: #ffaa00;
            font-size: 0.7rem;
            margin-left: 4px;
        }}

        .custom-section {{
            margin-top: 20px;
        }}

        .custom-section .artcc-header {{
            background: #1a2a4e;
        }}

        .artcc-briefing-link {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 12px;
            margin: 4px 0 8px 0;
            background: linear-gradient(135deg, #0f3460 0%, #1a4a8e 100%);
            border-radius: 4px;
            text-decoration: none;
            color: #e0e0e0;
            transition: background 0.2s, transform 0.1s;
            border: 1px solid #2a5a9e;
            font-weight: 600;
        }}

        .artcc-briefing-link:hover {{
            background: linear-gradient(135deg, #1a4a8e 0%, #2a6ace 100%);
            transform: translateX(3px);
        }}

        .artcc-briefing-link .icon {{
            font-size: 1.1rem;
        }}

        .awc-briefing-link {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 12px;
            margin: 4px 0;
            background: linear-gradient(135deg, #2d4a2d 0%, #3d6a3d 100%);
            border-radius: 4px;
            text-decoration: none;
            color: #e0e0e0;
            transition: background 0.2s, transform 0.1s;
            border: 1px solid #4a8a4a;
            font-size: 0.85rem;
        }}

        .awc-briefing-link:hover {{
            background: linear-gradient(135deg, #3d6a3d 0%, #4d8a4d 100%);
            transform: translateX(3px);
        }}

        .awc-briefing-link .icon {{
            font-size: 1rem;
        }}

        /* Modal/Popup styles */
        .modal-overlay {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: #1a1a1a;
            z-index: 1000;
            justify-content: center;
            align-items: center;
        }}

        .modal-overlay.active {{
            display: flex;
        }}

        .modal-container {{
            position: relative;
            width: 90%;
            height: 90%;
            max-width: 1200px;
            background: #16213e;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
        }}

        .modal-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 20px;
            background: #0f3460;
            border-bottom: 2px solid #1a4a8e;
        }}

        .modal-title {{
            font-size: 1.1rem;
            font-weight: 600;
            color: #e0e0e0;
        }}

        .modal-close {{
            background: none;
            border: none;
            color: #888;
            font-size: 1.5rem;
            cursor: pointer;
            padding: 4px 8px;
            border-radius: 4px;
            transition: color 0.2s, background 0.2s;
        }}

        .modal-close:hover {{
            color: #fff;
            background: rgba(255, 255, 255, 0.1);
        }}

        .modal-actions {{
            display: flex;
            gap: 10px;
            align-items: center;
        }}

        .modal-open-tab {{
            background: #1a4a8e;
            color: #e0e0e0;
            border: none;
            padding: 6px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.85rem;
            transition: background 0.2s;
        }}

        .modal-open-tab:hover {{
            background: #2a6ace;
        }}

        .modal-body {{
            height: calc(100% - 56px);
        }}

        .modal-iframe {{
            width: 100%;
            height: 100%;
            border: none;
        }}

        /* Leaflet customizations */
        .leaflet-container {{
            background: #1a1a2e;
        }}

        .airport-tooltip {{
            background: #16213e;
            color: #e0e0e0;
            border: 1px solid #0f3460;
            border-radius: 4px;
            padding: 4px 8px;
            font-size: 0.8rem;
            font-weight: 600;
        }}

        .airport-tooltip::before {{
            border-top-color: #0f3460;
        }}

        .leaflet-popup-content-wrapper {{
            background: #16213e;
            color: #e0e0e0;
            border-radius: 8px;
        }}

        .leaflet-popup-tip {{
            background: #16213e;
        }}

        .artcc-popup {{
            min-width: 200px;
        }}

        .artcc-popup h3 {{
            margin-bottom: 10px;
            padding-bottom: 8px;
            border-bottom: 1px solid #0f3460;
        }}

        .artcc-popup .stats {{
            display: flex;
            gap: 8px;
            margin-bottom: 10px;
        }}

        .artcc-popup a {{
            display: block;
            padding: 8px;
            background: #0f3460;
            color: #e0e0e0;
            text-decoration: none;
            border-radius: 4px;
            text-align: center;
            margin-top: 10px;
        }}

        .artcc-popup a:hover {{
            background: #1a4a8e;
        }}

        @media (max-width: 768px) {{
            .container {{
                flex-direction: column;
            }}

            .sidebar {{
                width: 100%;
                height: 50vh;
                border-left: none;
                border-top: 2px solid #0f3460;
            }}

            #map {{
                height: 50vh;
            }}
        }}
    </style>
</head>
<body>
    <!-- Modal Overlay -->
    <div id="briefing-modal" class="modal-overlay">
        <div class="modal-container">
            <div class="modal-header">
                <span class="modal-title" id="modal-title">Weather Briefing</span>
                <div class="modal-actions">
                    <button class="modal-open-tab" id="modal-open-tab">Open in New Tab</button>
                    <button class="modal-close" id="modal-close">&times;</button>
                </div>
            </div>
            <div class="modal-body">
                <iframe id="modal-iframe" class="modal-iframe"></iframe>
            </div>
        </div>
    </div>

    <div class="container">
        <div id="map"></div>
        <div class="sidebar">
            <div class="sidebar-header">
                <h1>VATSIM Weather Briefings</h1>
                <div class="timestamp">Updated: {timestamp}</div>
                <div class="legend">
                    <div class="legend-item">
                        <div class="legend-color" style="background: #ff00ff;"></div>
                        <span>LIFR</span>
                    </div>
                    <div class="legend-item">
                        <div class="legend-color" style="background: #ff0000;"></div>
                        <span>IFR</span>
                    </div>
                    <div class="legend-item">
                        <div class="legend-color" style="background: #5599ff;"></div>
                        <span>MVFR</span>
                    </div>
                    <div class="legend-item">
                        <div class="legend-color" style="background: #00ff00;"></div>
                        <span>VFR</span>
                    </div>
                </div>
            </div>
            {sidebar_html}
        </div>
    </div>

    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
        // Initialize map centered on CONUS
        const map = L.map('map', {{
            center: [39.0, -98.0],
            zoom: 4,
            minZoom: 3,
            maxZoom: 10,
        }});

        // Dark tile layer
        L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
            subdomains: 'abcd',
            maxZoom: 20
        }}).addTo(map);

        // ARTCC boundaries GeoJSON
        const artccData = {json.dumps(geojson)};

        // Weather region cells (Voronoi-style grid)
        const weatherRegions = {json.dumps(weather_regions_geojson)};

        // Category colors
        const categoryColors = {{
            'LIFR': '#ff00ff',
            'IFR': '#ff0000',
            'MVFR': '#5599ff',
            'VFR': '#00ff00',
            'UNK': '#888888'
        }};

        // Style function for weather region cells
        function weatherRegionStyle(feature) {{
            const color = categoryColors[feature.properties.category] || categoryColors['UNK'];
            return {{
                fillColor: color,
                fillOpacity: 0.5,
                stroke: false,  // No border lines
            }};
        }}

        // Style function for ARTCC polygons - borders only
        function artccStyle(feature) {{
            return {{
                fillColor: 'transparent',
                weight: 2,
                opacity: 0.7,
                color: '#ffffff',
                fillOpacity: 0,
            }};
        }}

        // Highlight on hover
        function highlightFeature(e) {{
            const layer = e.target;
            layer.setStyle({{
                weight: 3,
                opacity: 1,
                color: '#ffffff',
            }});
            layer.bringToFront();
        }}

        function resetHighlight(e) {{
            geojsonLayer.resetStyle(e.target);
        }}

        // Click handler - scroll to ARTCC in sidebar
        function onArtccClick(e) {{
            const artcc = e.target.feature.properties.artcc;
            const section = document.querySelector(`[data-artcc="${{artcc}}"]`);
            if (section) {{
                // Close all other sections
                document.querySelectorAll('.groupings-list').forEach(el => el.classList.remove('open'));
                document.querySelectorAll('.artcc-header').forEach(el => el.classList.remove('active'));

                // Open this section
                const header = section.querySelector('.artcc-header');
                const list = section.querySelector('.groupings-list');
                if (header && list) {{
                    header.classList.add('active');
                    list.classList.add('open');
                }}

                section.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
            }}
        }}

        function onEachFeature(feature, layer) {{
            const props = feature.properties;

            // Build popup content
            let statsHtml = '<div class="stats">';
            if (props.lifr > 0) statsHtml += `<span class="stat stat-lifr">${{props.lifr}} LIFR</span>`;
            if (props.ifr > 0) statsHtml += `<span class="stat stat-ifr">${{props.ifr}} IFR</span>`;
            if (props.mvfr > 0) statsHtml += `<span class="stat stat-mvfr">${{props.mvfr}} MVFR</span>`;
            if (props.vfr > 0) statsHtml += `<span class="stat stat-vfr">${{props.vfr}} VFR</span>`;
            statsHtml += '</div>';

            const popupContent = `
                <div class="artcc-popup">
                    <h3>${{props.name}} (${{props.artcc}})</h3>
                    ${{statsHtml}}
                    <div>${{props.groupings}} grouping(s) available</div>
                </div>
            `;

            layer.bindPopup(popupContent);

            layer.on({{
                mouseover: highlightFeature,
                mouseout: resetHighlight,
                click: onArtccClick,
            }});
        }}

        // Render weather region cells first (below ARTCC borders)
        // Use Canvas renderer to avoid anti-aliasing gaps between cells
        const weatherRegionLayer = L.geoJSON(weatherRegions, {{
            style: weatherRegionStyle,
            interactive: false,  // Don't capture mouse events
            renderer: L.canvas(),
        }}).addTo(map);

        // ARTCC borders on top
        const geojsonLayer = L.geoJSON(artccData, {{
            style: artccStyle,
            onEachFeature: onEachFeature,
        }}).addTo(map);

        // Grouping polygons for hover effect
        const groupingPolygons = {json.dumps(grouping_polygons)};

        // ARTCC bounds for stable zooming
        const artccBounds = {json.dumps(artcc_bounds)};

        // Variable to hold the current hover polygon layer
        let hoverPolygon = null;

        // Function to show grouping polygon on hover
        function showGroupingPolygon(groupingId) {{
            // Remove existing hover polygon
            if (hoverPolygon) {{
                map.removeLayer(hoverPolygon);
                hoverPolygon = null;
            }}

            const data = groupingPolygons[groupingId];
            if (data && data.coords && data.coords.length > 0) {{
                // Create polygon from coordinates
                // Leaflet expects [lat, lon] but our coords are [lon, lat]
                const latLngs = data.coords.map(c => [c[1], c[0]]);

                hoverPolygon = L.polygon(latLngs, {{
                    color: '#ffff00',
                    weight: 2,
                    fillColor: '#ffff00',
                    fillOpacity: 0.2,
                    dashArray: '5, 5',
                }}).addTo(map);

                // Zoom to ARTCC bounds (stable) instead of grouping bounds (jumpy)
                if (data.artcc && artccBounds[data.artcc]) {{
                    const bounds = artccBounds[data.artcc];
                    map.fitBounds([[bounds.south, bounds.west], [bounds.north, bounds.east]], {{ padding: [20, 20], maxZoom: 7 }});
                }}
            }}
        }}

        // Function to hide grouping polygon
        function hideGroupingPolygon() {{
            if (hoverPolygon) {{
                map.removeLayer(hoverPolygon);
                hoverPolygon = null;
            }}
        }}

        // Add hover listeners to grouping links
        document.querySelectorAll('.grouping-link').forEach(link => {{
            const groupingId = link.dataset.groupingId;
            if (groupingId) {{
                link.addEventListener('mouseenter', () => showGroupingPolygon(groupingId));
                link.addEventListener('mouseleave', hideGroupingPolygon);
            }}
        }});

        // Sidebar toggle functionality
        document.querySelectorAll('.artcc-header').forEach(header => {{
            header.addEventListener('click', () => {{
                const section = header.closest('.artcc-section');
                const list = section.querySelector('.groupings-list');
                const isOpen = list.classList.contains('open');

                // Close all
                document.querySelectorAll('.groupings-list').forEach(el => el.classList.remove('open'));
                document.querySelectorAll('.artcc-header').forEach(el => el.classList.remove('active'));

                // Toggle this one
                if (!isOpen) {{
                    list.classList.add('open');
                    header.classList.add('active');

                    // Pan map to this ARTCC
                    const artcc = section.dataset.artcc;
                    const feature = artccData.features.find(f => f.properties.artcc === artcc);
                    if (feature) {{
                        const bounds = L.geoJSON(feature).getBounds();
                        map.fitBounds(bounds, {{ padding: [50, 50] }});
                    }}
                }}
            }});
        }});

        // Function to scroll to ARTCC section from map popup
        function scrollToArtcc(artcc) {{
            const section = document.querySelector(`[data-artcc="${{artcc}}"]`);
            if (section) {{
                document.querySelectorAll('.groupings-list').forEach(el => el.classList.remove('open'));
                document.querySelectorAll('.artcc-header').forEach(el => el.classList.remove('active'));

                const header = section.querySelector('.artcc-header');
                const list = section.querySelector('.groupings-list');
                if (header && list) {{
                    header.classList.add('active');
                    list.classList.add('open');
                }}
                section.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
            }}
            map.closePopup();
        }}

        // Modal functionality
        const modal = document.getElementById('briefing-modal');
        const modalTitle = document.getElementById('modal-title');
        const modalIframe = document.getElementById('modal-iframe');
        const modalClose = document.getElementById('modal-close');
        const modalOpenTab = document.getElementById('modal-open-tab');
        let currentBriefingUrl = '';

        function openBriefingModal(url, title) {{
            currentBriefingUrl = url;
            modalTitle.textContent = title;
            modalIframe.src = url;
            modal.classList.add('active');
            document.body.style.overflow = 'hidden';
        }}

        function closeBriefingModal() {{
            modal.classList.remove('active');
            modalIframe.src = '';
            currentBriefingUrl = '';
            document.body.style.overflow = '';
        }}

        // Close modal handlers
        modalClose.addEventListener('click', closeBriefingModal);

        modal.addEventListener('click', (e) => {{
            if (e.target === modal) {{
                closeBriefingModal();
            }}
        }});

        document.addEventListener('keydown', (e) => {{
            if (e.key === 'Escape' && modal.classList.contains('active')) {{
                closeBriefingModal();
            }}
        }});

        // Open in new tab handler
        modalOpenTab.addEventListener('click', () => {{
            if (currentBriefingUrl) {{
                window.open(currentBriefingUrl, '_blank');
            }}
        }});

        // Intercept grouping link clicks to open in modal
        document.querySelectorAll('.grouping-link, .artcc-briefing-link').forEach(link => {{
            link.addEventListener('click', (e) => {{
                e.preventDefault();
                const url = link.getAttribute('href');
                const title = link.querySelector('.grouping-name, .artcc-briefing-name')?.textContent || 'Weather Briefing';
                openBriefingModal(url, title);
            }});
        }});
    </script>
</body>
</html>'''


def build_sidebar_html(
    artcc_groupings: Dict[str, List[Dict[str, Any]]],
    artcc_stats: Dict[str, Dict[str, int]],
) -> str:
    """Build the sidebar HTML with ARTCC sections and grouping links."""
    html_parts = []

    # Sort ARTCCs alphabetically, but put "custom" at the end
    sorted_artccs = sorted(
        [a for a in artcc_groupings.keys() if a != "custom"],
        key=lambda x: ARTCC_NAMES.get(x, x)
    )

    # Track grouping ID for hover polygon mapping
    grouping_id = 0

    for artcc in sorted_artccs:
        groupings = artcc_groupings[artcc]
        stats = artcc_stats.get(artcc, {})
        display_name = ARTCC_NAMES.get(artcc, artcc)

        # Build stats badges
        stats_html = ""
        if stats.get("LIFR", 0) > 0:
            stats_html += f'<span class="stat stat-lifr">{stats["LIFR"]}</span>'
        if stats.get("IFR", 0) > 0:
            stats_html += f'<span class="stat stat-ifr">{stats["IFR"]}</span>'
        if stats.get("MVFR", 0) > 0:
            stats_html += f'<span class="stat stat-mvfr">{stats["MVFR"]}</span>'
        if stats.get("VFR", 0) > 0:
            stats_html += f'<span class="stat stat-vfr">{stats["VFR"]}</span>'

        # Count total airports in this ARTCC
        total_airports = stats.get('total', 0)

        # Link to FAA/NWS real-world aviation weather briefing
        awc_briefing_html = f'''
                <a href="https://aviationweather.gov/pdwb/?cwsu={artcc.lower()}" target="_blank" class="awc-briefing-link">
                    <span class="awc-briefing-name">FAA Weather Briefing</span>
                    <span class="icon">🌐</span>
                </a>'''

        # ARTCC-wide briefing link at the top
        artcc_briefing_html = f'''
                <a href="{artcc}/_all.html" class="artcc-briefing-link">
                    <span class="artcc-briefing-name">All {display_name} Airports</span>
                    <span class="icon">📋</span>
                </a>'''

        # Build grouping links
        groupings_html = ""
        for g in sorted(groupings, key=lambda x: x['name']):
            airport_count = g.get('airport_count', 0)
            # Use path_prefix for custom groupings that were assigned to an ARTCC
            path_prefix = g.get('path_prefix', artcc)
            is_custom = g.get('is_custom', False)
            custom_marker = ' <span class="custom-marker">★</span>' if is_custom else ''
            groupings_html += f'''
                <a href="{path_prefix}/{g['filename']}" class="grouping-link" data-grouping-id="{grouping_id}">
                    <span class="grouping-name">{g['name']}{custom_marker}</span>
                    <span class="grouping-airports">{airport_count} airports</span>
                </a>'''
            grouping_id += 1

        html_parts.append(f'''
            <div class="artcc-section" data-artcc="{artcc}">
                <div class="artcc-header">
                    <div>
                        <span class="artcc-name">{display_name}</span>
                        <span class="artcc-code">{artcc}</span>
                    </div>
                    <div class="artcc-stats">{stats_html}</div>
                </div>
                <div class="groupings-list">
                    {awc_briefing_html}
                    {artcc_briefing_html}
                    {groupings_html}
                </div>
            </div>''')

    # Add custom groupings section if present (only for truly unmapped groupings)
    if "custom" in artcc_groupings:
        custom_groupings = artcc_groupings["custom"]
        stats = artcc_stats.get("custom", {})

        stats_html = ""
        if stats.get("LIFR", 0) > 0:
            stats_html += f'<span class="stat stat-lifr">{stats["LIFR"]}</span>'
        if stats.get("IFR", 0) > 0:
            stats_html += f'<span class="stat stat-ifr">{stats["IFR"]}</span>'
        if stats.get("MVFR", 0) > 0:
            stats_html += f'<span class="stat stat-mvfr">{stats["MVFR"]}</span>'
        if stats.get("VFR", 0) > 0:
            stats_html += f'<span class="stat stat-vfr">{stats["VFR"]}</span>'

        groupings_html = ""
        for g in sorted(custom_groupings, key=lambda x: x['name']):
            airport_count = g.get('airport_count', 0)
            groupings_html += f'''
                <a href="custom/{g['filename']}" class="grouping-link" data-grouping-id="{grouping_id}">
                    <span class="grouping-name">{g['name']}</span>
                    <span class="grouping-airports">{airport_count} airports</span>
                </a>'''
            grouping_id += 1

        html_parts.append(f'''
            <div class="artcc-section custom-section" data-artcc="custom">
                <div class="artcc-header">
                    <div>
                        <span class="artcc-name">Unmapped Groupings</span>
                    </div>
                    <div class="artcc-stats">{stats_html}</div>
                </div>
                <div class="groupings-list">
                    {groupings_html}
                </div>
            </div>''')

    return "\n".join(html_parts)
