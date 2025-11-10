"""Wind Information Modal Screen"""

from textual.screen import ModalScreen
from textual.widgets import Static, Input
from textual.containers import Container
from textual.binding import Binding
from textual.app import ComposeResult

from backend import get_wind_info
from backend.config import constants as backend_constants
from ui import config


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