"""
Table Management Module
Contains TableManager class and table configuration builders
"""

import asyncio
from typing import Any

from backend.core.models import AirportStats, GroupingStats
from widgets.split_flap_datatable import NUMERIC_FLAP_CHARS, SplitFlapDataTable

from .config import (
    ALTIMETER_FLAP_CHARS,
    CALLSIGN_FLAP_CHARS,
    ETA_FLAP_CHARS,
    ICAO_FLAP_CHARS,
    POSITION_FLAP_CHARS,
    WIND_FLAP_CHARS,
    ColumnConfig,
    TableConfig,
)
from .debug_logger import debug
from .utils import airport_grouping_sort_key, eta_sort_key


class TableManager:
    """Manages table population and updates with split-flap animations"""

    def __init__(
        self,
        table: SplitFlapDataTable,
        config: TableConfig,
        row_keys: list,
    ):
        self.table = table
        self.config = config
        self.row_keys = row_keys
        self.is_first_load = True
        self._active_tasks: set = set()  # Track all active async tasks for cleanup
        self._cached_data: list[tuple] = []  # Rows as last shown, for the cell diff
        # Tail rows still flapping to blank: the task that removes them, and the rows that stay
        self._pending_removal: tuple[asyncio.Task, int] | None = None

    def _create_tracked_task(self, coro) -> asyncio.Task:
        """Create a task and track it for cleanup."""
        task = asyncio.create_task(coro)
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)
        return task

    def cancel_pending_tasks(self) -> None:
        """Cancel every pending update and tail removal; a cancelled one leaves the table alone."""
        for task in list(self._active_tasks):
            task.cancel()
        self._pending_removal = None

    def setup_columns(self) -> None:
        """Set up table columns if they don't exist"""
        if not self.table.columns:
            for col_config in self.config.columns:
                self.table.add_column(
                    col_config.name,
                    flap_chars=col_config.flap_chars,
                    content_align=col_config.content_align,
                )

    def populate(self, data: list[Any]) -> asyncio.Task | None:
        """
        Populate table with data, applying sorting and animations.

        Args:
            data: List of AirportStats, GroupingStats, or tuple objects

        Returns:
            The task that updates the table in place, or None on the first
            load, which fills the table before returning. Await the task
            before reading or moving the cursor.
        """

        # Apply sorting if configured (works with both objects and tuples)
        if self.config.sort_function:
            data = sorted(data, key=self.config.sort_function)

        # Set up columns
        self.setup_columns()
        column_keys = list(self.table.columns.keys())

        if self.is_first_load:
            # First load: clear table and animate from blank
            self.table.clear()
            self.row_keys.clear()
            rows = [self._to_tuple(row_obj) for row_obj in data]
            self._add_rows_to_table(rows)
            self._cached_data = rows
            self.is_first_load = False
            return None
        # Subsequent updates: update the existing rows in place
        return self._create_tracked_task(self._update_async(data, column_keys))

    def _add_rows_to_table(self, data: list[Any]) -> None:
        """Add rows to table with animations"""
        # Signal start of bulk update for optimized staggering
        self.table.begin_bulk_update()

        for row_obj in data:
            # Convert object to tuple if needed
            row_data = self._to_tuple(row_obj)

            # Use animate=True for efficient single-pass animation setup
            row_key = self.table.add_row(*row_data, animate=True)
            self.row_keys.append(row_key)

        # Signal end of bulk update to start animations
        self.table.end_bulk_update()

    def _to_tuple(self, row_obj: Any) -> tuple:
        """
        Convert row object to tuple for display.
        Handles AirportStats, GroupingStats, and already-tuple data.
        """
        if isinstance(row_obj, AirportStats):
            # Check if we should hide wind column based on the config
            hide_wind = not any(col.name == "WIND" for col in self.config.columns)
            # Check if we should use combined format (arrivals<xH / arrivals_all)
            # This is true when max_eta != 0, detected by ARR column having "(<" in its name
            include_arrivals_all = any(
                "ARR" in col.name and "(<" in col.name for col in self.config.columns
            )
            return row_obj.to_tuple(
                hide_wind=hide_wind, include_arrivals_all=include_arrivals_all
            )
        elif isinstance(row_obj, GroupingStats):
            # Check if we should use combined format (arrivals<xH / arrivals_all)
            # This is true when max_eta != 0, detected by ARR column having "(<" in its name
            include_arrivals_all = any(
                "ARR" in col.name and "(<" in col.name for col in self.config.columns
            )
            return row_obj.to_tuple(include_arrivals_all=include_arrivals_all)
        else:
            # Already a tuple
            return row_obj

    async def _update_async(self, new_data: list[Any], column_keys: list) -> None:
        """Run the in-place update as a task, so ``populate`` hands it back to await."""
        self._update_efficiently(new_data, column_keys)

    def _update_efficiently(self, new_data: list[Any], column_keys: list) -> None:
        """
        Update the table in place, row by row in position.

        The rows both lists share get only their changed cells flapped, extra
        rows are appended, and rows past the new count fade out. Textual has
        no insert-at and the widget's animation state is keyed by row index,
        so rows stay positional.
        """
        self._finish_pending_removal()
        current_row_count = len(self.row_keys)
        new_rows = [self._to_tuple(row_obj) for row_obj in new_data]
        debug(
            f"_update_efficiently: current_rows={current_row_count}, new_rows={len(new_rows)}"
        )

        for i, row_data in enumerate(new_rows[:current_row_count]):
            old_row_data = self._cached_data[i] if i < len(self._cached_data) else None
            self._update_row_cells(
                self.row_keys[i], row_data, old_row_data, column_keys
            )

        if len(new_rows) > current_row_count:
            self._add_rows_to_table(new_rows[current_row_count:])
        elif len(new_rows) < current_row_count:
            self._remove_tail_rows(len(new_rows))

        self._cached_data = new_rows

    def _update_row_cells(
        self,
        row_key: Any,
        row_data: tuple,
        old_row_data: tuple | None,
        column_keys: list,
    ) -> None:
        """Flap the cells of one row whose value differs from what the row showed."""
        for col_idx, cell_value in enumerate(row_data[: len(column_keys)]):
            if (
                old_row_data is not None
                and col_idx < len(old_row_data)
                and str(old_row_data[col_idx]) == str(cell_value)
            ):
                continue
            col_config = (
                self.config.columns[col_idx]
                if col_idx < len(self.config.columns)
                else None
            )
            self.table.update_cell_animated(
                row_key,
                column_keys[col_idx],
                cell_value,
                update_width=col_config.update_width if col_config else False,
            )

    def _remove_tail_rows(self, keep: int) -> None:
        """Flap the rows past ``keep`` to blank, then remove them; at once without animations."""
        if not self.table.enable_animations:
            self._drop_tail_rows(keep)
            return
        blanked = self.table.blank_rows(self.row_keys[keep:])
        task = self._create_tracked_task(self._drop_tail_rows_when_blank(blanked, keep))
        self._pending_removal = (task, keep)

    async def _drop_tail_rows_when_blank(
        self, blanked: asyncio.Future[None], keep: int
    ) -> None:
        """Remove the faded tail rows once the widget reports them blank.

        A cancelled wait (the task, or the future by a table clear) removes nothing.
        """
        try:
            await blanked
        finally:
            if (
                self._pending_removal is not None
                and self._pending_removal[0] is asyncio.current_task()
            ):
                self._pending_removal = None
        self._drop_tail_rows(keep)

    def _finish_pending_removal(self) -> None:
        """Cut short a tail still fading out, so an update starts on the rows that stay."""
        if self._pending_removal is None:
            return
        task, keep = self._pending_removal
        self._pending_removal = None
        task.cancel()
        self._drop_tail_rows(keep)

    def _drop_tail_rows(self, keep: int) -> None:
        """Remove the rows past ``keep`` from the table and from ``row_keys``."""
        width_columns = [
            col_idx
            for col_idx, col_config in enumerate(self.config.columns)
            if col_config.update_width
        ]
        self.table.truncate_rows(keep, settle_columns=width_columns)
        del self.row_keys[keep:]


