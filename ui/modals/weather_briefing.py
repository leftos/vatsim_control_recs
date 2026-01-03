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
from backend.briefing import (
    AreaClusterer,
    count_area_categories,
    build_area_summary,
    parse_wind_from_metar,
    parse_taf_changes,
    format_taf_relative_time,
)
from backend.data.weather_parsing import (
    get_flight_category,
    extract_visibility_str,
    parse_ceiling_layer,
    parse_visibility_sm,
    parse_ceiling_feet,
    parse_weather_phenomena,
)
from ui import config
from ui.config import CATEGORY_COLORS, CATEGORY_ORDER


def _parse_metar_observation_time(metar: str) -> Optional[tuple]:
    """
    Parse observation time from METAR string.

    Args:
        metar: Raw METAR string

    Returns:
        Tuple of (zulu_str, local_str) like ("1856Z", "10:56 LT") or None
    """
    if not metar:
        return None

    # METAR timestamp format: DDHHMM Z (e.g., "251856Z")
    match = re.search(r'\b(\d{2})(\d{2})(\d{2})Z\b', metar)
    if not match:
        return None

    day = int(match.group(1))
    hour = int(match.group(2))
    minute = int(match.group(3))

    zulu_str = f"{hour:02d}{minute:02d}Z"

    # Convert to local time
    now = datetime.now(timezone.utc)
    try:
        obs_time = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
        # Handle month rollover (observation could be from previous month)
        if obs_time > now + timedelta(hours=2):  # More than 2h in future = likely prev month
            if now.month == 1:
                obs_time = obs_time.replace(year=now.year - 1, month=12)
            else:
                obs_time = obs_time.replace(month=now.month - 1)

        # Convert to local time
        local_time = obs_time.astimezone()
        local_str = local_time.strftime("%H:%M LT")

        return (zulu_str, local_str)
    except (ValueError, OSError):
        return (zulu_str, None)


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

    def __init__(self, grouping_name: str, airports: List[str], primary_airports: Optional[List[str]] = None):
        """
        Initialize the weather briefing modal.

        Args:
            grouping_name: Name of the grouping/sector
            airports: List of airport ICAO codes in this grouping
            primary_airports: Optional list of primary airports to highlight at the top
        """
        super().__init__()
        self.grouping_name = grouping_name
        self.airports = airports
        self.primary_airports = primary_airports or []
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

    def _update_progress(self, message: str) -> None:
        """Update the progress message in the summary widget."""
        try:
            summary_widget = self.query_one("#briefing-summary", Static)
            summary_widget.update(f"[dim]{message}[/dim]")
        except Exception:
            pass  # Widget may not be ready yet

    async def _fetch_all_weather_async(self) -> None:
        """Fetch all weather data in parallel"""
        loop = asyncio.get_event_loop()
        total_airports = len(self.airports)

        # Track completion status for parallel fetches
        progress = {
            'metar': (0, total_airports),
            'taf': (0, total_airports),
            'vatsim': False
        }

        def update_fetch_progress():
            metar_done, metar_total = progress['metar']
            taf_done, taf_total = progress['taf']
            vatsim_done = progress['vatsim']

            metar_str = f"[green]METAR ✓[/green]" if metar_done == metar_total else f"[dim]METAR {metar_done}/{metar_total}[/dim]"
            taf_str = f"[green]TAF ✓[/green]" if taf_done == taf_total else f"[dim]TAF {taf_done}/{taf_total}[/dim]"
            vatsim_str = f"[green]ATIS ✓[/green]" if vatsim_done else "[dim]ATIS...[/dim]"

            self._update_progress(f"Fetching: {metar_str} | {taf_str} | {vatsim_str}")

        # Progress callbacks for batch functions
        def metar_progress(completed, total):
            progress['metar'] = (completed, total)
            # Schedule UI update on main thread
            loop.call_soon_threadsafe(update_fetch_progress)

        def taf_progress(completed, total):
            progress['taf'] = (completed, total)
            loop.call_soon_threadsafe(update_fetch_progress)

        # Initial progress
        update_fetch_progress()

        # Create tasks for parallel fetching
        async def fetch_metars():
            result = await loop.run_in_executor(
                None,
                lambda: get_metar_batch(self.airports, progress_callback=metar_progress)
            )
            return result

        async def fetch_tafs():
            result = await loop.run_in_executor(
                None,
                lambda: get_taf_batch(self.airports, progress_callback=taf_progress)
            )
            return result

        async def fetch_vatsim():
            result = await loop.run_in_executor(None, download_vatsim_data)
            progress['vatsim'] = True
            update_fetch_progress()
            return result

        # Fetch all in parallel
        metars, tafs, vatsim_data = await asyncio.gather(
            fetch_metars(), fetch_tafs(), fetch_vatsim()
        )

        # Extract ATIS info
        self._update_progress("Extracting ATIS information...")
        await asyncio.sleep(0)
        atis_data = {}
        if vatsim_data:
            atis_data = get_atis_for_airports(vatsim_data, self.airports)

        # Build weather data structure with progress updates
        update_interval = max(1, total_airports // 20)  # Update ~20 times during processing
        for i, icao in enumerate(self.airports):
            if i % update_interval == 0:
                pct = int((i / total_airports) * 100)
                bar_filled = pct // 5  # 20 chars total
                bar_empty = 20 - bar_filled
                bar = f"[green]{'█' * bar_filled}[/green][dim]{'░' * bar_empty}[/dim]"
                self._update_progress(f"Processing weather: {bar} {pct}%")
                await asyncio.sleep(0)

            metar = metars.get(icao, '')
            taf = tafs.get(icao, '')
            category = get_flight_category(metar) if metar else "UNK"
            color = CATEGORY_COLORS.get(category, "white")

            # Parse visibility and ceiling for trend comparison
            visibility_sm = parse_visibility_sm(metar) if metar else None
            ceiling_ft = parse_ceiling_feet(metar) if metar else None

            self.weather_data[icao] = {
                'metar': metar,
                'taf': taf,
                'category': category,
                'color': color,
                'visibility': extract_visibility_str(metar) if metar else None,
                'visibility_sm': visibility_sm,
                'ceiling': parse_ceiling_layer(metar) if metar else None,
                'ceiling_ft': ceiling_ft,
                'wind': parse_wind_from_metar(metar) if metar else None,
                'atis': atis_data.get(icao),
                'phenomena': parse_weather_phenomena(metar) if metar else [],
                'taf_changes': parse_taf_changes(taf, category, visibility_sm, ceiling_ft) if taf else [],
            }

        # Final progress
        self._update_progress("Building area groups...")
        await asyncio.sleep(0)

        # Update display
        self._update_display()

    def _create_area_groups(self) -> List[Dict[str, Any]]:
        """Create area-based groupings using shared AreaClusterer."""
        if not config.UNIFIED_AIRPORT_DATA:
            return []
        clusterer = AreaClusterer(
            weather_data=self.weather_data,
            unified_airport_data=config.UNIFIED_AIRPORT_DATA,
            disambiguator=config.DISAMBIGUATOR,
        )
        return clusterer.create_area_groups()

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

        # Add timestamp (Zulu + Local)
        now = datetime.now(timezone.utc)
        zulu_str = now.strftime("%H%MZ")
        local_str = now.astimezone().strftime("%H:%M LT")
        timestamp = f"[dim]{zulu_str} ({local_str})[/dim]"

        summary_text = " | ".join(summary_parts) if summary_parts else "No weather data"
        summary_text = f"{timestamp} | {summary_text}" if summary_text else timestamp
        summary_widget = self.query_one("#briefing-summary", Static)
        summary_widget.update(summary_text)

        # Build content sections
        sections = []

        # Primary airports section (highlighted at the top)
        if self.primary_airports:
            primary_section_lines = ["[bold cyan]━━━ REQUESTED ━━━[/bold cyan]"]
            for icao in self.primary_airports:
                data = self.weather_data.get(icao, {})
                if data:
                    card = self._build_airport_card(icao, data, is_primary=True)
                    primary_section_lines.append(card)
            if len(primary_section_lines) > 1:  # Has content beyond header
                sections.append("\n".join(primary_section_lines))

        # Use area-based grouping (centered around ATIS airports)
        # First, temporarily exclude primary airports from weather_data for grouping
        original_weather_data = self.weather_data
        if self.primary_airports:
            self.weather_data = {
                k: v for k, v in self.weather_data.items()
                if k not in self.primary_airports
            }

        # Create area groups
        area_groups = self._create_area_groups()

        # Restore original weather_data
        self.weather_data = original_weather_data

        # Build area sections
        for area in area_groups:
            if not area['airports']:
                continue

            # Count categories and build summary using shared functions
            area_cats = count_area_categories(area['airports'])
            area_summary = build_area_summary(area_cats, color_scheme="ui")

            # Section header with area name and summary
            section_header = f"[bold cyan]━━━ {area['name'].upper()} ━━━[/bold cyan]"
            if area_summary:
                section_header += f"\n{area_summary}"
            section_lines = [section_header]

            # Airport cards in this area
            for icao, data, _size in area['airports']:
                card = self._build_airport_card(icao, data)
                section_lines.append(card)

            sections.append("\n".join(section_lines))

        content_widget = self.query_one("#briefing-content", Static)
        content_widget.update("\n\n".join(sections) if sections else "[dim]No weather data available[/dim]")

    def _build_airport_card(self, icao: str, data: Dict[str, Any], is_primary: bool = False) -> str:
        """Build a Rich markup card for one airport"""
        lines = []

        # Header with airport name and colored category suffix
        category = data.get('category', 'UNK')
        color = CATEGORY_COLORS.get(category, 'white')
        pretty_name = config.DISAMBIGUATOR.get_full_name(icao) if config.DISAMBIGUATOR else icao

        # Primary airports get bold ICAO
        if is_primary:
            header = f"[bold]{icao}[/bold] - {pretty_name} // [{color} bold]{category}[/{color} bold]"
        else:
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

        # Weather phenomena line
        phenomena = data.get('phenomena', [])
        if phenomena:
            lines.append(f"  Weather: {', '.join(phenomena)}")

        # TAF changes (only show significant ones - improving or worsening)
        taf_changes = data.get('taf_changes', [])
        significant_changes = [c for c in taf_changes if c.get('is_improvement') or c.get('is_deterioration')]

        if significant_changes:
            # Pre-process all TAF entries to calculate column widths for alignment
            taf_entries = []
            for change in significant_changes[:2]:  # Show at most 2 changes
                change_type = change.get('type', '')
                time_str = change.get('time_str', '')
                pred_category = change.get('category', 'UNK')
                trend = change.get('trend', 'stable')

                # Trend indicator: ▼ worsening, ▲ improving
                if trend == 'worsening':
                    indicator = "[red bold]▼[/red bold]"
                else:
                    indicator = "[green bold]▲[/green bold]"

                raw_relative = format_taf_relative_time(time_str)
                relative_time = f" [dim]{raw_relative}[/dim]" if raw_relative else ""
                pred_color = CATEGORY_COLORS.get(pred_category, 'white')

                # Build parts dict for each column
                parts = {
                    'vis': '',
                    'ceil': '',
                    'wind': '',
                    'wx': '',
                }

                # Visibility
                vis_sm = change.get('visibility_sm')
                if vis_sm is not None:
                    if vis_sm >= 6:
                        parts['vis'] = "P6SM"
                    elif vis_sm == int(vis_sm):
                        parts['vis'] = f"{int(vis_sm)}SM"
                    else:
                        parts['vis'] = f"{vis_sm:.1f}SM"

                # Ceiling (use METAR format like BKN020)
                ceiling_layer = change.get('ceiling_layer')
                if ceiling_layer:
                    parts['ceil'] = ceiling_layer

                # Wind
                taf_wind = change.get('wind')
                if taf_wind:
                    parts['wind'] = taf_wind

                # Weather phenomena
                taf_phenomena = change.get('phenomena', [])
                if taf_phenomena:
                    parts['wx'] = ', '.join(taf_phenomena)

                taf_entries.append({
                    'indicator': indicator,
                    'change_type': change_type,
                    'time_str': time_str,
                    'relative_time': relative_time,
                    'pred_category': pred_category,
                    'pred_color': pred_color,
                    'parts': parts,
                })

            # Calculate max width for each column (including time prefix and category)
            # Strip markup from relative_time for length calculation
            def strip_markup(s: str) -> str:
                """Remove Rich markup tags for length calculation"""
                import re
                return re.sub(r'\[/?[^\]]+\]', '', s)

            max_widths = {
                'prefix': max((len(f"TAF {e['change_type']} {e['time_str']}{strip_markup(e['relative_time'])}") for e in taf_entries), default=0),
                'cat': max((len(e['pred_category']) for e in taf_entries), default=0),
                'vis': max((len(e['parts']['vis']) for e in taf_entries), default=0),
                'ceil': max((len(e['parts']['ceil']) for e in taf_entries), default=0),
                'wind': max((len(e['parts']['wind']) for e in taf_entries), default=0),
                'wx': max((len(e['parts']['wx']) for e in taf_entries), default=0),
            }

            # Build aligned TAF lines
            for entry in taf_entries:
                parts = entry['parts']
                pred_color = entry['pred_color']
                pred_category = entry['pred_category']

                # Build prefix and pad to align
                prefix_plain = f"TAF {entry['change_type']} {entry['time_str']}{strip_markup(entry['relative_time'])}"
                prefix_padding = ' ' * (max_widths['prefix'] - len(prefix_plain))
                prefix_with_markup = f"TAF {entry['change_type']} {entry['time_str']}{entry['relative_time']}{prefix_padding}"

                # Right-align category (VFR/MVFR/IFR/LIFR) so they line up
                cat_padded = pred_category.rjust(max_widths['cat'])

                # Build padded parts list (only include non-empty columns that have data in any row)
                # Visibility is right-aligned so numbers line up (P6SM aligns with 2SM)
                # Other columns are left-aligned
                padded_parts = []
                if max_widths['vis'] > 0:
                    padded_parts.append(parts['vis'].rjust(max_widths['vis']))
                if max_widths['ceil'] > 0:
                    padded_parts.append(parts['ceil'].ljust(max_widths['ceil']))
                if max_widths['wind'] > 0:
                    padded_parts.append(parts['wind'].ljust(max_widths['wind']))
                if max_widths['wx'] > 0:
                    padded_parts.append(parts['wx'].ljust(max_widths['wx']))

                details_str = " | ".join(padded_parts).rstrip() if padded_parts else ""
                if details_str:
                    lines.append(f"  {entry['indicator']} {prefix_with_markup}: [{pred_color} bold]{cat_padded}[/{pred_color} bold] - {details_str}")
                else:
                    lines.append(f"  {entry['indicator']} {prefix_with_markup}: [{pred_color} bold]{cat_padded}[/{pred_color} bold]")

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

        # Title and timestamp (Zulu only for web - local time varies by viewer)
        now = datetime.now(timezone.utc)
        zulu_str = now.strftime("%Y-%m-%d %H%MZ")
        console.print(f"[bold]Weather Briefing: {self.grouping_name}[/bold]")
        console.print(f"Generated: {zulu_str}\n")

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

        if summary_parts:
            console.print(" | ".join(summary_parts))
        console.print()

        # Create area groups (centered around ATIS airports)
        area_groups = self._create_area_groups()

        # Build content with area sections
        for area in area_groups:
            if not area['airports']:
                continue

            # Area header and summary
            area_cats = count_area_categories(area['airports'])
            area_summary = build_area_summary(area_cats, color_scheme="ui")

            console.print(f"[bold cyan]━━━ {area['name'].upper()} ━━━[/bold cyan]")
            if area_summary:
                console.print(area_summary)

            for icao, data, _size in area['airports']:
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
