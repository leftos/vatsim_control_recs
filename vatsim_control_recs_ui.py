import argparse
import asyncio
from datetime import datetime, timezone
from textual.app import App, ComposeResult
from textual.widgets import DataTable, TabbedContent, TabPane, Footer, Input, Static
from textual.binding import Binding
from textual.containers import Container, Vertical, Horizontal
from textual.events import Key
from textual.timer import Timer
from textual.screen import ModalScreen
import os

# Import backend functionality
from vatsim_control_recs import analyze_flights_data, get_airport_flight_details, DISAMBIGUATOR # pyright: ignore[reportAttributeAccessIssue]

# Import custom split-flap datatable
from split_flap_datatable import SplitFlapDataTable, TIME_FLAP_CHARS, NUMERIC_FLAP_CHARS

# Custom flap character sets for specific column types
ETA_FLAP_CHARS = "9876543210<hm:ADELN -"  # For NEXT ETA columns: numbers in descending order for countdown effect, <, h, m, colon, LANDED letters, space, dash
ICAO_FLAP_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"  # For ICAO codes
CALLSIGN_FLAP_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789- "  # For flight callsigns
POSITION_FLAP_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ- "  # For controller positions

# Set up debug logging to file
DEBUG_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug.log")

def debug_log(message: str):
    """Write a debug message to the log file."""
    with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        f.write(f"[{timestamp}] {message}\n")

