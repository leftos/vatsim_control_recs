"""
Main Application Module
Contains the VATSIMControlApp Textual application class
"""

import asyncio
import os
import sys
import threading
from datetime import datetime, timezone
from typing import List, Any, Tuple
from textual.app import App, ComposeResult
from textual.widgets import DataTable, TabbedContent, TabPane, Footer, Input, Static
from textual.binding import Binding
from textual.containers import Container
from textual.events import Key

from backend import analyze_flights_data
from backend.core.groupings import load_all_groupings

from widgets.split_flap_datatable import SplitFlapDataTable
from .tables import TableManager, create_airports_table_config, create_groupings_table_config
from .modals import WindInfoScreen, MetarInfoScreen, FlightBoardScreen, TrackedAirportsModal, FlightLookupScreen, GoToScreen, VfrAlternativesScreen, HistoricalStatsScreen, HelpScreen, CommandPaletteScreen, FlightInfoScreen, DiversionModal, WeatherBriefingScreen


def set_terminal_title(title: str) -> None:
    """
    Set the terminal window/tab title using ANSI escape sequences.
    This works in most modern terminals including Windows Terminal, PowerShell, and WSL.
    """
    # Try multiple methods since Textual may capture stdout

    # Method 1: Write to stderr (less likely to be captured)
    try:
        sys.stderr.write(f"\033]0;{title}\007")
        sys.stderr.flush()
    except (OSError, IOError, AttributeError):
        pass  # Terminal may not support escape sequences

    # Method 2: Write directly to the terminal file descriptor
    try:
        # Get the actual terminal file descriptor
        if hasattr(sys.stdout, 'buffer'):
            # Use the underlying buffer
            os.write(sys.stdout.fileno(), f"\033]0;{title}\007".encode())
        else:
            os.write(1, f"\033]0;{title}\007".encode())
    except (OSError, IOError, AttributeError, ValueError):
        pass  # Terminal may not support escape sequences or stdout may be redirected

    # Method 3: Try the ST terminator instead of BEL
    try:
        sys.stdout.write(f"\033]2;{title}\033\\")
        sys.stdout.flush()
    except (OSError, IOError, AttributeError):
        pass  # Terminal may not support escape sequences


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
        # Visible in footer (compact labels)
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("ctrl+r", "refresh", "Refresh", priority=True),
        Binding("f1", "show_help", "Help", priority=True),
        Binding("f2", "show_command_palette", "Cmds", priority=True),
        # Hidden from footer (accessed via shortcuts or command palette)
        Binding("?", "show_help", "Help", show=False, priority=True),
        Binding("ctrl+p", "toggle_pause", "Pause", show=False, priority=True),
        Binding("ctrl+f", "toggle_search", "Find", show=False, priority=True),
        Binding("ctrl+g", "show_goto", "Go To", show=False, priority=True),
        Binding("ctrl+l", "show_goto", "Go To", show=False, priority=True),
        Binding("ctrl+w", "show_wind_lookup", "Wind Lkp", show=False, priority=True),
        Binding("ctrl+e", "show_metar_lookup", "METAR Lkp", show=False, priority=True),
        Binding("ctrl+a", "show_vfr_alternatives", "VFR Alts", show=False, priority=True),
        Binding("ctrl+t", "show_airport_tracking", "Tracked", show=False, priority=True),
        Binding("ctrl+s", "show_historical_stats", "Hist Stats", show=False, priority=True),
        Binding("ctrl+b", "show_weather_briefing", "Wx Brief", show=False, priority=True),
        Binding("escape", "cancel_search", "Cancel", show=False),
    ]
    
    def __init__(self, airport_data=None, groupings_data=None, total_flights=0, args=None, airport_allowlist=None):
        super().__init__()
        self.title = "VATSIM Control Recommendations"
        self.original_airport_data: List[Any] = list(airport_data) if airport_data else []
        self.airport_data: List[Any] = list(airport_data) if airport_data else []
        self.groupings_data: List[Any] = list(groupings_data) if groupings_data else []
        self.total_flights = total_flights
        self.args = args
        self.airport_allowlist = list(airport_allowlist) if airport_allowlist else []  # Store the expanded airport allowlist
        self.include_all_staffed = args.include_all_staffed if args else False
        self.hide_wind = args.hide_wind if args else False
        self.include_all_arriving = args.include_all_arriving if args else False
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
        self._activity_lock = threading.Lock()  # Lock for synchronizing watch_for_user_activity
        self.last_activity_source = ""  # Track what triggered the last activity
        self.initial_setup_complete = False  # Prevent timer resets during initial setup
        self.flight_board_open = False # Is a flight board currently open?
        self.active_flight_board = None  # Reference to the active flight board screen
        # TableManagers will be initialized after tables are created
        self.airports_manager = None
        self.groupings_manager = None
        # Cached data for Go To modal (warmed up on mount, kept fresh)
        self.cached_pilots: List[dict] = []
        self.cached_groupings: dict = {}
        # Pre-built results list for Go To modal (list of (type, identifier, data) tuples)
        self.cached_goto_results: List[Tuple[str, str, Any]] = []
        self.goto_cache_ready = False
        
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
                airports_table = SplitFlapDataTable(id="airports-table", enable_animations=enable_anims, on_select=self.action_open_flight_board)
                airports_table.cursor_type = "row"
                yield airports_table

            with TabPane("Custom Groupings", id="groupings"):
                enable_anims = not self.args.disable_animations if self.args else True
                groupings_table = SplitFlapDataTable(id="groupings-table", enable_animations=enable_anims, on_select=self.action_open_flight_board)
                groupings_table.cursor_type = "row"
                yield groupings_table
        
        yield Static("", id="status-bar")
        yield Footer()
    
    def on_mount(self) -> None:
        """Set up the datatables when the app starts."""
        # Set the terminal title using Textual's driver (bypasses stdout/stderr capture)
        try:
            # Use Textual's driver to write directly to the terminal
            driver = getattr(self, '_driver', None)
            if driver is not None and hasattr(driver, 'write'):
                driver.write("\033]0;VATSIM Control Recommendations\007")
            else:
                set_terminal_title("VATSIM Control Recommendations")
        except (AttributeError, OSError, IOError):
            # Fallback to other methods if driver is unavailable or write fails
            set_terminal_title("VATSIM Control Recommendations")

        self.populate_tables()
        self.update_status_bar()
        # Start auto-refresh timer - check every second
        self.refresh_timer = self.set_interval(1, self.auto_refresh_callback)
        # Start UTC clock and status bar update timer (update every 0.2 seconds)
        self.status_update_timer = self.set_interval(0.25, self.update_time_displays)
        # Initial update
        self.update_time_displays()

        # Mark initial setup as complete after all initialization events have settled
        # This prevents automatic events (tab activation, row highlights) from resetting the timer
        self.call_after_refresh(lambda: setattr(self, 'initial_setup_complete', True))

        # Warm up caches for Go To modal in background
        self.run_worker(self._warm_up_goto_cache(), exclusive=False)

    async def _warm_up_goto_cache(self) -> None:
        """Warm up caches for Go To modal so it opens quickly."""
        from backend.data.vatsim_api import download_vatsim_data
        from . import config

        loop = asyncio.get_event_loop()

        # Load groupings in executor (file I/O - don't block event loop)
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.cached_groupings = await loop.run_in_executor(
            None,
            load_all_groupings,
            os.path.join(script_dir, 'data', 'custom_groupings.json'),
            config.UNIFIED_AIRPORT_DATA or {}
        )

        # Load pilots data (network call)
        vatsim_data = await loop.run_in_executor(None, download_vatsim_data)
        if vatsim_data:
            self.cached_pilots = vatsim_data.get('pilots', [])

        # Pre-build the Go To results list in executor (includes disambiguator warmup)
        await loop.run_in_executor(None, self._build_goto_results)

    def _build_goto_results(self) -> None:
        """Build the Go To results list (runs in thread executor)."""
        from . import config

        results: List[Tuple[str, str, Any]] = []
        tracked_airports = list(self.airport_allowlist or [])

        # Pre-warm disambiguator for all tracked airports at once (batch is more efficient)
        if config.DISAMBIGUATOR and tracked_airports:
            airport_names = config.DISAMBIGUATOR.get_full_names_batch(tracked_airports)
        else:
            airport_names = {}

        # Add airports with their pre-fetched names
        for icao in sorted(tracked_airports):
            pretty_name = airport_names.get(icao, icao)
            results.append(('airport', icao, pretty_name))

        # Add all available groupings
        for name in sorted(self.cached_groupings.keys()):
            results.append(('grouping', name, None))

        # Add flights (sorted by callsign)
        for pilot in sorted(self.cached_pilots, key=lambda p: p.get('callsign', '')):
            callsign = pilot.get('callsign', '')
            if callsign:
                results.append(('flight', callsign, pilot))

        self.cached_goto_results = results
        self.goto_cache_ready = True

    def _disable_activity_watching(self) -> None:
        """Safely disable user activity tracking with lock."""
        with self._activity_lock:
            self.watch_for_user_activity = False

    def _enable_activity_watching(self) -> None:
        """Safely enable user activity tracking with lock."""
        with self._activity_lock:
            self.watch_for_user_activity = True

    def populate_tables(self) -> None:
        """Populate or refresh the datatable contents."""
        self._disable_activity_watching()  # Temporarily disable user activity tracking

        max_eta = self.args.max_eta_hours if self.args else 1.0
        
        # Initialize or recreate TableManagers with current configuration
        airports_table = self.query_one("#airports-table", SplitFlapDataTable)
        groupings_table = self.query_one("#groupings-table", SplitFlapDataTable)
        
        # Clear tables and recreate managers (needed when columns change, e.g., on first load)
        airports_table.clear(columns=True)
        self.airports_row_keys.clear()
        
        groupings_table.clear(columns=True)
        self.groupings_row_keys.clear()
        
        # Create fresh TableManagers with current config
        airports_config = create_airports_table_config(max_eta, self.hide_wind)
        self.airports_manager = TableManager(airports_table, airports_config, self.airports_row_keys)
        
        groupings_config = create_groupings_table_config(max_eta)
        self.groupings_manager = TableManager(groupings_table, groupings_config, self.groupings_row_keys)
        
        # Populate tables using TableManagers
        self.airports_manager.populate(self.airport_data)
        
        if self.groupings_data:
            self.groupings_manager.populate(self.groupings_data)

        self._enable_activity_watching()  # Re-enable user activity tracking
    
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
        global UNIFIED_AIRPORT_DATA, DISAMBIGUATOR
        
        # Import at module level to update globals
        from . import config
        
        # Run the blocking call in a thread pool to avoid blocking the UI
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            analyze_flights_data,
            self.args.max_eta_hours if self.args else 1.0,
            self.airport_allowlist,  # Use stored airport_allowlist (includes country expansions)
            self.args.groupings if self.args else None,
            self.include_all_staffed,
            self.hide_wind,
            self.include_all_arriving,
            config.UNIFIED_AIRPORT_DATA,  # Pass existing instance or None
            config.DISAMBIGUATOR  # Pass existing instance or None
        )
        
        # Update module-level instances from the result
        if len(result) == 5:
            airport_data, groupings_data, total_flights, config.UNIFIED_AIRPORT_DATA, config.DISAMBIGUATOR = result
            return airport_data, groupings_data, total_flights
        
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
                saved_airport_icao = self.airport_data[airports_table.cursor_row].icao
                # Save current scroll offset
                saved_scroll_offset = airports_table.scroll_offset.y
        elif current_tab == "groupings":
            groupings_table = self.query_one("#groupings-table", SplitFlapDataTable)
            if self.groupings_data and groupings_table.cursor_row is not None and groupings_table.cursor_row < len(self.groupings_data):
                saved_row_index = groupings_table.cursor_row
                saved_grouping_name = self.groupings_data[groupings_table.cursor_row].name
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
        _old_airport_data,
        _old_groupings_data,
        _old_search_active,
        saved_airport_icao,
        saved_grouping_name,
        saved_row_index,
        saved_scroll_offset,
        current_tab
    ):
        """Worker to fetch data asynchronously and update tables efficiently."""
        # Fetch fresh data
        airport_data, groupings_data, total_flights = await self.fetch_data_async()

        # Update pilots cache (uses VATSIM API's internal 15s cache, so no extra network call)
        from backend.data.vatsim_api import download_vatsim_data
        loop = asyncio.get_event_loop()
        vatsim_data = await loop.run_in_executor(None, download_vatsim_data)
        if vatsim_data:
            self.cached_pilots = vatsim_data.get('pilots', [])
            # Rebuild Go To results in background to keep cache warm
            await loop.run_in_executor(None, self._build_goto_results)

        if airport_data is not None:
            self._disable_activity_watching()  # Temporarily disable user activity tracking

            self.original_airport_data = list(airport_data)
            self.airport_data = list(airport_data)
            self.groupings_data = list(groupings_data) if groupings_data else []
            self.total_flights = total_flights or 0
            
            # If search is active, reapply the filter to the new data before updating table
            if self.search_active:
                search_input = self.query_one("#search-input", Input)
                search_text = search_input.value
                if search_text:
                    search_text = search_text.upper()
                    self.airport_data = [
                        row for row in self.original_airport_data
                        if search_text in row.icao.upper() or
                           search_text in row.name.upper() or
                           search_text in row.staffed.upper()
                    ]
                else:
                    self.airport_data = self.original_airport_data
            
            # Update tables using TableManagers
            if self.airports_manager:
                self.airports_manager.populate(self.airport_data)
            
            if self.groupings_manager and self.groupings_data:
                self.groupings_manager.populate(self.groupings_data)
            
            # Restore cursor position and scroll offset
            if current_tab == "airports":
                airports_table = self.query_one("#airports-table", SplitFlapDataTable)
                old_row_index = saved_row_index
                # Try to find the same airport by ICAO
                new_row_index = saved_row_index
                if saved_airport_icao:
                    for i, row in enumerate(self.airport_data):
                        if row.icao == saved_airport_icao:
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
                    self._enable_activity_watching()  # Re-enable user activity tracking

            elif current_tab == "groupings" and self.groupings_data:
                groupings_table = self.query_one("#groupings-table", SplitFlapDataTable)
                old_row_index = saved_row_index
                # Try to find the same grouping by name
                new_row_index = saved_row_index
                if saved_grouping_name:
                    for i, row in enumerate(self.groupings_data):
                        if row.name == saved_grouping_name:
                            new_row_index = i
                            break
                # Ensure row index is within bounds
                if new_row_index >= len(self.groupings_data):
                    new_row_index = max(0, len(self.groupings_data) - 1)
                
                if len(self.groupings_data) > 0:
                    # Calculate the scroll adjustment
                    row_diff = new_row_index - old_row_index
                    self._disable_activity_watching()  # Temporarily disable user activity tracking
                    # Move cursor to the new row
                    groupings_table.move_cursor(row=new_row_index)
                    # Restore scroll position with adjustment
                    groupings_table.scroll_to(y=saved_scroll_offset + row_diff, animate=False)
                    self._enable_activity_watching()  # Re-enable user activity tracking

            self.update_status_bar()

            self._enable_activity_watching()  # Re-enable user activity tracking
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
    
    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:  # noqa: ARG002
        """Handle tab changes."""
        self.record_user_activity(f"tab_change:{event.tab.id}")
    
    def on_data_table_row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
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
                if search_text in row.icao.upper() or
                   search_text in row.name.upper() or
                   search_text in row.staffed.upper()
            ]
        
        self.populate_tables()
    
    def action_open_flight_board(self) -> None:
        """Open the flight board for the selected airport or grouping"""
        # Don't allow opening flight board during search or if a flight board is already open
        if self.search_active or self.flight_board_open:
            return
        
        tabs = self.query_one("#tabs", TabbedContent)
        current_tab = tabs.active
        
        # Import DISAMBIGUATOR from config module
        from . import config
        
        if current_tab == "airports":
            airports_table = self.query_one("#airports-table", SplitFlapDataTable)
            if airports_table.cursor_row is not None and airports_table.row_count > 0:
                try:
                    # Get the ICAO code from the backend data, but account for table sorting
                    # The table sorts by airport_grouping_sort_key, so we need to sort our data the same way
                    from .utils import airport_grouping_sort_key
                    
                    # Sort airport_data the same way the table does
                    sorted_airports = sorted(self.airport_data, key=airport_grouping_sort_key)
                    
                    # Now we can safely index with cursor_row
                    icao: str = sorted_airports[airports_table.cursor_row].icao
                    # Use the full name as the title instead of just the ICAO
                    title: str = config.DISAMBIGUATOR.get_full_name(icao) if config.DISAMBIGUATOR else icao
                     
                    # Open the flight board and store reference
                    self.flight_board_open = True
                    enable_anims = not self.args.disable_animations if self.args else True
                    flight_board = FlightBoardScreen(title, icao, self.args.max_eta_hours if self.args else 1.0, self.refresh_interval, config.DISAMBIGUATOR, enable_anims)
                    self.active_flight_board = flight_board
                    self.push_screen(flight_board)
                except (IndexError, KeyError):
                    # Silently fail if there's an issue
                    pass
        
        elif current_tab == "groupings":
            groupings_table = self.query_one("#groupings-table", SplitFlapDataTable)
            if groupings_table.cursor_row is not None and groupings_table.row_count > 0:
                try:
                    # Get the grouping name from the backend data, but account for table sorting
                    # The table sorts by airport_grouping_sort_key, so we need to sort our data the same way
                    from .utils import airport_grouping_sort_key
                    from . import config
                    
                    # Sort groupings_data the same way the table does
                    sorted_groupings = sorted(self.groupings_data, key=airport_grouping_sort_key)
                    
                    # Now we can safely index with cursor_row
                    grouping_name = sorted_groupings[groupings_table.cursor_row].name
                    
                    # Get the list of airports in this grouping
                    # Load all groupings (custom + ARTCC) to get the airport list
                    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    all_groupings = load_all_groupings(
                        os.path.join(script_dir, 'data', 'custom_groupings.json'),
                        config.UNIFIED_AIRPORT_DATA or {}
                    )
                    
                    if grouping_name in all_groupings:
                        # Recursively resolve the grouping to actual airports (handles nested groupings)
                        def resolve_grouping_recursively(gname, visited=None):
                            """Recursively resolve a grouping name to its individual airports."""
                            if visited is None:
                                visited = set()
                            
                            if gname in visited:
                                return set()
                            visited.add(gname)
                            
                            if gname not in all_groupings:
                                return set()
                            
                            airports = set()
                            items = all_groupings[gname]
                            
                            for item in items:
                                if item in all_groupings:
                                    airports.update(resolve_grouping_recursively(item, visited))
                                else:
                                    airports.add(item)
                            
                            return airports
                        
                        airport_list = list(resolve_grouping_recursively(grouping_name))
                        title = grouping_name
                         
                        # Open the flight board and store reference
                        self.flight_board_open = True
                        enable_anims = not self.args.disable_animations if self.args else True
                        flight_board = FlightBoardScreen(title, airport_list, self.args.max_eta_hours if self.args else 1.0, self.refresh_interval, config.DISAMBIGUATOR, enable_anims)
                        self.active_flight_board = flight_board
                        self.push_screen(flight_board)
                except Exception:
                    # Silently fail if there's an issue
                    pass
    
    def action_show_wind_lookup(self) -> None:
        """Show the wind information lookup modal"""
        wind_screen = WindInfoScreen()
        self.push_screen(wind_screen)
    
    def action_show_metar_lookup(self) -> None:
        """Show the METAR lookup modal.

        Pre-fills airport based on context (checked in priority order):
        1. DiversionModal: Selected diversion airport
        2. FlightInfoScreen: Departure (if on ground) or arrival (if in flight)
        3. FlightBoardScreen: Departure/arrival airport from selected row
        4. Airports tab: Currently selected airport
        """
        initial_icao = None

        # Check modal screens in priority order (most specific first)
        for screen in self.screen_stack:
            # DiversionModal: Use selected diversion airport
            if isinstance(screen, DiversionModal):
                try:
                    table = screen.query_one("#diversion-table", SplitFlapDataTable)
                    if table.cursor_row >= 0:
                        row_data = table.get_row_at(table.cursor_row)
                        # First column is a Text object with "ICAO name" format
                        airport_text = str(row_data[0])
                        # Extract ICAO (first 4 characters before space)
                        initial_icao = airport_text.split()[0].strip()
                except Exception:
                    pass
                break

            # FlightInfoScreen: Use departure (on ground at departure) or arrival (in flight/landed)
            if isinstance(screen, FlightInfoScreen):
                flight_data = screen.flight_data
                flight_plan = flight_data.get('flight_plan')
                if flight_plan:
                    # Use the screen's _get_eta_info to determine flight state:
                    # - Returns "LANDED" if on ground at arrival airport
                    # - Returns None if on ground at departure (or no valid ETA)
                    # - Returns "ETA ..." if in flight
                    eta_info = screen._get_eta_info()
                    groundspeed = flight_data.get('groundspeed', 0)

                    if eta_info == "LANDED" or (eta_info and eta_info.startswith("ETA")):
                        # Landed at arrival or in flight - use arrival airport
                        initial_icao = flight_plan.get('arrival')
                    elif groundspeed <= 40:
                        # On ground but not at arrival - use departure airport
                        initial_icao = flight_plan.get('departure')
                    else:
                        # Fallback: in flight, use arrival
                        initial_icao = flight_plan.get('arrival')
                break

            # FlightBoardScreen: Use departure/arrival from selected row
            if isinstance(screen, FlightBoardScreen):
                departures_table = screen.query_one("#departures-table", SplitFlapDataTable)
                arrivals_table = screen.query_one("#arrivals-table", SplitFlapDataTable)

                is_grouping = isinstance(screen.airport_icao_or_list, list) and len(screen.airport_icao_or_list) > 1

                # Determine which table has focus/selection
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

                if focused_table and focused_table.cursor_row >= 0:
                    if is_grouping:
                        # For groupings, column 1 has the departure/arrival airport ICAO
                        try:
                            row_data = focused_table.get_row_at(focused_table.cursor_row)
                            initial_icao = str(row_data[1]).strip()
                        except Exception:
                            pass
                    else:
                        # For single airport, the board airport is the departure/arrival airport
                        initial_icao = screen.airport_icao_or_list
                break

        # Fall back to airports tab selection if no modal selection
        if initial_icao is None:
            tabs = self.query_one("#tabs", TabbedContent)
            if tabs.active == "airports":
                airports_table = self.query_one("#airports-table", SplitFlapDataTable)
                if airports_table.cursor_row is not None and airports_table.row_count > 0:
                    try:
                        from .utils import airport_grouping_sort_key
                        sorted_airports = sorted(self.airport_data, key=airport_grouping_sort_key)
                        initial_icao = sorted_airports[airports_table.cursor_row].icao
                    except (IndexError, KeyError):
                        pass

        metar_screen = MetarInfoScreen(initial_icao=initial_icao)
        self.push_screen(metar_screen)

    def action_show_vfr_alternatives(self) -> None:
        """Show the VFR alternatives finder modal.

        If a METAR modal is open with a looked-up airport, pre-fill that airport.
        """
        initial_icao = None

        # Check if MetarInfoScreen is open and has a current airport
        for screen in self.screen_stack:
            if screen.__class__.__name__ == 'MetarInfoScreen':
                initial_icao = getattr(screen, 'current_icao', None)
                break

        vfr_screen = VfrAlternativesScreen(initial_icao=initial_icao)
        self.push_screen(vfr_screen)

    def action_show_historical_stats(self) -> None:
        """Show the historical flight statistics modal"""
        from . import config
        stats_screen = HistoricalStatsScreen(
            tracked_airports=self.airport_allowlist,
            disambiguator=config.DISAMBIGUATOR
        )
        self.push_screen(stats_screen)

    def action_show_weather_briefing(self) -> None:
        """Show the weather briefing modal.

        Auto-fills with current grouping if viewing a FlightBoardScreen for a grouping.
        Otherwise opens a grouping picker.
        """
        # Check if FlightBoardScreen is open with a grouping
        for screen in self.screen_stack:
            if isinstance(screen, FlightBoardScreen):
                if isinstance(screen.airport_icao_or_list, list) and len(screen.airport_icao_or_list) > 1:
                    # It's a grouping - open weather briefing directly
                    briefing_screen = WeatherBriefingScreen(
                        grouping_name=screen.title,
                        airports=screen.airport_icao_or_list
                    )
                    self.push_screen(briefing_screen)
                    return

        # No grouping context - show picker
        goto_screen = GoToScreen(
            filter_type="$",
            title="Select Grouping for Weather Briefing",
            callback=self._open_weather_briefing_callback
        )
        self.push_screen(goto_screen)

    def _open_weather_briefing_callback(self, result) -> None:
        """Callback from grouping picker for weather briefing."""
        if result is None:
            # User cancelled
            return
        grouping_name, airports = result
        briefing_screen = WeatherBriefingScreen(
            grouping_name=grouping_name,
            airports=airports
        )
        self.push_screen(briefing_screen)

    def action_show_airport_tracking(self) -> None:
        """Show the tracked airports manager modal"""
        from . import config
        tracking_modal = TrackedAirportsModal(self.airport_allowlist, config.DISAMBIGUATOR)
        self.push_screen(tracking_modal, callback=self.handle_tracking_result)
    
    def handle_tracking_result(self, result) -> None:
        """Handle the result from tracked airports modal"""
        if result is None:
            # User cancelled
            return

        # Result is the updated airport allowlist
        self.airport_allowlist = result

        # Clear weather caches to ensure fresh data for changed airports
        from backend.data import clear_weather_caches
        clear_weather_caches()

        # Refresh data with new configuration
        self.action_refresh()
    
    def action_show_flight_lookup(self) -> None:
        """Show the flight lookup modal"""
        flight_lookup_screen = FlightLookupScreen()
        self.push_screen(flight_lookup_screen)

    def action_show_goto(self) -> None:
        """Show the unified Go To modal for airports, groupings, and flights"""
        goto_screen = GoToScreen()
        self.push_screen(goto_screen)

    def action_show_help(self) -> None:
        """Show the help modal with keyboard shortcuts"""
        self.push_screen(HelpScreen())

    def action_show_command_palette(self) -> None:
        """Show the command palette for searchable commands"""
        self.push_screen(CommandPaletteScreen())