# Table configuration constants
DEPARTURES_TABLE_CONFIG = TableConfig(
    columns=[
        ColumnConfig("FLIGHT", flap_chars=CALLSIGN_FLAP_CHARS),
        ColumnConfig("DEST", flap_chars=ICAO_FLAP_CHARS),
        ColumnConfig("NAME", update_width=True),
    ],
    sort_function=lambda x: str(x[0]),  # Sort by callsign
)

ARRIVALS_TABLE_CONFIG = TableConfig(
    columns=[
        ColumnConfig("FLIGHT", flap_chars=CALLSIGN_FLAP_CHARS),
        ColumnConfig("ORIG", flap_chars=ICAO_FLAP_CHARS),
        ColumnConfig("NAME", update_width=True),
        ColumnConfig(
            "ETA", flap_chars=ETA_FLAP_CHARS, content_align="right", update_width=True
        ),
        ColumnConfig("ETA (LT)", flap_chars=ETA_FLAP_CHARS, content_align="right"),
    ],
    sort_function=eta_sort_key,
)

# Table configurations for groupings (show both airports)
GROUPING_DEPARTURES_TABLE_CONFIG = TableConfig(
    columns=[
        ColumnConfig("FLIGHT", flap_chars=CALLSIGN_FLAP_CHARS),
        ColumnConfig("ORIG", flap_chars=ICAO_FLAP_CHARS),
        ColumnConfig("NAME", update_width=True),
        ColumnConfig("DEST", flap_chars=ICAO_FLAP_CHARS),
        ColumnConfig("NAME", update_width=True),
    ],
    sort_function=lambda x: str(x[0]),  # Sort by callsign
)

