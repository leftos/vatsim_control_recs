"""A refresh that changes the row count updates the airports table in place.

Rows are positional: the rows both refreshes share keep their RowKeys and get
their cells flapped to the new values, extra rows are appended, and rows past
the new count flap to blank and are then removed. The table is never cleared
and rebuilt, so the cursor stays where it was.
"""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult
from textual.pilot import Pilot

from backend.core.models import AirportStats
from ui.app import VATSIMControlApp
from ui.tables import (
    DEPARTURES_TABLE_CONFIG,
    TableManager,
    create_airports_table_config,
)
from widgets.split_flap_datatable import SplitFlapDataTable

# A fade flaps each visible cell to blank one character per 50 ms frame; the
# waits drive those frames themselves, so this is only the hang guard on waiting
# for the removal task a fade unblocks.
FADE_TIMEOUT = 10.0

APP_ARGS = SimpleNamespace(
    include_all_staffed=False,
    hide_wind=False,
    include_all_arriving=False,
    disable_animations=False,
    max_eta_hours=1.0,
    groupings=None,
    refresh_interval=0,
)


def _rows(
    count: int, name_suffix: str = "", staffed: str = "TWR"
) -> list[AirportStats]:
    """Airport rows with falling totals, so the table's sort keeps their order."""
    return [
        AirportStats(
            icao=f"K{i:03d}",
            name=f"AIRPORT {i}{name_suffix}",
            wind="27010KT",
            altimeter="A2992",
            total=1000 - i,
            departures=500 - i,
            arrivals=500,
            arrivals_all=500,
            next_eta="5M",
            staffed=staffed,
        )
        for i in range(count)
    ]


class _TableHost(App):
    """A bare app holding one split-flap table."""

    def __init__(self, animations: bool) -> None:
        super().__init__()
        self.animations = animations

    def compose(self) -> ComposeResult:
        yield SplitFlapDataTable(enable_animations=self.animations, id="table")


def _forbid_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any later clear-and-rebuild of the table fail the test."""

    def _clear(self: SplitFlapDataTable, columns: bool = False) -> None:
        raise AssertionError("the table was cleared and rebuilt")

    monkeypatch.setattr(SplitFlapDataTable, "clear", _clear)


async def _land(manager: TableManager, pilot: Pilot, count: int) -> None:
    """Populate ``count`` rows and wait for the update it schedules to land."""
    before = set(manager._active_tasks)
    manager.populate(_rows(count))
    await asyncio.gather(*(set(manager._active_tasks) - before))
    await pilot.pause()


def _step_animations(table: SplitFlapDataTable) -> None:
    """Drive the table's animating cells one frame at a time until none is flapping.

    A flap always takes ``delay_frames`` plus one lap of the cell's flap set plus
    one finishing frame, and the set grows by up to its own size mid-flap, since a
    character it lacks is appended the first frame that reaches for it, so twice
    ``delay_frames + len(flap_chars)`` bounds what a cell needs however slowly the
    50 ms frame timer ticks. A table with cells to flap must have a running
    animation timer, and stepping the last of them must stop it.
    """
    animating = bool(table._animating_cells)
    if animating:
        assert table._animation_timer is not None, "the animation timer did not start"
    delays = [
        table.animated_cells[key].delay_frames
        + len(table.animated_cells[key].flap_chars)
        for key in table._animating_cells
        if key in table.animated_cells
    ]
    bound = 2 * max(delays, default=0) + 1
    stepped = 0
    for _ in range(bound):
        if not table._animating_cells:
            break
        table._animate_cells()
        stepped += 1
    assert not table._animating_cells, (
        f"cells still flapping after {stepped} frames (bound {bound})"
    )
    if animating:
        assert table._animation_timer is None, "the animation timer did not stop"


async def _wait_for_fades(manager: TableManager, pilot: Pilot) -> None:
    """Wait for every fade-out the manager has running to finish."""
    while manager._active_tasks:
        _step_animations(manager.table)
        _, pending = await asyncio.wait(
            set(manager._active_tasks), timeout=FADE_TIMEOUT
        )
        assert not pending, "a fade-out did not finish"
    await pilot.pause()


def _hold_tail_removal(
    monkeypatch: pytest.MonkeyPatch, table: SplitFlapDataTable
) -> Callable[[], None]:
    """Hold every tail-row removal until the returned release is called.

    The rows still flap to blank and the widget still marks them as leaving;
    only the future that reports them blank is left unresolved, so the removal
    task waits and a test can read the mid-fade state however fast the fade
    itself runs. The widget keeps its own say: a table clear still cancels that
    future, as it does in production, so a held removal does not outlive the
    rows it was to remove.
    """
    real_resolve = table._resolve_blank_waiters
    monkeypatch.setattr(table, "_resolve_blank_waiters", lambda: None)

    def release() -> None:
        monkeypatch.setattr(table, "_resolve_blank_waiters", real_resolve)
        real_resolve()

    return release


def _cells_at_or_below(table: SplitFlapDataTable, first_row: int) -> list:
    """Index-keyed cell state the table still holds for rows >= ``first_row``."""
    return [
        cell_key
        for cells in (table.animated_cells, table._animating_cells)
        for cell_key in cells
        if cell_key[0] >= first_row
    ]


async def _populated_table(
    pilot: Pilot, count: int
) -> tuple[SplitFlapDataTable, TableManager]:
    """Fill the host's table with ``count`` rows, configured like the airports table."""
    table = pilot.app.query_one("#table", SplitFlapDataTable)
    manager = TableManager(table, create_airports_table_config(1.0), [])
    manager.populate(_rows(count))
    await pilot.pause()
    return table, manager


