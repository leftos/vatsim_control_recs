"""
Modal Screens Module
Contains all modal dialog screens (Wind, METAR, FlightBoard, Airport Tracking)
"""

import asyncio
import os
from textual.screen import ModalScreen
from textual.widgets import Static, Input, ListView, ListItem, Label, Button
from textual.containers import Container, Vertical, Horizontal
from textual.binding import Binding
from textual import events
from textual.app import ComposeResult

from backend import get_wind_info, get_metar, get_taf
from backend.core.flights import get_airport_flight_details
from backend.core.groupings import load_all_groupings
from backend.cache.manager import load_aircraft_approach_speeds
from backend.data.vatsim_api import download_vatsim_data
from backend.config import constants as backend_constants

from widgets.split_flap_datatable import SplitFlapDataTable
from . import config
from .tables import TableManager, DEPARTURES_TABLE_CONFIG, ARRIVALS_TABLE_CONFIG


class WindInfoScreen(ModalScreen):
    """Modal screen showing wind information for an airport"""
    
    CSS = """
    WindInfoScreen {
        align: center middle;
    }
    
    #wind-container {
        width: 60;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    
    #wind-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    
    #wind-input-container {
        height: auto;
        margin-bottom: 1;
    }
    
    #wind-result {
        text-align: center;
        height: auto;
        margin-top: 1;
    }
    
    #wind-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """
    
    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("enter", "fetch_wind", "Fetch Wind", priority=True),
    ]
    
    def __init__(self):
        super().__init__()
        self.wind_result = ""
    
    def compose(self) -> ComposeResult:
        with Container(id="wind-container"):
            yield Static("Wind Information Lookup", id="wind-title")
            with Container(id="wind-input-container"):
                yield Input(placeholder="Enter airport ICAO code (e.g., KSFO)", id="wind-input")
            yield Static("", id="wind-result")
            yield Static("Press Enter to fetch, Escape to close", id="wind-hint")
    
    def on_mount(self) -> None:
        """Focus the input when mounted"""
        wind_input = self.query_one("#wind-input", Input)
        wind_input.focus()
    
    def action_fetch_wind(self) -> None:
        """Fetch wind information for the entered airport"""
        wind_input = self.query_one("#wind-input", Input)
        icao = wind_input.value.strip().upper()
        
        if not icao:
            result_widget = self.query_one("#wind-result", Static)
            result_widget.update("Please enter an airport ICAO code")
            return
        
        # Fetch wind info from both sources
        wind_metar = get_wind_info(icao, source="metar")
        wind_minute = get_wind_info(icao, source="minute")
        
        result_widget = self.query_one("#wind-result", Static)
        
        # Get pretty name if available
        pretty_name = config.DISAMBIGUATOR.get_pretty_name(icao) if config.DISAMBIGUATOR else icao
        
        # Build the display string
        result_lines = [f"{pretty_name} ({icao})", ""]
        
        if wind_metar:
            result_lines.append(f"METAR: {wind_metar}")
        else:
            result_lines.append("METAR: No data available")
        
        if wind_minute:
            result_lines.append(f"Minute: {wind_minute}")
        else:
            result_lines.append("Minute: No data available")
        
        # Show which source is currently active
        active_source = "METAR" if backend_constants.WIND_SOURCE == "metar" else "Minute"
        result_lines.append(f"\nActive source: {active_source}")
        
        result_widget.update("\n".join(result_lines))
    
    def action_close(self) -> None:
        """Close the modal"""
        self.dismiss()


