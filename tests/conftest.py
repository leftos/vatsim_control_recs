"""Shared fixtures: headless Textual hosts that mount GoToScreen and the grouping
builder on fixture data.

Only the network (the VATSIM feed download) and the user data directory are
replaced; groupings, favorites and the Go To results cache go through the
project's own code.
"""

import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from textual.app import App
from textual.notifications import SeverityLevel
from textual.pilot import Pilot
from textual.widgets import Input, OptionList, Static

import common.paths
from backend.core.airport_search import AirportSearchIndex
from backend.core.groupings import (
    airport_idents,
    get_user_favorite_names,
    load_user_favorites,
)
from ui import config as ui_config
from ui.app import VATSIMControlApp
from ui.modals import goto_modal as goto_modal_module
from ui.modals.goto_modal import GoToScreen
from ui.modals.grouping_draft import BuilderContext

if TYPE_CHECKING:
    from ui.modals.grouping_builder import GroupingBuilderScreen

TRACKED_AIRPORTS = ["KSFO", "KOAK", "KSJC", "KLAX", "KSAN", "KSMF"]

# "California" nests the "Bay Area" grouping, like the ARTCC preset files do.
PRESET_GROUPINGS: dict[str, list[str]] = {
    "Bay Area": ["KSFO", "KOAK", "KSJC"],
    "California": ["Bay Area", "KLAX", "KSAN"],
}

FAVORITE_NAME = "My Ops"

# One favorite mixing a grouping reference with an individually picked airport,
# with a filter on each kind: KOAK is a "Bay Area" member, KSMF a direct pick.
FAVORITES: dict[str, Any] = {
    FAVORITE_NAME: {
        "airports": ["Bay Area", "KSMF"],
        "filters": {"KOAK": "arr", "KSMF": "dep"},
    },
}

PILOTS: list[dict[str, Any]] = [
    {
        "callsign": "UAL1",
        "flight_plan": {"departure": "KSFO", "arrival": "KLAX"},
    },
    {
        "callsign": "SWA22",
        "flight_plan": {"departure": "KOAK", "arrival": "KSAN"},
    },
]


class GoToHost(App):
    """Stands in for VATSIMControlApp with the attributes GoToScreen reads."""

    def __init__(self) -> None:
        super().__init__()
        self.args = None
        self.refresh_interval = 5
        self.flight_board_open = False
        self.active_flight_board = None
        self.airport_allowlist = list(TRACKED_AIRPORTS)
        self.cached_pilots = list(PILOTS)
        self.cached_groupings = {**PRESET_GROUPINGS, **load_user_favorites()}
        self.cached_goto_results: list[tuple[str, str, Any]] = []
        self.goto_cache_ready = False
        self.invalidate_calls = 0
        # Every track_airports call's airports, in call order
        self.track_calls: list[tuple[str, ...]] = []
        # Every toast as (severity, message); Textual reaps shown toasts after
        # their timeout, so a stalled run could otherwise miss one
        self.toasts: list[tuple[str, str]] = []
        # The real cache builder; GoToHost carries every attribute it reads
        VATSIMControlApp._build_goto_results(self)  # type: ignore[arg-type]

    def invalidate_goto_cache(self) -> None:
        """Record the invalidation; the real app re-warms from the network."""
        self.invalidate_calls += 1
        self.goto_cache_ready = False

    def track_airports(self, icaos: Sequence[str]) -> bool:
        """Record the airports and extend the list as the real app does.

        The real app's refresh is left out; its Go To cache invalidation is
        counted, so a test sees one warm-up per change.
        """
        self.track_calls.append(tuple(icaos))
        new = [icao for icao in icaos if icao not in self.airport_allowlist]
        if not self.airport_allowlist or not new:
            return False
        self.airport_allowlist = sorted({*self.airport_allowlist, *new})
        self.invalidate_goto_cache()
        return True

    def builder_context(self) -> BuilderContext:
        """The grouping builder's context over the fixture data."""
        return BuilderContext(
            groupings=self.cached_groupings,
            user_names=frozenset(get_user_favorite_names()),
            tracked=frozenset(self.airport_allowlist),
            search_index=AirportSearchIndex(GOTO_AIRPORTS),
            preset_names=frozenset(PRESET_GROUPINGS),
            airport_idents=frozenset(airport_idents(GOTO_AIRPORTS)),
        )

    def open_grouping_board(self, name: str) -> None:
        """Open a grouping board the way the real app does."""
        # The host carries every attribute the helper reads
        VATSIMControlApp.open_grouping_board(self, name)  # type: ignore[arg-type]

    def notify(
        self,
        message: str,
        *,
        title: str = "",
        severity: SeverityLevel = "information",
        timeout: float | None = None,
        markup: bool = True,
    ) -> None:
        self.toasts.append((severity, message))
        super().notify(
            message, title=title, severity=severity, timeout=timeout, markup=markup
        )


