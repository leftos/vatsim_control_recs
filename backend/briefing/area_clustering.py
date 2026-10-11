"""
Area clustering logic for weather briefings.

This module provides shared clustering functionality used by both the
UI weather briefing modal and the weather daemon HTML generator.
"""

from collections import Counter
from collections.abc import Sequence
from typing import Any, cast

from backend.core.calculations import haversine_distance_nm
from backend.data.weather_parsing import get_airport_size_priority as _get_size_priority

# Type aliases for clarity
Coords = tuple[float, float]  # (lat, lon)
AirportEntry = tuple[
    str, dict[str, Any], int, Coords | None
]  # (icao, data, size_priority, coords)
ValidAirportEntry = tuple[
    str, dict[str, Any], int, Coords
]  # Same but with non-None coords
AirportMember = tuple[str, dict[str, Any], int]  # (icao, data, size_priority)

# For seven or more towered airports: (extent limit in nm, k) steps tried in order,
# then the cap on k once the extent passes the last limit.
ExtentTier = tuple[tuple[tuple[float, int], ...], int]
_EXTENT_TIERS_BY_TOWERED: tuple[tuple[int, ExtentTier], ...] = (
    (12, (((200, 5), (500, 6)), 8)),
    (25, (((200, 6), (500, 8)), 10)),
    # Large groupings (e.g., ARTCC-wide)
    (40, (((300, 8), (600, 10)), 14)),
)
# Very large groupings - scale more aggressively
_VERY_LARGE_EXTENT_TIER: ExtentTier = (((300, 10), (600, 12), (1000, 15)), 18)

_CITY_SUFFIXES = (" area", " metro", " metropolitan", " region")


def _extent_tier(num_towered: int) -> ExtentTier:
    """The extent steps and k cap for seven or more towered airports."""
    for max_towered, tier in _EXTENT_TIERS_BY_TOWERED:
        if num_towered <= max_towered:
            return tier
    return _VERY_LARGE_EXTENT_TIER


def _k_for_extent(num_towered: int, extent: float) -> int:
    """Cluster count for seven or more towered airports spread over ``extent`` nm."""
    extent_steps, cap = _extent_tier(num_towered)
    for extent_limit, k in extent_steps:
        if extent < extent_limit:
            return k
    return min(num_towered, cap)


def _distance_nm(a: Coords, b: Coords) -> float:
    return haversine_distance_nm(a[0], a[1], b[0], b[1])


def _mean_coords(points: Sequence[Coords]) -> Coords:
    avg_lat = sum(p[0] for p in points) / len(points)
    avg_lon = sum(p[1] for p in points) / len(points)
    return (avg_lat, avg_lon)


def _nearest_index(coords: Coords, centroids: Sequence[Coords]) -> int:
    """Index of the centroid nearest ``coords`` (the first on a tie)."""
    best_index = 0
    best_distance = float("inf")
    for i, centroid in enumerate(centroids):
        dist = _distance_nm(coords, centroid)
        if dist < best_distance:
            best_distance = dist
            best_index = i
    return best_index


def _seed_centroids(airports: list[ValidAirportEntry], k: int) -> list[Coords]:
    """k-means++ style seeds: the largest airport, then each time the farthest one."""
    sorted_by_size = sorted(airports, key=lambda x: (x[2], x[0]))
    centroids: list[Coords] = [sorted_by_size[0][3]]
    remaining = sorted_by_size[1:]
    while len(centroids) < k and remaining:
        selected = max(
            remaining,
            key=lambda airport: min(_distance_nm(airport[3], c) for c in centroids),
        )
        centroids.append(selected[3])
        remaining.remove(selected)
    return centroids


def _assign_to_centroids(
    airports: list[ValidAirportEntry], centroids: list[Coords]
) -> list[list[ValidAirportEntry]]:
    """Put each airport in the cluster of its nearest centroid."""
    clusters: list[list[ValidAirportEntry]] = [[] for _ in centroids]
    for airport in airports:
        clusters[_nearest_index(airport[3], centroids)].append(airport)
    return clusters


