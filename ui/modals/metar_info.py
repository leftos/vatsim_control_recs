"""METAR and TAF Information Modal Screen"""

import asyncio
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from textual.screen import ModalScreen
from textual.widgets import Static, Input
from textual.containers import Container, VerticalScroll
from textual.binding import Binding
from textual.app import ComposeResult

from backend import get_metar, get_taf
from backend.data.vatsim_api import download_vatsim_data, get_atis_for_airports
from backend.data.atis_filter import filter_atis_text, colorize_atis_text
from backend.data.weather_parsing import (
    get_flight_category,
    parse_visibility_sm,
    parse_ceiling_feet,
    parse_ceiling_layer,
    extract_visibility_str,
    extract_flight_rules_weather,
    parse_weather_phenomena,
    parse_wind_from_metar,
    is_speci_metar,
    WEATHER_PHENOMENA,
    WEATHER_DESCRIPTORS,
    WEATHER_INTENSITY,
)
from ui import config
from ui.config import CATEGORY_COLORS


# Backward-compatible aliases for any external imports (deprecated)
_parse_visibility_sm = parse_visibility_sm
_parse_ceiling_feet = parse_ceiling_feet
_parse_ceiling_layer = parse_ceiling_layer
_extract_visibility_str = extract_visibility_str
_extract_flight_rules_weather = extract_flight_rules_weather