class MetarInfoScreen(ModalScreen):
    """Modal screen showing full METAR and TAF for an airport"""
    
    CSS = """
    MetarInfoScreen {
        align: center middle;
    }
    
    #metar-container {
        width: 80;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    
    #metar-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    
    #metar-input-container {
        height: auto;
        margin-bottom: 1;
    }
    
    #metar-result {
        text-align: left;
        height: auto;
        margin-top: 1;
        padding: 1;
    }
    
    #metar-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """
    
    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("enter", "fetch_metar", "Fetch METAR", priority=True),
    ]
    
    def __init__(self):
        super().__init__()
        self.metar_result = ""
    
    def compose(self) -> ComposeResult:
        with Container(id="metar-container"):
            yield Static("METAR & TAF Lookup", id="metar-title")
            with Container(id="metar-input-container"):
                yield Input(placeholder="Enter airport ICAO code (e.g., KSFO)", id="metar-input")
            yield Static("", id="metar-result")
            yield Static("Press Enter to fetch, Escape to close", id="metar-hint")
    
    def on_mount(self) -> None:
        """Focus the input when mounted"""
        metar_input = self.query_one("#metar-input", Input)
        metar_input.focus()
    
    def action_fetch_metar(self) -> None:
        """Fetch METAR and TAF for the entered airport"""
        metar_input = self.query_one("#metar-input", Input)
        icao = metar_input.value.strip().upper()
        
        if not icao:
            result_widget = self.query_one("#metar-result", Static)
            result_widget.update("Please enter an airport ICAO code")
            return
        
        # Fetch full METAR and TAF
        metar = get_metar(icao)
        taf = get_taf(icao)
        
        result_widget = self.query_one("#metar-result", Static)
        
        # Get pretty name if available
        pretty_name = config.DISAMBIGUATOR.get_pretty_name(icao) if config.DISAMBIGUATOR else icao
        
        # Build the display string
        result_lines = [f"{pretty_name} ({icao})", ""]
        
        # Add METAR
        if metar:
            result_lines.append(metar)
        else:
            result_lines.append("METAR: No data available")
        
        result_lines.append("")  # Add blank line between METAR and TAF
        
        # Add TAF
        if taf:
            result_lines.append(taf)
        else:
            result_lines.append("TAF: No data available")
        
        result_widget.update("\n".join(result_lines))
    
    def action_close(self) -> None:
        """Close the modal"""
        self.dismiss()


class AirportTrackingModal(ModalScreen):
    """Modal screen for adding/removing airport tracking"""
    
    CSS = """
    AirportTrackingModal {
        align: center middle;
    }
    
    #tracking-container {
        width: 80;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    
    #tracking-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    
    #tracking-input-container {
        height: auto;
        margin-bottom: 1;
    }
    
    #tracking-result {
        text-align: left;
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }
    
    #tracking-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """
    
    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("enter", "apply_tracking", "Apply", priority=True),
    ]
    
    def __init__(self):
        super().__init__()
    
    def compose(self) -> ComposeResult:
        with Container(id="tracking-container"):
            yield Static("Quick Add/Remove Airports", id="tracking-title")
            with Container(id="tracking-input-container"):
                yield Input(
                    placeholder="e.g., +KSFO +KOAK -KSJC -KMRY",
                    id="tracking-input"
                )
            yield Static("", id="tracking-result")
            yield Static(
                "Use + to add airports, - to remove airports\nPress Enter to apply, Escape to cancel",
                id="tracking-hint"
            )
    
    def on_mount(self) -> None:
        """Focus the input when mounted"""
        tracking_input = self.query_one("#tracking-input", Input)
        tracking_input.focus()
    
    def action_apply_tracking(self) -> None:
        """Parse input and update airport tracking"""
        tracking_input = self.query_one("#tracking-input", Input)
        input_text = tracking_input.value.strip()
        
        if not input_text:
            result_widget = self.query_one("#tracking-result", Static)
            result_widget.update("Please enter airport codes with + or - prefix")
            return
        
        # Parse the input
        airports_to_add = []
        airports_to_remove = []
        errors = []
        
        tokens = input_text.split()
        for token in tokens:
            token = token.strip().upper()
            if not token:
                continue
            
            if token.startswith('+'):
                icao = token[1:]
                if len(icao) == 4:  # Basic validation for ICAO codes
                    airports_to_add.append(icao)
                else:
                    errors.append(f"Invalid ICAO: {icao}")
            elif token.startswith('-'):
                icao = token[1:]
                if len(icao) == 4:
                    airports_to_remove.append(icao)
                else:
                    errors.append(f"Invalid ICAO: {icao}")
            else:
                errors.append(f"Missing +/- prefix: {token}")
        
        # Display results or errors
        result_widget = self.query_one("#tracking-result", Static)
        
        if errors:
            result_widget.update("Errors:\n" + "\n".join(errors))
            return
        
        if not airports_to_add and not airports_to_remove:
            result_widget.update("No valid airports specified")
            return
        
        # Return the modifications to the parent app
        self.dismiss((airports_to_add, airports_to_remove))
    
    def action_close(self) -> None:
        """Close the modal without applying changes"""
        self.dismiss(None)


