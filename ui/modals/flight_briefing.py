"""Flight Weather Briefing Modal - Pilot-style weather briefing for a flight route."""

import asyncio
import os
import tempfile
import webbrowser
from datetime import UTC, datetime
from io import StringIO
from typing import Any, ClassVar

from rich.console import Console
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from backend import get_metar_batch, get_taf_batch
from backend.briefing import (
    parse_taf_changes,
    parse_wind_from_metar,
)
from backend.core.calculations import haversine_distance_nm
from backend.core.route import (
    find_enroute_airports,
    format_ete,
    parse_route_waypoints,
    sample_route_points,
)
from backend.data.weather_parsing import (
    get_flight_category,
    parse_ceiling_feet,
    parse_ceiling_layer,
    parse_visibility_sm,
)
from ui import config
from ui.config import CATEGORY_COLORS
from ui.debug_logger import debug

_INSTRUMENT_CATEGORIES = ("LIFR", "IFR")


def _airport_info(icao: str) -> dict[str, Any]:
    """The unified airport record for an ICAO, or {} when it or the data is missing."""
    if not config.UNIFIED_AIRPORT_DATA:
        return {}
    return config.UNIFIED_AIRPORT_DATA.get(icao, {})


def _unique_in_order(icaos: list[str]) -> list[str]:
    """The ICAOs with repeats dropped, first occurrence kept."""
    return list(dict.fromkeys(icaos))


def _weather_entry(metar: str, taf: str) -> dict[str, Any]:
    """The weather the briefing keeps for one airport, parsed from its METAR."""
    category = get_flight_category(metar) if metar else "UNK"
    return {
        "metar": metar,
        "taf": taf,
        "category": category,
        "color": CATEGORY_COLORS.get(category, "white"),
        "visibility_sm": parse_visibility_sm(metar) if metar else None,
        "ceiling_ft": parse_ceiling_feet(metar) if metar else None,
        "ceiling_layer": parse_ceiling_layer(metar) if metar else None,
        "wind": parse_wind_from_metar(metar) if metar else None,
    }


def _worst_enroute_category(categories: list[str]) -> str:
    """The first instrument category enroute, else MVFR if any, else VFR."""
    worst = "VFR"
    for category in categories:
        if (category in _INSTRUMENT_CATEGORIES and worst in ("VFR", "MVFR")) or (
            category == "MVFR" and worst == "VFR"
        ):
            worst = category
    return worst


def _departure_phrase(category: str) -> str:
    if category in _INSTRUMENT_CATEGORIES:
        return f"{category} conditions at departure"
    if category == "MVFR":
        return "MVFR conditions at departure"
    return "VFR departure"


def _enroute_phrase(category: str) -> str | None:
    """The synopsis phrase for enroute weather, or None when it is VFR."""
    if category in _INSTRUMENT_CATEGORIES:
        return f"{category} enroute"
    if category == "MVFR":
        return "MVFR enroute"
    return None


def _arrival_phrase(category: str) -> str:
    if category in _INSTRUMENT_CATEGORIES:
        return f"{category} conditions at arrival"
    if category == "MVFR":
        return "MVFR arrival"
    return "VFR arrival"


def _taf_trend_phrase(taf: str, category: str) -> str | None:
    """The trend of the first improving or worsening change among the TAF's first two."""
    for change in parse_taf_changes(taf, category)[:2]:
        if change.get("is_improvement"):
            return f"improving to {change.get('category', 'VFR')}"
        if change.get("is_deterioration"):
            return f"deteriorating to {change.get('category', 'IFR')}"
    return None


def _conditions_text(wind: Any, visibility: Any, ceiling: Any) -> str:
    """Wind, visibility and ceiling joined by spaces, each only when present."""
    conditions = []
    if wind:
        conditions.append(wind)
    if visibility is not None:
        conditions.append(f"{visibility}SM")
    if ceiling:
        conditions.append(ceiling)
    return " ".join(conditions)


def _metar_display(metar: str, icao: str) -> str:
    """The METAR without its leading "METAR " and station ICAO."""
    if metar.startswith("METAR "):
        metar = metar[6:]
    if metar.startswith(icao):
        metar = metar[len(icao) :].strip()
    return metar


def _taf_trend(taf: str, category: str) -> str | None:
    """The TAF's first change as "↑ VFR (FM)", or None unless it improves or worsens."""
    for change in parse_taf_changes(taf, category)[:1]:
        if change.get("is_improvement") or change.get("is_deterioration"):
            arrow = "↑" if change.get("is_improvement") else "↓"
            return f"{arrow} {change.get('category', '')} ({change.get('type', 'FM')})"
    return None


