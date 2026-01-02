"""Go To Modal Screen - Unified navigation to airports, groupings, and flights"""

import asyncio
import os
from typing import List, Tuple, Any, Callable, Optional, TYPE_CHECKING

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

    def __init__(
        self,
        filter_type: Optional[str] = None,
        callback: Optional[Callable[[Optional[Tuple[str, List[str]]]], None]] = None,
        title: str = "Go To"
    ):
        """
        Initialize the GoTo modal.

        Args:
            filter_type: If "$", only show groupings (hide airports and flights)
            callback: If provided, call with (name, airports_list) tuple on selection
                     instead of navigating. Pass None if cancelled.
            title: Custom title for the modal (e.g., "Select Grouping")
        """
        super().__init__()
        self.filter_type = filter_type
        self.callback = callback
        self.custom_title = title
        self.tracked_airports: List[str] = []
        self.all_groupings: dict = {}  # All available groupings (name -> airports)
        self.pilots: List[dict] = []
        self.all_results: List[Tuple[str, str, Any]] = []
        self.filtered_results: List[Tuple[str, str, Any]] = []
        self.data_loaded = False
        self._load_worker = None

    def compose(self) -> ComposeResult:
        # Determine placeholder and hint based on filter_type
        if self.filter_type == "$":
            placeholder = "Search groupings..."
            hint = "Enter: Select | Esc: Close"
        else:
            placeholder = "Search... (@airport, #flight, $grouping)"
            hint = "@ Airport | # Flight | $ Grouping | Enter Select | Esc Close"

        with Container(id="goto-container"):
            yield Static(self.custom_title, id="goto-title")
            yield Input(placeholder=placeholder, id="goto-input")
            yield OptionList(Option("Loading...", disabled=True), id="goto-list")
            yield Static(hint, id="goto-hint")

    def on_mount(self) -> None:
        """Focus input and load data"""
        self.query_one("#goto-input", Input).focus()
        self._load_worker = self.run_worker(self._load_data(), exclusive=True)

    def on_key(self, event) -> None:
        """Handle key events for navigation between input and list"""
        option_list = self.query_one("#goto-list", OptionList)
        input_widget = self.query_one("#goto-input", Input)

        # Check which widget has focus
        input_focused = input_widget.has_focus
        list_focused = option_list.has_focus

        if input_focused:
            if event.key == "down":
                # Move from input to first item in list
                if option_list.option_count > 0:
                    option_list.focus()
                    option_list.highlighted = 0
                    event.prevent_default()
                    event.stop()
            elif event.key == "up":
                # Move from input to last item in list
                if option_list.option_count > 0:
                    option_list.focus()
                    option_list.highlighted = option_list.option_count - 1
                    event.prevent_default()
                    event.stop()

        elif list_focused:
            if event.key == "up" and option_list.highlighted == 0:
                # At first item, cycle to input
                input_widget.focus()
                event.prevent_default()
                event.stop()
            elif event.key == "down" and option_list.highlighted == option_list.option_count - 1:
                # At last item, cycle to input
                input_widget.focus()
                event.prevent_default()
                event.stop()

    def on_unmount(self) -> None:
        """Cancel any pending workers when modal is closed"""
        if self._load_worker and not self._load_worker.is_finished:
            self._load_worker.cancel()

    async def _load_data(self) -> None:
        """Load all data sources, using cached data when available for instant responsiveness"""
        # Access app data
        self.tracked_airports = list(self.vatsim_app.airport_allowlist or [])

        # Use cached groupings from app if available (warmed up on mount)
        if self.vatsim_app.cached_groupings:
            self.all_groupings = self.vatsim_app.cached_groupings
        else:
            # Fallback: load groupings (file I/O)
            script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            self.all_groupings = load_all_groupings(
                os.path.join(script_dir, 'data', 'custom_groupings.json'),
                config.UNIFIED_AIRPORT_DATA or {}
            )

        # Use cached pilots from app if available (kept fresh by refresh cycle)
        if self.vatsim_app.cached_pilots:
            self.pilots = self.vatsim_app.cached_pilots
        else:
            # Fallback: load VATSIM data in background (first invocation before cache is ready)
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
        """Build the complete results list, respecting filter_type if set"""
        self.all_results = []

        # Add airports (unless filtered to groupings only)
        if self.filter_type != "$":
            for icao in sorted(self.tracked_airports):
                pretty_name = config.DISAMBIGUATOR.get_full_name(icao) if config.DISAMBIGUATOR else icao
                self.all_results.append(('airport', icao, pretty_name))

        # Add all available groupings
        for name in sorted(self.all_groupings.keys()):
            self.all_results.append(('grouping', name, None))

        # Add flights (sorted by callsign) (unless filtered to groupings only)
        if self.filter_type != "$":
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
            # Show recursively expanded airport count to distinguish from flights
            resolved_airports = resolve_grouping_recursively(identifier, self.all_groupings)
            airport_count = len(resolved_airports)
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

        # If callback is provided, use it instead of navigating
        if self.callback:
            if item_type == 'grouping':
                airport_list = list(resolve_grouping_recursively(identifier, self.all_groupings))
                self.callback((identifier, airport_list))
            elif item_type == 'airport':
                # For airports, return as a single-item list
                self.callback((identifier, [identifier]))
            else:
                # Flights don't make sense for callbacks - just close
                self.callback(None)
            return

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
        # If callback is provided, call it with None to indicate cancellation
        if self.callback:
            self.callback(None)
        self.dismiss()
