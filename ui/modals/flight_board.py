"""Flight Board Modal Screen"""

import asyncio
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static
from textual.widgets.data_table import RowDoesNotExist

from backend import (
    find_airports_near_position,
    get_altimeter_setting,
    get_metar_batch,
    get_wind_info,
)
from backend.config import constants as backend_constants
from backend.core.flights import get_airport_flight_details
from backend.data.atis_filter import (
    format_runway_summary,
    parse_approach_info,
)
from backend.data.datis_api import get_datis_for_airports
from backend.data.vatsim_api import download_vatsim_data, get_atis_for_airports
from ui import config
from ui.config import CATEGORY_COLORS
from ui.debug_logger import debug
from ui.modals.flight_info import FlightInfoScreen
from ui.modals.metar_info import get_flight_category
from ui.modals.notification_manager import NotificationManager
from ui.tables import (
    ARRIVALS_TABLE_CONFIG,
    DEPARTURES_TABLE_CONFIG,
    GROUPING_ARRIVALS_TABLE_CONFIG,
    GROUPING_DEPARTURES_TABLE_CONFIG,
    TableManager,
)
from widgets.split_flap_datatable import SplitFlapDataTable

# Rotation order for the board's direction view. A board opened with mixed
# per-airport filters prepends "custom" so the saved configuration stays reachable.
DIRECTION_CYCLE = ("dep", "arr", "both")

DIRECTION_LABELS = {
    "custom": "CUSTOM FILTER",
    "dep": "DEPARTURES ONLY",
    "arr": "ARRIVALS ONLY",
    "both": "DEP + ARR",
}

RunwayPair = tuple[frozenset[str], frozenset[str]]


def _combine_atis_info(
    atis_list: list[dict[str, Any]],
) -> tuple[frozenset[str], frozenset[str], dict[str, frozenset[str]]]:
    """Merge the runways and approach types of every ATIS entry of one airport.

    An airport with dual ATIS (departure and arrival) has one entry per broadcast;
    entries without text contribute nothing.

    Args:
        atis_list: The airport's ATIS entries, each with an optional ``text_atis``.

    Returns:
        Landing runways, departing runways, and each runway's approach types.
    """
    landing: set[str] = set()
    departing: set[str] = set()
    approaches: dict[str, set[str]] = {}
    for atis in atis_list:
        atis_text = atis.get("text_atis", "")
        if not atis_text:
            continue
        info = parse_approach_info(atis_text)
        landing.update(info["landing"])
        departing.update(info["departing"])
        for rwy, approach_types in info["approaches"].items():
            approaches.setdefault(rwy, set()).update(approach_types)
    return (
        frozenset(landing),
        frozenset(departing),
        {rwy: frozenset(types) for rwy, types in approaches.items()},
    )


def _runways_changed(old: RunwayPair, new: RunwayPair) -> bool:
    """Whether the runways in use changed, judged only when both sides name runways.

    A side naming only landing runways uses them for departures too, so
    ``L/D:17L,17R`` and ``L:17L,17R`` are the same assignment.

    Args:
        old: The baseline's landing and departing runways.
        new: The fresh parse's landing and departing runways.

    Returns:
        True when both sides have runways and they differ after that normalization.
    """
    old_landing, old_departing = old
    new_landing, new_departing = new
    if not (old_landing or old_departing) or not (new_landing or new_departing):
        return False
    return new_landing != old_landing or (new_departing or new_landing) != (
        old_departing or old_landing
    )


def _airports_near_pilot(pilot: dict[str, Any]) -> list[str]:
    """The three airports closest to a pilot within 75 nm, or none without a position."""
    lat = pilot.get("latitude")
    lon = pilot.get("longitude")
    if lat is None or lon is None:
        return []
    return find_airports_near_position(
        lat, lon, config.UNIFIED_AIRPORT_DATA, radius_nm=75, max_results=3
    )


def _pilot_with_callsign(
    pilots: list[dict[str, Any]], callsign: str
) -> dict[str, Any] | None:
    """The first pilot whose callsign, stripped of spaces, is the one given."""
    for pilot in pilots:
        if pilot.get("callsign", "").strip() == callsign:
            return pilot
    return None


