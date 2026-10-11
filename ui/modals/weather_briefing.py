"""Weather Briefing Modal Screen - Consolidated weather for a grouping/sector"""

import asyncio
import os
import re
import tempfile
import webbrowser
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Any, ClassVar

from rich.console import Console
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from backend import get_metar_batch, get_taf_batch, get_weather_smart
from backend.briefing import (
    AreaClusterer,
    build_area_summary,
    count_area_categories,
    format_taf_relative_time,
    parse_taf_changes,
    parse_wind_from_metar,
)
from backend.data.atis_filter import colorize_atis_text, filter_atis_text
from backend.data.datis_api import get_datis_for_airports
from backend.data.vatsim_api import download_vatsim_data, get_atis_for_airports
from backend.data.weather_parsing import (
    extract_visibility_str,
    get_flight_category,
    is_speci_metar,
    parse_ceiling_feet,
    parse_ceiling_layer,
    parse_visibility_sm,
    parse_weather_phenomena,
)
from ui import config
from ui.config import CATEGORY_COLORS
from ui.debug_logger import debug

_REQUESTED_HEADER = "[bold cyan]━━━ REQUESTED ━━━[/bold cyan]"

# Category order and colour of the briefing's category summary
_SUMMARY_STYLES = (
    ("LIFR", "magenta"),
    ("IFR", "red"),
    ("MVFR", "#5599ff"),
    ("VFR", "#00ff00"),
)

# TAF detail columns after visibility, each left-aligned
_TAF_TEXT_COLUMNS = ("ceil", "wind", "wx")

_PRINT_CSS = """pre { margin: 0; padding: 0; white-space: pre-wrap; word-wrap: break-word; max-width: 100ch; }
body { margin: 20px; }
</style>"""


def _parse_metar_observation_time(metar: str) -> tuple | None:
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
    match = re.search(r"\b(\d{2})(\d{2})(\d{2})Z\b", metar)
    if not match:
        return None

    day = int(match.group(1))
    hour = int(match.group(2))
    minute = int(match.group(3))

    zulu_str = f"{hour:02d}{minute:02d}Z"

    # Convert to local time
    now = datetime.now(UTC)
    try:
        obs_time = now.replace(
            day=day, hour=hour, minute=minute, second=0, microsecond=0
        )
        # Handle month rollover (observation could be from previous month)
        if obs_time > now + timedelta(
            hours=2
        ):  # More than 2h in future = likely prev month
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


def _category_summary_parts(weather_data: dict[str, dict[str, Any]]) -> list[str]:
    """Coloured "<count> <category>" parts for each known category present.

    Args:
        weather_data: Weather entries by ICAO.

    Returns:
        The parts in LIFR, IFR, MVFR, VFR order, leaving out absent categories.
    """
    counts = Counter(data.get("category", "UNK") for data in weather_data.values())
    return [
        f"[{style}]{counts[category]} {category}[/{style}]"
        for category, style in _SUMMARY_STYLES
        if counts[category] > 0
    ]


def _metar_fields(metar: str) -> dict[str, Any]:
    """The weather entry fields read from a METAR.

    Args:
        metar: Raw METAR, or an empty string when the airport has none.

    Returns:
        Category, visibility, ceiling, wind, phenomena and SPECI flag; an empty
        METAR gives the unknown category and empty values.
    """
    if not metar:
        return {
            "category": "UNK",
            "visibility": None,
            "visibility_sm": None,
            "ceiling": None,
            "ceiling_ft": None,
            "wind": None,
            "phenomena": [],
            "is_speci": False,
        }
    return {
        "category": get_flight_category(metar),
        "visibility": extract_visibility_str(metar),
        "visibility_sm": parse_visibility_sm(metar),
        "ceiling": parse_ceiling_layer(metar),
        "ceiling_ft": parse_ceiling_feet(metar),
        "wind": parse_wind_from_metar(metar),
        "phenomena": parse_weather_phenomena(metar),
        "is_speci": is_speci_metar(metar),
    }


