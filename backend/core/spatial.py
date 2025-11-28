"""
Spatial indexing utilities for efficient geographic lookups.

This module provides a grid-based spatial index for O(1) average-case
nearest neighbor lookups instead of O(n) linear scans.
"""

import threading
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

from backend.core.calculations import haversine_distance_nm


# Default grid cell size in degrees (approximately 60nm at equator)
DEFAULT_CELL_SIZE = 1.0

# Cache for spatial index
_AIRPORT_SPATIAL_INDEX: Optional[Dict[str, Any]] = None
_AIRPORT_SPATIAL_INDEX_LOCK = threading.Lock()
_AIRPORT_SPATIAL_INDEX_TIMESTAMP: Optional[datetime] = None
_SPATIAL_INDEX_TTL_SECONDS = 300  # Rebuild every 5 minutes


class SpatialIndex:
    """
    A grid-based spatial index for efficient nearest neighbor queries.

    The index divides the world into cells of configurable size (default 1 degree).
    Lookups check the target cell and neighboring cells, providing O(1) average
    case performance for nearest neighbor queries.
    """

    def __init__(self, cell_size: float = DEFAULT_CELL_SIZE):
        """
        Initialize the spatial index.

        Args:
            cell_size: Size of grid cells in degrees (default: 1.0)
        """
        self.cell_size = cell_size
        self.grid: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
        self.airports: List[Dict[str, Any]] = []

    def build(self, airports_data: Dict[str, Dict[str, Any]]) -> None:
        """
        Build the spatial index from airport data.

        Args:
            airports_data: Dictionary mapping ICAO codes to airport data
                          (must include 'latitude' and 'longitude' keys)
        """
        self.grid.clear()
        self.airports.clear()

        for icao, data in airports_data.items():
            lat = data.get('latitude')
            lon = data.get('longitude')
            if lat is None or lon is None:
                continue

            airport = {
                'icao': icao,
                'latitude': lat,
                'longitude': lon,
                'data': data
            }
            self.airports.append(airport)

            # Add to grid cell
            cell_key = self._get_cell_key(lat, lon)
            if cell_key not in self.grid:
                self.grid[cell_key] = []
            self.grid[cell_key].append(airport)

    def _get_cell_key(self, lat: float, lon: float) -> Tuple[int, int]:
        """Get the grid cell key for a given coordinate."""
        return (int(lat / self.cell_size), int(lon / self.cell_size))

    def _get_neighboring_cells(self, lat: float, lon: float) -> List[Tuple[int, int]]:
        """Get all cell keys in the 3x3 neighborhood around the target cell."""
        center = self._get_cell_key(lat, lon)
        cells = []
        for dlat in [-1, 0, 1]:
            for dlon in [-1, 0, 1]:
                cells.append((center[0] + dlat, center[1] + dlon))
        return cells

    def find_nearest(
        self,
        lat: float,
        lon: float,
        max_distance_nm: Optional[float] = None,
        filter_fn: Optional[callable] = None
    ) -> Optional[str]:
        """
        Find the nearest airport to the given coordinates.

        Args:
            lat: Latitude of the query point
            lon: Longitude of the query point
            max_distance_nm: Maximum distance in nautical miles (optional)
            filter_fn: Optional function to filter airports (takes airport dict, returns bool)

        Returns:
            ICAO code of the nearest airport, or None if no airport found
        """
        if not self.airports:
            return None

        # Get candidate airports from neighboring cells
        candidates = []
        for cell_key in self._get_neighboring_cells(lat, lon):
            if cell_key in self.grid:
                candidates.extend(self.grid[cell_key])

        # If no candidates in neighborhood, fall back to all airports
        # (this happens near cell boundaries or with very sparse data)
        if not candidates:
            candidates = self.airports

        # Find nearest
        nearest_icao = None
        min_distance = float('inf')

        for airport in candidates:
            if filter_fn and not filter_fn(airport):
                continue

            try:
                distance = haversine_distance_nm(
                    lat, lon,
                    airport['latitude'],
                    airport['longitude']
                )
            except ValueError:
                # Skip airports with invalid coordinates
                continue

            if distance < min_distance:
                if max_distance_nm is None or distance <= max_distance_nm:
                    min_distance = distance
                    nearest_icao = airport['icao']

        return nearest_icao

    def find_within_distance(
        self,
        lat: float,
        lon: float,
        max_distance_nm: float,
        filter_fn: Optional[callable] = None
    ) -> List[Tuple[str, float]]:
        """
        Find all airports within a given distance.

        Args:
            lat: Latitude of the query point
            lon: Longitude of the query point
            max_distance_nm: Maximum distance in nautical miles
            filter_fn: Optional function to filter airports

        Returns:
            List of (ICAO code, distance) tuples, sorted by distance
        """
        results = []

        # Get candidate airports from neighboring cells
        # For larger distances, we may need to check more cells
        cells_to_check = max(1, int(max_distance_nm / 60) + 1)  # ~60nm per degree
        center = self._get_cell_key(lat, lon)

        candidates = []
        for dlat in range(-cells_to_check, cells_to_check + 1):
            for dlon in range(-cells_to_check, cells_to_check + 1):
                cell_key = (center[0] + dlat, center[1] + dlon)
                if cell_key in self.grid:
                    candidates.extend(self.grid[cell_key])

        for airport in candidates:
            if filter_fn and not filter_fn(airport):
                continue

            try:
                distance = haversine_distance_nm(
                    lat, lon,
                    airport['latitude'],
                    airport['longitude']
                )
            except ValueError:
                # Skip airports with invalid coordinates
                continue

            if distance <= max_distance_nm:
                results.append((airport['icao'], distance))

        # Sort by distance
        results.sort(key=lambda x: x[1])
        return results


def get_airport_spatial_index(airports_data: Dict[str, Dict[str, Any]]) -> SpatialIndex:
    """
    Get or build the cached airport spatial index.

    The index is cached globally and rebuilt every 5 minutes to pick up
    any changes to airport data.

    This function is thread-safe.

    Args:
        airports_data: Dictionary of airport data

    Returns:
        SpatialIndex instance
    """
    global _AIRPORT_SPATIAL_INDEX, _AIRPORT_SPATIAL_INDEX_TIMESTAMP

    current_time = datetime.now(timezone.utc)

    with _AIRPORT_SPATIAL_INDEX_LOCK:
        # Check if we need to build or rebuild the index
        needs_rebuild = (
            _AIRPORT_SPATIAL_INDEX is None or
            _AIRPORT_SPATIAL_INDEX_TIMESTAMP is None or
            (current_time - _AIRPORT_SPATIAL_INDEX_TIMESTAMP).total_seconds() > _SPATIAL_INDEX_TTL_SECONDS
        )

        if needs_rebuild:
            index = SpatialIndex()
            index.build(airports_data)
            _AIRPORT_SPATIAL_INDEX = index
            _AIRPORT_SPATIAL_INDEX_TIMESTAMP = current_time

        return _AIRPORT_SPATIAL_INDEX


def clear_spatial_index_cache() -> None:
    """Clear the spatial index cache (thread-safe)."""
    global _AIRPORT_SPATIAL_INDEX, _AIRPORT_SPATIAL_INDEX_TIMESTAMP

    with _AIRPORT_SPATIAL_INDEX_LOCK:
        _AIRPORT_SPATIAL_INDEX = None
        _AIRPORT_SPATIAL_INDEX_TIMESTAMP = None
