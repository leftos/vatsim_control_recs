"""Flight Weather Briefing Modal - Pilot-style weather briefing for a flight route."""

import asyncio
import os
import tempfile
import webbrowser
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from rich.console import Console
from textual.screen import ModalScreen
from textual.widgets import Static
from textual.containers import Container, VerticalScroll
from textual.binding import Binding
from textual.app import ComposeResult

from backend import get_metar_batch, get_taf_batch
from backend.data.vatsim_api import download_vatsim_data, get_atis_for_airports
from backend.core.calculations import haversine_distance_nm
from backend.core.route import (
    sample_route_points,
    find_enroute_airports,
    parse_route_waypoints,
    format_ete,
)
from backend.briefing import (
    parse_taf_changes,
    parse_wind_from_metar,
)
from backend.data.weather_parsing import (
    get_flight_category,
    parse_visibility_sm,
    parse_ceiling_feet,
    parse_ceiling_layer,
)
from ui import config
from ui.config import CATEGORY_COLORS


class FlightWeatherBriefingScreen(ModalScreen):
    """Modal screen showing pilot-style weather briefing for a flight route."""

    CSS = """
    FlightWeatherBriefingScreen {
        align: center middle;
    }

    #flight-briefing-container {
        width: 95%;
        max-width: 130;
        height: 90%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #flight-briefing-header {
        height: auto;
        dock: top;
        padding-bottom: 1;
        border-bottom: solid $primary;
    }

    #flight-briefing-title {
        text-style: bold;
        text-align: center;
    }

    #flight-briefing-route {
        text-align: center;
        color: $text-muted;
    }

    #flight-briefing-scroll {
        height: 1fr;
    }

    #flight-briefing-content {
        padding: 1 0;
    }

    #flight-briefing-hint {
        dock: bottom;
        height: 1;
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

    def __init__(
        self,
        callsign: str,
        departure: str,
        arrival: str,
        alternate: Optional[str] = None,
        route: Optional[str] = None,
        cruise_altitude: Optional[int] = None,
        groundspeed: Optional[int] = None,
    ):
        """
        Initialize the flight weather briefing.

        Args:
            callsign: Flight callsign
            departure: Departure airport ICAO
            arrival: Arrival airport ICAO
            alternate: Alternate airport ICAO (optional)
            route: Route string from flight plan (optional)
            cruise_altitude: Cruise altitude in feet (optional)
            groundspeed: Current groundspeed in knots (optional, for ETE calc)
        """
        super().__init__()
        self.callsign = callsign
        self.departure = departure
        self.arrival = arrival
        self.alternate = alternate
        self.route = route
        self.cruise_altitude = cruise_altitude
        self.groundspeed = groundspeed or 450  # Default cruise speed

        self.weather_data: Dict[str, Dict[str, Any]] = {}
        self.enroute_points: List[Dict[str, Any]] = []
        self.route_waypoints: List[str] = []
        self.total_distance: float = 0.0
        self._pending_tasks: list = []

    def compose(self) -> ComposeResult:
        route_display = f"{self.departure} → {self.arrival}"
        if self.alternate:
            route_display += f" (Alt: {self.alternate})"

        with Container(id="flight-briefing-container"):
            with Container(id="flight-briefing-header"):
                yield Static(f"Flight Weather Briefing: {self.callsign}", id="flight-briefing-title")
                yield Static(route_display, id="flight-briefing-route")
            with VerticalScroll(id="flight-briefing-scroll"):
                yield Static("Loading weather data...", id="flight-briefing-content", markup=True)
            yield Static("Escape/Q: Close | P: Print", id="flight-briefing-hint")

    async def on_mount(self) -> None:
        """Load weather data asynchronously."""
        task = asyncio.create_task(self._fetch_weather_async())
        self._pending_tasks.append(task)

    def on_unmount(self) -> None:
        """Cancel pending tasks when modal is dismissed."""
        for task in self._pending_tasks:
            if not task.done():
                task.cancel()
        self._pending_tasks.clear()

    async def _fetch_weather_async(self) -> None:
        """Fetch all weather data for the route."""
        loop = asyncio.get_event_loop()

        # Parse route waypoints for display
        self.route_waypoints = parse_route_waypoints(self.route) if self.route else []

        # Get coordinates for departure and arrival
        dep_info = config.UNIFIED_AIRPORT_DATA.get(self.departure, {}) if config.UNIFIED_AIRPORT_DATA else {}
        arr_info = config.UNIFIED_AIRPORT_DATA.get(self.arrival, {}) if config.UNIFIED_AIRPORT_DATA else {}

        dep_lat = dep_info.get('latitude')
        dep_lon = dep_info.get('longitude')
        arr_lat = arr_info.get('latitude')
        arr_lon = arr_info.get('longitude')

        # Calculate total distance
        if dep_lat and dep_lon and arr_lat and arr_lon:
            self.total_distance = haversine_distance_nm(dep_lat, dep_lon, arr_lat, arr_lon)

            # Sample enroute points
            sample_points = sample_route_points(
                dep_lat, dep_lon, arr_lat, arr_lon,
                interval_nm=200.0,  # Sample every ~200nm
                min_points=1,
                max_points=6
            )

            # Find airports near sample points
            if config.UNIFIED_AIRPORT_DATA:
                self.enroute_points = find_enroute_airports(
                    sample_points,
                    config.UNIFIED_AIRPORT_DATA,
                    search_radius_nm=100.0
                )

        # Collect all airports needing weather
        airports_to_fetch = [self.departure, self.arrival]
        if self.alternate:
            airports_to_fetch.append(self.alternate)
        airports_to_fetch.extend([p['icao'] for p in self.enroute_points])

        # Remove duplicates while preserving order
        seen = set()
        unique_airports = []
        for apt in airports_to_fetch:
            if apt not in seen:
                seen.add(apt)
                unique_airports.append(apt)

        # Fetch weather data in parallel
        try:
            metar_task = loop.run_in_executor(None, get_metar_batch, unique_airports)
            taf_task = loop.run_in_executor(None, get_taf_batch, unique_airports)
            vatsim_task = loop.run_in_executor(None, download_vatsim_data)

            metars, tafs, vatsim_data = await asyncio.gather(metar_task, taf_task, vatsim_task)

            # Get ATIS data from VATSIM data
            atis_data = get_atis_for_airports(vatsim_data, unique_airports) if vatsim_data else {}

        except Exception as e:
            self._update_content(f"[red]Error fetching weather: {e}[/red]")
            return

        # Process weather data
        for icao in unique_airports:
            metar = metars.get(icao, '')
            taf = tafs.get(icao, '')
            category = get_flight_category(metar) if metar else 'UNK'

            self.weather_data[icao] = {
                'metar': metar,
                'taf': taf,
                'category': category,
                'color': CATEGORY_COLORS.get(category, 'white'),
                'visibility_sm': parse_visibility_sm(metar) if metar else None,
                'ceiling_ft': parse_ceiling_feet(metar) if metar else None,
                'ceiling_layer': parse_ceiling_layer(metar) if metar else None,
                'wind': parse_wind_from_metar(metar) if metar else None,
                'atis': atis_data.get(icao),
            }

        # Update display
        self._update_display()

    def _update_content(self, content: str) -> None:
        """Update the content widget."""
        try:
            widget = self.query_one("#flight-briefing-content", Static)
            widget.update(content)
        except Exception:
            pass

    def _generate_synopsis(self) -> str:
        """Generate a synopsis of weather conditions along the route."""
        parts = []

        # Check departure conditions
        dep_data = self.weather_data.get(self.departure, {})
        dep_cat = dep_data.get('category', 'UNK')

        # Check arrival conditions
        arr_data = self.weather_data.get(self.arrival, {})
        arr_cat = arr_data.get('category', 'UNK')

        # Check for any enroute issues
        enroute_cats = [self.weather_data.get(p['icao'], {}).get('category', 'UNK') for p in self.enroute_points]
        worst_enroute = 'VFR'
        for cat in enroute_cats:
            if cat in ['LIFR', 'IFR'] and worst_enroute in ['VFR', 'MVFR']:
                worst_enroute = cat
            elif cat == 'MVFR' and worst_enroute == 'VFR':
                worst_enroute = cat

        # Build synopsis
        if dep_cat in ['LIFR', 'IFR']:
            parts.append(f"{dep_cat} conditions at departure")
        elif dep_cat == 'MVFR':
            parts.append("MVFR conditions at departure")
        else:
            parts.append("VFR departure")

        if worst_enroute in ['LIFR', 'IFR']:
            parts.append(f"{worst_enroute} enroute")
        elif worst_enroute == 'MVFR':
            parts.append("MVFR enroute")

        if arr_cat in ['LIFR', 'IFR']:
            parts.append(f"{arr_cat} conditions at arrival")
        elif arr_cat == 'MVFR':
            parts.append("MVFR arrival")
        else:
            parts.append("VFR arrival")

        # Check TAF for trends
        arr_taf = arr_data.get('taf', '')
        if arr_taf:
            changes = parse_taf_changes(arr_taf, arr_cat)
            for change in changes[:2]:  # First couple changes
                if change.get('is_improvement'):
                    new_cat = change.get('category', 'VFR')
                    parts.append(f"improving to {new_cat}")
                    break
                elif change.get('is_deterioration'):
                    new_cat = change.get('category', 'IFR')
                    parts.append(f"deteriorating to {new_cat}")
                    break

        return ". ".join(parts) + "." if parts else "Weather data unavailable."

    def _format_airport_line(
        self,
        icao: str,
        data: Dict[str, Any],
        distance_nm: Optional[float] = None,
        label: str = "",
        show_ete: bool = True
    ) -> str:
        """Format a single airport line for the briefing."""
        category = data.get('category', 'UNK')
        color = data.get('color', 'white')

        # Distance and ETE
        dist_str = f"{int(distance_nm):>4}nm" if distance_nm is not None else "   0nm"
        ete_str = format_ete(distance_nm or 0, self.groundspeed) if show_ete and distance_nm else ""

        # Basic line: ICAO (distance, ETE) ............ CATEGORY
        dots = "." * max(3, 45 - len(icao) - len(label) - len(dist_str) - len(ete_str))
        line = f"{label}{icao} ({dist_str} {ete_str:>6}) {dots} [{color}]{category}[/{color}]"

        return line

    def _format_weather_block(self, icao: str, data: Dict[str, Any], indent: str = "    ") -> List[str]:
        """Format the weather details for an airport."""
        lines = []

        # Wind and conditions
        wind = data.get('wind', '')
        ceiling = data.get('ceiling_layer', '')
        visibility = data.get('visibility_sm')

        conditions = []
        if wind:
            conditions.append(wind)
        if visibility:
            conditions.append(f"{visibility}SM")
        if ceiling:
            conditions.append(ceiling)

        if conditions:
            lines.append(f"{indent}[dim]{' '.join(conditions)}[/dim]")

        # METAR (truncated)
        metar = data.get('metar', '')
        if metar:
            # Remove METAR prefix and airport code for brevity
            metar_display = metar
            if metar_display.startswith('METAR '):
                metar_display = metar_display[6:]
            if metar_display.startswith(icao):
                metar_display = metar_display[len(icao):].strip()
            lines.append(f"{indent}[dim]{metar_display[:80]}[/dim]")

        # TAF trend if significant
        taf = data.get('taf', '')
        if taf:
            category = data.get('category', 'UNK')
            changes = parse_taf_changes(taf, category)
            for change in changes[:1]:  # Just first significant change
                if change.get('is_improvement') or change.get('is_deterioration'):
                    trend = "↑" if change.get('is_improvement') else "↓"
                    new_cat = change.get('category', '')
                    change_type = change.get('type', 'FM')
                    lines.append(f"{indent}[dim]TAF: {trend} {new_cat} ({change_type})[/dim]")
                    break

        return lines

    def _update_display(self) -> None:
        """Update the display with the weather briefing."""
        sections = []

        # Header with timestamp
        now = datetime.now(timezone.utc)
        zulu_str = now.strftime("%d%H%MZ")
        total_ete = format_ete(self.total_distance, self.groundspeed)
        header = f"[bold]Briefing valid: {zulu_str}[/bold]  |  Route: {int(self.total_distance)}nm  |  ETE: {total_ete}"
        sections.append(header)

        # Synopsis
        synopsis = self._generate_synopsis()
        sections.append(f"\n[bold cyan]SYNOPSIS[/bold cyan]\n{synopsis}")

        # Departure
        sections.append("\n[bold cyan]━━━ DEPARTURE ━━━[/bold cyan]")
        dep_data = self.weather_data.get(self.departure, {})
        if dep_data:
            sections.append(self._format_airport_line(self.departure, dep_data, 0, "DEP ", show_ete=False))
            sections.extend(self._format_weather_block(self.departure, dep_data))

        # Enroute section with waypoints interspersed
        if self.enroute_points or self.route_waypoints:
            sections.append("\n[bold cyan]━━━ ENROUTE ━━━[/bold cyan]")

            # Intersperse waypoints with enroute weather points
            # Distribute waypoints evenly along the route
            num_waypoints = len(self.route_waypoints)
            num_weather = len(self.enroute_points)

            if num_weather > 0:
                # Show weather points with some waypoints between them
                waypoints_per_segment = num_waypoints // (num_weather + 1) if num_waypoints > 0 else 0
                wp_idx = 0

                for i, point in enumerate(self.enroute_points):
                    # Show some waypoints before this weather point
                    for _ in range(waypoints_per_segment):
                        if wp_idx < num_waypoints:
                            wp = self.route_waypoints[wp_idx]
                            sections.append(f"    [dim]  · {wp}[/dim]")
                            wp_idx += 1

                    # Show the weather point
                    icao = point['icao']
                    data = self.weather_data.get(icao, {})
                    if data:
                        sections.append(self._format_airport_line(
                            icao, data, point['distance_nm'], "ENR "
                        ))
                        # Just show conditions, not full METAR for enroute
                        wind = data.get('wind', '')
                        ceiling = data.get('ceiling_layer', '')
                        if wind or ceiling:
                            cond = f"{wind} {ceiling}".strip()
                            sections.append(f"    [dim]{cond}[/dim]")

                # Show remaining waypoints
                while wp_idx < num_waypoints:
                    wp = self.route_waypoints[wp_idx]
                    sections.append(f"    [dim]  · {wp}[/dim]")
                    wp_idx += 1
            else:
                # No weather points, just show waypoints
                for wp in self.route_waypoints:
                    sections.append(f"    [dim]  · {wp}[/dim]")

        # Arrival
        sections.append("\n[bold cyan]━━━ ARRIVAL ━━━[/bold cyan]")
        arr_data = self.weather_data.get(self.arrival, {})
        if arr_data:
            sections.append(self._format_airport_line(
                self.arrival, arr_data, self.total_distance, "ARR "
            ))
            sections.extend(self._format_weather_block(self.arrival, arr_data))

        # Alternate
        if self.alternate:
            sections.append("\n[bold cyan]━━━ ALTERNATE ━━━[/bold cyan]")
            alt_data = self.weather_data.get(self.alternate, {})
            if alt_data:
                sections.append(self._format_airport_line(self.alternate, alt_data, label="ALT ", show_ete=False))
                sections.extend(self._format_weather_block(self.alternate, alt_data))

        self._update_content("\n".join(sections))

    def action_close(self) -> None:
        """Close the modal."""
        self.dismiss()

    def action_print(self) -> None:
        """Export briefing as HTML and open in browser."""
        if not self.weather_data:
            self.notify("No weather data to export", severity="warning")
            return

        console = Console(record=True, force_terminal=True, width=500)

        # Rebuild content for printing
        now = datetime.now(timezone.utc)
        zulu_str = now.strftime("%Y-%m-%d %H%MZ")
        total_ete = format_ete(self.total_distance, self.groundspeed)

        console.print(f"[bold]Flight Weather Briefing: {self.callsign}[/bold]")
        console.print(f"{self.departure} → {self.arrival}")
        if self.alternate:
            console.print(f"Alternate: {self.alternate}")
        console.print(f"Generated: {zulu_str}  |  Route: {int(self.total_distance)}nm  |  ETE: {total_ete}")
        console.print()

        # Synopsis
        synopsis = self._generate_synopsis()
        console.print("[bold cyan]SYNOPSIS[/bold cyan]")
        console.print(synopsis)
        console.print()

        # Departure
        console.print("[bold cyan]━━━ DEPARTURE ━━━[/bold cyan]")
        dep_data = self.weather_data.get(self.departure, {})
        if dep_data:
            console.print(self._format_airport_line(self.departure, dep_data, 0, "DEP ", show_ete=False))
            for line in self._format_weather_block(self.departure, dep_data):
                console.print(line)
        console.print()

        # Enroute
        if self.enroute_points or self.route_waypoints:
            console.print("[bold cyan]━━━ ENROUTE ━━━[/bold cyan]")
            num_waypoints = len(self.route_waypoints)
            num_weather = len(self.enroute_points)

            if num_weather > 0:
                waypoints_per_segment = num_waypoints // (num_weather + 1) if num_waypoints > 0 else 0
                wp_idx = 0

                for point in self.enroute_points:
                    for _ in range(waypoints_per_segment):
                        if wp_idx < num_waypoints:
                            console.print(f"    [dim]  · {self.route_waypoints[wp_idx]}[/dim]")
                            wp_idx += 1

                    icao = point['icao']
                    data = self.weather_data.get(icao, {})
                    if data:
                        console.print(self._format_airport_line(icao, data, point['distance_nm'], "ENR "))
                        wind = data.get('wind', '')
                        ceiling = data.get('ceiling_layer', '')
                        if wind or ceiling:
                            console.print(f"    [dim]{wind} {ceiling}[/dim]")

                while wp_idx < num_waypoints:
                    console.print(f"    [dim]  · {self.route_waypoints[wp_idx]}[/dim]")
                    wp_idx += 1
            else:
                for wp in self.route_waypoints:
                    console.print(f"    [dim]  · {wp}[/dim]")
            console.print()

        # Arrival
        console.print("[bold cyan]━━━ ARRIVAL ━━━[/bold cyan]")
        arr_data = self.weather_data.get(self.arrival, {})
        if arr_data:
            console.print(self._format_airport_line(self.arrival, arr_data, self.total_distance, "ARR "))
            for line in self._format_weather_block(self.arrival, arr_data):
                console.print(line)
        console.print()

        # Alternate
        if self.alternate:
            console.print("[bold cyan]━━━ ALTERNATE ━━━[/bold cyan]")
            alt_data = self.weather_data.get(self.alternate, {})
            if alt_data:
                console.print(self._format_airport_line(self.alternate, alt_data, label="ALT ", show_ete=False))
                for line in self._format_weather_block(self.alternate, alt_data):
                    console.print(line)

        # Export
        html_content = console.export_html(inline_styles=True)
        html_content = html_content.replace(
            '</style>',
            '''pre { margin: 0; padding: 0; white-space: pre-wrap; word-wrap: break-word; }
body { margin: 20px; font-family: monospace; }
</style>'''
        )

        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.html',
            prefix=f'flight_briefing_{self.callsign}_',
            delete=False,
            encoding='utf-8'
        ) as f:
            f.write(html_content)
            temp_path = f.name

        webbrowser.open(f'file://{temp_path}')
        self.notify(f"Opened in browser: {os.path.basename(temp_path)}")