@dataclass
class GoToHarness:
    """A mounted GoToScreen and the handles a test drives it through."""

    app: GoToHost
    pilot: Pilot
    screen: GoToScreen
    favorites_file: Path

    @property
    def search(self) -> Input:
        return self.screen.query_one("#goto-input", Input)

    @property
    def option_list(self) -> OptionList:
        return self.screen.query_one("#goto-list", OptionList)

    @property
    def title(self) -> str:
        return str(self.screen.query_one("#goto-title", Static).content)

    @property
    def hint(self) -> str:
        return str(self.screen.query_one("#goto-hint", Static).content)

    async def type_query(self, text: str) -> None:
        """Replace the search box text, as typing it would."""
        self.search.value = text
        await self.pilot.pause()

    async def focus_row(self, option_id: str) -> None:
        """Focus the list and highlight the row with this option id."""
        index = self.option_list.get_option_index(option_id)
        self.option_list.focus()
        self.option_list.highlighted = index
        await self.pilot.pause()

    def row_label(self, option_id: str) -> str:
        prompt = self.option_list.get_option(option_id).prompt
        return getattr(prompt, "plain", str(prompt))

    def notifications(self) -> list[tuple[str, str]]:
        """Return every toast so far as (severity, message) pairs."""
        return list(self.app.toasts)

    def saved_favorites(self) -> dict[str, Any]:
        return json.loads(self.favorites_file.read_text(encoding="utf-8"))

    def builder(self) -> "BuilderHarness":
        """Handles on the grouping builder Go To opened above itself."""
        from ui.modals.grouping_builder import GroupingBuilderScreen

        screen = self.app.screen
        assert isinstance(screen, GroupingBuilderScreen), f"{screen!r} is on top"
        return BuilderHarness(self.app, self.pilot, screen, [], self.favorites_file)


GoToFactory = Callable[..., AbstractAsyncContextManager[GoToHarness]]


@pytest.fixture
def goto_modal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GoToFactory:
    """Return a factory that mounts GoToScreen headless on the fixture data.

    The factory is an async context manager so the Textual app runs in the
    test's own task. Keyword arguments are passed to GoToScreen.
    """
    user_data_dir = tmp_path / "VATSIMControlRecs"
    user_data_dir.mkdir()
    favorites_file = user_data_dir / "favorites.json"
    favorites_file.write_text(json.dumps(FAVORITES, indent=2), encoding="utf-8")
    monkeypatch.setattr(common.paths, "get_user_data_dir", lambda: user_data_dir)

    downloads: list[str] = []

    def no_network() -> None:
        downloads.append("download_vatsim_data")
        raise AssertionError("Go To tests must not download the VATSIM feed")

    monkeypatch.setattr(goto_modal_module, "download_vatsim_data", no_network)

    @asynccontextmanager
    async def mount(**screen_kwargs: Any) -> AsyncIterator[GoToHarness]:
        app = GoToHost()
        async with app.run_test(size=(120, 40)) as pilot:
            screen = GoToScreen(**screen_kwargs)
            await app.push_screen(screen)
            await pilot.pause()
            await screen.workers.wait_for_complete()
            await pilot.pause()
            yield GoToHarness(app, pilot, screen, favorites_file)
        assert downloads == [], "GoToScreen called download_vatsim_data"

    return mount


