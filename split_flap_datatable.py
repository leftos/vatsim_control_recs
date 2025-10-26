#!/usr/bin/env python3
"""
Split-Flap DataTable Widget for Textual
A reusable DataTable widget with split-flap animation effects
"""

from typing import Any, Optional, Literal
from textual.widgets import DataTable
from textual.widgets._data_table import RowKey, ColumnKey
from rich.text import Text

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
        return ''.join(result)
    
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
        
        # Handle length differences - pad both to the longer length
        max_len = max(len(new_value), len(self.current_value))
        new_value = new_value.ljust(max_len)
        self.current_value = self.current_value.ljust(max_len)
        
        # Skip if already at target
        if new_value == self.current_value:
            return False
            
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
        all_complete = True
        changed = False
        
        # Animate all characters simultaneously
        for i in range(len(self.current_value)):
            current_char = self.current_value[i]
            target_char = self.target_value[i]
            
            if current_char != target_char:
                all_complete = False
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
        
        # Only update if something changed
        if not changed:
            return all_complete, None
        
        # Update current value
        self.current_value = ''.join(display)
        
        # Check if animation is complete
        if all_complete:
            self.animating = False
            self.current_value = self.target_value
        
        return self.animating or not all_complete, self.current_value


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
        **kwargs
    ):
        """
        Initialize the split-flap DataTable.
        
        Args:
            animation_speed: Time in seconds between animation frames (default: 0.05)
            default_flap_chars: Default character set for animations
            stagger_delay: Number of frames to stagger between cell animations (default: 1)
            enable_animations: Whether to enable split-flap animations (default: True).
                             If False, all updates will be instant.
            **kwargs: Additional arguments passed to DataTable
        """
        super().__init__(**kwargs, zebra_stripes=zebra_stripes)
        self.animation_speed = animation_speed
        self.default_flap_chars = default_flap_chars
        self.stagger_delay = stagger_delay
        self.enable_animations = enable_animations
        self.animated_cells: dict[tuple[int, int], AnimatedCell] = {}
        self.column_flap_chars: dict[int, str] = {}  # Map column index to flap chars
        self.column_alignment: dict[int, Literal["left", "center", "right"]] = {}  # Map column index to alignment
        self.cells_need_width_update: dict[tuple[int, int], bool] = {}  # Track which cells need width updates
        self._animation_timer = None
        self._update_counter = 0  # Counter for staggering animations
    
    def add_column(
        self,
        label: Any,
        *,
        flap_chars: Optional[str] = None,
        content_align: Literal["left", "center", "right"] = "left",
        **kwargs
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
        column_key = super().add_column(label, **kwargs)
        
        col_idx = len(self.columns) - 1
        
        # Store custom flap chars for this column if provided
        if flap_chars is not None:
            self.column_flap_chars[col_idx] = flap_chars
        
        # Store alignment for this column
        if content_align != "left":
            self.column_alignment[col_idx] = content_align
        
        return column_key
    
    def add_row(self, *cells: Any, **kwargs) -> RowKey:
        """
        Add a row and initialize animated cells.
        
        Args:
            *cells: Cell values for the row
            **kwargs: Additional arguments passed to DataTable.add_row
            
        Returns:
            The RowKey for the new row
        """
        row_key = super().add_row(*cells, **kwargs)
        
        # Initialize animated cells for this row
        row_idx = len(self.rows) - 1
        for col_idx, cell_value in enumerate(cells):
            # Get flap chars for this column
            flap_chars = self.column_flap_chars.get(col_idx, self.default_flap_chars)
            
            # Create animated cell
            cell_str = str(cell_value)
            self.animated_cells[(row_idx, col_idx)] = AnimatedCell(
                cell_str,
                flap_chars=flap_chars
            )
        
        # Start animation timer if not already running
        if self._animation_timer is None:
            self._animation_timer = self.set_interval(
                self.animation_speed,
                self._animate_cells
            )
        
        return row_key
    
    def update_cell_animated(
        self,
        row_key: RowKey | str,
        column_key: ColumnKey | str,
        value: Any,
        *,
        update_width: bool = False
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
            # Find column index for alignment
            try:
                if isinstance(column_key, str):
                    col_idx = self._get_column_index(column_key)
                else:
                    col_idx = list(self.columns.keys()).index(column_key)
                # Apply alignment
                aligned_value = self._apply_alignment(value, col_idx)
                self.update_cell(row_key, column_key, aligned_value, update_width=update_width)
            except (ValueError, KeyError):
                # If we can't find the column, just update normally
                self.update_cell(row_key, column_key, value, update_width=update_width)
            return
        
        # Find row and column indices
        try:
            if isinstance(row_key, str):
                row_idx = self._get_row_index(row_key)
            else:
                row_idx = list(self.rows.keys()).index(row_key)
            
            if isinstance(column_key, str):
                col_idx = self._get_column_index(column_key)
            else:
                col_idx = list(self.columns.keys()).index(column_key)
        except (ValueError, KeyError):
            # If we can't find the row/column, just update normally
            self.update_cell(row_key, column_key, value, update_width=update_width)
            return
        
        # Get or create animated cell
        cell_key = (row_idx, col_idx)
        if cell_key not in self.animated_cells:
            flap_chars = self.column_flap_chars.get(col_idx, self.default_flap_chars)
            self.animated_cells[cell_key] = AnimatedCell(str(value), flap_chars=flap_chars)
        
        # Track if this cell needs width update
        if update_width:
            self.cells_need_width_update[cell_key] = True
        
        # Set new target value with staggered delay
        delay = self._update_counter * self.stagger_delay
        self._update_counter = (self._update_counter + 1) % 20  # Reset after 20 updates to prevent excessive delays
        self.animated_cells[cell_key].set_target(str(value), delay_frames=delay)
    
    def _get_row_index(self, row_label: str) -> int:
        """Get row index from row label"""
        for idx, (key, row) in enumerate(self.rows.items()):
            if row.label == row_label:
                return idx
        raise ValueError(f"Row with label '{row_label}' not found")
    
    def _get_column_index(self, column_label: str) -> int:
        """Get column index from column label"""
        for idx, (key, column) in enumerate(self.columns.items()):
            if str(column.label) == column_label:
                return idx
        raise ValueError(f"Column with label '{column_label}' not found")
    
    def _animate_cells(self) -> None:
        """Perform one animation step for all animating cells"""
        if not self.animated_cells:
            return
        
        row_keys = list(self.rows.keys())
        col_keys = list(self.columns.keys())
        
        # Get visible row range for optimization
        try:
            # Calculate which rows are currently visible in the viewport
            scroll_y = self.scroll_offset.y
            visible_height = self.size.height
            # Estimate row height (typically 1 line + borders)
            row_height = 1
            first_visible_row = max(0, int(scroll_y / row_height))
            last_visible_row = min(len(row_keys), int((scroll_y + visible_height) / row_height) + 1)
        except Exception:
            # If we can't determine viewport, treat all rows as visible
            first_visible_row = 0
            last_visible_row = len(row_keys)
        
        # Batch cell updates to reduce overhead
        cells_to_update = []
        cells_to_instant_update = []
        
        for (row_idx, col_idx), cell in self.animated_cells.items():
            # Check if row is in visible viewport
            is_visible = first_visible_row <= row_idx < last_visible_row
            
            if is_visible:
                # Animate visible cells normally
                is_animating, display_value = cell.animate_step()
                
                # Only update if display value actually changed
                if display_value is not None:
                    cells_to_update.append((row_idx, col_idx, display_value))
            else:
                # Instantly update off-screen cells to their target
                if cell.animating:
                    cell.current_value = cell.target_value
                    cell.animating = False
                    cells_to_instant_update.append((row_idx, col_idx, cell.target_value))
        
        # Apply all updates
        for row_idx, col_idx, display_value in cells_to_update + cells_to_instant_update:
            try:
                if row_idx < len(row_keys) and col_idx < len(col_keys):
                    # Check if this cell needs width update
                    cell_key = (row_idx, col_idx)
                    needs_width_update = self.cells_need_width_update.get(cell_key, False)
                    
                    # Apply alignment if specified for this column
                    cell_value = self._apply_alignment(display_value, col_idx)
                    self.update_cell(
                        row_keys[row_idx],
                        col_keys[col_idx],
                        cell_value,
                        update_width=needs_width_update
                    )
                    
                    # Clear the width update flag after applying
                    if needs_width_update and cell_key in self.cells_need_width_update:
                        del self.cells_need_width_update[cell_key]
            except Exception:
                pass  # Cell might not exist anymore
    
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
        self.cells_need_width_update.clear()
        if columns:
            self.column_flap_chars.clear()
            self.column_alignment.clear()
        return result