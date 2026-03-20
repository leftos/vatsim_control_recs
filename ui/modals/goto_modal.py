"""Go To Modal Screen - Unified navigation to airports, groupings, and flights"""

import asyncio
import json
import os
from typing import Dict, List, Tuple, Any, Callable, Optional, Set, TYPE_CHECKING

from textual.screen import ModalScreen
from textual.widgets import Static, Input, OptionList
from textual.widgets.option_list import Option
from textual.containers import Container
from textual.binding import Binding
from textual.app import ComposeResult

from backend.data.vatsim_api import download_vatsim_data
from backend.core.groupings import (
    get_user_favorite_names,
    load_all_groupings,
    load_favorite_filters,
    resolve_grouping_recursively,
)
from ui import config
from .flight_info import FlightInfoScreen
from .flight_board import FlightBoardScreen
from common.paths import get_user_favorites_file
from .confirm_modal import ConfirmModal
from .save_grouping import SaveGroupingModal

if TYPE_CHECKING:
    from ui.app import VATSIMControlApp


HINT_SINGLE_SELECT = (
    "@ Airport | # Flight | $ Grouping"
    " | Tab Multi-select | Ctrl+D Delete \u2605 | E Edit \u2605 | Enter Open | Esc Close"
)
HINT_MULTI_SELECT = (
    "Enter/Space Toggle | F Filter"
    " | Ctrl+Enter Open | Ctrl+S Save | Ctrl+D Delete \u2605 | Tab Single-select | Esc Close"
)

# Filter mode display indicators
_FILTER_INDICATORS = {
    "dep": "[D\u25b8]",
    "arr": "[\u25c2A]",
}
_FILTER_INDICATOR_BOTH = "[  ]"