# Clear debug log on startup
with open(DEBUG_LOG_FILE, "w", encoding="utf-8") as f:
    f.write(f"=== Debug log started at {datetime.now()} ===\n")

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
        self.is_first_load = True
        self.enable_animations = enable_animations
    
    def compose(self) -> ComposeResult:
        # Determine the window title based on whether we have a disambiguator
        if self.disambiguator and isinstance(self.airport_icao_or_list, str):
            # For individual airports, get the full name
            full_name = self.disambiguator.get_pretty_name(self.airport_icao_or_list)
            window_title = f"Flight Board - {full_name} ({self.airport_icao_or_list})"
        else:
            # For groupings or when no disambiguator is available, use the original title
            window_title = f"Flight Board - {self.title}"
            
        # Set the window title
        self.app.console.set_window_title(window_title)
        
        with Container(id="board-container"):
            yield Static(window_title, id="board-header")
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
        if isinstance(app, VATSIMControlApp):
            app.watch_for_user_activity = False
        
        try:
            # Run the blocking call in a thread pool
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                get_airport_flight_details,
                self.airport_icao_or_list,
                0,  # Always show all arrivals on the flight board
                DISAMBIGUATOR
            )
            
            if result:
                self.departures_data, self.arrivals_data = result
                self.populate_tables()
        finally:
            # Re-enable activity tracking
            if isinstance(app, VATSIMControlApp):
                app.watch_for_user_activity = True
    
    def refresh_flight_data(self) -> None:
        """Refresh flight data (called by parent app)"""
        self.run_worker(self.load_flight_data(), exclusive=True)

    def populate_tables(self) -> None:
        """Populate the departure and arrivals tables with separate ICAO and NAME columns."""
        # Sort arrivals by ETA before displaying
        def eta_sort_key(arrival_row):
            """Sort key for arrivals: LANDED at top, then by ETA (soonest first), then by flight callsign for stability"""
            flight, origin_tuple, eta, eta_local = arrival_row
            eta_str = str(eta).upper()
            flight_str = str(flight)
            
            # Put LANDED flights at the top
            if "LANDED" in eta_str:
                return (0, 0, flight_str)
            
            # Handle relative time formats with hours and/or minutes like "1H", "1H30M", "2H", "45M", "<1M"
            if "H" in eta_str or "M" in eta_str:
                try:
                    total_minutes = 0
                    
                    # Check if it starts with '<' for "less than" times
                    if eta_str.startswith("<"):
                        # <1M means less than 1 minute, treat as 0.5 minutes for sorting
                        minutes_str = eta_str.replace("<", "").replace("M", "").strip()
                        total_minutes = float(minutes_str) - 0.5  # Subtract 0.5 to sort before the actual minute
                    elif "H" in eta_str and "M" in eta_str:
                        # Format like "1H30M" or "2H15M"
                        parts = eta_str.replace("H", " ").replace("M", "").split()
                        hours = int(parts[0])
                        minutes = int(parts[1])
                        total_minutes = hours * 60 + minutes
                    elif "H" in eta_str:
                        # Format like "1H" or "2H"
                        hours = int(eta_str.replace("H", "").strip())
                        total_minutes = hours * 60
                    elif "M" in eta_str:
                        # Format like "45M" or "30M"
                        total_minutes = float(eta_str.replace("M", "").strip())
                    
                    return (1, total_minutes, flight_str)
                except (ValueError, IndexError):
                    return (2, 0, flight_str)
            
            # Handle absolute time formats like "13:04"
            if ":" in eta_str:
                try:
                    # Parse HH:MM format
                    parts = eta_str.split(":")
                    hours = int(parts[0])
                    minutes = int(parts[1])
                    total_minutes = hours * 60 + minutes
                    return (2, total_minutes, flight_str)
                except ValueError:
                    return (2, 0, flight_str)
            
            # Default: treat as lowest priority
            return (3, 0, flight_str)
        
        # Sort the arrivals data (Python's sort is stable by default)
        self.arrivals_data = sorted(self.arrivals_data, key=eta_sort_key)
        
        # Set up departures table
        departures_table = self.query_one("#departures-table", SplitFlapDataTable)
        if not departures_table.columns: # Only add columns if they don't exist
            departures_table.add_column("FLIGHT", flap_chars=CALLSIGN_FLAP_CHARS)
            departures_table.add_column("DEST", flap_chars=ICAO_FLAP_CHARS)
            departures_table.add_column("NAME")
        
        column_keys_dep = list(departures_table.columns.keys())
        
        # For first load, create rows with blank values and animate
        if self.is_first_load:
            departures_table.clear()
            self.departures_row_keys.clear()
            for flight, destination_tuple in self.departures_data:
                dest_name = destination_tuple[0]
                dest_icao = destination_tuple[1]
                # Add row with blank values, then animate to actual values
                row_key = departures_table.add_row(" " * 7, " " * 4, " " * len(str(dest_name)))
                self.departures_row_keys.append(row_key)
                departures_table.update_cell_animated(row_key, column_keys_dep[0], flight)
                departures_table.update_cell_animated(row_key, column_keys_dep[1], dest_icao)
                departures_table.update_cell_animated(row_key, column_keys_dep[2], dest_name)
        else:
            # For subsequent updates, efficiently update existing rows
            current_row_count = len(self.departures_row_keys)
            new_row_count = len(self.departures_data)
            
            # Update existing rows
            for i in range(min(current_row_count, new_row_count)):
                flight, destination_tuple = self.departures_data[i]
                dest_name = destination_tuple[0]
                dest_icao = destination_tuple[1]
                if i < len(self.departures_row_keys):
                    departures_table.update_cell_animated(self.departures_row_keys[i], column_keys_dep[0], flight)
                    departures_table.update_cell_animated(self.departures_row_keys[i], column_keys_dep[1], dest_icao)
                    departures_table.update_cell_animated(self.departures_row_keys[i], column_keys_dep[2], dest_name)
            
            # Add new rows if needed
            if new_row_count > current_row_count:
                for i in range(current_row_count, new_row_count):
                    flight, destination_tuple = self.departures_data[i]
                    dest_name = destination_tuple[0]
                    dest_icao = destination_tuple[1]
                    row_key = departures_table.add_row(" " * 7, " " * 4, " " * len(str(dest_name)))
                    self.departures_row_keys.append(row_key)
                    departures_table.update_cell_animated(row_key, column_keys_dep[0], flight)
                    departures_table.update_cell_animated(row_key, column_keys_dep[1], dest_icao)
                    departures_table.update_cell_animated(row_key, column_keys_dep[2], dest_name)
            # Remove extra rows if needed
            elif new_row_count < current_row_count:
                for _ in range(current_row_count - new_row_count):
                    if self.departures_row_keys:
                        departures_table.remove_row(self.departures_row_keys.pop())
        
        # Set up arrivals table
        arrivals_table = self.query_one("#arrivals-table", SplitFlapDataTable)
        if not arrivals_table.columns: # Only add columns if they don't exist
            arrivals_table.add_column("FLIGHT", flap_chars=CALLSIGN_FLAP_CHARS)
            arrivals_table.add_column("ORIG", flap_chars=ICAO_FLAP_CHARS)
            arrivals_table.add_column("NAME")
            arrivals_table.add_column("ETA", flap_chars=ETA_FLAP_CHARS)
            arrivals_table.add_column("ETA (LT)", flap_chars=ETA_FLAP_CHARS)
        
        column_keys_arr = list(arrivals_table.columns.keys())
        
        # For first load, create rows with blank values and animate
        if self.is_first_load:
            arrivals_table.clear()
            self.arrivals_row_keys.clear()
            for flight, origin_tuple, eta, eta_local in self.arrivals_data:
                origin_name = origin_tuple[0]
                origin_icao = origin_tuple[1]
                # Add row with blank values, then animate to actual values
                row_key = arrivals_table.add_row(" " * 7, " " * 4, " " * len(str(origin_name)), " " * 6, " " * 5)
                self.arrivals_row_keys.append(row_key)
                arrivals_table.update_cell_animated(row_key, column_keys_arr[0], flight)
                arrivals_table.update_cell_animated(row_key, column_keys_arr[1], origin_icao)
                arrivals_table.update_cell_animated(row_key, column_keys_arr[2], origin_name)
                arrivals_table.update_cell_animated(row_key, column_keys_arr[3], eta)
                arrivals_table.update_cell_animated(row_key, column_keys_arr[4], eta_local)
            
            self.is_first_load = False
        else:
            # For subsequent updates, efficiently update existing rows
            current_row_count = len(self.arrivals_row_keys)
            new_row_count = len(self.arrivals_data)
            
            # Update existing rows
            for i in range(min(current_row_count, new_row_count)):
                flight, origin_tuple, eta, eta_local = self.arrivals_data[i]
                origin_name = origin_tuple[0]
                origin_icao = origin_tuple[1]
                if i < len(self.arrivals_row_keys):
                    arrivals_table.update_cell_animated(self.arrivals_row_keys[i], column_keys_arr[0], flight)
                    arrivals_table.update_cell_animated(self.arrivals_row_keys[i], column_keys_arr[1], origin_icao)
                    arrivals_table.update_cell_animated(self.arrivals_row_keys[i], column_keys_arr[2], origin_name)
                    arrivals_table.update_cell_animated(self.arrivals_row_keys[i], column_keys_arr[3], eta)
                    arrivals_table.update_cell_animated(self.arrivals_row_keys[i], column_keys_arr[4], eta_local)
            
            # Add new rows if needed
            if new_row_count > current_row_count:
                for i in range(current_row_count, new_row_count):
                    flight, origin_tuple, eta, eta_local = self.arrivals_data[i]
                    origin_name = origin_tuple[0]
                    origin_icao = origin_tuple[1]
                    row_key = arrivals_table.add_row(" " * 7, " " * 4, " " * len(str(origin_name)), " " * 6, " " * 5)
                    self.arrivals_row_keys.append(row_key)
                    arrivals_table.update_cell_animated(row_key, column_keys_arr[0], flight)
                    arrivals_table.update_cell_animated(row_key, column_keys_arr[1], origin_icao)
                    arrivals_table.update_cell_animated(row_key, column_keys_arr[2], origin_name)
                    arrivals_table.update_cell_animated(row_key, column_keys_arr[3], eta)
                    arrivals_table.update_cell_animated(row_key, column_keys_arr[4], eta_local)
            # Remove extra rows if needed
            elif new_row_count < current_row_count:
                for _ in range(current_row_count - new_row_count):
                    if self.arrivals_row_keys:
                        arrivals_table.remove_row(self.arrivals_row_keys.pop())
    
    def action_close_board(self) -> None:
        """Close the modal"""
        # Reset the flight board open flag and reference in the parent app
        app = self.app
        if isinstance(app, VATSIMControlApp):
            app.flight_board_open = False
            app.active_flight_board = None
            
        self.dismiss()