def _weather_entry(metar: str, taf: str, atis: Any) -> dict[str, Any]:
    """One airport's weather entry as the briefing stores it.

    Args:
        metar: Raw METAR, or an empty string.
        taf: Raw TAF, or an empty string.
        atis: The airport's ATIS list, or None.

    Returns:
        The entry, with the TAF's changes judged against the METAR's conditions.
    """
    fields = _metar_fields(metar)
    category = fields["category"]
    visibility_sm = fields["visibility_sm"]
    ceiling_ft = fields["ceiling_ft"]
    return {
        "metar": metar,
        "taf": taf,
        "category": category,
        "color": CATEGORY_COLORS.get(category, "white"),
        "visibility": fields["visibility"],
        "visibility_sm": visibility_sm,
        "ceiling": fields["ceiling"],
        "ceiling_ft": ceiling_ft,
        "wind": fields["wind"],
        "atis": atis,
        "phenomena": fields["phenomena"],
        "taf_changes": parse_taf_changes(taf, category, visibility_sm, ceiling_ft)
        if taf
        else [],
        "is_speci": fields["is_speci"],
    }


def _processing_message(index: int, total: int) -> str:
    """The progress bar shown while the weather entries are built."""
    pct = int((index / total) * 100)
    bar_filled = pct // 5
    bar_empty = 20 - bar_filled
    bar = f"[green]{'█' * bar_filled}[/green][dim]{'░' * bar_empty}[/dim]"
    return f"Processing weather: {bar} {pct}%"


def _card_header(icao: str, data: dict[str, Any], is_primary: bool) -> str:
    """The card's first line: ICAO, display name, category and SPECI mark."""
    category = data.get("category", "UNK")
    color = CATEGORY_COLORS.get(category, "white")
    pretty_name = (
        config.DISAMBIGUATOR.get_full_name(icao) if config.DISAMBIGUATOR else icao
    )
    speci_str = " [#ff9900 bold]SPECI[/#ff9900 bold]" if data.get("is_speci") else ""
    icao_str = f"[bold]{icao}[/bold]" if is_primary else icao
    return f"{icao_str} - {pretty_name} // [{color} bold]{category}[/{color} bold]{speci_str}"


def _conditions_line(data: dict[str, Any]) -> str:
    """The ceiling, visibility and wind line; no ceiling reads "CLR"."""
    ceiling = data.get("ceiling")
    conditions_parts = [f"Ceiling: {ceiling}" if ceiling else "Ceiling: CLR"]
    visibility = data.get("visibility")
    if visibility:
        conditions_parts.append(f"Vis: {visibility}")
    wind = data.get("wind")
    if wind:
        conditions_parts.append(f"Wind: {wind}")
    return "  " + " | ".join(conditions_parts)


def _strip_markup(s: str) -> str:
    """Remove Rich markup tags for length calculation"""
    return re.sub(r"\[/?[^\]]+\]", "", s)


def _taf_visibility(vis_sm: float | None) -> str:
    """A TAF visibility as P6SM, whole miles or miles to one decimal."""
    if vis_sm is None:
        return ""
    if vis_sm >= 6:
        return "P6SM"
    if vis_sm == int(vis_sm):
        return f"{int(vis_sm)}SM"
    return f"{vis_sm:.1f}SM"


def _taf_parts(change: dict[str, Any]) -> dict[str, str]:
    """A TAF change's detail columns, each empty when the change lacks it."""
    ceiling_layer = change.get("ceiling_layer")
    taf_wind = change.get("wind")
    taf_phenomena = change.get("phenomena", [])
    return {
        "vis": _taf_visibility(change.get("visibility_sm")),
        "ceil": ceiling_layer if ceiling_layer else "",
        "wind": taf_wind if taf_wind else "",
        "wx": ", ".join(taf_phenomena) if taf_phenomena else "",
    }


def _taf_entry(change: dict[str, Any]) -> dict[str, Any]:
    """The pieces of one TAF change's line, before column alignment."""
    time_str = change.get("time_str", "")
    pred_category = change.get("category", "UNK")
    if change.get("trend", "stable") == "worsening":
        indicator = "[red bold]▼[/red bold]"
    else:
        indicator = "[green bold]▲[/green bold]"
    raw_relative = format_taf_relative_time(time_str)
    return {
        "indicator": indicator,
        "change_type": change.get("type", ""),
        "time_str": time_str,
        "relative_time": f" [dim]{raw_relative}[/dim]" if raw_relative else "",
        "pred_category": pred_category,
        "pred_color": CATEGORY_COLORS.get(pred_category, "white"),
        "parts": _taf_parts(change),
    }