# --- the grouping builder ----------------------------------------------------

# The unified airport data the builder searches; KSAC is the one airport the
# default tracked list lacks.
BUILDER_AIRPORTS: dict[str, dict[str, Any]] = {
    "KSFO": {
        "icao": "KSFO",
        "iata": "SFO",
        "faa": "SFO",
        "name": "San Francisco Intl",
        "city": "San Francisco",
        "country": "US",
    },
    "KOAK": {
        "icao": "KOAK",
        "iata": "OAK",
        "faa": "OAK",
        "name": "Oakland Intl",
        "city": "Oakland",
        "country": "US",
    },
    "KSJC": {
        "icao": "KSJC",
        "iata": "SJC",
        "faa": "SJC",
        "name": "San Jose Intl",
        "city": "San Jose",
        "country": "US",
    },
    "KSMF": {
        "icao": "KSMF",
        "iata": "SMF",
        "faa": "SMF",
        "name": "Sacramento Intl",
        "city": "Sacramento",
        "country": "US",
    },
    "KSAC": {
        "icao": "KSAC",
        "iata": "SAC",
        "faa": "SAC",
        "name": "Sacramento Executive",
        "city": "Sacramento",
        "country": "US",
    },
}

BUILDER_TRACKED = ("KSFO", "KOAK", "KSJC", "KSMF")

# The unified airport data behind Go To's builder: every tracked airport, plus
# KSAC, which Go To's tracked list lacks.
GOTO_AIRPORTS: dict[str, dict[str, Any]] = {
    **BUILDER_AIRPORTS,
    "KLAX": {
        "icao": "KLAX",
        "iata": "LAX",
        "faa": "LAX",
        "name": "Los Angeles Intl",
        "city": "Los Angeles",
        "country": "US",
    },
    "KSAN": {
        "icao": "KSAN",
        "iata": "SAN",
        "faa": "SAN",
        "name": "San Diego Intl",
        "city": "San Diego",
        "country": "US",
    },
}

# The one preset grouping; the ★ one is FAVORITE_NAME from the favorites file.
BUILDER_PRESET = "Bay Area"
BUILDER_PRESETS: dict[str, list[str]] = {BUILDER_PRESET: ["KSFO", "KOAK", "KSJC"]}


class BuilderHost(App):
    """The smallest app the builder runs under, recording every toast."""

    def __init__(self) -> None:
        super().__init__()
        self.toasts: list[tuple[str, str]] = []

    def notify(
        self,
        message: str,
        *,
        title: str = "",
        severity: SeverityLevel = "information",
        timeout: float | None = None,
        markup: bool = True,
    ) -> None:
        self.toasts.append((severity, message))
        super().notify(
            message, title=title, severity=severity, timeout=timeout, markup=markup
        )


def _plain(prompt: Any) -> str:
    """An option prompt's text without its styles."""
    return getattr(prompt, "plain", str(prompt))


