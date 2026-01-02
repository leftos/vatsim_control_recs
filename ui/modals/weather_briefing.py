"""Weather Briefing Modal Screen - Consolidated weather for a grouping/sector"""

import asyncio
import os
import re
import tempfile
import webbrowser
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from rich.console import Console
from textual.screen import ModalScreen
from textual.widgets import Static
from textual.containers import Container, VerticalScroll
from textual.binding import Binding
from textual.app import ComposeResult

from backend import get_metar_batch, get_taf_batch
from backend.data.vatsim_api import download_vatsim_data, get_atis_for_airports
from backend.data.atis_filter import filter_atis_text, colorize_atis_text
from ui import config
from ui.config import CATEGORY_COLORS, CATEGORY_ORDER
from ui.modals.metar_info import (
    get_flight_category,
    _extract_visibility_str,
    _parse_ceiling_layer
)

# Tower type priority for sorting (lower = larger/more significant airport)
TOWER_TYPE_PRIORITY = {
    'ATCT-TRACON': 0,
    'ATCT-RAPCON': 0,
    'ATCT-RATCF': 0,
    'ATCT-A/C': 1,
    'ATCT': 2,
    'NON-ATCT': 3,
    '': 4,
}


def _parse_wind_from_metar(metar: str) -> Optional[str]:
    """
    Extract wind string from METAR.

    Args:
        metar: Raw METAR string

    Returns:
        Wind string (e.g., "28012G18KT", "VRB05KT", "00000KT") or None
    """
    if not metar:
        return None

    # Wind pattern: direction + speed + optional gust + unit
    # Examples: 28012KT, 28012G18KT, VRB05KT, 00000KT
    wind_pattern = r'\b(\d{3}|VRB)(\d{2,3})(G\d{2,3})?(KT|MPS|KMH)\b'
    match = re.search(wind_pattern, metar)
    if match:
        return match.group(0)
    return None


def _parse_taf_changes(taf: str, current_category: str) -> List[Dict[str, Any]]:
    """
    Parse TAF to find forecast weather changes.

    Args:
        taf: Raw TAF string
        current_category: Current flight category (VFR/MVFR/IFR/LIFR)

    Returns:
        List of changes with type, time, predicted category, and improvement/deterioration flags
    """
    changes = []
    if not taf:
        return changes

    category_priority = {"LIFR": 0, "IFR": 1, "MVFR": 2, "VFR": 3}
    current_priority = category_priority.get(current_category, 3)

    # Parse FM groups: FM251800 ...conditions...
    fm_pattern = r'FM(\d{6})\s+([^\n]+?)(?=\s+FM|\s+TEMPO|\s+BECMG|\s+PROB|$)'
    for match in re.finditer(fm_pattern, taf, re.DOTALL):
        time_str = match.group(1)
        conditions = match.group(2)
        predicted_cat, _ = get_flight_category(conditions)
        pred_priority = category_priority.get(predicted_cat, 3)

        changes.append({
            'type': 'FM',
            'time_str': time_str,
            'category': predicted_cat,
            'is_improvement': pred_priority > current_priority,
            'is_deterioration': pred_priority < current_priority,
        })

    # Parse TEMPO/BECMG groups
    tempo_becmg_pattern = r'(TEMPO|BECMG)\s+(\d{4})/(\d{4})\s+([^\n]+?)(?=\s+TEMPO|\s+BECMG|\s+FM|\s+PROB|$)'
    for match in re.finditer(tempo_becmg_pattern, taf, re.DOTALL):
        change_type = match.group(1)
        from_time = match.group(2)
        to_time = match.group(3)
        conditions = match.group(4)
        predicted_cat, _ = get_flight_category(conditions)
        pred_priority = category_priority.get(predicted_cat, 3)

        changes.append({
            'type': change_type,
            'time_str': f"{from_time}/{to_time}",
            'category': predicted_cat,
            'is_improvement': pred_priority > current_priority,
            'is_deterioration': pred_priority < current_priority,
        })

    return changes


