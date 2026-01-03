"""Weather Briefing Modal Screen - Consolidated weather for a grouping/sector"""

import asyncio
import os
import re
import tempfile
import webbrowser
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from rich.console import Console
from textual.screen import ModalScreen
from textual.widgets import Static
from textual.containers import Container, VerticalScroll
from textual.binding import Binding
from textual.app import ComposeResult

from backend import get_metar_batch, get_taf_batch, haversine_distance_nm
from backend.data.vatsim_api import download_vatsim_data, get_atis_for_airports
from backend.data.atis_filter import filter_atis_text, colorize_atis_text
from ui import config
from ui.config import CATEGORY_COLORS, CATEGORY_ORDER
from ui.modals.metar_info import (
    get_flight_category,
    _extract_visibility_str,
    _parse_ceiling_layer,
    _parse_visibility_sm,
    _parse_ceiling_feet,
    parse_weather_phenomena,
)

# Import shared constants from backend
from backend.data.weather_parsing import (
    CATEGORY_PRIORITY,
    FAR139_PRIORITY,
    TOWER_TYPE_PRIORITY,
    get_airport_size_priority as _get_airport_size_priority_impl,
)


def _parse_metar_observation_time(metar: str) -> Optional[tuple]:
    """
    Parse observation time from METAR string.

    Args:
        metar: Raw METAR string

    Returns:
        Tuple of (zulu_str, local_str) like ("1856Z", "10:56 LT") or None
    """
    if not metar:
        return None

    # METAR timestamp format: DDHHMM Z (e.g., "251856Z")
    match = re.search(r'\b(\d{2})(\d{2})(\d{2})Z\b', metar)
    if not match:
        return None

    day = int(match.group(1))
    hour = int(match.group(2))
    minute = int(match.group(3))

    zulu_str = f"{hour:02d}{minute:02d}Z"

    # Convert to local time
    now = datetime.now(timezone.utc)
    try:
        obs_time = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
        # Handle month rollover (observation could be from previous month)
        if obs_time > now + timedelta(hours=2):  # More than 2h in future = likely prev month
            if now.month == 1:
                obs_time = obs_time.replace(year=now.year - 1, month=12)
            else:
                obs_time = obs_time.replace(month=now.month - 1)

        # Convert to local time
        local_time = obs_time.astimezone()
        local_str = local_time.strftime("%H:%M LT")

        return (zulu_str, local_str)
    except (ValueError, OSError):
        return (zulu_str, None)


def _parse_taf_forecast_details(conditions: str) -> Dict[str, Any]:
    """
    Parse detailed forecast conditions from a TAF segment.

    Args:
        conditions: TAF segment conditions string (after FM/TEMPO/BECMG time code)

    Returns:
        Dict with visibility_sm, ceiling_ft, ceiling_layer, wind, phenomena, category
    """
    # Get category for this segment
    category, _ = get_flight_category(conditions)

    # Parse visibility (in SM)
    visibility_sm = _parse_visibility_sm(conditions)

    # Parse ceiling (in feet for trend comparison, and layer string for display)
    ceiling_ft = _parse_ceiling_feet(conditions)
    ceiling_layer = _parse_ceiling_layer(conditions)  # e.g., "BKN020", "OVC008"

    # Parse wind
    wind = _parse_wind_from_metar(conditions)  # Same format as METAR

    # Parse weather phenomena
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
    """
    Calculate weather trend between current and forecast conditions.

    Uses smarter logic: only flags as worsening/improving when:
    - Category actually changes, OR
    - Conditions are approaching a worse category boundary

    Args:
        current_vis: Current visibility in SM
        current_ceil: Current ceiling in feet
        current_cat: Current flight category
        forecast_vis: Forecast visibility in SM
        forecast_ceil: Forecast ceiling in feet
        forecast_cat: Forecast flight category

    Returns:
        "improving", "worsening", or "stable"
    """
    current_priority = CATEGORY_PRIORITY.get(current_cat, 3)
    forecast_priority = CATEGORY_PRIORITY.get(forecast_cat, 3)

    # If category changes, that's definitive
    if forecast_priority < current_priority:
        return "worsening"
    if forecast_priority > current_priority:
        return "improving"

    # Same category - only flag as worsening if approaching the next worse boundary
    # Category boundaries:
    # - VFR: ceil > 3000, vis > 5
    # - MVFR: ceil 1000-3000, vis 3-5
    # - IFR: ceil 500-1000, vis 1-3
    # - LIFR: ceil < 500, vis < 1

    # Define "approaching boundary" thresholds for each category
    boundary_thresholds = {
        'VFR': {'ceil': 4000, 'vis': 6.0},    # Near MVFR boundary
        'MVFR': {'ceil': 1500, 'vis': 3.5},   # Near IFR boundary
        'IFR': {'ceil': 700, 'vis': 1.5},     # Near LIFR boundary
        'LIFR': {'ceil': 200, 'vis': 0.5},    # Already at worst
    }

    thresholds = boundary_thresholds.get(forecast_cat, {'ceil': 1000, 'vis': 3.0})

    # Check if forecast is approaching boundary (worsening within category)
    vis_near_boundary = forecast_vis is not None and forecast_vis <= thresholds['vis']
    ceil_near_boundary = forecast_ceil is not None and forecast_ceil <= thresholds['ceil']

    # Only "worsening" if forecast is dropping AND getting close to boundary
    if vis_near_boundary or ceil_near_boundary:
        # Verify it's actually a decrease from current
        vis_decreasing = (current_vis is not None and forecast_vis is not None and
                         forecast_vis < current_vis - 0.5)
        ceil_decreasing = (current_ceil is not None and forecast_ceil is not None and
                          forecast_ceil < current_ceil - 200)

        if (vis_near_boundary and vis_decreasing) or (ceil_near_boundary and ceil_decreasing):
            return "worsening"

    # Check for improvement - conditions moving away from boundary
    if current_vis is not None and forecast_vis is not None:
        if forecast_vis > current_vis + 2 and forecast_vis > thresholds['vis']:
            return "improving"

    if current_ceil is not None and forecast_ceil is not None:
        if forecast_ceil > current_ceil + 1000 and forecast_ceil > thresholds['ceil']:
            return "improving"

    return "stable"