class FlightBoardScreen(ModalScreen):
    """Modal screen showing departure and arrivals board for an airport or grouping"""

    # Refresh altimeter cache every 30 seconds for flights on the board
    CACHE_REFRESH_INTERVAL = 30

    CSS = """
    FlightBoardScreen {
        align: center middle;
    }

    #board-container {
        width: 100%;
        height: 100%;
        background: $surface;
        border: none;
    }

    #board-header {
        height: 3;
        background: $boost;
        color: $text;
        content-align: center middle;
        text-align: center;
        border-bottom: solid $primary;
    }

    #board-tables {
        height: 1fr;
        layout: horizontal;
    }

    .board-section {
        height: 100%;
    }

    #departures-section {
        width: 40%;
    }

    #arrivals-section {
        width: 60%;
    }

    .section-title {
        height: 1;
        background: $panel;
        content-align: center middle;
        text-align: center;
        color: $text;
        text-style: bold;
    }

    .board-table {
        height: 1fr;
        width: 100%;
    }

    #loading-indicator {
        width: 100%;
        height: 100%;
        content-align: center middle;
        text-align: center;
        color: $text-muted;
    }

    #loading-indicator.hidden {
        display: none;
    }

    #board-tables.loading .board-table {
        display: none;
    }

    #notification-container {
        dock: bottom;
        width: 100%;
        height: auto;
        max-height: 15;
        layer: notification;
    }

    #notification-spacer {
        width: 40%;
        height: 1;
    }

    #notification-scroll {
        width: 60%;
        height: auto;
        overflow-y: auto;
        scrollbar-size: 0 0;
        border: none;
    }

    .notification-item {
        width: 100%;
        height: 1;
        padding: 0 1;
        background: $boost;
        text-align: right;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close_board", "Close", priority=True),
        Binding("q", "close_board", "Close"),
        Binding("f", "cycle_direction", "Dep/Arr/Both"),
        Binding("ctrl+n", "test_notifications", "Test Notifications", show=False),
    ]

    # Weather check interval in seconds (check for condition changes)
    WEATHER_CHECK_INTERVAL = 60

    def __init__(
        self,
        title: str,
        airport_icao_or_list,
        refresh_interval: int = 15,
        disambiguator=None,
        enable_animations: bool = True,
        airport_filters: dict[str, str] | None = None,
    ):
        super().__init__()
        self.title = title
        self.airport_icao_or_list = airport_icao_or_list
        self.disambiguator = disambiguator
        # Filters as saved by the caller. The F key overrides them for this board
        # only; the underlying favorite is never rewritten.
        self._base_filters = airport_filters
        self._direction_modes = self._build_direction_modes()
        self._direction_index = 0
        self.airport_filters = self._filters_for_mode(self._direction_modes[0])
        self.departures_data = []
        self.arrivals_data = []
        self.refresh_interval = refresh_interval
        self.departures_row_keys = []
        self.arrivals_row_keys = []
        self.enable_animations = enable_animations
        self.window_title = ""  # Store the current window title
        # TableManagers will be initialized after tables are created
        self.departures_manager = None
        self.arrivals_manager = None
        self.vatsim_data = None  # Store VATSIM data for flight info lookup
        self._cache_refresh_timer = None  # Timer for periodic cache refresh
        # Weather and runway change tracking
        self._previous_weather: dict[str, str] = {}  # ICAO -> flight category
        self._previous_runways: dict[
            str, RunwayPair
        ] = {}  # ICAO -> (landing, departing)
        self._previous_approaches: dict[
            str, dict[str, frozenset]
        ] = {}  # ICAO -> {runway: frozenset of approach types}
        self._weather_check_timer = None  # Timer for weather/runway change checks
        # Notification manager (initialized in on_mount)
        self._notification_manager: NotificationManager | None = None

    def _airport_icaos(self) -> list[str]:
        """The board's airports, whether it was opened on one airport or a grouping."""
        if isinstance(self.airport_icao_or_list, str):
            return [self.airport_icao_or_list]
        return list(self.airport_icao_or_list)

    def _classify_base_filters(self) -> str:
        """Reduce the saved filters to a single direction mode, or "custom" if mixed."""
        if not self._base_filters:
            return "both"
        modes = {self._base_filters.get(icao) for icao in self._airport_icaos()}
        if modes == {"dep"}:
            return "dep"
        if modes == {"arr"}:
            return "arr"
        if modes == {None}:
            return "both"
        return "custom"

    def _build_direction_modes(self) -> list[str]:
        """Order the rotation so it starts on the mode the board was opened with."""
        base_mode = self._classify_base_filters()
        if base_mode == "custom":
            return ["custom", *DIRECTION_CYCLE]
        start = DIRECTION_CYCLE.index(base_mode)
        return [*DIRECTION_CYCLE[start:], *DIRECTION_CYCLE[:start]]

    def _current_direction_mode(self) -> str:
        return self._direction_modes[self._direction_index]

    def _filters_for_mode(self, mode: str) -> dict[str, str] | None:
        """Per-airport filters the backend should apply for a direction mode."""
        if mode == "custom":
            return self._base_filters
        if mode == "both":
            return None
        return dict.fromkeys(self._airport_icaos(), mode)

    def _visible_sections(self) -> tuple[bool, bool]:
        """Whether the departures and arrivals panels can hold anything right now."""
        filters = self.airport_filters
        if not filters:
            return True, True
        icaos = self._airport_icaos()
        return (
            any(filters.get(icao) != "arr" for icao in icaos),
            any(filters.get(icao) != "dep" for icao in icaos),
        )

    def _header_text(self, window_title: str) -> str:
        """Title plus a mode suffix, so a hidden panel never looks like missing data."""
        mode = self._current_direction_mode()
        if mode == "both":
            return window_title
        return f"{window_title}  ·  {DIRECTION_LABELS[mode]}"

    def _apply_direction_view(self) -> None:
        """Show only the panels the current filters can fill, and resize them to fit."""
        show_departures, show_arrivals = self._visible_sections()
        both_visible = show_departures and show_arrivals

        departures_section = self.query_one("#departures-section", Vertical)
        arrivals_section = self.query_one("#arrivals-section", Vertical)
        departures_section.display = show_departures
        arrivals_section.display = show_arrivals
        departures_section.styles.width = "40%" if both_visible else "100%"
        arrivals_section.styles.width = "60%" if both_visible else "100%"

        # Notifications are right-aligned against the arrivals panel, so the spacer
        # that reserves the departures width is wrong once a panel is hidden.
        spacer = self.query_one("#notification-spacer", Static)
        notification_scroll = self.query_one("#notification-scroll", VerticalScroll)
        spacer.display = both_visible
        notification_scroll.styles.width = "60%" if both_visible else "100%"

        if not both_visible:
            visible_table = "#arrivals-table" if show_arrivals else "#departures-table"
            self.query_one(visible_table, SplitFlapDataTable).focus()

    def action_cycle_direction(self) -> None:
        """Rotate the board between departures only, arrivals only and both."""
        self._direction_index = (self._direction_index + 1) % len(self._direction_modes)
        self.airport_filters = self._filters_for_mode(self._current_direction_mode())
        self._apply_direction_view()
        self._apply_window_title(self.window_title)
        self.refresh_flight_data()

    def compose(self) -> ComposeResult:
        # Use a placeholder title initially - will be updated async after data loads
        if isinstance(self.airport_icao_or_list, str):
            initial_title = f"{self.airport_icao_or_list} - Loading..."
        else:
            initial_title = str(self.title)
        self.window_title = initial_title

        with Container(id="board-container"):
            yield Static(self._header_text(self.window_title), id="board-header")
            with Horizontal(id="board-tables", classes="loading"):
                yield Static("Loading flight data...", id="loading-indicator")
                with Vertical(classes="board-section", id="departures-section"):
                    yield Static("DEPARTURES", classes="section-title")
                    departures_table = SplitFlapDataTable(
                        classes="board-table",
                        id="departures-table",
                        enable_animations=self.enable_animations,
                        on_select=self.action_show_flight_info,
                    )
                    departures_table.cursor_type = "row"
                    yield departures_table
                with Vertical(classes="board-section", id="arrivals-section"):
                    yield Static("ARRIVALS", classes="section-title")
                    arrivals_table = SplitFlapDataTable(
                        classes="board-table",
                        id="arrivals-table",
                        enable_animations=self.enable_animations,
                        on_select=self.action_show_flight_info,
                    )
                    arrivals_table.cursor_type = "row"
                    yield arrivals_table
            # Notification container with spacer for right-alignment
            with Horizontal(id="notification-container"):
                yield Static("", id="notification-spacer")  # Left spacer
                yield VerticalScroll(id="notification-scroll")

    async def on_mount(self) -> None:
        """Load and display flight data when the screen is mounted"""
        # Initialize notification manager
        self._notification_manager = NotificationManager(
            self, container_id="notification-scroll"
        )

        # Lay the panels out before the first fetch so the loading state already
        # reflects the board's filters.
        self._apply_direction_view()

        await self.load_flight_data()
        # Note: Full data refresh is triggered by parent app, not independent timer

        # Start periodic cache refresh for altimeter lookups
        self._cache_refresh_timer = self.set_interval(
            self.CACHE_REFRESH_INTERVAL, self._refresh_altimeter_cache
        )

        # Start weather/runway change monitoring for groupings
        if (
            isinstance(self.airport_icao_or_list, list)
            and len(self.airport_icao_or_list) > 1
        ):
            # Initialize baseline weather and runways (fire-and-forget)
            self.run_worker(self._initialize_change_baseline(), exclusive=False)
            # Start periodic weather/runway checks
            self._weather_check_timer = self.set_interval(
                self.WEATHER_CHECK_INTERVAL, self._check_for_changes
            )

    def on_unmount(self) -> None:
        """Clean up timers when modal is dismissed."""
        if self._cache_refresh_timer:
            self._cache_refresh_timer.stop()
            self._cache_refresh_timer = None
        if self._weather_check_timer:
            self._weather_check_timer.stop()
            self._weather_check_timer = None
        if self._notification_manager:
            self._notification_manager.cleanup()

    async def load_flight_data(self) -> None:
        """Load flight data from backend"""

        # Disable parent app activity tracking for the entire operation
        app = self.app
        if hasattr(app, "_disable_activity_watching"):
            app._disable_activity_watching()  # type: ignore[attr-defined]

        try:
            loop = asyncio.get_event_loop()

            # Use the module-level instances
            unified_data_to_use = config.UNIFIED_AIRPORT_DATA
            disambiguator_to_use = config.DISAMBIGUATOR
            aircraft_speeds_to_use = config.AIRCRAFT_APPROACH_SPEEDS

            # Helper function to run all blocking operations in one executor call
            def fetch_and_process_flights():
                """Fetch VATSIM data and process flights - runs in thread pool."""
                vatsim_data = download_vatsim_data()
                if not vatsim_data:
                    return None, None, None

                result = get_airport_flight_details(
                    self.airport_icao_or_list,
                    disambiguator_to_use,
                    unified_data_to_use,
                    aircraft_speeds_to_use,
                    vatsim_data,
                    self.airport_filters,
                )
                return (
                    vatsim_data,
                    result[0] if result else [],
                    result[1] if result else [],
                )

            # Run all blocking operations in a single executor call
            vatsim_data, departures, arrivals = await loop.run_in_executor(
                None, fetch_and_process_flights
            )

            if vatsim_data:
                self.vatsim_data = vatsim_data
                self.departures_data = departures or []
                self.arrivals_data = arrivals or []

                # Populate tables immediately - don't wait for precaching
                self.call_after_refresh(self.populate_tables)

                # Start METAR precaching in background (fire-and-forget, don't block UI)
                self.run_worker(
                    self._precache_flight_altimeters_async(), exclusive=False
                )
        finally:
            # Re-enable activity tracking
            if hasattr(app, "_enable_activity_watching"):
                app._enable_activity_watching()  # type: ignore[attr-defined]

    async def _precache_flight_altimeters_async(self) -> None:
        """Async wrapper for precaching METARs in background."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._precache_flight_altimeters)

    def _precache_flight_altimeters(self) -> None:
        """Precache METARs for airports near displayed flights.

        This warms the cache so that when users click on a flight to see its info,
        the altimeter lookup is nearly instant instead of requiring network calls.
        """
        if not self.vatsim_data or not config.UNIFIED_AIRPORT_DATA:
            return

        airports_to_cache: set[str] = set()
        for dep in self.departures_data:
            if dep.departure:
                airports_to_cache.add(dep.departure.icao_code)
            airports_to_cache.add(dep.destination.icao_code)
        airports_to_cache.update(
            self._arrival_airports_to_cache(self.vatsim_data.get("pilots", []))
        )

        if airports_to_cache:
            get_metar_batch(list(airports_to_cache), max_workers=10)

    def _arrival_airports_to_cache(self, pilots: list[dict[str, Any]]) -> set[str]:
        """Airports whose METARs the board's arrivals may need.

        Each arrival contributes its origin, its arrival airport when known, and
        the airports near its current position when the feed has its pilot.

        Args:
            pilots: The VATSIM feed's pilot records.

        Returns:
            The ICAO codes to warm the METAR cache for.
        """
        pilots_by_callsign = {p.get("callsign"): p for p in pilots}
        airports: set[str] = set()
        for arr in self.arrivals_data:
            pilot = pilots_by_callsign.get(arr.callsign)
            if pilot:
                airports.update(_airports_near_pilot(pilot))
            airports.add(arr.origin.icao_code)
            if arr.arrival:
                airports.add(arr.arrival.icao_code)
        return airports

    async def _refresh_altimeter_cache(self) -> None:
        """Periodically refresh the altimeter cache for displayed flights.

        This runs in the background to keep the cache warm as flights move,
        ensuring fast altimeter lookups when users click on flight info.
        """
        if not self.vatsim_data or not self.arrivals_data:
            return

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._precache_flight_altimeters)

    def refresh_flight_data(self) -> None:
        """Refresh flight data (called by parent app)"""
        self.run_worker(self.load_flight_data(), exclusive=True)

    def _update_window_title(self) -> None:
        """Update the window title with fresh wind and altimeter data."""
        window_title: str
        if self.disambiguator and isinstance(self.airport_icao_or_list, str):
            # For individual airports, get the full name
            full_name = self.disambiguator.get_full_name(self.airport_icao_or_list)

            # Fetch wind information using the current global wind source
            wind_info = get_wind_info(
                self.airport_icao_or_list, source=backend_constants.WIND_SOURCE
            )

            # Fetch altimeter information (use raw format: A2992 or Q1013)
            altimeter_info = get_altimeter_setting(self.airport_icao_or_list)

            # Format title: "Airport Name (ICAO) - Altimeter Wind"
            title_parts = [f"{full_name} ({self.airport_icao_or_list})"]
            conditions_part = ""
            if altimeter_info:
                conditions_part += f"{altimeter_info}"
            if wind_info:
                if conditions_part:
                    conditions_part += " "
                conditions_part += f"{wind_info}"

            if conditions_part:
                title_parts.append(conditions_part)

            window_title = " - ".join(title_parts)
        else:
            # For groupings or when no disambiguator is available, use the original title
            window_title = str(self.title)

        self.window_title = window_title

        # Set the console window title
        self.app.console.set_window_title(window_title)

        # Update the header Static widget if it exists
        try:
            header = self.query_one("#board-header", Static)
            header.update(window_title)
        except Exception as e:
            # Widget not yet mounted, will be set in compose
            debug(f"Could not update board header yet: {e}")

    async def _update_window_title_async(self) -> None:
        """Async version of window title update - runs network calls in background."""
        if not self.disambiguator or not isinstance(self.airport_icao_or_list, str):
            # For groupings, just use the title directly (no network calls needed)
            self._apply_window_title(str(self.title))
            return

        icao = self.airport_icao_or_list
        loop = asyncio.get_event_loop()

        # Get full name synchronously (uses cache, no network)
        full_name = self.disambiguator.get_full_name(icao)

        # Run network calls in thread pool to avoid blocking UI
        def fetch_weather_data():
            wind = get_wind_info(icao, source=backend_constants.WIND_SOURCE)
            altimeter = get_altimeter_setting(icao)
            return wind, altimeter

        wind_info, altimeter_info = await loop.run_in_executor(None, fetch_weather_data)

        # Build the title
        title_parts = [f"{full_name} ({icao})"]
        conditions_part = ""
        if altimeter_info:
            conditions_part += f"{altimeter_info}"
        if wind_info:
            if conditions_part:
                conditions_part += " "
            conditions_part += f"{wind_info}"

        if conditions_part:
            title_parts.append(conditions_part)

        window_title = " - ".join(title_parts)
        self._apply_window_title(window_title)

    def _apply_window_title(self, window_title: str) -> None:
        """Apply the window title to the header and console."""
        self.window_title = window_title
        self.app.console.set_window_title(window_title)

        try:
            header = self.query_one("#board-header", Static)
            header.update(self._header_text(window_title))
        except Exception as e:
            debug(f"Could not apply board header title: {e}")

    def populate_tables(self) -> None:
        """Populate the departure and arrivals tables with separate ICAO and NAME columns."""

        # Update window title asynchronously - don't block table rendering
        self.run_worker(self._update_window_title_async(), exclusive=False)

        # Detect if this is a grouping (list of airports) or single airport
        is_grouping = (
            isinstance(self.airport_icao_or_list, list)
            and len(self.airport_icao_or_list) > 1
        )

        # Initialize TableManagers if not already done, using appropriate config
        if self.departures_manager is None:
            departures_table = self.query_one("#departures-table", SplitFlapDataTable)
            departures_config = (
                GROUPING_DEPARTURES_TABLE_CONFIG
                if is_grouping
                else DEPARTURES_TABLE_CONFIG
            )
            self.departures_manager = TableManager(
                departures_table, departures_config, self.departures_row_keys
            )

        if self.arrivals_manager is None:
            arrivals_table = self.query_one("#arrivals-table", SplitFlapDataTable)
            arrivals_config = (
                GROUPING_ARRIVALS_TABLE_CONFIG if is_grouping else ARRIVALS_TABLE_CONFIG
            )
            self.arrivals_manager = TableManager(
                arrivals_table, arrivals_config, self.arrivals_row_keys
            )

        # Transform structured data to tuple format for table display
        if is_grouping:
            # For groupings: show departure airport, destination airport, both with ICAO and name
            # Format: (callsign, from_icao, from_name, dest_icao, dest_name)
            departures_formatted = [
                (
                    dep.callsign,
                    dep.departure.icao_code if dep.departure else "----",
                    dep.departure.pretty_name if dep.departure else "----",
                    dep.destination.icao_code,
                    dep.destination.pretty_name,
                )
                for dep in self.departures_data
            ]

            # For arrivals: show arrival airport first, then origin airport, both with ICAO and name, plus ETA
            # Format: (callsign, to_icao, to_name, orig_icao, orig_name, eta, eta_local)
            arrivals_formatted = [
                (
                    arr.callsign,
                    arr.arrival.icao_code if arr.arrival else "----",
                    arr.arrival.pretty_name if arr.arrival else "----",
                    arr.origin.icao_code,
                    arr.origin.pretty_name,
                    arr.eta_display,
                    arr.eta_local_time,
                )
                for arr in self.arrivals_data
            ]
        else:
            # For single airport: show only destination for departures, origin for arrivals
            # Format: (callsign, icao, name)
            departures_formatted = [
                (dep.callsign, dep.destination.icao_code, dep.destination.pretty_name)
                for dep in self.departures_data
            ]

            # Format: (callsign, icao, name, eta, eta_local)
            arrivals_formatted = [
                (
                    arr.callsign,
                    arr.origin.icao_code,
                    arr.origin.pretty_name,
                    arr.eta_display,
                    arr.eta_local_time,
                )
                for arr in self.arrivals_data
            ]

        # Populate tables using TableManagers
        self.departures_manager.populate(departures_formatted)
        self.arrivals_manager.populate(arrivals_formatted)

        # Hide loading indicator and show tables
        try:
            board_tables = self.query_one("#board-tables", Horizontal)
            board_tables.remove_class("loading")
            loading_indicator = self.query_one("#loading-indicator", Static)
            loading_indicator.add_class("hidden")
        except Exception as e:
            debug(f"Could not hide flight board loading indicator: {e}")

    def action_close_board(self) -> None:
        """Close the modal, but dismiss notification first if visible."""
        # Check if notification is visible - if so, dismiss it first
        if self._notification_manager and self._notification_manager.is_visible():
            self._notification_manager.dismiss()
            return  # Don't close the board yet

        # Reset the flight board open flag and reference in the parent app
        app = self.app
        if hasattr(app, "flight_board_open"):
            app.flight_board_open = False
        if hasattr(app, "active_flight_board"):
            app.active_flight_board = None

        self.dismiss()

    def action_test_notifications(self) -> None:
        """Test notification stacking by showing multiple notifications at once."""
        if not self._notification_manager:
            return

        # Show 4 test notifications with different types
        self._notification_manager.show(
            "[bold]WX:[/bold] KSFO (San Francisco Intl) now [red bold]IFR[/red bold] [dim](was MVFR)[/dim]",
            "[dim]WX: KSFO (San Francisco Intl) now IFR (was MVFR)[/dim]",
            flash=True,
        )

        self._notification_manager.show(
            "[bold]RWY:[/bold] KOAK (Oakland Intl) now [yellow bold]L:27R,28R D:27R,28R[/yellow bold] [dim](was L:28R D:28R)[/dim]",
            "[dim]RWY: KOAK (Oakland Intl) now L:27R,28R D:27R,28R (was L:28R D:28R)[/dim]",
            flash=True,
        )

        self._notification_manager.show(
            "[bold]APP:[/bold] KSJC (San Jose Intl) RWY 30L now [cyan bold]ILS/RNAV[/cyan bold] [dim](was VISUAL)[/dim]",
            "[dim]APP: KSJC (San Jose Intl) RWY 30L now ILS/RNAV (was VISUAL)[/dim]",
            flash=True,
        )

        self._notification_manager.show(
            "[bold]WX:[/bold] KSAC (Sacramento Intl) now [yellow bold]MVFR[/yellow bold] [dim](was VFR)[/dim]",
            "[dim]WX: KSAC (Sacramento Intl) now MVFR (was VFR)[/dim]",
            flash=True,
        )

    def action_show_flight_info(self) -> None:
        """Show detailed flight information for the selected flight"""
        if not self.vatsim_data:
            return

        callsign = self._selected_callsign()
        if callsign is None:
            return

        flight_data = _pilot_with_callsign(self.vatsim_data.get("pilots", []), callsign)
        if not flight_data:
            return

        self.app.push_screen(FlightInfoScreen(flight_data))

    def _selected_table(self) -> SplitFlapDataTable | None:
        """The visible table the user picked a flight in.

        A focused table wins, then one with a cursor row, departures before
        arrivals in each case. A panel the direction mode hides is ignored: its
        cursor row is left over from before the mode changed.

        Returns:
            The table, or None when no visible table is focused or has a cursor row.
        """
        departures_table = self.query_one("#departures-table", SplitFlapDataTable)
        arrivals_table = self.query_one("#arrivals-table", SplitFlapDataTable)
        show_departures, show_arrivals = self._visible_sections()
        visible = [
            table
            for table, shown in (
                (departures_table, show_departures),
                (arrivals_table, show_arrivals),
            )
            if shown
        ]
        for table in visible:
            if table.has_focus:
                return table
        for table in visible:
            if table.cursor_row >= 0:
                return table
        return None

    def _selected_callsign(self) -> str | None:
        """The callsign in the first column of the selected row, stripped of spaces.

        Returns:
            The callsign, or None when no row is selected or the row is gone or empty.
        """
        table = self._selected_table()
        if not table or table.cursor_row < 0:
            return None
        try:
            row_data = table.get_row_at(table.cursor_row)
        except RowDoesNotExist:
            return None
        if not row_data:
            return None
        return str(row_data[0]).strip()

    # --- Weather and runway change notification methods ---

    async def _fetch_conditions(
        self, airports: list[str]
    ) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]]]:
        """Fetch the airports' METARs and ATIS for change detection.

        METARs and the VATSIM feed are fetched in parallel. VATSIM ATIS is
        preferred; airports without it are asked of the real-world D-ATIS.

        Args:
            airports: ICAO codes of the board's airports.

        Returns:
            METARs by ICAO, and the ATIS entries of each airport that has any.
        """
        loop = asyncio.get_event_loop()
        metar_task = loop.run_in_executor(None, get_metar_batch, airports)
        vatsim_task = loop.run_in_executor(None, download_vatsim_data)
        metars, vatsim_data = await asyncio.gather(metar_task, vatsim_task)

        atis_data: dict[str, list[dict[str, Any]]] = {}
        if vatsim_data:
            atis_data = get_atis_for_airports(vatsim_data, airports)

        airports_without_vatsim_atis = [
            icao for icao in airports if icao not in atis_data
        ]
        if airports_without_vatsim_atis:
            atis_data.update(get_datis_for_airports(airports_without_vatsim_atis))
        return metars, atis_data

    async def _initialize_change_baseline(self) -> None:
        """Fetch initial weather/runway conditions to establish baseline for change detection."""
        if not isinstance(self.airport_icao_or_list, list):
            return

        airports = self.airport_icao_or_list
        metars, atis_data = await self._fetch_conditions(airports)

        for icao in airports:
            metar = metars.get(icao, "")
            if metar:
                self._previous_weather[icao] = get_flight_category(metar)

        for icao in airports:
            atis_list = atis_data.get(icao, [])
            if not atis_list:
                continue
            landing, departing, approaches = _combine_atis_info(atis_list)
            if landing or departing:
                self._previous_runways[icao] = (landing, departing)
                self._previous_approaches[icao] = approaches

    async def _check_for_changes(self) -> None:
        """Check for weather/runway condition changes and show notifications."""
        if not isinstance(self.airport_icao_or_list, list):
            return

        airports = self.airport_icao_or_list
        metars, atis_data = await self._fetch_conditions(airports)

        for icao in airports:
            metar = metars.get(icao, "")
            if metar:
                self._check_weather_change(
                    icao, get_flight_category(metar), icao in atis_data
                )

        for icao in airports:
            atis_list = atis_data.get(icao, [])
            if atis_list:
                self._check_runway_changes(icao, *_combine_atis_info(atis_list))

    def _check_weather_change(
        self, icao: str, new_category: str, has_atis: bool
    ) -> None:
        """Toast a flight category change against the baseline, then make it the baseline."""
        old_category = self._previous_weather.get(icao)
        if old_category and new_category != old_category:
            self._show_weather_notification(icao, new_category, old_category, has_atis)
        self._previous_weather[icao] = new_category

    def _check_runway_changes(
        self,
        icao: str,
        new_landing: frozenset[str],
        new_departing: frozenset[str],
        new_approaches: dict[str, frozenset[str]],
    ) -> None:
        """Toast runway and approach changes against the baseline, then update it.

        The baseline is overwritten only by a parse that found runways (or
        approaches), so a transient parsing gap neither notifies nor erases it.

        Args:
            icao: The airport.
            new_landing: Landing runways in the fresh parse.
            new_departing: Departing runways in the fresh parse.
            new_approaches: Approach types per runway in the fresh parse.
        """
        old_runways = self._previous_runways.get(icao)
        if old_runways and _runways_changed(old_runways, (new_landing, new_departing)):
            old_landing, old_departing = old_runways
            self._show_runway_notification(
                icao, new_landing, new_departing, old_landing, old_departing
            )

        self._notify_approach_changes(
            icao, self._previous_approaches.get(icao, {}), new_approaches
        )

        if new_landing or new_departing:
            self._previous_runways[icao] = (new_landing, new_departing)
        if new_approaches:
            self._previous_approaches[icao] = new_approaches

    def _notify_approach_changes(
        self,
        icao: str,
        old_approaches: dict[str, frozenset[str]],
        new_approaches: dict[str, frozenset[str]],
    ) -> None:
        """Toast each runway whose approach types changed, for runways on both sides."""
        if not old_approaches or not new_approaches:
            return
        for rwy, new_app in new_approaches.items():
            if rwy in old_approaches and old_approaches[rwy] != new_app:
                self._show_approach_notification(
                    icao, rwy, new_app, old_approaches[rwy]
                )

    def _show_weather_notification(
        self, icao: str, new_category: str, old_category: str, has_atis: bool = False
    ) -> None:
        """Show a weather change notification toast, with flashing if airport has ATIS."""
        if not self._notification_manager:
            return

        new_color = CATEGORY_COLORS.get(new_category, "white")
        old_color = CATEGORY_COLORS.get(old_category, "white")

        # Get airport name
        airport_name = icao
        if config.DISAMBIGUATOR:
            airport_name = config.DISAMBIGUATOR.get_full_name(icao)

        # Build bright version (normal colors)
        text_bright = (
            f"[bold]WX:[/bold] {icao} ({airport_name}) now "
            f"[{new_color} bold]{new_category}[/{new_color} bold] "
            f"[dim](was [{old_color}]{old_category}[/{old_color}])[/dim]"
        )

        # Build dim version (all dim)
        text_dim = (
            f"[dim]WX: {icao} ({airport_name}) now "
            f"{new_category} "
            f"(was {old_category})[/dim]"
        )

        self._notification_manager.show(text_bright, text_dim, flash=has_atis)

    def _show_runway_notification(
        self,
        icao: str,
        new_landing: frozenset,
        new_departing: frozenset,
        old_landing: frozenset,
        old_departing: frozenset,
    ) -> None:
        """Show a runway change notification toast."""
        if not self._notification_manager:
            return

        # Get airport name
        airport_name = icao
        if config.DISAMBIGUATOR:
            airport_name = config.DISAMBIGUATOR.get_full_name(icao)

        # Format runway changes
        new_summary = format_runway_summary(
            {"landing": set(new_landing), "departing": set(new_departing)}
        )
        old_summary = format_runway_summary(
            {"landing": set(old_landing), "departing": set(old_departing)}
        )

        # Build notification text
        text_bright = (
            f"[bold]RWY:[/bold] {icao} ({airport_name}) now "
            f"[yellow bold]{new_summary}[/yellow bold] "
            f"[dim](was {old_summary})[/dim]"
        )

        text_dim = (
            f"[dim]RWY: {icao} ({airport_name}) now "
            f"{new_summary} "
            f"(was {old_summary})[/dim]"
        )

        # Always flash for runway changes (they're always from staffed airports with ATIS)
        self._notification_manager.show(text_bright, text_dim, flash=True)

    def _show_approach_notification(
        self,
        icao: str,
        runway: str,
        new_approaches: frozenset,
        old_approaches: frozenset,
    ) -> None:
        """Show an approach type change notification toast."""
        if not self._notification_manager:
            return

        # Get airport name
        airport_name = icao
        if config.DISAMBIGUATOR:
            airport_name = config.DISAMBIGUATOR.get_full_name(icao)

        # Format approach types
        new_app_str = "/".join(sorted(new_approaches))
        old_app_str = "/".join(sorted(old_approaches))

        # Build notification text
        text_bright = (
            f"[bold]APP:[/bold] {icao} ({airport_name}) RWY {runway} now "
            f"[cyan bold]{new_app_str}[/cyan bold] "
            f"[dim](was {old_app_str})[/dim]"
        )

        text_dim = (
            f"[dim]APP: {icao} ({airport_name}) RWY {runway} now "
            f"{new_app_str} "
            f"(was {old_app_str})[/dim]"
        )

        # Flash for approach changes
        self._notification_manager.show(text_bright, text_dim, flash=True)
