"""The user-facing "groupings" wording: the tab, the save button and the CLI help.

Renaming these strings leaves the identifiers alone (``save_as_grouping``,
``id="groupings"``), so these tests pin the words a user reads, never the names
the code calls itself.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from textual.app import App
from textual.containers import Horizontal
from textual.widgets import Button, TabbedContent

from main import build_arg_parser
from ui.app import VATSIMControlApp
from ui.modals.tracked_airports import TrackedAirportsModal

ARGS = SimpleNamespace(
    include_all_staffed=False,
    hide_wind=False,
    include_all_arriving=False,
    disable_animations=True,
    max_eta_hours=1.0,
    groupings=None,
    refresh_interval=0,
)


async def _empty_fetch() -> tuple[list[Any], list[Any], int]:
    """The analysis boundary, replaced so the test never leaves the process."""
    return [], [], 0


@asynccontextmanager
async def _app(monkeypatch: pytest.MonkeyPatch):
    """Mount the real app headless with the feed and analysis boundary replaced."""
    from backend.data import vatsim_api

    monkeypatch.setattr(vatsim_api, "download_vatsim_data", lambda: {"pilots": []})

    app = VATSIMControlApp(
        airport_data=[],
        groupings_data=[],
        total_flights=0,
        args=ARGS,
        airport_allowlist=[],
    )
    app.idle_timeout = 0  # never auto-pause: the idle check always passes
    app.refresh_interval = 3600  # no auto-refresh inside the test
    app.fetch_data_async = _empty_fetch

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        yield app, pilot


async def test_groupings_tab_is_titled_groupings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The main tab's label reads "Groupings"."""
    async with _app(monkeypatch) as (app, _pilot):
        tab = app.query_one("#tabs", TabbedContent).get_tab("groupings")

        assert tab.label_text == "Groupings"


class _TrackedHost(App):
    """The smallest app the tracked-airports modal mounts under."""


async def test_tracked_airports_save_button_names_my_groupings() -> None:
    """The manager's save button and its binding both say "my groupings"."""
    app = _TrackedHost()
    async with app.run_test(size=(120, 40)) as pilot:
        await app.push_screen(TrackedAirportsModal(["KSFO"]))
        await pilot.pause()

        button = app.screen.query_one("#save-button", Button)
        binding = next(b for b in TrackedAirportsModal.BINDINGS if b.key == "s")

        assert str(button.label) == "Save to my groupings (S)"
        assert binding.description == "Save to my groupings"


async def test_tracked_airports_button_row_fits_its_buttons() -> None:
    """The row is wide enough for the longer save label: no button is clipped."""
    app = _TrackedHost()
    async with app.run_test(size=(120, 40)) as pilot:
        await app.push_screen(TrackedAirportsModal(["KSFO"]))
        await pilot.pause()

        row = app.screen.query_one("#tracked-buttons", Horizontal)
        buttons = list(app.screen.query("#tracked-buttons Button"))

        assert buttons
        for button in buttons:
            assert button.region.right <= row.content_region.right


def test_groupings_cli_help_drops_custom() -> None:
    """The --groupings help names presets, ARTCC groupings and the user's own."""
    parser = build_arg_parser()
    # argparse offers no public accessor for a single action's help text.
    action = next(a for a in parser._actions if "--groupings" in a.option_strings)

    assert action.help is not None
    assert "custom" not in action.help
    assert "Custom" not in action.help
