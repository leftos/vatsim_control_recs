import argparse
import asyncio
from datetime import datetime, timezone
from textual.app import App, ComposeResult
from textual.widgets import DataTable, TabbedContent, TabPane, Footer, Header, Input, Static
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.events import Key
from textual.worker import Worker, WorkerState
from textual.timer import Timer

# Import backend functionality
from vatsim_control_recs import analyze_flights_data # pyright: ignore[reportAttributeAccessIssue]


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
    ]
    
    def __init__(self, airport_data=None, groupings_data=None, total_flights=0, args=None):
        super().__init__()
        self.console.set_window_title("VATSIM Control Recommendations")
        self.original_airport_data = airport_data or []
        self.airport_data = airport_data or []
        self.groupings_data = groupings_data or []
        self.total_flights = total_flights
        self.args = args
        self.include_all_staffed = args.include_all_staffed if args else True
        self.search_active = False
        self.refresh_paused = False
        self.refresh_interval = args.refresh_interval if args else 5
        self.refresh_timer = None
        self.last_refresh_time = datetime.now(timezone.utc)
        self.status_update_timer = None
        self.last_activity_time = datetime.now()
        self.idle_timeout = 3  # seconds of idle time before auto-refresh resumes
        self.user_is_active = False
        self.airports_row_keys = []
        self.groupings_row_keys = []
        self.watch_for_user_activity = True  # Control whether to track user activity
        
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
        # Start auto-refresh timer
        self.refresh_timer = self.set_interval(self.refresh_interval, self.auto_refresh_callback)
        # Start UTC clock and status bar update timer (update every second)
        self.status_update_timer = self.set_interval(1, self.update_time_displays)
        # Initial update
        self.update_time_displays()
    
    def populate_tables(self) -> None:
        """Populate or refresh the datatable contents."""
        self.watch_for_user_activity = False  # Temporarily disable user activity tracking

        max_eta = self.args.max_eta_hours if self.args else 1.0
        arr_suffix = f"(<{max_eta:,.1g}h)" if max_eta != 0 else "(all)"

        # Set up airports table
        airports_table = self.query_one("#airports-table", DataTable)
        airports_table.clear(columns=True)
        airports_table.add_columns("ICAO", "TOTAL", "DEPARTING", f"ARRIVING {arr_suffix}", "NEXT ETA", "STAFFED POSITIONS")

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
            old_data: The previous data (list of tuples) - not used but kept for signature compatibility
            new_data: The new data (list of tuples)
        """        
        current_row_count = table.row_count
        new_row_count = len(new_data)
        
        # Get column keys list once
        column_keys = list(table.columns.keys())
        
        # Update existing rows in place (up to the minimum of current and new row counts)
        rows_to_update = min(current_row_count, new_row_count)
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
        """Callback for auto-refresh timer."""
        # Don't refresh if manually paused
        if self.refresh_paused:
            return
        
        # Check if user has been idle long enough
        idle_time = (datetime.now() - self.last_activity_time).total_seconds()
        if idle_time >= self.idle_timeout:
            self.user_is_active = False
            self.action_refresh()
        # If user is still active, we'll try again on the next timer tick
    
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
        except:
            pass
        
        # Update status bar with time since last refresh
        self.update_status_bar()
    
    def record_user_activity(self) -> None:
        """Record user activity to pause auto-refresh temporarily."""
        if not self.watch_for_user_activity:
            return
        
        self.last_activity_time = datetime.now()
        self.user_is_active = True
        if isinstance(self.refresh_timer, Timer):
            self.refresh_timer.reset()
    
    def update_status_bar(self) -> None:
        """Update the status bar with current state."""
        status_bar = self.query_one("#status-bar", Static)
        
        # Check if user has been idle long enough
        idle_time = (datetime.now() - self.last_activity_time).total_seconds()
        if idle_time >= self.idle_timeout:
            self.user_is_active = False
        
        # Determine pause status
        if self.refresh_paused:
            pause_status = "Paused"
        elif self.user_is_active:
            pause_status = "Auto-paused (not idle)"
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
            except:
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
            self.record_user_activity()
    
    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Handle tab changes."""
        self.record_user_activity()
    
    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Handle row navigation in tables."""
        self.record_user_activity()
    
    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle search input changes."""
        if event.input.id == "search-input":
            self.filter_airports(event.value)
            self.record_user_activity()
    
    def filter_airports(self, search_text: str) -> None:
        """Filter airports table based on search text."""
        if not search_text:
            self.airport_data = self.original_airport_data
        else:
            search_text = search_text.upper()
            self.airport_data = [
                row for row in self.original_airport_data
                if search_text in row[0].upper() or  # ICAO
                   search_text in row[5].upper()      # Staffed positions (now at index 5)
            ]
        
        self.populate_tables()


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
    parser.add_argument("--include-all-staffed", action="store_true", default=True,
                        help="Include airports with zero planes if they are staffed (default: True)")
    
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