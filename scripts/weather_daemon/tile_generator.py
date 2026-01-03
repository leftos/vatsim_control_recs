"""
Weather Overlay Tile Generator

Generates map tiles (z/x/y scheme) with weather category coloring.
Uses Web Mercator projection (EPSG:3857) compatible with Leaflet/OSM tiles.

Memory-optimized with KD-tree spatial indexing for low-memory servers.
"""

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
from PIL import Image
from scipy.spatial import cKDTree  # type: ignore[attr-defined]

logger = logging.getLogger("weather_daemon")

# Tile size in pixels (standard for web maps)
TILE_SIZE = 256

# Weather category colors as RGBA tuples
CATEGORY_RGBA = {
    'VFR': (0, 255, 0, 140),
    'MVFR': (85, 153, 255, 140),
    'IFR': (255, 0, 0, 140),
    'LIFR': (255, 0, 255, 140),
}

# Category to index mapping
CATEGORY_INDEX = {'VFR': 0, 'MVFR': 1, 'IFR': 2, 'LIFR': 3}
INDEX_TO_CATEGORY = {0: 'VFR', 1: 'MVFR', 2: 'IFR', 3: 'LIFR'}


@dataclass
class TileBounds:
    """Geographic bounds for a tile."""
    north: float
    south: float
    east: float
    west: float


def lat_to_tile_y(lat: float, zoom: int) -> int:
    """Convert latitude to tile Y coordinate (Web Mercator)."""
    n = 2 ** zoom
    lat_rad = math.radians(lat)
    return int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)


def lon_to_tile_x(lon: float, zoom: int) -> int:
    """Convert longitude to tile X coordinate."""
    n = 2 ** zoom
    return int((lon + 180.0) / 360.0 * n)


def get_tile_bounds(x: int, y: int, zoom: int) -> TileBounds:
    """Get geographic bounds for a tile."""
    n = 2 ** zoom

    def tile_to_lat(ty):
        lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ty / n)))
        return math.degrees(lat_rad)

    def tile_to_lon(tx):
        return tx / n * 360.0 - 180.0

    return TileBounds(
        north=tile_to_lat(y),
        south=tile_to_lat(y + 1),
        west=tile_to_lon(x),
        east=tile_to_lon(x + 1),
    )


def points_in_polygon(lats: np.ndarray, lons: np.ndarray, polygon: List[Tuple[float, float]]) -> np.ndarray:
    """Vectorized point-in-polygon test using ray casting."""
    n = len(polygon)
    inside = np.zeros(lats.shape, dtype=bool)

    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        cond1 = (yi > lons) != (yj > lons)
        cond2 = lats < (xj - xi) * (lons - yi) / (yj - yi + 1e-10) + xi
        inside ^= (cond1 & cond2)
        j = i

    return inside


