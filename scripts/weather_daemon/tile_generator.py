"""
Weather Overlay Tile Generator

Generates map tiles (z/x/y scheme) with weather category coloring.
Uses Web Mercator projection (EPSG:3857) compatible with Leaflet/OSM tiles.
"""

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image as PILImage

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore

from .config import CATEGORY_COLORS

logger = logging.getLogger("weather_daemon")

# Tile size in pixels (standard for web maps)
TILE_SIZE = 256

# Weather category colors as RGB tuples
CATEGORY_RGB = {
    'LIFR': (255, 0, 255),    # Magenta
    'IFR': (255, 0, 0),        # Red
    'MVFR': (85, 153, 255),    # Blue
    'VFR': (0, 255, 0),        # Green
    'UNK': (136, 136, 136),    # Gray
}

# Tile opacity (0-255)
TILE_OPACITY = 140  # ~55% opacity


@dataclass
class TileBounds:
    """Geographic bounds for a tile."""
    north: float
    south: float
    east: float
    west: float


def lon_to_tile_x(lon: float, zoom: int) -> int:
    """Convert longitude to tile X coordinate."""
    n = 2 ** zoom
    return int((lon + 180.0) / 360.0 * n)


def lat_to_tile_y(lat: float, zoom: int) -> int:
    """Convert latitude to tile Y coordinate (Web Mercator)."""
    n = 2 ** zoom
    lat_rad = math.radians(lat)
    return int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)


def tile_to_lon(x: int, zoom: int) -> float:
    """Convert tile X coordinate to longitude (west edge)."""
    n = 2 ** zoom
    return x / n * 360.0 - 180.0


def tile_to_lat(y: int, zoom: int) -> float:
    """Convert tile Y coordinate to latitude (north edge, Web Mercator)."""
    n = 2 ** zoom
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    return math.degrees(lat_rad)


def get_tile_bounds(x: int, y: int, zoom: int) -> TileBounds:
    """Get geographic bounds for a tile."""
    return TileBounds(
        north=tile_to_lat(y, zoom),
        south=tile_to_lat(y + 1, zoom),
        west=tile_to_lon(x, zoom),
        east=tile_to_lon(x + 1, zoom),
    )


def pixel_to_latlon(
    px: int,
    py: int,
    tile_x: int,
    tile_y: int,
    zoom: int,
) -> Tuple[float, float]:
    """Convert pixel coordinates within a tile to lat/lon."""
    n = 2 ** zoom

    # Calculate the fractional tile position
    x_frac = tile_x + px / TILE_SIZE
    y_frac = tile_y + py / TILE_SIZE

    # Convert to lon/lat
    lon = x_frac / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y_frac / n)))
    lat = math.degrees(lat_rad)

    return lat, lon


def point_in_polygon(point: Tuple[float, float], polygon: List[Tuple[float, float]]) -> bool:
    """Check if a point is inside a polygon using ray casting."""
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


def point_in_any_polygon(
    point: Tuple[float, float],
    polygons: List[List[Tuple[float, float]]],
) -> bool:
    """Check if a point is inside any of the given polygons."""
    for polygon in polygons:
        if point_in_polygon(point, polygon):
            return True
    return False


