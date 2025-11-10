"""METAR and TAF Information Modal Screen"""

from textual.screen import ModalScreen
from textual.widgets import Static, Input
from textual.containers import Container
from textual.binding import Binding
from textual.app import ComposeResult

from backend import get_metar, get_taf
from ui import config


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