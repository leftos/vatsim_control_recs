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

# Import backend functionality
from vatsim_control_recs import analyze_flights_data, get_airport_flight_details, DISAMBIGUATOR # pyright: ignore[reportAttributeAccessIssue]

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
        width: 1fr;
        height: 100%;
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
    
    def __init__(self, title: str, airport_icao_or_list, max_eta_hours: float, refresh_interval: int = 15, disambiguator=None):
        super().__init__()
        self.title = title
        self.airport_icao_or_list = airport_icao_or_list
        self.max_eta_hours = max_eta_hours
        self.disambiguator = disambiguator
        self.departures_data = []
        self.arrivals_data = []
        self.refresh_interval = refresh_interval
        self.refresh_timer = None
        self.display_toggle_timer = None
        self.show_icao = False
    
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
                with Vertical(classes="board-section"):
                    yield Static("DEPARTURES", classes="section-title")
                    departures_table = DataTable(classes="board-table", id="departures-table")
                    departures_table.cursor_type = "row"
                    yield departures_table
                with Vertical(classes="board-section"):
                    yield Static("ARRIVALS", classes="section-title")
                    arrivals_table = DataTable(classes="board-table", id="arrivals-table")
                    arrivals_table.cursor_type = "row"
                    yield arrivals_table
    
    async def on_mount(self) -> None:
        """Load and display flight data when the screen is mounted"""
        await self.load_flight_data()
        # Start auto-refresh timer
        self.refresh_timer = self.set_interval(self.refresh_interval, self.refresh_flight_data)
        # Start the 3-second display toggle timer
        self.display_toggle_timer = self.set_interval(3, self.toggle_display)
    
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
        """Refresh flight data (called by timer)"""
        self.run_worker(self.load_flight_data(), exclusive=True)
    
    def toggle_display(self) -> None:
        """Toggle the display between ICAO and pretty name."""
        self.show_icao = not self.show_icao
        self.populate_tables() # Re-populate tables with the new display style

    def populate_tables(self) -> None:
        """Populate the departure and arrivals tables based on the current display toggle."""
        # Set up departures table
        departures_table = self.query_one("#departures-table", DataTable)
        if not departures_table.columns: # Only add columns if they don't exist
            departures_table.add_columns("FLIGHT", "DESTINATION")
        
        departures_table.clear()
        for flight, destination_tuple in self.departures_data:
            display_name = destination_tuple[1] if self.show_icao else destination_tuple[0]
            departures_table.add_row(flight, display_name)
        
        # Set up arrivals table
        arrivals_table = self.query_one("#arrivals-table", DataTable)
        if not arrivals_table.columns: # Only add columns if they don't exist
            arrivals_table.add_columns("FLIGHT", "ORIGIN", "ETA", "ETA (Local)")
        
        arrivals_table.clear()
        for flight, origin_tuple, eta, eta_local in self.arrivals_data:
            display_name = origin_tuple[1] if self.show_icao else origin_tuple[0]
            arrivals_table.add_row(flight, display_name, eta, eta_local)
    
    def action_close_board(self) -> None:
        """Close the modal and stop refresh timer"""
        if self.refresh_timer:
            self.refresh_timer.stop()
            if self.display_toggle_timer:
                self.display_toggle_timer.stop()
        
        # Reset the flight board open flag in the parent app
        app = self.app
        if isinstance(app, VATSIMControlApp):
            app.flight_board_open = False
            
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
                airports_table = DataTable(id="airports-table")
                airports_table.cursor_type = "row"
                yield airports_table
                
            with TabPane("Custom Groupings", id="groupings"):
                groupings_table = DataTable(id="groupings-table")
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
        airports_table = self.query_one("#airports-table", DataTable)
        airports_table.clear(columns=True)
        airports_table.add_columns("ICAO", "NAME", "TOTAL", "DEPARTING", f"ARRIVING {arr_suffix}", "NEXT ETA", "STAFFED POSITIONS")

        for row_data in self.airport_data:
            self.airports_row_keys.append(airports_table.add_row(*row_data))
        
        # Set up groupings table
        groupings_table = self.query_one("#groupings-table", DataTable)
        groupings_table.clear(columns=True)
        groupings_table.add_columns("GROUPING", "TOTAL", "DEPARTING", f"ARRIVING {arr_suffix}", "NEXT ETA")
        
        if self.groupings_data:
            for row_data in self.groupings_data:
                self.groupings_row_keys.append(groupings_table.add_row(*row_data))
                
        self.watch_for_user_activity = True  # Re-enable user activity tracking
    
    def update_table_efficiently(self, table: DataTable, row_keys: list, new_data: list) -> None:
        """
        Efficiently update a table by updating cells in existing rows, then adding/removing rows as needed.
        This is much faster than clearing and rebuilding the entire table.
        
        Args:
            table: The DataTable widget to update
            row_keys: List of row keys for tracking table rows
            new_data: The new data (list of tuples)
        """
        current_row_count = table.row_count
        new_row_count = len(new_data)
        
        # Get column keys list once
        column_keys = list(table.columns.keys())
        
        # Update existing rows in place (up to the minimum of current and new row counts)
        rows_to_update = min(current_row_count, new_row_count, len(row_keys))
        for row_index in range(rows_to_update):
            new_row_data = new_data[row_index]
            # Update each cell in the row
            for col_index, col_key in enumerate(column_keys):
                if col_index < len(new_row_data):
                    table.update_cell(row_keys[row_index], col_key, new_row_data[col_index])
        
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
                
        # Store old data for efficient updates
        old_airport_data = self.airport_data.copy()
        old_groupings_data = self.groupings_data.copy()
        
        # Get current tab and cursor position
        tabs = self.query_one("#tabs", TabbedContent)
        current_tab = tabs.active
        
        # Save cursor position and scroll offset before refresh
        saved_airport_icao = None
        saved_grouping_name = None
        saved_row_index = 0
        saved_scroll_offset = 0
        
        if current_tab == "airports":
            airports_table = self.query_one("#airports-table", DataTable)
            if airports_table.cursor_row is not None and airports_table.cursor_row < len(self.airport_data):
                saved_row_index = airports_table.cursor_row
                saved_airport_icao = self.airport_data[airports_table.cursor_row][0]  # ICAO is first column
                # Save current scroll offset
                saved_scroll_offset = airports_table.scroll_offset.y
        elif current_tab == "groupings":
            groupings_table = self.query_one("#groupings-table", DataTable)
            if self.groupings_data and groupings_table.cursor_row is not None and groupings_table.cursor_row < len(self.groupings_data):
                saved_row_index = groupings_table.cursor_row
                saved_grouping_name = self.groupings_data[groupings_table.cursor_row][0]  # Name is first column
                saved_scroll_offset = groupings_table.scroll_offset.y
        
        # Start async data fetch
        self.run_worker(self.refresh_worker(
            old_airport_data,
            old_groupings_data,
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
            
            # If search is active, reapply filter
            if self.search_active:
                search_input = self.query_one("#search-input", Input)
                self.filter_airports(search_input.value)
            else:
                # Efficiently update tables instead of rebuilding
                airports_table = self.query_one("#airports-table", DataTable)
                groupings_table = self.query_one("#groupings-table", DataTable)
                
                # Update airports table efficiently
                if old_airport_data and len(old_airport_data) > 0:
                    self.update_table_efficiently(airports_table, self.airports_row_keys, self.airport_data)
                else:
                    # First time or empty, just populate normally
                    airports_table.clear()
                    for row_data in self.airport_data:
                        self.airports_row_keys.append(airports_table.add_row(*row_data))
                
                # Update groupings table efficiently
                if self.groupings_data and len(self.groupings_data) > 0:
                    if old_groupings_data and len(old_groupings_data) > 0:
                        self.update_table_efficiently(groupings_table, self.groupings_row_keys, self.groupings_data)
                    else:
                        groupings_table.clear()
                        for row_data in self.groupings_data:
                            self.groupings_row_keys.append(groupings_table.add_row(*row_data))
            
            # Restore cursor position and scroll offset
            if current_tab == "airports":
                airports_table = self.query_one("#airports-table", DataTable)
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
                groupings_table = self.query_one("#groupings-table", DataTable)
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
            airports_table = self.query_one("#airports-table", DataTable)
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
        if event.input.id == "search-input":
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
            airports_table = self.query_one("#airports-table", DataTable)
            if airports_table.cursor_row is not None and airports_table.cursor_row < len(self.airport_data):
                # Get the ICAO code from the selected row
                icao = self.airport_data[airports_table.cursor_row][0]
                title = icao
                full_name = DISAMBIGUATOR.get_pretty_name(icao) if DISAMBIGUATOR else icao
                 
                # Open the flight board
                self.flight_board_open = True
                self.push_screen(FlightBoardScreen(title, icao, self.args.max_eta_hours if self.args else 1.0, self.refresh_interval, DISAMBIGUATOR))
        
        elif current_tab == "groupings":
            groupings_table = self.query_one("#groupings-table", DataTable)
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
                             
                            # Open the flight board
                            self.flight_board_open = True
                            self.push_screen(FlightBoardScreen(title, airport_list, self.args.max_eta_hours if self.args else 1.0, self.refresh_interval, DISAMBIGUATOR))
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