class WeatherTileGenerator:
    """Generates weather overlay tiles."""

    def __init__(
        self,
        artcc_boundaries: Dict[str, List[List[Tuple[float, float]]]],
        airport_weather: Dict[str, Dict[str, Any]],
        output_dir: Path,
        conus_artccs: Set[str],
        max_distance_deg: float = 0.7,
        zoom_levels: Tuple[int, ...] = (4, 5, 6, 7, 8, 9, 10),
    ):
        """
        Initialize the tile generator.

        Args:
            artcc_boundaries: Dict mapping ARTCC codes to list of boundary polygons
            airport_weather: Dict mapping ICAO codes to {lat, lon, category}
            output_dir: Directory to write tiles to
            conus_artccs: Set of ARTCC codes to include (CONUS only)
            max_distance_deg: Maximum distance from airport to color (in degrees)
            zoom_levels: Tuple of zoom levels to generate
        """
        self.artcc_boundaries = artcc_boundaries
        self.airport_weather = airport_weather
        self.output_dir = output_dir
        self.conus_artccs = conus_artccs
        self.max_distance_sq = max_distance_deg ** 2
        self.zoom_levels = zoom_levels

        # Build a combined list of all CONUS boundary polygons
        self.all_boundaries: List[List[Tuple[float, float]]] = []
        for artcc, polys in artcc_boundaries.items():
            if artcc in conus_artccs:
                self.all_boundaries.extend(polys)

        # Build a flat list of airports with valid weather for fast lookup
        self.airports_list: List[Tuple[str, float, float, str]] = []
        valid_categories = {'VFR', 'MVFR', 'IFR', 'LIFR'}
        for icao, data in airport_weather.items():
            cat = data.get('category')
            if cat in valid_categories:
                self.airports_list.append((
                    icao,
                    data['lat'],
                    data['lon'],
                    cat,
                ))

        # Calculate CONUS bounding box
        self.bounds = self._calculate_bounds()

        logger.info(f"WeatherTileGenerator initialized with {len(self.airports_list)} airports")
        logger.info(f"CONUS bounds: {self.bounds}")

    def _calculate_bounds(self) -> TileBounds:
        """Calculate the bounding box of all CONUS ARTCCs."""
        all_lats = []
        all_lons = []

        for artcc, polys in self.artcc_boundaries.items():
            if artcc not in self.conus_artccs:
                continue
            for poly in polys:
                for lat, lon in poly:
                    all_lats.append(lat)
                    all_lons.append(lon)

        if not all_lats:
            # Default to CONUS if no data
            return TileBounds(north=50.0, south=24.0, east=-66.0, west=-125.0)

        return TileBounds(
            north=max(all_lats),
            south=min(all_lats),
            east=max(all_lons),
            west=min(all_lons),
        )

    def _get_tile_range(self, zoom: int) -> Tuple[int, int, int, int]:
        """Get the range of tiles that cover CONUS at a given zoom level."""
        min_x = lon_to_tile_x(self.bounds.west, zoom)
        max_x = lon_to_tile_x(self.bounds.east, zoom)
        min_y = lat_to_tile_y(self.bounds.north, zoom)  # Note: Y is inverted
        max_y = lat_to_tile_y(self.bounds.south, zoom)

        return min_x, max_x, min_y, max_y

    def _find_nearest_airport(
        self,
        lat: float,
        lon: float,
    ) -> Optional[str]:
        """Find the weather category of the nearest airport within range."""
        min_dist_sq = float('inf')
        nearest_category = None

        for icao, ap_lat, ap_lon, category in self.airports_list:
            dist_sq = (lat - ap_lat) ** 2 + (lon - ap_lon) ** 2
            if dist_sq < min_dist_sq:
                min_dist_sq = dist_sq
                nearest_category = category

        # Only return if within max distance
        if min_dist_sq <= self.max_distance_sq:
            return nearest_category
        return None

    def _generate_tile(self, x: int, y: int, zoom: int) -> Optional["PILImage.Image"]:
        """
        Generate a single tile image.

        Returns None if the tile is completely empty (no weather data).
        """
        if not HAS_PIL:
            raise RuntimeError("PIL/Pillow is required for tile generation")

        # Create RGBA image (transparent background)
        img = Image.new('RGBA', (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
        pixels = img.load()

        tile_bounds = get_tile_bounds(x, y, zoom)

        # Quick check: is this tile anywhere near CONUS?
        if (tile_bounds.south > self.bounds.north or
            tile_bounds.north < self.bounds.south or
            tile_bounds.west > self.bounds.east or
            tile_bounds.east < self.bounds.west):
            return None

        has_content = False

        # Sample at pixel resolution
        # For performance, we can skip pixels at lower zooms
        step = 1 if zoom >= 7 else 2 if zoom >= 5 else 4

        for py in range(0, TILE_SIZE, step):
            for px in range(0, TILE_SIZE, step):
                lat, lon = pixel_to_latlon(px, py, x, y, zoom)

                # Check if point is inside any ARTCC boundary
                if not point_in_any_polygon((lat, lon), self.all_boundaries):
                    continue

                # Find nearest airport's weather
                category = self._find_nearest_airport(lat, lon)
                if category is None:
                    continue

                # Get color for this category
                rgb = CATEGORY_RGB.get(category, CATEGORY_RGB['UNK'])
                color = (*rgb, TILE_OPACITY)

                # Fill the step x step block
                for dy in range(step):
                    for dx in range(step):
                        if py + dy < TILE_SIZE and px + dx < TILE_SIZE:
                            pixels[px + dx, py + dy] = color

                has_content = True

        return img if has_content else None

    def _save_tile(self, img: "PILImage.Image", x: int, y: int, zoom: int) -> Path:
        """Save a tile to disk in z/x/y.png format."""
        tile_dir = self.output_dir / str(zoom) / str(x)
        tile_dir.mkdir(parents=True, exist_ok=True)

        tile_path = tile_dir / f"{y}.png"
        img.save(tile_path, 'PNG', optimize=True)

        return tile_path

    def generate_zoom_level(self, zoom: int, max_workers: int = 8) -> int:
        """
        Generate all tiles for a single zoom level.

        Returns the number of tiles generated.
        """
        min_x, max_x, min_y, max_y = self._get_tile_range(zoom)

        # Build list of all tile coordinates
        tiles = [
            (x, y)
            for x in range(min_x, max_x + 1)
            for y in range(min_y, max_y + 1)
        ]

        logger.info(f"Generating zoom level {zoom}: {len(tiles)} potential tiles")

        generated = 0

        def process_tile(coords: Tuple[int, int]) -> bool:
            x, y = coords
            img = self._generate_tile(x, y, zoom)
            if img:
                self._save_tile(img, x, y, zoom)
                return True
            return False

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_tile, t): t for t in tiles}

            for future in as_completed(futures):
                if future.result():
                    generated += 1

        logger.info(f"Zoom level {zoom}: generated {generated} tiles")
        return generated

    def generate_all(self, max_workers: int = 8) -> Dict[int, int]:
        """
        Generate tiles for all configured zoom levels.

        Returns dict mapping zoom level to number of tiles generated.
        """
        if not HAS_PIL:
            logger.error("PIL/Pillow is required for tile generation. Install with: pip install Pillow")
            return {}

        results = {}

        for zoom in self.zoom_levels:
            print(f"    Generating zoom level {zoom}...")
            results[zoom] = self.generate_zoom_level(zoom, max_workers)

        total = sum(results.values())
        print(f"    Generated {total} total tiles across {len(self.zoom_levels)} zoom levels")
        logger.info(f"Tile generation complete: {total} tiles")

        return results


def generate_weather_tiles(
    artcc_boundaries: Dict[str, List[List[Tuple[float, float]]]],
    airport_weather: Dict[str, Dict[str, Any]],
    output_dir: Path,
    conus_artccs: Set[str],
    zoom_levels: Tuple[int, ...] = (4, 5, 6, 7, 8, 9, 10),
    max_workers: int = 8,
) -> Dict[int, int]:
    """
    Generate weather overlay tiles.

    Args:
        artcc_boundaries: ARTCC boundary polygons
        airport_weather: Airport weather data {icao: {lat, lon, category}}
        output_dir: Directory for tile output
        conus_artccs: Set of CONUS ARTCC codes
        zoom_levels: Zoom levels to generate
        max_workers: Parallel workers for tile generation

    Returns:
        Dict mapping zoom level to tile count
    """
    generator = WeatherTileGenerator(
        artcc_boundaries=artcc_boundaries,
        airport_weather=airport_weather,
        output_dir=output_dir,
        conus_artccs=conus_artccs,
        zoom_levels=zoom_levels,
    )

    return generator.generate_all(max_workers=max_workers)
