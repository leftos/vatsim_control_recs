"""
Weather Overlay Tile Generator

Generates map tiles (z/x/y scheme) with weather category coloring.
Uses Web Mercator projection (EPSG:3857) compatible with Leaflet/OSM tiles.

Memory-optimized with KD-tree spatial indexing for low-memory servers.
"""

import logging
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy.spatial import cKDTree  # type: ignore[attr-defined]

logger = logging.getLogger("weather_daemon")

# Tile size in pixels (standard for web maps)
TILE_SIZE = 256

# Weather category colors as RGBA tuples
CATEGORY_RGBA = {
    "VFR": (0, 255, 0, 140),
    "MVFR": (85, 153, 255, 140),
    "IFR": (255, 0, 0, 140),
    "LIFR": (255, 0, 255, 140),
}

# Category to index mapping
CATEGORY_INDEX = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}
INDEX_TO_CATEGORY = {0: "VFR", 1: "MVFR", 2: "IFR", 3: "LIFR"}


@dataclass
class TileBounds:
    """Geographic bounds for a tile."""

    north: float
    south: float
    east: float
    west: float


def lat_to_tile_y(lat: float, zoom: int) -> int:
    """Convert latitude to tile Y coordinate (Web Mercator)."""
    n = 2**zoom
    lat_rad = math.radians(lat)
    return int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)


def lon_to_tile_x(lon: float, zoom: int) -> int:
    """Convert longitude to tile X coordinate."""
    n = 2**zoom
    return int((lon + 180.0) / 360.0 * n)


def get_tile_bounds(x: int, y: int, zoom: int) -> TileBounds:
    """Get geographic bounds for a tile."""
    n = 2**zoom

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


def points_in_polygon(
    lats: np.ndarray, lons: np.ndarray, polygon: list[tuple[float, float]]
) -> np.ndarray:
    """Vectorized point-in-polygon test using ray casting."""
    n = len(polygon)
    inside = np.zeros(lats.shape, dtype=bool)

    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        cond1 = (yi > lons) != (yj > lons)
        cond2 = lats < (xj - xi) * (lons - yi) / (yj - yi + 1e-10) + xi
        inside ^= cond1 & cond2
        j = i

    return inside


def polygon_box(
    polygon: list[tuple[float, float]],
) -> tuple[float, float, float, float] | None:
    """A polygon's (min_lat, max_lat, min_lon, max_lon), or None when it has under 3 points."""
    if len(polygon) < 3:
        return None
    lats = [lat for lat, _ in polygon]
    lons = [lon for _, lon in polygon]
    return min(lats), max(lats), min(lons), max(lons)


def write_file_atomically(path: Path, data: bytes) -> None:
    """Write ``data`` to a temp file beside ``path``, then replace ``path`` with it.

    A reader (nginx) or a run killed mid-write sees either the old file or the new
    one, never a truncated one. The temp file is removed if the write fails.
    """
    temp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        temp_path.write_bytes(data)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


# Written into the tiles directory after a run's last tile, so the index page can
# version the tile URLs by the newest complete tile set
TILE_VERSION_FILE = "version.txt"


def write_tile_version(tiles_dir: Path, version: int) -> None:
    """Record ``version`` as the newest complete tile set in ``tiles_dir``."""
    write_file_atomically(tiles_dir / TILE_VERSION_FILE, f"{version}\n".encode())


def read_tile_version(tiles_dir: Path, fallback: int) -> int:
    """The newest complete tile set's version, or ``fallback`` (logged) when there is none."""
    version_path = tiles_dir / TILE_VERSION_FILE
    try:
        return int(version_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as e:
        logger.info(
            f"No tile version in {version_path} ({e}); using the run's start time {fallback}"
        )
        return fallback


def encode_tile_png(valid_mask: np.ndarray, pixel_categories: np.ndarray) -> bytes:
    """Colour each valid pixel by its category index and encode the tile as PNG."""
    rgba = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)

    for cat_name, cat_idx in CATEGORY_INDEX.items():
        cat_mask = valid_mask & (pixel_categories == cat_idx)
        if np.any(cat_mask):
            rgba[cat_mask] = CATEGORY_RGBA[cat_name]

    img = Image.fromarray(rgba, "RGBA")
    buffer = BytesIO()
    img.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


# Pixel coordinates are float32, so a tile's edge pixels can sit a few millionths of a
# degree outside its float64 bounds; the margin keeps a polygon whose box ends exactly
# on a tile edge.
REACH_MARGIN_DEG = 1e-3