class SaveGroupingModal(ModalScreen):
    """Modal screen for saving tracked airports as a custom grouping"""
    
    CSS = """
    SaveGroupingModal {
        align: center middle;
    }
    
    #save-grouping-container {
        width: 60;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    
    #save-grouping-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    
    #save-grouping-input-container {
        height: auto;
        margin-bottom: 1;
    }
    
    #save-grouping-result {
        text-align: center;
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }
    
    #save-grouping-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """
    
    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("enter", "save_grouping", "Save", priority=True),
    ]
    
    def __init__(self, airport_list: list):
        super().__init__()
        self.airport_list = airport_list
    
    def compose(self) -> ComposeResult:
        with Container(id="save-grouping-container"):
            yield Static("Save as Custom Grouping", id="save-grouping-title")
            with Container(id="save-grouping-input-container"):
                yield Input(
                    placeholder="Enter grouping name (e.g., My Airports)",
                    id="save-grouping-input"
                )
            yield Static("", id="save-grouping-result")
            yield Static(
                f"This will save {len(self.airport_list)} airports to custom_groupings.json\nPress Enter to save, Escape to cancel",
                id="save-grouping-hint"
            )
    
    def on_mount(self) -> None:
        """Focus the input when mounted"""
        grouping_input = self.query_one("#save-grouping-input", Input)
        grouping_input.focus()
    
    def action_save_grouping(self) -> None:
        """Save the grouping to custom_groupings.json"""
        import json
        import os
        
        grouping_input = self.query_one("#save-grouping-input", Input)
        grouping_name = grouping_input.value.strip()
        
        if not grouping_name:
            result_widget = self.query_one("#save-grouping-result", Static)
            result_widget.update("Please enter a grouping name")
            return
        
        # Get the path to custom_groupings.json
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        groupings_file = os.path.join(script_dir, 'data', 'custom_groupings.json')
        
        try:
            # Load existing groupings
            with open(groupings_file, 'r', encoding='utf-8') as f:
                groupings_data = json.load(f)
            
            # Add or update the grouping
            groupings_data[grouping_name] = sorted(self.airport_list)
            
            # Save back to file
            with open(groupings_file, 'w', encoding='utf-8') as f:
                json.dump(groupings_data, f, indent=2, ensure_ascii=False)
            
            self.dismiss(f"Saved '{grouping_name}' with {len(self.airport_list)} airports")
        except Exception as e:
            result_widget = self.query_one("#save-grouping-result", Static)
            result_widget.update(f"Error saving grouping: {str(e)}")
    
    def action_close(self) -> None:
        """Close without saving"""
        self.dismiss(None)