def _taf_prefix_plain(entry: dict[str, Any]) -> str:
    """The TAF line's prefix without markup, as it is measured for alignment."""
    return f"TAF {entry['change_type']} {entry['time_str']}{_strip_markup(entry['relative_time'])}"


def _taf_column_widths(taf_entries: list[dict[str, Any]]) -> dict[str, int]:
    """The widest prefix, category and detail column across the TAF lines."""
    widths = {
        "prefix": max((len(_taf_prefix_plain(e)) for e in taf_entries), default=0),
        "cat": max((len(e["pred_category"]) for e in taf_entries), default=0),
    }
    for column in ("vis", *_TAF_TEXT_COLUMNS):
        widths[column] = max((len(e["parts"][column]) for e in taf_entries), default=0)
    return widths


def _taf_details(parts: dict[str, str], widths: dict[str, int]) -> str:
    """The aligned detail columns; a column no line has is left out.

    Visibility is right-aligned so the numbers line up, the rest left-aligned.
    """
    padded_parts = [parts["vis"].rjust(widths["vis"])] if widths["vis"] > 0 else []
    padded_parts.extend(
        parts[column].ljust(widths[column])
        for column in _TAF_TEXT_COLUMNS
        if widths[column] > 0
    )
    return " | ".join(padded_parts).rstrip()


def _taf_line(entry: dict[str, Any], widths: dict[str, int]) -> str:
    """One aligned TAF change line of an airport card."""
    prefix_padding = " " * (widths["prefix"] - len(_taf_prefix_plain(entry)))
    prefix_with_markup = f"TAF {entry['change_type']} {entry['time_str']}{entry['relative_time']}{prefix_padding}"
    pred_color = entry["pred_color"]
    cat_padded = entry["pred_category"].rjust(widths["cat"])
    line = f"  {entry['indicator']} {prefix_with_markup}: [{pred_color} bold]{cat_padded}[/{pred_color} bold]"
    details_str = _taf_details(entry["parts"], widths)
    return f"{line} - {details_str}" if details_str else line


def _taf_lines(taf_changes: list[dict[str, Any]]) -> list[str]:
    """Lines for the first two improving or worsening TAF changes, aligned."""
    significant_changes = [
        c for c in taf_changes if c.get("is_improvement") or c.get("is_deterioration")
    ]
    taf_entries = [_taf_entry(change) for change in significant_changes[:2]]
    widths = _taf_column_widths(taf_entries)
    return [_taf_line(entry, widths) for entry in taf_entries]


def _atis_type_label(atis_type: str) -> str:
    """The DEP:/ARR: label of a departure or arrival ATIS."""
    if atis_type == "departure":
        return "[cyan]DEP:[/cyan] "
    if atis_type == "arrival":
        return "[cyan]ARR:[/cyan] "
    return ""


def _atis_lines(atis_list: list[dict[str, Any]]) -> list[str]:
    """Card lines for each ATIS with text left after the METAR part is filtered.

    A real-world D-ATIS is marked (RW), and the ATIS letter is coloured.
    """
    lines = []
    for atis in atis_list:
        atis_code = atis.get("atis_code", "")
        atis_type = atis.get("type", "combined")
        atis_source = atis.get("source", "vatsim")
        filtered_text = filter_atis_text(atis.get("text_atis", ""))
        if filtered_text:
            type_label = _atis_type_label(atis_type)
            source_label = "[#ffaa00](RW)[/#ffaa00] " if atis_source == "rw" else ""
            display_text = colorize_atis_text(filtered_text, atis_code)
            lines.append(f"  [dim]{source_label}{type_label}{display_text}[/dim]")
    return lines