class WeatherTileGenerator:
    """
    Memory-efficient weather tile generator using KD-tree spatial indexing.

    Instead of computing distances to ALL airports for each pixel,
    uses a KD-tree to find only the nearest airport efficiently.

    Uses cosine-corrected coordinates to produce circular (not oval) regions.
    """

    def __init__(
        self,
        artcc_boundaries: dict[str, list[list[tuple[float, float]]]],
        airport_weather: dict[str, dict[str, Any]],
        output_dir: Path,
        conus_artccs: set[str],
        max_distance_nm: float = 60.0,
        zoom_levels: tuple[int, ...] = (4, 5, 6, 7, 8, 9, 10),
    ):
        """Initialize the tile generator with KD-tree for efficient lookups."""
        self.output_dir = output_dir
        self.zoom_levels = zoom_levels
        # Store max distance in nautical miles for latitude-independent coverage
        # 1 degree of latitude = 60 nm, so we convert to degrees at query time
        self.max_distance_nm = max_distance_nm

        # Build combined list of all CONUS boundary polygons
        self.all_boundaries: list[list[tuple[float, float]]] = []
        for artcc, polys in artcc_boundaries.items():
            if artcc in conus_artccs:
                self.all_boundaries.extend(polys)

        # Each polygon's (min_lat, max_lat, min_lon, max_lon), same order as all_boundaries;
        # None for a polygon too short to enclose anything
        self.boundary_boxes: list[tuple[float, float, float, float] | None] = [
            polygon_box(polygon) for polygon in self.all_boundaries
        ]

        # Calculate bounding box of all regions
        self.bounds = self._calculate_bounds(artcc_boundaries, conus_artccs)

        # Build airport arrays for spatial queries
        # Note: We store raw coordinates and apply per-pixel cosine correction during tile generation
        # This ensures accurate distance calculations at all latitudes (especially important for Alaska)
        valid_categories = {"VFR", "MVFR", "IFR", "LIFR"}
        raw_coords = []
        categories = []

        for data in airport_weather.values():
            cat = data.get("category")
            if cat in valid_categories:
                lat, lon = data["lat"], data["lon"]
                raw_coords.append((lat, lon))
                categories.append(CATEGORY_INDEX[cat])

        if raw_coords:
            self.airport_coords = np.array(raw_coords, dtype=np.float32)
            self.airport_categories = np.array(categories, dtype=np.int8)
        else:
            self.airport_coords = np.array([], dtype=np.float32)
            self.airport_categories = np.array([], dtype=np.int8)

        logger.info(f"WeatherTileGenerator initialized with {len(raw_coords)} airports")

    def _calculate_bounds(
        self,
        artcc_boundaries: dict[str, list[list[tuple[float, float]]]],
        conus_artccs: set[str],
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

    def _get_tile_range(self, zoom: int) -> tuple[int, int, int, int]:
        """Get the range of tiles that cover CONUS at a given zoom level."""
        return (
            lon_to_tile_x(self.bounds.west, zoom),
            lon_to_tile_x(self.bounds.east, zoom),
            lat_to_tile_y(self.bounds.north, zoom),
            lat_to_tile_y(self.bounds.south, zoom),
        )

    def _polygons_reaching(
        self, tile_bounds: TileBounds
    ) -> list[list[tuple[float, float]]]:
        """The polygons whose bounding box meets the tile's bounds (widened by the margin)."""
        south = tile_bounds.south - REACH_MARGIN_DEG
        north = tile_bounds.north + REACH_MARGIN_DEG
        west = tile_bounds.west - REACH_MARGIN_DEG
        east = tile_bounds.east + REACH_MARGIN_DEG
        reaching = []
        for polygon, box in zip(self.all_boundaries, self.boundary_boxes, strict=True):
            if box is None:
                continue
            min_lat, max_lat, min_lon, max_lon = box
            if (
                max_lat >= south
                and min_lat <= north
                and max_lon >= west
                and min_lon <= east
            ):
                reaching.append(polygon)
        return reaching

    def _generate_tile(self, tile_x: int, tile_y: int, zoom: int) -> bytes | None:
        """
        Generate a single tile using per-pixel cosine-corrected distance calculations.

        Uses the tile's center latitude for cosine correction, which is accurate enough
        within a single tile and properly handles high-latitude regions like Alaska.
        """
        if len(self.airport_coords) == 0:
            return None

        tile_bounds = get_tile_bounds(tile_x, tile_y, zoom)

        # Quick bounds check
        if (
            tile_bounds.south > self.bounds.north
            or tile_bounds.north < self.bounds.south
            or tile_bounds.west > self.bounds.east
            or tile_bounds.east < self.bounds.west
        ):
            return None

        # A pixel outside a polygon's box cannot be inside the polygon
        reaching = self._polygons_reaching(tile_bounds)
        if not reaching:
            return None

        # Create coordinate grids for the tile
        n = 2**zoom
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
        for polygon in reaching:
            inside_any |= points_in_polygon(lats, lons, polygon)

        if not np.any(inside_any):
            return None

        # Use tile center latitude for cosine correction
        # This is accurate within a single tile (tiles span ~1-2 degrees of latitude)
        tile_center_lat = (tile_bounds.north + tile_bounds.south) / 2
        cos_lat = math.cos(math.radians(tile_center_lat))

        # Build a temporary KD-tree with coordinates scaled for this tile's latitude
        airport_lats = self.airport_coords[:, 0]
        airport_lons = self.airport_coords[:, 1]
        scaled_coords = np.column_stack([airport_lats, airport_lons * cos_lat])
        kdtree = cKDTree(scaled_coords)

        # Scale pixel coordinates with the same factor
        lons_scaled = lons * cos_lat
        coords_flat = np.column_stack([lats.ravel(), lons_scaled.ravel()])

        # Query KD-tree for nearest airport to each pixel
        distances, indices = kdtree.query(coords_flat, k=1)

        # Reshape results
        distances = distances.reshape(TILE_SIZE, TILE_SIZE)
        indices = indices.reshape(TILE_SIZE, TILE_SIZE)

        # Convert max distance from nautical miles to degrees
        # 1 degree of latitude = 60 nm always
        # In cosine-scaled space, latitude degrees are the unit of measure
        max_distance_deg = self.max_distance_nm / 60.0
        max_distance_sq = max_distance_deg**2

        # Get categories and apply distance mask
        pixel_categories = self.airport_categories[indices]
        valid_mask = inside_any & (distances**2 <= max_distance_sq)

        if not np.any(valid_mask):
            return None

        return encode_tile_png(valid_mask, pixel_categories)

    def _all_tile_coords(self) -> list[tuple[int, int, int]]:
        """Every (x, y, zoom) in the region's tile range at each zoom level."""
        all_tiles = []
        for zoom in self.zoom_levels:
            min_x, max_x, min_y, max_y = self._get_tile_range(zoom)
            all_tiles.extend(
                (x, y, zoom)
                for x in range(min_x, max_x + 1)
                for y in range(min_y, max_y + 1)
            )
        return all_tiles

    def generate_all(self, max_workers: int = 2) -> dict[int, int]:
        """
        Generate tiles for all zoom levels.

        Args:
            max_workers: Number of parallel workers. Keep low (1-2) for memory-constrained servers.
        """
        if len(self.airport_coords) == 0:
            logger.warning("No airports with valid weather data")
            print("    WARNING: No airports with valid weather data")
            return {}

        all_tiles = self._all_tile_coords()
        total_potential = len(all_tiles)
        print(
            f"    Processing {total_potential} potential tiles with {max_workers} workers..."
        )
        logger.info(
            f"Processing {total_potential} potential tiles with {max_workers} workers"
        )

        results: dict[int, int] = dict.fromkeys(self.zoom_levels, 0)
        generated = 0

        def process_tile(tile_info):
            x, y, zoom = tile_info
            png_bytes = self._generate_tile(x, y, zoom)
            if png_bytes:
                tile_dir = self.output_dir / str(zoom) / str(x)
                tile_dir.mkdir(parents=True, exist_ok=True)
                write_file_atomically(tile_dir / f"{y}.png", png_bytes)
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
        logger.info(
            f"Generated {total} tiles across {len(self.zoom_levels)} zoom levels"
        )

        return results


def generate_weather_tiles(
    artcc_boundaries: dict[str, list[list[tuple[float, float]]]],
    airport_weather: dict[str, dict[str, Any]],
    output_dir: Path,
    conus_artccs: set[str],
    zoom_levels: tuple[int, ...] = (4, 5, 6, 7, 8, 9, 10),
    max_workers: int = 2,
) -> dict[int, int]:
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
