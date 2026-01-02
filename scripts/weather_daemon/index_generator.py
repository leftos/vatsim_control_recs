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
) -> Optional[Path]:
    """
    Generate the interactive index page with ARTCC map.

    Args:
        config: Daemon configuration
        artcc_groupings: Dict mapping ARTCC codes to lists of grouping info dicts

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
) -> str:
    """Generate the complete HTML content."""

    # Convert boundaries to GeoJSON for Leaflet
    geojson_features = []
    for artcc, polys in boundaries.items():
        stats = artcc_stats.get(artcc, {})
        color = get_artcc_color(stats)
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
                    "color": color,
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

        /* Leaflet customizations */
        .leaflet-container {{
            background: #1a1a2e;
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

        // Style function for ARTCC polygons
        function artccStyle(feature) {{
            return {{
                fillColor: feature.properties.color,
                weight: 2,
                opacity: 0.8,
                color: '#ffffff',
                fillOpacity: 0.25,
            }};
        }}

        // Highlight on hover
        function highlightFeature(e) {{
            const layer = e.target;
            layer.setStyle({{
                weight: 3,
                fillOpacity: 0.4,
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
                    <a href="#" onclick="scrollToArtcc('${{props.artcc}}'); return false;">View Briefings</a>
                </div>
            `;

            layer.bindPopup(popupContent);

            layer.on({{
                mouseover: highlightFeature,
                mouseout: resetHighlight,
                click: onArtccClick,
            }});
        }}

        const geojsonLayer = L.geoJSON(artccData, {{
            style: artccStyle,
            onEachFeature: onEachFeature,
        }}).addTo(map);

        // Grouping polygons for hover effect
        const groupingPolygons = {json.dumps(grouping_polygons)};

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

                // Fit map to show the polygon
                map.fitBounds(hoverPolygon.getBounds(), {{ padding: [50, 50], maxZoom: 8 }});
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