def _format_taf_relative_time(time_str: str) -> str:
    """
    Format a TAF time string with relative duration.

    Args:
        time_str: TAF time string, either "DDHHMM" (FM format) or "HHMM/HHMM" (TEMPO/BECMG)

    Returns:
        Relative time string like "(in 2h)" or "(1h ago)" or empty string if can't parse
    """
    now = datetime.now(timezone.utc)

    try:
        # FM format: DDHHMM (e.g., "251800" = 25th day at 18:00Z)
        if len(time_str) == 6 and time_str.isdigit():
            day = int(time_str[:2])
            hour = int(time_str[2:4])
            minute = int(time_str[4:6])

            # Build target datetime in current month
            target = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)

            # Handle month rollover
            if target < now - timedelta(days=15):
                # Target is likely next month
                if now.month == 12:
                    target = target.replace(year=now.year + 1, month=1)
                else:
                    target = target.replace(month=now.month + 1)
            elif target > now + timedelta(days=15):
                # Target is likely previous month
                if now.month == 1:
                    target = target.replace(year=now.year - 1, month=12)
                else:
                    target = target.replace(month=now.month - 1)

        # TEMPO/BECMG format: DDHH/DDHH (e.g., "2515/2518")
        elif '/' in time_str:
            start_str = time_str.split('/')[0]
            if len(start_str) == 4 and start_str.isdigit():
                day = int(start_str[:2])
                hour = int(start_str[2:4])

                target = now.replace(day=day, hour=hour, minute=0, second=0, microsecond=0)

                # Handle month rollover
                if target < now - timedelta(days=15):
                    if now.month == 12:
                        target = target.replace(year=now.year + 1, month=1)
                    else:
                        target = target.replace(month=now.month + 1)
                elif target > now + timedelta(days=15):
                    if now.month == 1:
                        target = target.replace(year=now.year - 1, month=12)
                    else:
                        target = target.replace(month=now.month - 1)
            else:
                return ""
        else:
            return ""

        # Calculate relative time
        diff_seconds = (target - now).total_seconds()
        abs_hours = abs(diff_seconds) / 3600

        if abs_hours < 1:
            mins = int(abs(diff_seconds) / 60)
            time_label = f"{mins}m"
        elif abs_hours < 24:
            hours = int(abs_hours)
            time_label = f"{hours}h"
        else:
            days = int(abs_hours / 24)
            time_label = f"{days}d"

        if diff_seconds > 0:
            return f" [dim](in {time_label})[/dim]"
        else:
            return f" [dim]({time_label} ago)[/dim]"

    except (ValueError, AttributeError):
        return ""