@dataclass
class BuilderHarness:
    """A mounted grouping builder and the handles a test drives it through."""

    app: BuilderHost
    pilot: Pilot
    screen: "GroupingBuilderScreen"
    results: list[Any]
    favorites_file: Path

    @property
    def name_input(self) -> Input:
        return self.screen.query_one("#builder-name", Input)

    @property
    def search(self) -> Input:
        return self.screen.query_one("#builder-search", Input)

    @property
    def results_list(self) -> OptionList:
        return self.screen.query_one("#builder-results", OptionList)

    @property
    def members_list(self) -> OptionList:
        return self.screen.query_one("#builder-members", OptionList)

    @property
    def name_error(self) -> Static:
        return self.screen.query_one("#builder-name-error", Static)

    @property
    def hint(self) -> str:
        return str(self.screen.query_one("#builder-hint", Static).content)

    async def press(self, *keys: str) -> None:
        await self.pilot.press(*keys)
        await self.pilot.pause()

    async def type_name(self, text: str) -> None:
        """Replace the Name field's text, as typing it would."""
        self.name_input.value = text
        await self.pilot.pause()

    async def type_query(self, text: str) -> None:
        """Replace the Add search's text, as typing it would."""
        self.search.value = text
        await self.pilot.pause()

    async def focus_member(self, option_id: str) -> None:
        """Focus the Members list and highlight the row with this option id."""
        await self._focus_row(self.members_list, option_id)

    async def focus_result(self, option_id: str) -> None:
        """Focus the Add results and highlight the row with this option id."""
        await self._focus_row(self.results_list, option_id)

    async def _focus_row(self, option_list: OptionList, option_id: str) -> None:
        index = option_list.get_option_index(option_id)
        option_list.focus()
        option_list.highlighted = index
        await self.pilot.pause()

    def member_ids(self) -> list[str | None]:
        return [option.id for option in self.members_list.options]

    def result_ids(self) -> list[str | None]:
        return [option.id for option in self.results_list.options]

    def member_label(self, option_id: str) -> str:
        return _plain(self.members_list.get_option(option_id).prompt)

    def result_label(self, option_id: str) -> str:
        return _plain(self.results_list.get_option(option_id).prompt)

    def member_texts(self) -> list[str]:
        return [_plain(option.prompt) for option in self.members_list.options]

    def result_texts(self) -> list[str]:
        return [_plain(option.prompt) for option in self.results_list.options]

    def highlighted_member_id(self) -> str | None:
        option = self.members_list.highlighted_option
        return None if option is None else option.id

    def notifications(self) -> list[tuple[str, str]]:
        """Return every toast so far as (severity, message) pairs."""
        return list(self.app.toasts)

    def saved_favorites(self) -> dict[str, Any]:
        return json.loads(self.favorites_file.read_text(encoding="utf-8"))

    @property
    def is_open(self) -> bool:
        """Whether the builder is still the active screen."""
        return self.app.screen is self.screen


BuilderFactory = Callable[..., AbstractAsyncContextManager[BuilderHarness]]


@pytest.fixture
def grouping_builder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BuilderFactory:
    """Return a factory that mounts GroupingBuilderScreen headless on fixture data.

    The favorites file lives in a per-test user data directory and starts with
    FAVORITES (or the `favorites` the factory is given); the context's groupings
    are BUILDER_PRESETS plus that file's entries, as the app merges them.
    """
    user_data_dir = tmp_path / "VATSIMControlRecs"
    user_data_dir.mkdir()
    favorites_file = user_data_dir / "favorites.json"
    monkeypatch.setattr(common.paths, "get_user_data_dir", lambda: user_data_dir)

    @asynccontextmanager
    async def mount(
        *,
        size: tuple[int, int] = (120, 40),
        items: Sequence[str] = (),
        filters: Mapping[str, str] | None = None,
        name: str = "",
        original_name: str | None = None,
        tracked: Sequence[str] = BUILDER_TRACKED,
        airports: dict[str, dict[str, Any]] | None = None,
        loaded: bool = True,
        favorites: dict[str, Any] | None = None,
    ) -> AsyncIterator[BuilderHarness]:
        from ui.modals.grouping_builder import GroupingBuilderScreen

        unified = BUILDER_AIRPORTS if airports is None else airports
        monkeypatch.setattr(ui_config, "UNIFIED_AIRPORT_DATA", unified)
        favorites_file.write_text(
            json.dumps(FAVORITES if favorites is None else favorites, indent=2),
            encoding="utf-8",
        )
        user_groupings = load_user_favorites()
        context = BuilderContext(
            groupings={**BUILDER_PRESETS, **user_groupings},
            user_names=frozenset(user_groupings),
            tracked=frozenset(tracked),
            search_index=AirportSearchIndex(unified) if loaded else None,
            preset_names=frozenset(BUILDER_PRESETS),
            airport_idents=frozenset(airport_idents(unified)),
        )
        screen = GroupingBuilderScreen(
            context,
            items=items,
            filters=dict(filters or {}),
            name=name,
            original_name=original_name,
        )
        results: list[Any] = []
        app = BuilderHost()
        async with app.run_test(size=size) as pilot:
            await app.push_screen(screen, callback=results.append)
            await pilot.pause()
            yield BuilderHarness(app, pilot, screen, results, favorites_file)

    return mount
