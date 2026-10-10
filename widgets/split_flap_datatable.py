#!/usr/bin/env python3
"""
Split-Flap DataTable Widget for Textual
A reusable DataTable widget with split-flap animation effects
"""

import asyncio
import os
import sys
from collections.abc import Callable
from typing import Any, Literal

from rich.cells import cell_len
from rich.text import Text
from textual.render import measure
from textual.widgets import DataTable
from textual.widgets._data_table import (
    Column,
    ColumnKey,
    RowKey,
    default_cell_formatter,
)

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ui.debug_logger import debug

# Default character sets for the split-flap effect
DEFAULT_FLAP_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 :-"
TIME_FLAP_CHARS = "0123456789: -"
NUMERIC_FLAP_CHARS = "0123456789. -"


class AnimatedCell:
    """Represents a cell with split-flap animation state"""

    def __init__(self, initial_value: str, flap_chars: str = DEFAULT_FLAP_CHARS):
        """
        Initialize an animated cell.

        Args:
            initial_value: The initial value to display
            flap_chars: Character set to use for animation
        """
        self.flap_chars = flap_chars
        self.animating = False
        self.delay_frames = 0  # Frames to wait before starting animation
        # Build character index dictionary for O(1) lookups
        self._build_char_index()
        # Now normalize the initial value after flap_chars is set
        self.target_value = self._normalize_to_flap_chars(initial_value)
        self.current_value = self._normalize_to_flap_chars(initial_value)
        # The target without the padding that lines it up with the old value
        self.final_value = self.target_value

    def _build_char_index(self):
        """Build a dictionary for fast character index lookups"""
        self.char_to_idx = {char: idx for idx, char in enumerate(self.flap_chars)}

    def _normalize_to_flap_chars(self, value: str) -> str:
        """
        Normalize value to use characters available in flap_chars.
        Prefers same case if available, otherwise uses any case available.

        Args:
            value: The string value to normalize

        Returns:
            Normalized string using available flap characters
        """
        result = []
        for char in value:
            if char in self.flap_chars:
                # Character exists in flap set with same case - use it
                result.append(char)
            else:
                # Try to find the character in different case
                if char.isupper() and char.lower() in self.flap_chars:
                    result.append(char.lower())
                elif char.islower() and char.upper() in self.flap_chars:
                    result.append(char.upper())
                else:
                    # Character not in set at all - will be added dynamically during animation
                    result.append(char)
        return "".join(result)

    def set_target(self, new_value: str, delay_frames: int = 0) -> bool:
        """
        Set a new target value and start animation if different.

        Args:
            new_value: The new value to animate to
            delay_frames: Number of animation frames to delay before starting

        Returns:
            True if animation started, False if value unchanged
        """
        # Normalize the new value to use available flap characters
        new_value = self._normalize_to_flap_chars(new_value)
        self.final_value = new_value

        # Handle length differences - pad both to the longer length
        max_len = max(len(new_value), len(self.current_value))
        new_value = new_value.ljust(max_len)
        self.current_value = self.current_value.ljust(max_len)

        # Already showing the new value: drop any flap still heading to an older one.
        # A frame still padded runs one more step, which ends it on the unpadded value.
        if new_value == self.current_value:
            self.target_value = new_value
            self.delay_frames = 0
            self.animating = self.current_value != self.final_value
            return self.animating

        if new_value != self.target_value:
            self.target_value = new_value
            self.delay_frames = delay_frames
            self.animating = True
            return True
        return False

    def animate_step(self) -> tuple[bool, str | None]:
        """
        Perform one animation step.

        Returns:
            Tuple of (is_animating, current_display_value or None if unchanged)
        """
        if not self.animating:
            return False, None

        # Handle delay before starting animation
        if self.delay_frames > 0:
            self.delay_frames -= 1
            return True, None  # Still animating but not changing yet

        # Build display with current animation state
        display = list(self.current_value)
        changed = False

        # Animate all characters simultaneously
        for i in range(len(self.current_value)):
            current_char = self.current_value[i]
            target_char = self.target_value[i]

            if current_char != target_char:
                changed = True

                # Check if target character exists in flap_chars, if not add it
                if target_char not in self.char_to_idx:
                    self.flap_chars += target_char
                    self._build_char_index()

                # Get current index using dictionary lookup (O(1))
                if current_char not in self.char_to_idx:
                    # Current char not in set, add it
                    self.flap_chars += current_char
                    self._build_char_index()

                current_idx = self.char_to_idx[current_char]

                # Move one step forward (wrapping around)
                next_idx = (current_idx + 1) % len(self.flap_chars)
                display[i] = self.flap_chars[next_idx]

        if not changed:
            # Every character reached its target: end on the value without its padding
            padded = self.current_value
            final = self.finish()
            return False, (final if final != padded else None)

        # Update current value
        self.current_value = "".join(display)
        return True, self.current_value

    def finish(self) -> str:
        """
        End the animation at once on the new value, without its padding.

        Returns:
            The value the cell now shows
        """
        self.animating = False
        self.current_value = self.final_value
        return self.current_value


