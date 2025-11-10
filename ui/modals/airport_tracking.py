"""Airport Tracking Modal Screen"""

from textual.screen import ModalScreen
from textual.widgets import Static, Input
from textual.containers import Container
from textual.binding import Binding
from textual.app import ComposeResult


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