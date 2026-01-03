"""
Area clustering logic for weather briefings.

This module provides shared clustering functionality used by both the
UI weather briefing modal and the weather daemon HTML generator.
"""

from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from backend.core.calculations import haversine_distance_nm
from backend.data.weather_parsing import get_airport_size_priority as _get_size_priority


# Type aliases for clarity
AirportEntry = Tuple[str, Dict[str, Any], int, Optional[Tuple[float, float]]]  # (icao, data, size_priority, coords)
AirportMember = Tuple[str, Dict[str, Any], int]  # (icao, data, size_priority)


class AreaClusterer:
    """
    Clusters airports into geographic areas for weather briefings.

    This class handles k-means clustering of airports based on their
    geographic locations, grouping them around major towered airports.
    """

    def __init__(
        self,
        weather_data: Dict[str, Dict[str, Any]],
        unified_airport_data: Dict[str, Dict[str, Any]],
        disambiguator: Any = None,
    ):
        """
        Initialize the clusterer.

        Args:
            weather_data: Dict mapping ICAO to weather data (must have 'category' key)
            unified_airport_data: Dict mapping ICAO to airport info (coords, city, etc.)
            disambiguator: Optional AirportDisambiguator for display names
        """
        self.weather_data = weather_data
        self.unified_airport_data = unified_airport_data
        self.disambiguator = disambiguator

    def get_airport_size_priority(self, icao: str) -> int:
        """Get airport size priority for sorting (lower = more significant)."""
        airport_info = self.unified_airport_data.get(icao, {})
        return _get_size_priority(airport_info)

    def get_airport_coords(self, icao: str) -> Optional[Tuple[float, float]]:
        """Get airport coordinates (lat, lon) if available."""
        airport_info = self.unified_airport_data.get(icao, {})
        lat = airport_info.get('latitude')
        lon = airport_info.get('longitude')
        if lat is not None and lon is not None:
            return (lat, lon)
        return None

    def get_airport_city(self, icao: str) -> str:
        """Get airport city name if available."""
        airport_info = self.unified_airport_data.get(icao, {})
        return airport_info.get('city', '') or ''

    def get_airport_state(self, icao: str) -> str:
        """Get airport state/region if available."""
        airport_info = self.unified_airport_data.get(icao, {})
        return airport_info.get('state', '') or ''

    def calculate_grouping_extent(self) -> float:
        """
        Calculate the geographic extent (max distance) of all airports.

        Returns:
            Approximate diagonal distance in nautical miles.
        """
        coords = []
        for icao in self.weather_data:
            c = self.get_airport_coords(icao)
            if c:
                coords.append(c)

        if len(coords) < 2:
            return 50.0  # Default for single airport

        min_lat = min(c[0] for c in coords)
        max_lat = max(c[0] for c in coords)
        min_lon = min(c[1] for c in coords)
        max_lon = max(c[1] for c in coords)

        return haversine_distance_nm(min_lat, min_lon, max_lat, max_lon)

    def calculate_optimal_k(self, num_towered: int, num_total: int) -> int:
        """
        Calculate optimal number of clusters (k) for k-means.

        Scales based on number of towered airports and geographic extent.
        """
        extent = self.calculate_grouping_extent()

        if num_towered <= 1:
            return 1
        elif num_towered <= 3:
            return min(num_towered, 2)
        elif num_towered <= 6:
            return min(num_towered, 4)
        elif num_towered <= 12:
            if extent < 200:
                return 5
            elif extent < 500:
                return 6
            else:
                return min(num_towered, 8)
        elif num_towered <= 25:
            # Medium number of towered airports
            if extent < 200:
                return 6
            elif extent < 500:
                return 8
            else:
                return min(num_towered, 10)
        elif num_towered <= 40:
            # Large groupings (e.g., ARTCC-wide)
            if extent < 300:
                return 8
            elif extent < 600:
                return 10
            else:
                return min(num_towered, 14)
        else:
            # Very large groupings - scale more aggressively
            if extent < 300:
                return 10
            elif extent < 600:
                return 12
            elif extent < 1000:
                return 15
            else:
                return min(num_towered, 18)

    def kmeans_clustering(
        self,
        airports: List[AirportEntry],
        k: int,
        max_iterations: int = 50
    ) -> List[List[AirportEntry]]:
        """
        Simple k-means clustering for airports using haversine distance.

        Args:
            airports: List of (icao, data, size_priority, coords) tuples
            k: Number of clusters
            max_iterations: Maximum iterations for convergence

        Returns:
            List of clusters, each containing airport tuples
        """
        valid_airports = [a for a in airports if a[3] is not None]

        if not valid_airports:
            return []

        if len(valid_airports) <= k:
            return [[a] for a in valid_airports]

        # Initialize centroids using k-means++ style selection
        sorted_by_size = sorted(valid_airports, key=lambda x: (x[2], x[0]))
        centroids = [sorted_by_size[0][3]]  # First centroid is largest airport

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

        clusters: List[List[AirportEntry]] = [[] for _ in range(k)]

        for _ in range(max_iterations):
            new_clusters: List[List[AirportEntry]] = [[] for _ in range(k)]

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

    def generate_area_name(self, centers: List[AirportEntry]) -> str:
        """
        Generate an area name from the center airports.

        Handles deduplication (e.g., "Sacramento / Sacramento" -> "Sacramento Area").
        """
        if not centers:
            return "Unknown Area"

        city_names = []
        seen_cities: Set[str] = set()

        for icao, data, size_priority, coords in centers:
            city = self.get_airport_city(icao)
            if not city and self.disambiguator:
                city = self.disambiguator.get_display_name(icao)
            if not city:
                city = icao

            # Normalize for comparison
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

    def create_fallback_area_groups(self) -> List[Dict[str, Any]]:
        """
        Create area groups when no ATIS airports are present.

        Groups by city, state, or geographic proximity.
        """
        all_airports = [
            (icao, data, self.get_airport_size_priority(icao))
            for icao, data in self.weather_data.items()
            if data.get('category') != 'UNK'
        ]

        if not all_airports:
            return []

        city_groups: Dict[str, List[AirportMember]] = {}
        state_groups: Dict[str, List[AirportMember]] = {}
        no_location: List[AirportMember] = []

        for icao, data, size_priority in all_airports:
            city = self.get_airport_city(icao)
            state = self.get_airport_state(icao)

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

    def create_area_groups(self) -> List[Dict[str, Any]]:
        """
        Create area-based groupings using k-means clustering.

        Returns a list of area groups, each containing:
        - name: Area name (e.g., "Sacramento Area")
        - airports: List of (icao, data, size_priority) tuples
        - center_icao: The main towered airport for this area
        """
        towered_airports: List[AirportEntry] = []
        non_towered_airports: List[AirportEntry] = []

        for icao, data in self.weather_data.items():
            if data.get('category') == 'UNK':
                continue
            coords = self.get_airport_coords(icao)
            size_priority = self.get_airport_size_priority(icao)
            entry: AirportEntry = (icao, data, size_priority, coords)

            # Towered airports have priority 0-2 or have active ATIS
            if size_priority <= 2 or data.get('atis'):
                towered_airports.append(entry)
            else:
                non_towered_airports.append(entry)

        if not towered_airports:
            return self.create_fallback_area_groups()

        num_total = len(towered_airports) + len(non_towered_airports)
        k = self.calculate_optimal_k(len(towered_airports), num_total)

        clusters = self.kmeans_clustering(towered_airports, k)

        if not clusters:
            return self.create_fallback_area_groups()

        # Calculate cluster centroids for assigning non-towered airports
        cluster_centroids: List[Optional[Tuple[float, float]]] = []
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

        # Build final area groups
        area_groups = []
        for cluster in clusters:
            if not cluster:
                continue

            # Find center airports for naming (ATIS first, then by size)
            centers = sorted(
                cluster,
                key=lambda x: (
                    0 if x[1].get('atis') else 1,
                    x[2],
                    x[0]
                )
            )

            area_name = self.generate_area_name(centers[:3])

            # Convert to member format (drop coords)
            members: List[AirportMember] = [
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

        # Sort by primary airport size
        area_groups.sort(key=lambda g: (
            self.get_airport_size_priority(g['center_icao']) if g['center_icao'] else 99,
            g['name']
        ))

        return area_groups


def count_area_categories(airports: List[AirportMember]) -> Dict[str, int]:
    """
    Count flight categories for a list of airports.

    Args:
        airports: List of (icao, data, size_priority) tuples

    Returns:
        Dict with counts for LIFR, IFR, MVFR, VFR
    """
    counts = {"LIFR": 0, "IFR": 0, "MVFR": 0, "VFR": 0}
    for _, data, _ in airports:
        cat = data.get('category', 'UNK')
        if cat in counts:
            counts[cat] += 1
    return counts


def build_area_summary(counts: Dict[str, int], color_scheme: str = "ui") -> str:
    """
    Build a formatted summary string from category counts.

    Args:
        counts: Dict with LIFR, IFR, MVFR, VFR counts
        color_scheme: Either "ui" (Textual colors) or "html" (hex colors for web)

    Returns:
        Rich markup string like "1 IFR | 5 VFR"
    """
    if color_scheme == "html":
        colors = {
            "LIFR": "#ffaaff",
            "IFR": "#ff9999",
            "MVFR": "#77bbff",
            "VFR": "#66ff66",
        }
    else:  # ui
        colors = {
            "LIFR": "magenta",
            "IFR": "red",
            "MVFR": "#5599ff",
            "VFR": "#00ff00",
        }

    parts = []
    if counts.get("LIFR", 0) > 0:
        c = colors["LIFR"]
        parts.append(f"[{c}]{counts['LIFR']} LIFR[/{c}]")
    if counts.get("IFR", 0) > 0:
        c = colors["IFR"]
        parts.append(f"[{c}]{counts['IFR']} IFR[/{c}]")
    if counts.get("MVFR", 0) > 0:
        c = colors["MVFR"]
        parts.append(f"[{c}]{counts['MVFR']} MVFR[/{c}]")
    if counts.get("VFR", 0) > 0:
        c = colors["VFR"]
        parts.append(f"[{c}]{counts['VFR']} VFR[/{c}]")

    return " | ".join(parts) if parts else ""
