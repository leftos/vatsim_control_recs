"""Flight Lookup Modal Screen"""

from textual.screen import ModalScreen
from textual.widgets import Static, Input
from textual.containers import Container
from textual.binding import Binding
from textual.app import ComposeResult

from backend.data.vatsim_api import download_vatsim_data
from .flight_info import FlightInfoScreen


class FlightLookupScreen(ModalScreen):
    """Modal screen for looking up flight information by callsign"""

    CSS = """
    FlightLookupScreen {
        align: center middle;
    }
    
    #flight-lookup-container {
        width: 60;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    
    #flight-lookup-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    
    #flight-lookup-input-container {
        height: auto;
        margin-bottom: 1;
    }
    
    #flight-lookup-result {
        text-align: center;
        height: auto;
        margin-top: 1;
        color: $error;
    }
    
    #flight-lookup-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("enter", "lookup_flight", "Lookup Flight", priority=True),
    ]

    def __init__(self):
        super().__init__()
        self.vatsim_data = None

    def compose(self) -> ComposeResult:
        with Container(id="flight-lookup-container"):
            yield Static("Flight Lookup", id="flight-lookup-title")
            with Container(id="flight-lookup-input-container"):
                yield Input(
                    placeholder="Enter callsign (e.g., AAL123)",
                    id="flight-lookup-input",
                )
            yield Static("", id="flight-lookup-result")
            yield Static(
                "Press Enter to lookup, Escape to close", id="flight-lookup-hint"
            )

    def on_mount(self) -> None:
        """Focus the input and load VATSIM data when mounted"""
        flight_input = self.query_one("#flight-lookup-input", Input)
        flight_input.focus()

        # Load VATSIM data in the background
        self.run_worker(self.load_vatsim_data(), exclusive=False)

    async def load_vatsim_data(self) -> None:
        """Load VATSIM data asynchronously"""
        import asyncio

        loop = asyncio.get_event_loop()
        self.vatsim_data = await loop.run_in_executor(None, download_vatsim_data)

    def action_lookup_flight(self) -> None:
        """Lookup flight information for the entered callsign"""
        flight_input = self.query_one("#flight-lookup-input", Input)
        callsign = flight_input.value.strip().upper()

        result_widget = self.query_one("#flight-lookup-result", Static)

        if not callsign:
            result_widget.update("Please enter a callsign")
            return

        if not self.vatsim_data:
            result_widget.update("Loading VATSIM data, please wait...")
            return

        # Search for the flight in VATSIM data
        flight_data = None
        for pilot in self.vatsim_data.get("pilots", []):
            if pilot.get("callsign", "").upper() == callsign:
                flight_data = pilot
                break

        if not flight_data:
            result_widget.update(f"Flight {callsign} not found on VATSIM")
            return

        # Clear error message and open flight info modal
        result_widget.update("")
        self.app.push_screen(FlightInfoScreen(flight_data))

    def action_close(self) -> None:
        """Close the modal"""
        self.dismiss()
