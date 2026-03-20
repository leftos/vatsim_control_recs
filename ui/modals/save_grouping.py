"""Save Grouping Modal Screen"""

import json
from typing import Dict, Optional

from textual.screen import ModalScreen
from textual.widgets import Static, Input
from textual.containers import Container
from textual.binding import Binding
from textual.app import ComposeResult

from common.paths import get_user_favorites_file


class SaveGroupingModal(ModalScreen):
    """Modal screen for saving airports/groupings as a user favorite"""

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

    def __init__(
        self,
        airport_list: list,
        resolve_count: Optional[int] = None,
        filters: Optional[Dict[str, str]] = None,
        prefill_name: Optional[str] = None,
    ):
        """
        Args:
            airport_list: List of airport ICAOs and/or grouping names to save.
            resolve_count: Total resolved airport count (shown when items
                include grouping references that expand to more airports).
            filters: Per-airport dep/arr filters to persist. Maps ICAO to
                "dep" or "arr". None or empty means no filters.
            prefill_name: Pre-fill the name input (used when editing an
                existing favorite).
        """
        super().__init__()
        self.airport_list = airport_list
        self.resolve_count = resolve_count
        self.filters = filters or {}
        self.prefill_name = prefill_name

    def compose(self) -> ComposeResult:
        item_count = len(self.airport_list)
        hint_parts = [f"This will save {item_count} airports/groupings"]
        if self.resolve_count is not None and self.resolve_count != item_count:
            hint_parts.append(f" (resolves to {self.resolve_count} airports)")
        hint_parts.append(
            " to favorites\nPress Enter to save, Escape to cancel"
        )

        with Container(id="save-grouping-container"):
            yield Static("Save as Favorite", id="save-grouping-title")
            with Container(id="save-grouping-input-container"):
                yield Input(
                    placeholder="Enter grouping name (e.g., My Airports)",
                    id="save-grouping-input",
                    value=self.prefill_name or "",
                )
            yield Static("", id="save-grouping-result")
            yield Static("".join(hint_parts), id="save-grouping-hint")

    def on_mount(self) -> None:
        """Focus the input when mounted"""
        grouping_input = self.query_one("#save-grouping-input", Input)
        grouping_input.focus()

    def action_save_grouping(self) -> None:
        """Save the grouping to the user's favorites file"""
        grouping_input = self.query_one("#save-grouping-input", Input)
        grouping_name = grouping_input.value.strip()

        if not grouping_name:
            result_widget = self.query_one("#save-grouping-result", Static)
            result_widget.update("Please enter a grouping name")
            return

        favorites_file = get_user_favorites_file()

        try:
            existing_data = {}
            if favorites_file.exists():
                with open(favorites_file, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)

            if self.filters:
                existing_data[grouping_name] = {
                    "airports": sorted(self.airport_list),
                    "filters": self.filters,
                }
            else:
                existing_data[grouping_name] = sorted(self.airport_list)

            favorites_file.parent.mkdir(parents=True, exist_ok=True)

            with open(favorites_file, "w", encoding="utf-8") as f:
                json.dump(existing_data, f, indent=2, ensure_ascii=False)

            self.dismiss(grouping_name)
        except Exception as e:
            result_widget = self.query_one("#save-grouping-result", Static)
            result_widget.update(f"Error saving: {str(e)}")

    def action_close(self) -> None:
        """Close without saving"""
        self.dismiss(None)