async def test_growing_table_keeps_existing_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _TableHost(animations=True).run_test(size=(180, 50)) as pilot:
        table, manager = await _populated_table(pilot, 20)
        keys_before = list(table.rows)
        _forbid_clear(monkeypatch)

        await _land(manager, pilot, 25)

        assert table.row_count == 25
        assert list(table.rows)[:20] == keys_before
        assert manager.row_keys == list(table.rows)


async def test_shrinking_table_fades_then_removes_tail_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _TableHost(animations=True).run_test(size=(180, 50)) as pilot:
        table, manager = await _populated_table(pilot, 25)
        keys_before = list(table.rows)
        _forbid_clear(monkeypatch)
        release = _hold_tail_removal(monkeypatch, table)

        await _land(manager, pilot, 12)

        assert table.row_count == 25, "the tail rows go only after their fade"
        for row in range(12, 25):
            for col in range(len(table.columns)):
                assert table.animated_cells[(row, col)].target_value.strip() == ""
        release()

        await _wait_for_fades(manager, pilot)

        assert table.row_count == 12
        assert list(table.rows) == keys_before[:12]
        assert manager.row_keys == list(table.rows)
        assert _cells_at_or_below(table, 12) == []


async def test_animation_wait_is_bounded_by_frames_not_wall_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both waits finish a full flap through a deadline far shorter than it takes."""
    monkeypatch.setitem(globals(), "FADE_TIMEOUT", 0.5)
    async with _TableHost(animations=True).run_test(size=(180, 50)) as pilot:
        table, manager = await _populated_table(pilot, 25)
        await _wait_for_animations(table, pilot)
        assert not table._animating_cells

        await _land(manager, pilot, 12)
        await _wait_for_fades(manager, pilot)
        await _wait_for_animations(table, pilot)

        assert table.row_count == 12
        assert not table._animating_cells


async def test_refresh_during_fade_keeps_table_consistent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _TableHost(animations=True).run_test(size=(180, 50)) as pilot:
        table, manager = await _populated_table(pilot, 25)
        keys_before = list(table.rows)
        _forbid_clear(monkeypatch)

        await _land(manager, pilot, 12)
        await _land(manager, pilot, 18)
        await _wait_for_fades(manager, pilot)

        assert table.row_count == 18
        assert list(table.rows)[:12] == keys_before[:12]
        assert manager.row_keys == list(table.rows)
        assert _cells_at_or_below(table, 18) == []


async def test_no_animation_shrink_removes_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _TableHost(animations=False).run_test(size=(180, 50)) as pilot:
        table, manager = await _populated_table(pilot, 25)
        keys_before = list(table.rows)
        _forbid_clear(monkeypatch)

        await _land(manager, pilot, 12)

        assert table.row_count == 12
        assert list(table.rows) == keys_before[:12]
        assert manager.row_keys == list(table.rows)


async def _wait_for_animations(table: SplitFlapDataTable, pilot: Pilot) -> None:
    """Wait until no cell is flapping, then for the width pass that follows."""
    await pilot.pause()
    _step_animations(table)
    await pilot.pause()
    await pilot.pause()


def _column_width(table: SplitFlapDataTable, label: str) -> int:
    """Content width of the column with this label."""
    (column,) = [c for c in table.columns.values() if str(c.label) == label]
    return column.content_width


@pytest.mark.parametrize("animations", [True, False])
async def test_column_widths_follow_the_widest_cell(animations: bool) -> None:
    """A column narrows when its widest text goes and widens for wider text."""
    long_suffix = " WITH A VERY LONG NAME"
    long_staffed = "TWR, GND, DEL, APP"
    async with _TableHost(animations).run_test(size=(180, 50)) as pilot:
        table = pilot.app.query_one("#table", SplitFlapDataTable)
        manager = TableManager(table, create_airports_table_config(1.0), [])
        manager.populate(_rows(5, name_suffix=long_suffix))
        await _wait_for_animations(table, pilot)
        assert _column_width(table, "NAME") == len(f"AIRPORT 0{long_suffix}")
        assert _column_width(table, "STAFFED") == len("STAFFED")

        update = manager.populate(_rows(5, staffed=long_staffed))
        assert update is not None
        await update
        await _wait_for_animations(table, pilot)

        assert _column_width(table, "NAME") == len("AIRPORT 0")
        assert _column_width(table, "STAFFED") == len(long_staffed)


async def test_cursor_restored_after_count_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.data import vatsim_api

    monkeypatch.setattr(vatsim_api, "download_vatsim_data", lambda: {"pilots": []})
    app = VATSIMControlApp(
        airport_data=_rows(30),
        groupings_data=[],
        total_flights=0,
        args=APP_ARGS,
        airport_allowlist=["KSFO"],
    )
    app.refresh_interval = 3600  # no auto-refresh inside the test

    async def _fetch_more_rows() -> tuple[list[AirportStats], list, int]:
        return _rows(34), [], 0

    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        monkeypatch.setattr(app, "fetch_data_async", _fetch_more_rows)
        table = app.query_one("#airports-table", SplitFlapDataTable)
        table.move_cursor(row=7)
        await pilot.pause()
        saved_icao = app.airport_data[7].icao

        await app._apply_refresh_data(
            saved_icao, None, 7, table.scroll_offset.y, "airports"
        )
        await pilot.pause()

        assert table.row_count == 34
        assert table.cursor_row == 7
        assert app.airport_data[table.cursor_row].icao == saved_icao


def _control_app(monkeypatch: pytest.MonkeyPatch, count: int) -> VATSIMControlApp:
    """The main app on ``count`` fixture airports, with no network and no auto-refresh."""
    from backend.data import vatsim_api

    monkeypatch.setattr(vatsim_api, "download_vatsim_data", lambda: {"pilots": []})
    app = VATSIMControlApp(
        airport_data=_rows(count),
        groupings_data=[],
        total_flights=0,
        args=APP_ARGS,
        airport_allowlist=["KSFO"],
    )
    app.refresh_interval = 3600
    return app


def _count_calls(monkeypatch: pytest.MonkeyPatch, name: str) -> list[int]:
    """Count the calls to a ``SplitFlapDataTable`` method; the list grows by one per call."""
    original = getattr(SplitFlapDataTable, name)
    calls: list[int] = []

    def _counting(self: SplitFlapDataTable, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SplitFlapDataTable, name, _counting)
    return calls


@pytest.mark.parametrize("animations", [True, False])
async def test_fixed_width_column_grows_for_a_wider_value(animations: bool) -> None:
    """A column with no shrink re-measure still widens for a wider value in an existing row."""
    long_callsign = "ABCDEFGHIJKL"
    async with _TableHost(animations).run_test(size=(180, 50)) as pilot:
        table = pilot.app.query_one("#table", SplitFlapDataTable)
        manager = TableManager(table, DEPARTURES_TABLE_CONFIG, [])
        manager.populate([("AAL1", "KSFO", "SAN FRANCISCO"), ("BAW2", "EGLL", "LHR")])
        await _wait_for_animations(table, pilot)

        update = manager.populate(
            [
                ("AAL1", "KSFO", "SAN FRANCISCO"),
                (long_callsign, "KLAX", "LOS ANGELES"),
                ("UAL3", "KJFK", "KENNEDY"),
            ]
        )
        assert update is not None
        await update
        await _wait_for_animations(table, pilot)

        assert _column_width(table, "FLIGHT") == len(long_callsign)


@pytest.mark.parametrize("animations", [True, False])
async def test_column_narrows_when_its_widest_row_is_removed(animations: bool) -> None:
    long_name = "AIRPORT 24 WITH A VERY LONG NAME"
    rows = _rows(25)
    rows[24] = replace(rows[24], name=long_name)
    async with _TableHost(animations).run_test(size=(180, 50)) as pilot:
        table = pilot.app.query_one("#table", SplitFlapDataTable)
        manager = TableManager(table, create_airports_table_config(1.0), [])
        manager.populate(rows)
        await _wait_for_animations(table, pilot)
        await _land_rows(manager, pilot, rows)
        await _wait_for_animations(table, pilot)
        assert _column_width(table, "NAME") == len(long_name)

        await _land_rows(manager, pilot, rows[:12])
        await _wait_for_fades(manager, pilot)
        await _wait_for_animations(table, pilot)

        assert table.row_count == 12
        assert _column_width(table, "NAME") == len("AIRPORT 11")


async def _land_rows(manager: TableManager, pilot: Pilot, rows: list) -> None:
    """Populate these rows and wait for the update it schedules to land."""
    update = manager.populate(rows)
    assert update is not None
    await update
    await pilot.pause()


async def test_adding_many_rows_does_not_rebuild_the_key_cache_per_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _TableHost(animations=True).run_test(size=(180, 50)) as pilot:
        table = pilot.app.query_one("#table", SplitFlapDataTable)
        manager = TableManager(table, create_airports_table_config(1.0), [])
        manager.setup_columns()
        rebuilds = _count_calls(monkeypatch, "_rebuild_cache")

        manager.populate(_rows(2000))

        assert table.row_count == 2000
        assert len(rebuilds) <= 2, f"{len(rebuilds)} cache rebuilds for 2000 rows"
        assert _column_width(table, "ICAO") == len("K1999")
        assert _column_width(table, "NAME") == len("AIRPORT 1999")


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("table update failed"), asyncio.CancelledError()],
    ids=["error", "cancelled"],
)
async def test_failed_table_update_leaves_activity_watching_on(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    """An update that raises, or is cancelled by a table rebuild, still re-enables tracking."""
    app = _control_app(monkeypatch, 30)

    async def _fetch_rows() -> tuple[list[AirportStats], list, int]:
        return _rows(30), [], 0

    async def _fail() -> None:
        raise failure

    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        monkeypatch.setattr(app, "fetch_data_async", _fetch_rows)
        monkeypatch.setattr(app, "_populate_tables_and_wait", _fail)

        with pytest.raises(type(failure)):
            await app._apply_refresh_data(None, None, 0, 0, "airports")

        assert app.watch_for_user_activity


async def test_populate_tables_cancels_the_old_managers_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _control_app(monkeypatch, 25)
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        table = app.query_one("#airports-table", SplitFlapDataTable)
        old_manager = app.airports_manager
        assert old_manager is not None
        _hold_tail_removal(monkeypatch, table)
        await _land(old_manager, pilot, 12)
        assert old_manager._pending_removal is not None, "a fade is pending"
        old_manager.populate(_rows(12))  # scheduled, not yet run

        app.airport_data = _rows(40)
        app.populate_tables()
        await _wait_for_fades(old_manager, pilot)
        assert app.airports_manager is not None
        await _wait_for_fades(app.airports_manager, pilot)
        await _wait_for_animations(table, pilot)

        assert table.row_count == 40
        assert app.airports_row_keys == list(table.rows)


async def test_clear_during_fade_does_not_truncate_the_next_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _TableHost(animations=True).run_test(size=(180, 50)) as pilot:
        table, manager = await _populated_table(pilot, 25)
        await _wait_for_animations(table, pilot)
        _hold_tail_removal(monkeypatch, table)
        await _land(manager, pilot, 12)
        assert manager._pending_removal is not None, "a fade is pending"

        table.clear()
        await pilot.pause()
        refilled = [table.add_row(*manager._to_tuple(row)) for row in _rows(30)]
        manager.row_keys[:] = refilled
        await _land(manager, pilot, 30)
        await _wait_for_fades(manager, pilot)

        assert list(table.rows) == refilled
        assert manager.row_keys == refilled


async def test_identical_refresh_flaps_no_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _TableHost(animations=True).run_test(size=(180, 50)) as pilot:
        _table, manager = await _populated_table(pilot, 10)
        updates = _count_calls(monkeypatch, "update_cell_animated")

        await _land(manager, pilot, 10)

        assert updates == []


async def test_retarget_to_the_padded_frame_ends_on_the_unpadded_value() -> None:
    """A cell sent mid-flap to the text it already shows, less padding, shows it unpadded."""
    async with _TableHost(animations=True).run_test(size=(180, 50)) as pilot:
        table = pilot.app.query_one("#table", SplitFlapDataTable)
        column = table.add_column("NAME")
        row = table.add_row("WXYZ")
        table.begin_bulk_update()
        table.update_cell_animated(row, column, "W")
        cell = table.animated_cells[(0, 0)]
        for _ in range(40):
            if cell.current_value.endswith(" "):
                break
            table._animate_cells()
        assert cell.animating and cell.current_value.endswith(" ")
        shown = cell.current_value.rstrip()

        table.update_cell_animated(row, column, shown)
        for _ in range(40):
            if not table._animating_cells:
                break
            table._animate_cells()

        assert str(table.get_cell(row, column)) == shown
        assert cell.current_value == shown
