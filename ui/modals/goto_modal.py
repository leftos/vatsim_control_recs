"""Go To Modal Screen - Unified navigation to airports, groupings, and flights"""

import asyncio
import json
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from backend.core.groupings import (
    get_user_favorite_names,
    load_all_groupings,
    load_favorite_filters,
    resolve_grouping_recursively,
)
from backend.data.vatsim_api import download_vatsim_data
from common.paths import get_user_favorites_file
from ui import config
from ui.debug_logger import debug

from .confirm_modal import ConfirmModal
from .flight_board import FlightBoardScreen
from .flight_info import FlightInfoScreen
from .save_grouping import SaveGroupingModal

if TYPE_CHECKING:
    from ui.app import VATSIMControlApp


TITLE_PICK_SEVERAL = (
    "Go To \u00b7 Pick several \u00b7 {picked} picked \u2192 {airports} airports"
)
TITLE_EDITING = "Go To \u00b7 Editing \u2605 {name}"

# The hint line lists "<key> <action>" items that work in the current state
HINT_SEPARATOR = " \u00b7 "
HINT_MAX_ITEMS = 6

# Filter mode display indicators
_FILTER_INDICATORS = {
    "dep": "[D\u25b8]",
    "arr": "[\u25c2A]",
}
_FILTER_INDICATOR_BOTH = "[  ]"

# Filter cycling order: None (both) -> dep -> arr -> None
_FILTER_CYCLE = {None: "dep", "dep": "arr", "arr": None}


# Actions dropped from an over-long hint, lowest priority first. Picking,
# opening and saving outrank the per-row keys; Tab and Esc are never dropped.
_HINT_DROP_ORDER = ("Ctrl+D ", "E ", "F ", "Ctrl+S ", "Ctrl+Enter ")

# Cap on the rows the list builds; a larger result set keeps only the first 100.
_MAX_OPTIONS = 100


