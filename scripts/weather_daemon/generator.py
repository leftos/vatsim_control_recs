"""
Headless Weather Briefing Generator

Generates HTML weather briefings without requiring the Textual UI.
Reuses core logic from ui/modals/weather_briefing.py.
"""

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from rich.console import Console

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend import (
    get_metar_batch,
    get_taf_batch,
    haversine_distance_nm,
    load_all_groupings,
    load_unified_airport_data,
)
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

# Category priority for trend comparison (lower = worse conditions)
CATEGORY_PRIORITY = {"LIFR": 0, "IFR": 1, "MVFR": 2, "VFR": 3}

# Tower type priority for sorting (lower = larger/more significant airport)
TOWER_TYPE_PRIORITY = {
    'ATCT-TRACON': 0,
    'ATCT-RAPCON': 0,
    'ATCT-RATCF': 0,
    'ATCT-A/C': 1,
    'ATCT': 2,
    'NON-ATCT': 3,
    '': 4,
}


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
    """Parse TAF to find forecast weather changes."""
    changes = []
    if not taf:
        return changes

    fm_pattern = r'FM(\d{6})\s+([^\n]+?)(?=\s+FM|\s+TEMPO|\s+BECMG|\s+PROB|$)'
    for match in re.finditer(fm_pattern, taf, re.DOTALL):
        time_str = match.group(1)
        conditions = match.group(2)
        details = _parse_taf_forecast_details(conditions)
        predicted_cat = details['category']
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

    tempo_becmg_pattern = r'(TEMPO|BECMG)\s+(\d{4})/(\d{4})\s+([^\n]+?)(?=\s+TEMPO|\s+BECMG|\s+FM|\s+PROB|$)'
    for match in re.finditer(tempo_becmg_pattern, taf, re.DOTALL):
        change_type = match.group(1)
        from_time = match.group(2)
        to_time = match.group(3)
        conditions = match.group(4)
        details = _parse_taf_forecast_details(conditions)
        predicted_cat = details['category']
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
            return f" [dim](in {time_label})[/dim]"
        else:
            return f" [dim]({time_label} ago)[/dim]"

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
        """Get airport size priority for sorting."""
        airport_info = self.unified_airport_data.get(icao, {})
        tower_type = airport_info.get('tower_type', '')
        return TOWER_TYPE_PRIORITY.get(tower_type, 4)

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
                    indicator = "[red bold]▼[/red bold]"
                else:
                    indicator = "[green bold]▲[/green bold]"

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
                lines.append(f"  [dim]{display_text}[/dim]")

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
        console.print(f"Generated: {zulu_str} ({local_str})\n")

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

        area_groups = self._create_area_groups()

        for area in area_groups:
            if not area['airports']:
                continue

            console.print(f"[bold cyan]━━━ {area['name'].upper()} ━━━[/bold cyan]")

            for icao, data, _size in area['airports']:
                card = self._build_airport_card(icao, data)
                console.print(card)
            console.print()

        html_content = console.export_html(inline_styles=True)

        html_content = html_content.replace(
            '</style>',
            '''pre { margin: 0; padding: 0; white-space: pre-wrap; word-wrap: break-word; max-width: 100ch; }
body { margin: 20px; }
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


def generate_all_briefings(config: DaemonConfig) -> Dict[str, str]:
    """
    Generate weather briefings for all groupings.

    Returns:
        Dict mapping output file paths to grouping names
    """
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%SZ')}] Starting weather briefing generation...")

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

    # Collect all unique airports
    all_airports: Set[str] = set()
    for airports, _ in groupings_to_process.values():
        all_airports.update(airports)

    print(f"  Fetching weather for {len(all_airports)} unique airports...")

    # Batch fetch all weather data
    airports_list = list(all_airports)
    metars = get_metar_batch(airports_list, max_workers=config.max_workers)
    tafs = get_taf_batch(airports_list, max_workers=config.max_workers)

    # Fetch VATSIM data for ATIS
    print("  Fetching VATSIM ATIS data...")
    vatsim_data = download_vatsim_data()
    atis_data = get_atis_for_airports(vatsim_data, airports_list) if vatsim_data else {}

    print(f"  Generating {len(groupings_to_process)} briefings...")

    # Create output directories
    config.output_dir.mkdir(parents=True, exist_ok=True)

    generated_files: Dict[str, str] = {}
    artcc_groupings_map: Dict[str, List[Dict]] = {}  # artcc -> list of grouping info

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
        if artcc not in artcc_groupings_map:
            artcc_groupings_map[artcc] = []

        category_summary = generator.get_category_summary()
        artcc_groupings_map[artcc].append({
            'name': grouping_name,
            'filename': f"{safe_name}.html",
            'airport_count': len(airports),
            'categories': category_summary,
        })

    print(f"  Generated {len(generated_files)} briefing files")

    # Generate index if enabled
    if config.generate_index:
        from .index_generator import generate_index_page
        index_path = generate_index_page(config, artcc_groupings_map)
        if index_path:
            generated_files[str(index_path)] = "Index"

    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%SZ')}] Generation complete!")
    return generated_files


if __name__ == "__main__":
    # Simple test run
    config = DaemonConfig(output_dir=Path("./test_output"))
    generate_all_briefings(config)
