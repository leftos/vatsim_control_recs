"""
Headless Weather Briefing Generator

Generates HTML weather briefings without requiring the Textual UI.
Reuses core logic from ui/modals/weather_briefing.py.
"""

import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from rich.console import Console

# Set up logging
logger = logging.getLogger("weather_daemon")


def _save_weather_cache(cache_dir: Path, metars: Dict, tafs: Dict, atis_data: Dict) -> None:
    """Save weather data to cache for later use with --use-cached."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "weather_cache.json"

    cache_data = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'metars': metars,
        'tafs': tafs,
        'atis': atis_data,
    }

    try:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f)
        logger.debug(f"Saved weather cache to {cache_file}")
    except Exception as e:
        logger.warning(f"Failed to save weather cache: {e}")


def _load_weather_cache(cache_dir: Path, max_age_seconds: Optional[int] = None) -> Optional[Tuple[Dict, Dict, Dict, str]]:
    """Load weather data from cache.

    Args:
        cache_dir: Directory containing cache file
        max_age_seconds: If set, only return cache if fresher than this many seconds

    Returns:
        (metars, tafs, atis, timestamp) or None if cache missing/expired
    """
    cache_file = cache_dir / "weather_cache.json"

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)

        timestamp_str = cache_data.get('timestamp', '')

        # Check TTL if specified
        if max_age_seconds is not None and timestamp_str:
            try:
                cache_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                age = (datetime.now(timezone.utc) - cache_time).total_seconds()
                if age > max_age_seconds:
                    logger.debug(f"Cache expired ({age:.0f}s old, TTL={max_age_seconds}s)")
                    return None
            except (ValueError, TypeError):
                pass  # Can't parse timestamp, ignore TTL check

        return (
            cache_data.get('metars', {}),
            cache_data.get('tafs', {}),
            cache_data.get('atis', {}),
            timestamp_str or 'unknown',
        )
    except Exception as e:
        logger.warning(f"Failed to load weather cache: {e}")
        return None


class ProgressTracker:
    """Tracks and reports progress for long-running operations."""

    def __init__(self, operation: str, total: int, log_interval_pct: int = 10):
        """
        Initialize progress tracker.

        Args:
            operation: Name of the operation (e.g., "Fetching METAR")
            total: Total number of items to process
            log_interval_pct: How often to log progress (percentage)
        """
        self.operation = operation
        self.total = total
        self.completed = 0
        self.log_interval_pct = log_interval_pct
        self.last_logged_pct = -log_interval_pct  # Ensure first update logs
        self.start_time = datetime.now(timezone.utc)

    def update(self, completed: int = None, increment: int = 1) -> None:
        """Update progress and print/log if at interval."""
        if completed is not None:
            self.completed = completed
        else:
            self.completed += increment

        if self.total == 0:
            return

        pct = int((self.completed / self.total) * 100)

        # Log at intervals
        if pct >= self.last_logged_pct + self.log_interval_pct or self.completed == self.total:
            self._report(pct)
            self.last_logged_pct = pct

    def _report(self, pct: int) -> None:
        """Report progress to console and log."""
        elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds()

        if self.completed == self.total:
            msg = f"    {self.operation}: {self.completed}/{self.total} (100%) - {elapsed:.1f}s"
            print(msg)
            logger.info(f"{self.operation}: completed {self.total} items in {elapsed:.1f}s")
        else:
            # Estimate remaining time
            if self.completed > 0:
                rate = self.completed / elapsed
                remaining = (self.total - self.completed) / rate if rate > 0 else 0
                msg = f"    {self.operation}: {self.completed}/{self.total} ({pct}%) - ETA {remaining:.0f}s"
            else:
                msg = f"    {self.operation}: {self.completed}/{self.total} ({pct}%)"
            print(msg)
            logger.debug(f"{self.operation}: {pct}% ({self.completed}/{self.total})")

    def callback(self, completed: int, total: int) -> None:
        """Callback compatible with batch fetch functions."""
        self.update(completed=completed)

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend import (
    get_metar_batch,
    get_taf_batch,
    get_rate_limit_status,
    fetch_weather_bbox,
    haversine_distance_nm,
    load_all_groupings,
    load_unified_airport_data,
)
from .artcc_boundaries import get_artcc_boundaries
from .tile_generator import generate_weather_tiles
from backend.core.groupings import (
    load_preset_groupings,
    load_custom_groupings,
    resolve_grouping_recursively,
    PRESET_GROUPINGS_DIR,
)
from backend.data.vatsim_api import download_vatsim_data, get_atis_for_airports
from backend.data.atis_filter import filter_atis_text, colorize_atis_text
from airport_disambiguator import AirportDisambiguator

from .config import DaemonConfig, CATEGORY_COLORS, ARTCC_NAMES

# Import METAR parsing functions
from ui.modals.metar_info import (
    get_flight_category,
    _extract_visibility_str,
    _parse_ceiling_layer,
    _parse_visibility_sm,
    _parse_ceiling_feet,
    parse_weather_phenomena,
)

def get_artcc_bboxes(
    artcc_codes: Set[str],
    cache_dir: Path,
    padding_degrees: float = 0.5
) -> Dict[str, Tuple[float, float, float, float]]:
    """
    Calculate bounding boxes for ARTCCs from their polygon boundaries.

    Adds padding to each bounding box to ensure airports at the edges
    of ARTCC boundaries are captured (since the polygon-to-bbox conversion
    may exclude some airports that are technically within the ARTCC).

    Args:
        artcc_codes: Set of ARTCC codes to get bboxes for
        cache_dir: Cache directory for ARTCC boundary data
        padding_degrees: Extra degrees to add around the bbox (default: 0.5, ~30nm)

    Returns:
        Dict mapping ARTCC codes to (min_lat, min_lon, max_lat, max_lon)
    """
    boundaries = get_artcc_boundaries(cache_dir)
    bboxes = {}

    for artcc in artcc_codes:
        if artcc not in boundaries or artcc == "custom":
            continue

        polygons = boundaries[artcc]
        all_points = []
        for polygon in polygons:
            all_points.extend(polygon)

        if not all_points:
            continue

        min_lat = min(p[0] for p in all_points) - padding_degrees
        max_lat = max(p[0] for p in all_points) + padding_degrees
        min_lon = min(p[1] for p in all_points) - padding_degrees
        max_lon = max(p[1] for p in all_points) + padding_degrees

        # Clamp to valid ranges
        min_lat = max(-90.0, min_lat)
        max_lat = min(90.0, max_lat)
        min_lon = max(-180.0, min_lon)
        max_lon = min(180.0, max_lon)

        bboxes[artcc] = (min_lat, min_lon, max_lat, max_lon)

    return bboxes


# Import shared constants from backend
from backend.data.weather_parsing import (
    CATEGORY_PRIORITY,
    FAR139_PRIORITY,
    TOWER_TYPE_PRIORITY,
    get_airport_size_priority as _get_airport_size_priority_impl,
)


def _parse_wind_from_metar(metar: str) -> Optional[str]:
    """Extract wind string from METAR."""
    if not metar:
        return None
    wind_pattern = r'\b(\d{3}|VRB)(\d{2,3})(G\d{2,3})?(KT|MPS|KMH)\b'
    match = re.search(wind_pattern, metar)
    if match:
        return match.group(0)
    return None


def _parse_taf_forecast_details(conditions: str) -> Dict[str, Any]:
    """Parse detailed forecast conditions from a TAF segment."""
    category, _ = get_flight_category(conditions)
    visibility_sm = _parse_visibility_sm(conditions)
    ceiling_ft = _parse_ceiling_feet(conditions)
    ceiling_layer = _parse_ceiling_layer(conditions)
    wind = _parse_wind_from_metar(conditions)
    phenomena = parse_weather_phenomena(conditions)
    return {
        'category': category,
        'visibility_sm': visibility_sm,
        'ceiling_ft': ceiling_ft,
        'ceiling_layer': ceiling_layer,
        'wind': wind,
        'phenomena': phenomena,
    }


def _calculate_trend(
    current_vis: Optional[float],
    current_ceil: Optional[int],
    current_cat: str,
    forecast_vis: Optional[float],
    forecast_ceil: Optional[int],
    forecast_cat: str
) -> str:
    """Calculate weather trend between current and forecast conditions."""
    current_priority = CATEGORY_PRIORITY.get(current_cat, 3)
    forecast_priority = CATEGORY_PRIORITY.get(forecast_cat, 3)

    if forecast_priority < current_priority:
        return "worsening"
    if forecast_priority > current_priority:
        return "improving"

    boundary_thresholds = {
        'VFR': {'ceil': 4000, 'vis': 6.0},
        'MVFR': {'ceil': 1500, 'vis': 3.5},
        'IFR': {'ceil': 700, 'vis': 1.5},
        'LIFR': {'ceil': 200, 'vis': 0.5},
    }
    thresholds = boundary_thresholds.get(forecast_cat, {'ceil': 1000, 'vis': 3.0})

    vis_near_boundary = forecast_vis is not None and forecast_vis <= thresholds['vis']
    ceil_near_boundary = forecast_ceil is not None and forecast_ceil <= thresholds['ceil']

    if vis_near_boundary or ceil_near_boundary:
        vis_decreasing = (current_vis is not None and forecast_vis is not None and
                         forecast_vis < current_vis - 0.5)
        ceil_decreasing = (current_ceil is not None and forecast_ceil is not None and
                          forecast_ceil < current_ceil - 200)
        if (vis_near_boundary and vis_decreasing) or (ceil_near_boundary and ceil_decreasing):
            return "worsening"

    if current_vis is not None and forecast_vis is not None:
        if forecast_vis > current_vis + 2 and forecast_vis > thresholds['vis']:
            return "improving"

    if current_ceil is not None and forecast_ceil is not None:
        if forecast_ceil > current_ceil + 1000 and forecast_ceil > thresholds['ceil']:
            return "improving"

    return "stable"


def _parse_taf_changes(
    taf: str,
    current_category: str,
    current_vis: Optional[float] = None,
    current_ceil: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Parse TAF to find forecast weather changes.

    Trend is calculated relative to the previous period (not always current).
    """
    changes = []
    if not taf:
        return changes

    # Track previous period for trend comparison
    prev_cat = current_category
    prev_vis = current_vis
    prev_ceil = current_ceil

    fm_pattern = r'FM(\d{6})\s+([^\n]+?)(?=\s+FM|\s+TEMPO|\s+BECMG|\s+PROB|$)'
    for match in re.finditer(fm_pattern, taf, re.DOTALL):
        time_str = match.group(1)
        conditions = match.group(2)
        details = _parse_taf_forecast_details(conditions)
        predicted_cat = details['category']
        # Compare to previous period, not always current
        trend = _calculate_trend(
            prev_vis, prev_ceil, prev_cat,
            details['visibility_sm'], details['ceiling_ft'], predicted_cat
        )
        changes.append({
            'type': 'FM',
            'time_str': time_str,
            'category': predicted_cat,
            'visibility_sm': details['visibility_sm'],
            'ceiling_ft': details['ceiling_ft'],
            'ceiling_layer': details['ceiling_layer'],
            'wind': details['wind'],
            'phenomena': details['phenomena'],
            'trend': trend,
            'is_improvement': trend == 'improving',
            'is_deterioration': trend == 'worsening',
        })
        # Update previous for next iteration
        prev_cat = predicted_cat
        prev_vis = details['visibility_sm']
        prev_ceil = details['ceiling_ft']

    # For TEMPO/BECMG, compare to current or most recent FM
    tempo_becmg_pattern = r'(TEMPO|BECMG)\s+(\d{4})/(\d{4})\s+([^\n]+?)(?=\s+TEMPO|\s+BECMG|\s+FM|\s+PROB|$)'
    for match in re.finditer(tempo_becmg_pattern, taf, re.DOTALL):
        change_type = match.group(1)
        from_time = match.group(2)
        to_time = match.group(3)
        conditions = match.group(4)
        details = _parse_taf_forecast_details(conditions)
        predicted_cat = details['category']
        # TEMPO/BECMG compare to current conditions (they're temporary deviations)
        trend = _calculate_trend(
            current_vis, current_ceil, current_category,
            details['visibility_sm'], details['ceiling_ft'], predicted_cat
        )
        changes.append({
            'type': change_type,
            'time_str': f"{from_time}/{to_time}",
            'category': predicted_cat,
            'visibility_sm': details['visibility_sm'],
            'ceiling_ft': details['ceiling_ft'],
            'ceiling_layer': details['ceiling_layer'],
            'wind': details['wind'],
            'phenomena': details['phenomena'],
            'trend': trend,
            'is_improvement': trend == 'improving',
            'is_deterioration': trend == 'worsening',
        })

    return changes


