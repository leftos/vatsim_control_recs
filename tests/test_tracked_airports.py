"""The Tracked Airports Manager: Space toggles a row, S saves the picks."""

import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from textual.app import App
from textual.pilot import Pilot
from textual.widgets import Input, Label, ListView, Static

import common.paths
from ui.modals.tracked_airports import TrackedAirportsModal

AIRPORTS = ["KSFO", "KOAK", "KSJC"]


class TrackedHost(App):
    """The smallest app the modal can run under."""


@dataclass
class Harness:
    """A mounted Tracked Airports modal and the handles a test drives it with."""

    app: TrackedHost
    pilot: Pilot
    modal: TrackedAirportsModal
    favorites_file: Path

    @property
    def list_view(self) -> ListView:
        return self.modal.query_one("#tracked-list", ListView)

    @property
    def status(self) -> str:
        return str(self.modal.query_one("#tracked-status", Static).content)

    def index_of(self, icao: str) -> int:
        """The row index of an airport; the modal shows the list sorted."""
        for index, item in enumerate(self.list_view.children):
            if item.name == icao:
                return index
        raise AssertionError(f"{icao} is not in the tracked list")

    def row_text(self, icao: str) -> str:
        item = self.list_view.children[self.index_of(icao)]
        return str(item.query_one(Label).content)

    async def highlight(self, icao: str) -> None:
        """Focus the list and highlight this airport's row."""
        self.list_view.focus()
        self.list_view.index = self.index_of(icao)
        await self.pilot.pause()

    async def save_as(self, name: str) -> None:
        """Press S, type a name into the save dialog, then press Enter."""
        await self.pilot.press("s")
        await self.pilot.pause()
        self.app.screen.query_one("#save-grouping-input", Input).value = name
        await self.pilot.press("enter")
        await self.pilot.pause()

    async def quick_add(self, text: str) -> None:
        """Press A, type into the quick add box, then press Enter."""
        await self.pilot.press("a")
        await self.pilot.pause()
        self.app.screen.query_one("#tracking-input", Input).value = text
        await self.pilot.press("enter")
        await self.pilot.pause()
        await self.pilot.pause()

    def saved_favorites(self) -> dict:
        return json.loads(self.favorites_file.read_text(encoding="utf-8"))


TrackedFactory = Callable[..., AbstractAsyncContextManager[Harness]]


@pytest.fixture
def tracked_modal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TrackedFactory:
    """Return a factory that mounts the modal headless on an isolated data dir."""
    user_data_dir = tmp_path / "VATSIMControlRecs"
    user_data_dir.mkdir()
    favorites_file = user_data_dir / "favorites.json"
    monkeypatch.setattr(common.paths, "get_user_data_dir", lambda: user_data_dir)

    @asynccontextmanager
    async def mount(airports: list[str] | None = None) -> AsyncIterator[Harness]:
        app = TrackedHost()
        async with app.run_test(size=(120, 40)) as pilot:
            modal = TrackedAirportsModal(AIRPORTS if airports is None else airports)
            await app.push_screen(modal)
            await pilot.pause()
            yield Harness(app, pilot, modal, favorites_file)

    return mount


async def test_space_selects_the_highlighted_airport(
    tracked_modal: TrackedFactory,
) -> None:
    async with tracked_modal() as h:
        await h.highlight("KSFO")
        await h.pilot.press("space")
        assert h.modal.selected_airports == {"KSFO"}
        assert h.row_text("KSFO").startswith("[✓]")


async def test_space_leaves_the_cursor_on_the_toggled_row(
    tracked_modal: TrackedFactory,
) -> None:
    async with tracked_modal() as h:
        await h.highlight("KSJC")
        await h.pilot.press("space")
        assert h.list_view.index == h.index_of("KSJC")
        assert h.modal.selected_airports == {"KSJC"}


async def test_s_saves_only_the_picked_airports(
    tracked_modal: TrackedFactory,
) -> None:
    async with tracked_modal() as h:
        await h.highlight("KOAK")
        await h.pilot.press("enter")
        await h.save_as("Test")
        assert h.saved_favorites() == {"Test": ["KOAK"]}
        assert h.status == "Saved 'Test' with 1 airports"


async def test_s_with_nothing_picked_saves_the_whole_list(
    tracked_modal: TrackedFactory,
) -> None:
    async with tracked_modal() as h:
        await h.save_as("All")
        assert h.saved_favorites() == {"All": ["KOAK", "KSFO", "KSJC"]}
        assert h.status == "Saved 'All' with 3 airports"


async def test_opens_with_first_row_highlighted(
    tracked_modal: TrackedFactory,
) -> None:
    async with tracked_modal() as h:
        assert h.list_view.index == 0
        assert h.list_view.highlighted_child is not None
        assert h.list_view.highlighted_child.name == "KOAK"


async def test_removal_highlights_the_row_that_takes_its_place(
    tracked_modal: TrackedFactory,
) -> None:
    async with tracked_modal() as h:
        await h.highlight("KSFO")  # index 1, a middle row
        await h.pilot.press("space")
        await h.pilot.pause()
        await h.pilot.press("delete")
        await h.pilot.pause()
        assert h.list_view.index == 1
        assert h.list_view.highlighted_child.name == "KSJC"

    async with tracked_modal() as h:
        await h.highlight("KSJC")  # index 2, the last row
        await h.pilot.press("space")
        await h.pilot.pause()
        await h.pilot.press("delete")
        await h.pilot.pause()
        assert h.list_view.index == 1
        assert h.list_view.highlighted_child.name == "KSFO"


async def test_quick_add_highlights_first_added_airport(
    tracked_modal: TrackedFactory,
) -> None:
    async with tracked_modal() as h:
        await h.quick_add("+KSLC +KSEA")
        assert h.list_view.index == h.index_of("KSEA")
        assert h.list_view.highlighted_child.name == "KSEA"


async def test_quick_add_of_nothing_new_keeps_the_highlighted_airport(
    tracked_modal: TrackedFactory,
) -> None:
    async with tracked_modal() as h:
        await h.highlight("KSJC")
        await h.quick_add("+KSJC")
        assert h.list_view.index == h.index_of("KSJC")
        assert h.list_view.highlighted_child.name == "KSJC"