def _area_lines(area: dict[str, Any], cards: list[str]) -> list[str]:
    """An area section's header, its category summary when it has one, and cards."""
    area_summary = build_area_summary(
        count_area_categories(area["airports"]), color_scheme="ui"
    )
    header = f"[bold cyan]━━━ {area['name'].upper()} ━━━[/bold cyan]"
    return [header, area_summary, *cards] if area_summary else [header, *cards]


def _printable_html(html_content: str) -> str:
    """The exported page with print CSS and without the body's indentation."""
    html_content = html_content.replace("</style>", _PRINT_CSS)
    return html_content.replace("<body>\n    <pre", "<body>\n<pre")


class _FetchProgress:
    """The fetch-phase progress line: weather fetched so far and ATIS done."""

    def __init__(self, report: Callable[[str], None], total_airports: int) -> None:
        self.report = report
        self.weather = (0, total_airports)
        self.vatsim_done = False

    def show(self) -> None:
        """Report the current state."""
        weather_done, weather_total = self.weather
        weather_str = (
            "[green]Weather ✓[/green]"
            if weather_done == weather_total
            else f"[dim]Weather {weather_done}/{weather_total}[/dim]"
        )
        vatsim_str = (
            "[green]ATIS ✓[/green]" if self.vatsim_done else "[dim]ATIS...[/dim]"
        )
        self.report(f"Fetching: {weather_str} | {vatsim_str}")


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

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close", priority=True),
        Binding("q", "close", "Close"),
        Binding("p", "print", "Print"),
    ]

    def __init__(
        self,
        grouping_name: str,
        airports: list[str],
        primary_airports: list[str] | None = None,
        is_flight_route: bool = False,
    ):
        """
        Initialize the weather briefing modal.

        Args:
            grouping_name: Name of the grouping/sector
            airports: List of airport ICAO codes in this grouping
            primary_airports: Optional list of primary airports to highlight at the top
            is_flight_route: If True, display airports in route order without area clustering
        """
        super().__init__()
        self.grouping_name = grouping_name
        self.airports = airports
        self.primary_airports = primary_airports or []
        self.is_flight_route = is_flight_route
        self.weather_data: dict[str, dict[str, Any]] = {}
        self._pending_tasks: list = []

    def compose(self) -> ComposeResult:
        with Container(id="briefing-container"):
            with Container(id="briefing-header"):
                yield Static(
                    f"Weather Briefing: {self.grouping_name}", id="briefing-title"
                )
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
        except Exception as e:
            debug(f"Could not update briefing progress, widget not ready: {e}")

    async def _fetch_all_weather_async(self) -> None:
        """Fetch all weather data in parallel"""
        try:
            await self._fetch_all_weather_async_inner()
        except Exception as e:
            # Show error in the content widget
            try:
                content_widget = self.query_one("#briefing-content", Static)
                content_widget.update(f"[red]Error fetching weather: {e}[/red]")
                summary_widget = self.query_one("#briefing-summary", Static)
                summary_widget.update("[red]Error[/red]")
            except Exception as widget_error:
                debug(f"Could not display weather fetch error: {widget_error}")
            raise  # Re-raise to log the error

    async def _fetch_all_weather_async_inner(self) -> None:
        """Inner implementation of weather fetching"""
        loop = asyncio.get_event_loop()
        progress = _FetchProgress(self._update_progress, len(self.airports))
        progress.show()
        airport_coords = self._airport_coords()

        (metars, tafs), vatsim_data = await asyncio.gather(
            self._fetch_weather(loop, airport_coords, progress),
            self._fetch_vatsim(loop, progress),
        )

        self._update_progress("Extracting ATIS information...")
        await asyncio.sleep(0)
        atis_data = await self._fetch_atis(loop, vatsim_data)

        await self._store_weather_data(metars, tafs, atis_data)

        self._update_progress("Building area groups...")
        await asyncio.sleep(0)

        self._update_display()

    def _airport_coords(self) -> dict[str, tuple[Any, Any]]:
        """(lat, lon) of each briefed airport the unified airport data places."""
        airport_coords = {}
        if config.UNIFIED_AIRPORT_DATA:
            for icao in self.airports:
                apt_data = config.UNIFIED_AIRPORT_DATA.get(icao, {})
                lat = apt_data.get("latitude")
                lon = apt_data.get("longitude")
                if lat is not None and lon is not None:
                    airport_coords[icao] = (lat, lon)
        return airport_coords

    async def _fetch_weather(
        self,
        loop: asyncio.AbstractEventLoop,
        airport_coords: dict[str, tuple[Any, Any]],
        progress: _FetchProgress,
    ) -> tuple[dict[str, str], dict[str, str]]:
        """METARs and TAFs by ICAO.

        The smart fetch is used when at least 80% of the airports have a
        position; otherwise the METAR and TAF batch queries.
        """

        def weather_progress(completed: int, total: int) -> None:
            progress.weather = (completed, total)
            loop.call_soon_threadsafe(progress.show)

        if len(airport_coords) >= len(self.airports) * 0.8:
            return await loop.run_in_executor(
                None,
                lambda: get_weather_smart(
                    self.airports,
                    airport_coords,
                    include_taf=True,
                    progress_callback=weather_progress,
                ),
            )
        metars = await loop.run_in_executor(
            None,
            lambda: get_metar_batch(self.airports, progress_callback=weather_progress),
        )
        tafs = await loop.run_in_executor(
            None,
            lambda: get_taf_batch(self.airports),
        )
        return (metars, tafs)

    async def _fetch_vatsim(
        self, loop: asyncio.AbstractEventLoop, progress: _FetchProgress
    ) -> Any:
        """The VATSIM data feed, marking ATIS done in the progress line."""
        result = await loop.run_in_executor(None, download_vatsim_data)
        progress.vatsim_done = True
        progress.show()
        return result

    async def _fetch_atis(
        self, loop: asyncio.AbstractEventLoop, vatsim_data: Any
    ) -> dict[str, Any]:
        """ATIS by ICAO: VATSIM's, else the real-world D-ATIS."""
        atis_data = {}
        if vatsim_data:
            atis_data = get_atis_for_airports(vatsim_data, self.airports)

        airports_without_vatsim_atis = [
            icao for icao in self.airports if icao not in atis_data
        ]
        if airports_without_vatsim_atis:
            rw_atis_data = await loop.run_in_executor(
                None, lambda: get_datis_for_airports(airports_without_vatsim_atis)
            )
            atis_data.update(rw_atis_data)
        return atis_data

    async def _store_weather_data(
        self,
        metars: dict[str, str],
        tafs: dict[str, str],
        atis_data: dict[str, Any],
    ) -> None:
        """Build each airport's weather entry, reporting progress ~20 times."""
        total_airports = len(self.airports)
        update_interval = max(1, total_airports // 20)
        for i, icao in enumerate(self.airports):
            if i % update_interval == 0:
                self._update_progress(_processing_message(i, total_airports))
                await asyncio.sleep(0)

            self.weather_data[icao] = _weather_entry(
                metars.get(icao, ""), tafs.get(icao, ""), atis_data.get(icao)
            )

    def _create_area_groups(self) -> list[dict[str, Any]]:
        """Create area-based groupings using shared AreaClusterer."""
        if not config.UNIFIED_AIRPORT_DATA:
            return []
        clusterer = AreaClusterer(
            weather_data=self.weather_data,
            unified_airport_data=config.UNIFIED_AIRPORT_DATA,
            disambiguator=config.DISAMBIGUATOR,
        )
        return clusterer.create_area_groups()

    def _non_primary_area_groups(self) -> list[dict[str, Any]]:
        """Area groups of the airports other than the requested ones."""
        original_weather_data = self.weather_data
        if self.primary_airports:
            self.weather_data = {
                k: v
                for k, v in self.weather_data.items()
                if k not in self.primary_airports
            }
        area_groups = self._create_area_groups()
        self.weather_data = original_weather_data
        return area_groups

    def _route_cards(self) -> list[str]:
        """Cards of the route's airports with weather, in route order."""
        return [
            self._build_airport_card(
                icao,
                self.weather_data[icao],
                is_primary=icao in self.primary_airports,
            )
            for icao in self.airports
            if self.weather_data.get(icao, {})
        ]

    def _primary_cards(self) -> list[str]:
        """Cards of the requested airports with weather."""
        return [
            self._build_airport_card(icao, self.weather_data[icao], is_primary=True)
            for icao in self.primary_airports
            if self.weather_data.get(icao, {})
        ]

    def _area_section_lines(self, area: dict[str, Any]) -> list[str]:
        """An area's header, summary and airport cards."""
        cards = [
            self._build_airport_card(icao, data)
            for icao, data, _size in area["airports"]
        ]
        return _area_lines(area, cards)

    def _grouping_sections(self) -> list[str]:
        """The requested airports' section, then one section per area."""
        sections = []
        primary_cards = self._primary_cards()
        if primary_cards:
            sections.append("\n".join([_REQUESTED_HEADER, *primary_cards]))
        sections.extend(
            "\n".join(self._area_section_lines(area))
            for area in self._non_primary_area_groups()
            if area["airports"]
        )
        return sections

    def _update_display(self) -> None:
        """Update the display with weather data"""
        summary_parts = _category_summary_parts(self.weather_data)

        now = datetime.now(UTC)
        zulu_str = now.strftime("%H%MZ")
        local_str = now.astimezone().strftime("%H:%M LT")
        timestamp = f"[dim]{zulu_str} ({local_str})[/dim]"

        summary_text = " | ".join(summary_parts) if summary_parts else "No weather data"
        summary_widget = self.query_one("#briefing-summary", Static)
        summary_widget.update(f"{timestamp} | {summary_text}")

        if self.is_flight_route:
            sections = self._route_cards()
        else:
            sections = self._grouping_sections()

        content_widget = self.query_one("#briefing-content", Static)
        content_widget.update(
            "\n\n".join(sections)
            if sections
            else "[dim]No weather data available[/dim]"
        )

    def _build_airport_card(
        self, icao: str, data: dict[str, Any], is_primary: bool = False
    ) -> str:
        """Build a Rich markup card for one airport"""
        lines = [_card_header(icao, data, is_primary), _conditions_line(data)]

        phenomena = data.get("phenomena", [])
        if phenomena:
            lines.append(f"  Weather: {', '.join(phenomena)}")

        lines.extend(_taf_lines(data.get("taf_changes", [])))
        lines.extend(_atis_lines(data.get("atis") or []))
        return "\n".join(lines)

    def action_close(self) -> None:
        """Close the modal"""
        self.dismiss()

    def _print_route(self, console: Console) -> None:
        """Print the route's cards in route order, a blank line after each."""
        for card in self._route_cards():
            console.print(card)
            console.print()

    def _print_grouping(self, console: Console) -> None:
        """Print the requested airports, then each area with its summary."""
        if self.primary_airports:
            console.print(_REQUESTED_HEADER)
            for card in self._primary_cards():
                console.print(card)
            console.print()

        for area in self._non_primary_area_groups():
            if not area["airports"]:
                continue
            for line in self._area_section_lines(area):
                console.print(line)
            console.print()

    def _open_in_browser(self, html_content: str) -> None:
        """Write the page to a temp file, open it and say which file it is."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".html",
            prefix=f"weather_briefing_{self.grouping_name}_",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(html_content)
            temp_path = f.name

        webbrowser.open(f"file://{temp_path}")
        self.notify(f"Opened in browser: {os.path.basename(temp_path)}")

    def action_print(self) -> None:
        """Export weather briefing as HTML and open in browser"""
        if not self.weather_data:
            self.notify("No weather data to export", severity="warning")
            return

        # Wide enough that Rich never wraps; the page's CSS wraps instead
        console = Console(record=True, force_terminal=True, width=500, file=StringIO())

        # Zulu only: a reader's local time varies
        now = datetime.now(UTC)
        zulu_str = now.strftime("%Y-%m-%d %H%MZ")
        console.print(f"[bold]Weather Briefing: {self.grouping_name}[/bold]")
        console.print(f"Generated: {zulu_str}\n")

        summary_parts = _category_summary_parts(self.weather_data)
        if summary_parts:
            console.print(" | ".join(summary_parts))
        console.print()

        if self.is_flight_route:
            self._print_route(console)
        else:
            self._print_grouping(console)

        self._open_in_browser(_printable_html(console.export_html(inline_styles=True)))
