"""Go To Modal Screen - Unified navigation to airports, groupings, and flights"""

import asyncio
import os
from typing import List, Tuple, Any, TYPE_CHECKING

from textual.screen import ModalScreen
from textual.widgets import Static, Input, OptionList
from textual.widgets.option_list import Option
from textual.containers import Container
from textual.binding import Binding
from textual.app import ComposeResult

from backend.data.vatsim_api import download_vatsim_data
from backend.core.groupings import load_all_groupings, resolve_grouping_recursively
from ui import config
from .flight_info import FlightInfoScreen
from .flight_board import FlightBoardScreen

if TYPE_CHECKING:
    from ui.app import VATSIMControlApp


class GoToScreen(ModalScreen):
    """Unified Go To modal for navigating to airports, groupings, and flights"""

    @property
    def vatsim_app(self) -> "VATSIMControlApp":
        """Return the app with proper type hint"""
        return self.app  # type: ignore[return-value]

    CSS = """
    GoToScreen {
        align: center middle;
    }

    #goto-container {
        width: 80;
        height: auto;
        max-height: 70%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
        overflow: hidden;
    }

    #goto-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #goto-input {
        margin-bottom: 1;
    }

    #goto-list {
        height: 1fr;
        max-height: 100%;
        overflow-y: auto;
    }

    #goto-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
    ]

    def __init__(self):
        super().__init__()
        self.tracked_airports: List[str] = []
        self.all_groupings: dict = {}  # All available groupings (name -> airports)
        self.pilots: List[dict] = []
        self.all_results: List[Tuple[str, str, Any]] = []
        self.filtered_results: List[Tuple[str, str, Any]] = []
        self.data_loaded = False
        self._load_worker = None

    def compose(self) -> ComposeResult:
        with Container(id="goto-container"):
            yield Static("Go To", id="goto-title")
            yield Input(placeholder="Search... (@airport, #flight, $grouping)", id="goto-input")
            yield OptionList(Option("Loading...", disabled=True), id="goto-list")
            yield Static("@ Airport | # Flight | $ Grouping | Enter Select | Esc Close", id="goto-hint")

    def on_mount(self) -> None:
        """Focus input and load data"""
        self.query_one("#goto-input", Input).focus()
        self._load_worker = self.run_worker(self._load_data(), exclusive=True)

    def on_unmount(self) -> None:
        """Cancel any pending workers when modal is closed"""
        if self._load_worker and not self._load_worker.is_finished:
            self._load_worker.cancel()

    async def _load_data(self) -> None:
        """Load all data sources asynchronously"""
        # Access app data
        self.tracked_airports = list(self.vatsim_app.airport_allowlist or [])

        # Load all available groupings (custom + ARTCC + preset)
        script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.all_groupings = load_all_groupings(
            os.path.join(script_dir, 'data', 'custom_groupings.json'),
            config.UNIFIED_AIRPORT_DATA or {}
        )

        # Load VATSIM data in background
        try:
            loop = asyncio.get_event_loop()
            vatsim_data = await loop.run_in_executor(None, download_vatsim_data)

            if vatsim_data:
                self.pilots = vatsim_data.get('pilots', [])
        except Exception:
            # If VATSIM data fails, continue with just airports and groupings
            pass

        # Build initial results
        self._build_all_results()
        self.filtered_results = list(self.all_results)
        self.data_loaded = True

        # Update the option list
        self._update_option_list()

    def _build_all_results(self) -> None:
        """Build the complete results list"""
        self.all_results = []

        # Add airports
        for icao in sorted(self.tracked_airports):
            pretty_name = config.DISAMBIGUATOR.get_full_name(icao) if config.DISAMBIGUATOR else icao
            self.all_results.append(('airport', icao, pretty_name))

        # Add all available groupings
        for name in sorted(self.all_groupings.keys()):
            self.all_results.append(('grouping', name, None))

        # Add flights (sorted by callsign)
        for pilot in sorted(self.pilots, key=lambda p: p.get('callsign', '')):
            callsign = pilot.get('callsign', '')
            if callsign:
                self.all_results.append(('flight', callsign, pilot))

    def _format_label(self, item_type: str, identifier: str, data: Any) -> str:
        """Format the display label for an item

        Uses filter symbols as prefixes for consistency:
        @ for airports, # for flights, $ for groupings
        """
        if item_type == 'airport':
            return f"@ {identifier} - {data}"
        elif item_type == 'grouping':
            # Show airport count for groupings to distinguish from flights
            airport_count = len(self.all_groupings.get(identifier, []))
            return f"$ {identifier} ({airport_count} airports)"
        elif item_type == 'flight':
            fp = data.get('flight_plan') or {}
            dep = fp.get('departure') or '----'
            arr = fp.get('arrival') or '----'
            return f"# {identifier} - {dep} -> {arr}"
        return identifier

    def _update_option_list(self) -> None:
        """Update the option list with current filtered results"""
        option_list = self.query_one("#goto-list", OptionList)
        option_list.clear_options()

        if not self.data_loaded:
            option_list.add_option(Option("Loading...", disabled=True))
            return

        if not self.filtered_results:
            option_list.add_option(Option("No results found", disabled=True))
            return

        # Limit to 100 results for performance
        for item_type, identifier, data in self.filtered_results[:100]:
            label = self._format_label(item_type, identifier, data)
            option_list.add_option(Option(label, id=f"{item_type}:{identifier}"))

        if option_list.option_count > 0:
            option_list.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter results as user types

        Special prefixes for type filtering:
        - @ for airports only
        - # for flights only
        - $ for groupings only
        """
        if not self.data_loaded:
            return

        query = event.value.strip()

        # Check for type filter prefix
        type_filter = None
        if query.startswith('@'):
            type_filter = 'airport'
            query = query[1:].strip()
        elif query.startswith('#'):
            type_filter = 'flight'
            query = query[1:].strip()
        elif query.startswith('$'):
            type_filter = 'grouping'
            query = query[1:].strip()

        query_lower = query.lower()

        if not query and not type_filter:
            self.filtered_results = list(self.all_results)
        else:
            self.filtered_results = []
            for item_type, identifier, data in self.all_results:
                # Skip if type doesn't match filter
                if type_filter and item_type != type_filter:
                    continue

                # If no query text, include all of this type
                if not query:
                    self.filtered_results.append((item_type, identifier, data))
                    continue

                # Search within the item
                searchable = identifier.lower()
                if item_type == 'airport' and data:
                    searchable += ' ' + data.lower()
                elif item_type == 'flight' and data:
                    fp = data.get('flight_plan') or {}
                    searchable += ' ' + (fp.get('departure') or '').lower()
                    searchable += ' ' + (fp.get('arrival') or '').lower()

                if query_lower in searchable:
                    self.filtered_results.append((item_type, identifier, data))

        self._update_option_list()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key in input"""
        await self._execute_selected()

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle option selection"""
        await self._execute_selected()

    async def _execute_selected(self) -> None:
        """Execute action for the selected item"""
        option_list = self.query_one("#goto-list", OptionList)

        if option_list.highlighted is None or not self.filtered_results:
            return

        # Ensure we're within bounds of both the displayed list (max 100) and filtered results
        max_index = min(100, len(self.filtered_results))
        if option_list.highlighted >= max_index:
            return

        item_type, identifier, data = self.filtered_results[option_list.highlighted]

        self.dismiss()

        if item_type == 'airport':
            self._open_airport(identifier)
        elif item_type == 'grouping':
            self._open_grouping(identifier)
        elif item_type == 'flight':
            self.vatsim_app.push_screen(FlightInfoScreen(data))

    def _open_airport(self, icao: str) -> None:
        """Open flight board for an airport"""
        title = config.DISAMBIGUATOR.get_full_name(icao) if config.DISAMBIGUATOR else icao
        enable_anims = not self.vatsim_app.args.disable_animations if self.vatsim_app.args else True
        max_eta = self.vatsim_app.args.max_eta_hours if self.vatsim_app.args else 1.0

        self.vatsim_app.flight_board_open = True
        flight_board = FlightBoardScreen(
            title, icao, max_eta,
            self.vatsim_app.refresh_interval,
            config.DISAMBIGUATOR,
            enable_anims
        )
        self.vatsim_app.active_flight_board = flight_board
        self.vatsim_app.push_screen(flight_board)

    def _open_grouping(self, name: str) -> None:
        """Open flight board for a grouping"""
        # Use the already-loaded groupings
        airport_list = list(resolve_grouping_recursively(name, self.all_groupings))
        enable_anims = not self.vatsim_app.args.disable_animations if self.vatsim_app.args else True
        max_eta = self.vatsim_app.args.max_eta_hours if self.vatsim_app.args else 1.0

        self.vatsim_app.flight_board_open = True
        flight_board = FlightBoardScreen(
            name, airport_list, max_eta,
            self.vatsim_app.refresh_interval,
            config.DISAMBIGUATOR,
            enable_anims
        )
        self.vatsim_app.active_flight_board = flight_board
        self.vatsim_app.push_screen(flight_board)

    def action_close(self) -> None:
        """Close the modal"""
        self.dismiss()