# Filter cycling order: None (both) -> dep -> arr -> None
_FILTER_CYCLE = {None: "dep", "dep": "arr", "arr": None}


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

    #goto-selection-summary {
        color: $text-muted;
        margin-bottom: 1;
        display: none;
    }

    #goto-selection-summary.has-selection {
        display: block;
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
        title: str = "Go To",
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
        self.all_groupings: dict = {}
        self.pilots: List[dict] = []
        self.all_results: List[Tuple[str, str, Any]] = []
        self.filtered_results: List[Tuple[str, str, Any]] = []
        self.data_loaded = False
        self._load_worker = None

        # Multi-select state
        self.multi_select_mode: bool = False
        self.selected_items: List[Tuple[str, str, Any]] = []
        self.selected_identifiers: Set[str] = set()

        # Per-airport filter state: maps keys like "airport:KSFO",
        # "grouping:Bay Area", or "sub:KSFO" to "dep" or "arr".
        # Absent = both directions.
        self.selected_filters: Dict[str, str] = {}

        # When editing an existing favorite via E key
        self._editing_favorite_name: Optional[str] = None

        # User grouping names for star prefix
        self.user_grouping_names: Set[str] = set()

        # Display items list — maps 1:1 with OptionList entries.
        # Each entry is (item_type, identifier, data) where item_type
        # can be "sub" for expanded grouping sub-items.
        self._display_items: List[Tuple[str, str, Any]] = []

    def compose(self) -> ComposeResult:
        # Determine placeholder and hint based on filter_type
        if self.filter_type == "$":
            placeholder = "Search groupings..."
            hint = "Enter: Select | Esc: Close"
        else:
            placeholder = "Search... (@airport, #flight, $grouping)"
            hint = HINT_SINGLE_SELECT

        with Container(id="goto-container"):
            yield Static(self.custom_title, id="goto-title")
            yield Input(placeholder=placeholder, id="goto-input")
            yield Static("", id="goto-selection-summary")
            yield OptionList(Option("Loading...", disabled=True), id="goto-list")
            yield Static(hint, id="goto-hint")

    def on_mount(self) -> None:
        """Focus input and load data"""
        self.query_one("#goto-input", Input).focus()
        self._load_worker = self.run_worker(self._load_data(), exclusive=True)

    def _can_multi_select(self) -> bool:
        """Multi-select is disabled in callback mode and grouping-only filter."""
        return self.callback is None and self.filter_type is None

    def on_key(self, event) -> None:
        """Handle key events for navigation between input and list"""
        option_list = self.query_one("#goto-list", OptionList)
        input_widget = self.query_one("#goto-input", Input)

        input_focused = input_widget.has_focus
        list_focused = option_list.has_focus

        # Tab: toggle multi-select mode
        if event.key == "tab" and self._can_multi_select():
            self.multi_select_mode = not self.multi_select_mode
            self._update_hint_text()
            self._update_option_list()
            event.prevent_default()
            event.stop()
            return

        # Ctrl+Enter: open multi-select flight board
        # Terminals vary: some send "ctrl+enter", others map it to "ctrl+j" or "ctrl+m"
        if event.key in ("ctrl+enter", "ctrl+j") and self.multi_select_mode:
            self._open_multi_select_flight_board()
            event.prevent_default()
            event.stop()
            return

        # Ctrl+S: save selection as favorite
        if event.key == "ctrl+s" and self.multi_select_mode:
            self._save_selection()
            event.prevent_default()
            event.stop()
            return

        # Ctrl+D: delete highlighted favorite
        if event.key == "ctrl+d":
            self._prompt_delete_favorite()
            event.prevent_default()
            event.stop()
            return

        # E key: edit highlighted favorite (list must be focused)
        if event.key == "e" and list_focused and self._can_multi_select():
            self._edit_favorite()
            event.prevent_default()
            event.stop()
            return

        # F key: cycle filter on highlighted item (multi-select, list focused)
        if (
            event.key == "f"
            and self.multi_select_mode
            and list_focused
        ):
            self._cycle_filter_on_highlighted()
            event.prevent_default()
            event.stop()
            return

        # Space in multi-select mode when list is focused: toggle selection
        if (
            event.key == "space"
            and self.multi_select_mode
            and list_focused
        ):
            self._toggle_highlighted()
            event.prevent_default()
            event.stop()
            return

        if input_focused:
            if event.key == "down":
                if option_list.option_count > 0:
                    option_list.focus()
                    option_list.highlighted = 0
                    event.prevent_default()
                    event.stop()
            elif event.key == "up":
                if option_list.option_count > 0:
                    option_list.focus()
                    option_list.highlighted = option_list.option_count - 1
                    event.prevent_default()
                    event.stop()

        elif list_focused:
            if event.key == "up" and option_list.highlighted == 0:
                input_widget.focus()
                event.prevent_default()
                event.stop()
            elif (
                event.key == "down"
                and option_list.highlighted == option_list.option_count - 1
            ):
                input_widget.focus()
                event.prevent_default()
                event.stop()

    def on_unmount(self) -> None:
        """Cancel any pending workers when modal is closed"""
        if self._load_worker and not self._load_worker.is_finished:
            self._load_worker.cancel()

    async def _load_data(self) -> None:
        """Load all data sources, using cached data when available"""
        loop = asyncio.get_event_loop()

        # Load user grouping names for star prefix (file I/O)
        self.user_grouping_names = await loop.run_in_executor(
            None, get_user_favorite_names
        )

        if self.vatsim_app.goto_cache_ready:
            self.all_groupings = self.vatsim_app.cached_groupings
            self.tracked_airports = list(self.vatsim_app.airport_allowlist or [])
            self.pilots = self.vatsim_app.cached_pilots

            if self.filter_type is None:
                self.all_results = list(self.vatsim_app.cached_goto_results)
            else:
                self.all_results = [
                    (item_type, identifier, data)
                    for item_type, identifier, data in self.vatsim_app.cached_goto_results
                    if item_type == self.filter_type
                    or (self.filter_type == "$" and item_type == "grouping")
                ]

            self.data_loaded = True
            self._apply_current_filter()
            return

        # Fallback path
        self.tracked_airports = list(self.vatsim_app.airport_allowlist or [])

        if self.vatsim_app.cached_groupings:
            self.all_groupings = self.vatsim_app.cached_groupings
        else:
            script_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            self.all_groupings = await loop.run_in_executor(
                None,
                load_all_groupings,
                os.path.join(script_dir, "data", "custom_groupings.json"),
                config.UNIFIED_AIRPORT_DATA or {},
            )

        if self.vatsim_app.cached_pilots:
            self.pilots = self.vatsim_app.cached_pilots
        else:
            try:
                vatsim_data = await loop.run_in_executor(None, download_vatsim_data)
                if vatsim_data:
                    self.pilots = vatsim_data.get("pilots", [])
            except Exception:
                pass

        await loop.run_in_executor(None, self._build_all_results)
        self.data_loaded = True
        self._apply_current_filter()

    def _build_all_results(self) -> None:
        """Build the complete results list, respecting filter_type if set"""
        self.all_results = []

        if self.filter_type != "$":
            for icao in sorted(self.tracked_airports):
                pretty_name = (
                    config.DISAMBIGUATOR.get_full_name(icao)
                    if config.DISAMBIGUATOR
                    else icao
                )
                self.all_results.append(("airport", icao, pretty_name))

        for name in sorted(self.all_groupings.keys()):
            self.all_results.append(("grouping", name, None))

        if self.filter_type != "$":
            for pilot in sorted(self.pilots, key=lambda p: p.get("callsign", "")):
                callsign = pilot.get("callsign", "")
                if callsign:
                    self.all_results.append(("flight", callsign, pilot))

    def _get_filter_indicator(self, key: str) -> str:
        """Get the filter indicator string for a given filter key."""
        mode = self.selected_filters.get(key)
        return _FILTER_INDICATORS.get(mode, _FILTER_INDICATOR_BOTH)

    def _format_label(
        self, item_type: str, identifier: str, data: Any
    ) -> str:
        """Format the display label for an item.

        Uses filter symbols as prefixes: @ for airports, # for flights,
        $ for groupings (or star for user groupings).
        In multi-select mode, prepends a checkbox indicator with optional
        filter mode. Sub-items (expanded grouping airports) show indented
        with filter indicators.
        """
        # Handle sub-items (expanded grouping airports)
        if item_type == "sub":
            indicator = self._get_filter_indicator(f"sub:{identifier}")
            pretty_name = (
                config.DISAMBIGUATOR.get_pretty_name(identifier)
                if config.DISAMBIGUATOR
                else identifier
            )
            return f"     {indicator} @ {identifier} - {pretty_name}"

        # Build base label
        if item_type == "airport":
            base = f"@ {identifier} - {data}"
        elif item_type == "grouping":
            resolved_airports = resolve_grouping_recursively(
                identifier, self.all_groupings
            )
            airport_count = len(resolved_airports)
            prefix = "\u2605" if identifier in self.user_grouping_names else "$"
            base = f"{prefix} {identifier} ({airport_count} airports)"
        elif item_type == "flight":
            fp = data.get("flight_plan") or {}
            dep = fp.get("departure") or "----"
            arr = fp.get("arrival") or "----"
            base = f"# {identifier} - {dep} -> {arr}"
        else:
            base = identifier

        # Prepend checkbox in multi-select mode
        if self.multi_select_mode:
            key = f"{item_type}:{identifier}"
            if key in self.selected_identifiers:
                indicator = self._get_filter_indicator(key)
                return f"[x {indicator[1:-1]}] {base}" if indicator != _FILTER_INDICATOR_BOTH else f"[x] {base}"
            return f"[ ] {base}"

        return base

    def _update_option_list(self) -> None:
        """Update the option list with current filtered results.

        In multi-select mode, inserts expanded sub-items for selected
        groupings immediately after the grouping entry.
        """
        option_list = self.query_one("#goto-list", OptionList)
        option_list.clear_options()
        self._display_items = []

        if not self.data_loaded:
            option_list.add_option(Option("Loading...", disabled=True))
            return

        # In multi-select mode, filter out flights
        results = self.filtered_results
        if self.multi_select_mode:
            results = [
                r for r in results if r[0] != "flight"
            ]

        if not results:
            option_list.add_option(Option("No results found", disabled=True))
            return

        count = 0
        for item_type, identifier, data in results:
            if count >= 100:
                break

            label = self._format_label(item_type, identifier, data)
            option_list.add_option(Option(label, id=f"{item_type}:{identifier}"))
            self._display_items.append((item_type, identifier, data))
            count += 1

            # In multi-select mode, expand selected groupings
            if (
                self.multi_select_mode
                and item_type == "grouping"
                and f"grouping:{identifier}" in self.selected_identifiers
            ):
                resolved = sorted(
                    resolve_grouping_recursively(identifier, self.all_groupings)
                )
                for icao in resolved:
                    if count >= 100:
                        break
                    sub_label = self._format_label("sub", icao, None)
                    option_list.add_option(
                        Option(sub_label, id=f"sub:{icao}")
                    )
                    self._display_items.append(("sub", icao, None))
                    count += 1

        if option_list.option_count > 0:
            option_list.highlighted = 0

    def _apply_current_filter(self) -> None:
        """Apply filtering based on current input text. Called after data loads."""
        try:
            input_widget = self.query_one("#goto-input", Input)
            query = input_widget.value.strip()
        except Exception:
            query = ""

        self._filter_results(query)
        self._update_option_list()

    def _filter_results(self, query: str) -> None:
        """Filter all_results based on query string."""
        type_filter = None
        if query.startswith("@"):
            type_filter = "airport"
            query = query[1:].strip()
        elif query.startswith("#"):
            type_filter = "flight"
            query = query[1:].strip()
        elif query.startswith("$"):
            type_filter = "grouping"
            query = query[1:].strip()

        query_lower = query.lower()

        if not query and not type_filter:
            self.filtered_results = list(self.all_results)
        else:
            self.filtered_results = []
            for item_type, identifier, data in self.all_results:
                if type_filter and item_type != type_filter:
                    continue

                if not query:
                    self.filtered_results.append((item_type, identifier, data))
                    continue

                searchable = identifier.lower()
                if item_type == "airport" and data:
                    searchable += " " + data.lower()
                elif item_type == "flight" and data:
                    fp = data.get("flight_plan") or {}
                    searchable += " " + (fp.get("departure") or "").lower()
                    searchable += " " + (fp.get("arrival") or "").lower()

                if query_lower in searchable:
                    self.filtered_results.append((item_type, identifier, data))

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter results as user types"""
        if not self.data_loaded:
            return

        query = event.value.strip()
        self._filter_results(query)
        self._update_option_list()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key in input"""
        if self.multi_select_mode:
            self._toggle_highlighted()
        else:
            await self._execute_selected()

    async def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        """Handle option selection (Enter/click on list item)"""
        if self.multi_select_mode:
            self._toggle_highlighted()
        else:
            await self._execute_selected()

    def _toggle_highlighted(self) -> None:
        """Toggle selection of the currently highlighted item."""
        option_list = self.query_one("#goto-list", OptionList)
        if option_list.highlighted is None:
            return

        if option_list.highlighted >= len(self._display_items):
            return

        item_type, identifier, data = self._display_items[option_list.highlighted]

        # Sub-items are not toggleable (they belong to their parent grouping)
        if item_type == "sub":
            return

        self._toggle_selection(item_type, identifier, data)

    def _toggle_selection(
        self, item_type: str, identifier: str, data: Any
    ) -> None:
        """Add or remove an item from the multi-select selection."""
        key = f"{item_type}:{identifier}"
        if key in self.selected_identifiers:
            self.selected_identifiers.discard(key)
            self.selected_items = [
                (t, i, d)
                for t, i, d in self.selected_items
                if f"{t}:{i}" != key
            ]
            # Clean up filters for deselected item
            self.selected_filters.pop(key, None)
            # Clean up sub-item filters if it was a grouping
            if item_type == "grouping":
                resolved = resolve_grouping_recursively(
                    identifier, self.all_groupings
                )
                for icao in resolved:
                    self.selected_filters.pop(f"sub:{icao}", None)
        else:
            self.selected_identifiers.add(key)
            self.selected_items.append((item_type, identifier, data))

        self._update_selection_summary()
        self._update_option_list()

    def _cycle_filter_on_highlighted(self) -> None:
        """Cycle the dep/arr filter on the currently highlighted item."""
        option_list = self.query_one("#goto-list", OptionList)
        if option_list.highlighted is None:
            return

        if option_list.highlighted >= len(self._display_items):
            return

        item_type, identifier, _data = self._display_items[option_list.highlighted]

        # Save position before rebuilding the list
        saved_pos = option_list.highlighted

        if item_type == "sub":
            # Cycle filter on individual sub-item airport
            key = f"sub:{identifier}"
            current = self.selected_filters.get(key)
            new_mode = _FILTER_CYCLE[current]
            if new_mode is None:
                self.selected_filters.pop(key, None)
            else:
                self.selected_filters[key] = new_mode
            self._update_option_list()
            option_list.highlighted = min(
                saved_pos, option_list.option_count - 1
            )
            return

        key = f"{item_type}:{identifier}"

        # Only act on selected items
        if key not in self.selected_identifiers:
            return

        if item_type == "grouping":
            # Cycle filter on ALL resolved airports of the grouping
            resolved = resolve_grouping_recursively(
                identifier, self.all_groupings
            )
            # Determine current grouping-level filter by checking if all
            # sub-items share the same filter
            sub_modes = {
                self.selected_filters.get(f"sub:{icao}") for icao in resolved
            }
            if len(sub_modes) == 1:
                current = sub_modes.pop()
            else:
                # Mixed — treat as "both" so next press sets all to dep
                current = None

            new_mode = _FILTER_CYCLE[current]
            for icao in resolved:
                sub_key = f"sub:{icao}"
                if new_mode is None:
                    self.selected_filters.pop(sub_key, None)
                else:
                    self.selected_filters[sub_key] = new_mode

            # Also set grouping-level filter for display
            if new_mode is None:
                self.selected_filters.pop(key, None)
            else:
                self.selected_filters[key] = new_mode
        else:
            # Airport: cycle its own filter
            current = self.selected_filters.get(key)
            new_mode = _FILTER_CYCLE[current]
            if new_mode is None:
                self.selected_filters.pop(key, None)
            else:
                self.selected_filters[key] = new_mode

        self._update_option_list()
        option_list.highlighted = min(
            saved_pos, option_list.option_count - 1
        )

    def _edit_favorite(self) -> None:
        """Load a favorite into multi-select mode for editing."""
        item = self._get_highlighted_item()
        if item is None:
            return

        item_type, identifier, _data = item
        if item_type != "grouping" or identifier not in self.user_grouping_names:
            return

        # Switch to multi-select mode
        self.multi_select_mode = True
        self._editing_favorite_name = identifier
        self.selected_items.clear()
        self.selected_identifiers.clear()
        self.selected_filters.clear()

        # Load the favorite's stored items (grouping refs + individual airports)
        items_in_favorite = self.all_groupings.get(identifier, [])
        for ref in items_in_favorite:
            # Skip self-references (the favorite is in all_groupings too)
            if ref == identifier:
                continue
            if ref in self.all_groupings:
                # It's a grouping reference
                self.selected_identifiers.add(f"grouping:{ref}")
                self.selected_items.append(("grouping", ref, None))
            else:
                # It's an airport ICAO
                pretty_name = (
                    config.DISAMBIGUATOR.get_full_name(ref)
                    if config.DISAMBIGUATOR
                    else ref
                )
                self.selected_identifiers.add(f"airport:{ref}")
                self.selected_items.append(("airport", ref, pretty_name))

        # Load saved filters and map them into selected_filters
        saved_filters = load_favorite_filters(identifier)
        for icao, mode in saved_filters.items():
            self.selected_filters[f"sub:{icao}"] = mode

        self._update_hint_text()
        self._update_selection_summary()
        self._update_option_list()

    def _update_selection_summary(self) -> None:
        """Update the selection summary widget text."""
        summary_widget = self.query_one("#goto-selection-summary", Static)

        if not self.selected_items:
            summary_widget.update("")
            summary_widget.remove_class("has-selection")
            return

        # Build display names
        names = []
        for item_type, identifier, _data in self.selected_items:
            if item_type == "airport":
                names.append(identifier)
            elif item_type == "grouping":
                names.append(identifier)

        # Count total unique airports
        all_airports: Set[str] = set()
        for item_type, identifier, _data in self.selected_items:
            if item_type == "airport":
                all_airports.add(identifier)
            elif item_type == "grouping":
                all_airports.update(
                    resolve_grouping_recursively(identifier, self.all_groupings)
                )

        # Build text with truncation for >3 items
        if len(names) <= 3:
            items_text = ", ".join(names)
        else:
            items_text = ", ".join(names[:3]) + f" + {len(names) - 3} more"

        summary_widget.update(
            f"Selected: {items_text} ({len(all_airports)} airports total)"
        )
        summary_widget.add_class("has-selection")

    def _update_hint_text(self) -> None:
        """Update the hint text based on current mode."""
        hint_widget = self.query_one("#goto-hint", Static)
        if self.multi_select_mode:
            hint_widget.update(HINT_MULTI_SELECT)
        else:
            hint_widget.update(HINT_SINGLE_SELECT)

    def _resolve_filters(self) -> Dict[str, str]:
        """Resolve selected_filters to per-airport ICAO filters.

        Grouping-level filters apply to all their airports.
        Sub-item (per-airport) filters override grouping-level.
        """
        resolved_filters: Dict[str, str] = {}

        for item_type, identifier, _data in self.selected_items:
            key = f"{item_type}:{identifier}"
            filter_mode = self.selected_filters.get(key)
            if filter_mode and item_type == "grouping":
                for icao in resolve_grouping_recursively(
                    identifier, self.all_groupings
                ):
                    if icao not in resolved_filters:
                        resolved_filters[icao] = filter_mode
            elif filter_mode and item_type == "airport":
                resolved_filters[identifier] = filter_mode

        # Sub-item filters always override
        for key, mode in self.selected_filters.items():
            if key.startswith("sub:"):
                icao = key[4:]
                resolved_filters[icao] = mode

        return resolved_filters

    def _open_multi_select_flight_board(self) -> None:
        """Open a flight board for the multi-select selection."""
        if not self.selected_items:
            return

        # Resolve all selected items to a flat, deduplicated set of ICAO codes
        all_airports: Set[str] = set()
        names: List[str] = []
        for item_type, identifier, _data in self.selected_items:
            names.append(identifier)
            if item_type == "airport":
                all_airports.add(identifier)
            elif item_type == "grouping":
                all_airports.update(
                    resolve_grouping_recursively(identifier, self.all_groupings)
                )

        if not all_airports:
            return

        # Resolve filters
        resolved_filters = self._resolve_filters()

        # Build title
        if len(names) <= 3:
            title = " + ".join(names)
        else:
            title = " + ".join(names[:3]) + f" + {len(names) - 3} more"

        self.dismiss()

        enable_anims = (
            not self.vatsim_app.args.disable_animations
            if self.vatsim_app.args
            else True
        )
        max_eta = self.vatsim_app.args.max_eta_hours if self.vatsim_app.args else 1.0

        self.vatsim_app.flight_board_open = True
        flight_board = FlightBoardScreen(
            title,
            list(all_airports),
            max_eta,
            self.vatsim_app.refresh_interval,
            config.DISAMBIGUATOR,
            enable_anims,
            airport_filters=resolved_filters or None,
        )
        self.vatsim_app.active_flight_board = flight_board
        self.vatsim_app.push_screen(flight_board)

    def _save_selection(self) -> None:
        """Save the current multi-select selection as a custom grouping."""
        if not self.multi_select_mode or not self.selected_items:
            return

        # Build the raw reference list (mix of ICAOs and grouping names)
        raw_items: List[str] = []
        for item_type, identifier, _data in self.selected_items:
            raw_items.append(identifier)

        # Compute resolved airport count for the hint
        all_airports: Set[str] = set()
        for item_type, identifier, _data in self.selected_items:
            if item_type == "airport":
                all_airports.add(identifier)
            elif item_type == "grouping":
                all_airports.update(
                    resolve_grouping_recursively(identifier, self.all_groupings)
                )

        # Resolve filters for persistence
        resolved_filters = self._resolve_filters()

        # Stash for callback
        self._pending_save_items = raw_items
        self._pending_save_filters = resolved_filters

        save_modal = SaveGroupingModal(
            raw_items,
            resolve_count=len(all_airports),
            filters=resolved_filters or None,
            prefill_name=self._editing_favorite_name,
        )
        self.app.push_screen(
            save_modal, callback=self._on_save_dismissed
        )

    def _on_save_dismissed(self, result: Optional[str]) -> None:
        """Handle SaveGroupingModal dismissal."""
        if result is None:
            return

        saved_name = result
        saved_items = getattr(self, "_pending_save_items", [])

        # If we were editing a favorite and the name changed, remove the old one
        old_name = self._editing_favorite_name
        if old_name and old_name != saved_name:
            favorites_file = get_user_favorites_file()
            try:
                with open(favorites_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if old_name in data:
                    data.pop(old_name)
                    with open(favorites_file, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                # Remove old from local state
                self.all_groupings.pop(old_name, None)
                self.all_results = [
                    r for r in self.all_results
                    if not (r[0] == "grouping" and r[1] == old_name)
                ]
                self.user_grouping_names.discard(old_name)
            except (json.JSONDecodeError, OSError):
                pass

        # Invalidate app-level cache so next open is fresh
        self.vatsim_app.invalidate_goto_cache()
        self.user_grouping_names = get_user_favorite_names()

        # Inject the new favorite into local state so it's visible immediately
        self.all_groupings[saved_name] = saved_items
        # Only add if not already in results
        if not any(
            r[0] == "grouping" and r[1] == saved_name
            for r in self.all_results
        ):
            self.all_results.append(("grouping", saved_name, None))

        # Switch to single-select with the new name pre-filled
        self.multi_select_mode = False
        self.selected_items.clear()
        self.selected_identifiers.clear()
        self.selected_filters.clear()
        self._editing_favorite_name = None
        self._update_selection_summary()
        self._update_hint_text()

        input_widget = self.query_one("#goto-input", Input)
        input_widget.value = saved_name
        input_widget.focus()

    def _get_highlighted_item(self) -> Optional[Tuple[str, str, Any]]:
        """Return the currently highlighted item, or None.

        Uses _display_items which includes sub-items. For sub-items,
        returns the sub-item tuple directly.
        """
        option_list = self.query_one("#goto-list", OptionList)
        if option_list.highlighted is None:
            return None

        if option_list.highlighted >= len(self._display_items):
            return None

        return self._display_items[option_list.highlighted]

    def _prompt_delete_favorite(self) -> None:
        """Prompt to delete the highlighted item if it's a user favorite."""
        item = self._get_highlighted_item()
        if item is None:
            return

        item_type, identifier, _data = item
        if item_type != "grouping" or identifier not in self.user_grouping_names:
            return

        self.app.push_screen(
            ConfirmModal(f"Delete favorite '{identifier}'?"),
            callback=lambda confirmed: self._on_delete_confirmed(
                confirmed, identifier
            ),
        )

    def _on_delete_confirmed(self, confirmed: bool, name: str) -> None:
        """Handle delete confirmation result."""
        if not confirmed:
            return

        favorites_file = get_user_favorites_file()
        try:
            with open(favorites_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.pop(name, None)
            with open(favorites_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, OSError):
            return

        # Update local state
        self.all_groupings.pop(name, None)
        self.all_results = [
            r for r in self.all_results
            if not (r[0] == "grouping" and r[1] == name)
        ]
        self.user_grouping_names.discard(name)
        self.vatsim_app.invalidate_goto_cache()

        self._apply_current_filter()

    async def _execute_selected(self) -> None:
        """Execute action for the selected item"""
        option_list = self.query_one("#goto-list", OptionList)

        if option_list.highlighted is None or not self._display_items:
            return

        if option_list.highlighted >= len(self._display_items):
            return

        item_type, identifier, data = self._display_items[option_list.highlighted]

        # Sub-items in single-select mode shouldn't happen, but handle gracefully
        if item_type == "sub":
            return

        self.dismiss()

        if self.callback:
            if item_type == "grouping":
                airport_list = list(
                    resolve_grouping_recursively(identifier, self.all_groupings)
                )
                self.callback((identifier, airport_list))
            elif item_type == "airport":
                self.callback((identifier, [identifier]))
            elif item_type == "flight":
                self.callback(("flight", data))
            else:
                self.callback(None)
            return

        if item_type == "airport":
            self._open_airport(identifier)
        elif item_type == "grouping":
            self._open_grouping(identifier)
        elif item_type == "flight":
            self.vatsim_app.push_screen(FlightInfoScreen(data))

    def _open_airport(self, icao: str) -> None:
        """Open flight board for an airport"""
        title = (
            config.DISAMBIGUATOR.get_full_name(icao) if config.DISAMBIGUATOR else icao
        )
        enable_anims = (
            not self.vatsim_app.args.disable_animations
            if self.vatsim_app.args
            else True
        )
        max_eta = self.vatsim_app.args.max_eta_hours if self.vatsim_app.args else 1.0

        self.vatsim_app.flight_board_open = True
        flight_board = FlightBoardScreen(
            title,
            icao,
            max_eta,
            self.vatsim_app.refresh_interval,
            config.DISAMBIGUATOR,
            enable_anims,
        )
        self.vatsim_app.active_flight_board = flight_board
        self.vatsim_app.push_screen(flight_board)

    def _open_grouping(self, name: str) -> None:
        """Open flight board for a grouping, loading saved filters for favorites."""
        airport_list = list(resolve_grouping_recursively(name, self.all_groupings))
        enable_anims = (
            not self.vatsim_app.args.disable_animations
            if self.vatsim_app.args
            else True
        )
        max_eta = self.vatsim_app.args.max_eta_hours if self.vatsim_app.args else 1.0

        # Load saved filters for favorites
        airport_filters = None
        if name in self.user_grouping_names:
            saved_filters = load_favorite_filters(name)
            if saved_filters:
                airport_filters = saved_filters

        self.vatsim_app.flight_board_open = True
        flight_board = FlightBoardScreen(
            name,
            airport_list,
            max_eta,
            self.vatsim_app.refresh_interval,
            config.DISAMBIGUATOR,
            enable_anims,
            airport_filters=airport_filters,
        )
        self.vatsim_app.active_flight_board = flight_board
        self.vatsim_app.push_screen(flight_board)

    def action_close(self) -> None:
        """Close the modal"""
        if self.callback:
            self.callback(None)
        self.dismiss()