class VATSIMControlApp(App):
    """Textual app for VATSIM Control Recommendations"""
    
    CSS = """
    #header-bar {
        height: 1;
        background: $boost;
        color: $text;
        layout: horizontal;
    }
    
    .header-title {
        width: 1fr;
        content-align: center middle;
        text-align: center;
    }
    
    .header-clocks {
        width: auto;
        content-align: right middle;
        padding-right: 2;
    }
    
    #tabs {
        height: 1fr;
    }
    
    TabbedContent {
        height: 100%;
    }
    
    TabbedContent > ContentSwitcher {
        height: 1fr;
    }
    
    DataTable {
        height: 100%;
        width: 100%;
    }
    
    TabPane {
        height: 100%;
    }
    
    #search-container {
        height: auto;
        display: none;
        padding: 1;
        background: $surface;
    }
    
    #search-container.visible {
        display: block;
    }
    
    #search-input {
        width: 100%;
    }
    
    #status-bar {
        height: 1;
        background: $surface;
        color: $text;
        padding: 0 1;
    }
    """
    
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("ctrl+r", "refresh", "Refresh", priority=True),
        Binding("ctrl+space", "toggle_pause", "Pause/Resume", priority=True),
        Binding("ctrl+f", "toggle_search", "Find", priority=True),
        Binding("escape", "cancel_search", "Cancel Search", show=False),
        Binding("enter", "open_flight_board", "Flight Board", priority=True),
    ]
    
    def __init__(self, airport_data=None, groupings_data=None, total_flights=0, args=None):
        super().__init__()
        self.console.set_window_title("VATSIM Control Recommendations")
        self.original_airport_data = airport_data or []
        self.airport_data = airport_data or []
        self.groupings_data = groupings_data or []
        self.total_flights = total_flights
        self.args = args
        self.include_all_staffed = args.include_all_staffed if args else False
        self.search_active = False
        self.refresh_paused = False
        self.refresh_interval = args.refresh_interval if args else 5
        self.refresh_timer = None
        self.last_refresh_time = datetime.now(timezone.utc)
        self.status_update_timer = None
        self.last_activity_time = datetime.now(timezone.utc)
        self.idle_timeout = 3  # seconds of idle time before auto-refresh resumes
        self.user_is_active = False
        self.airports_row_keys = []
        self.groupings_row_keys = []
        self.watch_for_user_activity = True  # Control whether to track user activity
        self.last_activity_source = ""  # Track what triggered the last activity
        self.initial_setup_complete = False  # Prevent timer resets during initial setup
        self.flight_board_open = False # Is a flight board currently open?
        self.active_flight_board = None  # Reference to the active flight board screen
        
    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        # Create custom header with clocks
        with Container(id="header-bar"):
            yield Static("VATSIM Control Recommendations", classes="header-title")
            yield Static("", classes="header-clocks")
        
        with Container(id="search-container"):
            yield Input(placeholder="Type to filter airports...", id="search-input")
        
        with TabbedContent(initial="airports", id="tabs"):
            with TabPane("Individual Airports", id="airports"):
                enable_anims = not self.args.disable_animations if self.args else True
                airports_table = SplitFlapDataTable(id="airports-table", enable_animations=enable_anims)
                airports_table.cursor_type = "row"
                yield airports_table
                
            with TabPane("Custom Groupings", id="groupings"):
                enable_anims = not self.args.disable_animations if self.args else True
                groupings_table = SplitFlapDataTable(id="groupings-table", enable_animations=enable_anims)
                groupings_table.cursor_type = "row"
                yield groupings_table
        
        yield Static("", id="status-bar")
        yield Footer()
    
    def on_mount(self) -> None:
        """Set up the datatables when the app starts."""
        self.populate_tables()
        self.update_status_bar()
        # Start auto-refresh timer - check every second
        self.refresh_timer = self.set_interval(1, self.auto_refresh_callback)
        # Start UTC clock and status bar update timer (update every second)
        self.status_update_timer = self.set_interval(1, self.update_time_displays)
        # Initial update
        self.update_time_displays()
        
        # Mark initial setup as complete after all initialization events have settled
        # This prevents automatic events (tab activation, row highlights) from resetting the timer
        self.call_after_refresh(lambda: setattr(self, 'initial_setup_complete', True))
    
    def populate_tables(self) -> None:
        """Populate or refresh the datatable contents."""
        self.watch_for_user_activity = False  # Temporarily disable user activity tracking

        max_eta = self.args.max_eta_hours if self.args else 1.0
        arr_suffix = f"(<{max_eta:,.1g}h)" if max_eta != 0 else "(all)"

        # Set up airports table
        airports_table = self.query_one("#airports-table", SplitFlapDataTable)
        
        # DIAGNOSTIC LOG
        debug_log(f"populate_tables BEFORE clear - airports_table.row_count={airports_table.row_count}, len(airports_row_keys)={len(self.airports_row_keys)}")
        
        airports_table.clear(columns=True)
        self.airports_row_keys.clear()  # Clear stale row keys when clearing table
        airports_table.add_column("ICAO", flap_chars=ICAO_FLAP_CHARS)
        airports_table.add_column("NAME")
        airports_table.add_column("TOTAL", flap_chars=NUMERIC_FLAP_CHARS)
        airports_table.add_column("DEPARTING", flap_chars=NUMERIC_FLAP_CHARS)
        airports_table.add_column(f"ARRIVING {arr_suffix}", flap_chars=NUMERIC_FLAP_CHARS)
        airports_table.add_column("NEXT ETA", flap_chars=ETA_FLAP_CHARS)
        airports_table.add_column("STAFFED POSITIONS", flap_chars=POSITION_FLAP_CHARS)

        column_keys = list(airports_table.columns.keys())
        for row_data in self.airport_data:
            # Add row with blank values, then animate to actual values
            blank_row = tuple(" " * len(str(cell)) for cell in row_data)
            row_key = airports_table.add_row(*blank_row)
            self.airports_row_keys.append(row_key)
            # Animate each cell to its target value
            for col_idx, cell_value in enumerate(row_data):
                airports_table.update_cell_animated(row_key, column_keys[col_idx], cell_value)
        
        # DIAGNOSTIC LOG
        debug_log(f"populate_tables AFTER populate - airports_table.row_count={airports_table.row_count}, len(airports_row_keys)={len(self.airports_row_keys)}")
        
        # Set up groupings table
        groupings_table = self.query_one("#groupings-table", SplitFlapDataTable)
        
        # DIAGNOSTIC LOG
        debug_log(f"populate_tables BEFORE clear - groupings_table.row_count={groupings_table.row_count}, len(groupings_row_keys)={len(self.groupings_row_keys)}")
        
        groupings_table.clear(columns=True)
        self.groupings_row_keys.clear()  # Clear stale row keys when clearing table
        groupings_table.add_column("GROUPING")
        groupings_table.add_column("TOTAL", flap_chars=NUMERIC_FLAP_CHARS)
        groupings_table.add_column("DEPARTING", flap_chars=NUMERIC_FLAP_CHARS)
        groupings_table.add_column(f"ARRIVING {arr_suffix}", flap_chars=NUMERIC_FLAP_CHARS)
        groupings_table.add_column("NEXT ETA", flap_chars=ETA_FLAP_CHARS)
        
        if self.groupings_data:
            column_keys = list(groupings_table.columns.keys())
            for row_data in self.groupings_data:
                # Add row with blank values, then animate to actual values
                blank_row = tuple(" " * len(str(cell)) for cell in row_data)
                row_key = groupings_table.add_row(*blank_row)
                self.groupings_row_keys.append(row_key)
                # Animate each cell to its target value
                for col_idx, cell_value in enumerate(row_data):
                    groupings_table.update_cell_animated(row_key, column_keys[col_idx], cell_value)
        
        # DIAGNOSTIC LOG
        debug_log(f"populate_tables AFTER populate - groupings_table.row_count={groupings_table.row_count}, len(groupings_row_keys)={len(self.groupings_row_keys)}")
                
        self.watch_for_user_activity = True  # Re-enable user activity tracking
    
    def update_table_efficiently(self, table: SplitFlapDataTable, row_keys: list, new_data: list) -> None:
        """
        Efficiently update a table by updating cells in existing rows, then adding/removing rows as needed.
        This is much faster than clearing and rebuilding the entire table.
        Uses animated cell updates for smooth transitions.
        
        Args:
            table: The SplitFlapDataTable widget to update
            row_keys: List of row keys for tracking table rows
            new_data: The new data (list of tuples)
        """
        current_row_count = table.row_count
        new_row_count = len(new_data)
        
        # DIAGNOSTIC LOG
        debug_log(f"update_table_efficiently current_row_count={current_row_count}, new_row_count={new_row_count}, len(row_keys)={len(row_keys)}")
        debug_log(f"update_table_efficiently table.columns={list(table.columns.keys())}")
        debug_log(f"update_table_efficiently table row_keys in table: {[key in table.rows for key in row_keys[:5]]}")
        
        # Get column keys list once
        column_keys = list(table.columns.keys())
        
        # Update existing rows in place (up to the minimum of current and new row counts)
        rows_to_update = min(current_row_count, new_row_count, len(row_keys))
        debug_log(f"update_table_efficiently rows_to_update={rows_to_update}")
        
        for row_index in range(rows_to_update):
            new_row_data = new_data[row_index]
            # Update each cell in the row
            for col_index, col_key in enumerate(column_keys):
                if col_index < len(new_row_data):
                    debug_log(f"update_table_efficiently Updating row {row_index}, col {col_index}: row_key={row_keys[row_index]}, col_key={col_key}, value={new_row_data[col_index]}")
                    table.update_cell_animated(row_keys[row_index], col_key, new_row_data[col_index])
        
        # If we have more new data than current rows, add the additional rows
        if new_row_count > current_row_count:
            for row_index in range(current_row_count, new_row_count):
                row_keys.append(table.add_row(*new_data[row_index]))        
        # If we have fewer new data than current rows, remove the extra rows from the end
        elif new_row_count < current_row_count:
            for _ in range(current_row_count - new_row_count):
                # Remove the last row
                last_row_index = table.row_count - 1
                if last_row_index >= 0:
                    table.remove_row(row_keys.pop())
    
    async def action_quit(self) -> None:
        """Quit the application."""
        self.exit()
    
    def auto_refresh_callback(self) -> None:
        """Callback for auto-refresh timer (called every second)."""
        # Check if at least refresh_interval seconds have passed since last refresh
        time_since_refresh = (datetime.now(timezone.utc) - self.last_refresh_time).total_seconds()
        if time_since_refresh < self.refresh_interval:
            return
        
        # Don't refresh if manually paused
        if self.refresh_paused:
            return
        
        # Check if user has been idle long enough (not auto-paused)
        idle_time = (datetime.now(timezone.utc) - self.last_activity_time).total_seconds()
        if idle_time < self.idle_timeout:
            return
        
        # All conditions met - perform refresh
        self.user_is_active = False
        self.action_refresh()
    
    def action_toggle_pause(self) -> None:
        """Toggle pause/resume auto-refresh."""
        self.refresh_paused = not self.refresh_paused
        self.update_status_bar()
    
    def format_time_since(self, seconds: int) -> str:
        """Format seconds into a human-readable time string."""
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}m {secs}s"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m"
    
    def update_time_displays(self) -> None:
        """Update clocks and status bar with time since last refresh."""
        # Update clocks (UTC and local)
        try:
            clocks = self.query_one(".header-clocks", Static)
            current_utc = datetime.now(timezone.utc)
            current_local = datetime.now()
            clocks.update(f"Local {current_local.strftime('%H:%M:%S')} | UTC {current_utc.strftime('%H:%M:%S')}")
        except Exception:
            pass
        
        # Update status bar with time since last refresh
        self.update_status_bar()
    
    def record_user_activity(self, source: str = "unknown") -> None:
        """Record user activity to pause auto-refresh temporarily."""
        # Ignore activity during initial setup to prevent automatic events from resetting timer
        if not self.watch_for_user_activity or not self.initial_setup_complete:
            return
        
        self.last_activity_time = datetime.now(timezone.utc)
        self.last_activity_source = source
        self.user_is_active = True
    
    def update_status_bar(self) -> None:
        """Update the status bar with current state."""
        status_bar = self.query_one("#status-bar", Static)
        
        # Check if user has been idle long enough
        idle_time = (datetime.now(timezone.utc) - self.last_activity_time).total_seconds()
        if idle_time >= self.idle_timeout:
            self.user_is_active = False
        
        # Determine pause status
        if self.refresh_paused:
            pause_status = "Paused"
        elif self.user_is_active:
            pause_status = f"Auto-paused (not idle) - triggered by: {self.last_activity_source}"
        else:
            pause_status = f"Active ({self.refresh_interval}s)"
        
        groupings_count = len(self.groupings_data) if self.groupings_data else 0
        
        # Calculate time since last refresh
        time_since_refresh = int((datetime.now(timezone.utc) - self.last_refresh_time).total_seconds())
        time_str = self.format_time_since(time_since_refresh)
        
        status_bar.update(f"Auto-refresh: {pause_status} | Last refresh: {time_str} ago | {len(self.airport_data)} airports, {groupings_count} groupings")
    
    async def fetch_data_async(self):
        """Asynchronously fetch data from VATSIM."""
        # Run the blocking call in a thread pool to avoid blocking the UI
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            analyze_flights_data,
            self.args.max_eta_hours if self.args else 1.0,
            self.args.airports if self.args else None,
            self.args.groupings if self.args else None,
            self.args.supergroupings if self.args else None,
            self.include_all_staffed
        )
        return result
    
    def action_refresh(self) -> None:
        """Refresh the data from VATSIM asynchronously."""
        # Update last refresh time
        self.last_refresh_time = datetime.now(timezone.utc)
        
        # Also trigger refresh on the flight board if it's open
        if self.flight_board_open and self.active_flight_board:
            self.active_flight_board.refresh_flight_data()
                
        # Store old data and search state for efficient updates
        old_airport_data = self.airport_data.copy()
        old_groupings_data = self.groupings_data.copy()
        old_search_active = self.search_active
        
        # Get current tab and cursor position
        tabs = self.query_one("#tabs", TabbedContent)
        current_tab = tabs.active
        
        # Save cursor position and scroll offset before refresh
        saved_airport_icao = None
        saved_grouping_name = None
        saved_row_index = 0
        saved_scroll_offset = 0
        
        if current_tab == "airports":
            airports_table = self.query_one("#airports-table", SplitFlapDataTable)
            if airports_table.cursor_row is not None and airports_table.cursor_row < len(self.airport_data):
                saved_row_index = airports_table.cursor_row
                saved_airport_icao = self.airport_data[airports_table.cursor_row][0]  # ICAO is first column
                # Save current scroll offset
                saved_scroll_offset = airports_table.scroll_offset.y
        elif current_tab == "groupings":
            groupings_table = self.query_one("#groupings-table", SplitFlapDataTable)
            if self.groupings_data and groupings_table.cursor_row is not None and groupings_table.cursor_row < len(self.groupings_data):
                saved_row_index = groupings_table.cursor_row
                saved_grouping_name = self.groupings_data[groupings_table.cursor_row][0]  # Name is first column
                saved_scroll_offset = groupings_table.scroll_offset.y
        
        # Start async data fetch
        self.run_worker(self.refresh_worker(
            old_airport_data,
            old_groupings_data,
            old_search_active,
            saved_airport_icao,
            saved_grouping_name,
            saved_row_index,
            saved_scroll_offset,
            current_tab
        ), exclusive=True)
    
    async def refresh_worker(
        self,
        old_airport_data,
        old_groupings_data,
        old_search_active,
        saved_airport_icao,
        saved_grouping_name,
        saved_row_index,
        saved_scroll_offset,
        current_tab
    ):
        """Worker to fetch data asynchronously and update tables efficiently."""
        # Fetch fresh data
        airport_data, groupings_data, total_flights = await self.fetch_data_async()
        
        if airport_data is not None:
            self.watch_for_user_activity = False  # Temporarily disable user activity tracking
            
            self.original_airport_data = airport_data
            self.airport_data = airport_data
            self.groupings_data = groupings_data or []
            self.total_flights = total_flights or 0
            
            # Efficiently update tables instead of rebuilding
            airports_table = self.query_one("#airports-table", SplitFlapDataTable)
            groupings_table = self.query_one("#groupings-table", SplitFlapDataTable)
            
            # If search is active, reapply the filter to the new data before updating table
            if self.search_active:
                search_input = self.query_one("#search-input", Input)
                search_text = search_input.value
                if search_text:
                    search_text = search_text.upper()
                    self.airport_data = [
                        row for row in self.original_airport_data
                        if search_text in row[0].upper() or  # ICAO
                           search_text in row[1].upper() or  # Airport name
                           search_text in row[6].upper()      # Staffed positions
                    ]
                else:
                    self.airport_data = self.original_airport_data
            
            # Update airports table efficiently
            if old_airport_data and len(old_airport_data) > 0:
                self.update_table_efficiently(airports_table, self.airports_row_keys, self.airport_data)
            else:
                # First time or empty, populate with animation
                airports_table.clear()
                self.airports_row_keys.clear()  # Clear stale row keys when clearing table
                column_keys = list(airports_table.columns.keys())
                for row_data in self.airport_data:
                    # Add row with blank values, then animate to actual values
                    blank_row = tuple(" " * len(str(cell)) for cell in row_data)
                    row_key = airports_table.add_row(*blank_row)
                    self.airports_row_keys.append(row_key)
                    # Animate each cell to its target value
                    for col_idx, cell_value in enumerate(row_data):
                        airports_table.update_cell_animated(row_key, column_keys[col_idx], cell_value)
                
            # Update groupings table efficiently
            if self.groupings_data and len(self.groupings_data) > 0:
                if old_groupings_data and len(old_groupings_data) > 0:
                    # DIAGNOSTIC LOG
                    debug_log(f"refresh_worker BEFORE efficient update - groupings_table.row_count={groupings_table.row_count}, len(groupings_row_keys)={len(self.groupings_row_keys)}, len(groupings_data)={len(self.groupings_data)}")
                    
                    self.update_table_efficiently(groupings_table, self.groupings_row_keys, self.groupings_data)
                else:
                    # DIAGNOSTIC LOG
                    debug_log(f"refresh_worker BEFORE rebuild - groupings_table.row_count={groupings_table.row_count}, len(groupings_row_keys)={len(self.groupings_row_keys)}")
                    
                    groupings_table.clear()
                    self.groupings_row_keys.clear()  # Clear stale row keys when clearing table
                    column_keys = list(groupings_table.columns.keys())
                    for row_data in self.groupings_data:
                        # Add row with blank values, then animate to actual values
                        blank_row = tuple(" " * len(str(cell)) for cell in row_data)
                        row_key = groupings_table.add_row(*blank_row)
                        self.groupings_row_keys.append(row_key)
                        # Animate each cell to its target value
                        for col_idx, cell_value in enumerate(row_data):
                            groupings_table.update_cell_animated(row_key, column_keys[col_idx], cell_value)
                    
                    # DIAGNOSTIC LOG
                    debug_log(f"refresh_worker AFTER rebuild - groupings_table.row_count={groupings_table.row_count}, len(groupings_row_keys)={len(self.groupings_row_keys)}")
            
            # Restore cursor position and scroll offset
            if current_tab == "airports":
                airports_table = self.query_one("#airports-table", SplitFlapDataTable)
                old_row_index = saved_row_index
                # Try to find the same airport by ICAO
                new_row_index = saved_row_index
                if saved_airport_icao:
                    for i, row in enumerate(self.airport_data):
                        if row[0] == saved_airport_icao:
                            new_row_index = i
                            break
                # Ensure row index is within bounds
                if new_row_index >= len(self.airport_data):
                    new_row_index = max(0, len(self.airport_data) - 1)
                
                if len(self.airport_data) > 0:
                    # Calculate the scroll adjustment to maintain visual position
                    row_diff = new_row_index - old_row_index
                    # Move cursor to the new row
                    airports_table.move_cursor(row=new_row_index)
                    # Restore scroll position with adjustment for row index change
                    airports_table.scroll_to(y=saved_scroll_offset + row_diff, animate=False)
                    self.watch_for_user_activity = True  # Re-enable user activity tracking
                    
            elif current_tab == "groupings" and self.groupings_data:
                groupings_table = self.query_one("#groupings-table", SplitFlapDataTable)
                old_row_index = saved_row_index
                # Try to find the same grouping by name
                new_row_index = saved_row_index
                if saved_grouping_name:
                    for i, row in enumerate(self.groupings_data):
                        if row[0] == saved_grouping_name:
                            new_row_index = i
                            break
                # Ensure row index is within bounds
                if new_row_index >= len(self.groupings_data):
                    new_row_index = max(0, len(self.groupings_data) - 1)
                
                if len(self.groupings_data) > 0:
                    # Calculate the scroll adjustment
                    row_diff = new_row_index - old_row_index
                    self.watch_for_user_activity = False  # Temporarily disable user activity tracking
                    # Move cursor to the new row
                    groupings_table.move_cursor(row=new_row_index)
                    # Restore scroll position with adjustment
                    groupings_table.scroll_to(y=saved_scroll_offset + row_diff, animate=False)
                    self.watch_for_user_activity = True  # Re-enable user activity tracking
            
            self.update_status_bar()
            
            self.watch_for_user_activity = True  # Re-enable user activity tracking
        else:
            try:
                status_bar = self.query_one("#status-bar", Static)
                refresh_status = "PAUSED - " if self.refresh_paused else ""
                status_bar.update(f"{refresh_status}Failed to refresh data from VATSIM")
            except Exception:
                pass
    
    def action_toggle_search(self) -> None:
        """Toggle the search box visibility."""
        # Only allow search on the airports tab
        tabs = self.query_one("#tabs", TabbedContent)
        if tabs.active != "airports":
            return
        
        search_container = self.query_one("#search-container")
        search_input = self.query_one("#search-input", Input)
        
        if self.search_active:
            # Hide search and reset filter
            search_container.remove_class("visible")
            search_input.value = ""
            self.search_active = False
            self.airport_data = self.original_airport_data
            self.populate_tables()
            
            # Return focus to table
            airports_table = self.query_one("#airports-table", SplitFlapDataTable)
            airports_table.focus()
        else:
            # Show search and focus input
            search_container.add_class("visible")
            self.search_active = True
            search_input.focus()
    
    def action_cancel_search(self) -> None:
        """Cancel search and hide search box."""
        if self.search_active:
            self.action_toggle_search()
    
    def on_key(self, event: Key) -> None:
        """Handle key events to detect user activity."""
        # Record activity for navigation keys
        if event.key in ["up", "down", "left", "right", "pageup", "pagedown", "home", "end"]:
            self.record_user_activity(f"key:{event.key}")
    
    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Handle tab changes."""
        self.record_user_activity(f"tab_change:{event.tab.id}")
    
    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Handle row navigation in tables."""
        # Don't record activity for automatic row highlights (e.g., on app init)
        # Only key presses (handled by on_key) will record user activity
        pass
    
    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle search input changes."""
        if event.input.id == "search-input" and self.search_active:
            self.filter_airports(event.value)
            self.record_user_activity("search_input")
    
    def filter_airports(self, search_text: str) -> None:
        """Filter airports table based on search text."""
        if not search_text:
            self.airport_data = self.original_airport_data
        else:
            search_text = search_text.upper()
            self.airport_data = [
                row for row in self.original_airport_data
                if search_text in row[0].upper() or  # ICAO
                   search_text in row[1].upper() or  # Airport name (now at index 1)
                   search_text in row[6].upper()      # Staffed positions (now at index 6)
            ]
        
        self.populate_tables()
    
    def action_open_flight_board(self) -> None:
        """Open the flight board for the selected airport or grouping"""
        # Don't allow opening flight board during search, or if a flight board is already open
        if self.search_active or self.flight_board_open:
            return
        
        tabs = self.query_one("#tabs", TabbedContent)
        current_tab = tabs.active
        
        if current_tab == "airports":
            airports_table = self.query_one("#airports-table", SplitFlapDataTable)
            if airports_table.cursor_row is not None and airports_table.cursor_row < len(self.airport_data):
                # Get the ICAO code from the selected row
                icao = self.airport_data[airports_table.cursor_row][0]
                title = icao
                full_name = DISAMBIGUATOR.get_pretty_name(icao) if DISAMBIGUATOR else icao
                 
                # Open the flight board and store reference
                self.flight_board_open = True
                enable_anims = not self.args.disable_animations if self.args else True
                flight_board = FlightBoardScreen(title, icao, self.args.max_eta_hours if self.args else 1.0, self.refresh_interval, DISAMBIGUATOR, enable_anims)
                self.active_flight_board = flight_board
                self.push_screen(flight_board)
        
        elif current_tab == "groupings":
            groupings_table = self.query_one("#groupings-table", SplitFlapDataTable)
            if self.groupings_data and groupings_table.cursor_row is not None and groupings_table.cursor_row < len(self.groupings_data):
                # Get the grouping name from the selected row
                grouping_name = self.groupings_data[groupings_table.cursor_row][0]
                
                # Get the list of airports in this grouping
                # We need to load the custom groupings file again to get the airport list
                import json
                import os
                script_dir = os.path.dirname(os.path.abspath(__file__))
                try:
                    with open(os.path.join(script_dir, 'custom_groupings.json'), 'r', encoding='utf-8') as f:
                        all_groupings = json.load(f)
                        if grouping_name in all_groupings:
                            airport_list = all_groupings[grouping_name]
                            title = grouping_name
                             
                            # Open the flight board and store reference
                            self.flight_board_open = True
                            enable_anims = not self.args.disable_animations if self.args else True
                            flight_board = FlightBoardScreen(title, airport_list, self.args.max_eta_hours if self.args else 1.0, self.refresh_interval, DISAMBIGUATOR, enable_anims)
                            self.active_flight_board = flight_board
                            self.push_screen(flight_board)
                except (FileNotFoundError, json.JSONDecodeError):
                    pass


def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description="Analyze VATSIM flight data and controller staffing")
    parser.add_argument("--max-eta-hours", type=float, default=1.0,
                        help="Maximum ETA in hours for arrival filter (default: 1.0)")
    parser.add_argument("--refresh-interval", type=int, default=15,
                        help="Auto-refresh interval in seconds (default: 15)")
    parser.add_argument("--airports", nargs="+",
                        help="List of airport ICAO codes to include in analysis (default: all)")
    parser.add_argument("--groupings", nargs="+",
                        help="List of custom grouping names to include in analysis (default: all)")
    parser.add_argument("--supergroupings", nargs="+",
                        help="List of custom grouping names to use as supergroupings. This will include all airports in these supergroupings and any detected sub-groupings.")
    parser.add_argument("--include-all-staffed", action="store_true",
                        help="Include airports with zero planes if they are staffed (default: False)")
    parser.add_argument("--disable-animations", action="store_true",
                        help="Disable split-flap animations for instant text updates (default: False)")
    
    # Parse arguments
    args = parser.parse_args()
    
    print("Loading VATSIM data...")
    
    # Get the data
    airport_data, groupings_data, total_flights = analyze_flights_data(
        max_eta_hours=args.max_eta_hours,
        airport_allowlist=args.airports,
        groupings_allowlist=args.groupings,
        supergroupings_allowlist=args.supergroupings,
        include_all_staffed=args.include_all_staffed
    )
    
    if airport_data is None:
        print("Failed to download VATSIM data")
        return
    
    # Run the Textual app
    app = VATSIMControlApp(airport_data, groupings_data, total_flights or 0, args)
    app.run()


if __name__ == "__main__":
    main()