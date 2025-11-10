"""Flight Board Modal Screen"""

import asyncio
import os

from textual.screen import ModalScreen
from textual.widgets import Static
from textual.containers import Container, Vertical, Horizontal
from textual.binding import Binding
from textual.app import ComposeResult

from backend import get_wind_info
from backend.core.flights import get_airport_flight_details
from backend.cache.manager import load_aircraft_approach_speeds
from backend.data.vatsim_api import download_vatsim_data
from backend.config import constants as backend_constants

from widgets.split_flap_datatable import SplitFlapDataTable
from ui import config
from ui.tables import TableManager, DEPARTURES_TABLE_CONFIG, ARRIVALS_TABLE_CONFIG


class FlightBoardScreen(ModalScreen):
    """Modal screen showing departure and arrivals board for an airport or grouping"""
    
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
    
    def compose(self) -> ComposeResult:
        # Build initial window title
        self._update_window_title()
        
        with Container(id="board-container"):
            yield Static(self.window_title, id="board-header")
            with Horizontal(id="board-tables"):
                with Vertical(classes="board-section", id="departures-section"):
                    yield Static("DEPARTURES", classes="section-title")
                    departures_table = SplitFlapDataTable(classes="board-table", id="departures-table", enable_animations=self.enable_animations)
                    departures_table.cursor_type = "row"
                    yield departures_table
                with Vertical(classes="board-section", id="arrivals-section"):
                    yield Static("ARRIVALS", classes="section-title")
                    arrivals_table = SplitFlapDataTable(classes="board-table", id="arrivals-table", enable_animations=self.enable_animations)
                    arrivals_table.cursor_type = "row"
                    yield arrivals_table
    
    async def on_mount(self) -> None:
        """Load and display flight data when the screen is mounted"""
        await self.load_flight_data()
        # Note: Refresh is now triggered by parent app, not independent timer
    
    async def load_flight_data(self) -> None:
        """Load flight data from backend"""
        
        # Disable parent app activity tracking for the entire operation
        app = self.app
        if hasattr(app, 'watch_for_user_activity'):
            setattr(app, 'watch_for_user_activity', False)
        
        try:
            # Run the blocking call in a thread pool
            loop = asyncio.get_event_loop()
            # Prepare parameters for get_airport_flight_details
            aircraft_speeds = load_aircraft_approach_speeds(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'aircraft_data.csv'))
            vatsim_data = download_vatsim_data()
            
            # Use the module-level instances
            unified_data_to_use = config.UNIFIED_AIRPORT_DATA
            disambiguator_to_use = config.DISAMBIGUATOR
            
            result = await loop.run_in_executor(
                None,
                get_airport_flight_details,
                self.airport_icao_or_list,
                0,  # Always show all arrivals on the flight board
                disambiguator_to_use,
                unified_data_to_use,
                aircraft_speeds,
                vatsim_data
            )
            
            if result:
                self.departures_data, self.arrivals_data = result
                # Defer populate_tables until after the widget tree is fully mounted
                self.call_after_refresh(self.populate_tables)
        finally:
            # Re-enable activity tracking
            if hasattr(app, 'watch_for_user_activity'):
                setattr(app, 'watch_for_user_activity', True)
    
    def refresh_flight_data(self) -> None:
        """Refresh flight data (called by parent app)"""
        self.run_worker(self.load_flight_data(), exclusive=True)

    def _update_window_title(self) -> None:
        """Update the window title with fresh wind data."""
        window_title: str
        if self.disambiguator and isinstance(self.airport_icao_or_list, str):
            # For individual airports, get the full name
            full_name = self.disambiguator.get_pretty_name(self.airport_icao_or_list)
            
            # Fetch wind information using the current global wind source
            wind_info = get_wind_info(self.airport_icao_or_list, source=backend_constants.WIND_SOURCE)
            
            # Format title: "Airport Name (ICAO) - Wind XXX@Y"
            if wind_info:
                window_title = f"{full_name} ({self.airport_icao_or_list}) - Wind {wind_info}"
            else:
                window_title = f"{full_name} ({self.airport_icao_or_list})"
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
    
    def populate_tables(self) -> None:
        """Populate the departure and arrivals tables with separate ICAO and NAME columns."""
        
        # Update window title with fresh wind data
        self._update_window_title()
        
        # Initialize TableManagers if not already done
        if self.departures_manager is None:
            departures_table = self.query_one("#departures-table", SplitFlapDataTable)
            self.departures_manager = TableManager(departures_table, DEPARTURES_TABLE_CONFIG, self.departures_row_keys)
        
        if self.arrivals_manager is None:
            arrivals_table = self.query_one("#arrivals-table", SplitFlapDataTable)
            self.arrivals_manager = TableManager(arrivals_table, ARRIVALS_TABLE_CONFIG, self.arrivals_row_keys)
        
        # Transform structured data to tuple format for table display
        # departures_data is List[DepartureInfo], convert to (callsign, icao, name)
        departures_formatted = [
            (dep.callsign, dep.destination.icao_code, dep.destination.pretty_name)
            for dep in self.departures_data
        ]
        
        # arrivals_data is List[ArrivalInfo], convert to (callsign, icao, name, eta, eta_local)
        arrivals_formatted = [
            (arr.callsign, arr.origin.icao_code, arr.origin.pretty_name, arr.eta_display, arr.eta_local_time)
            for arr in self.arrivals_data
        ]
        
        # Populate tables using TableManagers
        self.departures_manager.populate(departures_formatted)
        self.arrivals_manager.populate(arrivals_formatted)
    
    def action_close_board(self) -> None:
        """Close the modal"""
        # Reset the flight board open flag and reference in the parent app
        app = self.app
        if hasattr(app, 'flight_board_open'):
            setattr(app, 'flight_board_open', False)
        if hasattr(app, 'active_flight_board'):
            setattr(app, 'active_flight_board', None)
            
        self.dismiss()