"""Tracked Airports Management Modal Screen"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, ClassVar, NamedTuple

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListItem, ListView, Static

from backend.core.groupings import resolve_grouping_recursively

from .airport_tracking import AirportTrackingModal
from .grouping_builder import GroupingBuilderScreen
from .grouping_draft import BuilderResult

if TYPE_CHECKING:
    from ui.app import VATSIMControlApp


class TrackingChange(NamedTuple):
    """What the manager changed in the tracked list it opened with.

    Attributes:
        added: The airports the manager added, sorted.
        removed: The airports the manager removed, sorted.
        track_everything: The manager's list ended empty, which tracks every
            airport with activity.
    """

    added: tuple[str, ...]
    removed: tuple[str, ...]
    track_everything: bool


class TrackedAirportsModal(ModalScreen):
    """Modal screen for viewing and managing all tracked airports"""

    @property
    def vatsim_app(self) -> "VATSIMControlApp":
        """Return the app with proper type hint"""
        return self.app  # type: ignore[return-value]

    CSS = """
    TrackedAirportsModal {
        align: center middle;
    }

    #tracked-container {
        width: 94;
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

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close", priority=True),
        Binding("a", "add_airports", "Add Airports", priority=True),
        Binding("delete", "remove_selected", "Remove Selected", priority=True),
        Binding("s", "save_as_grouping", "Save to my groupings", priority=True),
        Binding("space", "toggle_selected", "Select", show=False),
    ]

    def __init__(self, airport_allowlist: list, disambiguator=None):
        super().__init__()
        self.airport_allowlist = sorted(airport_allowlist) if airport_allowlist else []
        # Closing reports changes against this list, not the whole list, so an
        # airport tracked elsewhere while the manager is open is not dropped
        self._opened_with = frozenset(self.airport_allowlist)
        self.disambiguator = disambiguator
        self.selected_airports = set()
        # The groupings the open builder saw, to count a saved grouping's airports
        self._builder_groupings: Mapping[str, Sequence[str]] = {}

    def compose(self) -> ComposeResult:
        tracking_mode = (
            "Tracking specific airports"
            if self.airport_allowlist
            else "Tracking all airports with activity"
        )
        airport_count = len(self.airport_allowlist) if self.airport_allowlist else "all"

        with Container(id="tracked-container"):
            yield Static("Tracked Airports Manager", id="tracked-title")
            yield Static(
                f"{tracking_mode} ({airport_count} airports)", id="tracked-info"
            )

            list_view = ListView(id="tracked-list")
            yield list_view

            with Horizontal(id="tracked-buttons"):
                yield Button(
                    "Add Airports (A)", id="add-button", classes="tracked-button"
                )
                yield Button(
                    "Remove Selected (Del)",
                    id="remove-button",
                    classes="tracked-button",
                )
                yield Button(
                    "Save to my groupings (S)",
                    id="save-button",
                    classes="tracked-button",
                )
                yield Button("Close (Esc)", id="close-button", classes="tracked-button")

            yield Static(
                "Use arrow keys to navigate, Space or Enter to select/deselect",
                id="tracked-hint",
            )
            yield Static("", id="tracked-status")

    async def on_mount(self) -> None:
        """Populate the list when mounted, highlighting the first row."""
        list_view = self.query_one("#tracked-list", ListView)
        list_view.focus()
        await self.populate_list(0)

    async def populate_list(self, highlight_index: int | None = None) -> None:
        """Rebuild the list and highlight the row at ``highlight_index``.

        ``clear`` and ``extend`` mount their items asynchronously, so the index
        is set only once the rows are in place; the ListView then clamps it to
        the last row and leaves it ``None`` when the list is empty.
        """
        list_view = self.query_one("#tracked-list", ListView)
        await list_view.clear()

        if not self.airport_allowlist:
            await list_view.append(
                ListItem(
                    Label("No specific airports tracked (showing all with activity)")
                )
            )
            return

        await list_view.extend(
            ListItem(Label(self._display_text(icao)), name=icao)
            for icao in self.airport_allowlist
        )
        if highlight_index is not None:
            list_view.index = highlight_index

    def _display_text(self, icao: str) -> str:
        """The row text for one airport, marked when it is picked."""
        pretty_name = (
            self.disambiguator.get_full_name(icao) if self.disambiguator else icao
        )
        display_text = f"{icao} - {pretty_name}"
        if icao in self.selected_airports:
            display_text = f"[✓] {display_text}"
        return display_text

    def _highlighted_icao(self) -> str | None:
        """The ICAO of the highlighted row, or None when nothing is highlighted."""
        item = self.query_one("#tracked-list", ListView).highlighted_child
        return item.name if item is not None else None

    def _toggle_airport(self, item: ListItem) -> None:
        """Flip one row's picked state and redraw that row alone.

        Redrawing in place rather than rebuilding the list keeps the highlight
        where it was: ``populate_list`` clears the ListView, which drops the
        highlighted index.
        """
        if not item.name:
            return

        icao = item.name
        if icao in self.selected_airports:
            self.selected_airports.remove(icao)
        else:
            self.selected_airports.add(icao)

        item.query_one(Label).update(self._display_text(icao))

    def action_toggle_selected(self) -> None:
        """Space: toggle the highlighted airport, leaving the cursor on its row."""
        list_view = self.query_one("#tracked-list", ListView)
        if self.focused is not list_view:
            return

        item = list_view.highlighted_child
        if item is not None:
            self._toggle_airport(item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter: toggle the selected airport, leaving the cursor on its row."""
        self._toggle_airport(event.item)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses"""
        if event.button.id == "add-button":
            self.action_add_airports()
        elif event.button.id == "remove-button":
            await self.action_remove_selected()
        elif event.button.id == "save-button":
            self.action_save_as_grouping()
        elif event.button.id == "close-button":
            self.action_close()

    def action_add_airports(self) -> None:
        """Open the quick add/remove modal as a sub-modal"""
        self.app.push_screen(
            AirportTrackingModal(), callback=self.handle_quick_tracking_result
        )

    async def handle_quick_tracking_result(self, result) -> None:
        """Handle result from quick tracking modal"""
        if result is None:
            return

        airports_to_add, airports_to_remove = result
        previous_icao = self._highlighted_icao()
        previous_index = self.query_one("#tracked-list", ListView).index
        newly_added: list[str] = []

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
                        newly_added.append(icao)
                self.airport_allowlist.sort()

        if airports_to_remove:
            for icao in airports_to_remove:
                if icao in self.airport_allowlist:
                    self.airport_allowlist.remove(icao)
                if icao in self.selected_airports:
                    self.selected_airports.remove(icao)

        # Highlight the first airport added, in list order; when nothing was
        # added, keep the highlighted airport where it was.
        if newly_added:
            highlight = min(self.airport_allowlist.index(icao) for icao in newly_added)
        elif previous_icao is not None and previous_icao in self.airport_allowlist:
            highlight = self.airport_allowlist.index(previous_icao)
        else:
            highlight = previous_index

        # Update the display
        await self.populate_list(highlight)
        self._update_info()

    def _update_info(self) -> None:
        """Say how many airports the list tracks, or that it tracks them all."""
        tracking_mode = (
            "Tracking specific airports"
            if self.airport_allowlist
            else "Tracking all airports with activity"
        )
        airport_count = len(self.airport_allowlist) if self.airport_allowlist else "all"
        self.query_one("#tracked-info", Static).update(
            f"{tracking_mode} ({airport_count} airports)"
        )

    async def action_remove_selected(self) -> None:
        """Remove selected airports from tracking"""
        if not self.selected_airports:
            return

        previous_index = self.query_one("#tracked-list", ListView).index

        for icao in self.selected_airports:
            if icao in self.airport_allowlist:
                self.airport_allowlist.remove(icao)

        self.selected_airports.clear()
        await self.populate_list(previous_index)
        self._update_info()

    def action_save_as_grouping(self) -> None:
        """Open the grouping builder on the picked airports (the whole list when none)."""
        airports = sorted(self.selected_airports) or self.airport_allowlist
        if not airports:
            status_widget = self.query_one("#tracked-status", Static)
            status_widget.update("No airports to save (tracking all airports)")
            return

        # Until the manager closes, its own list is the tracked one
        context = replace(
            self.vatsim_app.builder_context(),
            tracked=frozenset(self.airport_allowlist),
        )
        self._builder_groupings = context.groupings
        builder = GroupingBuilderScreen(
            context, items=list(airports), filters={}, name="", original_name=None
        )
        self.app.push_screen(builder, callback=self.handle_builder_result)

    async def handle_builder_result(self, result: BuilderResult | None) -> None:
        """Apply a builder save: list the airports it tracks, then report it."""
        if result is None:
            return
        if result.track:
            await self._track(result.track)
        # The Go To modal caches its results; a new ★ grouping must show up
        self.vatsim_app.invalidate_goto_cache()

        saved = result.saved
        groupings = {**self._builder_groupings, saved.name: list(saved.items)}
        count = len(resolve_grouping_recursively(saved.name, groupings))
        self.query_one("#tracked-status", Static).update(
            f"Saved '{saved.name}' with {count} airports"
        )

    async def _track(self, icaos: Sequence[str]) -> None:
        """Add airports to the manager's list; the app gets it when the manager closes."""
        previous_index = self.query_one("#tracked-list", ListView).index
        self.airport_allowlist = sorted({*self.airport_allowlist, *icaos})
        await self.populate_list(previous_index)
        self._update_info()

    def tracking_change(self) -> TrackingChange:
        """The airports added and removed since the manager opened."""
        final = set(self.airport_allowlist)
        return TrackingChange(
            added=tuple(sorted(final - self._opened_with)),
            removed=tuple(sorted(self._opened_with - final)),
            track_everything=not final,
        )

    def action_close(self) -> None:
        """Close and return what changed in the tracked list."""
        self.dismiss(self.tracking_change())