class MetarInfoScreen(ModalScreen):
    """Modal screen showing full METAR and TAF for an airport"""
    
    CSS = """
    MetarInfoScreen {
        align: center middle;
    }

    #metar-container {
        width: 80;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #metar-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #metar-input-container {
        height: auto;
        margin-bottom: 1;
    }

    #metar-scroll {
        height: auto;
        max-height: 100%;
    }

    #metar-result {
        text-align: left;
        height: auto;
        margin-top: 1;
        padding: 1;
    }

    #metar-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """
    
    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("enter", "fetch_metar", "Fetch METAR", priority=True),
    ]

    def __init__(self, initial_icao: str | None = None):
        """
        Initialize the METAR lookup modal.

        Args:
            initial_icao: Optional ICAO code to pre-fill and auto-fetch
        """
        super().__init__()
        self.metar_result = ""
        self.initial_icao = initial_icao
        self.current_icao: str | None = None  # Track current airport for VFR alternatives (used by global Ctrl+A)
        self._autofill_clear_available = initial_icao is not None  # First backspace clears autofilled input

    def compose(self) -> ComposeResult:
        with Container(id="metar-container"):
            yield Static("METAR & TAF Lookup", id="metar-title")
            with Container(id="metar-input-container"):
                yield Input(placeholder="Enter airport ICAO code (e.g., KSFO)", id="metar-input")
            with VerticalScroll(id="metar-scroll"):
                yield Static("", id="metar-result", markup=True)
            yield Static("Press Enter to fetch, Escape to close", id="metar-hint")

    def on_mount(self) -> None:
        """Focus the input when mounted, and auto-fetch if initial_icao provided"""
        metar_input = self.query_one("#metar-input", Input)

        if self.initial_icao:
            # Pre-fill and auto-fetch
            metar_input.value = self.initial_icao
            self.action_fetch_metar()
        else:
            metar_input.focus()

    def on_key(self, event) -> None:
        """Handle key events, including special backspace behavior for autofilled input."""
        if event.key == "backspace" and self._autofill_clear_available:
            # Clear the entire input on first backspace after autofill
            metar_input = self.query_one("#metar-input", Input)
            metar_input.value = ""
            metar_input.focus()
            self._autofill_clear_available = False
            event.prevent_default()
            event.stop()
        elif self._autofill_clear_available and event.is_printable:
            # User is typing - disable the clear-on-backspace behavior
            self._autofill_clear_available = False
    
    def _parse_taf_time(self, time_str: str, current_month: int, current_year: int) -> Optional[datetime]:
        """
        Parse TAF time string to datetime object.
        
        Args:
            time_str: Time string in format DDHH or DDHHMM
            current_month: Current month (1-12)
            current_year: Current year
            
        Returns:
            datetime object in UTC or None if parsing fails
        """
        try:
            if len(time_str) == 4:  # DDHH format
                day = int(time_str[0:2])
                hour = int(time_str[2:4])
                minute = 0
            elif len(time_str) == 6:  # DDHHMM format
                day = int(time_str[0:2])
                hour = int(time_str[2:4])
                minute = int(time_str[4:6])
            else:
                return None
            
            # Handle hour=24 (which means 00 of next day)
            if hour == 24:
                day += 1
                hour = 0
            
            # Handle day overflow (e.g., day 32 in a 31-day month)
            # Create a base datetime and let it handle month/year rollover
            from calendar import monthrange
            _, days_in_month = monthrange(current_year, current_month)
            if day > days_in_month:
                # Roll over to next month
                if current_month == 12:
                    return datetime(current_year + 1, 1, day - days_in_month, hour, minute, tzinfo=timezone.utc)
                else:
                    return datetime(current_year, current_month + 1, day - days_in_month, hour, minute, tzinfo=timezone.utc)
            
            return datetime(current_year, current_month, day, hour, minute, tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None

    def _format_relative_time(self, target_time: Optional[datetime], current_time: datetime) -> str:
        """
        Format a relative time duration (e.g., "in 2h", "1h ago").

        Args:
            target_time: Target datetime in UTC
            current_time: Current datetime in UTC

        Returns:
            Formatted relative time string with dim markup, or empty string if invalid
        """
        if not target_time:
            return ""

        diff_seconds = (target_time - current_time).total_seconds()
        abs_hours = abs(diff_seconds) / 3600

        if abs_hours < 1:
            mins = int(abs(diff_seconds) / 60)
            time_str = f"{mins}m"
        elif abs_hours < 24:
            hours = int(abs_hours)
            time_str = f"{hours}h"
        else:
            days = int(abs_hours / 24)
            time_str = f"{days}d"

        if diff_seconds > 0:
            return f" [dim](in {time_str})[/dim]"
        else:
            return f" [dim]({time_str} ago)[/dim]"

    def _highlight_flight_category_components(self, metar: str) -> str:
        """
        Highlight visibility and ceiling components in METAR that determine flight category.

        Args:
            metar: Raw METAR string

        Returns:
            METAR string with rich text markup highlighting flight category components
        """
        if not metar:
            return metar

        # Get flight category and color
        _category = get_flight_category(metar)
        color = CATEGORY_COLORS.get(_category, "white")

        # Extract visibility and ceiling strings
        vis_str, ceiling_str = _extract_flight_rules_weather(metar)

        highlighted = metar

        # Highlight visibility if present using the flight category color
        if vis_str:
            highlighted = highlighted.replace(vis_str, f"[bold underline {color}]{vis_str}[/bold underline {color}]", 1)

        # Highlight ceiling if present using the flight category color
        if ceiling_str:
            highlighted = highlighted.replace(ceiling_str, f"[bold underline {color}]{ceiling_str}[/bold underline {color}]", 1)

        return highlighted

    def _colorize_taf(self, taf: str) -> str:
        """
        Colorize the TAF entry applicable to the current Zulu time and add relative times.

        Args:
            taf: Raw TAF string

        Returns:
            TAF string with rich text markup for the current period and relative time annotations
        """
        if not taf:
            return taf

        current_time = datetime.now(timezone.utc)
        current_month = current_time.month
        current_year = current_time.year

        # Split TAF into lines
        lines = taf.split('\n')
        colorized_lines = []

        for line in lines:
            # Skip empty lines
            if not line.strip():
                colorized_lines.append(line)
                continue

            # Check if this is a TAF header line with valid period
            # Format: TAF ICAO DDHHMM DDHH/DDHH ...
            header_match = re.search(r'TAF\s+\w{4}\s+\d{6}Z?\s+(\d{4})/(\d{4})', line)
            if header_match:
                valid_from_str = header_match.group(1)
                valid_to_str = header_match.group(2)
                valid_from = self._parse_taf_time(valid_from_str, current_month, current_year)
                valid_to = self._parse_taf_time(valid_to_str, current_month, current_year)

                # Handle month rollover for valid_to
                if valid_to and valid_from and valid_to < valid_from:
                    if current_month == 12:
                        valid_to = valid_to.replace(month=1, year=current_year + 1)
                    else:
                        valid_to = valid_to.replace(month=current_month + 1)

                # Add relative time annotation after the period
                relative_from = self._format_relative_time(valid_from, current_time)
                period_end = header_match.end()
                annotated_line = line[:period_end] + relative_from + line[period_end:]

                # Check if current time is within this period
                if valid_from and valid_to and valid_from <= current_time <= valid_to:
                    conditions = line[period_end:]
                    colorized_lines.append(f"{line[:period_end]}{relative_from}[bold yellow]{conditions}[/bold yellow]")
                else:
                    colorized_lines.append(annotated_line)
                continue

            # Check for FM (FROM) groups: FM DDHHMM
            fm_match = re.search(r'\s+(FM\d{6})\s+', line)
            if fm_match:
                fm_time_str = fm_match.group(1)[2:]  # Remove 'FM' prefix
                fm_time = self._parse_taf_time(fm_time_str, current_month, current_year)

                # Add relative time annotation after FM time
                relative_time = self._format_relative_time(fm_time, current_time)
                fm_end = fm_match.end()
                annotated_line = line[:fm_end-1] + relative_time + " " + line[fm_end:]

                if fm_time:
                    # FM periods are valid from their time until the next FM or end of TAF
                    time_diff = (current_time - fm_time).total_seconds()
                    if 0 <= time_diff <= 86400:  # Within 24 hours from FM time
                        conditions = line[fm_end:]
                        colorized_lines.append(f"{line[:fm_end-1]}{relative_time} [bold yellow]{conditions}[/bold yellow]")
                    else:
                        colorized_lines.append(annotated_line)
                else:
                    colorized_lines.append(annotated_line)
                continue

            # Check for TEMPO or BECMG groups: TEMPO DDHH/DDHH or BECMG DDHH/DDHH
            tempo_becmg_match = re.search(r'\s+(TEMPO|BECMG)\s+(\d{4})/(\d{4})', line)
            if tempo_becmg_match:
                valid_from_str = tempo_becmg_match.group(2)
                valid_to_str = tempo_becmg_match.group(3)
                valid_from = self._parse_taf_time(valid_from_str, current_month, current_year)
                valid_to = self._parse_taf_time(valid_to_str, current_month, current_year)

                # Handle hour rollover (same day or next day)
                if valid_to and valid_from and valid_to < valid_from:
                    valid_to = valid_to.replace(day=valid_to.day + 1)

                # Add relative time annotation after the period
                relative_from = self._format_relative_time(valid_from, current_time)
                group_end = tempo_becmg_match.end()
                annotated_line = line[:group_end] + relative_from + line[group_end:]

                # Check if current time is within this period
                if valid_from and valid_to and valid_from <= current_time <= valid_to:
                    conditions = line[group_end:]
                    colorized_lines.append(f"{line[:group_end]}{relative_from}[bold yellow]{conditions}[/bold yellow]")
                else:
                    colorized_lines.append(annotated_line)
                continue

            # Default: no colorization
            colorized_lines.append(line)

        return '\n'.join(colorized_lines)
    
    def action_fetch_metar(self) -> None:
        """Fetch METAR and TAF for the entered airport"""
        metar_input = self.query_one("#metar-input", Input)
        icao = metar_input.value.strip().upper()

        if not icao:
            result_widget = self.query_one("#metar-result", Static)
            result_widget.update("Please enter an airport ICAO code")
            self.current_icao = None
            self._update_hint(None)
            return

        # Track current airport
        self.current_icao = icao

        # Get full name if available (no length limit)
        pretty_name = config.DISAMBIGUATOR.get_full_name(icao) if config.DISAMBIGUATOR else icao

        # Show loading indicator
        result_widget = self.query_one("#metar-result", Static)
        result_widget.update(f"[bold]{pretty_name} ({icao})[/bold]\n\n[dim]Loading METAR & TAF...[/dim]")

        # Fetch asynchronously
        asyncio.create_task(self._fetch_metar_async(icao, pretty_name))

    async def _fetch_metar_async(self, icao: str, pretty_name: str) -> None:
        """Fetch ATIS, METAR and TAF asynchronously"""
        loop = asyncio.get_event_loop()

        # Fetch METAR, TAF, and VATSIM data (for ATIS) in parallel
        metar_task = loop.run_in_executor(None, get_metar, icao)
        taf_task = loop.run_in_executor(None, get_taf, icao)
        vatsim_task = loop.run_in_executor(None, download_vatsim_data)

        metar, taf, vatsim_data = await asyncio.gather(metar_task, taf_task, vatsim_task)

        result_widget = self.query_one("#metar-result", Static)

        # Build display
        result_lines = []

        # Airport title first with flight category on same line
        category: Optional[str] = None
        if metar:
            category = get_flight_category(metar)
            color = CATEGORY_COLORS.get(category, "white")
            result_lines.append(f"{pretty_name} ({icao}) // [{color} bold]{category}[/{color} bold]")
            result_lines.append("")
        else:
            result_lines.append(f"{pretty_name} ({icao})")
            result_lines.append("")

        # ATIS (filtered to remove METAR-duplicated info, with colorized runway/approach info)
        if vatsim_data:
            atis_info = self._get_atis_for_airport(vatsim_data, icao)
            if atis_info:
                atis_code = atis_info.get('atis_code', '')
                raw_text = atis_info.get('text_atis', '')
                # Filter out METAR-duplicated info
                filtered_text = filter_atis_text(raw_text)
                if filtered_text:
                    # Colorize ATIS: letter in cyan, approaches/runways in yellow
                    colorized_text = colorize_atis_text(filtered_text, atis_code)
                    result_lines.append(f"[dim]{colorized_text}[/dim]")
                    result_lines.append("")

        # METAR with highlighted flight category components
        if metar:
            # Check if this is a SPECI (special) report
            if is_speci_metar(metar):
                result_lines.append("[bold #ff9900]⚠ SPECI[/bold #ff9900] [dim](significant weather change)[/dim]")
            highlighted_metar = self._highlight_flight_category_components(metar)
            result_lines.append(highlighted_metar)
            self._update_hint(category)
        else:
            result_lines.append("METAR: No data available")
            self._update_hint(None)

        result_lines.append("")  # Add blank line between METAR and TAF

        # Add TAF with colorization
        if taf:
            colorized_taf = self._colorize_taf(taf)
            result_lines.append(colorized_taf)
        else:
            result_lines.append("TAF: No data available")

        result_widget.update("\n".join(result_lines))

    def _get_atis_for_airport(self, vatsim_data: dict, icao: str) -> dict | None:
        """Extract ATIS for a specific airport from VATSIM data."""
        atis_dict = get_atis_for_airports(vatsim_data, [icao])
        return atis_dict.get(icao)

    def _update_hint(self, category: str | None) -> None:
        """Update the hint text based on current flight category"""
        hint_widget = self.query_one("#metar-hint", Static)
        if category in ('IFR', 'LIFR'):
            hint_widget.update("Press Enter to fetch, [bold]Ctrl+A[/bold] for VFR alternatives, Escape to close")
        else:
            hint_widget.update("Press Enter to fetch, Escape to close")

    def action_close(self) -> None:
        """Close the modal"""
        self.dismiss()