class WeatherTileGenerator:
    """
    Memory-efficient weather tile generator using KD-tree spatial indexing.

    Instead of computing distances to ALL airports for each pixel,
    uses a KD-tree to find only the nearest airport efficiently.

    Uses cosine-corrected coordinates to produce circular (not oval) regions.
    """

    def __init__(
        self,
        artcc_boundaries: Dict[str, List[List[Tuple[float, float]]]],
        airport_weather: Dict[str, Dict[str, Any]],
        output_dir: Path,
        conus_artccs: Set[str],
        max_distance_deg: float = 1.0,
        zoom_levels: Tuple[int, ...] = (4, 5, 6, 7, 8, 9, 10),
    ):
        """Initialize the tile generator with KD-tree for efficient lookups."""
        self.output_dir = output_dir
        self.zoom_levels = zoom_levels
        self.max_distance_sq = max_distance_deg ** 2

        # Build combined list of all CONUS boundary polygons
        self.all_boundaries: List[List[Tuple[float, float]]] = []
        for artcc, polys in artcc_boundaries.items():
            if artcc in conus_artccs:
                self.all_boundaries.extend(polys)

        # Calculate CONUS bounding box first (needed for cosine correction)
        self.bounds = self._calculate_bounds(artcc_boundaries, conus_artccs)

        # Calculate reference latitude for cosine correction
        # At CONUS latitudes (~25-50°N), 1° longitude < 1° latitude in ground distance
        # We scale longitude by cos(ref_lat) to normalize distances
        self.ref_lat = (self.bounds.north + self.bounds.south) / 2
        self.cos_ref_lat = math.cos(math.radians(self.ref_lat))

        # Build airport arrays and KD-tree for spatial queries
        valid_categories = {'VFR', 'MVFR', 'IFR', 'LIFR'}
        coords = []
        raw_coords = []
        categories = []

        for icao, data in airport_weather.items():
            cat = data.get('category')
            if cat in valid_categories:
                lat, lon = data['lat'], data['lon']
                raw_coords.append((lat, lon))
                # Apply cosine correction to longitude for equal-distance KD-tree
                coords.append((lat, lon * self.cos_ref_lat))
                categories.append(CATEGORY_INDEX[cat])

        if coords:
            self.airport_coords = np.array(raw_coords, dtype=np.float32)
            self.airport_coords_scaled = np.array(coords, dtype=np.float32)
            self.airport_categories = np.array(categories, dtype=np.int8)
            # Build KD-tree with scaled coordinates for circular distance regions
            self.kdtree = cKDTree(self.airport_coords_scaled)
        else:
            self.airport_coords = np.array([], dtype=np.float32)
            self.airport_coords_scaled = np.array([], dtype=np.float32)
            self.airport_categories = np.array([], dtype=np.int8)
            self.kdtree = None

        logger.info(f"WeatherTileGenerator initialized with {len(coords)} airports (KD-tree indexed, ref_lat={self.ref_lat:.1f}°)")

    def _calculate_bounds(
        self,
        artcc_boundaries: Dict[str, List[List[Tuple[float, float]]]],
        conus_artccs: Set[str],
    ) -> TileBounds:
        """Calculate the bounding box of all CONUS ARTCCs."""
        all_lats, all_lons = [], []

        for artcc, polys in artcc_boundaries.items():
            if artcc not in conus_artccs:
                continue
            for poly in polys:
                for lat, lon in poly:
                    all_lats.append(lat)
                    all_lons.append(lon)

        if not all_lats:
            return TileBounds(north=50.0, south=24.0, east=-66.0, west=-125.0)

        return TileBounds(
            north=max(all_lats),
            south=min(all_lats),
            east=max(all_lons),
            west=min(all_lons),
        )

    def _get_tile_range(self, zoom: int) -> Tuple[int, int, int, int]:
        """Get the range of tiles that cover CONUS at a given zoom level."""
        return (
            lon_to_tile_x(self.bounds.west, zoom),
            lon_to_tile_x(self.bounds.east, zoom),
            lat_to_tile_y(self.bounds.north, zoom),
            lat_to_tile_y(self.bounds.south, zoom),
        )

    def _generate_tile(self, tile_x: int, tile_y: int, zoom: int) -> Optional[bytes]:
        """
        Generate a single tile using KD-tree for memory-efficient nearest neighbor lookups.

        Memory usage: O(tile_size^2) instead of O(tile_size^2 * num_airports)
        """
        if self.kdtree is None:
            return None

        tile_bounds = get_tile_bounds(tile_x, tile_y, zoom)

        # Quick bounds check
        if (tile_bounds.south > self.bounds.north or
            tile_bounds.north < self.bounds.south or
            tile_bounds.west > self.bounds.east or
            tile_bounds.east < self.bounds.west):
            return None

        # Create coordinate grids for the tile
        n = 2 ** zoom
        px = np.arange(TILE_SIZE, dtype=np.float32)
        py = np.arange(TILE_SIZE, dtype=np.float32)
        px_grid, py_grid = np.meshgrid(px, py)

        # Convert to lat/lon
        x_frac = tile_x + px_grid / TILE_SIZE
        y_frac = tile_y + py_grid / TILE_SIZE

        lons = x_frac / n * 360.0 - 180.0
        lats = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * y_frac / n))))

        # Check which pixels are inside any ARTCC boundary
        inside_any = np.zeros((TILE_SIZE, TILE_SIZE), dtype=bool)
        for polygon in self.all_boundaries:
            if len(polygon) >= 3:
                inside_any |= points_in_polygon(lats, lons, polygon)

        if not np.any(inside_any):
            return None

        # Flatten coordinates for KD-tree query
        # Apply same cosine correction used when building the tree
        lons_scaled = lons * self.cos_ref_lat
        coords_flat = np.column_stack([lats.ravel(), lons_scaled.ravel()])

        # Query KD-tree for nearest airport to each pixel
        # This is O(n log m) where n=pixels, m=airports - much more memory efficient
        distances, indices = self.kdtree.query(coords_flat, k=1)

        # Reshape results
        distances = distances.reshape(TILE_SIZE, TILE_SIZE)
        indices = indices.reshape(TILE_SIZE, TILE_SIZE)

        # Get categories and apply distance mask
        pixel_categories = self.airport_categories[indices]
        valid_mask = inside_any & (distances ** 2 <= self.max_distance_sq)

        if not np.any(valid_mask):
            return None

        # Create RGBA image
        rgba = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)

        for cat_name, cat_idx in CATEGORY_INDEX.items():
            cat_mask = valid_mask & (pixel_categories == cat_idx)
            if np.any(cat_mask):
                rgba[cat_mask] = CATEGORY_RGBA[cat_name]

        # Convert to PNG bytes
        img = Image.fromarray(rgba, 'RGBA')
        buffer = BytesIO()
        img.save(buffer, 'PNG', optimize=True)
        return buffer.getvalue()

    def generate_all(self, max_workers: int = 2) -> Dict[int, int]:
        """
        Generate tiles for all zoom levels.

        Args:
            max_workers: Number of parallel workers. Keep low (1-2) for memory-constrained servers.
        """
        if self.kdtree is None:
            logger.warning("No airports with valid weather data")
            print("    WARNING: No airports with valid weather data")
            return {}

        # Collect all tile coordinates
        all_tiles = []
        for zoom in self.zoom_levels:
            min_x, max_x, min_y, max_y = self._get_tile_range(zoom)
            for x in range(min_x, max_x + 1):
                for y in range(min_y, max_y + 1):
                    all_tiles.append((x, y, zoom))

        total_potential = len(all_tiles)
        print(f"    Processing {total_potential} potential tiles with {max_workers} workers...")
        logger.info(f"Processing {total_potential} potential tiles with {max_workers} workers")

        results: Dict[int, int] = {z: 0 for z in self.zoom_levels}
        generated = 0

        def process_tile(tile_info):
            x, y, zoom = tile_info
            png_bytes = self._generate_tile(x, y, zoom)
            if png_bytes:
                tile_dir = self.output_dir / str(zoom) / str(x)
                tile_dir.mkdir(parents=True, exist_ok=True)
                tile_path = tile_dir / f"{y}.png"
                with open(tile_path, 'wb') as f:
                    f.write(png_bytes)
                return zoom
            return None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_tile, t): t for t in all_tiles}

            for future in as_completed(futures):
                try:
                    zoom = future.result()
                    if zoom is not None:
                        results[zoom] += 1
                        generated += 1
                        if generated % 100 == 0:
                            print(f"    Generated {generated} tiles...")
                except Exception as e:
                    logger.error(f"Tile generation error: {e}")

        total = sum(results.values())
        print(f"    Generated {total} tiles total")
        logger.info(f"Generated {total} tiles across {len(self.zoom_levels)} zoom levels")

        return results


def generate_weather_tiles(
    artcc_boundaries: Dict[str, List[List[Tuple[float, float]]]],
    airport_weather: Dict[str, Dict[str, Any]],
    output_dir: Path,
    conus_artccs: Set[str],
    zoom_levels: Tuple[int, ...] = (4, 5, 6, 7, 8, 9, 10),
    max_workers: int = 2,
) -> Dict[int, int]:
    """
    Generate weather overlay tiles.

    Uses KD-tree spatial indexing for memory efficiency on low-memory servers.
    """
    generator = WeatherTileGenerator(
        artcc_boundaries=artcc_boundaries,
        airport_weather=airport_weather,
        output_dir=output_dir,
        conus_artccs=conus_artccs,
        zoom_levels=zoom_levels,
    )

    return generator.generate_all(max_workers=max_workers)
