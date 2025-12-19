"""Tracked Airports Management Modal Screen"""

from textual.screen import ModalScreen
from textual.widgets import Static, ListView, ListItem, Label, Button
from textual.containers import Container, Horizontal
from textual.binding import Binding
from textual.app import ComposeResult

from .airport_tracking import AirportTrackingModal
from .save_grouping import SaveGroupingModal


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
            pretty_name = self.disambiguator.get_full_name(icao) if self.disambiguator else icao
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