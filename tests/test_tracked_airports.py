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
from backend.core.airport_search import AirportSearchIndex
from backend.core.groupings import airport_idents
from tests.conftest import BUILDER_AIRPORTS, BuilderHarness
from ui.modals.confirm_modal import ChoiceModal
from ui.modals.grouping_builder import GroupingBuilderScreen
from ui.modals.grouping_draft import BuilderContext
from ui.modals.tracked_airports import TrackedAirportsModal

AIRPORTS = ["KSFO", "KOAK", "KSJC"]


class TrackedHost(App):
    """The smallest app the modal and the grouping builder can run under."""

    def __init__(self) -> None:
        super().__init__()
        self.invalidate_calls = 0
        self.airport_allowlist = list(AIRPORTS)

    def invalidate_goto_cache(self) -> None:
        """Count the Go To cache invalidations the modal asks for."""
        self.invalidate_calls += 1

    def builder_context(self) -> BuilderContext:
        """The builder's context: no groupings, the five fixture airports."""
        return BuilderContext(
            groupings={},
            user_names=frozenset(),
            tracked=frozenset(self.airport_allowlist),
            search_index=AirportSearchIndex(BUILDER_AIRPORTS),
            preset_names=frozenset(),
            airport_idents=frozenset(airport_idents(BUILDER_AIRPORTS)),
        )


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

    async def open_builder(self) -> BuilderHarness:
        """Press S and return handles on the grouping builder it opens."""
        await self.pilot.press("s")
        await self.pilot.pause()
        screen = self.app.screen
        assert isinstance(screen, GroupingBuilderScreen)
        return BuilderHarness(self.app, self.pilot, screen, [], self.favorites_file)

    async def save_as(self, name: str) -> None:
        """Press S, name the grouping in the builder, then press Ctrl+S."""
        builder = await self.open_builder()
        await builder.type_name(name)
        await builder.press("ctrl+s")
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


async def test_s_save_invalidates_goto_cache(tracked_modal: TrackedFactory) -> None:
    """A saved ★ grouping must show up in Go To, whose results are cached."""
    async with tracked_modal() as h:
        await h.save_as("Test")
        assert h.saved_favorites() == {"Test": ["KOAK", "KSFO", "KSJC"]}
        assert h.app.invalidate_calls == 1


async def test_save_with_untracked_add_extends_the_managers_list(
    tracked_modal: TrackedFactory,
) -> None:
    async with tracked_modal() as h:
        builder = await h.open_builder()
        await builder.type_query("KSAC")
        await builder.focus_result("airport:KSAC")
        await builder.press("enter")
        await builder.type_name("Trip")
        await builder.press("ctrl+s")
        assert isinstance(h.app.screen, ChoiceModal)

        await h.pilot.press("1")
        await h.pilot.pause()
        await h.pilot.pause()

        assert h.app.screen is h.modal
        assert h.saved_favorites() == {"Trip": ["KOAK", "KSFO", "KSJC", "KSAC"]}
        assert h.modal.airport_allowlist == ["KOAK", "KSAC", "KSFO", "KSJC"]
        assert h.row_text("KSAC").startswith("KSAC")
        info = str(h.modal.query_one("#tracked-info", Static).content)
        assert info == "Tracking specific airports (4 airports)"
        assert h.status == "Saved 'Trip' with 4 airports"
        # The manager applies its list to the app when it closes, not before
        assert h.app.airport_allowlist == AIRPORTS


async def test_manager_save_uses_its_own_tracked_list(
    tracked_modal: TrackedFactory,
) -> None:
    async with tracked_modal() as h:
        await h.quick_add("+KSAC")
        await h.highlight("KSAC")
        await h.pilot.press("space")

        builder = await h.open_builder()
        await builder.type_name("Sacramento")
        await builder.press("ctrl+s")

        assert not isinstance(h.app.screen, ChoiceModal)
        assert h.saved_favorites() == {"Sacramento": ["KSAC"]}
        assert h.status == "Saved 'Sacramento' with 1 airports"


async def test_cancelled_s_save_leaves_goto_cache(
    tracked_modal: TrackedFactory,
) -> None:
    async with tracked_modal() as h:
        await h.pilot.press("s")
        await h.pilot.pause()
        await h.pilot.press("escape")
        await h.pilot.pause()
        assert not h.favorites_file.exists()
        assert h.app.invalidate_calls == 0
