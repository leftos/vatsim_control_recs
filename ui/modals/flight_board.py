"""Flight Board Modal Screen"""

import asyncio

from textual.screen import ModalScreen
from textual.widgets import Static
from textual.widgets.data_table import RowDoesNotExist
from textual.containers import Container, Vertical, Horizontal
from textual.binding import Binding
from textual.app import ComposeResult

from backend import get_wind_info, get_altimeter_setting, get_metar_batch, find_airports_near_position
from backend.core.flights import get_airport_flight_details
from backend.data.vatsim_api import download_vatsim_data
from backend.config import constants as backend_constants

from widgets.split_flap_datatable import SplitFlapDataTable
from ui import config
from ui.tables import TableManager, DEPARTURES_TABLE_CONFIG, ARRIVALS_TABLE_CONFIG, GROUPING_DEPARTURES_TABLE_CONFIG, GROUPING_ARRIVALS_TABLE_CONFIG
from ui.modals.flight_info import FlightInfoScreen


class FlightBoardScreen(ModalScreen):
    """Modal screen showing departure and arrivals board for an airport or grouping"""

    # Refresh altimeter cache every 30 seconds for flights on the board
    CACHE_REFRESH_INTERVAL = 30

    CSS = """
    FlightBoardScreen {
        align: center middle;
    }
    
    #board-container {
        width: 90%;
        height: 85%;
        background: $surface;
        border: thick $primary;
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
    """

    BINDINGS = [
        Binding("escape", "close_board", "Close", priority=True),
        Binding("q", "close_board", "Close"),
    ]

    def __init__(self, title: str, airport_icao_or_list, max_eta_hours: float, refresh_interval: int = 15, disambiguator=None, enable_animations: bool = True):
        super().__init__()
        self.title = title
        self.airport_icao_or_list = airport_icao_or_list
        self.max_eta_hours = max_eta_hours
        self.disambiguator = disambiguator
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

    def compose(self) -> ComposeResult:
        # Use a placeholder title initially - will be updated async after data loads
        if isinstance(self.airport_icao_or_list, str):
            initial_title = f"{self.airport_icao_or_list} - Loading..."
        else:
            initial_title = str(self.title)
        self.window_title = initial_title

        with Container(id="board-container"):
            yield Static(self.window_title, id="board-header")
            with Horizontal(id="board-tables", classes="loading"):
                yield Static("Loading flight data...", id="loading-indicator")
                with Vertical(classes="board-section", id="departures-section"):
                    yield Static("DEPARTURES", classes="section-title")
                    departures_table = SplitFlapDataTable(classes="board-table", id="departures-table", enable_animations=self.enable_animations, on_select=self.action_show_flight_info)
                    departures_table.cursor_type = "row"
                    yield departures_table
                with Vertical(classes="board-section", id="arrivals-section"):
                    yield Static("ARRIVALS", classes="section-title")
                    arrivals_table = SplitFlapDataTable(classes="board-table", id="arrivals-table", enable_animations=self.enable_animations, on_select=self.action_show_flight_info)
                    arrivals_table.cursor_type = "row"
                    yield arrivals_table

    async def on_mount(self) -> None:
        """Load and display flight data when the screen is mounted"""
        await self.load_flight_data()
        # Note: Full data refresh is triggered by parent app, not independent timer

        # Start periodic cache refresh for altimeter lookups
        self._cache_refresh_timer = self.set_interval(
            self.CACHE_REFRESH_INTERVAL,
            self._refresh_altimeter_cache
        )

    def on_unmount(self) -> None:
        """Clean up timers when modal is dismissed."""
        if self._cache_refresh_timer:
            self._cache_refresh_timer.stop()
            self._cache_refresh_timer = None

    async def load_flight_data(self) -> None:
        """Load flight data from backend"""

        # Disable parent app activity tracking for the entire operation
        app = self.app
        if hasattr(app, '_disable_activity_watching'):
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
                    0,  # Always show all arrivals on the flight board
                    disambiguator_to_use,
                    unified_data_to_use,
                    aircraft_speeds_to_use,
                    vatsim_data
                )
                return vatsim_data, result[0] if result else [], result[1] if result else []

            # Run all blocking operations in a single executor call
            vatsim_data, departures, arrivals = await loop.run_in_executor(
                None,
                fetch_and_process_flights
            )

            if vatsim_data:
                self.vatsim_data = vatsim_data
                self.departures_data = departures or []
                self.arrivals_data = arrivals or []

                # Populate tables immediately - don't wait for precaching
                self.call_after_refresh(self.populate_tables)

                # Start METAR precaching in background (fire-and-forget, don't block UI)
                self.run_worker(self._precache_flight_altimeters_async(), exclusive=False)
        finally:
            # Re-enable activity tracking
            if hasattr(app, '_enable_activity_watching'):
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

        airports_to_cache = set()
        pilots_by_callsign = {p.get('callsign'): p for p in self.vatsim_data.get('pilots', [])}

        # For departures on the ground, use their departure airport
        for dep in self.departures_data:
            if dep.departure:
                airports_to_cache.add(dep.departure.icao_code)
            # Also add destination in case they need it
            airports_to_cache.add(dep.destination.icao_code)

        # For arrivals (in-flight or on ground), find airports near their current position
        for arr in self.arrivals_data:
            pilot = pilots_by_callsign.get(arr.callsign)
            if pilot:
                lat = pilot.get('latitude')
                lon = pilot.get('longitude')
                if lat is not None and lon is not None:
                    # Find airports near this flight's position
                    nearby = find_airports_near_position(
                        lat, lon,
                        config.UNIFIED_AIRPORT_DATA,
                        radius_nm=75,  # Search within 75nm
                        max_results=3   # Get closest 3 airports
                    )
                    airports_to_cache.update(nearby)

            # Also add origin airport
            airports_to_cache.add(arr.origin.icao_code)
            if arr.arrival:
                airports_to_cache.add(arr.arrival.icao_code)

        # Batch fetch METARs to warm the cache
        if airports_to_cache:
            get_metar_batch(list(airports_to_cache), max_workers=10)

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
            wind_info = get_wind_info(self.airport_icao_or_list, source=backend_constants.WIND_SOURCE)

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
        except Exception:
            # Widget not yet mounted, will be set in compose
            pass

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
            header.update(window_title)
        except Exception:
            pass

    def populate_tables(self) -> None:
        """Populate the departure and arrivals tables with separate ICAO and NAME columns."""

        # Update window title asynchronously - don't block table rendering
        self.run_worker(self._update_window_title_async(), exclusive=False)

        # Detect if this is a grouping (list of airports) or single airport
        is_grouping = isinstance(self.airport_icao_or_list, list) and len(self.airport_icao_or_list) > 1

        # Initialize TableManagers if not already done, using appropriate config
        if self.departures_manager is None:
            departures_table = self.query_one("#departures-table", SplitFlapDataTable)
            departures_config = GROUPING_DEPARTURES_TABLE_CONFIG if is_grouping else DEPARTURES_TABLE_CONFIG
            self.departures_manager = TableManager(departures_table, departures_config, self.departures_row_keys)

        if self.arrivals_manager is None:
            arrivals_table = self.query_one("#arrivals-table", SplitFlapDataTable)
            arrivals_config = GROUPING_ARRIVALS_TABLE_CONFIG if is_grouping else ARRIVALS_TABLE_CONFIG
            self.arrivals_manager = TableManager(arrivals_table, arrivals_config, self.arrivals_row_keys)

        # Transform structured data to tuple format for table display
        if is_grouping:
            # For groupings: show departure airport, destination airport, both with ICAO and name
            # Format: (callsign, from_icao, from_name, dest_icao, dest_name)
            departures_formatted = [
                (dep.callsign,
                 dep.departure.icao_code if dep.departure else "----",
                 dep.departure.pretty_name if dep.departure else "----",
                 dep.destination.icao_code,
                 dep.destination.pretty_name)
                for dep in self.departures_data
            ]

            # For arrivals: show arrival airport first, then origin airport, both with ICAO and name, plus ETA
            # Format: (callsign, to_icao, to_name, orig_icao, orig_name, eta, eta_local)
            arrivals_formatted = [
                (arr.callsign,
                 arr.arrival.icao_code if arr.arrival else "----",
                 arr.arrival.pretty_name if arr.arrival else "----",
                 arr.origin.icao_code,
                 arr.origin.pretty_name,
                 arr.eta_display,
                 arr.eta_local_time)
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
                (arr.callsign, arr.origin.icao_code, arr.origin.pretty_name, arr.eta_display, arr.eta_local_time)
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
        except Exception:
            pass

    def action_close_board(self) -> None:
        """Close the modal"""
        # Reset the flight board open flag and reference in the parent app
        app = self.app
        if hasattr(app, 'flight_board_open'):
            setattr(app, 'flight_board_open', False)
        if hasattr(app, 'active_flight_board'):
            setattr(app, 'active_flight_board', None)

        self.dismiss()

    def action_show_flight_info(self) -> None:
        """Show detailed flight information for the selected flight"""
        if not self.vatsim_data:
            return

        # Determine which table has focus
        departures_table = self.query_one("#departures-table", SplitFlapDataTable)
        arrivals_table = self.query_one("#arrivals-table", SplitFlapDataTable)

        # Get the focused table
        focused_table = None
        if departures_table.has_focus:
            focused_table = departures_table
        elif arrivals_table.has_focus:
            focused_table = arrivals_table
        else:
            # If neither has focus, try to use whichever has a cursor
            if departures_table.cursor_row >= 0:
                focused_table = departures_table
            elif arrivals_table.cursor_row >= 0:
                focused_table = arrivals_table

        if not focused_table or focused_table.cursor_row < 0:
            return

        # Get the row data from the selected row (always first column is callsign)
        try:
            row_data = focused_table.get_row_at(focused_table.cursor_row)
        except RowDoesNotExist:
            return
        if not row_data or len(row_data) == 0:
            return

        # Extract callsign from first column and clean it
        callsign = str(row_data[0]).strip()

        # Find the flight in vatsim_data
        flight_data = None
        pilots_list = self.vatsim_data.get('pilots', [])
        for pilot in pilots_list:
            if pilot.get('callsign', '').strip() == callsign:
                flight_data = pilot
                break

        if not flight_data:
            # Debug: log that flight was not found
            return

        # Open the flight info modal
        self.app.push_screen(FlightInfoScreen(flight_data))
