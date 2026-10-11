"""Flight Information Modal Screen"""

import asyncio
import re
from datetime import UTC, datetime
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from backend import (
    bearing_to_compass,
    calculate_bearing,
    calculate_eta,
    find_airports_near_position,
    find_nearest_airport_with_metar,
    get_metar,
    haversine_distance_nm,
)
from backend.core.flights import get_nearest_airport_if_on_ground
from backend.data.navaids import MeaCheck, MeaViolation, get_max_mea_for_route
from backend.data.vatsim_api import download_vatsim_data, get_member_stats
from ui import config
from ui.debug_logger import debug
from ui.modals.metar_info import _extract_flight_rules_weather, get_flight_category

# Cache for VFR alternate results: {origin_icao: {'result': [...], 'timestamp': datetime}}
_VFR_ALTERNATES_CACHE: dict[str, dict[str, Any]] = {}
_VFR_ALTERNATES_CACHE_DURATION = 60  # 60 seconds (matches METAR cache)


def _parse_filed_altitude(filed_altitude: object) -> int:
    """The filed altitude in feet, or 0 when it is not a plain number of feet."""
    try:
        return int(str(filed_altitude))
    except ValueError:
        return 0


def _checked_level(violation: MeaViolation, filed_alt: int) -> int:
    """The level a segment is flown at: the route's level at its airway, else the filed one."""
    return violation.level_ft if violation.level_ft is not None else filed_alt


def _mea_warning_lines(violations: list[MeaViolation], filed_alt: int) -> list[str]:
    """The MEA warning for segments whose MEA is above their level, or no lines."""
    exceeding = [v for v in violations if 0 < _checked_level(v, filed_alt) < v.mea]
    if not exceeding:
        return []
    exceeding.sort(key=lambda v: v.mea, reverse=True)
    worst = exceeding[0]
    if worst.level_ft is None:
        level_text = f"Filed altitude {filed_alt:,}"
    else:
        level_text = f"Filed level {worst.level_ft:,} on {worst.airway}"
    lines = [
        "[bold yellow]MEA WARNING[/bold yellow]",
        f"  {level_text} < required MEA {worst.mea:,}",
    ]
    # Show segments that exceed their level (limit to 3, highest first)
    lines.extend(
        f"  [dim]{v.airway} ({v.segment_start}→{v.segment_end}) requires {v.mea:,}[/dim]"
        for v in exceeding[:3]
    )
    return lines


def format_mea_lines(check: MeaCheck, filed_altitude: object) -> list[str]:
    """The flight info lines for a route's MEA check, ending in a blank line if any.

    Each segment is compared with the level in force at its airway's entry, or
    with the filed altitude when the route sets none there. The MEA warning
    comes first, then one dim line per airway the check skipped because one
    end of the flown stretch is not on it.

    Args:
        check: The route's MEA check
        filed_altitude: The flight plan's altitude field (feet, e.g. "30000")

    Returns:
        The lines to show, or an empty list when there is nothing to show
    """
    lines = _mea_warning_lines(check.violations, _parse_filed_altitude(filed_altitude))
    lines.extend(
        f"[dim]MEA not checked: {s.airway} ({s.missing_fix} not on it)[/dim]"
        for s in check.skipped
    )
    if lines:
        lines.append("")
    return lines


def _needs_alternates(weather: tuple | None) -> bool:
    """Whether an airport's weather (category first) is IFR or LIFR, so VFR alternates are worth finding."""
    return bool(weather) and weather[0] in ("IFR", "LIFR")


def _airport_position(
    airports: dict[str, dict[str, Any]], icao: str
) -> tuple[float, float] | None:
    """An airport's (latitude, longitude), or None when it is unknown or has no position."""
    airport = airports.get(icao) or {}
    latitude = airport.get("latitude")
    longitude = airport.get("longitude")
    if latitude is None or longitude is None:
        return None
    return latitude, longitude


def _fresh_cached_alternates(origin_icao: str, now: datetime) -> list | None:
    """The cached alternates of an origin while the entry is younger than the cache duration, else None."""
    cache_entry = _VFR_ALTERNATES_CACHE.get(origin_icao)
    if cache_entry is None:
        return None
    time_since_cache = (now - cache_entry["timestamp"]).total_seconds()
    if time_since_cache >= _VFR_ALTERNATES_CACHE_DURATION:
        return None
    return cache_entry["result"]