class SplitFlapDataTable(DataTable):
    """
    A DataTable widget with split-flap animation effects.

    Usage:
        table = SplitFlapDataTable()
        table.add_column("Name")
        table.add_column("Time", flap_chars=TIME_FLAP_CHARS)  # Use time-specific chars

        row_key = table.add_row("Alice", "12:34")

        # Update with animation
        table.update_cell_animated(row_key, "Name", "Bob")
    """

    def __init__(
        self,
        *,
        animation_speed: float = 0.05,
        default_flap_chars: str = DEFAULT_FLAP_CHARS,
        stagger_delay: int = 1,
        enable_animations: bool = True,
        zebra_stripes: bool = True,
        on_select: Callable[[], None] | None = None,
        **kwargs,
    ):
        """
        Initialize the split-flap DataTable.

        Args:
            animation_speed: Time in seconds between animation frames (default: 0.05)
            default_flap_chars: Default character set for animations
            stagger_delay: Number of frames to stagger between cell animations (default: 1)
            enable_animations: Whether to enable split-flap animations (default: True).
                             If False, all updates will be instant.
            on_select: Optional callback function called when a row is selected (double-click or Enter)
            **kwargs: Additional arguments passed to DataTable
        """
        super().__init__(**kwargs, zebra_stripes=zebra_stripes)
        self.animation_speed = animation_speed
        self.default_flap_chars = default_flap_chars
        self.stagger_delay = stagger_delay
        self.enable_animations = enable_animations
        self.on_select_callback = on_select
        self.animated_cells: dict[tuple[int, int], AnimatedCell] = {}
        self.column_flap_chars: dict[int, str] = {}  # Map column index to flap chars
        self.column_alignment: dict[
            int, Literal["left", "center", "right"]
        ] = {}  # Map column index to alignment
        # Columns to re-measure once the current updates settle, by index
        self._columns_to_settle: set[int] = set()
        self._settle_scheduled = False
        self._animation_timer = None
        self._update_counter = 0  # Counter for staggering animations
        # Rows flapping to blank before their removal; the cursor skips them
        self._empty_rows: set[RowKey] = set()
        # Cells of blanked rows, with the future to resolve once none of them animates
        self._blank_waiters: list[
            tuple[set[tuple[int, int]], asyncio.Future[None]]
        ] = []
        # Performance optimization: track actively animating cells
        self._animating_cells: set[tuple[int, int]] = set()
        # Cache for row/column key lookups
        self._row_key_to_idx: dict[RowKey, int] = {}
        self._col_key_to_idx: dict[ColumnKey, int] = {}
        self._cached_row_keys: list[RowKey] = []
        self._cached_col_keys: list[ColumnKey] = []
        self._cache_valid = False

    def _invalidate_cache(self) -> None:
        """Invalidate the row/column key cache."""
        self._cache_valid = False

    def reset_stagger_counter(self) -> None:
        """Reset the stagger counter for bulk operations."""
        self._update_counter = 0

    def begin_bulk_update(self) -> None:
        """
        Call before performing many cell updates to optimize performance.
        Resets stagger counter and prepares for batch updates.
        """
        self._update_counter = 0

    def end_bulk_update(self) -> None:
        """
        Call after performing many cell updates.
        Ensures animation timer is running if needed.
        """
        if (
            self.enable_animations
            and self._animation_timer is None
            and self._animating_cells
        ):
            speed = self._get_adaptive_animation_speed()
            self._animation_timer = self.set_interval(speed, self._animate_cells)

    def _rebuild_cache(self) -> None:
        """Rebuild the row/column key lookup cache."""
        if self._cache_valid:
            return
        self._cached_row_keys = list(self.rows.keys())
        self._cached_col_keys = list(self.columns.keys())
        self._row_key_to_idx = {
            key: idx for idx, key in enumerate(self._cached_row_keys)
        }
        self._col_key_to_idx = {
            key: idx for idx, key in enumerate(self._cached_col_keys)
        }
        self._cache_valid = True

    def _get_row_idx_fast(self, row_key: RowKey) -> int | None:
        """Get row index from cache (O(1) lookup)."""
        self._rebuild_cache()
        return self._row_key_to_idx.get(row_key)

    def _get_col_idx_fast(self, col_key: ColumnKey) -> int | None:
        """Get column index from cache (O(1) lookup)."""
        self._rebuild_cache()
        return self._col_key_to_idx.get(col_key)

    def add_column(
        self,
        label: Any,
        *,
        flap_chars: str | None = None,
        content_align: Literal["left", "center", "right"] = "left",
        **kwargs,
    ) -> ColumnKey:
        """
        Add a column with optional custom flap characters and alignment.

        Args:
            label: The column label
            flap_chars: Custom character set for this column (optional)
            content_align: Text alignment for cells in this column ("left", "center", or "right")
            **kwargs: Additional arguments passed to DataTable.add_column

        Returns:
            The ColumnKey for the new column
        """
        # Convert label to Text with alignment if content_align is specified
        if content_align != "left" and not isinstance(label, Text):
            aligned_label = Text(str(label), justify=content_align)
        else:
            aligned_label = label

        column_key = super().add_column(aligned_label, **kwargs)
        self._invalidate_cache()

        col_idx = len(self.columns) - 1

        # Store custom flap chars for this column if provided
        if flap_chars is not None:
            self.column_flap_chars[col_idx] = flap_chars

        # Store alignment for this column
        if content_align != "left":
            self.column_alignment[col_idx] = content_align

        return column_key

    def add_row(self, *cells: Any, animate: bool = False, **kwargs) -> RowKey:
        """
        Add a row and initialize animated cells.

        Args:
            *cells: Cell values for the row
            animate: If True, animate the row in from empty. If False, show instantly (default: False)
            **kwargs: Additional arguments passed to DataTable.add_row

        Returns:
            The RowKey for the new row
        """
        # Check if we should actually animate (respects enable_animations setting)
        should_animate = animate and self.enable_animations

        if should_animate:
            # Add row with empty values first, then animate to target values
            empty_cells = [""] * len(cells)
            row_key = super().add_row(*empty_cells, **kwargs)
            self._invalidate_cache()

            # Initialize animated cells with empty values and set targets directly
            # This is more efficient than calling update_cell_animated for each cell
            row_idx = len(self.rows) - 1
            columns = list(self.columns.values())
            for col_idx, cell_value in enumerate(cells):
                flap_chars = self.column_flap_chars.get(
                    col_idx, self.default_flap_chars
                )
                animated_cell = AnimatedCell("", flap_chars=flap_chars)
                cell_key = (row_idx, col_idx)
                self.animated_cells[cell_key] = animated_cell

                # Set target directly with minimal stagger
                # Cap delay to prevent excessive wait times
                delay = min(self._update_counter * self.stagger_delay, 10)
                self._update_counter = (self._update_counter + 1) % 20
                if animated_cell.set_target(str(cell_value), delay_frames=delay):
                    self._animating_cells.add(cell_key)

                # Frames never re-measure, so fit the column to the final text now
                # (Textual's add_row has rejected more cells than columns)
                self._grow_column(columns[col_idx], col_idx, cell_value)
        else:
            # Add row normally without animation
            # Normalize cells to uppercase for consistency with animation behavior
            normalized_cells = []
            for col_idx, cell_value in enumerate(cells):
                flap_chars = self.column_flap_chars.get(
                    col_idx, self.default_flap_chars
                )
                has_lowercase = any(c.islower() for c in flap_chars)
                if has_lowercase:
                    normalized_cells.append(cell_value)
                else:
                    normalized_cells.append(str(cell_value).upper())

            row_key = super().add_row(*normalized_cells, **kwargs)
            self._invalidate_cache()

            # Initialize animated cells for this row
            row_idx = len(self.rows) - 1
            for col_idx, cell_value in enumerate(normalized_cells):
                # Get flap chars for this column
                flap_chars = self.column_flap_chars.get(
                    col_idx, self.default_flap_chars
                )

                # Create animated cell
                cell_str = str(cell_value)
                self.animated_cells[(row_idx, col_idx)] = AnimatedCell(
                    cell_str, flap_chars=flap_chars
                )

        # Start animation timer if not already running and we have animating cells
        if self._animation_timer is None and self._animating_cells:
            self._animation_timer = self.set_interval(
                self.animation_speed, self._animate_cells
            )

        return row_key

    def blank_rows(self, row_keys: list[RowKey]) -> asyncio.Future[None]:
        """
        Flap every cell of the given rows to blank and mark the rows as leaving.

        The cursor skips leaving rows. The caller removes them for real once
        the returned future resolves (see ``truncate_rows``).

        Args:
            row_keys: The rows to blank

        Returns:
            A future resolved once none of the rows' cells is animating
        """
        blanked: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._rebuild_cache()
        cells: set[tuple[int, int]] = set()
        for row_key in row_keys:
            self._empty_rows.add(row_key)
            row_idx = self._row_key_to_idx[row_key]
            for col_idx, col_key in enumerate(self._cached_col_keys):
                self.update_cell_animated(row_key, col_key, "")
                cells.add((row_idx, col_idx))
        self._blank_waiters.append((cells, blanked))
        self._resolve_blank_waiters()
        return blanked

    def truncate_rows(self, keep: int, settle_columns: list[int]) -> list[RowKey]:
        """
        Remove every row past the first ``keep`` and forget their per-cell state.

        Args:
            keep: How many rows, counted from the top, stay in the table
            settle_columns: Indices of the columns to re-measure once the
                updates settle, since a removed row may have held their widest cell

        Returns:
            The keys of the removed rows
        """
        self._rebuild_cache()
        removed = self._cached_row_keys[keep:]
        for row_key in reversed(removed):
            self.remove_row(row_key)
            self._empty_rows.discard(row_key)
        self._forget_cells_from_row(keep)
        self._invalidate_cache()
        if removed:
            self._queue_column_settle(settle_columns)
        return removed

    def _forget_cells_from_row(self, first_row: int) -> None:
        """Drop the index-keyed state of every cell at row ``first_row`` or below."""
        for cell_key in [key for key in self.animated_cells if key[0] >= first_row]:
            del self.animated_cells[cell_key]
        self._animating_cells = {
            key for key in self._animating_cells if key[0] < first_row
        }
        for cells, blanked in self._blank_waiters:
            if any(row_idx >= first_row for row_idx, _ in cells):
                blanked.cancel()
        self._resolve_blank_waiters()

    def _resolve_blank_waiters(self) -> None:
        """Resolve the future of each blanked row set whose cells all stopped animating."""
        waiting = []
        for cells, blanked in self._blank_waiters:
            if blanked.done():
                continue
            if cells & self._animating_cells:
                waiting.append((cells, blanked))
            else:
                blanked.set_result(None)
        self._blank_waiters = waiting

    def update_cell_animated(
        self,
        row_key: RowKey | str,
        column_key: ColumnKey | str,
        value: Any,
        *,
        update_width: bool = False,
    ) -> None:
        """
        Update a cell with split-flap animation (or instantly if animations are disabled).

        Args:
            row_key: The key or label identifying the row
            column_key: The key or label identifying the column
            value: The new value for the cell
            update_width: Whether to update column width to fit new content
        """
        # If animations are disabled, update instantly with alignment
        if not self.enable_animations:
            self._update_cell_instantly(row_key, column_key, value, update_width)
            return

        # Find row and column indices using fast cache lookups
        try:
            cell_key = (
                self._resolve_row_idx(row_key),
                self._resolve_col_idx(column_key),
            )
        except (ValueError, KeyError):
            # If we can't find the row/column, just update normally
            self.update_cell(row_key, column_key, value, update_width=update_width)
            return

        self._set_cell_target(cell_key, value, update_width)

    def _resolve_row_idx(self, row_key: RowKey | str) -> int:
        """Row index of a key or label; raises ValueError or KeyError when there is none."""
        if isinstance(row_key, str):
            return self._get_row_index(row_key)
        row_idx = self._get_row_idx_fast(row_key)
        if row_idx is None:
            row_idx = list(self.rows.keys()).index(row_key)
        return row_idx

    def _resolve_col_idx(self, column_key: ColumnKey | str) -> int:
        """Column index of a key or label; raises ValueError or KeyError when there is none."""
        if isinstance(column_key, str):
            return self._get_column_index(column_key)
        col_idx = self._get_col_idx_fast(column_key)
        if col_idx is None:
            col_idx = list(self.columns.keys()).index(column_key)
        return col_idx

    def _update_cell_instantly(
        self,
        row_key: RowKey | str,
        column_key: ColumnKey | str,
        value: Any,
        update_width: bool,
    ) -> None:
        """Show a cell's new value at once, normalized and aligned as an animation would."""
        try:
            col_idx = self._resolve_col_idx(column_key)
        except (ValueError, KeyError):
            # If we can't find the column, just update normally
            self.update_cell(row_key, column_key, value, update_width=update_width)
            return
        # Normalize to uppercase (consistent with animation flap_chars behavior)
        normalized_value = self._normalize_for_display(value, col_idx)
        aligned_value = self._apply_alignment(normalized_value, col_idx)
        self.update_cell(row_key, column_key, aligned_value)
        self._fit_column(col_idx, value, update_width)

    def _fit_column(self, col_idx: int, value: Any, update_width: bool) -> None:
        """
        Widen a column now if the value needs it; every column grows.

        Args:
            col_idx: The column index
            value: The value the column is about to show
            update_width: Whether to also re-measure the column once updates
                settle, so it can shrink
        """
        columns = list(self.columns.values())
        if col_idx < len(columns):
            self._grow_column(columns[col_idx], col_idx, value)
        if update_width:
            self._queue_column_settle([col_idx])

    def _queue_column_settle(self, col_indices: list[int]) -> None:
        """Re-measure these columns once the current updates settle."""
        self._columns_to_settle.update(col_indices)
        if not self._settle_scheduled:
            self._settle_scheduled = True
            self.call_later(self._settle_if_idle)

    def _grow_column(self, column: Column, col_idx: int, value: Any) -> None:
        """Widen a column at once if a value it is about to show would not fit."""
        width = cell_len(self._normalize_for_display(value, col_idx))
        if width > column.content_width:
            column.content_width = width
            self._require_update_dimensions = True
            self.check_idle()

    def _settle_if_idle(self) -> None:
        """Re-measure the changed columns, unless animations will do it when they end."""
        self._settle_scheduled = False
        if not self._animating_cells:
            self._settle_column_widths()

    def _settle_column_widths(self) -> None:
        """Re-measure, once, every column whose cells changed since the last settle."""
        if not self._columns_to_settle:
            return
        self._rebuild_cache()
        column_keys = [
            self._cached_col_keys[col_idx]
            for col_idx in sorted(self._columns_to_settle)
            if col_idx < len(self._cached_col_keys)
        ]
        self._columns_to_settle.clear()
        self.recompute_column_widths(column_keys)

    def recompute_column_widths(self, column_keys: list[ColumnKey]) -> None:
        """
        Fit each given column to its widest cell, measuring every cell once.

        Textual re-measures a whole column only inside
        ``DataTable._update_column_widths``, once for every cell that got
        narrower, which is quadratic when a refresh shifts every row. This
        does it once per column. It relies on Textual 8.2.8 internals (pinned
        in pyproject.toml): ``DataTable._data``, ``DataTable._clear_caches``,
        ``DataTable._require_update_dimensions``, ``Column.content_width``
        and ``default_cell_formatter``.

        Args:
            column_keys: The columns to re-measure
        """
        console = self.app.console
        for column_key in column_keys:
            column = self.columns[column_key]
            widths = [
                measure(
                    console,
                    default_cell_formatter(row_cells[column_key], wrap=False, height=1),
                    1,
                )
                for row_cells in self._data.values()
            ]
            column.content_width = max([measure(console, column.label, 1), *widths])
        self._clear_caches()
        self._require_update_dimensions = True
        self.check_idle()
        self.refresh(layout=True)

    def _set_cell_target(
        self, cell_key: tuple[int, int], value: Any, update_width: bool
    ) -> None:
        """Start a cell flapping toward a new value, staggered after the cells before it."""
        col_idx = cell_key[1]
        if cell_key not in self.animated_cells:
            flap_chars = self.column_flap_chars.get(col_idx, self.default_flap_chars)
            self.animated_cells[cell_key] = AnimatedCell(
                str(value), flap_chars=flap_chars
            )

        self._fit_column(col_idx, value, update_width)

        # Set new target value with staggered delay
        # Cap delay to prevent excessive wait times for large tables
        # Max delay of 10 frames (~0.5s at 50ms/frame) regardless of table size
        delay = min(self._update_counter * self.stagger_delay, 10)
        self._update_counter = (self._update_counter + 1) % 20  # Reset after 20 updates
        started = self.animated_cells[cell_key].set_target(
            str(value), delay_frames=delay
        )

        # Track animating cells for efficient iteration
        if started:
            self._animating_cells.add(cell_key)

        # Restart animation timer if it was stopped and we have cells to animate
        if self._animation_timer is None and self._animating_cells:
            # Adaptive animation speed based on number of cells
            speed = self._get_adaptive_animation_speed()
            self._animation_timer = self.set_interval(speed, self._animate_cells)

    def _get_row_index(self, row_label: str) -> int:
        """Get row index from row label"""
        for idx, (_key, row) in enumerate(self.rows.items()):
            if row.label == row_label:
                return idx
        raise ValueError(f"Row with label '{row_label}' not found")

    def _get_column_index(self, column_label: str) -> int:
        """Get column index from column label"""
        for idx, (_key, column) in enumerate(self.columns.items()):
            if str(column.label) == column_label:
                return idx
        raise ValueError(f"Column with label '{column_label}' not found")

    def _get_adaptive_animation_speed(self) -> float:
        """Calculate adaptive animation speed based on number of cells"""
        cell_count = len(self.animated_cells)

        # Base speed
        base_speed = self.animation_speed

        # Slow down animations for very large tables to reduce CPU load
        if cell_count > 1000:
            return base_speed * 2.0  # Half speed
        elif cell_count > 500:
            return base_speed * 1.5  # 33% slower

        return base_speed

    def _get_visible_row_range(self) -> tuple[int, int]:
        """
        Get the range of row indices that are currently visible in the viewport.
        Returns (first_visible_row, last_visible_row) where last is exclusive.
        Uses DataTable's internal row positioning for accuracy.
        """
        self._rebuild_cache()
        num_rows = len(self._cached_row_keys)

        if num_rows == 0:
            return (0, 0)

        try:
            # Get the visible content region
            scroll_y = self.scroll_offset.y
            visible_height = self.size.height
            buffer_rows = 3  # Buffer for smooth scrolling

            # Use ordered_rows to get actual row heights if available
            ordered = self.ordered_rows
            if ordered:
                # Calculate cumulative heights to find visible range
                current_y = self.header_height if self.show_header else 0
                first_visible = num_rows  # Default to end if not found
                last_visible = 0

                for row_idx, row in enumerate(ordered):
                    row_bottom = current_y + row.height
                    # Check if this row intersects the visible region (with buffer)
                    visible_top = scroll_y - buffer_rows
                    visible_bottom = scroll_y + visible_height + buffer_rows

                    if row_bottom > visible_top and current_y < visible_bottom:
                        if row_idx < first_visible:
                            first_visible = row_idx
                        last_visible = row_idx + 1

                    current_y = row_bottom

                    # Early exit if we've passed the visible region
                    if current_y > visible_bottom:
                        break

                return (max(0, first_visible), min(num_rows, last_visible))
            else:
                # Fallback: estimate with row_height=1
                row_height = 1
                first_visible = max(0, int(scroll_y / row_height) - buffer_rows)
                last_visible = min(
                    num_rows,
                    int((scroll_y + visible_height) / row_height) + buffer_rows + 1,
                )
                return (first_visible, last_visible)

        except (AttributeError, TypeError, ZeroDivisionError):
            # If we can't determine viewport, treat all rows as visible
            return (0, num_rows)

    def _animate_cells(self) -> None:
        """Perform one animation step for animating cells only"""
        # Fast path: nothing to animate
        if not self._animating_cells:
            self._stop_animation_timer()
            return

        # Use cached keys (rebuilt only when invalidated)
        self._rebuild_cache()
        cells_to_update, cells_completed = self._step_animating_cells()

        # Remove completed cells from tracking set
        for cell_key in cells_completed:
            self._animating_cells.discard(cell_key)
        self._resolve_blank_waiters()

        # Stop timer if no more cells are animating
        if not self._animating_cells:
            self._stop_animation_timer()

        self._apply_cell_updates(cells_to_update)
        if not self._animating_cells:
            self._settle_column_widths()

    def _stop_animation_timer(self) -> None:
        """Stop the animation timer if it runs."""
        if self._animation_timer is not None:
            self._animation_timer.stop()
            self._animation_timer = None

    def _step_animating_cells(
        self,
    ) -> tuple[list[tuple[int, int, str]], list[tuple[int, int]]]:
        """
        Advance every animating cell one frame; off-screen cells jump to their target.

        Returns:
            The (row, column, text) updates to show, and the cells that finished
        """
        # Get visible row range using accurate calculation
        first_visible_row, last_visible_row = self._get_visible_row_range()

        # Batch cell updates to reduce overhead
        cells_to_update: list[tuple[int, int, str]] = []
        cells_completed: list[tuple[int, int]] = []

        # Only iterate over cells that are actually animating
        for cell_key in list(
            self._animating_cells
        ):  # Copy to allow modification during iteration
            row_idx, col_idx = cell_key
            cell = self.animated_cells.get(cell_key)
            if cell is None:
                # Cell was removed, clean up tracking
                self._animating_cells.discard(cell_key)
                continue

            # Check if row is in visible viewport (or buffer zone)
            if first_visible_row <= row_idx < last_visible_row:
                # Animate visible cells normally
                is_animating, display_value = cell.animate_step()

                # Only update if display value actually changed
                if display_value is not None:
                    cells_to_update.append((row_idx, col_idx, display_value))

                # Track completed cells
                if not is_animating:
                    cells_completed.append(cell_key)
            else:
                # Instantly complete off-screen animations to save CPU
                if cell.animating:
                    cells_to_update.append((row_idx, col_idx, cell.finish()))
                cells_completed.append(cell_key)
        return cells_to_update, cells_completed

    def _apply_cell_updates(self, cells_to_update: list[tuple[int, int, str]]) -> None:
        """Write one frame's cell texts into the table, aligned per column."""
        row_keys = self._cached_row_keys
        col_keys = self._cached_col_keys
        num_rows = len(row_keys)
        num_cols = len(col_keys)
        for row_idx, col_idx, display_value in cells_to_update:
            try:
                if row_idx < num_rows and col_idx < num_cols:
                    # Apply alignment if specified for this column; widths are
                    # fitted per update, never per frame
                    cell_value = self._apply_alignment(display_value, col_idx)
                    self.update_cell(row_keys[row_idx], col_keys[col_idx], cell_value)
            except (KeyError, IndexError) as exc:
                # The cell can go while it animates (its row was removed)
                debug(
                    f"Skipped a frame for the cell at row {row_idx}, column {col_idx}: {exc!r}"
                )

    def _normalize_for_display(self, value: Any, col_idx: int) -> str:
        """
        Normalize a value for display, converting to uppercase based on column's flap_chars.
        This ensures consistent display whether animations are enabled or disabled.

        Args:
            value: The cell value
            col_idx: The column index

        Returns:
            Normalized string value (uppercase if flap_chars is uppercase-only)
        """
        str_value = str(value)
        flap_chars = self.column_flap_chars.get(col_idx, self.default_flap_chars)

        # Check if flap_chars has lowercase letters
        has_lowercase = any(c.islower() for c in flap_chars)

        if has_lowercase:
            # Flap chars include lowercase, keep original case
            return str_value
        else:
            # Flap chars are uppercase-only, convert to uppercase
            return str_value.upper()

    def _apply_alignment(self, value: Any, col_idx: int) -> Any:
        """
        Apply alignment to a cell value if specified for the column.

        Args:
            value: The cell value (typically a string)
            col_idx: The column index

        Returns:
            A Text object with alignment if specified, otherwise the original value
        """
        alignment = self.column_alignment.get(col_idx)
        if alignment and isinstance(value, str):
            return Text(value, justify=alignment)
        return value

    def _is_row_empty(self, row_key: RowKey) -> bool:
        """Check if a row is marked as empty/hidden"""
        return row_key in self._empty_rows

    def _get_next_visible_row(self, start_idx: int, direction: int = 1) -> int | None:
        """
        Get the next visible (non-empty) row index from start_idx.

        Args:
            start_idx: Starting row index
            direction: 1 for down, -1 for up

        Returns:
            Next visible row index or None if no visible rows found
        """
        self._rebuild_cache()
        row_keys = self._cached_row_keys
        idx = start_idx + direction

        while 0 <= idx < len(row_keys):
            if not self._is_row_empty(row_keys[idx]):
                return idx
            idx += direction

        return None

    def move_cursor(
        self,
        *,
        row: int | None = None,
        column: int | None = None,
        animate: bool = False,
        scroll: bool = True,
    ) -> None:
        """
        Move cursor, skipping empty rows.

        Args:
            row: Target row index
            column: Target column index
            animate: Whether to animate the movement
            scroll: Whether to scroll to keep cursor in view
        """
        self._rebuild_cache()
        row_keys = self._cached_row_keys

        # If moving to a specific row, check if it's empty
        if row is not None and 0 <= row < len(row_keys):
            row_key = row_keys[row]
            if self._is_row_empty(row_key):
                # Find nearest non-empty row
                # Try down first
                next_row = self._get_next_visible_row(row - 1, direction=1)
                if next_row is None:
                    # Try up
                    next_row = self._get_next_visible_row(row, direction=-1)

                if next_row is not None:
                    row = next_row
                else:
                    # No visible rows available
                    return

        super().move_cursor(row=row, column=column, animate=animate, scroll=scroll)

    def action_cursor_down(self) -> None:
        """Move cursor down, skipping empty rows"""
        if self.cursor_row is None:
            return super().action_cursor_down()

        next_row = self._get_next_visible_row(self.cursor_row, direction=1)
        if next_row is not None:
            self.move_cursor(row=next_row)

    def action_cursor_up(self) -> None:
        """Move cursor up, skipping empty rows"""
        if self.cursor_row is None:
            return super().action_cursor_up()

        next_row = self._get_next_visible_row(self.cursor_row, direction=-1)
        if next_row is not None:
            self.move_cursor(row=next_row)

    async def _on_click(self, event):
        """Handle click events, preventing clicks on empty rows and handling double-click"""
        # Let parent handle the click first
        await super()._on_click(event)

        # After click, if cursor landed on empty row, move to nearest visible
        if self.cursor_row is not None:
            self._rebuild_cache()
            row_keys = self._cached_row_keys
            if self.cursor_row < len(row_keys):
                row_key = row_keys[self.cursor_row]
                if self._is_row_empty(row_key):
                    # Move to nearest non-empty row
                    next_row = self._get_next_visible_row(
                        self.cursor_row - 1, direction=1
                    )
                    if next_row is None:
                        next_row = self._get_next_visible_row(
                            self.cursor_row, direction=-1
                        )

                    if next_row is not None:
                        self.move_cursor(row=next_row)

        # Handle double-click by calling the callback
        if event.chain == 2 and self.on_select_callback is not None:
            debug("Double-click detected, calling on_select_callback")
            self.on_select_callback()

    def clear(self, columns: bool = False):
        """
        Clear the table and reset animated cells.

        Args:
            columns: Whether to also clear columns

        Returns:
            The DataTable instance for method chaining
        """
        result = super().clear(columns=columns)
        self.animated_cells.clear()
        self._columns_to_settle.clear()
        self._empty_rows.clear()
        for _cells, blanked in self._blank_waiters:
            blanked.cancel()
        self._blank_waiters.clear()
        self._animating_cells.clear()
        self._invalidate_cache()
        if columns:
            self.column_flap_chars.clear()
            self.column_alignment.clear()
        return result

    def action_select_cursor(self) -> None:
        """Handle row selection (Enter key or double-click)."""
        debug(
            f"SplitFlapDataTable.action_select_cursor called, callback={self.on_select_callback}"
        )
        super().action_select_cursor()
        if self.on_select_callback is not None:
            debug("Calling on_select_callback")
            self.on_select_callback()
        else:
            debug("No callback set")