def _cap_hint(actions: list[str], tab: str, close: str) -> list[str]:
    """Fit the actions, the Tab item and the Esc item into HINT_MAX_ITEMS."""
    for prefix in _HINT_DROP_ORDER:
        if len(actions) + 2 <= HINT_MAX_ITEMS:
            break
        actions = [action for action in actions if not action.startswith(prefix)]
    return [*actions, tab, close]


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
        width: 96;
        max-width: 100%;
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

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close", priority=True),
    ]

    def __init__(
        self,
        filter_type: str | None = None,
        callback: Callable[[tuple[str, list[str]] | None], None] | None = None,
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
        self.tracked_airports: list[str] = []
        self.all_groupings: dict = {}
        self.pilots: list[dict] = []
        self.all_results: list[tuple[str, str, Any]] = []
        self.filtered_results: list[tuple[str, str, Any]] = []
        self.data_loaded = False
        self._load_worker = None

        # Multi-select state
        self.multi_select_mode: bool = False
        self.selected_items: list[tuple[str, str, Any]] = []
        self.selected_identifiers: set[str] = set()

        # Per-airport filter state: maps keys like "airport:KSFO",
        # "grouping:Bay Area", or "sub:KSFO" to "dep" or "arr".
        # Absent = both directions.
        self.selected_filters: dict[str, str] = {}

        # When editing an existing favorite via E key
        self._editing_favorite_name: str | None = None

        # User grouping names for star prefix
        self.user_grouping_names: set[str] = set()

        # Display items list — maps 1:1 with OptionList entries.
        # Each entry is (item_type, identifier, data) where item_type
        # can be "sub" for expanded grouping sub-items.
        self._display_items: list[tuple[str, str, Any]] = []

    def compose(self) -> ComposeResult:
        if self.filter_type == "$":
            placeholder = "Search groupings..."
        else:
            placeholder = "Search... (@airport, #flight, $grouping)"

        with Container(id="goto-container"):
            yield Static(Content(self.custom_title), id="goto-title")
            # No select-all on focus: a letter typed in the list is appended to
            # the query, and the next letter must not replace the whole query
            yield Input(placeholder=placeholder, id="goto-input", select_on_focus=False)
            yield Static("", id="goto-selection-summary")
            yield OptionList(Option("Loading...", disabled=True), id="goto-list")
            yield Static("", id="goto-hint")

    def on_mount(self) -> None:
        """Focus input and load data"""
        self.query_one("#goto-input", Input).focus()
        self._refresh_chrome()
        self._load_worker = self.run_worker(self._load_data(), exclusive=True)

    def _can_multi_select(self) -> bool:
        """Multi-select is disabled in callback mode and grouping-only filter."""
        return self.callback is None and self.filter_type is None

    def on_key(self, event: events.Key) -> None:
        """Dispatch the modal's keys; the hint line mirrors these handlers."""
        if (
            self._handle_command_key(event.key)
            or self._handle_list_key(event)
            or self._handle_focus_move(event.key)
        ):
            event.prevent_default()
            event.stop()

    def _handle_command_key(self, key: str) -> bool:
        """Handle the keys that work from the input and the list alike."""
        if key == "tab" and self._can_multi_select():
            self._toggle_multi_select()
            return True
        # Terminals vary: some send "ctrl+enter", others "ctrl+j". Outside
        # multi-select ctrl+j is left alone, as some terminals send it for Enter.
        if key == "ctrl+enter" or (key == "ctrl+j" and self.multi_select_mode):
            self._open_multi_select_flight_board()
            return True
        if key == "ctrl+s":
            self._save_selection()
            return True
        if key == "ctrl+d":
            self._prompt_delete_favorite()
            return True
        return False

    def _handle_list_key(self, event: events.Key) -> bool:
        """Handle E (and F and Space while picking); other characters go to the search."""
        if not self.query_one("#goto-list", OptionList).has_focus:
            return False
        if self._can_multi_select() and event.key == "e":
            self._edit_favorite()
            return True
        if self.multi_select_mode and event.key in ("f", "space"):
            if event.key == "f":
                self._cycle_filter_on_highlighted()
            else:
                self._toggle_highlighted("Space")
            return True
        if event.is_printable and event.character:
            self._type_into_search(event.character)
            return True
        return False

    def _handle_focus_move(self, key: str) -> bool:
        """Move focus between the input and the list with Up and Down."""
        option_list = self.query_one("#goto-list", OptionList)
        input_widget = self.query_one("#goto-input", Input)

        if input_widget.has_focus:
            if option_list.option_count == 0 or key not in ("down", "up"):
                return False
            option_list.focus()
            option_list.highlighted = (
                0 if key == "down" else option_list.option_count - 1
            )
            return True

        if option_list.has_focus and (
            (key == "up" and option_list.highlighted == 0)
            or (
                key == "down"
                and option_list.highlighted == option_list.option_count - 1
            )
        ):
            input_widget.focus()
            return True
        return False

    def _toggle_multi_select(self) -> None:
        """Switch between browse and multi-select; picking happens in the list."""
        self.multi_select_mode = not self.multi_select_mode
        self._update_option_list()
        if self.multi_select_mode:
            self.query_one("#goto-list", OptionList).focus()

    def _type_into_search(self, character: str) -> None:
        """Move focus to the search input and append the typed character."""
        input_widget = self.query_one("#goto-input", Input)
        input_widget.focus()
        input_widget.value += character
        input_widget.cursor_position = len(input_widget.value)

    def _warn(self, message: str) -> None:
        """Explain a key press that did nothing because its precondition failed."""
        self.notify(message, severity="warning", markup=False)

    def on_descendant_focus(self, _event: events.DescendantFocus) -> None:
        self._update_hint_text()

    def on_option_list_option_highlighted(
        self, _event: OptionList.OptionHighlighted
    ) -> None:
        self._update_hint_text()

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
            except Exception as e:
                debug(f"Could not load pilots for Go To search: {e}")

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

    def _format_label(self, item_type: str, identifier: str, data: Any) -> str:
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
                return (
                    f"[x {indicator[1:-1]}] {base}"
                    if indicator != _FILTER_INDICATOR_BOTH
                    else f"[x] {base}"
                )
            return f"[ ] {base}"

        return base

    def _update_option_list(self) -> None:
        """Rebuild the list, then the title and hint that describe it."""
        self._fill_option_list()
        self._refresh_chrome()

    def _fill_option_list(self) -> None:
        """Fill the option list with current filtered results.

        In multi-select mode, inserts expanded sub-items for selected
        groupings immediately after the grouping entry.
        """
        option_list = self.query_one("#goto-list", OptionList)
        option_list.clear_options()
        self._display_items = []

        results = self._visible_results()
        placeholder = self._empty_state_option(results)
        if placeholder is not None:
            option_list.add_option(placeholder)
            return

        count = 0
        for item_type, identifier, data in results:
            if count >= _MAX_OPTIONS:
                break

            self._add_row(option_list, item_type, identifier, data)
            count += 1

            # In multi-select mode, expand selected groupings
            if self._is_selected_grouping(item_type, identifier):
                count += self._add_member_rows(
                    option_list, identifier, _MAX_OPTIONS - count
                )

        if option_list.option_count > 0:
            option_list.highlighted = 0

    def _visible_results(self) -> list[tuple[str, str, Any]]:
        """Return the results to show; flights are hidden while picking."""
        if not self.multi_select_mode:
            return list(self.filtered_results)
        return [r for r in self.filtered_results if r[0] != "flight"]

    def _empty_state_option(self, results: list[tuple[str, str, Any]]) -> Option | None:
        """Return the disabled placeholder row for an empty list, else None."""
        if not self.data_loaded:
            return Option("Loading...", disabled=True)
        if not results:
            return Option("No results found", disabled=True)
        return None

    def _is_selected_grouping(self, item_type: str, identifier: str) -> bool:
        """True for a picked grouping row, which expands to its member airports."""
        return (
            self.multi_select_mode
            and item_type == "grouping"
            and f"grouping:{identifier}" in self.selected_identifiers
        )

    def _add_row(
        self, option_list: OptionList, item_type: str, identifier: str, data: Any
    ) -> None:
        """Add one option row and its matching _display_items entry."""
        label = self._format_label(item_type, identifier, data)
        # Plain Content: a "[x]" checkbox or a bracketed name would otherwise parse as markup
        option_list.add_option(Option(Content(label), id=f"{item_type}:{identifier}"))
        self._display_items.append((item_type, identifier, data))

    def _add_member_rows(
        self, option_list: OptionList, identifier: str, budget: int
    ) -> int:
        """Add a selected grouping's member rows, up to budget; return the count."""
        resolved = sorted(resolve_grouping_recursively(identifier, self.all_groupings))
        added = 0
        for icao in resolved:
            if added >= budget:
                break
            self._add_row(option_list, "sub", icao, None)
            added += 1
        return added

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
            self._toggle_highlighted("Enter")
        else:
            await self._execute_selected()

    async def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        """Handle option selection (Enter/click on list item)"""
        if self.multi_select_mode:
            self._toggle_highlighted("Enter")
        else:
            await self._execute_selected()

    def _toggle_highlighted(self, key_name: str) -> None:
        """Toggle selection of the currently highlighted item.

        Args:
            key_name: The key that asked for the toggle, named in the warning
                when there is no row to pick or the row cannot be picked.
        """
        item = self._get_highlighted_item()
        if item is None:
            self._warn(f"{key_name} needs a highlighted row")
            return

        item_type, identifier, data = item

        # Sub-items are not toggleable (they belong to their parent grouping)
        if item_type == "sub":
            self._warn(
                f"{key_name} can't pick a member row; pick or unpick its grouping"
            )
            return

        self._toggle_selection(item_type, identifier, data)

    def _toggle_selection(self, item_type: str, identifier: str, data: Any) -> None:
        """Add or remove an item from the multi-select selection."""
        key = f"{item_type}:{identifier}"
        if key in self.selected_identifiers:
            self.selected_identifiers.discard(key)
            self.selected_items = [
                (t, i, d) for t, i, d in self.selected_items if f"{t}:{i}" != key
            ]
            # Clean up filters for deselected item
            self.selected_filters.pop(key, None)
            # Clean up sub-item filters if it was a grouping
            if item_type == "grouping":
                resolved = resolve_grouping_recursively(identifier, self.all_groupings)
                for icao in resolved:
                    self.selected_filters.pop(f"sub:{icao}", None)
        else:
            self.selected_identifiers.add(key)
            self.selected_items.append((item_type, identifier, data))

        self._update_selection_summary()
        self._update_option_list()

    def _filter_blocked_reason(self, item: tuple[str, str, Any] | None) -> str | None:
        """Say why F cannot cycle a filter on this row, or None when it can.

        F is only handled while picking several, so the mode is not checked.
        """
        if item is None:
            return "no row is highlighted"
        item_type, identifier, _data = item
        if (
            item_type == "sub"
            or f"{item_type}:{identifier}" in self.selected_identifiers
        ):
            return None
        return f"{self._row_name(item)} isn't picked"

    def _cycle_filter_on_highlighted(self) -> None:
        """Cycle the dep/arr filter on the currently highlighted item."""
        item = self._get_highlighted_item()
        reason = self._filter_blocked_reason(item)
        if item is None or reason is not None:
            self._warn(f"F works on picked rows only; {reason}")
            return

        item_type, identifier, _data = item
        option_list = self.query_one("#goto-list", OptionList)
        # Save position before rebuilding the list
        saved_pos = option_list.highlighted or 0

        if item_type == "grouping":
            self._cycle_grouping_filter(identifier)
        else:
            # A picked airport or a grouping's member airport: its own key
            self._cycle_filter_key(f"{item_type}:{identifier}")

        self._update_option_list()
        option_list.highlighted = min(saved_pos, option_list.option_count - 1)

    def _cycle_filter_key(self, key: str) -> None:
        """Advance one filter key through both -> dep -> arr -> both."""
        new_mode = _FILTER_CYCLE[self.selected_filters.get(key)]
        if new_mode is None:
            self.selected_filters.pop(key, None)
        else:
            self.selected_filters[key] = new_mode

    def _cycle_grouping_filter(self, identifier: str) -> None:
        """Cycle the filter on every resolved airport of a picked grouping."""
        resolved = resolve_grouping_recursively(identifier, self.all_groupings)
        # Determine current grouping-level filter by checking if all
        # sub-items share the same filter
        sub_modes = {self.selected_filters.get(f"sub:{icao}") for icao in resolved}
        # Mixed sub-filters are treated as "both" so next press sets all to dep
        current = sub_modes.pop() if len(sub_modes) == 1 else None

        new_mode = _FILTER_CYCLE[current]
        # The grouping-level key is for display; the sub keys are what is saved
        for key in [f"grouping:{identifier}", *(f"sub:{icao}" for icao in resolved)]:
            if new_mode is None:
                self.selected_filters.pop(key, None)
            else:
                self.selected_filters[key] = new_mode

    def _is_favorite_row(self, item: tuple[str, str, Any] | None) -> bool:
        return (
            item is not None
            and item[0] == "grouping"
            and item[1] in self.user_grouping_names
        )

    @staticmethod
    def _row_name(item: tuple[str, str, Any]) -> str:
        """Name a row in a toast: groupings quoted, ICAOs and callsigns bare."""
        item_type, identifier, _data = item
        return f"'{identifier}'" if item_type == "grouping" else identifier

    def _describe_non_favorite(self, item: tuple[str, str, Any] | None) -> str:
        """Say what the highlighted row is, when E or Ctrl+D needs a ★ row."""
        if item is None:
            return "no row is highlighted"
        kinds = {
            "grouping": "a preset",
            "airport": "an airport",
            "sub": "a grouping member",
            "flight": "a flight",
        }
        return f"{self._row_name(item)} is {kinds.get(item[0], 'not a grouping')}"

    def _edit_favorite(self) -> None:
        """Load a favorite into multi-select mode for editing."""
        item = self._get_highlighted_item()
        if item is None or not self._is_favorite_row(item):
            self._warn(f"E edits ★ groupings only; {self._describe_non_favorite(item)}")
            return

        identifier = item[1]

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

        # Saved filters are per ICAO. An individually picked airport's row reads
        # its "airport:" key; an airport that came from a grouping reads "sub:".
        saved_filters = load_favorite_filters(identifier)
        for icao, mode in saved_filters.items():
            airport_key = f"airport:{icao}"
            if airport_key in self.selected_identifiers:
                self.selected_filters[airport_key] = mode
            else:
                self.selected_filters[f"sub:{icao}"] = mode

        self._update_selection_summary()
        self._update_option_list()

    def _picked_airports(self) -> set[str]:
        """Resolve the picks to their distinct airport ICAOs."""
        airports: set[str] = set()
        for item_type, identifier, _data in self.selected_items:
            if item_type == "airport":
                airports.add(identifier)
            elif item_type == "grouping":
                airports.update(
                    resolve_grouping_recursively(identifier, self.all_groupings)
                )
        return airports

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
            if item_type == "airport" or item_type == "grouping":
                names.append(identifier)

        all_airports = self._picked_airports()

        # Build text with truncation for >3 items
        if len(names) <= 3:
            items_text = ", ".join(names)
        else:
            items_text = ", ".join(names[:3]) + f" + {len(names) - 3} more"

        summary_widget.update(
            f"Selected: {items_text} ({len(all_airports)} airports total)"
        )
        summary_widget.add_class("has-selection")

    def _refresh_chrome(self) -> None:
        """Bring the title and the hint line up to date with the state."""
        self._update_title()
        self._update_hint_text()

    def _update_title(self) -> None:
        """Name the mode in the title; picker mode keeps its caller's title."""
        title = self.custom_title
        if self.multi_select_mode and self._editing_favorite_name is not None:
            title = TITLE_EDITING.format(name=self._editing_favorite_name)
        elif self.multi_select_mode:
            title = TITLE_PICK_SEVERAL.format(
                picked=len(self.selected_items),
                airports=len(self._picked_airports()),
            )
        self.query_one("#goto-title", Static).update(Content(title))

    def _update_hint_text(self) -> None:
        """Show the keys that do something for this mode, focus and row."""
        item = self._get_highlighted_item()
        if not self._can_multi_select():
            enter = "Enter select" if self.callback else "Enter open"
            items = [enter, "Esc close"] if item is not None else ["Esc close"]
        elif self.multi_select_mode:
            editing = self._editing_favorite_name is not None
            close = "Esc close (discard edit)" if editing else "Esc close"
            actions = self._multi_select_hint_actions(item)
            items = _cap_hint(actions, "Tab browse", close)
        else:
            actions = self._browse_hint_actions(item)
            items = _cap_hint(actions, "Tab pick several", "Esc close")
        self.query_one("#goto-hint", Static).update(Content(HINT_SEPARATOR.join(items)))

    def _browse_hint_actions(self, item: tuple[str, str, Any] | None) -> list[str]:
        actions = ["Enter open"] if item is not None else []
        return [*actions, *self._favorite_hint_items(item)]

    def _multi_select_hint_actions(
        self, item: tuple[str, str, Any] | None
    ) -> list[str]:
        list_focused = self.query_one("#goto-list", OptionList).has_focus
        actions: list[str] = []
        if item is not None and item[0] != "sub":
            actions.append("Space pick" if list_focused else "Enter pick")
        if list_focused and self._filter_blocked_reason(item) is None:
            actions.append("F dep/arr/both")
        if self._picked_airports():
            actions.append("Ctrl+Enter open")
        if self.selected_items:
            actions.append("Ctrl+S save")
        return [*actions, *self._favorite_hint_items(item)]

    def _favorite_hint_items(self, item: tuple[str, str, Any] | None) -> list[str]:
        """E (list focus only) and Ctrl+D, which act on ★ rows."""
        if not self._is_favorite_row(item):
            return []
        list_focused = self.query_one("#goto-list", OptionList).has_focus
        return ["E edit", "Ctrl+D delete"] if list_focused else ["Ctrl+D delete"]

    def _resolve_filters(self) -> dict[str, str]:
        """Resolve selected_filters to per-airport ICAO filters.

        Grouping-level filters apply to all their airports.
        Sub-item (per-airport) filters override grouping-level.
        """
        resolved_filters: dict[str, str] = {}

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
        reason = self._picks_blocked_reason()
        if reason is not None:
            self._warn(f"Ctrl+Enter opens picks; {reason}")
            return
        if not self.selected_items:
            self._warn("Ctrl+Enter needs at least one pick")
            return

        all_airports = self._picked_airports()
        if not all_airports:
            self._warn(
                "Ctrl+Enter needs at least one airport; the picks resolve to none"
            )
            return

        names = [identifier for _type, identifier, _data in self.selected_items]

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
        reason = self._picks_blocked_reason()
        if reason is not None:
            self._warn(f"Ctrl+S saves picks; {reason}")
            return
        if not self.selected_items:
            self._warn("Ctrl+S needs at least one pick")
            return

        # Build the raw reference list (mix of ICAOs and grouping names)
        raw_items: list[str] = [
            identifier for _item_type, identifier, _data in self.selected_items
        ]

        # Resolved airport count for the save dialog's hint
        all_airports = self._picked_airports()

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
        self.app.push_screen(save_modal, callback=self._on_save_dismissed)

    def _on_save_dismissed(self, result: str | None) -> None:
        """Handle SaveGroupingModal dismissal."""
        if result is None:
            return

        saved_name = result
        saved_items = getattr(self, "_pending_save_items", [])

        # If we were editing a favorite and the name changed, remove the old one
        old_name = self._editing_favorite_name
        if old_name and old_name != saved_name:
            try:
                self._remove_favorite_from_file(old_name)
                self._forget_favorite(old_name)
            except (json.JSONDecodeError, OSError) as e:
                debug(
                    f"Go To: saved '{saved_name}' but removing the old "
                    f"'{old_name}' failed: {e}"
                )
                self.notify(
                    f"Saved '{saved_name}' but couldn't remove the old '{old_name}': {e}",
                    severity="error",
                    markup=False,
                )

        # Invalidate app-level cache so next open is fresh
        self.vatsim_app.invalidate_goto_cache()
        self.user_grouping_names = get_user_favorite_names()

        # Inject the new favorite into local state so it's visible immediately
        self.all_groupings[saved_name] = saved_items
        # Only add if not already in results
        if not any(r[0] == "grouping" and r[1] == saved_name for r in self.all_results):
            self.all_results.append(("grouping", saved_name, None))

        # Switch to single-select with the new name pre-filled
        self.multi_select_mode = False
        self.selected_items.clear()
        self.selected_identifiers.clear()
        self.selected_filters.clear()
        self._editing_favorite_name = None
        self._update_selection_summary()

        input_widget = self.query_one("#goto-input", Input)
        input_widget.value = saved_name
        input_widget.focus()
        # Rebuild even when the query already equals the name: the rows still
        # carry multi-select checkboxes, and the title still names the old mode
        self._apply_current_filter()

    @staticmethod
    def _remove_favorite_from_file(name: str) -> None:
        """Remove one entry from favorites.json.

        Raises:
            OSError: The file could not be read or written.
            json.JSONDecodeError: The file is not valid JSON.
        """
        favorites_file = get_user_favorites_file()
        with open(favorites_file, encoding="utf-8") as f:
            data = json.load(f)
        if name in data:
            data.pop(name)
            with open(favorites_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    def _forget_favorite(self, name: str) -> None:
        """Drop a removed favorite from the modal's local state."""
        self.all_groupings.pop(name, None)
        self.all_results = [
            r for r in self.all_results if not (r[0] == "grouping" and r[1] == name)
        ]
        self.user_grouping_names.discard(name)

    def _picks_blocked_reason(self) -> str | None:
        """Say why Ctrl+S and Ctrl+Enter cannot act on picks now, or None."""
        if not self._can_multi_select():
            return "picking isn't available here"
        if not self.multi_select_mode:
            return "press Tab to pick several"
        return None

    def _get_highlighted_item(self) -> tuple[str, str, Any] | None:
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
        if not self._can_multi_select():
            self._warn("Ctrl+D isn't available here")
            return
        item = self._get_highlighted_item()
        if item is None or not self._is_favorite_row(item):
            self._warn(
                f"Ctrl+D deletes ★ groupings only; {self._describe_non_favorite(item)}"
            )
            return

        identifier = item[1]
        self.app.push_screen(
            ConfirmModal(f"Delete favorite '{identifier}'?"),
            callback=lambda confirmed: self._on_delete_confirmed(confirmed, identifier),
        )

    def _on_delete_confirmed(self, confirmed: bool, name: str) -> None:
        """Handle delete confirmation result."""
        if not confirmed:
            return

        try:
            self._remove_favorite_from_file(name)
        except (json.JSONDecodeError, OSError) as e:
            debug(f"Go To: deleting '{name}' failed: {e}")
            self.notify(
                f"Couldn't delete '{name}': {e}", severity="error", markup=False
            )
            return

        self._forget_favorite(name)
        self.vatsim_app.invalidate_goto_cache()

        self._apply_current_filter()

    async def _execute_selected(self) -> None:
        """Execute action for the selected item"""
        item = self._get_highlighted_item()
        if item is None:
            self._warn("Enter needs a highlighted row")
            return

        item_type, identifier, data = item

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
        """Open the flight board for a grouping, with its ★ filters when it is one."""
        self.vatsim_app.open_grouping_board(name)

    def action_close(self) -> None:
        """Close the modal"""
        if self.callback:
            self.callback(None)
        self.dismiss()