class WeatherBriefingScreen(ModalScreen):
    """Modal screen showing consolidated weather briefing for a grouping/sector"""

    CSS = """
    WeatherBriefingScreen {
        align: center middle;
    }

    #briefing-container {
        width: 90%;
        max-width: 120;
        height: 85%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #briefing-header {
        height: auto;
    }

    #briefing-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #briefing-summary {
        text-align: center;
        margin-bottom: 1;
    }

    #briefing-scroll {
        height: 1fr;
    }

    #briefing-content {
        height: auto;
    }

    .airport-card {
        margin-bottom: 1;
        padding: 0 1;
    }

    #briefing-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("q", "close", "Close"),
        Binding("p", "print", "Print"),
    ]

    def __init__(self, grouping_name: str, airports: List[str]):
        """
        Initialize the weather briefing modal.

        Args:
            grouping_name: Name of the grouping/sector
            airports: List of airport ICAO codes in this grouping
        """
        super().__init__()
        self.grouping_name = grouping_name
        self.airports = airports
        self.weather_data: Dict[str, Dict[str, Any]] = {}
        self._pending_tasks: list = []

    def compose(self) -> ComposeResult:
        with Container(id="briefing-container"):
            with Container(id="briefing-header"):
                yield Static(f"Weather Briefing: {self.grouping_name}", id="briefing-title")
                yield Static("Loading weather data...", id="briefing-summary")
            with VerticalScroll(id="briefing-scroll"):
                yield Static("", id="briefing-content", markup=True)
            yield Static("Escape/Q: Close | P: Print", id="briefing-hint")

    async def on_mount(self) -> None:
        """Load weather data asynchronously after modal is shown"""
        task = asyncio.create_task(self._fetch_all_weather_async())
        self._pending_tasks.append(task)

    def on_unmount(self) -> None:
        """Cancel pending tasks when modal is dismissed."""
        for task in self._pending_tasks:
            if not task.done():
                task.cancel()
        self._pending_tasks.clear()

    async def _fetch_all_weather_async(self) -> None:
        """Fetch all weather data in parallel"""
        loop = asyncio.get_event_loop()

        # Fetch METARs, TAFs, and VATSIM data in parallel
        metar_task = loop.run_in_executor(None, get_metar_batch, self.airports)
        taf_task = loop.run_in_executor(None, get_taf_batch, self.airports)
        vatsim_task = loop.run_in_executor(None, download_vatsim_data)

        metars, tafs, vatsim_data = await asyncio.gather(metar_task, taf_task, vatsim_task)

        # Extract ATIS info
        atis_data = {}
        if vatsim_data:
            atis_data = get_atis_for_airports(vatsim_data, self.airports)

        # Build weather data structure
        for icao in self.airports:
            metar = metars.get(icao, '')
            taf = tafs.get(icao, '')
            category, color = get_flight_category(metar) if metar else ("UNK", "white")

            self.weather_data[icao] = {
                'metar': metar,
                'taf': taf,
                'category': category,
                'color': color,
                'visibility': _extract_visibility_str(metar) if metar else None,
                'ceiling': _parse_ceiling_layer(metar) if metar else None,
                'wind': _parse_wind_from_metar(metar) if metar else None,
                'atis': atis_data.get(icao),
                'taf_changes': _parse_taf_changes(taf, category) if taf else [],
            }

        # Update display
        self._update_display()

    def _get_airport_size_priority(self, icao: str) -> int:
        """Get airport size priority for sorting (lower = larger airport)."""
        if not config.UNIFIED_AIRPORT_DATA:
            return 4
        airport_info = config.UNIFIED_AIRPORT_DATA.get(icao, {})
        tower_type = airport_info.get('tower_type', '')
        return TOWER_TYPE_PRIORITY.get(tower_type, 4)

    def _update_display(self) -> None:
        """Update the display with weather data"""
        # Count categories
        category_counts = {"LIFR": 0, "IFR": 0, "MVFR": 0, "VFR": 0, "UNK": 0}
        for data in self.weather_data.values():
            cat = data.get('category', 'UNK')
            category_counts[cat] = category_counts.get(cat, 0) + 1

        # Build summary
        summary_parts = []
        if category_counts["LIFR"] > 0:
            summary_parts.append(f"[magenta]{category_counts['LIFR']} LIFR[/magenta]")
        if category_counts["IFR"] > 0:
            summary_parts.append(f"[red]{category_counts['IFR']} IFR[/red]")
        if category_counts["MVFR"] > 0:
            summary_parts.append(f"[#5599ff]{category_counts['MVFR']} MVFR[/#5599ff]")
        if category_counts["VFR"] > 0:
            summary_parts.append(f"[#00ff00]{category_counts['VFR']} VFR[/#00ff00]")
        if category_counts["UNK"] > 0:
            summary_parts.append(f"[dim]{category_counts['UNK']} UNK[/dim]")

        summary_text = " | ".join(summary_parts) if summary_parts else "No weather data"
        summary_widget = self.query_one("#briefing-summary", Static)
        summary_widget.update(summary_text)

        # Group airports by category
        airports_by_category: Dict[str, List[tuple]] = {cat: [] for cat in CATEGORY_ORDER}
        for icao, data in self.weather_data.items():
            cat = data.get('category', 'UNK')
            size_priority = self._get_airport_size_priority(icao)
            airports_by_category[cat].append((icao, data, size_priority))

        # Sort within each category by size (larger airports first), then alphabetically
        for cat in airports_by_category:
            airports_by_category[cat].sort(key=lambda x: (x[2], x[0]))

        # Build content with category sections
        sections = []
        for cat in CATEGORY_ORDER:
            airports = airports_by_category[cat]
            if not airports:
                continue

            color = CATEGORY_COLORS.get(cat, 'white')
            # Section header
            section_header = f"[{color} bold]━━━ {cat} ({len(airports)}) ━━━[/{color} bold]"
            section_lines = [section_header]

            # Airport cards in this category
            for icao, data, _size in airports:
                card = self._build_airport_card(icao, data)
                section_lines.append(card)

            sections.append("\n".join(section_lines))

        content_widget = self.query_one("#briefing-content", Static)
        content_widget.update("\n\n".join(sections) if sections else "[dim]No weather data available[/dim]")

    def _build_airport_card(self, icao: str, data: Dict[str, Any]) -> str:
        """Build a Rich markup card for one airport"""
        lines = []

        # Header with airport name and colored category suffix
        category = data.get('category', 'UNK')
        color = CATEGORY_COLORS.get(category, 'white')
        pretty_name = config.DISAMBIGUATOR.get_full_name(icao) if config.DISAMBIGUATOR else icao

        header = f"{icao} - {pretty_name} // [{color} bold]{category}[/{color} bold]"
        lines.append(header)

        # Conditions line
        conditions_parts = []
        ceiling = data.get('ceiling')
        if ceiling:
            conditions_parts.append(f"Ceiling: {ceiling}")
        else:
            conditions_parts.append("Ceiling: CLR")

        visibility = data.get('visibility')
        if visibility:
            conditions_parts.append(f"Vis: {visibility}")

        wind = data.get('wind')
        if wind:
            conditions_parts.append(f"Wind: {wind}")

        if conditions_parts:
            lines.append("  " + " | ".join(conditions_parts))

        # TAF changes (only show significant ones)
        taf_changes = data.get('taf_changes', [])
        significant_changes = [c for c in taf_changes if c.get('is_improvement') or c.get('is_deterioration')]

        for change in significant_changes[:2]:  # Show at most 2 changes
            change_type = change.get('type', '')
            time_str = change.get('time_str', '')
            pred_category = change.get('category', 'UNK')

            if change.get('is_deterioration'):
                indicator = "[red bold]![/red bold]"
                direction = "worsening"
            else:
                indicator = "[green bold]+[/green bold]"
                direction = "improving"

            relative_time = _format_taf_relative_time(time_str)
            pred_color = CATEGORY_COLORS.get(pred_category, 'white')
            lines.append(f"  {indicator} TAF {change_type} {time_str}{relative_time}: [{pred_color} bold]{pred_category}[/{pred_color} bold] ({direction})")

        # ATIS info (filtered to show only non-METAR info)
        atis = data.get('atis')
        if atis:
            atis_code = atis.get('atis_code', '')
            raw_text = atis.get('text_atis', '')
            # Filter out METAR-duplicated info
            filtered_text = filter_atis_text(raw_text)
            if filtered_text:
                # Colorize ATIS letter if present
                display_text = colorize_atis_text(filtered_text, atis_code)
                lines.append(f"  [dim]{display_text}[/dim]")

        return "\n".join(lines)

    def action_close(self) -> None:
        """Close the modal"""
        self.dismiss()

    def action_print(self) -> None:
        """Export weather briefing as HTML and open in browser"""
        if not self.weather_data:
            self.notify("No weather data to export", severity="warning")
            return

        # Build the content using Rich Console for HTML export
        # Use wide width to prevent Rich from wrapping - let CSS handle wrapping instead
        console = Console(record=True, force_terminal=True, width=500)

        # Title and timestamp
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
        console.print(f"[bold]Weather Briefing: {self.grouping_name}[/bold]")
        console.print(f"Generated: {timestamp}\n")

        # Summary
        category_counts = {"LIFR": 0, "IFR": 0, "MVFR": 0, "VFR": 0, "UNK": 0}
        for data in self.weather_data.values():
            cat = data.get('category', 'UNK')
            category_counts[cat] = category_counts.get(cat, 0) + 1

        summary_parts = []
        if category_counts["LIFR"] > 0:
            summary_parts.append(f"[magenta]{category_counts['LIFR']} LIFR[/magenta]")
        if category_counts["IFR"] > 0:
            summary_parts.append(f"[red]{category_counts['IFR']} IFR[/red]")
        if category_counts["MVFR"] > 0:
            summary_parts.append(f"[#5599ff]{category_counts['MVFR']} MVFR[/#5599ff]")
        if category_counts["VFR"] > 0:
            summary_parts.append(f"[#00ff00]{category_counts['VFR']} VFR[/#00ff00]")
        if category_counts["UNK"] > 0:
            summary_parts.append(f"[dim]{category_counts['UNK']} UNK[/dim]")

        if summary_parts:
            console.print(" | ".join(summary_parts))
        console.print()

        # Group airports by category
        airports_by_category: Dict[str, list] = {cat: [] for cat in CATEGORY_ORDER}
        for icao, data in self.weather_data.items():
            cat = data.get('category', 'UNK')
            size_priority = self._get_airport_size_priority(icao)
            airports_by_category[cat].append((icao, data, size_priority))

        for cat in airports_by_category:
            airports_by_category[cat].sort(key=lambda x: (x[2], x[0]))

        # Build content with category sections
        for cat in CATEGORY_ORDER:
            airports = airports_by_category[cat]
            if not airports:
                continue

            color = CATEGORY_COLORS.get(cat, 'white')
            console.print(f"[{color} bold]━━━ {cat} ({len(airports)}) ━━━[/{color} bold]")

            for icao, data, _size in airports:
                card = self._build_airport_card(icao, data)
                console.print(card)
            console.print()

        # Export to HTML
        html_content = console.export_html(inline_styles=True)

        # Add CSS for better printing - remove default padding/margins, wrap long lines
        html_content = html_content.replace(
            '</style>',
            '''pre { margin: 0; padding: 0; white-space: pre-wrap; word-wrap: break-word; max-width: 100ch; }
body { margin: 20px; }
</style>'''
        )
        # Remove HTML indentation that causes left padding
        html_content = html_content.replace('<body>\n    <pre', '<body>\n<pre')

        # Write to temp file and open in browser
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.html',
            prefix=f'weather_briefing_{self.grouping_name}_',
            delete=False,
            encoding='utf-8'
        ) as f:
            f.write(html_content)
            temp_path = f.name

        # Open in default browser
        webbrowser.open(f'file://{temp_path}')
        self.notify(f"Opened in browser: {os.path.basename(temp_path)}")
