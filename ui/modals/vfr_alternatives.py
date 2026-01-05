"""VFR Alternatives Modal Screen - Find nearby airports with VFR/MVFR conditions"""

import asyncio
from textual.screen import ModalScreen
from textual.widgets import Static, Input
from textual.containers import Container
from textual.binding import Binding
from textual.app import ComposeResult

from backend import (
    get_metar,
    find_airports_near_position,
    haversine_distance_nm,
    calculate_bearing,
    bearing_to_compass,
)
from ui import config
from .metar_info import get_flight_category, _extract_flight_rules_weather


# Constants for alternate airport search
MAX_ALTERNATE_SEARCH_RADIUS_NM = 100.0
MAX_RESULTS = 10


class VfrAlternativesScreen(ModalScreen):
    """Modal screen for finding VFR alternative airports near a given airport"""

    CSS = """
    VfrAlternativesScreen {
        align: center middle;
    }

    #vfr-container {
        width: 85;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #vfr-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #vfr-input-container {
        height: auto;
        margin-bottom: 1;
    }

    #vfr-result {
        text-align: left;
        height: auto;
        margin-top: 1;
        padding: 1;
    }

    #vfr-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("enter", "find_alternatives", "Find Alternatives", priority=True),
    ]

    def __init__(self, initial_icao: str | None = None):
        """
        Initialize the VFR alternatives modal.

        Args:
            initial_icao: Optional ICAO code to pre-fill and auto-search
        """
        super().__init__()
        self.initial_icao = initial_icao
        self._search_task: asyncio.Task | None = None
        self._search_cancelled = False

    def compose(self) -> ComposeResult:
        with Container(id="vfr-container"):
            yield Static("VFR Alternatives Finder", id="vfr-title")
            with Container(id="vfr-input-container"):
                yield Input(
                    placeholder="Enter airport ICAO code (e.g., KSFO)", id="vfr-input"
                )
            yield Static("", id="vfr-result", markup=True)
            yield Static("Press Enter to search, Escape to close", id="vfr-hint")

    def on_mount(self) -> None:
        """Focus the input when mounted, and auto-search if initial_icao provided"""
        vfr_input = self.query_one("#vfr-input", Input)

        if self.initial_icao:
            # Pre-fill and auto-search
            vfr_input.value = self.initial_icao
            self.action_find_alternatives()
        else:
            vfr_input.focus()

    def on_unmount(self) -> None:
        """Cancel any pending search when modal is closed"""
        self._search_cancelled = True
        if self._search_task and not self._search_task.done():
            self._search_task.cancel()

    def _format_weather_details(
        self, visibility_str: str | None, ceiling_str: str | None
    ) -> str:
        """
        Format visibility and ceiling into a METAR-style details string.

        Args:
            visibility_str: Visibility string from METAR (e.g., "2SM", "1/2SM"), or None
            ceiling_str: Ceiling string from METAR (e.g., "BKN004"), or None

        Returns:
            Formatted string like "(10SM CLR)" or "(10SM BKN050)" or "" if no data
        """
        parts = []
        if visibility_str:
            parts.append(visibility_str)
        if ceiling_str:
            parts.append(ceiling_str)
        else:
            parts.append("CLR")  # No ceiling means clear
        if parts:
            return f"({' '.join(parts)})"
        return ""

    def action_find_alternatives(self) -> None:
        """Find VFR alternatives for the entered airport"""
        # Cancel any existing search
        self._search_cancelled = True
        if self._search_task and not self._search_task.done():
            self._search_task.cancel()

        self._search_cancelled = False
        self._search_task = asyncio.create_task(self._find_alternatives_async())

    async def _find_alternatives_async(self) -> None:
        """Async search that updates UI live as results are found"""
        vfr_input = self.query_one("#vfr-input", Input)
        icao = vfr_input.value.strip().upper()
        result_widget = self.query_one("#vfr-result", Static)

        if not icao:
            result_widget.update("Please enter an airport ICAO code")
            return

        if not config.UNIFIED_AIRPORT_DATA:
            result_widget.update("[red]Airport data not loaded[/red]")
            return

        # Check if airport exists
        origin_data = config.UNIFIED_AIRPORT_DATA.get(icao)
        if not origin_data:
            result_widget.update(f"[red]Airport {icao} not found in database[/red]")
            return

        origin_lat = origin_data.get("latitude")
        origin_lon = origin_data.get("longitude")
        if origin_lat is None or origin_lon is None:
            result_widget.update(f"[red]No coordinates for {icao}[/red]")
            return

        # Get full name if available (no length limit)
        pretty_name = (
            config.DISAMBIGUATOR.get_full_name(icao) if config.DISAMBIGUATOR else icao
        )

        # Build header lines
        header_lines = [f"[bold]{pretty_name} ({icao})[/bold]", ""]

        # Get weather at origin airport
        loop = asyncio.get_event_loop()
        origin_metar = await loop.run_in_executor(None, get_metar, icao)

        if self._search_cancelled:
            return

        if origin_metar:
            category, color = get_flight_category(origin_metar)
            vis_str, ceil_str = _extract_flight_rules_weather(origin_metar)
            details = self._format_weather_details(vis_str, ceil_str)
            header_lines.append(
                f"Current conditions: [{color} bold]{category}[/{color} bold] {details}"
            )
        else:
            header_lines.append("Current conditions: [dim]No METAR available[/dim]")

        header_lines.append("")
        header_lines.append(
            "[bold]VFR/MVFR Alternatives:[/bold] [dim]Searching...[/dim]"
        )

        result_widget.update("\n".join(header_lines))

        # Find nearby airports
        nearby_all = find_airports_near_position(
            origin_lat,
            origin_lon,
            config.UNIFIED_AIRPORT_DATA,
            radius_nm=MAX_ALTERNATE_SEARCH_RADIUS_NM,
            max_results=500,
        )

        # Filter to airports likely to have METAR
        nearby = [
            apt_icao
            for apt_icao in nearby_all
            if len(apt_icao) == 4 and apt_icao.isalpha() and apt_icao != icao
        ]

        if self._search_cancelled:
            return

        # Search airports and update live
        alternates = []
        checked_count = 0

        for apt_icao in nearby:
            if self._search_cancelled:
                return

            if len(alternates) >= MAX_RESULTS:
                break

            checked_count += 1

            # Fetch METAR in executor to avoid blocking
            metar = await loop.run_in_executor(None, get_metar, apt_icao)

            if self._search_cancelled:
                return

            if not metar:
                continue

            category, color = get_flight_category(metar)
            if category not in ("VFR", "MVFR"):
                continue

            # Get weather details
            vis_str, ceil_str = _extract_flight_rules_weather(metar)

            # Calculate distance and direction
            apt_data = config.UNIFIED_AIRPORT_DATA.get(apt_icao, {})
            apt_lat = apt_data.get("latitude")
            apt_lon = apt_data.get("longitude")
            if apt_lat is None or apt_lon is None:
                continue

            distance = haversine_distance_nm(origin_lat, origin_lon, apt_lat, apt_lon)
            bearing = calculate_bearing(origin_lat, origin_lon, apt_lat, apt_lon)
            direction = bearing_to_compass(bearing)

            # Get full name (no length limit)
            alt_name = (
                config.DISAMBIGUATOR.get_full_name(apt_icao)
                if config.DISAMBIGUATOR
                else apt_icao
            )

            alternates.append(
                {
                    "icao": apt_icao,
                    "name": alt_name,
                    "category": category,
                    "color": color,
                    "distance": distance,
                    "direction": direction,
                    "vis_str": vis_str,
                    "ceil_str": ceil_str,
                }
            )

            # Update display with current results
            self._update_results(
                header_lines, alternates, checked_count, len(nearby), result_widget
            )

        # Final update
        if not self._search_cancelled:
            self._update_results(
                header_lines,
                alternates,
                checked_count,
                len(nearby),
                result_widget,
                done=True,
            )

    def _update_results(
        self,
        header_lines: list,
        alternates: list,
        checked: int,
        total: int,
        result_widget: Static,
        done: bool = False,
    ) -> None:
        """Update the results display"""
        lines = header_lines[:-1]  # Remove the "Searching..." line

        if alternates:
            if done:
                lines.append(
                    f"[bold]VFR/MVFR Alternatives ({len(alternates)} found):[/bold]"
                )
            else:
                lines.append(
                    f"[bold]VFR/MVFR Alternatives ({len(alternates)} found):[/bold] [dim]Checking {checked}/{total}...[/dim]"
                )

            for alt in alternates:
                details = self._format_weather_details(alt["vis_str"], alt["ceil_str"])
                color = alt["color"]
                lines.append(
                    f"  [{color}]{alt['icao']}[/{color}] - {alt['name']} | "
                    f"[{color} bold]{alt['category']}[/{color} bold] {details} | "
                    f"{alt['distance']:.0f}nm {alt['direction']}"
                )
        else:
            if done:
                lines.append(
                    "[yellow]No VFR/MVFR alternatives found within 100nm[/yellow]"
                )
            else:
                lines.append(
                    f"[bold]VFR/MVFR Alternatives:[/bold] [dim]Checking {checked}/{total}...[/dim]"
                )

        result_widget.update("\n".join(lines))

    def action_close(self) -> None:
        """Close the modal"""
        self._search_cancelled = True
        self.dismiss()