def _section_header(title: str) -> str:
    return f"[bold cyan]━━━ {title} ━━━[/bold cyan]"


def _waypoint_line(waypoint: str) -> str:
    return f"    [dim]  · {waypoint}[/dim]"


class FlightWeatherBriefingScreen(ModalScreen):
    """Modal screen showing pilot-style weather briefing for a flight route."""

    CSS = """
    FlightWeatherBriefingScreen {
        align: center middle;
    }

    #flight-briefing-container {
        width: 95%;
        max-width: 130;
        height: 90%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #flight-briefing-header {
        height: auto;
        dock: top;
        padding-bottom: 1;
        border-bottom: solid $primary;
    }

    #flight-briefing-title {
        text-style: bold;
        text-align: center;
    }

    #flight-briefing-route {
        text-align: center;
        color: $text-muted;
    }

    #flight-briefing-scroll {
        height: 1fr;
    }

    #flight-briefing-content {
        padding: 1 0;
    }

    #flight-briefing-hint {
        dock: bottom;
        height: 1;
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
        callsign: str,
        departure: str,
        arrival: str,
        alternate: str | None = None,
        route: str | None = None,
        cruise_altitude: int | None = None,
        groundspeed: int | None = None,
    ):
        """
        Initialize the flight weather briefing.

        Args:
            callsign: Flight callsign
            departure: Departure airport ICAO
            arrival: Arrival airport ICAO
            alternate: Alternate airport ICAO (optional)
            route: Route string from flight plan (optional)
            cruise_altitude: Cruise altitude in feet (optional)
            groundspeed: Current groundspeed in knots (optional, for ETE calc)
        """
        super().__init__()
        self.callsign = callsign
        self.departure = departure
        self.arrival = arrival
        self.alternate = alternate
        self.route = route
        self.cruise_altitude = cruise_altitude
        self.groundspeed = groundspeed or 450  # Default cruise speed

        self.weather_data: dict[str, dict[str, Any]] = {}
        self.enroute_points: list[dict[str, Any]] = []
        self.route_waypoints: list[str] = []
        self.total_distance: float = 0.0
        self._pending_tasks: list = []

    def compose(self) -> ComposeResult:
        route_display = f"{self.departure} → {self.arrival}"
        if self.alternate:
            route_display += f" (Alt: {self.alternate})"

        with Container(id="flight-briefing-container"):
            with Container(id="flight-briefing-header"):
                yield Static(
                    f"Flight Weather Briefing: {self.callsign}",
                    id="flight-briefing-title",
                )
                yield Static(route_display, id="flight-briefing-route")
            with VerticalScroll(id="flight-briefing-scroll"):
                yield Static(
                    "Loading weather data...", id="flight-briefing-content", markup=True
                )
            yield Static("Escape/Q: Close | P: Print", id="flight-briefing-hint")

    async def on_mount(self) -> None:
        """Load weather data asynchronously."""
        task = asyncio.create_task(self._fetch_weather_async())
        self._pending_tasks.append(task)

    def on_unmount(self) -> None:
        """Cancel pending tasks when modal is dismissed."""
        for task in self._pending_tasks:
            if not task.done():
                task.cancel()
        self._pending_tasks.clear()

    async def _fetch_weather_async(self) -> None:
        """Fetch all weather data for the route."""
        loop = asyncio.get_event_loop()

        # Parse route waypoints for display
        self.route_waypoints = parse_route_waypoints(self.route) if self.route else []
        self._locate_route()

        # Collect all airports needing weather
        airports_to_fetch = [self.departure, self.arrival]
        if self.alternate:
            airports_to_fetch.append(self.alternate)
        airports_to_fetch.extend([p["icao"] for p in self.enroute_points])
        unique_airports = _unique_in_order(airports_to_fetch)

        # Fetch weather data in parallel
        try:
            metar_task = loop.run_in_executor(None, get_metar_batch, unique_airports)
            taf_task = loop.run_in_executor(None, get_taf_batch, unique_airports)

            metars, tafs = await asyncio.gather(metar_task, taf_task)
        except Exception as e:
            self._update_content(f"[red]Error fetching weather: {e}[/red]")
            return

        for icao in unique_airports:
            self.weather_data[icao] = _weather_entry(
                metars.get(icao, ""), tafs.get(icao, "")
            )

        self._update_display()

    def _locate_route(self) -> None:
        """Set the route distance and enroute airports when both ends have a position."""
        dep_info = _airport_info(self.departure)
        arr_info = _airport_info(self.arrival)

        dep_lat = dep_info.get("latitude")
        dep_lon = dep_info.get("longitude")
        arr_lat = arr_info.get("latitude")
        arr_lon = arr_info.get("longitude")

        if not (dep_lat and dep_lon and arr_lat and arr_lon):
            return

        self.total_distance = haversine_distance_nm(dep_lat, dep_lon, arr_lat, arr_lon)
        sample_points = sample_route_points(
            dep_lat,
            dep_lon,
            arr_lat,
            arr_lon,
            interval_nm=200.0,  # Sample every ~200nm
            min_points=1,
            max_points=6,
        )
        if config.UNIFIED_AIRPORT_DATA:
            self.enroute_points = find_enroute_airports(
                sample_points, config.UNIFIED_AIRPORT_DATA, search_radius_nm=100.0
            )

    def _update_content(self, content: str) -> None:
        """Update the content widget."""
        try:
            widget = self.query_one("#flight-briefing-content", Static)
            widget.update(content)
        except Exception as e:
            debug(f"Could not update flight briefing content: {e}")

    def _generate_synopsis(self) -> str:
        """Generate a synopsis of weather conditions along the route."""
        dep_cat = self.weather_data.get(self.departure, {}).get("category", "UNK")
        arr_data = self.weather_data.get(self.arrival, {})
        arr_cat = arr_data.get("category", "UNK")
        worst_enroute = _worst_enroute_category(
            [
                self.weather_data.get(p["icao"], {}).get("category", "UNK")
                for p in self.enroute_points
            ]
        )

        parts = [_departure_phrase(dep_cat)]
        enroute = _enroute_phrase(worst_enroute)
        if enroute:
            parts.append(enroute)
        parts.append(_arrival_phrase(arr_cat))

        arr_taf = arr_data.get("taf", "")
        trend = _taf_trend_phrase(arr_taf, arr_cat) if arr_taf else None
        if trend:
            parts.append(trend)

        return ". ".join(parts) + "."

    def _format_airport_line(
        self,
        icao: str,
        data: dict[str, Any],
        distance_nm: float | None = None,
        label: str = "",
        show_ete: bool = True,
    ) -> str:
        """Format a single airport line for the briefing."""
        category = data.get("category", "UNK")
        color = data.get("color", "white")

        # Distance and ETE
        dist_str = f"{int(distance_nm):>4}nm" if distance_nm is not None else "   0nm"
        ete_str = (
            format_ete(distance_nm or 0, self.groundspeed)
            if show_ete and distance_nm
            else ""
        )

        # Basic line: ICAO (distance, ETE) ............ CATEGORY
        dots = "." * max(3, 45 - len(icao) - len(label) - len(dist_str) - len(ete_str))
        line = f"{label}{icao} ({dist_str} {ete_str:>6}) {dots} [{color}]{category}[/{color}]"

        return line

    def _format_weather_block(
        self, icao: str, data: dict[str, Any], indent: str = "    "
    ) -> list[str]:
        """Format the weather details for an airport."""
        lines = []

        conditions = _conditions_text(
            data.get("wind", ""),
            data.get("visibility_sm"),
            data.get("ceiling_layer", ""),
        )
        if conditions:
            lines.append(f"{indent}[dim]{conditions}[/dim]")

        metar = data.get("metar", "")
        if metar:
            lines.append(f"{indent}[dim]{_metar_display(metar, icao)[:80]}[/dim]")

        taf = data.get("taf", "")
        trend = _taf_trend(taf, data.get("category", "UNK")) if taf else None
        if trend:
            lines.append(f"{indent}[dim]TAF: {trend}[/dim]")

        return lines

    def _airport_lines(
        self,
        icao: str,
        distance_nm: float | None,
        *,
        label: str,
        show_ete: bool,
    ) -> list[str]:
        """An airport's line and weather block, or nothing when it has no weather."""
        data = self.weather_data.get(icao, {})
        if not data:
            return []
        return [
            self._format_airport_line(
                icao, data, distance_nm, label, show_ete=show_ete
            ),
            *self._format_weather_block(icao, data),
        ]

    def _departure_lines(self) -> list[str]:
        return self._airport_lines(self.departure, 0, label="DEP ", show_ete=False)

    def _arrival_lines(self) -> list[str]:
        return self._airport_lines(
            self.arrival, self.total_distance, label="ARR ", show_ete=True
        )

    def _alternate_lines(self, alternate: str) -> list[str]:
        return self._airport_lines(alternate, None, label="ALT ", show_ete=False)

    def _enroute_point_lines(self, point: dict[str, Any]) -> list[str]:
        """An enroute airport's line and its conditions, without the METAR."""
        icao = point["icao"]
        data = self.weather_data.get(icao, {})
        if not data:
            return []
        lines = [self._format_airport_line(icao, data, point["distance_nm"], "ENR ")]
        conditions = _conditions_text(
            data.get("wind", ""),
            data.get("visibility_sm"),
            data.get("ceiling_layer", ""),
        )
        if conditions:
            lines.append(f"    [dim]{conditions}[/dim]")
        return lines

    def _enroute_lines(self) -> list[str]:
        """Enroute weather points with the route's waypoints spread evenly between them."""
        waypoints = self.route_waypoints
        if not self.enroute_points:
            return [_waypoint_line(waypoint) for waypoint in waypoints]

        waypoints_per_segment = len(waypoints) // (len(self.enroute_points) + 1)
        lines = []
        wp_idx = 0
        for point in self.enroute_points:
            shown = waypoints[wp_idx : wp_idx + waypoints_per_segment]
            lines.extend(_waypoint_line(waypoint) for waypoint in shown)
            wp_idx += len(shown)
            lines.extend(self._enroute_point_lines(point))
        lines.extend(_waypoint_line(waypoint) for waypoint in waypoints[wp_idx:])
        return lines

    def _update_display(self) -> None:
        """Update the display with the weather briefing."""
        zulu_str = datetime.now(UTC).strftime("%d%H%MZ")
        total_ete = format_ete(self.total_distance, self.groundspeed)
        sections = [
            f"[bold]Briefing valid: {zulu_str}[/bold]  |  Route: {int(self.total_distance)}nm  |  ETE: {total_ete}",
            f"\n[bold cyan]SYNOPSIS[/bold cyan]\n{self._generate_synopsis()}",
            f"\n{_section_header('DEPARTURE')}",
            *self._departure_lines(),
        ]

        if self.enroute_points or self.route_waypoints:
            sections.append(f"\n{_section_header('ENROUTE')}")
            sections.extend(self._enroute_lines())

        sections.append(f"\n{_section_header('ARRIVAL')}")
        sections.extend(self._arrival_lines())

        if self.alternate:
            sections.append(f"\n{_section_header('ALTERNATE')}")
            sections.extend(self._alternate_lines(self.alternate))

        self._update_content("\n".join(sections))

    def action_close(self) -> None:
        """Close the modal."""
        self.dismiss()

    def _print_lines(self) -> list[str]:
        """The briefing as the printed page shows it, one Rich markup line each."""
        zulu_str = datetime.now(UTC).strftime("%Y-%m-%d %H%MZ")
        total_ete = format_ete(self.total_distance, self.groundspeed)

        lines = [
            f"[bold]Flight Weather Briefing: {self.callsign}[/bold]",
            f"{self.departure} → {self.arrival}",
        ]
        if self.alternate:
            lines.append(f"Alternate: {self.alternate}")
        lines += [
            f"Generated: {zulu_str}  |  Route: {int(self.total_distance)}nm  |  ETE: {total_ete}",
            "",
            "[bold cyan]SYNOPSIS[/bold cyan]",
            self._generate_synopsis(),
            "",
            _section_header("DEPARTURE"),
            *self._departure_lines(),
            "",
        ]

        if self.enroute_points or self.route_waypoints:
            lines += [
                _section_header("ENROUTE"),
                *self._enroute_lines(),
                "",
            ]

        lines += [_section_header("ARRIVAL"), *self._arrival_lines(), ""]

        if self.alternate:
            lines += [
                _section_header("ALTERNATE"),
                *self._alternate_lines(self.alternate),
            ]

        return lines

    def action_print(self) -> None:
        """Export briefing as HTML and open in browser."""
        if not self.weather_data:
            self.notify("No weather data to export", severity="warning")
            return

        console = Console(record=True, force_terminal=True, width=500, file=StringIO())
        for line in self._print_lines():
            console.print(line)

        # Export
        html_content = console.export_html(inline_styles=True)
        html_content = html_content.replace(
            "</style>",
            """pre { margin: 0; padding: 0; white-space: pre-wrap; word-wrap: break-word; }
body { margin: 20px; font-family: monospace; }
</style>""",
        )

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".html",
            prefix=f"flight_briefing_{self.callsign}_",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(html_content)
            temp_path = f.name

        webbrowser.open(f"file://{temp_path}")
        self.notify(f"Opened in browser: {os.path.basename(temp_path)}")