def _updated_centroids(
    clusters: list[list[ValidAirportEntry]], centroids: list[Coords]
) -> list[Coords]:
    """Each cluster's mean position; an empty cluster keeps its old centroid."""
    return [
        _mean_coords([a[3] for a in cluster]) if cluster else centroids[i]
        for i, cluster in enumerate(clusters)
    ]


def _normalized_city(city: str) -> str:
    """The city lower-cased and trimmed, without an "area"/"metro"/... suffix."""
    normalized = city.lower().strip()
    for suffix in _CITY_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
    return normalized


def _join_area_name(city_names: list[str]) -> str:
    if len(city_names) == 1:
        return f"{city_names[0]} Area"
    if len(city_names) == 2:
        return f"{city_names[0]} / {city_names[1]} Area"
    return f"{city_names[0]} / {city_names[1]}+ Area"


def _by_size_then_icao(member: AirportMember) -> tuple[int, str]:
    return (member[2], member[0])


def _named_location_groups(
    groups: dict[str, list[AirportMember]], suffix: str
) -> list[dict[str, Any]]:
    """One centre-less area group per location, named "<location> <suffix>"."""
    result = []
    for location, members in sorted(groups.items()):
        members.sort(key=_by_size_then_icao)
        result.append(
            {
                "name": f"{location} {suffix}",
                "airports": members,
                "center_icao": None,
            }
        )
    return result


def _attach_untowered(
    clusters: list[list[AirportEntry]], untowered: list[AirportEntry]
) -> None:
    """Add each located untowered airport to the cluster whose mean is nearest."""
    centroids: list[Coords] = [
        _mean_coords([a[3] for a in cluster if a[3]]) for cluster in clusters
    ]
    for airport in untowered:
        coords = airport[3]
        if not coords:
            continue
        clusters[_nearest_index(coords, centroids)].append(airport)


