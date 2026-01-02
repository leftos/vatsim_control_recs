"""Route Weather Modal Screen - Weather along a flight's filed route"""

import asyncio
from typing import List, Dict, Any, Optional, Set
from textual.screen import ModalScreen
from textual.widgets import Static
from textual.containers import Container, VerticalScroll
from textual.binding import Binding
from textual.app import ComposeResult

from backend import get_metar_batch, haversine_distance_nm, find_airports_near_position
from backend.data.navaids import parse_route_string, Waypoint
from ui import config
from ui.config import CATEGORY_COLORS
from ui.modals.metar_info import (
    get_flight_category,
    _extract_visibility_str,
    _parse_ceiling_layer,
    parse_weather_phenomena,
)
from ui.modals.weather_briefing import (
    _parse_wind_from_metar,
    _parse_metar_observation_time,
)


# Search radius around each waypoint for nearby airports
WAYPOINT_SEARCH_RADIUS_NM = 30.0


class RouteWeatherScreen(ModalScreen):
    """Modal screen showing weather along a flight's filed route"""

    CSS = """
    RouteWeatherScreen {
        align: center middle;
    }

    #route-wx-container {
        width: 90%;
        max-width: 120;
        height: 85%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #route-wx-header {
        height: auto;
    }

    #route-wx-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #route-wx-summary {
        text-align: center;
        margin-bottom: 1;
    }

    #route-wx-scroll {
        height: 1fr;
    }

    #route-wx-content {
        height: auto;
    }

    #route-wx-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("q", "close", "Close"),
    ]

    def __init__(self, flight_data: dict):
        """
        Initialize the route weather modal.

        Args:
            flight_data: Dictionary containing flight information from VATSIM data
        """
        super().__init__()
        self.flight_data = flight_data
        self.callsign = flight_data.get('callsign', 'Unknown')

        # Extract route info
        flight_plan = flight_data.get('flight_plan') or {}
        self.departure = flight_plan.get('departure', '')
        self.arrival = flight_plan.get('arrival', '')
        self.route_string = flight_plan.get('route', '')

        # Will be populated asynchronously
        self.waypoints: List[Waypoint] = []
        self.route_airports: List[str] = []  # Airports along the route
        self.weather_data: Dict[str, Dict[str, Any]] = {}
        self._pending_tasks: list = []

    def compose(self) -> ComposeResult:
        with Container(id="route-wx-container"):
            with Container(id="route-wx-header"):
                title = f"Route Weather: {self.callsign}"
                if self.departure and self.arrival:
                    title += f" ({self.departure} → {self.arrival})"
                yield Static(title, id="route-wx-title")
                yield Static("Loading route data...", id="route-wx-summary")
            with VerticalScroll(id="route-wx-scroll"):
                yield Static("", id="route-wx-content", markup=True)
            yield Static("Escape/Q: Close", id="route-wx-hint")

    async def on_mount(self) -> None:
        """Load route and weather data asynchronously"""
        task = asyncio.create_task(self._load_route_weather())
        self._pending_tasks.append(task)

    def on_unmount(self) -> None:
        """Cancel pending tasks when modal is dismissed."""
        for task in self._pending_tasks:
            if not task.done():
                task.cancel()
        self._pending_tasks.clear()

    async def _load_route_weather(self) -> None:
        """Parse route and fetch weather for airports along the route"""
        loop = asyncio.get_event_loop()

        # Build airport coordinates dict for route parsing
        airport_coords = {}
        if config.UNIFIED_AIRPORT_DATA:
            for icao, data in config.UNIFIED_AIRPORT_DATA.items():
                lat = data.get('latitude')
                lon = data.get('longitude')
                if lat is not None and lon is not None:
                    airport_coords[icao] = (lat, lon)

        # Parse route string in executor (may involve file I/O for navaids)
        self.waypoints = await loop.run_in_executor(
            None,
            parse_route_string,
            self.route_string,
            airport_coords
        )

        # Collect airports to fetch weather for
        # Start with departure and arrival
        airports_to_fetch: Set[str] = set()

        if self.departure:
            airports_to_fetch.add(self.departure)
        if self.arrival:
            airports_to_fetch.add(self.arrival)

        # Find airports near each waypoint
        waypoint_airports: Dict[str, List[str]] = {}  # waypoint_id -> nearby airports

        for waypoint in self.waypoints:
            if config.UNIFIED_AIRPORT_DATA:
                nearby = find_airports_near_position(
                    waypoint.latitude,
                    waypoint.longitude,
                    config.UNIFIED_AIRPORT_DATA,
                    radius_nm=WAYPOINT_SEARCH_RADIUS_NM,
                    max_results=5
                )
                # Filter to 4-letter ICAO codes only
                nearby_icao = [
                    icao for icao in nearby
                    if len(icao) == 4 and icao.isalpha() and icao not in (self.departure, self.arrival)
                ]
                if nearby_icao:
                    waypoint_airports[waypoint.identifier] = nearby_icao
                    airports_to_fetch.update(nearby_icao)

        # Build ordered list of airports along route
        self.route_airports = []

        # Add departure first
        if self.departure:
            self.route_airports.append(self.departure)

        # Add waypoint-associated airports in order
        for waypoint in self.waypoints:
            if waypoint.identifier in waypoint_airports:
                for icao in waypoint_airports[waypoint.identifier]:
                    if icao not in self.route_airports:
                        self.route_airports.append(icao)

        # Add arrival last
        if self.arrival and self.arrival not in self.route_airports:
            self.route_airports.append(self.arrival)

        # Fetch METARs for all airports
        all_airports = list(airports_to_fetch)
        metars = await loop.run_in_executor(
            None,
            get_metar_batch,
            all_airports
        )

        # Build weather data structure
        for icao in all_airports:
            metar = metars.get(icao, '')
            if metar:
                category, color = get_flight_category(metar)
                self.weather_data[icao] = {
                    'metar': metar,
                    'category': category,
                    'color': color,
                    'visibility': _extract_visibility_str(metar),
                    'ceiling': _parse_ceiling_layer(metar),
                    'wind': _parse_wind_from_metar(metar),
                    'obs_time': _parse_metar_observation_time(metar),
                    'phenomena': parse_weather_phenomena(metar),
                }
            else:
                self.weather_data[icao] = {
                    'category': 'UNK',
                    'color': 'white',
                }

        # Update display
        self._update_display()

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

        total_waypoints = len(self.waypoints)
        summary_text = f"{total_waypoints} waypoints parsed"
        if summary_parts:
            summary_text += " | " + " | ".join(summary_parts)

        summary_widget = self.query_one("#route-wx-summary", Static)
        summary_widget.update(summary_text)

        # Build content
        sections = []

        # Departure section
        if self.departure and self.departure in self.weather_data:
            sections.append(self._build_section("DEPARTURE", [self.departure]))

        # Enroute section - airports along the route (excluding departure/arrival)
        enroute = [
            icao for icao in self.route_airports
            if icao not in (self.departure, self.arrival) and icao in self.weather_data
        ]
        if enroute:
            sections.append(self._build_section("ENROUTE", enroute))

        # Arrival section
        if self.arrival and self.arrival in self.weather_data:
            sections.append(self._build_section("ARRIVAL", [self.arrival]))

        content_widget = self.query_one("#route-wx-content", Static)
        if sections:
            content_widget.update("\n\n".join(sections))
        elif not self.route_string:
            content_widget.update("[dim]No route filed[/dim]")
        else:
            content_widget.update("[dim]No airports found along route[/dim]")

    def _build_section(self, header: str, airports: List[str]) -> str:
        """Build a section with header and airport cards"""
        lines = [f"[bold cyan]═══ {header} ═══[/bold cyan]"]

        for icao in airports:
            data = self.weather_data.get(icao, {})
            card = self._build_airport_card(icao, data)
            lines.append(card)

        return "\n".join(lines)

    def _build_airport_card(self, icao: str, data: Dict[str, Any]) -> str:
        """Build a Rich markup card for one airport"""
        lines = []

        # Header with airport name and colored category suffix
        category = data.get('category', 'UNK')
        color = CATEGORY_COLORS.get(category, 'white')
        pretty_name = config.DISAMBIGUATOR.get_full_name(icao) if config.DISAMBIGUATOR else icao

        # Calculate distance from previous waypoint if applicable
        distance_info = self._get_distance_info(icao)
        distance_str = f" {distance_info}" if distance_info else ""

        header = f"[bold]{icao}[/bold] ({pretty_name}) // [{color} bold]{category}[/{color} bold]{distance_str}"
        lines.append(header)

        # Skip further details if no METAR
        if category == 'UNK':
            return "\n".join(lines)

        # Observation time line (Zulu + Local)
        obs_time = data.get('obs_time')
        if obs_time:
            zulu_str, local_str = obs_time
            if local_str:
                lines.append(f"  [dim]Obs: {zulu_str} ({local_str})[/dim]")
            else:
                lines.append(f"  [dim]Obs: {zulu_str}[/dim]")

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

        return "\n".join(lines)

    def _get_distance_info(self, icao: str) -> str:
        """Get distance info for an airport relative to route waypoints."""
        if not config.UNIFIED_AIRPORT_DATA:
            return ""

        airport_data = config.UNIFIED_AIRPORT_DATA.get(icao, {})
        apt_lat = airport_data.get('latitude')
        apt_lon = airport_data.get('longitude')
        if apt_lat is None or apt_lon is None:
            return ""

        # For enroute airports, find the nearest waypoint and show distance
        if icao not in (self.departure, self.arrival):
            min_distance = float('inf')
            nearest_waypoint = None

            for waypoint in self.waypoints:
                distance = haversine_distance_nm(
                    waypoint.latitude, waypoint.longitude,
                    apt_lat, apt_lon
                )
                if distance < min_distance:
                    min_distance = distance
                    nearest_waypoint = waypoint.identifier

            if nearest_waypoint and min_distance < WAYPOINT_SEARCH_RADIUS_NM:
                return f"[dim]({min_distance:.0f}nm from {nearest_waypoint})[/dim]"

        return ""

    def action_close(self) -> None:
        """Close the modal"""
        self.dismiss()
