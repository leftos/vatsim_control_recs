"""Shared fixtures: a headless Textual host that mounts GoToScreen on fixture data.

Only the network (the VATSIM feed download) and the user data directory are
replaced; groupings, favorites and the Go To results cache go through the
project's own code.
"""

import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from textual.app import App
from textual.notifications import SeverityLevel
from textual.pilot import Pilot
from textual.widgets import Input, OptionList, Static

import common.paths
from backend.core.groupings import load_user_favorites
from ui.app import VATSIMControlApp
from ui.modals import goto_modal as goto_modal_module
from ui.modals.goto_modal import GoToScreen

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
        # Every toast as (severity, message); Textual reaps shown toasts after
        # their timeout, so a stalled run could otherwise miss one
        self.toasts: list[tuple[str, str]] = []
        # The real cache builder; GoToHost carries every attribute it reads
        VATSIMControlApp._build_goto_results(self)  # type: ignore[arg-type]

    def invalidate_goto_cache(self) -> None:
        """Record the invalidation; the real app re-warms from the network."""
        self.invalidate_calls += 1
        self.goto_cache_ready = False

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