class AreaClusterer:
    """
    Clusters airports into geographic areas for weather briefings.

    This class handles k-means clustering of airports based on their
    geographic locations, grouping them around major towered airports.
    """

    def __init__(
        self,
        weather_data: dict[str, dict[str, Any]],
        unified_airport_data: dict[str, dict[str, Any]],
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

    def get_airport_coords(self, icao: str) -> Coords | None:
        """Get airport coordinates (lat, lon) if available."""
        airport_info = self.unified_airport_data.get(icao, {})
        lat = airport_info.get("latitude")
        lon = airport_info.get("longitude")
        if lat is not None and lon is not None:
            return (lat, lon)
        return None

    def get_airport_city(self, icao: str) -> str:
        """Get airport city name if available."""
        airport_info = self.unified_airport_data.get(icao, {})
        return airport_info.get("city", "") or ""

    def get_airport_state(self, icao: str) -> str:
        """Get airport state/region if available."""
        airport_info = self.unified_airport_data.get(icao, {})
        return airport_info.get("state", "") or ""

    def calculate_grouping_extent(self) -> float:
        """
        Calculate the geographic extent (max distance) of all airports.

        Returns:
            Approximate diagonal distance in nautical miles.
        """
        coords: list[Coords] = []
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

    def calculate_optimal_k(self, num_towered: int) -> int:
        """
        Calculate optimal number of clusters (k) for k-means.

        Scales based on number of towered airports and geographic extent.
        """
        extent = self.calculate_grouping_extent()

        if num_towered <= 1:
            return 1
        if num_towered <= 3:
            return min(num_towered, 2)
        if num_towered <= 6:
            return min(num_towered, 4)
        return _k_for_extent(num_towered, extent)

    def kmeans_clustering(
        self, airports: list[AirportEntry], k: int, max_iterations: int = 50
    ) -> list[list[AirportEntry]]:
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
        valid_airports: list[ValidAirportEntry] = [
            cast(ValidAirportEntry, a) for a in airports if a[3] is not None
        ]

        if not valid_airports:
            return []

        if len(valid_airports) <= k:
            return [[cast(AirportEntry, va)] for va in valid_airports]

        centroids = _seed_centroids(valid_airports, k)
        clusters: list[list[ValidAirportEntry]] = [[] for _ in range(k)]

        for _ in range(max_iterations):
            new_clusters = _assign_to_centroids(valid_airports, centroids)
            if new_clusters == clusters:
                break
            clusters = new_clusters
            centroids = _updated_centroids(clusters, centroids)

        # Cast back to AirportEntry (ValidAirportEntry is a subtype)
        return [cast(list[AirportEntry], c) for c in clusters if c]

    def generate_area_name(self, centers: list[AirportEntry]) -> str:
        """
        Generate an area name from the center airports.

        Handles deduplication (e.g., "Sacramento / Sacramento" -> "Sacramento Area").
        """
        if not centers:
            return "Unknown Area"

        city_names = []
        seen_cities: set[str] = set()

        for icao, _data, _size_priority, _coords in centers:
            city = self._naming_city(icao)
            normalized = _normalized_city(city)
            if normalized not in seen_cities:
                seen_cities.add(normalized)
                city_names.append(city)

        return _join_area_name(city_names)

    def _naming_city(self, icao: str) -> str:
        """The airport's city, else its display name, else its ICAO."""
        city = self.get_airport_city(icao)
        if not city and self.disambiguator:
            city = self.disambiguator.get_pretty_name(icao)
        return city or icao

    def _naming_center_key(self, entry: AirportEntry) -> tuple[int, int, int, str]:
        """Sort key for the airports that name an area.

        ATIS first, then by size rank, then airports with an IATA code, then ICAO.
        """
        icao, data, size_priority, _coords = entry
        has_iata = bool(self.unified_airport_data.get(icao, {}).get("iata"))
        return (
            0 if data.get("atis") else 1,
            size_priority,
            0 if has_iata else 1,
            icao,
        )

    def _center_display_name(self, icao: str) -> str:
        """The centre airport's display name, or its ICAO without a disambiguator."""
        if self.disambiguator:
            return self.disambiguator.get_pretty_name(icao)
        return icao

    def _rename_clashing_areas(self, area_groups: list[dict[str, Any]]) -> None:
        """Rename areas that share a name after their centre airport.

        Each area whose name another area also has becomes "<centre display name>
        Area"; one that still clashes after that gets the centre ICAO added.
        Areas with a unique name keep it.
        """
        name_counts = Counter(group["name"] for group in area_groups)
        clashing = [group for group in area_groups if name_counts[group["name"]] > 1]
        for group in clashing:
            group["name"] = f"{self._center_display_name(group['center_icao'])} Area"

        renamed_counts = Counter(group["name"] for group in area_groups)
        for group in clashing:
            if renamed_counts[group["name"]] > 1:
                icao = group["center_icao"]
                group["name"] = f"{self._center_display_name(icao)} ({icao}) Area"

    def create_fallback_area_groups(self) -> list[dict[str, Any]]:
        """
        Create area groups when no ATIS airports are present.

        Groups by city, state, or geographic proximity.
        """
        all_airports = [
            (icao, data, self.get_airport_size_priority(icao))
            for icao, data in self.weather_data.items()
            if data.get("category") != "UNK"
        ]

        if not all_airports:
            return []

        city_groups, state_groups, no_location = self._group_by_location(all_airports)
        result = _named_location_groups(city_groups, "Area")
        result += _named_location_groups(state_groups, "Region")

        if no_location:
            no_location.sort(key=_by_size_then_icao)
            result.append(
                {
                    "name": "Other Airports",
                    "airports": no_location,
                    "center_icao": None,
                }
            )

        return result

    def _group_by_location(
        self, airports: list[AirportMember]
    ) -> tuple[
        dict[str, list[AirportMember]],
        dict[str, list[AirportMember]],
        list[AirportMember],
    ]:
        """Split airports by city, else by state, else into the unlocated list."""
        city_groups: dict[str, list[AirportMember]] = {}
        state_groups: dict[str, list[AirportMember]] = {}
        no_location: list[AirportMember] = []

        for member in airports:
            icao = member[0]
            city = self.get_airport_city(icao)
            state = self.get_airport_state(icao)
            if city:
                city_groups.setdefault(city, []).append(member)
            elif state:
                state_groups.setdefault(state, []).append(member)
            else:
                no_location.append(member)

        return city_groups, state_groups, no_location

    def _split_by_tower(self) -> tuple[list[AirportEntry], list[AirportEntry]]:
        """Known-category airports split into (towered, untowered).

        Towered airports have size priority 0-2 or an active ATIS.
        """
        towered_airports: list[AirportEntry] = []
        non_towered_airports: list[AirportEntry] = []

        for icao, data in self.weather_data.items():
            if data.get("category") == "UNK":
                continue
            coords = self.get_airport_coords(icao)
            size_priority = self.get_airport_size_priority(icao)
            entry: AirportEntry = (icao, data, size_priority, coords)

            if size_priority <= 2 or data.get("atis"):
                towered_airports.append(entry)
            else:
                non_towered_airports.append(entry)

        return towered_airports, non_towered_airports

    def _build_area_group(self, cluster: list[AirportEntry]) -> dict[str, Any]:
        """The named area group for one cluster, its members ATIS first."""
        centers = sorted(cluster, key=self._naming_center_key)
        area_name = self.generate_area_name(centers[:3])

        # Convert to member format (drop coords)
        members: list[AirportMember] = [
            (icao, data, size_priority) for icao, data, size_priority, coords in cluster
        ]
        members.sort(key=lambda x: (0 if x[1].get("atis") else 1, x[2], x[0]))

        return {
            "name": area_name,
            "airports": members,
            "center_icao": centers[0][0],
        }

    def _area_sort_key(self, group: dict[str, Any]) -> tuple[int, str]:
        """Order areas by their centre airport's size, then by name."""
        return (self.get_airport_size_priority(group["center_icao"]), group["name"])

    def create_area_groups(self) -> list[dict[str, Any]]:
        """
        Create area-based groupings using k-means clustering.

        Returns a list of area groups, each containing:
        - name: Area name (e.g., "Sacramento Area")
        - airports: List of (icao, data, size_priority) tuples
        - center_icao: The main towered airport for this area
        """
        towered_airports, non_towered_airports = self._split_by_tower()

        if not towered_airports:
            return self.create_fallback_area_groups()

        k = self.calculate_optimal_k(len(towered_airports))

        clusters = self.kmeans_clustering(towered_airports, k)

        if not clusters:
            return self.create_fallback_area_groups()

        _attach_untowered(clusters, non_towered_airports)

        area_groups = [self._build_area_group(cluster) for cluster in clusters]
        self._rename_clashing_areas(area_groups)
        area_groups.sort(key=self._area_sort_key)
        return area_groups


def count_area_categories(airports: list[AirportMember]) -> dict[str, int]:
    """
    Count flight categories for a list of airports.

    Args:
        airports: List of (icao, data, size_priority) tuples

    Returns:
        Dict with counts for LIFR, IFR, MVFR, VFR
    """
    counts = {"LIFR": 0, "IFR": 0, "MVFR": 0, "VFR": 0}
    for _, data, _ in airports:
        cat = data.get("category", "UNK")
        if cat in counts:
            counts[cat] += 1
    return counts


def build_area_summary(counts: dict[str, int], color_scheme: str = "ui") -> str:
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