def _format_taf_relative_time(time_str: str) -> str:
    """Format a TAF time string with relative duration."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)

    try:
        if len(time_str) == 6 and time_str.isdigit():
            day = int(time_str[:2])
            hour = int(time_str[2:4])
            minute = int(time_str[4:6])
            target = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
            if target < now - timedelta(days=15):
                if now.month == 12:
                    target = target.replace(year=now.year + 1, month=1)
                else:
                    target = target.replace(month=now.month + 1)
            elif target > now + timedelta(days=15):
                if now.month == 1:
                    target = target.replace(year=now.year - 1, month=12)
                else:
                    target = target.replace(month=now.month - 1)
        elif '/' in time_str:
            start_str = time_str.split('/')[0]
            if len(start_str) == 4 and start_str.isdigit():
                day = int(start_str[:2])
                hour = int(start_str[2:4])
                target = now.replace(day=day, hour=hour, minute=0, second=0, microsecond=0)
                if target < now - timedelta(days=15):
                    if now.month == 12:
                        target = target.replace(year=now.year + 1, month=1)
                    else:
                        target = target.replace(month=now.month + 1)
                elif target > now + timedelta(days=15):
                    if now.month == 1:
                        target = target.replace(year=now.year - 1, month=12)
                    else:
                        target = target.replace(month=now.month - 1)
            else:
                return ""
        else:
            return ""

        diff_seconds = (target - now).total_seconds()
        abs_hours = abs(diff_seconds) / 3600

        if abs_hours < 1:
            mins = int(abs(diff_seconds) / 60)
            time_label = f"{mins:2d}m"
        elif abs_hours < 24:
            hours = int(abs_hours)
            time_label = f"{hours:2d}h"
        else:
            days = int(abs_hours / 24)
            time_label = f"{days:2d}d"

        if diff_seconds > 0:
            return f" [#999999](in {time_label})[/#999999]"
        else:
            return f" [#999999]({time_label} ago)[/#999999]"

    except (ValueError, AttributeError):
        return ""


class WeatherBriefingGenerator:
    """Generates weather briefings for a grouping without UI dependencies."""

    def __init__(
        self,
        grouping_name: str,
        airports: List[str],
        unified_airport_data: Dict[str, Dict],
        disambiguator: AirportDisambiguator,
    ):
        self.grouping_name = grouping_name
        self.airports = airports
        self.unified_airport_data = unified_airport_data
        self.disambiguator = disambiguator
        self.weather_data: Dict[str, Dict[str, Any]] = {}

    def fetch_weather_data(
        self,
        metars: Dict[str, str],
        tafs: Dict[str, str],
        atis_data: Dict[str, Dict],
    ) -> None:
        """Build weather data structure from pre-fetched data."""
        for icao in self.airports:
            metar = metars.get(icao, '')
            taf = tafs.get(icao, '')
            category, color = get_flight_category(metar) if metar else ("UNK", "white")
            visibility_sm = _parse_visibility_sm(metar) if metar else None
            ceiling_ft = _parse_ceiling_feet(metar) if metar else None

            self.weather_data[icao] = {
                'metar': metar,
                'taf': taf,
                'category': category,
                'color': color,
                'visibility': _extract_visibility_str(metar) if metar else None,
                'visibility_sm': visibility_sm,
                'ceiling': _parse_ceiling_layer(metar) if metar else None,
                'ceiling_ft': ceiling_ft,
                'wind': _parse_wind_from_metar(metar) if metar else None,
                'atis': atis_data.get(icao),
                'phenomena': parse_weather_phenomena(metar) if metar else [],
                'taf_changes': _parse_taf_changes(taf, category, visibility_sm, ceiling_ft) if taf else [],
            }

    def _get_airport_size_priority(self, icao: str) -> int:
        """Get airport size priority for sorting (lower = more significant)."""
        airport_info = self.unified_airport_data.get(icao, {})
        return _get_airport_size_priority_impl(airport_info)

    def _get_airport_coords(self, icao: str) -> Optional[Tuple[float, float]]:
        """Get airport coordinates."""
        airport_info = self.unified_airport_data.get(icao, {})
        lat = airport_info.get('latitude')
        lon = airport_info.get('longitude')
        if lat is not None and lon is not None:
            return (lat, lon)
        return None

    def _get_airport_city(self, icao: str) -> str:
        """Get airport city name."""
        airport_info = self.unified_airport_data.get(icao, {})
        return airport_info.get('city', '') or ''

    def _get_airport_state(self, icao: str) -> str:
        """Get airport state/region."""
        airport_info = self.unified_airport_data.get(icao, {})
        return airport_info.get('state', '') or ''

    def _calculate_grouping_extent(self) -> float:
        """Calculate geographic extent of airports."""
        coords = []
        for icao in self.weather_data:
            c = self._get_airport_coords(icao)
            if c:
                coords.append(c)

        if len(coords) < 2:
            return 50.0

        min_lat = min(c[0] for c in coords)
        max_lat = max(c[0] for c in coords)
        min_lon = min(c[1] for c in coords)
        max_lon = max(c[1] for c in coords)
        return haversine_distance_nm(min_lat, min_lon, max_lat, max_lon)

    def _calculate_optimal_k(self, num_towered: int, num_total: int) -> int:
        """Calculate optimal number of clusters."""
        extent = self._calculate_grouping_extent()

        if num_towered <= 1:
            return 1
        elif num_towered <= 3:
            return min(num_towered, 2)
        elif num_towered <= 6:
            return min(num_towered, 3)
        elif num_towered <= 12:
            if extent < 200:
                return 3
            elif extent < 500:
                return 4
            else:
                return min(num_towered, 5)
        else:
            if extent < 200:
                return 4
            elif extent < 500:
                return 5
            elif extent < 1000:
                return 6
            else:
                return min(num_towered, 8)

    def _kmeans_clustering(self, airports: List[tuple], k: int, max_iterations: int = 50) -> List[List[tuple]]:
        """K-means clustering for airports using haversine distance."""
        valid_airports = [a for a in airports if a[3] is not None]

        if not valid_airports:
            return []

        if len(valid_airports) <= k:
            return [[a] for a in valid_airports]

        sorted_by_size = sorted(valid_airports, key=lambda x: (x[2], x[0]))
        centroids = [sorted_by_size[0][3]]

        remaining = sorted_by_size[1:]
        while len(centroids) < k and remaining:
            distances = []
            for airport in remaining:
                coords = airport[3]
                min_dist = min(
                    haversine_distance_nm(coords[0], coords[1], c[0], c[1])
                    for c in centroids
                )
                distances.append((airport, min_dist))
            distances.sort(key=lambda x: -x[1])
            selected = distances[0][0]
            centroids.append(selected[3])
            remaining.remove(selected)

        clusters = [[] for _ in range(k)]

        for _ in range(max_iterations):
            new_clusters = [[] for _ in range(k)]

            for airport in valid_airports:
                coords = airport[3]
                min_dist = float('inf')
                best_cluster = 0

                for i, centroid in enumerate(centroids):
                    dist = haversine_distance_nm(
                        coords[0], coords[1],
                        centroid[0], centroid[1]
                    )
                    if dist < min_dist:
                        min_dist = dist
                        best_cluster = i

                new_clusters[best_cluster].append(airport)

            if new_clusters == clusters:
                break

            clusters = new_clusters

            new_centroids = []
            for i, cluster in enumerate(clusters):
                if cluster:
                    avg_lat = sum(a[3][0] for a in cluster) / len(cluster)
                    avg_lon = sum(a[3][1] for a in cluster) / len(cluster)
                    new_centroids.append((avg_lat, avg_lon))
                else:
                    new_centroids.append(centroids[i])

            centroids = new_centroids

        return [c for c in clusters if c]

    def _generate_area_name(self, centers: List[tuple]) -> str:
        """Generate an area name from center airports."""
        if not centers:
            return "Unknown Area"

        city_names = []
        seen_cities: Set[str] = set()

        for icao, data, size_priority, coords in centers:
            city = self._get_airport_city(icao)
            if not city:
                city = self.disambiguator.get_display_name(icao) if self.disambiguator else icao

            normalized = city.lower().strip()
            for suffix in [' area', ' metro', ' metropolitan', ' region']:
                if normalized.endswith(suffix):
                    normalized = normalized[:-len(suffix)].strip()

            if normalized not in seen_cities:
                seen_cities.add(normalized)
                city_names.append(city)

        if len(city_names) == 1:
            return f"{city_names[0]} Area"
        elif len(city_names) == 2:
            return f"{city_names[0]} / {city_names[1]} Area"
        else:
            return f"{city_names[0]} / {city_names[1]}+ Area"

    def _create_fallback_area_groups(self) -> List[Dict[str, Any]]:
        """Create area groups when no ATIS airports are present."""
        all_airports = [
            (icao, data, self._get_airport_size_priority(icao))
            for icao, data in self.weather_data.items()
            if data.get('category') != 'UNK'
        ]

        if not all_airports:
            return []

        city_groups: Dict[str, List[tuple]] = {}
        state_groups: Dict[str, List[tuple]] = {}
        no_location = []

        for icao, data, size_priority in all_airports:
            city = self._get_airport_city(icao)
            state = self._get_airport_state(icao)

            if city:
                if city not in city_groups:
                    city_groups[city] = []
                city_groups[city].append((icao, data, size_priority))
            elif state:
                if state not in state_groups:
                    state_groups[state] = []
                state_groups[state].append((icao, data, size_priority))
            else:
                no_location.append((icao, data, size_priority))

        result = []

        for city, members in sorted(city_groups.items()):
            members.sort(key=lambda x: (x[2], x[0]))
            result.append({
                'name': f"{city} Area",
                'airports': members,
                'center_icao': None,
            })

        for state, members in sorted(state_groups.items()):
            members.sort(key=lambda x: (x[2], x[0]))
            result.append({
                'name': f"{state} Region",
                'airports': members,
                'center_icao': None,
            })

        if no_location:
            no_location.sort(key=lambda x: (x[2], x[0]))
            result.append({
                'name': "Other Airports",
                'airports': no_location,
                'center_icao': None,
            })

        return result

    def _create_area_groups(self) -> List[Dict[str, Any]]:
        """Create area-based groupings using k-means clustering."""
        towered_airports = []
        non_towered_airports = []

        for icao, data in self.weather_data.items():
            if data.get('category') == 'UNK':
                continue
            coords = self._get_airport_coords(icao)
            size_priority = self._get_airport_size_priority(icao)
            entry = (icao, data, size_priority, coords)

            if size_priority <= 2 or data.get('atis'):
                towered_airports.append(entry)
            else:
                non_towered_airports.append(entry)

        if not towered_airports:
            return self._create_fallback_area_groups()

        num_total = len(towered_airports) + len(non_towered_airports)
        k = self._calculate_optimal_k(len(towered_airports), num_total)

        clusters = self._kmeans_clustering(towered_airports, k)

        if not clusters:
            return self._create_fallback_area_groups()

        cluster_centroids = []
        for cluster in clusters:
            if cluster:
                avg_lat = sum(a[3][0] for a in cluster if a[3]) / len([a for a in cluster if a[3]])
                avg_lon = sum(a[3][1] for a in cluster if a[3]) / len([a for a in cluster if a[3]])
                cluster_centroids.append((avg_lat, avg_lon))
            else:
                cluster_centroids.append(None)

        for airport in non_towered_airports:
            icao, data, size_priority, coords = airport
            if not coords:
                continue

            best_cluster_idx = 0
            best_distance = float('inf')

            for i, centroid in enumerate(cluster_centroids):
                if centroid:
                    dist = haversine_distance_nm(
                        coords[0], coords[1],
                        centroid[0], centroid[1]
                    )
                    if dist < best_distance:
                        best_distance = dist
                        best_cluster_idx = i

            clusters[best_cluster_idx].append(airport)

        area_groups = []
        for cluster in clusters:
            if not cluster:
                continue

            centers = sorted(
                cluster,
                key=lambda x: (
                    0 if x[1].get('atis') else 1,
                    x[2],
                    x[0]
                )
            )

            area_name = self._generate_area_name(centers[:3])

            members = [
                (icao, data, size_priority)
                for icao, data, size_priority, coords in cluster
            ]
            members.sort(key=lambda x: (
                0 if x[1].get('atis') else 1,
                x[2],
                x[0]
            ))

            area_groups.append({
                'name': area_name,
                'airports': members,
                'center_icao': centers[0][0] if centers else None,
            })

        area_groups.sort(key=lambda g: (
            self._get_airport_size_priority(g['center_icao']) if g['center_icao'] else 99,
            g['name']
        ))

        return area_groups

    def _build_airport_card(self, icao: str, data: Dict[str, Any]) -> str:
        """Build a Rich markup card for one airport."""
        lines = []

        category = data.get('category', 'UNK')
        color = CATEGORY_COLORS.get(category, 'white')
        pretty_name = self.disambiguator.get_full_name(icao) if self.disambiguator else icao

        header = f"{icao} - {pretty_name} // [{color} bold]{category}[/{color} bold]"
        lines.append(header)

        conditions_parts = []
        ceiling = data.get('ceiling')
        if ceiling:
            conditions_parts.append(f"Ceiling: {ceiling}")
        else:
            conditions_parts.append("Ceiling: CLR")

        visibility = data.get('visibility')
        if visibility:
            conditions_parts.append(f"Vis: {visibility}")

        wind = data.get('wind')
        if wind:
            conditions_parts.append(f"Wind: {wind}")

        if conditions_parts:
            lines.append("  " + " | ".join(conditions_parts))

        phenomena = data.get('phenomena', [])
        if phenomena:
            lines.append(f"  Weather: {', '.join(phenomena)}")

        taf_changes = data.get('taf_changes', [])
        significant_changes = [c for c in taf_changes if c.get('is_improvement') or c.get('is_deterioration')]

        if significant_changes:
            taf_entries = []
            for change in significant_changes[:2]:
                change_type = change.get('type', '')
                time_str = change.get('time_str', '')
                pred_category = change.get('category', 'UNK')
                trend = change.get('trend', 'stable')

                if trend == 'worsening':
                    indicator = "[#ff9999 bold]▼[/#ff9999 bold]"
                else:
                    indicator = "[#66ff66 bold]▲[/#66ff66 bold]"

                relative_time = _format_taf_relative_time(time_str)
                pred_color = CATEGORY_COLORS.get(pred_category, 'white')

                parts = {'vis': '', 'ceil': '', 'wind': '', 'wx': ''}

                vis_sm = change.get('visibility_sm')
                if vis_sm is not None:
                    if vis_sm >= 6:
                        parts['vis'] = "P6SM"
                    elif vis_sm == int(vis_sm):
                        parts['vis'] = f"{int(vis_sm)}SM"
                    else:
                        parts['vis'] = f"{vis_sm:.1f}SM"

                ceiling_layer = change.get('ceiling_layer')
                if ceiling_layer:
                    parts['ceil'] = ceiling_layer

                taf_wind = change.get('wind')
                if taf_wind:
                    parts['wind'] = taf_wind

                taf_phenomena = change.get('phenomena', [])
                if taf_phenomena:
                    parts['wx'] = ', '.join(taf_phenomena)

                taf_entries.append({
                    'indicator': indicator,
                    'change_type': change_type,
                    'time_str': time_str,
                    'relative_time': relative_time,
                    'pred_category': pred_category,
                    'pred_color': pred_color,
                    'parts': parts,
                })

            def strip_markup(s: str) -> str:
                return re.sub(r'\[/?[^\]]+\]', '', s)

            max_widths = {
                'prefix': max((len(f"TAF {e['change_type']} {e['time_str']}{strip_markup(e['relative_time'])}") for e in taf_entries), default=0),
                'cat': max((len(e['pred_category']) for e in taf_entries), default=0),
                'vis': max((len(e['parts']['vis']) for e in taf_entries), default=0),
                'ceil': max((len(e['parts']['ceil']) for e in taf_entries), default=0),
                'wind': max((len(e['parts']['wind']) for e in taf_entries), default=0),
                'wx': max((len(e['parts']['wx']) for e in taf_entries), default=0),
            }

            for entry in taf_entries:
                parts = entry['parts']
                pred_color = entry['pred_color']
                pred_category = entry['pred_category']

                prefix_plain = f"TAF {entry['change_type']} {entry['time_str']}{strip_markup(entry['relative_time'])}"
                prefix_padding = ' ' * (max_widths['prefix'] - len(prefix_plain))
                prefix_with_markup = f"TAF {entry['change_type']} {entry['time_str']}{entry['relative_time']}{prefix_padding}"

                cat_padded = pred_category.rjust(max_widths['cat'])

                padded_parts = []
                if max_widths['vis'] > 0:
                    padded_parts.append(parts['vis'].rjust(max_widths['vis']))
                if max_widths['ceil'] > 0:
                    padded_parts.append(parts['ceil'].ljust(max_widths['ceil']))
                if max_widths['wind'] > 0:
                    padded_parts.append(parts['wind'].ljust(max_widths['wind']))
                if max_widths['wx'] > 0:
                    padded_parts.append(parts['wx'].ljust(max_widths['wx']))

                details_str = " | ".join(padded_parts).rstrip() if padded_parts else ""
                if details_str:
                    lines.append(f"  {entry['indicator']} {prefix_with_markup}: [{pred_color} bold]{cat_padded}[/{pred_color} bold] - {details_str}")
                else:
                    lines.append(f"  {entry['indicator']} {prefix_with_markup}: [{pred_color} bold]{cat_padded}[/{pred_color} bold]")

        atis = data.get('atis')
        if atis:
            atis_code = atis.get('atis_code', '')
            raw_text = atis.get('text_atis', '')
            filtered_text = filter_atis_text(raw_text)
            if filtered_text:
                display_text = colorize_atis_text(filtered_text, atis_code)
                lines.append(f"  [#aaaaaa]{display_text}[/#aaaaaa]")

        return "\n".join(lines)

    def generate_html(self) -> str:
        """Generate HTML content for the weather briefing."""
        if not self.weather_data:
            return "<html><body><p>No weather data available</p></body></html>"

        # Use StringIO to prevent console output during recording
        console = Console(record=True, force_terminal=True, width=500, file=StringIO())

        now = datetime.now(timezone.utc)
        zulu_str = now.strftime("%Y-%m-%d %H%MZ")
        local_str = now.astimezone().strftime("%H:%M LT")
        console.print(f"[bold]Weather Briefing: {self.grouping_name}[/bold]")
        console.print(f"Generated: [#aaaaff]{zulu_str}[/#aaaaff] ([#ffcc66]{local_str}[/#ffcc66])\n")

        category_counts = {"LIFR": 0, "IFR": 0, "MVFR": 0, "VFR": 0, "UNK": 0}
        for data in self.weather_data.values():
            cat = data.get('category', 'UNK')
            category_counts[cat] = category_counts.get(cat, 0) + 1

        summary_parts = []
        if category_counts["LIFR"] > 0:
            summary_parts.append(f"[#ffaaff]{category_counts['LIFR']} LIFR[/#ffaaff]")
        if category_counts["IFR"] > 0:
            summary_parts.append(f"[#ff9999]{category_counts['IFR']} IFR[/#ff9999]")
        if category_counts["MVFR"] > 0:
            summary_parts.append(f"[#77bbff]{category_counts['MVFR']} MVFR[/#77bbff]")
        if category_counts["VFR"] > 0:
            summary_parts.append(f"[#66ff66]{category_counts['VFR']} VFR[/#66ff66]")

        if summary_parts:
            console.print(" | ".join(summary_parts))
        console.print()

        area_groups = self._create_area_groups()

        for area in area_groups:
            if not area['airports']:
                continue

            console.print(f"[bold #66cccc]━━━ {area['name'].upper()} ━━━[/bold #66cccc]")

            for icao, data, _size in area['airports']:
                card = self._build_airport_card(icao, data)
                console.print(card)
            console.print()

        html_content = console.export_html(inline_styles=True)

        # Add cache control meta tags to prevent browser caching
        html_content = html_content.replace(
            '<head>',
            '''<head>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">'''
        )

        html_content = html_content.replace(
            '</style>',
            '''pre { margin: 0; padding: 0; white-space: pre-wrap; word-wrap: break-word; max-width: 100ch; }
body { margin: 20px; background: #1a1a1a; color: #e0e0e0; }
</style>'''
        )
        html_content = html_content.replace('<body>\n    <pre', '<body>\n<pre')

        return html_content

    def get_category_summary(self) -> Dict[str, int]:
        """Get category counts for this briefing."""
        counts = {"LIFR": 0, "IFR": 0, "MVFR": 0, "VFR": 0, "UNK": 0}
        for data in self.weather_data.values():
            cat = data.get('category', 'UNK')
            counts[cat] = counts.get(cat, 0) + 1
        return counts

    def get_airport_weather_points(self) -> List[Dict[str, Any]]:
        """
        Get per-airport weather data with coordinates for map visualization.

        Returns:
            List of dicts with {icao, lat, lon, category} for each airport
        """
        points = []
        for icao, data in self.weather_data.items():
            coords = self._get_airport_coords(icao)
            if coords:
                points.append({
                    'icao': icao,
                    'lat': coords[0],
                    'lon': coords[1],
                    'category': data.get('category', 'UNK'),
                })
        return points


def _generate_artcc_wide_briefings(
    config: DaemonConfig,
    groupings_to_process: Dict[str, Tuple[List[str], str]],
    metars: Dict[str, str],
    tafs: Dict[str, str],
    atis_data: Dict[str, Any],
    unified_airport_data: Dict[str, Any],
    disambiguator: Any,
    generated_files: Dict[str, str],
) -> None:
    """
    Generate ARTCC-wide briefings containing all airports for each ARTCC.

    This is a shared helper used by both generate_all_briefings and
    generate_with_cached_weather to avoid code duplication.
    """
    from .config import ARTCC_NAMES

    # Collect all unique airports per ARTCC
    artcc_all_airports: Dict[str, Set[str]] = {}
    for grouping_name, (airports, artcc) in groupings_to_process.items():
        if artcc != "custom":
            if artcc not in artcc_all_airports:
                artcc_all_airports[artcc] = set()
            artcc_all_airports[artcc].update(airports)

    if not artcc_all_airports:
        return

    print(f"  Generating {len(artcc_all_airports)} ARTCC-wide briefings...")
    logger.info(f"Generating {len(artcc_all_airports)} ARTCC-wide briefings")

    for artcc, airports in artcc_all_airports.items():
        display_name = ARTCC_NAMES.get(artcc, artcc)
        grouping_name = f"All {display_name} Airports"

        generator = WeatherBriefingGenerator(
            grouping_name=grouping_name,
            airports=list(airports),
            unified_airport_data=unified_airport_data,
            disambiguator=disambiguator,
        )

        generator.fetch_weather_data(metars, tafs, atis_data)
        html_content = generator.generate_html()

        # Write to ARTCC directory as _all.html
        artcc_dir = config.output_dir / artcc
        artcc_dir.mkdir(parents=True, exist_ok=True)
        output_path = artcc_dir / "_all.html"

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        generated_files[str(output_path)] = grouping_name
        logger.debug(f"Generated ARTCC briefing: {output_path}")


def generate_all_briefings(config: DaemonConfig) -> Dict[str, str]:
    """
    Generate weather briefings for all groupings.

    Returns:
        Dict mapping output file paths to grouping names
    """
    start_time = datetime.now(timezone.utc)
    timestamp = start_time.strftime('%H:%M:%SZ')
    print(f"[{timestamp}] Starting weather briefing generation...")
    logger.info(f"Starting weather briefing generation")

    # Load airport data
    print("  Loading airport data...")
    unified_airport_data = load_unified_airport_data(
        str(config.data_dir / "APT_BASE.csv"),
        str(config.data_dir / "airports.json"),
        str(config.data_dir / "iata-icao.csv"),
    )

    # Initialize disambiguator
    print("  Initializing airport disambiguator...")
    airports_json_path = str(config.data_dir / "airports.json")
    disambiguator = AirportDisambiguator(airports_json_path, lazy_load=True, unified_data=unified_airport_data)

    # Load all groupings
    print("  Loading groupings...")
    all_groupings = load_all_groupings(str(config.custom_groupings_path), unified_airport_data)

    # Separate preset and custom groupings
    preset_groupings = load_preset_groupings() if config.include_presets else {}
    custom_groupings = load_custom_groupings(str(config.custom_groupings_path)) if config.include_custom else {}

    # Determine which groupings to process
    groupings_to_process: Dict[str, Tuple[List[str], str]] = {}  # name -> (airports, artcc)

    # Map preset groupings to their ARTCC
    if config.include_presets:
        for json_file in config.preset_groupings_dir.glob("*.json"):
            artcc = json_file.stem  # e.g., "ZOA" from "ZOA.json"
            if config.artcc_filter and artcc not in config.artcc_filter:
                continue

            try:
                with open(json_file, 'r') as f:
                    artcc_groupings = json.load(f)

                for grouping_name, airports in artcc_groupings.items():
                    # Resolve nested groupings
                    resolved = resolve_grouping_recursively(grouping_name, all_groupings)
                    if resolved:
                        groupings_to_process[grouping_name] = (list(resolved), artcc)
            except Exception as e:
                print(f"  Warning: Error loading {json_file}: {e}")

    # Add custom groupings (placed in "custom" directory)
    if config.include_custom and custom_groupings:
        for grouping_name in custom_groupings:
            if grouping_name not in groupings_to_process:
                resolved = resolve_grouping_recursively(grouping_name, all_groupings)
                if resolved:
                    groupings_to_process[grouping_name] = (list(resolved), "custom")

    print(f"  Found {len(groupings_to_process)} groupings to process")

    # Collect all unique airports and determine which ARTCCs are involved
    all_airports: Set[str] = set()
    artccs_involved: Set[str] = set()
    custom_airports: Set[str] = set()  # Airports in custom groupings (no ARTCC bbox)

    for airports, artcc in groupings_to_process.values():
        all_airports.update(airports)
        if artcc != "custom":
            artccs_involved.add(artcc)
        else:
            custom_airports.update(airports)

    num_airports = len(all_airports)
    airports_list = list(all_airports)

    # Check if we have fresh cached weather data
    cache_result = _load_weather_cache(config.weather_cache_dir, max_age_seconds=config.weather_cache_ttl)

    if cache_result is not None:
        metars, tafs, atis_data, cache_timestamp = cache_result
        print(f"  Using cached weather data (from {cache_timestamp})")
        logger.info(f"Using cached weather data from {cache_timestamp}")
        atis_count = len([a for a in atis_data.values() if a]) if atis_data else 0
    else:
        # Fetch fresh weather data using bounding box approach for efficiency
        # This uses ~1 API call per ARTCC instead of ~1 per airport
        metars: Dict[str, str] = {}
        tafs: Dict[str, str] = {}

        if artccs_involved:
            # Get bounding boxes for all ARTCCs
            artcc_bboxes = get_artcc_bboxes(artccs_involved, config.artcc_cache_dir)

            if artcc_bboxes:
                print(f"  Fetching weather via bbox for {len(artcc_bboxes)} ARTCCs ({num_airports} airports)...")
                logger.info(f"Fetching weather via bbox for {len(artcc_bboxes)} ARTCCs")

                bbox_progress = ProgressTracker("Weather fetch (bbox)", len(artcc_bboxes), log_interval_pct=25)

                # Fetch weather for each ARTCC bbox in parallel
                from concurrent.futures import ThreadPoolExecutor, as_completed
                with ThreadPoolExecutor(max_workers=min(5, len(artcc_bboxes))) as executor:
                    future_to_artcc = {
                        executor.submit(fetch_weather_bbox, bbox, True): artcc
                        for artcc, bbox in artcc_bboxes.items()
                    }

                    for future in as_completed(future_to_artcc):
                        artcc = future_to_artcc[future]
                        try:
                            bbox_metars, bbox_tafs = future.result()
                            metars.update(bbox_metars)
                            tafs.update(bbox_tafs)
                        except Exception as e:
                            logger.warning(f"Failed to fetch weather for {artcc}: {e}")

                        bbox_progress.update()

                print(f"    Retrieved {len(metars)} METARs, {len(tafs)} TAFs from bbox queries")
                logger.info(f"Retrieved {len(metars)} METARs, {len(tafs)} TAFs from bbox queries")

        # Fallback: fetch any missing airports individually (e.g., custom groupings or bbox misses)
        # Filter to only airports likely to have METAR reporting:
        # - 4-letter ICAO starting with 'K' (US airports with METAR stations)
        # - OR has an actual control tower (not NON-ATCT)
        # - OR has FAR 139 certification (scheduled passenger service)
        # - OR has an IATA code (commercial airports)
        # This avoids hundreds of wasted requests for small private strips like "68AR", "7CO8"
        def likely_has_metar(icao: str) -> bool:
            if len(icao) == 4 and icao.startswith('K'):
                return True
            airport_info = unified_airport_data.get(icao, {})
            tower_type = airport_info.get('tower_type', '')
            # Only count actual towers, not NON-ATCT
            if tower_type and tower_type != 'NON-ATCT':
                return True
            if airport_info.get('far139'):
                return True
            if airport_info.get('iata'):
                return True
            return False

        missing_airports = [a for a in airports_list if a not in metars]
        fetchable_airports = [a for a in missing_airports if likely_has_metar(a)]
        skipped_count = len(missing_airports) - len(fetchable_airports)

        if fetchable_airports:
            print(f"  Fetching {len(fetchable_airports)} missing airports individually (skipping {skipped_count} unlikely to have METAR)...")
            logger.info(f"Fetching {len(fetchable_airports)} airports individually, skipping {skipped_count} without likely METAR")

            fallback_metars = get_metar_batch(fetchable_airports, max_workers=config.max_workers)
            fallback_tafs = get_taf_batch(fetchable_airports, max_workers=config.max_workers)

            metars.update(fallback_metars)
            tafs.update(fallback_tafs)
        elif skipped_count > 0:
            print(f"  Skipping {skipped_count} airports unlikely to have METAR stations")
            logger.info(f"Skipping {skipped_count} airports unlikely to have METAR stations")

        # Log rate limit status after fetches
        rate_status = get_rate_limit_status()
        if rate_status['is_rate_limited']:
            logger.warning(f"Rate limiting active: backoff={rate_status['backoff_seconds']:.1f}s, errors={rate_status['error_count']}")
        elif rate_status['error_count'] > 0:
            logger.info(f"Rate limit recovery: {rate_status['error_count']} errors, recovered")

        # Fetch VATSIM data for ATIS
        print("  Fetching VATSIM ATIS data...")
        logger.info("Fetching VATSIM ATIS data")
        vatsim_data = download_vatsim_data()
        atis_data = get_atis_for_airports(vatsim_data, airports_list) if vatsim_data else {}
        atis_count = len([a for a in atis_data.values() if a]) if atis_data else 0
        print(f"    Found {atis_count} airports with ATIS")
        logger.info(f"Found {atis_count} airports with active ATIS")

        # Cache weather data for next run
        _save_weather_cache(config.weather_cache_dir, metars, tafs, atis_data)

    num_groupings = len(groupings_to_process)
    print(f"  Generating {num_groupings} briefings...")
    logger.info(f"Generating {num_groupings} briefings")

    # Create output directories
    config.output_dir.mkdir(parents=True, exist_ok=True)

    generated_files: Dict[str, str] = {}
    artcc_groupings_map: Dict[str, List[Dict]] = {}  # artcc -> list of grouping info

    # Helper to get airport's ARTCC from unified data
    def get_airport_artcc(icao: str) -> Optional[str]:
        airport_info = unified_airport_data.get(icao, {})
        # Field is 'artcc' in unified airport data
        artcc_code = airport_info.get('artcc', '')
        if artcc_code and len(artcc_code) == 3:
            return artcc_code
        return None

    # Helper to get airport coordinates
    def get_airport_coords(icao: str) -> Optional[Tuple[float, float]]:
        airport_info = unified_airport_data.get(icao, {})
        lat = airport_info.get('latitude')
        lon = airport_info.get('longitude')
        if lat is not None and lon is not None:
            return (float(lat), float(lon))
        return None

    # Helper to infer ARTCCs for a grouping from its airports
    def infer_artccs_for_grouping(airports: List[str]) -> Set[str]:
        artccs = set()
        for icao in airports:
            artcc_code = get_airport_artcc(icao)
            if artcc_code:
                artccs.add(artcc_code)
        return artccs

    briefing_progress = ProgressTracker("Briefing generation", num_groupings, log_interval_pct=10)

    for grouping_name, (airports, artcc) in groupings_to_process.items():
        # Create generator
        generator = WeatherBriefingGenerator(
            grouping_name=grouping_name,
            airports=airports,
            unified_airport_data=unified_airport_data,
            disambiguator=disambiguator,
        )

        # Populate weather data from batch
        generator.fetch_weather_data(metars, tafs, atis_data)

        # Generate HTML
        html_content = generator.generate_html()

        # Create ARTCC subdirectory
        artcc_dir = config.output_dir / artcc
        artcc_dir.mkdir(parents=True, exist_ok=True)

        # Sanitize filename
        safe_name = re.sub(r'[^\w\-_]', '_', grouping_name)
        output_path = artcc_dir / f"{safe_name}.html"

        # Write file
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        generated_files[str(output_path)] = grouping_name

        # Track for index generation
        category_summary = generator.get_category_summary()
        airport_weather_points = generator.get_airport_weather_points()

        # Collect airport coordinates for hover polygon
        airport_coords = []
        for icao in airports:
            coords = get_airport_coords(icao)
            if coords:
                airport_coords.append(coords)

        grouping_info = {
            'name': grouping_name,
            'filename': f"{safe_name}.html",
            'airport_count': len(airports),
            'categories': category_summary,
            'airport_coords': airport_coords,  # For hover polygon
            'airport_weather_points': airport_weather_points,  # For localized map coloring
        }

        # For custom groupings, infer ARTCCs from airport data
        if artcc == "custom":
            inferred_artccs = infer_artccs_for_grouping(airports)
            if inferred_artccs:
                # Add to each inferred ARTCC
                for inferred_artcc in inferred_artccs:
                    if inferred_artcc not in artcc_groupings_map:
                        artcc_groupings_map[inferred_artcc] = []
                    # Create a copy with the correct path for this ARTCC
                    artcc_info = grouping_info.copy()
                    artcc_info['is_custom'] = True  # Mark as originally custom
                    artcc_info['path_prefix'] = 'custom'  # Files are still in custom dir
                    artcc_groupings_map[inferred_artcc].append(artcc_info)
            else:
                # No ARTCC could be inferred - keep in custom
                if "custom" not in artcc_groupings_map:
                    artcc_groupings_map["custom"] = []
                artcc_groupings_map["custom"].append(grouping_info)
        else:
            if artcc not in artcc_groupings_map:
                artcc_groupings_map[artcc] = []
            artcc_groupings_map[artcc].append(grouping_info)

        briefing_progress.update()

    # Generate ARTCC-wide briefings (all airports in each ARTCC)
    _generate_artcc_wide_briefings(
        config=config,
        groupings_to_process=groupings_to_process,
        metars=metars,
        tafs=tafs,
        atis_data=atis_data,
        unified_airport_data=unified_airport_data,
        disambiguator=disambiguator,
        generated_files=generated_files,
    )

    # Generate weather overlay tiles (if enabled)
    if config.generate_tiles:
        print("  Generating weather overlay tiles...")
        logger.info("Generating weather overlay tiles")

        # Collect all airport weather data for tile generation
        all_airport_weather: Dict[str, Dict] = {}
        for artcc, groupings in artcc_groupings_map.items():
            for g in groupings:
                for point in g.get('airport_weather_points', []):
                    icao = point.get('icao')
                    if icao and icao not in all_airport_weather:
                        all_airport_weather[icao] = point

        valid_weather_count = sum(1 for ap in all_airport_weather.values()
                                   if ap.get('category') in {'VFR', 'MVFR', 'IFR', 'LIFR'})
        print(f"    Collected {valid_weather_count} airports with valid weather (of {len(all_airport_weather)} total)")
        logger.info(f"Collected {valid_weather_count} airports with valid weather (of {len(all_airport_weather)} total)")

        # Get ARTCC boundaries for tile generation
        from .index_generator import CONUS_ARTCCS
        artcc_boundaries = get_artcc_boundaries(config.artcc_cache_dir)

        print(f"    Found {len(artcc_boundaries)} ARTCC boundaries, {len(CONUS_ARTCCS)} in CONUS")
        logger.info(f"Found {len(artcc_boundaries)} ARTCC boundaries, {len(CONUS_ARTCCS)} in CONUS")

        if not all_airport_weather:
            print("    WARNING: No airport weather data collected - skipping tile generation")
            logger.warning("No airport weather data collected - skipping tile generation")
        else:
            # Generate tiles (zoom 4-7: continental to regional view)
            tiles_dir = config.output_dir / "tiles"
            tile_results = generate_weather_tiles(
                artcc_boundaries=artcc_boundaries,
                airport_weather=all_airport_weather,
                output_dir=tiles_dir,
                conus_artccs=CONUS_ARTCCS,
                zoom_levels=(4, 5, 6, 7),
                max_workers=2,  # Keep low for memory-constrained servers
            )

            total_tiles = sum(tile_results.values())
            print(f"    Generated {total_tiles} weather tiles")
            logger.info(f"Generated {total_tiles} weather tiles across {len(tile_results)} zoom levels")
    else:
        print("  Skipping tile generation (--no-tiles)")
        logger.info("Skipping tile generation (--no-tiles)")

    # Generate index if enabled
    if config.generate_index:
        from .index_generator import generate_index_page
        index_path = generate_index_page(config, artcc_groupings_map, unified_airport_data)
        if index_path:
            generated_files[str(index_path)] = "Index"

    # Final summary
    total_time = (datetime.now(timezone.utc) - start_time).total_seconds()
    end_timestamp = datetime.now(timezone.utc).strftime('%H:%M:%SZ')
    print(f"[{end_timestamp}] Generation complete! {len(generated_files)} files in {total_time:.1f}s")
    logger.info(f"Generation complete: {len(generated_files)} files in {total_time:.1f}s")
    return generated_files


def generate_index_only(config: DaemonConfig) -> Dict[str, str]:
    """
    Generate only the index page without fetching weather data.

    Useful for quick updates to the map/UI without re-fetching weather.

    Returns:
        Dict mapping output file path to "Index"
    """
    start_time = datetime.now(timezone.utc)
    timestamp = start_time.strftime('%H:%M:%SZ')
    print(f"[{timestamp}] Regenerating index page only...")
    logger.info("Regenerating index page only")

    # Load airport data
    print("  Loading airport data...")
    unified_airport_data = load_unified_airport_data(
        str(config.data_dir / "APT_BASE.csv"),
        str(config.data_dir / "airports.json"),
        str(config.data_dir / "iata-icao.csv"),
    )

    # Load all groupings
    print("  Loading groupings...")
    all_groupings = load_all_groupings(str(config.custom_groupings_path), unified_airport_data)

    # Separate preset and custom groupings
    preset_groupings = load_preset_groupings() if config.include_presets else {}
    custom_groupings = load_custom_groupings(str(config.custom_groupings_path)) if config.include_custom else {}

    # Determine which groupings to process
    groupings_to_process: Dict[str, Tuple[List[str], str]] = {}  # name -> (airports, artcc)

    # Map preset groupings to their ARTCC
    if config.include_presets:
        for json_file in config.preset_groupings_dir.glob("*.json"):
            artcc = json_file.stem
            if config.artcc_filter and artcc not in config.artcc_filter:
                continue

            try:
                with open(json_file, 'r') as f:
                    artcc_groupings = json.load(f)

                for grouping_name, airports in artcc_groupings.items():
                    resolved = resolve_grouping_recursively(grouping_name, all_groupings)
                    if resolved:
                        groupings_to_process[grouping_name] = (list(resolved), artcc)
            except Exception as e:
                print(f"  Warning: Error loading {json_file}: {e}")

    # Add custom groupings
    if config.include_custom and custom_groupings:
        for grouping_name in custom_groupings:
            if grouping_name not in groupings_to_process:
                resolved = resolve_grouping_recursively(grouping_name, all_groupings)
                if resolved:
                    groupings_to_process[grouping_name] = (list(resolved), "custom")

    print(f"  Found {len(groupings_to_process)} groupings")

    # Helper to get airport's ARTCC from unified data
    def get_airport_artcc(icao: str) -> Optional[str]:
        airport_info = unified_airport_data.get(icao, {})
        artcc_code = airport_info.get('artcc', '')
        if artcc_code and len(artcc_code) == 3:
            return artcc_code
        return None

    # Helper to get airport coordinates
    def get_airport_coords(icao: str) -> Optional[Tuple[float, float]]:
        airport_info = unified_airport_data.get(icao, {})
        lat = airport_info.get('latitude')
        lon = airport_info.get('longitude')
        if lat is not None and lon is not None:
            return (float(lat), float(lon))
        return None

    # Helper to infer ARTCCs for a grouping from its airports
    def infer_artccs_for_grouping(airports: List[str]) -> Set[str]:
        artccs = set()
        for icao in airports:
            artcc_code = get_airport_artcc(icao)
            if artcc_code:
                artccs.add(artcc_code)
        return artccs

    artcc_groupings_map: Dict[str, List[Dict]] = {}

    for grouping_name, (airports, artcc) in groupings_to_process.items():
        # Collect airport coordinates for hover polygon
        airport_coords = []
        for icao in airports:
            coords = get_airport_coords(icao)
            if coords:
                airport_coords.append(coords)

        safe_name = re.sub(r'[^\w\-_]', '_', grouping_name)

        grouping_info = {
            'name': grouping_name,
            'filename': f"{safe_name}.html",
            'airport_count': len(airports),
            'categories': {},  # No weather data
            'airport_coords': airport_coords,
        }

        # For custom groupings, infer ARTCCs from airport data
        if artcc == "custom":
            inferred_artccs = infer_artccs_for_grouping(airports)
            if inferred_artccs:
                for inferred_artcc in inferred_artccs:
                    if inferred_artcc not in artcc_groupings_map:
                        artcc_groupings_map[inferred_artcc] = []
                    artcc_info = grouping_info.copy()
                    artcc_info['is_custom'] = True
                    artcc_info['path_prefix'] = 'custom'
                    artcc_groupings_map[inferred_artcc].append(artcc_info)
            else:
                if "custom" not in artcc_groupings_map:
                    artcc_groupings_map["custom"] = []
                artcc_groupings_map["custom"].append(grouping_info)
        else:
            if artcc not in artcc_groupings_map:
                artcc_groupings_map[artcc] = []
            artcc_groupings_map[artcc].append(grouping_info)

    # Generate index
    generated_files: Dict[str, str] = {}
    from .index_generator import generate_index_page
    index_path = generate_index_page(config, artcc_groupings_map, unified_airport_data)
    if index_path:
        generated_files[str(index_path)] = "Index"

    total_time = (datetime.now(timezone.utc) - start_time).total_seconds()
    end_timestamp = datetime.now(timezone.utc).strftime('%H:%M:%SZ')
    print(f"[{end_timestamp}] Index regeneration complete! {total_time:.1f}s")
    logger.info(f"Index regeneration complete in {total_time:.1f}s")
    return generated_files


def generate_with_cached_weather(config: DaemonConfig) -> Dict[str, str]:
    """
    Generate briefings using cached weather data (no API calls).

    Useful for testing code changes without waiting for weather fetch.

    Returns:
        Dict mapping output file paths to grouping names
    """
    # Load cached weather data
    cache_result = _load_weather_cache(config.weather_cache_dir)
    if cache_result is None:
        print("Error: No cached weather data found. Run without --use-cached first.")
        logger.error("No cached weather data found")
        return {}

    metars, tafs, atis_data, cache_timestamp = cache_result

    start_time = datetime.now(timezone.utc)
    timestamp = start_time.strftime('%H:%M:%SZ')
    print(f"[{timestamp}] Regenerating briefings with cached weather data...")
    print(f"  Cache timestamp: {cache_timestamp}")
    logger.info(f"Regenerating briefings with cached weather from {cache_timestamp}")

    # Load airport data
    print("  Loading airport data...")
    unified_airport_data = load_unified_airport_data(
        str(config.data_dir / "APT_BASE.csv"),
        str(config.data_dir / "airports.json"),
        str(config.data_dir / "iata-icao.csv"),
    )

    # Initialize disambiguator
    print("  Initializing airport disambiguator...")
    airports_json_path = str(config.data_dir / "airports.json")
    disambiguator = AirportDisambiguator(airports_json_path, lazy_load=True, unified_data=unified_airport_data)

    # Load all groupings
    print("  Loading groupings...")
    all_groupings = load_all_groupings(str(config.custom_groupings_path), unified_airport_data)

    # Separate preset and custom groupings
    preset_groupings = load_preset_groupings() if config.include_presets else {}
    custom_groupings = load_custom_groupings(str(config.custom_groupings_path)) if config.include_custom else {}

    # Determine which groupings to process
    groupings_to_process: Dict[str, Tuple[List[str], str]] = {}

    if config.include_presets:
        for json_file in config.preset_groupings_dir.glob("*.json"):
            artcc = json_file.stem
            if config.artcc_filter and artcc not in config.artcc_filter:
                continue

            try:
                with open(json_file, 'r') as f:
                    artcc_groupings = json.load(f)

                for grouping_name, airports in artcc_groupings.items():
                    resolved = resolve_grouping_recursively(grouping_name, all_groupings)
                    if resolved:
                        groupings_to_process[grouping_name] = (list(resolved), artcc)
            except Exception as e:
                print(f"  Warning: Error loading {json_file}: {e}")

    if config.include_custom and custom_groupings:
        for grouping_name in custom_groupings:
            if grouping_name not in groupings_to_process:
                resolved = resolve_grouping_recursively(grouping_name, all_groupings)
                if resolved:
                    groupings_to_process[grouping_name] = (list(resolved), "custom")

    num_groupings = len(groupings_to_process)
    print(f"  Found {num_groupings} groupings to process")
    print(f"  Generating {num_groupings} briefings (using cached weather)...")
    logger.info(f"Generating {num_groupings} briefings with cached weather")

    # Create output directories
    config.output_dir.mkdir(parents=True, exist_ok=True)

    generated_files: Dict[str, str] = {}
    artcc_groupings_map: Dict[str, List[Dict]] = {}

    # Helper to get airport's ARTCC from unified data
    def get_airport_artcc(icao: str) -> Optional[str]:
        airport_info = unified_airport_data.get(icao, {})
        artcc_code = airport_info.get('artcc', '')
        if artcc_code and len(artcc_code) == 3:
            return artcc_code
        return None

    # Helper to get airport coordinates
    def get_airport_coords(icao: str) -> Optional[Tuple[float, float]]:
        airport_info = unified_airport_data.get(icao, {})
        lat = airport_info.get('latitude')
        lon = airport_info.get('longitude')
        if lat is not None and lon is not None:
            return (float(lat), float(lon))
        return None

    # Helper to infer ARTCCs for a grouping from its airports
    def infer_artccs_for_grouping(airports: List[str]) -> Set[str]:
        artccs = set()
        for icao in airports:
            artcc_code = get_airport_artcc(icao)
            if artcc_code:
                artccs.add(artcc_code)
        return artccs

    briefing_progress = ProgressTracker("Briefing generation", num_groupings, log_interval_pct=10)

    for grouping_name, (airports, artcc) in groupings_to_process.items():
        # Create generator
        generator = WeatherBriefingGenerator(
            grouping_name=grouping_name,
            airports=airports,
            unified_airport_data=unified_airport_data,
            disambiguator=disambiguator,
        )

        # Populate weather data from cache
        generator.fetch_weather_data(metars, tafs, atis_data)

        # Generate HTML
        html_content = generator.generate_html()

        # Create ARTCC subdirectory
        artcc_dir = config.output_dir / artcc
        artcc_dir.mkdir(parents=True, exist_ok=True)

        # Sanitize filename
        safe_name = re.sub(r'[^\w\-_]', '_', grouping_name)
        output_path = artcc_dir / f"{safe_name}.html"

        # Write file
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        generated_files[str(output_path)] = grouping_name

        # Track for index generation
        category_summary = generator.get_category_summary()
        airport_weather_points = generator.get_airport_weather_points()

        # Collect airport coordinates for hover polygon
        airport_coords = []
        for icao in airports:
            coords = get_airport_coords(icao)
            if coords:
                airport_coords.append(coords)

        grouping_info = {
            'name': grouping_name,
            'filename': f"{safe_name}.html",
            'airport_count': len(airports),
            'categories': category_summary,
            'airport_coords': airport_coords,
            'airport_weather_points': airport_weather_points,  # For localized map coloring
        }

        # For custom groupings, infer ARTCCs from airport data
        if artcc == "custom":
            inferred_artccs = infer_artccs_for_grouping(airports)
            if inferred_artccs:
                for inferred_artcc in inferred_artccs:
                    if inferred_artcc not in artcc_groupings_map:
                        artcc_groupings_map[inferred_artcc] = []
                    artcc_info = grouping_info.copy()
                    artcc_info['is_custom'] = True
                    artcc_info['path_prefix'] = 'custom'
                    artcc_groupings_map[inferred_artcc].append(artcc_info)
            else:
                if "custom" not in artcc_groupings_map:
                    artcc_groupings_map["custom"] = []
                artcc_groupings_map["custom"].append(grouping_info)
        else:
            if artcc not in artcc_groupings_map:
                artcc_groupings_map[artcc] = []
            artcc_groupings_map[artcc].append(grouping_info)

        briefing_progress.update()

    # Generate ARTCC-wide briefings (all airports in each ARTCC)
    _generate_artcc_wide_briefings(
        config=config,
        groupings_to_process=groupings_to_process,
        metars=metars,
        tafs=tafs,
        atis_data=atis_data,
        unified_airport_data=unified_airport_data,
        disambiguator=disambiguator,
        generated_files=generated_files,
    )

    # Generate weather overlay tiles (if enabled)
    if config.generate_tiles:
        print("  Generating weather overlay tiles...")
        logger.info("Generating weather overlay tiles")

        # Collect all airport weather data for tile generation
        all_airport_weather: Dict[str, Dict] = {}
        for artcc, groupings in artcc_groupings_map.items():
            for g in groupings:
                for point in g.get('airport_weather_points', []):
                    icao = point.get('icao')
                    if icao and icao not in all_airport_weather:
                        all_airport_weather[icao] = point

        valid_weather_count = sum(1 for ap in all_airport_weather.values()
                                   if ap.get('category') in {'VFR', 'MVFR', 'IFR', 'LIFR'})
        print(f"    Collected {valid_weather_count} airports with valid weather (of {len(all_airport_weather)} total)")
        logger.info(f"Collected {valid_weather_count} airports with valid weather (of {len(all_airport_weather)} total)")

        # Get ARTCC boundaries for tile generation
        from .index_generator import CONUS_ARTCCS
        artcc_boundaries = get_artcc_boundaries(config.artcc_cache_dir)

        print(f"    Found {len(artcc_boundaries)} ARTCC boundaries, {len(CONUS_ARTCCS)} in CONUS")
        logger.info(f"Found {len(artcc_boundaries)} ARTCC boundaries, {len(CONUS_ARTCCS)} in CONUS")

        if not all_airport_weather:
            print("    WARNING: No airport weather data collected - skipping tile generation")
            logger.warning("No airport weather data collected - skipping tile generation")
        else:
            # Generate tiles (zoom 4-7: continental to regional view)
            tiles_dir = config.output_dir / "tiles"
            tile_results = generate_weather_tiles(
                artcc_boundaries=artcc_boundaries,
                airport_weather=all_airport_weather,
                output_dir=tiles_dir,
                conus_artccs=CONUS_ARTCCS,
                zoom_levels=(4, 5, 6, 7),
                max_workers=2,  # Keep low for memory-constrained servers
            )

            total_tiles = sum(tile_results.values())
            print(f"    Generated {total_tiles} weather tiles")
            logger.info(f"Generated {total_tiles} weather tiles across {len(tile_results)} zoom levels")
    else:
        print("  Skipping tile generation (--no-tiles)")
        logger.info("Skipping tile generation (--no-tiles)")

    # Generate index if enabled
    if config.generate_index:
        from .index_generator import generate_index_page
        index_path = generate_index_page(config, artcc_groupings_map, unified_airport_data)
        if index_path:
            generated_files[str(index_path)] = "Index"

    total_time = (datetime.now(timezone.utc) - start_time).total_seconds()
    end_timestamp = datetime.now(timezone.utc).strftime('%H:%M:%SZ')
    print(f"[{end_timestamp}] Cached regeneration complete! {len(generated_files)} files in {total_time:.1f}s")
    logger.info(f"Cached regeneration complete: {len(generated_files)} files in {total_time:.1f}s")
    return generated_files


if __name__ == "__main__":
    # Simple test run
    config = DaemonConfig(output_dir=Path("./test_output"))
    generate_all_briefings(config)