GROUPING_ARRIVALS_TABLE_CONFIG = TableConfig(
    columns=[
        ColumnConfig("FLIGHT", flap_chars=CALLSIGN_FLAP_CHARS),
        ColumnConfig("DEST", flap_chars=ICAO_FLAP_CHARS),
        ColumnConfig("NAME", update_width=True),
        ColumnConfig("ORIG", flap_chars=ICAO_FLAP_CHARS),
        ColumnConfig("NAME", update_width=True),
        ColumnConfig(
            "ETA", flap_chars=ETA_FLAP_CHARS, content_align="right", update_width=True
        ),
        ColumnConfig("ETA (LT)", flap_chars=ETA_FLAP_CHARS, content_align="right"),
    ],
    sort_function=eta_sort_key,
)


def create_airports_table_config(
    max_eta: float, hide_wind: bool = False
) -> TableConfig:
    """Create airport table configuration based on max_eta setting and hide_wind option"""
    arr_suffix = f"(<{max_eta:,.1g}h)" if max_eta != 0 else "(all)"

    columns = [
        ColumnConfig("ICAO", flap_chars=ICAO_FLAP_CHARS),
        ColumnConfig("NAME", update_width=True),
    ]

    # Conditionally add wind column
    if not hide_wind:
        columns.append(
            ColumnConfig("WIND", flap_chars=WIND_FLAP_CHARS, update_width=True)
        )

    # Always add altimeter column
    columns.append(
        ColumnConfig("ALT", flap_chars=ALTIMETER_FLAP_CHARS, update_width=True)
    )

    columns.extend(
        [
            ColumnConfig(
                "TOTAL",
                flap_chars=NUMERIC_FLAP_CHARS,
                content_align="right",
                update_width=True,
            ),
            ColumnConfig(
                "DEP    ", flap_chars=NUMERIC_FLAP_CHARS, content_align="right"
            ),
            ColumnConfig(
                f"ARR {arr_suffix}",
                flap_chars=NUMERIC_FLAP_CHARS,
                content_align="right",
                update_width=True,
            ),
            ColumnConfig("NEXT ETA", flap_chars=ETA_FLAP_CHARS, content_align="right"),
            ColumnConfig("STAFFED", flap_chars=POSITION_FLAP_CHARS, update_width=True),
        ]
    )

    return TableConfig(columns=columns, sort_function=airport_grouping_sort_key)


def create_groupings_table_config(max_eta: float) -> TableConfig:
    """Create groupings table configuration based on max_eta setting"""
    arr_suffix = f"(<{max_eta:,.1g}h)" if max_eta != 0 else "(all)"

    columns = [
        ColumnConfig("GROUPING", update_width=True),
        ColumnConfig(
            "TOTAL",
            flap_chars=NUMERIC_FLAP_CHARS,
            content_align="right",
            update_width=True,
        ),
        ColumnConfig("DEP    ", flap_chars=NUMERIC_FLAP_CHARS, content_align="right"),
        ColumnConfig(
            f"ARR {arr_suffix}",
            flap_chars=NUMERIC_FLAP_CHARS,
            content_align="right",
            update_width=True,
        ),
        ColumnConfig("NEXT ETA", flap_chars=ETA_FLAP_CHARS, content_align="right"),
        ColumnConfig("STAFFED", flap_chars=POSITION_FLAP_CHARS, update_width=True),
    ]

    return TableConfig(columns=columns, sort_function=airport_grouping_sort_key)
