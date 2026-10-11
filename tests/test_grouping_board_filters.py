"""A grouping board carries the saved filters of the ★ grouping that opened it.

The Groupings tab and Go To both open a grouping board through
``VATSIMControlApp.open_grouping_board``, so a ★ grouping's per-airport dep/arr
filters follow it whichever way it is opened; a preset grouping opens unfiltered.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from textual.pilot import Pilot
from textual.widgets import TabbedContent

import common.paths
from backend.core.groupings import load_user_favorites
from backend.core.models import GroupingStats
from tests.conftest import (
    FAVORITE_NAME,
    FAVORITES,
    PRESET_GROUPINGS,
    GoToFactory,
)
from ui.app import VATSIMControlApp
from ui.modals import flight_board as flight_board_module
from widgets.split_flap_datatable import SplitFlapDataTable

# The ★ grouping written to favorites.json for the tab-path tests: KOAK is
# arrival-only while KSFO is unfiltered, so the board classifies its filters as
# "custom" and keeps the saved dict as they are.
STARRED_GROUPING: dict[str, Any] = {
    "airports": ["KSFO", "KOAK"],
    "filters": {"KOAK": "arr"},
}
STARRED_FILTERS = {"KOAK": "arr"}

PRESET_NAME = "Bay Area"

ARGS = SimpleNamespace(
    include_all_staffed=False,
    hide_wind=False,
    include_all_arriving=False,
    disable_animations=True,
    max_eta_hours=1.0,
    groupings=None,
    refresh_interval=0,
)


def _grouping_rows(name: str) -> list[GroupingStats]:
    """One groupings-table row, as the analysis would produce it."""
    return [
        GroupingStats(
            name=name,
            total=2,
            departures=1,
            arrivals=1,
            arrivals_all=1,
            next_eta="",
            staffed="N/A",
        )
    ]


def _isolate_board_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the flight board's network boundary: feed, METARs and D-ATIS."""
    monkeypatch.setattr(flight_board_module, "download_vatsim_data", dict)
    monkeypatch.setattr(flight_board_module, "get_metar_batch", lambda _airports: {})
    monkeypatch.setattr(
        flight_board_module, "get_datis_for_airports", lambda _airports: {}
    )


@asynccontextmanager
async def _app_on_a_grouping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, grouping_name: str
) -> AsyncIterator[tuple[VATSIMControlApp, Pilot]]:
    """Mount the real app listing one grouping row, with the user data isolated."""
    user_data_dir = tmp_path / "VATSIMControlRecs"
    user_data_dir.mkdir()
    (user_data_dir / "favorites.json").write_text(
        json.dumps({FAVORITE_NAME: STARRED_GROUPING}, indent=2), encoding="utf-8"
    )
    monkeypatch.setattr(common.paths, "get_user_data_dir", lambda: user_data_dir)

    from backend.data import vatsim_api

    monkeypatch.setattr(vatsim_api, "download_vatsim_data", lambda: {"pilots": []})
    _isolate_board_network(monkeypatch)

    app = VATSIMControlApp(
        airport_data=[],
        groupings_data=_grouping_rows(grouping_name),
        total_flights=0,
        args=ARGS,
        airport_allowlist=["KSFO", "KOAK", "KSJC"],
    )
    app.refresh_interval = 3600  # no auto-refresh inside the test

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        # The warm-up has landed; pin the grouping sources it loaded
        app.cached_groupings = {**PRESET_GROUPINGS, **load_user_favorites()}
        yield app, pilot


async def _open_from_groupings_tab(app: VATSIMControlApp, pilot: Pilot) -> None:
    """Select the Groupings tab's only row and open its flight board."""
    app.query_one("#tabs", TabbedContent).active = "groupings"
    await pilot.pause()

    groupings_table = app.query_one("#groupings-table", SplitFlapDataTable)
    groupings_table.move_cursor(row=0)
    await pilot.pause()

    app.action_open_flight_board()
    await pilot.pause()


async def test_starred_grouping_opens_with_its_saved_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _app_on_a_grouping(tmp_path, monkeypatch, FAVORITE_NAME) as (
        app,
        pilot,
    ):
        await _open_from_groupings_tab(app, pilot)

        assert app.active_flight_board is not None
        assert app.active_flight_board.airport_filters == STARRED_FILTERS


async def test_preset_grouping_opens_with_no_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _app_on_a_grouping(tmp_path, monkeypatch, PRESET_NAME) as (
        app,
        pilot,
    ):
        await _open_from_groupings_tab(app, pilot)

        assert app.active_flight_board is not None
        assert not app.active_flight_board.airport_filters


async def test_go_to_open_still_applies_filters(
    goto_modal: GoToFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_board_network(monkeypatch)

    async with goto_modal() as h:
        h.screen._open_grouping(FAVORITE_NAME)
        await h.pilot.pause()

        assert h.app.active_flight_board is not None
        assert (
            h.app.active_flight_board.airport_filters
            == FAVORITES[FAVORITE_NAME]["filters"]
        )
