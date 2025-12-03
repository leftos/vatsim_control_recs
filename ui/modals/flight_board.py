"""Flight Board Modal Screen"""

import asyncio
import os

from textual.screen import ModalScreen
from textual.widgets import Static
from textual.widgets.data_table import RowDoesNotExist
from textual.containers import Container, Vertical, Horizontal
from textual.binding import Binding
from textual.app import ComposeResult
from textual.events import Key

from backend import get_wind_info, get_altimeter_setting
from backend.core.flights import get_airport_flight_details
from backend.cache.manager import load_aircraft_approach_speeds
from backend.data.vatsim_api import download_vatsim_data
from backend.config import constants as backend_constants

from widgets.split_flap_datatable import SplitFlapDataTable
from ui import config
from ui.tables import TableManager, DEPARTURES_TABLE_CONFIG, ARRIVALS_TABLE_CONFIG, GROUPING_DEPARTURES_TABLE_CONFIG, GROUPING_ARRIVALS_TABLE_CONFIG
from ui.modals.flight_info import FlightInfoScreen


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
        Binding("enter", "show_flight_info", "Flight Info", show=True),
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
        if hasattr(app, '_disable_activity_watching'):
            app._disable_activity_watching()
        
        try:
            # Run the blocking call in a thread pool
            loop = asyncio.get_event_loop()
            # Prepare parameters for get_airport_flight_details
            aircraft_speeds = load_aircraft_approach_speeds(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'aircraft_data.csv'))
            vatsim_data = download_vatsim_data()
            
            # Store vatsim_data for flight info lookup
            self.vatsim_data = vatsim_data
            
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
            if hasattr(app, '_enable_activity_watching'):
                app._enable_activity_watching()
    
    def refresh_flight_data(self) -> None:
        """Refresh flight data (called by parent app)"""
        self.run_worker(self.load_flight_data(), exclusive=True)

    def _update_window_title(self) -> None:
        """Update the window title with fresh wind and altimeter data."""
        window_title: str
        if self.disambiguator and isinstance(self.airport_icao_or_list, str):
            # For individual airports, get the full name
            full_name = self.disambiguator.get_pretty_name(self.airport_icao_or_list)
            
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
    
    def populate_tables(self) -> None:
        """Populate the departure and arrivals tables with separate ICAO and NAME columns."""
        
        # Update window title with fresh wind data
        self._update_window_title()
        
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
    
    def action_close_board(self) -> None:
        """Close the modal"""
        # Reset the flight board open flag and reference in the parent app
        app = self.app
        if hasattr(app, 'flight_board_open'):
            setattr(app, 'flight_board_open', False)
        if hasattr(app, 'active_flight_board'):
            setattr(app, 'active_flight_board', None)
            
        self.dismiss()
    
    def on_key(self, event: Key) -> None:
        """Handle key events."""
        # Handle Enter key for opening flight info when a DataTable has focus
        if event.key == "enter":
            # Check if focused widget is one of our DataTables
            focused = self.focused
            if focused and focused.id in ["departures-table", "arrivals-table"]:
                # Let the action handle the rest of the logic
                self.action_show_flight_info()
                event.prevent_default()
                event.stop()
                return
    
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