def _parse_wind_from_metar(metar: str) -> Optional[str]:
    """
    Extract wind string from METAR.

    Args:
        metar: Raw METAR string

    Returns:
        Wind string (e.g., "28012G18KT", "VRB05KT", "00000KT") or None
    """
    if not metar:
        return None

    # Wind pattern: direction + speed + optional gust + unit
    # Examples: 28012KT, 28012G18KT, VRB05KT, 00000KT
    wind_pattern = r'\b(\d{3}|VRB)(\d{2,3})(G\d{2,3})?(KT|MPS|KMH)\b'
    match = re.search(wind_pattern, metar)
    if match:
        return match.group(0)
    return None


def _parse_taf_changes(
    taf: str,
    current_category: str,
    current_vis: Optional[float] = None,
    current_ceil: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Parse TAF to find forecast weather changes with detailed conditions.

    Args:
        taf: Raw TAF string
        current_category: Current flight category (VFR/MVFR/IFR/LIFR)
        current_vis: Current visibility in SM (for trend comparison)
        current_ceil: Current ceiling in feet (for trend comparison)

    Returns:
        List of changes with type, time, predicted category, detailed conditions, and trend
    """
    changes = []
    if not taf:
        return changes

    # Parse FM groups: FM251800 ...conditions...
    fm_pattern = r'FM(\d{6})\s+([^\n]+?)(?=\s+FM|\s+TEMPO|\s+BECMG|\s+PROB|$)'
    for match in re.finditer(fm_pattern, taf, re.DOTALL):
        time_str = match.group(1)
        conditions = match.group(2)

        # Parse detailed forecast conditions
        details = _parse_taf_forecast_details(conditions)
        predicted_cat = details['category']

        # Calculate trend using smarter logic
        trend = _calculate_trend(
            current_vis, current_ceil, current_category,
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

    # Parse TEMPO/BECMG groups
    tempo_becmg_pattern = r'(TEMPO|BECMG)\s+(\d{4})/(\d{4})\s+([^\n]+?)(?=\s+TEMPO|\s+BECMG|\s+FM|\s+PROB|$)'
    for match in re.finditer(tempo_becmg_pattern, taf, re.DOTALL):
        change_type = match.group(1)
        from_time = match.group(2)
        to_time = match.group(3)
        conditions = match.group(4)

        # Parse detailed forecast conditions
        details = _parse_taf_forecast_details(conditions)
        predicted_cat = details['category']

        # Calculate trend using smarter logic
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
    """
    Format a TAF time string with relative duration.

    Args:
        time_str: TAF time string, either "DDHHMM" (FM format) or "HHMM/HHMM" (TEMPO/BECMG)

    Returns:
        Relative time string like "(in 2h)" or "(1h ago)" or empty string if can't parse
    """
    now = datetime.now(timezone.utc)

    try:
        # FM format: DDHHMM (e.g., "251800" = 25th day at 18:00Z)
        if len(time_str) == 6 and time_str.isdigit():
            day = int(time_str[:2])
            hour = int(time_str[2:4])
            minute = int(time_str[4:6])

            # Build target datetime in current month
            target = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)

            # Handle month rollover
            if target < now - timedelta(days=15):
                # Target is likely next month
                if now.month == 12:
                    target = target.replace(year=now.year + 1, month=1)
                else:
                    target = target.replace(month=now.month + 1)
            elif target > now + timedelta(days=15):
                # Target is likely previous month
                if now.month == 1:
                    target = target.replace(year=now.year - 1, month=12)
                else:
                    target = target.replace(month=now.month - 1)

        # TEMPO/BECMG format: DDHH/DDHH (e.g., "2515/2518")
        elif '/' in time_str:
            start_str = time_str.split('/')[0]
            if len(start_str) == 4 and start_str.isdigit():
                day = int(start_str[:2])
                hour = int(start_str[2:4])

                target = now.replace(day=day, hour=hour, minute=0, second=0, microsecond=0)

                # Handle month rollover
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

        # Calculate relative time
        diff_seconds = (target - now).total_seconds()
        abs_hours = abs(diff_seconds) / 3600

        # Right-align number to 2 chars so "9h" aligns with "19h"
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
            return f" [dim](in {time_label})[/dim]"
        else:
            return f" [dim]({time_label} ago)[/dim]"

    except (ValueError, AttributeError):
        return ""


class WeatherBriefingScreen(ModalScreen):
    """Modal screen showing consolidated weather briefing for a grouping/sector"""

    CSS = """
    WeatherBriefingScreen {
        align: center middle;
    }

    #briefing-container {
        width: 90%;
        max-width: 120;
        height: 85%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #briefing-header {
        height: auto;
    }

    #briefing-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #briefing-summary {
        text-align: center;
        margin-bottom: 1;
    }

    #briefing-scroll {
        height: 1fr;
    }

    #briefing-content {
        height: auto;
    }

    .airport-card {
        margin-bottom: 1;
        padding: 0 1;
    }

    #briefing-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("q", "close", "Close"),
        Binding("p", "print", "Print"),
    ]

    def __init__(self, grouping_name: str, airports: List[str], primary_airports: Optional[List[str]] = None):
        """
        Initialize the weather briefing modal.

        Args:
            grouping_name: Name of the grouping/sector
            airports: List of airport ICAO codes in this grouping
            primary_airports: Optional list of primary airports to highlight at the top
        """
        super().__init__()
        self.grouping_name = grouping_name
        self.airports = airports
        self.primary_airports = primary_airports or []
        self.weather_data: Dict[str, Dict[str, Any]] = {}
        self._pending_tasks: list = []

    def compose(self) -> ComposeResult:
        with Container(id="briefing-container"):
            with Container(id="briefing-header"):
                yield Static(f"Weather Briefing: {self.grouping_name}", id="briefing-title")
                yield Static("Loading weather data...", id="briefing-summary")
            with VerticalScroll(id="briefing-scroll"):
                yield Static("", id="briefing-content", markup=True)
            yield Static("Escape/Q: Close | P: Print", id="briefing-hint")

    async def on_mount(self) -> None:
        """Load weather data asynchronously after modal is shown"""
        task = asyncio.create_task(self._fetch_all_weather_async())
        self._pending_tasks.append(task)

    def on_unmount(self) -> None:
        """Cancel pending tasks when modal is dismissed."""
        for task in self._pending_tasks:
            if not task.done():
                task.cancel()
        self._pending_tasks.clear()

    def _update_progress(self, message: str) -> None:
        """Update the progress message in the summary widget."""
        try:
            summary_widget = self.query_one("#briefing-summary", Static)
            summary_widget.update(f"[dim]{message}[/dim]")
        except Exception:
            pass  # Widget may not be ready yet

    async def _fetch_all_weather_async(self) -> None:
        """Fetch all weather data in parallel"""
        loop = asyncio.get_event_loop()
        total_airports = len(self.airports)

        # Track completion status for parallel fetches
        progress = {
            'metar': (0, total_airports),
            'taf': (0, total_airports),
            'vatsim': False
        }

        def update_fetch_progress():
            metar_done, metar_total = progress['metar']
            taf_done, taf_total = progress['taf']
            vatsim_done = progress['vatsim']

            metar_str = f"[green]METAR ✓[/green]" if metar_done == metar_total else f"[dim]METAR {metar_done}/{metar_total}[/dim]"
            taf_str = f"[green]TAF ✓[/green]" if taf_done == taf_total else f"[dim]TAF {taf_done}/{taf_total}[/dim]"
            vatsim_str = f"[green]ATIS ✓[/green]" if vatsim_done else "[dim]ATIS...[/dim]"

            self._update_progress(f"Fetching: {metar_str} | {taf_str} | {vatsim_str}")

        # Progress callbacks for batch functions
        def metar_progress(completed, total):
            progress['metar'] = (completed, total)
            # Schedule UI update on main thread
            loop.call_soon_threadsafe(update_fetch_progress)

        def taf_progress(completed, total):
            progress['taf'] = (completed, total)
            loop.call_soon_threadsafe(update_fetch_progress)

        # Initial progress
        update_fetch_progress()

        # Create tasks for parallel fetching
        async def fetch_metars():
            result = await loop.run_in_executor(
                None,
                lambda: get_metar_batch(self.airports, progress_callback=metar_progress)
            )
            return result

        async def fetch_tafs():
            result = await loop.run_in_executor(
                None,
                lambda: get_taf_batch(self.airports, progress_callback=taf_progress)
            )
            return result

        async def fetch_vatsim():
            result = await loop.run_in_executor(None, download_vatsim_data)
            progress['vatsim'] = True
            update_fetch_progress()
            return result

        # Fetch all in parallel
        metars, tafs, vatsim_data = await asyncio.gather(
            fetch_metars(), fetch_tafs(), fetch_vatsim()
        )

        # Extract ATIS info
        self._update_progress("Extracting ATIS information...")
        await asyncio.sleep(0)
        atis_data = {}
        if vatsim_data:
            atis_data = get_atis_for_airports(vatsim_data, self.airports)

        # Build weather data structure with progress updates
        update_interval = max(1, total_airports // 20)  # Update ~20 times during processing
        for i, icao in enumerate(self.airports):
            if i % update_interval == 0:
                pct = int((i / total_airports) * 100)
                bar_filled = pct // 5  # 20 chars total
                bar_empty = 20 - bar_filled
                bar = f"[green]{'█' * bar_filled}[/green][dim]{'░' * bar_empty}[/dim]"
                self._update_progress(f"Processing weather: {bar} {pct}%")
                await asyncio.sleep(0)

            metar = metars.get(icao, '')
            taf = tafs.get(icao, '')
            category, color = get_flight_category(metar) if metar else ("UNK", "white")

            # Parse visibility and ceiling for trend comparison
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

        # Final progress
        self._update_progress("Building area groups...")
        await asyncio.sleep(0)

        # Update display
        self._update_display()

    def _get_airport_size_priority(self, icao: str) -> int:
        """Get airport size priority for sorting (lower = larger airport)."""
        if not config.UNIFIED_AIRPORT_DATA:
            return 9
        airport_info = config.UNIFIED_AIRPORT_DATA.get(icao, {})
        return _get_airport_size_priority_impl(airport_info)

    def _get_airport_coords(self, icao: str) -> Optional[tuple]:
        """Get airport coordinates (lat, lon) if available."""
        if not config.UNIFIED_AIRPORT_DATA:
            return None
        airport_info = config.UNIFIED_AIRPORT_DATA.get(icao, {})
        lat = airport_info.get('latitude')
        lon = airport_info.get('longitude')
        if lat is not None and lon is not None:
            return (lat, lon)
        return None

    def _get_airport_city(self, icao: str) -> str:
        """Get airport city name if available."""
        if not config.UNIFIED_AIRPORT_DATA:
            return ""
        airport_info = config.UNIFIED_AIRPORT_DATA.get(icao, {})
        return airport_info.get('city', '') or ''

    def _get_airport_state(self, icao: str) -> str:
        """Get airport state/region if available."""
        if not config.UNIFIED_AIRPORT_DATA:
            return ""
        airport_info = config.UNIFIED_AIRPORT_DATA.get(icao, {})
        return airport_info.get('state', '') or ''

    def _calculate_grouping_extent(self) -> float:
        """
        Calculate the geographic extent (max distance) of all airports in the briefing.
        Returns the approximate diagonal distance in nautical miles.
        """
        coords = []
        for icao in self.weather_data:
            c = self._get_airport_coords(icao)
            if c:
                coords.append(c)

        if len(coords) < 2:
            return 50.0  # Default for single airport

        # Find bounding box
        min_lat = min(c[0] for c in coords)
        max_lat = max(c[0] for c in coords)
        min_lon = min(c[1] for c in coords)
        max_lon = max(c[1] for c in coords)

        # Calculate diagonal distance
        diagonal = haversine_distance_nm(min_lat, min_lon, max_lat, max_lon)
        return diagonal

    def _calculate_optimal_k(self, num_towered: int, num_total: int) -> int:
        """
        Calculate optimal number of clusters (k) for k-means.
        Scales based on number of towered airports and geographic extent.
        """
        extent = self._calculate_grouping_extent()

        if num_towered <= 1:
            return 1
        elif num_towered <= 3:
            return min(num_towered, 2)
        elif num_towered <= 6:
            return min(num_towered, 4)
        elif num_towered <= 12:
            # Scale based on extent
            if extent < 200:
                return 4
            elif extent < 500:
                return 5
            else:
                return min(num_towered, 6)
        elif num_towered <= 20:
            # Medium number of towered airports
            if extent < 200:
                return 5
            elif extent < 500:
                return 7
            else:
                return min(num_towered, 8)
        else:
            # Many towered airports - use more clusters for larger areas
            if extent < 200:
                return 6
            elif extent < 500:
                return 8
            elif extent < 1000:
                return 10
            else:
                return min(num_towered, 12)

    def _kmeans_clustering(
        self,
        airports: List[tuple],
        k: int,
        max_iterations: int = 50
    ) -> List[List[tuple]]:
        """
        Simple k-means clustering for airports using haversine distance.

        Args:
            airports: List of (icao, data, size_priority, coords) tuples
            k: Number of clusters
            max_iterations: Maximum iterations for convergence

        Returns:
            List of clusters, each containing airport tuples
        """
        # Filter to airports with valid coordinates
        valid_airports = [a for a in airports if a[3] is not None]

        if not valid_airports:
            return []

        if len(valid_airports) <= k:
            # Each airport is its own cluster
            return [[a] for a in valid_airports]

        # Initialize centroids using k-means++ style selection
        # Start with the largest airport (lowest size_priority)
        sorted_by_size = sorted(valid_airports, key=lambda x: (x[2], x[0]))
        centroids = [sorted_by_size[0][3]]  # First centroid is largest airport

        # Select remaining centroids weighted by distance from existing centroids
        remaining = sorted_by_size[1:]
        while len(centroids) < k and remaining:
            # Calculate min distance to any existing centroid for each airport
            distances = []
            for airport in remaining:
                coords = airport[3]
                min_dist = min(
                    haversine_distance_nm(coords[0], coords[1], c[0], c[1])
                    for c in centroids
                )
                distances.append((airport, min_dist))

            # Select airport with maximum distance (k-means++ style)
            distances.sort(key=lambda x: -x[1])
            selected = distances[0][0]
            centroids.append(selected[3])
            remaining.remove(selected)

        # K-means iterations
        clusters = [[] for _ in range(k)]

        for _ in range(max_iterations):
            # Assignment step: assign each airport to nearest centroid
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

            # Check for convergence
            if new_clusters == clusters:
                break

            clusters = new_clusters

            # Update step: recalculate centroids
            new_centroids = []
            for i, cluster in enumerate(clusters):
                if cluster:
                    avg_lat = sum(a[3][0] for a in cluster) / len(cluster)
                    avg_lon = sum(a[3][1] for a in cluster) / len(cluster)
                    new_centroids.append((avg_lat, avg_lon))
                else:
                    # Empty cluster - keep old centroid
                    new_centroids.append(centroids[i])

            centroids = new_centroids

        # Remove empty clusters
        clusters = [c for c in clusters if c]

        return clusters

    def _create_area_groups(self) -> List[Dict[str, Any]]:
        """
        Create area-based groupings using k-means clustering on towered airports.

        Returns a list of area groups, each containing:
        - name: Area name (e.g., "Sacramento Area")
        - airports: List of (icao, data, size_priority) tuples
        - center_icao: The main towered airport for this area
        """
        # Categorize airports (skip UNK)
        towered_airports = []  # Airports with tower (size_priority <= 2)
        non_towered_airports = []

        for icao, data in self.weather_data.items():
            if data.get('category') == 'UNK':
                continue
            coords = self._get_airport_coords(icao)
            size_priority = self._get_airport_size_priority(icao)
            entry = (icao, data, size_priority, coords)

            # Towered airports have priority 0-2 (ATCT, ATCT-A, NON-ATCT with ATIS)
            # Also include any airport with active ATIS
            if size_priority <= 2 or data.get('atis'):
                towered_airports.append(entry)
            else:
                non_towered_airports.append(entry)

        # If no towered airports, use fallback grouping
        if not towered_airports:
            return self._create_fallback_area_groups()

        # Calculate optimal k for clustering
        num_total = len(towered_airports) + len(non_towered_airports)
        k = self._calculate_optimal_k(len(towered_airports), num_total)

        # Run k-means on towered airports
        clusters = self._kmeans_clustering(towered_airports, k)

        if not clusters:
            return self._create_fallback_area_groups()

        # Calculate cluster centroids for assigning non-towered airports
        cluster_centroids = []
        for cluster in clusters:
            if cluster:
                avg_lat = sum(a[3][0] for a in cluster if a[3]) / len([a for a in cluster if a[3]])
                avg_lon = sum(a[3][1] for a in cluster if a[3]) / len([a for a in cluster if a[3]])
                cluster_centroids.append((avg_lat, avg_lon))
            else:
                cluster_centroids.append(None)

        # Assign non-towered airports to nearest cluster
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

        # Build final area groups with names
        area_groups = []
        for cluster in clusters:
            if not cluster:
                continue

            # Find the "center" airports for naming (towered airports with ATIS first, then by size)
            centers = sorted(
                cluster,
                key=lambda x: (
                    0 if x[1].get('atis') else 1,  # ATIS first
                    x[2],  # Then by size
                    x[0]   # Then alphabetically
                )
            )

            # Generate area name from top centers
            area_name = self._generate_area_name(centers[:3])  # Use top 3 for naming

            # Sort members: ATIS first, then size, then alphabetically
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

        # Sort area groups by the size of their primary airport
        area_groups.sort(key=lambda g: (
            self._get_airport_size_priority(g['center_icao']) if g['center_icao'] else 99,
            g['name']
        ))

        return area_groups

    def _generate_area_name(self, centers: List[tuple]) -> str:
        """
        Generate an area name from the ATIS airport centers.
        Handles deduplication (e.g., "Sacramento / Sacramento" -> "Sacramento Area").
        """
        if not centers:
            return "Unknown Area"

        # Get unique city names
        city_names = []
        seen_cities = set()

        for icao, data, size_priority, coords in centers:
            city = self._get_airport_city(icao)
            if not city:
                # Fall back to airport name
                if config.DISAMBIGUATOR:
                    city = config.DISAMBIGUATOR.get_display_name(icao)
                else:
                    city = icao

            # Normalize for comparison (lowercase, remove common suffixes)
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
            # For 3+ cities, use first two and "+"
            return f"{city_names[0]} / {city_names[1]}+ Area"

    def _group_by_city(self, airports: List[tuple]) -> List[Dict[str, Any]]:
        """Group airports by city name."""
        city_groups: Dict[str, List[tuple]] = {}
        no_city = []

        for icao, data, size_priority in airports:
            city = self._get_airport_city(icao)
            if city:
                if city not in city_groups:
                    city_groups[city] = []
                city_groups[city].append((icao, data, size_priority))
            else:
                no_city.append((icao, data, size_priority))

        result = []
        for city, members in sorted(city_groups.items()):
            members.sort(key=lambda x: (x[2], x[0]))
            result.append({
                'name': f"{city} Area",
                'airports': members,
                'center_icao': None,
            })

        if no_city:
            no_city.sort(key=lambda x: (x[2], x[0]))
            result.append({
                'name': "Other Airports",
                'airports': no_city,
                'center_icao': None,
            })

        return result

    def _create_fallback_area_groups(self) -> List[Dict[str, Any]]:
        """
        Create area groups when no ATIS airports are present.
        Groups by city, state, or geographic proximity.
        """
        all_airports = [
            (icao, data, self._get_airport_size_priority(icao))
            for icao, data in self.weather_data.items()
            if data.get('category') != 'UNK'  # Skip unknown weather
        ]

        if not all_airports:
            return []

        # First, try to group by city
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

        # Add city-based groups
        for city, members in sorted(city_groups.items()):
            members.sort(key=lambda x: (x[2], x[0]))
            result.append({
                'name': f"{city} Area",
                'airports': members,
                'center_icao': None,
            })

        # Add state-based groups
        for state, members in sorted(state_groups.items()):
            members.sort(key=lambda x: (x[2], x[0]))
            result.append({
                'name': f"{state} Region",
                'airports': members,
                'center_icao': None,
            })

        # Add remaining airports
        if no_location:
            no_location.sort(key=lambda x: (x[2], x[0]))
            result.append({
                'name': "Other Airports",
                'airports': no_location,
                'center_icao': None,
            })

        return result

    def _update_display(self) -> None:
        """Update the display with weather data"""
        # Count categories
        category_counts = {"LIFR": 0, "IFR": 0, "MVFR": 0, "VFR": 0, "UNK": 0}
        for data in self.weather_data.values():
            cat = data.get('category', 'UNK')
            category_counts[cat] = category_counts.get(cat, 0) + 1

        # Build summary
        summary_parts = []
        if category_counts["LIFR"] > 0:
            summary_parts.append(f"[magenta]{category_counts['LIFR']} LIFR[/magenta]")
        if category_counts["IFR"] > 0:
            summary_parts.append(f"[red]{category_counts['IFR']} IFR[/red]")
        if category_counts["MVFR"] > 0:
            summary_parts.append(f"[#5599ff]{category_counts['MVFR']} MVFR[/#5599ff]")
        if category_counts["VFR"] > 0:
            summary_parts.append(f"[#00ff00]{category_counts['VFR']} VFR[/#00ff00]")

        # Add timestamp (Zulu + Local)
        now = datetime.now(timezone.utc)
        zulu_str = now.strftime("%H%MZ")
        local_str = now.astimezone().strftime("%H:%M LT")
        timestamp = f"[dim]{zulu_str} ({local_str})[/dim]"

        summary_text = " | ".join(summary_parts) if summary_parts else "No weather data"
        summary_text = f"{timestamp} | {summary_text}" if summary_text else timestamp
        summary_widget = self.query_one("#briefing-summary", Static)
        summary_widget.update(summary_text)

        # Build content sections
        sections = []

        # Primary airports section (highlighted at the top)
        if self.primary_airports:
            primary_section_lines = ["[bold cyan]━━━ REQUESTED ━━━[/bold cyan]"]
            for icao in self.primary_airports:
                data = self.weather_data.get(icao, {})
                if data:
                    card = self._build_airport_card(icao, data, is_primary=True)
                    primary_section_lines.append(card)
            if len(primary_section_lines) > 1:  # Has content beyond header
                sections.append("\n".join(primary_section_lines))

        # Use area-based grouping (centered around ATIS airports)
        # First, temporarily exclude primary airports from weather_data for grouping
        original_weather_data = self.weather_data
        if self.primary_airports:
            self.weather_data = {
                k: v for k, v in self.weather_data.items()
                if k not in self.primary_airports
            }

        # Create area groups
        area_groups = self._create_area_groups()

        # Restore original weather_data
        self.weather_data = original_weather_data

        # Build area sections
        for area in area_groups:
            if not area['airports']:
                continue

            # Section header with area name
            section_header = f"[bold cyan]━━━ {area['name'].upper()} ━━━[/bold cyan]"
            section_lines = [section_header]

            # Airport cards in this area
            for icao, data, _size in area['airports']:
                card = self._build_airport_card(icao, data)
                section_lines.append(card)

            sections.append("\n".join(section_lines))

        content_widget = self.query_one("#briefing-content", Static)
        content_widget.update("\n\n".join(sections) if sections else "[dim]No weather data available[/dim]")

    def _build_airport_card(self, icao: str, data: Dict[str, Any], is_primary: bool = False) -> str:
        """Build a Rich markup card for one airport"""
        lines = []

        # Header with airport name and colored category suffix
        category = data.get('category', 'UNK')
        color = CATEGORY_COLORS.get(category, 'white')
        pretty_name = config.DISAMBIGUATOR.get_full_name(icao) if config.DISAMBIGUATOR else icao

        # Primary airports get bold ICAO
        if is_primary:
            header = f"[bold]{icao}[/bold] - {pretty_name} // [{color} bold]{category}[/{color} bold]"
        else:
            header = f"{icao} - {pretty_name} // [{color} bold]{category}[/{color} bold]"
        lines.append(header)

        # Conditions line
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

        # Weather phenomena line
        phenomena = data.get('phenomena', [])
        if phenomena:
            lines.append(f"  Weather: {', '.join(phenomena)}")

        # TAF changes (only show significant ones - improving or worsening)
        taf_changes = data.get('taf_changes', [])
        significant_changes = [c for c in taf_changes if c.get('is_improvement') or c.get('is_deterioration')]

        if significant_changes:
            # Pre-process all TAF entries to calculate column widths for alignment
            taf_entries = []
            for change in significant_changes[:2]:  # Show at most 2 changes
                change_type = change.get('type', '')
                time_str = change.get('time_str', '')
                pred_category = change.get('category', 'UNK')
                trend = change.get('trend', 'stable')

                # Trend indicator: ▼ worsening, ▲ improving
                if trend == 'worsening':
                    indicator = "[red bold]▼[/red bold]"
                else:
                    indicator = "[green bold]▲[/green bold]"

                relative_time = _format_taf_relative_time(time_str)
                pred_color = CATEGORY_COLORS.get(pred_category, 'white')

                # Build parts dict for each column
                parts = {
                    'vis': '',
                    'ceil': '',
                    'wind': '',
                    'wx': '',
                }

                # Visibility
                vis_sm = change.get('visibility_sm')
                if vis_sm is not None:
                    if vis_sm >= 6:
                        parts['vis'] = "P6SM"
                    elif vis_sm == int(vis_sm):
                        parts['vis'] = f"{int(vis_sm)}SM"
                    else:
                        parts['vis'] = f"{vis_sm:.1f}SM"

                # Ceiling (use METAR format like BKN020)
                ceiling_layer = change.get('ceiling_layer')
                if ceiling_layer:
                    parts['ceil'] = ceiling_layer

                # Wind
                taf_wind = change.get('wind')
                if taf_wind:
                    parts['wind'] = taf_wind

                # Weather phenomena
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

            # Calculate max width for each column (including time prefix and category)
            # Strip markup from relative_time for length calculation
            def strip_markup(s: str) -> str:
                """Remove Rich markup tags for length calculation"""
                import re
                return re.sub(r'\[/?[^\]]+\]', '', s)

            max_widths = {
                'prefix': max((len(f"TAF {e['change_type']} {e['time_str']}{strip_markup(e['relative_time'])}") for e in taf_entries), default=0),
                'cat': max((len(e['pred_category']) for e in taf_entries), default=0),
                'vis': max((len(e['parts']['vis']) for e in taf_entries), default=0),
                'ceil': max((len(e['parts']['ceil']) for e in taf_entries), default=0),
                'wind': max((len(e['parts']['wind']) for e in taf_entries), default=0),
                'wx': max((len(e['parts']['wx']) for e in taf_entries), default=0),
            }

            # Build aligned TAF lines
            for entry in taf_entries:
                parts = entry['parts']
                pred_color = entry['pred_color']
                pred_category = entry['pred_category']

                # Build prefix and pad to align
                prefix_plain = f"TAF {entry['change_type']} {entry['time_str']}{strip_markup(entry['relative_time'])}"
                prefix_padding = ' ' * (max_widths['prefix'] - len(prefix_plain))
                prefix_with_markup = f"TAF {entry['change_type']} {entry['time_str']}{entry['relative_time']}{prefix_padding}"

                # Right-align category (VFR/MVFR/IFR/LIFR) so they line up
                cat_padded = pred_category.rjust(max_widths['cat'])

                # Build padded parts list (only include non-empty columns that have data in any row)
                # Visibility is right-aligned so numbers line up (P6SM aligns with 2SM)
                # Other columns are left-aligned
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

        # ATIS info (filtered to show only non-METAR info)
        atis = data.get('atis')
        if atis:
            atis_code = atis.get('atis_code', '')
            raw_text = atis.get('text_atis', '')
            # Filter out METAR-duplicated info
            filtered_text = filter_atis_text(raw_text)
            if filtered_text:
                # Colorize ATIS letter if present
                display_text = colorize_atis_text(filtered_text, atis_code)
                lines.append(f"  [dim]{display_text}[/dim]")

        return "\n".join(lines)

    def action_close(self) -> None:
        """Close the modal"""
        self.dismiss()

    def action_print(self) -> None:
        """Export weather briefing as HTML and open in browser"""
        if not self.weather_data:
            self.notify("No weather data to export", severity="warning")
            return

        # Build the content using Rich Console for HTML export
        # Use wide width to prevent Rich from wrapping - let CSS handle wrapping instead
        console = Console(record=True, force_terminal=True, width=500)

        # Title and timestamp (Zulu only for web - local time varies by viewer)
        now = datetime.now(timezone.utc)
        zulu_str = now.strftime("%Y-%m-%d %H%MZ")
        console.print(f"[bold]Weather Briefing: {self.grouping_name}[/bold]")
        console.print(f"Generated: {zulu_str}\n")

        # Summary
        category_counts = {"LIFR": 0, "IFR": 0, "MVFR": 0, "VFR": 0, "UNK": 0}
        for data in self.weather_data.values():
            cat = data.get('category', 'UNK')
            category_counts[cat] = category_counts.get(cat, 0) + 1

        summary_parts = []
        if category_counts["LIFR"] > 0:
            summary_parts.append(f"[magenta]{category_counts['LIFR']} LIFR[/magenta]")
        if category_counts["IFR"] > 0:
            summary_parts.append(f"[red]{category_counts['IFR']} IFR[/red]")
        if category_counts["MVFR"] > 0:
            summary_parts.append(f"[#5599ff]{category_counts['MVFR']} MVFR[/#5599ff]")
        if category_counts["VFR"] > 0:
            summary_parts.append(f"[#00ff00]{category_counts['VFR']} VFR[/#00ff00]")

        if summary_parts:
            console.print(" | ".join(summary_parts))
        console.print()

        # Create area groups (centered around ATIS airports)
        area_groups = self._create_area_groups()

        # Build content with area sections
        for area in area_groups:
            if not area['airports']:
                continue

            console.print(f"[bold cyan]━━━ {area['name'].upper()} ━━━[/bold cyan]")

            for icao, data, _size in area['airports']:
                card = self._build_airport_card(icao, data)
                console.print(card)
            console.print()

        # Export to HTML
        html_content = console.export_html(inline_styles=True)

        # Add CSS for better printing - remove default padding/margins, wrap long lines
        html_content = html_content.replace(
            '</style>',
            '''pre { margin: 0; padding: 0; white-space: pre-wrap; word-wrap: break-word; max-width: 100ch; }
body { margin: 20px; }
</style>'''
        )
        # Remove HTML indentation that causes left padding
        html_content = html_content.replace('<body>\n    <pre', '<body>\n<pre')

        # Write to temp file and open in browser
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.html',
            prefix=f'weather_briefing_{self.grouping_name}_',
            delete=False,
            encoding='utf-8'
        ) as f:
            f.write(html_content)
            temp_path = f.name

        # Open in default browser
        webbrowser.open(f'file://{temp_path}')
        self.notify(f"Opened in browser: {os.path.basename(temp_path)}")