def _vfr_alternate(
    icao: str, metar: str | None, origin: tuple[float, float]
) -> tuple | None:
    """The (icao, category, color, distance_nm, direction) of a VFR or MVFR airport with a position, else None."""
    if not metar:
        return None
    category = get_flight_category(metar)
    if category not in ("VFR", "MVFR"):
        return None
    color = config.CATEGORY_COLORS.get(category, "white")
    position = _airport_position(config.UNIFIED_AIRPORT_DATA, icao)
    if position is None:
        return None
    distance = haversine_distance_nm(*origin, *position)
    bearing = calculate_bearing(*origin, *position)
    return (icao, category, color, distance, bearing_to_compass(bearing))


def _aircraft_with_equipment(flight_plan: dict) -> str:
    """The aircraft type with the FAA equipment suffix when the plan has one (e.g. "B738/L")."""
    aircraft_short = flight_plan.get(
        "aircraft_short", flight_plan.get("aircraft", "----")
    )
    aircraft_faa = flight_plan.get("aircraft_faa", "")
    # Extract equipment suffix from FAA format (e.g., "H/B738/L" -> "L")
    if aircraft_faa and "/" in aircraft_faa:
        equipment_suffix = aircraft_faa.rsplit("/", 1)[-1]
        if equipment_suffix:
            return f"{aircraft_short}/{equipment_suffix}"
    return aircraft_short


def _rules_label(flight_rules: str) -> str:
    """ "IFR" or "VFR" for the plan's I or V flight rules, else "?"."""
    if flight_rules.upper() == "I":
        return "IFR"
    if flight_rules.upper() == "V":
        return "VFR"
    return "?"


def _altitude_and_rules(flight_plan: dict) -> str:
    """The filed altitude and flight rules part of the summary line (e.g. "35,000 // IFR" or "VFR/105")."""
    altitude = flight_plan.get("altitude", "----")
    # "VFR" in altitude field means VFR flight, regardless of flight_rules
    # Could be "VFR" or "VFR/105" (VFR at 10,500 feet), displayed as filed
    if str(altitude).upper().startswith("VFR"):
        return altitude
    text = ""
    if altitude != "----":
        try:
            text = f"{int(altitude):,} // "
        except ValueError:
            text = f"{altitude} // "
    return text + _rules_label(flight_plan.get("flight_rules", "?"))


def _alternates_line(alternates: list | None, searched: bool) -> str:
    """The line under an IFR/LIFR airport: its alternates, none found, or still searching."""
    if alternates:
        alt_strs = [
            f"[{alt_color}]{alt_icao}[/{alt_color}] ({dist:.0f}nm {direction})"
            for alt_icao, _, alt_color, dist, direction in alternates
        ]
        return f"    Consider: {', '.join(alt_strs)}"
    if searched:
        return "    [dim]No VFR/MVFR alternates within 100nm[/dim]"
    return "    [dim]Searching for alternates...[/dim]"


