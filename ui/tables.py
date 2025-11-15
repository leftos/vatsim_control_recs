"""
Table Management Module
Contains TableManager class and table configuration builders
"""

import asyncio
from typing import Any, List, Union
from widgets.split_flap_datatable import SplitFlapDataTable, NUMERIC_FLAP_CHARS
from backend.core.models import AirportStats, GroupingStats
from .config import (
    ColumnConfig, TableConfig,
    ETA_FLAP_CHARS, ICAO_FLAP_CHARS, CALLSIGN_FLAP_CHARS,
    POSITION_FLAP_CHARS, WIND_FLAP_CHARS, ALTIMETER_FLAP_CHARS
)
from .utils import eta_sort_key
from .debug_logger import debug


class TableManager:
    """Manages table population and updates with split-flap animations"""
    
    def __init__(self, table: SplitFlapDataTable, config: TableConfig, row_keys: list,
                 progressive_load_chunk_size: int = 20):
        self.table = table
        self.config = config
        self.row_keys = row_keys
        self.is_first_load = True
        self.progressive_load_chunk_size = progressive_load_chunk_size
        self.progressive_load_active = False
        self.pending_removal_tasks = []  # Track pending async removal tasks
    
    async def _wait_and_remove_row(self, row_key) -> None:
        """Wait for the clearing animation to complete, then actually remove the row"""
        debug(f"_wait_and_remove_row: Waiting for animations on row_key={row_key}")
        # Wait for animations to complete
        while True:
            await asyncio.sleep(0.1)
            
            # Check if row still exists
            if row_key not in self.table.rows:
                debug(f"_wait_and_remove_row: Row {row_key} no longer exists in table, exiting")
                break
            
            row_keys = list(self.table.rows.keys())
            if row_key not in row_keys:
                break
            
            row_idx = row_keys.index(row_key)
            
            # Check all cells in this row to see if any are still animating
            still_animating = False
            for col_idx in range(len(self.table.columns)):
                cell_key = (row_idx, col_idx)
                if cell_key in self.table.animated_cells:
                    if self.table.animated_cells[cell_key].animating:
                        still_animating = True
                        break
            
            if not still_animating:
                debug(f"_wait_and_remove_row: Animations complete for row_key={row_key}")
                break
        
        # Animation complete - now actually remove the row from the datatable
        if row_key in self.table.rows:
            debug(f"_wait_and_remove_row: Actually removing row_key={row_key} from datatable")
            # Remove from the _empty_rows set if present
            if row_key in self.table._empty_rows:
                self.table._empty_rows.remove(row_key)
            
            # Get row index for cleaning up animated cells
            row_keys = list(self.table.rows.keys())
            if row_key in row_keys:
                row_idx = row_keys.index(row_key)
                
                # Clean up animated cells for this row
                cells_to_remove = [
                    (r, c) for (r, c) in self.table.animated_cells.keys()
                    if r == row_idx
                ]
                for cell_key in cells_to_remove:
                    del self.table.animated_cells[cell_key]
            
            # Actually remove the row using parent's method
            super(SplitFlapDataTable, self.table).remove_row(row_key)
            debug(f"_wait_and_remove_row: Row {row_key} physically removed from table")
            
            # Remove from row_keys list
            if row_key in self.row_keys:
                self.row_keys.remove(row_key)
                debug(f"_wait_and_remove_row: Row {row_key} removed from row_keys. Remaining rows: {len(self.row_keys)}")
    
    def setup_columns(self) -> None:
        """Set up table columns if they don't exist"""
        if not self.table.columns:
            for col_config in self.config.columns:
                self.table.add_column(
                    col_config.name,
                    flap_chars=col_config.flap_chars,
                    content_align=col_config.content_align
                )
    
    def populate(self, data: List[Any], progressive: bool = False) -> None:
        """
        Populate table with data, applying sorting and animations.
        
        Args:
            data: List of AirportStats, GroupingStats, or tuple objects
            progressive: If True, load data in chunks for better perceived performance
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
            
            if progressive and len(data) > self.progressive_load_chunk_size:
                # Progressive loading: load in chunks
                self.progressive_load_active = True
                asyncio.create_task(self._populate_progressive(data, column_keys))
            else:
                # Standard loading: load all at once
                self._add_rows_to_table(data, column_keys)
            
            self.is_first_load = False
        else:
            # Subsequent updates: efficiently update existing rows
            # Run as async task to await pending removals
            asyncio.create_task(self._update_efficiently_async(data, column_keys))

    def _add_rows_to_table(self, data: List[Any], column_keys: list) -> None:
        """Add rows to table with animations"""
        for row_obj in data:
            # Convert object to tuple if needed
            row_data = self._to_tuple(row_obj)
            
            # Add row with blank values, then animate to actual values
            blank_row = tuple(" " * len(str(cell)) for cell in row_data)
            row_key = self.table.add_row(*blank_row)
            self.row_keys.append(row_key)
            
            # Animate each cell to its target value
            for col_idx, cell_value in enumerate(row_data):
                col_config = self.config.columns[col_idx] if col_idx < len(self.config.columns) else None
                update_width = col_config.update_width if col_config else False
                self.table.update_cell_animated(row_key, column_keys[col_idx], cell_value, update_width=update_width)
    
    async def _populate_progressive(self, data: List[Any], column_keys: list) -> None:
        """Populate table progressively in chunks for better perceived performance"""
        total_rows = len(data)
        chunk_size = self.progressive_load_chunk_size
        
        for i in range(0, total_rows, chunk_size):
            chunk = data[i:i + chunk_size]
            
            # Add this chunk of rows
            self._add_rows_to_table(chunk, column_keys)
            
            # Small delay to allow UI to update and remain responsive
            await asyncio.sleep(0.05)  # 50ms between chunks
        
        self.progressive_load_active = False
    
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
            include_arrivals_all = any("ARR" in col.name and "(<" in col.name for col in self.config.columns)
            return row_obj.to_tuple(hide_wind=hide_wind, include_arrivals_all=include_arrivals_all)
        elif isinstance(row_obj, GroupingStats):
            # Check if we should use combined format (arrivals<xH / arrivals_all)
            # This is true when max_eta != 0, detected by ARR column having "(<" in its name
            include_arrivals_all = any("ARR" in col.name and "(<" in col.name for col in self.config.columns)
            return row_obj.to_tuple(include_arrivals_all=include_arrivals_all)
        else:
            # Already a tuple
            return row_obj
    
    async def _update_efficiently_async(self, new_data: List[Any], column_keys: list) -> None:
        """Async wrapper that waits for pending removals before updating"""
        # CRITICAL: Wait for any pending row removals to complete before proceeding
        if self.pending_removal_tasks:
            debug(f"_update_efficiently_async: Waiting for {len(self.pending_removal_tasks)} pending removal tasks to complete")
            await asyncio.gather(*self.pending_removal_tasks, return_exceptions=True)
            self.pending_removal_tasks.clear()
            debug(f"_update_efficiently_async: All pending removals completed")
        
        # Now proceed with the synchronous update
        self._update_efficiently(new_data, column_keys)
    
    def _update_efficiently(self, new_data: List[Any], column_keys: list) -> None:
        """Efficiently update table by modifying existing rows and adding/removing as needed"""
        current_row_count = len(self.row_keys)
        new_row_count = len(new_data)
        
        debug(f"_update_efficiently: START - current_rows={current_row_count}, new_rows={new_row_count}, empty_rows={len(self.table._empty_rows)}")
        
        # Track if we cached the old data for comparison
        if not hasattr(self, '_cached_data'):
            self._cached_data = []
        
        # Update existing rows with diff checking
        cells_updated = 0
        cells_skipped = 0
        
        for i in range(min(current_row_count, new_row_count)):
            row_obj = new_data[i]
            row_data = self._to_tuple(row_obj)
            old_row_data = self._cached_data[i] if i < len(self._cached_data) else None
            
            if i < len(self.row_keys):
                for col_idx, cell_value in enumerate(row_data):
                    if col_idx < len(column_keys):
                        # Only update if value actually changed
                        if old_row_data is None or col_idx >= len(old_row_data) or str(old_row_data[col_idx]) != str(cell_value):
                            col_config = self.config.columns[col_idx] if col_idx < len(self.config.columns) else None
                            update_width = col_config.update_width if col_config else False
                            self.table.update_cell_animated(self.row_keys[i], column_keys[col_idx], cell_value, update_width=update_width)
                            cells_updated += 1
                        else:
                            cells_skipped += 1
        
        # Debug log for diff efficiency
        if cells_updated > 0 or cells_skipped > 0:
            total_cells = cells_updated + cells_skipped
            skip_rate = (cells_skipped / total_cells * 100) if total_cells > 0 else 0
        
        # Add new rows if needed
        if new_row_count > current_row_count:
            rows_to_add = new_row_count - current_row_count
            debug(f"_update_efficiently: Adding {rows_to_add} new rows")
            for i in range(current_row_count, new_row_count):
                row_obj = new_data[i]
                row_data = self._to_tuple(row_obj)
                blank_row = tuple(" " * len(str(cell)) for cell in row_data)
                row_key = self.table.add_row(*blank_row)
                self.row_keys.append(row_key)
                
                for col_idx, cell_value in enumerate(row_data):
                    if col_idx < len(column_keys):
                        col_config = self.config.columns[col_idx] if col_idx < len(self.config.columns) else None
                        update_width = col_config.update_width if col_config else False
                        self.table.update_cell_animated(row_key, column_keys[col_idx], cell_value, update_width=update_width)
        
        # Remove extra rows if needed
        elif new_row_count < current_row_count:
            rows_to_remove = current_row_count - new_row_count
            debug(f"_update_efficiently: Removing {rows_to_remove} rows (current={current_row_count}, new={new_row_count})")
            
            for i in range(new_row_count, current_row_count):
                if i < len(self.row_keys):
                    row_key = self.row_keys[i]
                    debug(f"Starting removal animation for row_key={row_key} at index {i}")
                    # Start clearing animation
                    self.table.remove_row(row_key)
                    # Schedule actual removal after animation completes and track the task
                    task = asyncio.create_task(self._wait_and_remove_row(row_key))
                    self.pending_removal_tasks.append(task)
        
        # Cache the new data for next comparison (convert to tuples)
        self._cached_data = [self._to_tuple(row) for row in new_data]


# Table configuration constants
DEPARTURES_TABLE_CONFIG = TableConfig(
    columns=[
        ColumnConfig("FLIGHT", flap_chars=CALLSIGN_FLAP_CHARS),
        ColumnConfig("DEST", flap_chars=ICAO_FLAP_CHARS),
        ColumnConfig("NAME", update_width=True),
    ],
    sort_function=lambda x: str(x[0])  # Sort by callsign
)

ARRIVALS_TABLE_CONFIG = TableConfig(
    columns=[
        ColumnConfig("FLIGHT", flap_chars=CALLSIGN_FLAP_CHARS),
        ColumnConfig("ORIG", flap_chars=ICAO_FLAP_CHARS),
        ColumnConfig("NAME", update_width=True),
        ColumnConfig("ETA", flap_chars=ETA_FLAP_CHARS, content_align="right", update_width=True),
        ColumnConfig("ETA (LT)", flap_chars=ETA_FLAP_CHARS, content_align="right"),
    ],
    sort_function=eta_sort_key
)

# Table configurations for groupings (show both airports)
GROUPING_DEPARTURES_TABLE_CONFIG = TableConfig(
    columns=[
        ColumnConfig("FLIGHT", flap_chars=CALLSIGN_FLAP_CHARS),
        ColumnConfig("FROM", flap_chars=ICAO_FLAP_CHARS),
        ColumnConfig("NAME", update_width=True),
        ColumnConfig("DEST", flap_chars=ICAO_FLAP_CHARS),
        ColumnConfig("NAME", update_width=True),
    ],
    sort_function=lambda x: str(x[0])  # Sort by callsign
)

GROUPING_ARRIVALS_TABLE_CONFIG = TableConfig(
    columns=[
        ColumnConfig("FLIGHT", flap_chars=CALLSIGN_FLAP_CHARS),
        ColumnConfig("TO", flap_chars=ICAO_FLAP_CHARS),
        ColumnConfig("NAME", update_width=True),
        ColumnConfig("ORIG", flap_chars=ICAO_FLAP_CHARS),
        ColumnConfig("NAME", update_width=True),
        ColumnConfig("ETA", flap_chars=ETA_FLAP_CHARS, content_align="right", update_width=True),
        ColumnConfig("ETA (LT)", flap_chars=ETA_FLAP_CHARS, content_align="right"),
    ],
    sort_function=eta_sort_key
)


def create_airports_table_config(max_eta: float, hide_wind: bool = False) -> TableConfig:
    """Create airport table configuration based on max_eta setting and hide_wind option"""
    arr_suffix = f"(<{max_eta:,.1g}h)" if max_eta != 0 else "(all)"
    
    columns = [
        ColumnConfig("ICAO", flap_chars=ICAO_FLAP_CHARS),
        ColumnConfig("NAME", update_width=True),
    ]
    
    # Conditionally add wind column
    if not hide_wind:
        columns.append(ColumnConfig("WIND", flap_chars=WIND_FLAP_CHARS, update_width=True))
    
    # Always add altimeter column
    columns.append(ColumnConfig("ALT", flap_chars=ALTIMETER_FLAP_CHARS, update_width=True))
    
    columns.extend([
        ColumnConfig("TOTAL", flap_chars=NUMERIC_FLAP_CHARS, content_align="right", update_width=True),
        ColumnConfig("DEP    ", flap_chars=NUMERIC_FLAP_CHARS, content_align="right"),
        ColumnConfig(f"ARR {arr_suffix}", flap_chars=NUMERIC_FLAP_CHARS, content_align="right", update_width=True),
        ColumnConfig("NEXT ETA", flap_chars=ETA_FLAP_CHARS, content_align="right"),
        ColumnConfig("STAFFED", flap_chars=POSITION_FLAP_CHARS, update_width=True),
    ])
    
    return TableConfig(columns=columns)


def create_groupings_table_config(max_eta: float) -> TableConfig:
    """Create groupings table configuration based on max_eta setting"""
    arr_suffix = f"(<{max_eta:,.1g}h)" if max_eta != 0 else "(all)"
    
    columns=[
        ColumnConfig("GROUPING", update_width=True),
        ColumnConfig("TOTAL", flap_chars=NUMERIC_FLAP_CHARS, content_align="right", update_width=True),
        ColumnConfig("DEP    ", flap_chars=NUMERIC_FLAP_CHARS, content_align="right"),
        ColumnConfig(f"ARR {arr_suffix}", flap_chars=NUMERIC_FLAP_CHARS, content_align="right", update_width=True),
        ColumnConfig("NEXT ETA", flap_chars=ETA_FLAP_CHARS, content_align="right"),
        ColumnConfig("STAFFED", flap_chars=POSITION_FLAP_CHARS, update_width=True),
    ]
                    
    return TableConfig(columns=columns)