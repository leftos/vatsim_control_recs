"""Diversion Airport Finder Modal Screen

Finds suitable diversion airports for a flight based on:
- Aircraft runway requirements (ADG-based)
- Available instrument approaches (from CIFP)
- Weather conditions (VFR/MVFR/IFR/LIFR)
- ATC staffing status
"""

import asyncio
from enum import Enum
from typing import Dict, List, Optional, Tuple

from rich.text import Text
from textual.screen import ModalScreen
from textual.widgets import Static, Checkbox
from textual.containers import Container, Horizontal
from textual.binding import Binding
from textual.app import ComposeResult

from backend import (
    find_suitable_diversions,
    DiversionOption,
    DiversionFilters,
    get_metar_batch,
    get_required_runway_length,
    haversine_distance_nm,
)
from ui import config
from ui.config import CATEGORY_COLORS
from widgets.split_flap_datatable import SplitFlapDataTable
from .metar_info import get_flight_category, _extract_flight_rules_weather


# Constants for diversion search
DEFAULT_SEARCH_RADIUS_NM = 100.0
MAX_RESULTS = 50


class SortMode(Enum):
    """Sort modes for diversion results"""
    POSITION = "pos"      # Distance from aircraft position (default)
    DESTINATION = "dest"  # Distance from destination airport
    RUNWAY = "runway"     # Longest runway length