class FlightInfoScreen(ModalScreen):
    """Modal screen showing detailed flight information"""

    # Refresh flight data every 15 seconds to match VATSIM cache duration
    FLIGHT_DATA_REFRESH_INTERVAL = 15

    CSS = """
    FlightInfoScreen {
        align: center middle;
    }

    #flight-info-container {
        width: 90;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #flight-info-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
        color: $accent;
    }

    #flight-info-scroll {
        height: auto;
        max-height: 100%;
    }

    .info-section {
        margin-bottom: 1;
    }

    .info-label {
        text-style: bold;
        color: $text;
    }

    .info-value {
        color: $text-muted;
        margin-left: 2;
    }

    #flight-info-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "Close", priority=True),
        Binding("q", "close", "Close"),
        Binding("c", "copy_route", "Copy Route", priority=True),
        Binding("d", "show_diversions", "Find Diversions", priority=True),
        Binding("w", "show_route_weather", "Route Weather", priority=True),
    ]

    def __init__(self, flight_data: dict):
        """
        Initialize the flight info modal.

        Args:
            flight_data: Dictionary containing flight information from VATSIM data
        """
        super().__init__()
        self.flight_data = flight_data
        self.callsign = flight_data.get(
            "callsign", ""
        )  # Store callsign for refresh lookups
        self.cid = flight_data.get("cid")  # VATSIM Client ID for member stats
        self.altimeter_info = None  # Will be populated asynchronously
        self.altimeter_loading = True
        self.member_stats = None  # Will be populated asynchronously (pilot/ATC hours)
        # Weather info: (category, color, visibility_sm, ceiling_ft)
        self.departure_weather = None
        self.arrival_weather = None
        # Alternate airports: list of (icao, category, color, distance_nm, direction)
        # None = not yet searched, [] = searched but none found
        self.departure_alternates = None
        self.arrival_alternates = None
        self.alternates_searched = False  # Track if we've completed the search
        # MEA check of the route, or None if not yet loaded
        self.mea_info: MeaCheck | None = None
        self.mea_loading = False  # Only load for IFR flights with airways
        self._pending_tasks: list = []  # Track pending async tasks
        self._refresh_timer = None  # Timer for periodic refresh
        self._refresh_in_progress = False  # Track if a background refresh is running

    def compose(self) -> ComposeResult:
        with Container(id="flight-info-container"):
            yield Static(self._format_title(), id="flight-info-title")
            with VerticalScroll(id="flight-info-scroll"):
                yield Static(
                    self._format_flight_info(),
                    classes="info-section",
                    id="flight-info-content",
                )
            yield Static(
                "C: Copy Route | D: Diversions | W: Route Wx | Escape/Q: Close",
                id="flight-info-hint",
            )

    async def on_mount(self) -> None:
        """Load altimeter info and member stats asynchronously after modal is shown"""
        # Start loading altimeter info in the background (tracked for cleanup)
        task = asyncio.create_task(self._load_altimeter_info())
        self._pending_tasks.append(task)

        # Start loading member stats in parallel
        if self.cid:
            stats_task = asyncio.create_task(self._load_member_stats())
            self._pending_tasks.append(stats_task)

        # Start loading MEA info for IFR flights with routes
        if self._should_check_mea():
            self.mea_loading = True
            mea_task = asyncio.create_task(self._load_mea_info())
            self._pending_tasks.append(mea_task)

        # Start periodic refresh timer to keep flight data current
        self._refresh_timer = self.set_interval(
            self.FLIGHT_DATA_REFRESH_INTERVAL, self._refresh_flight_data
        )

    def on_unmount(self) -> None:
        """Cancel pending tasks and timers when modal is dismissed."""
        # Stop the refresh timer
        if self._refresh_timer:
            self._refresh_timer.stop()
            self._refresh_timer = None

        # Cancel pending async tasks
        for task in self._pending_tasks:
            if not task.done():
                task.cancel()
        self._pending_tasks.clear()

    async def _refresh_flight_data(self) -> None:
        """Periodically refresh all flight data for long-open modals.

        Fetches fresh VATSIM data to update the aircraft's position,
        altitude, groundspeed, and then refreshes the altimeter based
        on the new position.
        """
        # Don't refresh if initial load is in progress or already refreshing
        if self.altimeter_loading or self._refresh_in_progress:
            return

        # Mark refresh in progress but don't update display yet -
        # keep showing existing data while fetching new data
        self._refresh_in_progress = True

        # Fetch fresh flight data and altimeter in background
        task = asyncio.create_task(self._load_fresh_flight_data())
        self._pending_tasks.append(task)

    async def _load_fresh_flight_data(self) -> None:
        """Fetch fresh VATSIM data and update flight info."""
        loop = asyncio.get_event_loop()

        try:
            # Fetch fresh VATSIM data and look up this flight
            fresh_data = await loop.run_in_executor(None, self._fetch_fresh_pilot_data)

            if fresh_data:
                self.flight_data = fresh_data

            # Now fetch altimeter for the (potentially new) position
            self.altimeter_info = await loop.run_in_executor(
                None, self._get_altimeter_info_sync
            )

            # Refresh airport weather for VFR flights
            if self._is_vfr_flight():
                await self._refresh_vfr_weather()
        except asyncio.CancelledError:
            return
        except Exception as e:
            debug(f"Flight info refresh failed, keeping existing data: {e}")
        finally:
            self._refresh_in_progress = False
            self._update_display()

    async def _refresh_vfr_weather(self) -> None:
        """Re-fetch a VFR flight's airport weather and redo the alternate search for each IFR/LIFR end."""
        flight_plan = self.flight_data["flight_plan"]
        departure = flight_plan.get("departure")
        arrival = flight_plan.get("arrival")
        await self._load_airport_weather(departure, arrival)
        await self._refresh_alternates(
            departure, self.departure_weather, "departure_alternates"
        )
        await self._refresh_alternates(
            arrival, self.arrival_weather, "arrival_alternates"
        )
        self.alternates_searched = True

    async def _refresh_alternates(
        self, icao: str | None, weather: tuple | None, target_attr: str
    ) -> None:
        """Search alternates afresh for an IFR/LIFR airport (progressively), else clear them."""
        if _needs_alternates(weather):
            setattr(self, target_attr, [])  # Clear before searching
            await self._find_vfr_alternates_async(icao, target_attr)
        else:
            setattr(self, target_attr, None)

    async def _load_airport_weather(
        self, departure: str | None, arrival: str | None
    ) -> None:
        """Fetch the weather of the departure and arrival airports the plan names."""
        loop = asyncio.get_event_loop()
        if departure:
            self.departure_weather = await loop.run_in_executor(
                None, self._get_airport_weather_sync, departure
            )
        if arrival:
            self.arrival_weather = await loop.run_in_executor(
                None, self._get_airport_weather_sync, arrival
            )

    def _fetch_fresh_pilot_data(self) -> dict | None:
        """Fetch fresh VATSIM data and find this pilot by callsign."""
        if not self.callsign:
            return None

        try:
            vatsim_data = download_vatsim_data()
            if not vatsim_data:
                return None

            pilots = vatsim_data.get("pilots", [])
            for pilot in pilots:
                if pilot.get("callsign") == self.callsign:
                    return pilot
        except Exception as e:
            debug(f"Could not fetch fresh pilot data for {self.callsign}: {e}")

        return None

    async def _load_member_stats(self) -> None:
        """Asynchronously fetch member statistics (pilot/ATC hours)."""
        if not self.cid:
            return

        loop = asyncio.get_event_loop()
        try:
            self.member_stats = await loop.run_in_executor(
                None, get_member_stats, self.cid
            )
            # Update title with member stats
            self._update_title()
        except asyncio.CancelledError:
            return
        except Exception as e:
            # Stats are optional, so a failure here must not break the screen
            debug(f"Member stats unavailable for CID {self.cid}: {e}")

    def _update_title(self) -> None:
        """Update the title widget with member stats."""
        try:
            title_widget = self.query_one("#flight-info-title", Static)
            title_widget.update(self._format_title())
        except Exception as e:
            debug(f"Could not update flight info title, widget not mounted: {e}")

    def _update_display(self) -> None:
        """Update the flight info display."""
        try:
            content_widget = self.query_one("#flight-info-content", Static)
            content_widget.update(self._format_flight_info())
        except Exception as e:
            debug(f"Could not update flight info content, widget not mounted: {e}")

    def _is_vfr_flight(self) -> bool:
        """Check if this is a VFR flight."""
        flight_plan = self.flight_data.get("flight_plan")
        if not flight_plan:
            return False

        altitude = flight_plan.get("altitude", "")
        flight_rules = flight_plan.get("flight_rules", "")

        # VFR if altitude starts with "VFR" or flight_rules is "V"
        return str(altitude).upper().startswith("VFR") or flight_rules.upper() == "V"

    def _should_check_mea(self) -> bool:
        """Check if we should verify MEA for this flight.

        Only check MEA for IFR flights with a route that contains airways.
        """
        if self._is_vfr_flight():
            return False

        flight_plan = self.flight_data.get("flight_plan")
        if not flight_plan:
            return False

        route = flight_plan.get("route", "")
        if not route:
            return False

        # Check if route contains any airways (V##, J##, T##, Q##)
        return bool(re.search(r"\b[VJTQ]\d+\b", route.upper()))

    async def _load_mea_info(self) -> None:
        """Asynchronously fetch MEA requirements for the route."""
        flight_plan = self.flight_data.get("flight_plan")
        if not flight_plan:
            self.mea_loading = False
            return

        route = flight_plan.get("route", "")
        if not route:
            self.mea_loading = False
            return

        loop = asyncio.get_event_loop()
        try:
            self.mea_info = await loop.run_in_executor(
                None, get_max_mea_for_route, route
            )
            self.mea_loading = False
            self._update_display()
        except asyncio.CancelledError:
            return
        except Exception as e:
            debug(f"MEA check failed for route {route!r}: {e}")
            self.mea_loading = False
            self.mea_info = None

    def _get_airport_weather_sync(self, icao: str) -> tuple | None:
        """
        Get flight category and weather details for an airport.

        Returns:
            Tuple of (category, color, visibility_str, ceiling_str) or None
            where visibility_str and ceiling_str are verbatim from METAR
            (e.g., "2SM", "BKN004")
        """
        if not icao or icao == "----":
            return None
        try:
            metar = get_metar(icao)
            if metar:
                category = get_flight_category(metar)
                color = config.CATEGORY_COLORS.get(category, "white")
                visibility_str, ceiling_str = _extract_flight_rules_weather(metar)
                return (category, color, visibility_str, ceiling_str)
        except Exception as e:
            debug(f"Could not derive flight category for {icao}: {e}")
        return None

    # Constants for alternate airport search
    MAX_ALTERNATE_SEARCH_RADIUS_NM = 100.0

    async def _find_vfr_alternates_async(
        self, origin_icao: str, target_attr: str, max_results: int = 3
    ) -> None:
        """
        Find VFR alternates asynchronously, updating display as results are found.

        Args:
            origin_icao: ICAO code of the origin airport
            target_attr: Attribute name to store results ('departure_alternates' or 'arrival_alternates')
            max_results: Maximum number of alternates to return
        """
        if not origin_icao:
            return

        # Check cache first
        current_time = datetime.now(UTC)
        cached = _fresh_cached_alternates(origin_icao, current_time)
        if cached is not None:
            debug(f"[VFR_ALT] Cache hit for {origin_icao}")
            setattr(self, target_attr, cached)
            self._update_display()
            return

        if not config.UNIFIED_AIRPORT_DATA:
            return

        origin = _airport_position(config.UNIFIED_AIRPORT_DATA, origin_icao)
        if origin is None:
            return

        nearby = self._alternate_candidates(origin_icao, origin)
        if not nearby:
            setattr(self, target_attr, [])
            _VFR_ALTERNATES_CACHE[origin_icao] = {
                "result": [],
                "timestamp": current_time,
            }
            return

        alternates = await self._search_alternates(
            origin, nearby, target_attr, max_results
        )
        if alternates is None:
            return

        # Cache the final result
        _VFR_ALTERNATES_CACHE[origin_icao] = {
            "result": alternates,
            "timestamp": datetime.now(UTC),
        }

        setattr(self, target_attr, alternates)

    def _alternate_candidates(
        self, origin_icao: str, origin: tuple[float, float]
    ) -> list[str]:
        """The four-letter airports within the search radius of the origin, nearest first, without the origin."""
        nearby_all = find_airports_near_position(
            *origin,
            config.UNIFIED_AIRPORT_DATA,
            radius_nm=self.MAX_ALTERNATE_SEARCH_RADIUS_NM,
            max_results=500,
        )
        return [
            icao
            for icao in nearby_all
            if len(icao) == 4 and icao.isalpha() and icao != origin_icao
        ]

    async def _search_alternates(
        self,
        origin: tuple[float, float],
        nearby: list[str],
        target_attr: str,
        max_results: int,
    ) -> list[tuple] | None:
        """Check the candidates' METARs in order, showing each VFR/MVFR find; None when cancelled."""
        loop = asyncio.get_event_loop()
        alternates = []

        # Search airports one at a time, updating display as we find VFR/MVFR
        for icao in nearby:
            if len(alternates) >= max_results:
                break

            try:
                metar = await loop.run_in_executor(None, get_metar, icao)
            except asyncio.CancelledError:
                return None

            alternate = _vfr_alternate(icao, metar, origin)
            if alternate is None:
                continue
            alternates.append(alternate)

            # Update display immediately with new result
            setattr(self, target_attr, alternates.copy())
            self._update_display()

        return alternates

    async def _load_altimeter_info(self) -> None:
        """Asynchronously fetch altimeter and airport weather information"""
        loop = asyncio.get_event_loop()

        # Run the blocking altimeter lookup in a thread pool
        try:
            self.altimeter_info = await loop.run_in_executor(
                None, self._get_altimeter_info_sync
            )

            # Update display immediately with altimeter info
            self.altimeter_loading = False
            self._update_display()

            # For VFR flights, fetch departure/arrival weather for warnings
            if self._is_vfr_flight():
                await self._load_vfr_weather()
        except asyncio.CancelledError:
            return  # Clean exit on cancellation
        except Exception as e:
            print(f"Error loading altimeter info: {e}")
            self.altimeter_info = ""
            self.altimeter_loading = False
            self._update_display()

    async def _load_vfr_weather(self) -> None:
        """Fetch a VFR flight's airport weather, then the alternates of each IFR/LIFR end, showing each stage."""
        flight_plan = self.flight_data["flight_plan"]
        departure = flight_plan.get("departure")
        arrival = flight_plan.get("arrival")
        await self._load_airport_weather(departure, arrival)

        # Update display with weather info before searching alternates
        self._update_display()

        # Use async method to update display progressively as alternates are found
        if _needs_alternates(self.departure_weather):
            await self._find_vfr_alternates_async(departure, "departure_alternates")
        if _needs_alternates(self.arrival_weather):
            await self._find_vfr_alternates_async(arrival, "arrival_alternates")
        self.alternates_searched = True

        # Final update with alternates
        self._update_display()

    def _format_title(self) -> str:
        """Format the modal title with callsign and member stats"""
        callsign = self.flight_data.get("callsign", "Unknown")
        pilot_name = self.flight_data.get("name", "Unknown Pilot")

        # Add pilot/ATC hours if available (hide if 0)
        stats_str = ""
        if self.member_stats:
            pilot_hours = self.member_stats.get("pilot", 0)
            atc_hours = self.member_stats.get("atc", 0)
            parts = []
            if pilot_hours:
                parts.append(f"P:{pilot_hours:,.0f}h")
            if atc_hours:
                parts.append(f"C:{atc_hours:,.0f}h")
            if parts:
                stats_str = f" ({' / '.join(parts)})"

        return f"{callsign} - {pilot_name}{stats_str}"

    def _format_flight_info(self) -> str:
        """Format all flight information for display"""
        flight_plan = self.flight_data.get("flight_plan")
        if flight_plan:
            lines = self._flight_plan_lines(flight_plan)
        else:
            lines = self._no_flight_plan_lines()
        return "\n".join(lines)

    def _flight_plan_lines(self, flight_plan: dict) -> list[str]:
        """The body for a filed flight: summary, route, MEA, VFR weather, altimeter and remarks."""
        lines = [self._summary_line(flight_plan), ""]
        lines.extend(self._route_lines(flight_plan))
        lines.extend(self._mea_lines(flight_plan))
        # VFR weather warning - show if VFR flight has non-VFR conditions
        if self._is_vfr_flight():
            lines.extend(self._vfr_weather_warning_lines(flight_plan))
        lines.extend(self._altimeter_lines())
        lines.extend(self._remarks_lines(flight_plan))
        return lines

    def _summary_line(self, flight_plan: dict) -> str:
        """The plan's summary: DEP → ARR (Altn: ALTN) // ACFT // ALT // RULES, then squawk and ETA."""
        departure = flight_plan.get("departure", "----")
        arrival = flight_plan.get("arrival", "----")
        line = f"{departure} → {arrival}"

        alternate = flight_plan.get("alternate", "")
        if alternate:
            line += f" (Altn: {alternate})"
        line += f" // {_aircraft_with_equipment(flight_plan)} // "
        line += _altitude_and_rules(flight_plan)

        # Assigned squawk code (if available - "0000" means not assigned)
        assigned_squawk = flight_plan.get("assigned_transponder", "")
        if assigned_squawk and assigned_squawk != "0000":
            line += f" // SQ: {assigned_squawk}"

        # Add ETA for arrivals (in-flight or landed)
        eta_info = self._get_eta_info()
        if eta_info:
            line += f" // {eta_info}"
        return line

    def _route_lines(self, flight_plan: dict) -> list[str]:
        """The filed route, wrapped to fit the modal, or no lines without one."""
        route = flight_plan.get("route", "")
        if not route:
            return []
        route_lines = self._wrap_text(route, 82)
        return ["[bold]ROUTE[/bold]", *(f"  {line}" for line in route_lines), ""]

    def _mea_lines(self, flight_plan: dict) -> list[str]:
        """An IFR flight's MEA check lines, the loading line while the check runs, or no lines."""
        if not self._is_vfr_flight() and self.mea_info is not None:
            return format_mea_lines(self.mea_info, flight_plan.get("altitude", ""))
        if self.mea_loading:
            return ["[dim]Checking MEA requirements...[/dim]", ""]
        return []

    def _vfr_weather_warning_lines(self, flight_plan: dict) -> list[str]:
        """The VFR weather warning for non-VFR conditions at either end, or no lines."""
        is_on_ground = self.flight_data.get("groundspeed", 0) <= 40
        warning_lines = []

        # Departure alternates only while still on the ground
        if self.departure_weather and self.departure_weather[0] != "VFR":
            warning_lines.extend(
                self._airport_warning_lines(
                    flight_plan.get("departure", "----"),
                    self.departure_weather,
                    self.departure_alternates,
                    show_alternates=is_on_ground,
                )
            )

        # Arrival alternates help planning before departure too; skip them only once landed there
        if self.arrival_weather and self.arrival_weather[0] != "VFR":
            landed_at_arrival = is_on_ground and self._get_eta_info() == "LANDED"
            warning_lines.extend(
                self._airport_warning_lines(
                    flight_plan.get("arrival", "----"),
                    self.arrival_weather,
                    self.arrival_alternates,
                    show_alternates=not landed_at_arrival,
                )
            )

        if not warning_lines:
            return []
        return ["[bold]VFR WEATHER WARNING[/bold]", *warning_lines, ""]

    def _airport_warning_lines(
        self,
        icao: str,
        weather: tuple,
        alternates: list | None,
        *,
        show_alternates: bool,
    ) -> list[str]:
        """One airport's category and weather, then its alternates line when shown and IFR/LIFR."""
        cat, color, vis, ceil = weather
        details = self._format_weather_details(vis, ceil)
        lines = [f"  {icao} is [{color} bold]{cat}[/{color} bold]{details}"]
        if show_alternates and cat in ("IFR", "LIFR"):
            lines.append(_alternates_line(alternates, self.alternates_searched))
        return lines

    def _altimeter_lines(self) -> list[str]:
        """The loading line or the nearest altimeter, each followed by a blank line, or no lines."""
        if self.altimeter_loading:
            return ["[dim]Loading nearest altimeter...[/dim]", ""]
        if self.altimeter_info:
            return [self.altimeter_info, ""]
        return []

    def _remarks_lines(self, flight_plan: dict) -> list[str]:
        """The remarks, wrapped and cut to their first five lines, or no lines without any."""
        remarks = flight_plan.get("remarks", "")
        if not remarks:
            return []
        remarks_lines = self._wrap_text(remarks, 82)
        lines = ["[bold]REMARKS[/bold]", *(f"  {line}" for line in remarks_lines[:5])]
        if len(remarks_lines) > 5:
            lines.append(f"  ... ({len(remarks_lines) - 5} more lines)")
        lines.append("")
        return lines

    def _no_flight_plan_lines(self) -> list[str]:
        """The body without a flight plan: the airport the aircraft is on, if any, and the altimeter."""
        lines = ["[bold]NO FLIGHT PLAN[/bold]", ""]
        lines.extend(self._on_ground_lines())
        lines.extend(
            self._altimeter_lines()
            or ["[dim]No altimeter information available[/dim]", ""]
        )
        return lines

    def _on_ground_lines(self) -> list[str]:
        """The airport the aircraft is stopped or taxiing at, or no lines."""
        groundspeed = self.flight_data.get("groundspeed", 0)
        if groundspeed <= 40 and config.UNIFIED_AIRPORT_DATA:
            nearest_airport = get_nearest_airport_if_on_ground(
                self.flight_data, config.UNIFIED_AIRPORT_DATA
            )
            if nearest_airport:
                airport_data = config.UNIFIED_AIRPORT_DATA.get(nearest_airport, {})
                airport_name = airport_data.get("city", "Unknown")
                return [
                    f"[bold]On Ground at [/bold]{nearest_airport} - {airport_name}",
                    "",
                ]
        return []

    def _wrap_text(self, text: str, width: int) -> list:
        """Wrap text to specified width"""
        words = text.split()
        lines = []
        current_line = []
        current_length = 0

        for word in words:
            word_length = len(word)
            if current_length + word_length + len(current_line) <= width:
                current_line.append(word)
                current_length += word_length
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]
                current_length = word_length

        if current_line:
            lines.append(" ".join(current_line))

        return lines

    def _format_weather_details(
        self, visibility_str: str | None, ceiling_str: str | None
    ) -> str:
        """
        Format visibility and ceiling into a METAR-style details string.

        Args:
            visibility_str: Visibility string from METAR (e.g., "2SM", "1/2SM"), or None
            ceiling_str: Ceiling string from METAR (e.g., "BKN004"), or None

        Returns:
            Formatted string like " (2SM BKN004)" or "" if no data
        """
        parts = [p for p in (visibility_str, ceiling_str) if p]
        if parts:
            return f" ({' '.join(parts)})"
        return ""

    def _get_eta_info(self) -> str | None:
        """
        Calculate and format ETA information for the flight.

        Returns:
            Formatted ETA string like "ETA 45m (14:30)" for in-flight,
            "LANDED" if on ground at arrival, or None if not applicable.
        """
        flight_plan = self.flight_data.get("flight_plan")
        if not flight_plan:
            return None

        arrival = flight_plan.get("arrival")
        if not arrival or arrival == "----":
            return None

        distance_to_arrival = self._distance_to_arrival_nm(arrival)
        if distance_to_arrival is None:
            return None

        # Check if on ground (groundspeed <= 40 knots)
        groundspeed = self.flight_data.get("groundspeed", 0)
        if groundspeed <= 40:
            # Landed when within 5nm of the arrival airport; on the ground elsewhere
            # it is probably at departure, so no ETA is needed
            return "LANDED" if distance_to_arrival <= 5.0 else None

        return self._in_flight_eta(arrival, flight_plan, groundspeed)

    def _distance_to_arrival_nm(self, arrival: str) -> float | None:
        """Great-circle distance from the aircraft to the arrival airport, or None if either position is unknown."""
        latitude = self.flight_data.get("latitude")
        longitude = self.flight_data.get("longitude")
        if latitude is None or longitude is None:
            return None

        if config.UNIFIED_AIRPORT_DATA is None:
            return None

        arrival_position = _airport_position(config.UNIFIED_AIRPORT_DATA, arrival)
        if arrival_position is None:
            return None

        try:
            return haversine_distance_nm(latitude, longitude, *arrival_position)
        except ValueError:
            return None

    def _in_flight_eta(
        self, arrival: str, flight_plan: dict, groundspeed: float
    ) -> str | None:
        """The "ETA <time> (<local> LT)" text for an airborne flight, or None without an ETA."""
        # Prepare flight dict for calculate_eta (it expects 'arrival' at top level)
        flight_for_eta = {
            "arrival": arrival,
            "latitude": self.flight_data.get("latitude"),
            "longitude": self.flight_data.get("longitude"),
            "groundspeed": groundspeed,
            "flight_plan": flight_plan,
        }

        eta_display, eta_local_time, _eta_hours = calculate_eta(
            flight_for_eta, config.UNIFIED_AIRPORT_DATA, config.AIRCRAFT_APPROACH_SPEEDS
        )

        if eta_display and eta_display not in ("----", ""):
            return f"ETA {eta_display} ({eta_local_time} LT)"

        return None

    def _get_altimeter_info_sync(self) -> str:
        """Get nearest airport altimeter setting for the flight's current position (synchronous version for executor)"""
        # Check if we have position data
        latitude = self.flight_data.get("latitude")
        longitude = self.flight_data.get("longitude")

        if latitude is None or longitude is None:
            return ""

        if config.UNIFIED_AIRPORT_DATA is None:
            return ""

        try:
            # Get heading and groundspeed to bias search towards airports ahead
            heading = self.flight_data.get("heading")
            groundspeed = self.flight_data.get("groundspeed")

            # Find nearest airport with METAR (biased towards direction of flight)
            result = find_nearest_airport_with_metar(
                latitude,
                longitude,
                config.UNIFIED_AIRPORT_DATA,
                max_distance_nm=100.0,
                aircraft_heading=heading,
                aircraft_groundspeed=groundspeed,
            )

            if result:
                airport_icao, altimeter, distance_nm = result

                # Get the city name from airport data
                airport_data = config.UNIFIED_AIRPORT_DATA.get(airport_icao, {})
                city_name = airport_data.get("city", airport_icao)

                altimeter_word = ""
                if altimeter.startswith("A"):
                    altimeter_word = "Altimeter"
                elif altimeter.startswith("Q"):
                    altimeter_word = "QNH"

                return f"[bold]{city_name} {altimeter_word}:[/bold] {altimeter} ({airport_icao}, {distance_nm:.1f}nm)"
            else:
                # No airport with METAR found within range
                return ""
        except Exception as e:
            # Log the error for debugging
            import traceback

            print(f"Error getting altimeter info: {e}")
            traceback.print_exc()
            return ""

    def action_close(self) -> None:
        """Close the modal"""
        self.dismiss()

    def action_copy_route(self) -> None:
        """Copy the flight route to clipboard"""
        flight_plan = self.flight_data.get("flight_plan")
        if not flight_plan:
            self.notify("No flight plan filed", severity="warning")
            return

        route = flight_plan.get("route", "")
        if not route:
            self.notify("No route filed", severity="warning")
            return

        try:
            import pyperclip

            pyperclip.copy(route)
            self.notify("Route copied to clipboard", severity="information")
        except Exception:
            self.notify("Failed to copy to clipboard", severity="error")

    def action_show_diversions(self) -> None:
        """Open the diversion finder modal for this flight"""
        from .diversion_modal import DiversionModal

        # Get the current VATSIM data from the app if available
        vatsim_data = getattr(self.app, "vatsim_data", None)

        diversion_modal = DiversionModal(
            flight_data=self.flight_data,
            vatsim_data=vatsim_data,
        )
        self.app.push_screen(diversion_modal)

    def action_show_route_weather(self) -> None:
        """Open the route weather modal for this flight"""
        from .route_weather import RouteWeatherScreen

        # Check if flight has a route
        flight_plan = self.flight_data.get("flight_plan")
        if not flight_plan:
            self.notify("No flight plan filed", severity="warning")
            return

        route = flight_plan.get("route", "")
        departure = flight_plan.get("departure", "")
        arrival = flight_plan.get("arrival", "")

        if not route and not (departure and arrival):
            self.notify("No route filed", severity="warning")
            return

        route_weather = RouteWeatherScreen(self.flight_data)
        self.app.push_screen(route_weather)