class TrackedAirportsModal(ModalScreen):
    """Modal screen for viewing and managing all tracked airports"""
    
    CSS = """
    TrackedAirportsModal {
        align: center middle;
    }
    
    #tracked-container {
        width: 90;
        height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    
    #tracked-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    
    #tracked-info {
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }
    
    #tracked-list {
        height: 1fr;
        border: solid $primary;
        margin-bottom: 1;
    }
    
    #tracked-buttons {
        height: auto;
        layout: horizontal;
        align: center middle;
    }
    
    .tracked-button {
        margin: 0 1;
    }
    
    #tracked-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    
    #tracked-status {
        text-align: center;
        color: $success;
        margin-top: 1;
    }
    """
    
    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("a", "add_airports", "Add Airports", priority=True),
        Binding("delete", "remove_selected", "Remove Selected", priority=True),
        Binding("s", "save_as_grouping", "Save as Grouping", priority=True),
    ]
    
    def __init__(self, airport_allowlist: list, disambiguator=None):
        super().__init__()
        self.airport_allowlist = sorted(airport_allowlist) if airport_allowlist else []
        self.disambiguator = disambiguator
        self.selected_airports = set()
    
    def compose(self) -> ComposeResult:
        tracking_mode = "Tracking specific airports" if self.airport_allowlist else "Tracking all airports with activity"
        airport_count = len(self.airport_allowlist) if self.airport_allowlist else "all"
        
        with Container(id="tracked-container"):
            yield Static("Tracked Airports Manager", id="tracked-title")
            yield Static(f"{tracking_mode} ({airport_count} airports)", id="tracked-info")
            
            list_view = ListView(id="tracked-list")
            yield list_view
            
            with Horizontal(id="tracked-buttons"):
                yield Button("Add Airports (A)", id="add-button", classes="tracked-button")
                yield Button("Remove Selected (Del)", id="remove-button", classes="tracked-button")
                yield Button("Save as Grouping (S)", id="save-button", classes="tracked-button")
                yield Button("Close (Esc)", id="close-button", classes="tracked-button")
            
            yield Static("Use arrow keys to navigate, Space to select/deselect", id="tracked-hint")
            yield Static("", id="tracked-status")
    
    def on_mount(self) -> None:
        """Populate the list when mounted"""
        self.populate_list()
        list_view = self.query_one("#tracked-list", ListView)
        list_view.focus()
    
    def populate_list(self) -> None:
        """Populate the list with tracked airports"""
        list_view = self.query_one("#tracked-list", ListView)
        list_view.clear()
        
        if not self.airport_allowlist:
            list_view.append(ListItem(Label("No specific airports tracked (showing all with activity)")))
            return
        
        for icao in self.airport_allowlist:
            pretty_name = self.disambiguator.get_pretty_name(icao) if self.disambiguator else icao
            display_text = f"{icao} - {pretty_name}"
            if icao in self.selected_airports:
                display_text = f"[✓] {display_text}"
            list_view.append(ListItem(Label(display_text), name=icao))
    
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle item selection/deselection"""
        if event.item.name:
            icao = event.item.name
            if icao in self.selected_airports:
                self.selected_airports.remove(icao)
            else:
                self.selected_airports.add(icao)
            self.populate_list()
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses"""
        if event.button.id == "add-button":
            self.action_add_airports()
        elif event.button.id == "remove-button":
            self.action_remove_selected()
        elif event.button.id == "save-button":
            self.action_save_as_grouping()
        elif event.button.id == "close-button":
            self.action_close()
    
    def action_add_airports(self) -> None:
        """Open the quick add/remove modal as a sub-modal"""
        self.app.push_screen(AirportTrackingModal(), callback=self.handle_quick_tracking_result)
    
    def handle_quick_tracking_result(self, result) -> None:
        """Handle result from quick tracking modal"""
        if result is None:
            return
        
        airports_to_add, airports_to_remove = result
        
        # Update the airport allowlist
        if airports_to_add:
            if not self.airport_allowlist:
                # If tracking all airports, we need to create an explicit allowlist
                # This is a bit tricky - we'll let the parent handle this
                pass
            else:
                for icao in airports_to_add:
                    if icao not in self.airport_allowlist:
                        self.airport_allowlist.append(icao)
                self.airport_allowlist.sort()
        
        if airports_to_remove:
            for icao in airports_to_remove:
                if icao in self.airport_allowlist:
                    self.airport_allowlist.remove(icao)
                if icao in self.selected_airports:
                    self.selected_airports.remove(icao)
        
        # Update the display
        self.populate_list()
        info_widget = self.query_one("#tracked-info", Static)
        tracking_mode = "Tracking specific airports" if self.airport_allowlist else "Tracking all airports with activity"
        airport_count = len(self.airport_allowlist) if self.airport_allowlist else "all"
        info_widget.update(f"{tracking_mode} ({airport_count} airports)")
    
    def action_remove_selected(self) -> None:
        """Remove selected airports from tracking"""
        if not self.selected_airports:
            return
        
        for icao in self.selected_airports:
            if icao in self.airport_allowlist:
                self.airport_allowlist.remove(icao)
        
        self.selected_airports.clear()
        self.populate_list()
        
        # Update the info display
        info_widget = self.query_one("#tracked-info", Static)
        tracking_mode = "Tracking specific airports" if self.airport_allowlist else "Tracking all airports with activity"
        airport_count = len(self.airport_allowlist) if self.airport_allowlist else "all"
        info_widget.update(f"{tracking_mode} ({airport_count} airports)")
    
    def action_save_as_grouping(self) -> None:
        """Open modal to save current airports as a custom grouping"""
        if not self.airport_allowlist:
            status_widget = self.query_one("#tracked-status", Static)
            status_widget.update("No airports to save (tracking all airports)")
            return
        
        save_modal = SaveGroupingModal(self.airport_allowlist)
        self.app.push_screen(save_modal, callback=self.handle_save_grouping_result)
    
    def handle_save_grouping_result(self, result) -> None:
        """Handle result from save grouping modal"""
        if result:
            status_widget = self.query_one("#tracked-status", Static)
            status_widget.update(result)
    
    def action_close(self) -> None:
        """Close and return the updated airport list"""
        self.dismiss(self.airport_allowlist)


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