class DiversionModal(ModalScreen):
    """Modal screen for finding diversion airports for a flight"""

    CSS = """
    DiversionModal {
        align: center middle;
    }

    #diversion-container {
        width: 100;
        height: auto;
        max-height: 85%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #diversion-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #diversion-subtitle {
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }

    #filter-container {
        height: auto;
        margin-bottom: 1;
        padding: 0 1;
    }

    #filter-container Checkbox {
        margin-right: 2;
        min-width: 20;
    }

    #diversion-table {
        height: 1fr;
        max-height: 25;
        margin-top: 1;
        scrollbar-gutter: stable;
    }

    #diversion-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("r", "refresh", "Refresh", priority=True),
        Binding("1", "sort_position", "Sort by position", priority=True),
        Binding("2", "sort_destination", "Sort by destination", priority=True),
        Binding("3", "sort_runway", "Sort by runway", priority=True),
    ]

    def __init__(
        self,
        flight_data: dict,
        vatsim_data: Optional[dict] = None,
    ):
        """
        Initialize the diversion modal.

        Args:
            flight_data: VATSIM flight data dictionary (from pilots array)
            vatsim_data: Optional full VATSIM data for controller positions
        """
        super().__init__()
        self.flight_data = flight_data
        self.vatsim_data = vatsim_data
        self._search_task: Optional[asyncio.Task] = None
        self._search_cancelled = False

        # Extract flight info
        self.callsign = flight_data.get('callsign', 'Unknown')
        # Handle None values explicitly (get() default only applies when key is missing)
        self.latitude = flight_data.get('latitude') or 0.0
        self.longitude = flight_data.get('longitude') or 0.0
        self.aircraft_type = ''
        self.destination_icao = ''
        self.destination_lat: Optional[float] = None
        self.destination_lon: Optional[float] = None
        if flight_data.get('flight_plan'):
            self.aircraft_type = flight_data['flight_plan'].get('aircraft_short', '')
            self.destination_icao = flight_data['flight_plan'].get('arrival', '')

        # Current filter state
        self.filters = DiversionFilters(
            require_runway_capability=True,
            require_approaches=False,
            require_good_weather=False,
            require_staffed=False,
        )

        # Cache for search results
        self._cached_diversions: List[DiversionOption] = []
        self._weather_data: Dict[str, Tuple[str, str]] = {}
        self._controller_data: Dict[str, List[str]] = {}
        self._search_completed = False  # Track if initial search finished
        self._search_airport_count = 0  # Number of airports in database (for debug)

        # Sort mode (default: by distance from position)
        self._sort_mode = SortMode.POSITION

    def compose(self) -> ComposeResult:
        # Build subtitle with aircraft info
        aircraft_info = self.aircraft_type or "Unknown aircraft"
        required_runway = get_required_runway_length(self.aircraft_type) if self.aircraft_type else 6000
        dest_info = f" → {self.destination_icao}" if self.destination_icao else ""
        subtitle = f"{self.callsign}{dest_info} | {aircraft_info} | Min runway: {required_runway:,}ft"

        with Container(id="diversion-container"):
            yield Static("Diversion Airport Finder", id="diversion-title")
            yield Static(subtitle, id="diversion-subtitle")

            with Horizontal(id="filter-container"):
                yield Checkbox("Runway OK", value=True, id="filter-runway")
                yield Checkbox("Has Approaches", value=False, id="filter-approaches")
                yield Checkbox("VFR/MVFR", value=False, id="filter-weather")
                yield Checkbox("ATC Online", value=False, id="filter-staffed")

            table = SplitFlapDataTable(id="diversion-table", enable_animations=False)
            table.cursor_type = "row"
            yield table
            yield Static("1: Sort Pos | 2: Sort Dest | 3: Sort Runway | R: Refresh | Esc: Close", id="diversion-hint")

    def on_mount(self) -> None:
        """Start searching when mounted"""
        self._setup_table()
        self._lookup_destination_coords()
        self._extract_controller_data()
        self._start_search()

    def _lookup_destination_coords(self) -> None:
        """Look up destination airport coordinates"""
        if not self.destination_icao or not config.UNIFIED_AIRPORT_DATA:
            return

        dest_data = config.UNIFIED_AIRPORT_DATA.get(self.destination_icao, {})
        self.destination_lat = dest_data.get('latitude')
        self.destination_lon = dest_data.get('longitude')

    def _setup_table(self) -> None:
        """Set up the DataTable columns"""
        table = self.query_one("#diversion-table", SplitFlapDataTable)
        table.add_column("Airport", width=26)
        table.add_column("Pos", width=6)  # Distance from current position
        table.add_column("Dest", width=6)  # Distance from destination
        table.add_column("Runway", width=9)
        table.add_column("Approaches", width=15)
        table.add_column("Wx", width=5)
        table.add_column("ATC", width=10)

    def on_unmount(self) -> None:
        """Cancel any pending search when modal is closed"""
        self._search_cancelled = True
        if self._search_task and not self._search_task.done():
            self._search_task.cancel()

    def _extract_controller_data(self) -> None:
        """Extract controller positions from VATSIM data"""
        self._controller_data.clear()

        if not self.vatsim_data:
            return

        controllers = self.vatsim_data.get('controllers', [])
        for controller in controllers:
            callsign = controller.get('callsign', '')
            if not callsign:
                continue

            # Extract facility from callsign (e.g., "KSFO_TWR" -> "KSFO")
            parts = callsign.split('_')
            if len(parts) >= 2:
                facility = parts[0].upper()
                position = parts[-1].upper()

                # Map position suffixes
                pos_map = {
                    'TWR': 'TWR',
                    'GND': 'GND',
                    'DEL': 'DEL',
                    'APP': 'APP',
                    'DEP': 'DEP',
                    'CTR': 'CTR',
                }
                pos_display = pos_map.get(position) or position

                if facility not in self._controller_data:
                    self._controller_data[facility] = []
                if pos_display not in self._controller_data[facility]:
                    self._controller_data[facility].append(pos_display)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Handle filter checkbox changes - triggers fresh search"""
        checkbox_id = event.checkbox.id

        if checkbox_id == "filter-runway":
            self.filters.require_runway_capability = event.value
        elif checkbox_id == "filter-approaches":
            self.filters.require_approaches = event.value
        elif checkbox_id == "filter-weather":
            self.filters.require_good_weather = event.value
        elif checkbox_id == "filter-staffed":
            self.filters.require_staffed = event.value

        # Re-run search with new filters to get relevant results
        self._cached_diversions.clear()
        self._search_completed = False
        self._start_search()

    def _start_search(self) -> None:
        """Start the diversion search"""
        self._search_cancelled = True
        if self._search_task and not self._search_task.done():
            self._search_task.cancel()

        self._search_cancelled = False
        self._search_task = asyncio.create_task(self._search_diversions_async())

    def action_refresh(self) -> None:
        """Refresh the search"""
        self._weather_data.clear()
        self._cached_diversions.clear()
        self._search_completed = False
        self._start_search()

    def action_sort_position(self) -> None:
        """Sort by distance from current position"""
        self._sort_mode = SortMode.POSITION
        self._apply_filters_and_display()

    def action_sort_destination(self) -> None:
        """Sort by distance from destination"""
        self._sort_mode = SortMode.DESTINATION
        self._apply_filters_and_display()

    def action_sort_runway(self) -> None:
        """Sort by runway length (longest first)"""
        self._sort_mode = SortMode.RUNWAY
        self._apply_filters_and_display()

    async def _search_diversions_async(self) -> None:
        """Async search for diversion airports with progressive results"""
        hint_widget = self.query_one("#diversion-hint", Static)
        table = self.query_one("#diversion-table", SplitFlapDataTable)

        airports_data = config.UNIFIED_AIRPORT_DATA
        if not airports_data:
            hint_widget.update("[red]Airport data not loaded[/red]")
            return

        # Validate coordinates - (0, 0) is in the Atlantic, not a valid position
        if self.latitude == 0.0 and self.longitude == 0.0:
            hint_widget.update("[red]Invalid aircraft position (0, 0)[/red]")
            return

        # Basic sanity check for coordinates
        if not (-90 <= self.latitude <= 90) or not (-180 <= self.longitude <= 180):
            hint_widget.update(f"[red]Invalid coordinates: {self.latitude}, {self.longitude}[/red]")
            return

        table.clear()
        loop = asyncio.get_running_loop()

        # Store airport count for debug
        self._search_airport_count = len(airports_data) if airports_data else 0

        # Progressive search: expand radius in steps to show results quickly
        search_radii = [25, 50, 75, 100]  # nm
        all_diversions: List[DiversionOption] = []
        seen_icaos: set = set()

        # Capture current filters for the search
        current_filters = DiversionFilters(
            require_runway_capability=self.filters.require_runway_capability,
            require_approaches=self.filters.require_approaches,
            require_good_weather=self.filters.require_good_weather,
            require_staffed=self.filters.require_staffed,
        )

        for radius in search_radii:
            if self._search_cancelled:
                return

            hint_widget.update(f"[dim]Searching within {radius}nm...[/dim]")

            try:
                def search_at_radius(r: float = radius, f: DiversionFilters = current_filters):
                    return find_suitable_diversions(
                        self.latitude,
                        self.longitude,
                        self.aircraft_type,
                        airports_data,
                        radius_nm=r,
                        filters=f,
                        weather_data=self._weather_data,
                        controller_data=self._controller_data,
                        max_results=200,
                    )

                diversions = await loop.run_in_executor(None, search_at_radius)

                # Add new diversions (avoid duplicates from previous radius)
                for d in diversions:
                    if d.icao not in seen_icaos:
                        seen_icaos.add(d.icao)
                        all_diversions.append(d)

                # Update cache and display after each radius
                self._cached_diversions = all_diversions
                self._search_completed = True
                self._apply_filters_and_display()

                # Small delay to allow UI to update
                await asyncio.sleep(0.05)

            except Exception as e:
                hint_widget.update(f"[red]Search error: {e}[/red]")
                return

        # Final update
        self._cached_diversions = all_diversions
        self._apply_filters_and_display()

        # Fetch weather for displayed diversions in background
        await self._fetch_weather_for_diversions(all_diversions[:50])

    async def _fetch_weather_for_diversions(self, diversions: List[DiversionOption]) -> None:
        """Fetch weather data for diversion airports in batch"""
        if self._search_cancelled:
            return

        loop = asyncio.get_running_loop()

        # Collect ICAOs that need weather (skip already cached)
        icaos_to_fetch = [d.icao for d in diversions if d.icao not in self._weather_data]

        if not icaos_to_fetch:
            return

        # Batch fetch all METARs in parallel
        metars = await loop.run_in_executor(
            None,
            lambda: get_metar_batch(icaos_to_fetch, max_workers=10)
        )

        if self._search_cancelled:
            return

        # Process results
        for icao, metar in metars.items():
            if metar:
                category, _ = get_flight_category(metar)
                vis_str, ceil_str = _extract_flight_rules_weather(metar)
                details = f"{vis_str or ''} {ceil_str or ''}".strip()
                self._weather_data[icao] = (category, details)

                # Update diversion in cache
                for d in self._cached_diversions:
                    if d.icao == icao:
                        d.weather_category = category
                        d.weather_details = details
                        break

        # Update display once with all weather data
        self._apply_filters_and_display()

    def _apply_filters_and_display(self) -> None:
        """Apply current filters and update display"""
        hint_widget = self.query_one("#diversion-hint", Static)
        table = self.query_one("#diversion-table", SplitFlapDataTable)

        if not self._cached_diversions:
            if not self._search_completed:
                # Search still in progress, don't show "no results" message
                return
            hint_widget.update(f"[yellow]No airports within 100nm of {self.latitude:.2f}, {self.longitude:.2f}[/yellow]")
            table.clear()
            return

        # Get required runway for filter
        required_runway = get_required_runway_length(self.aircraft_type) if self.aircraft_type else None

        # Filter diversions
        filtered = []
        for d in self._cached_diversions:
            # Skip the destination airport itself
            if self.destination_icao and d.icao == self.destination_icao:
                continue

            # Runway filter
            if self.filters.require_runway_capability and required_runway:
                if d.longest_runway_ft is None or d.longest_runway_ft < required_runway:
                    continue

            # Approaches filter
            if self.filters.require_approaches and not d.has_approaches:
                continue

            # Weather filter
            if self.filters.require_good_weather:
                if d.weather_category and d.weather_category not in ('VFR', 'MVFR'):
                    continue

            # Staffing filter
            if self.filters.require_staffed and not d.is_staffed:
                continue

            filtered.append(d)

        # Sort based on current sort mode
        if self._sort_mode == SortMode.POSITION:
            # Already sorted by distance from position (default from search)
            filtered.sort(key=lambda d: d.distance_nm)
        elif self._sort_mode == SortMode.DESTINATION:
            # Sort by distance from destination
            def get_dest_distance(d: DiversionOption) -> float:
                if self.destination_lat is None or self.destination_lon is None:
                    return float('inf')
                div_data = config.UNIFIED_AIRPORT_DATA.get(d.icao, {}) if config.UNIFIED_AIRPORT_DATA else {}
                div_lat = div_data.get('latitude')
                div_lon = div_data.get('longitude')
                if div_lat is None or div_lon is None:
                    return float('inf')
                return haversine_distance_nm(
                    self.destination_lat, self.destination_lon,
                    div_lat, div_lon
                )
            filtered.sort(key=get_dest_distance)
        elif self._sort_mode == SortMode.RUNWAY:
            # Sort by runway length (longest first)
            filtered.sort(key=lambda d: d.longest_runway_ft or 0, reverse=True)

        # Limit results after sorting
        filtered = filtered[:MAX_RESULTS]

        # Clear and repopulate table
        table.clear()

        if not filtered:
            hint_widget.update("[yellow]No airports match current filters[/yellow]")
            return

        # Show hotkeys with current sort mode highlighted
        sort_hints = {
            SortMode.POSITION: "[bold]1: Pos[/bold] | 2: Dest | 3: Rwy",
            SortMode.DESTINATION: "1: Pos | [bold]2: Dest[/bold] | 3: Rwy",
            SortMode.RUNWAY: "1: Pos | 2: Dest | [bold]3: Rwy[/bold]",
        }
        sort_hint = sort_hints.get(self._sort_mode, "1: Pos | 2: Dest | 3: Rwy")
        hint_widget.update(f"{sort_hint} | R: Refresh | Esc: Close")

        for d in filtered:
            # Look up airport data once for multiple uses
            div_data = config.UNIFIED_AIRPORT_DATA.get(d.icao, {}) if config.UNIFIED_AIRPORT_DATA else {}

            # Format runway
            if d.longest_runway_ft:
                runway_str = f"{d.longest_runway_ft:,}ft"
            else:
                runway_str = "N/A"

            # Format approaches
            if d.has_approaches:
                if d.approach_count <= 2:
                    app_str = ", ".join(d.approaches[:2])[:15]
                else:
                    app_str = f"{d.approach_count} approaches"
            else:
                app_str = "-"

            # Format weather with color (using shared color config)
            if d.weather_category:
                wx_color = CATEGORY_COLORS.get(d.weather_category, 'white')
                wx_text = Text(d.weather_category, style=f"bold {wx_color}")
            else:
                wx_text = Text("...", style="dim")

            # Format ATC - check if airport is towered
            tower_type = div_data.get('tower_type', '')
            if tower_type == 'NON-ATCT':
                # Untowered airport
                atc_str = "N/A"
            elif d.is_staffed:
                atc_str = ", ".join(d.staffed_positions[:3])
            else:
                # Towered but not staffed
                atc_str = ""

            # Get pretty name if available (with fallback)
            try:
                name = config.DISAMBIGUATOR.get_short_name(d.icao, max_length=16) if config.DISAMBIGUATOR else d.name[:16]
            except Exception:
                name = d.name[:16] if d.name else d.icao

            # Build airport column with ICAO and name
            airport_text = Text()
            airport_text.append(d.icao, style="bold")
            airport_text.append(f" {name}", style="dim")

            # Distance from current position with bearing
            pos_dist_str = f"{d.distance_nm:>3.0f}{d.bearing_compass}"

            # Distance from destination
            if self.destination_lat is not None and self.destination_lon is not None:
                div_lat = div_data.get('latitude')
                div_lon = div_data.get('longitude')
                if div_lat is not None and div_lon is not None:
                    dest_dist = haversine_distance_nm(
                        self.destination_lat, self.destination_lon,
                        div_lat, div_lon
                    )
                    dest_dist_str = f"{dest_dist:>3.0f}nm"
                else:
                    dest_dist_str = "-"
            else:
                dest_dist_str = "-"

            table.add_row(airport_text, pos_dist_str, dest_dist_str, runway_str, app_str, wx_text, atc_str)

    def action_close(self) -> None:
        """Close the modal"""
        self._search_cancelled = True
        self.dismiss()
