"""Flight Information Modal Screen"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, Any
from textual.screen import ModalScreen
from textual.widgets import Static
from textual.containers import Container, Vertical
from textual.binding import Binding
from textual.app import ComposeResult
from backend import (
    find_nearest_airport_with_metar,
    find_airports_near_position,
    get_metar,
    haversine_distance_nm,
    calculate_bearing,
    bearing_to_compass,
    calculate_eta
)
from backend.core.flights import get_nearest_airport_if_on_ground
from backend.data.vatsim_api import download_vatsim_data
from ui import config
from ui.debug_logger import debug
from ui.modals.metar_info import get_flight_category, _extract_flight_rules_weather


# Cache for VFR alternate results: {origin_icao: {'result': [...], 'timestamp': datetime}}
_VFR_ALTERNATES_CACHE: Dict[str, Dict[str, Any]] = {}
_VFR_ALTERNATES_CACHE_DURATION = 60  # 60 seconds (matches METAR cache)


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
    
    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("q", "close", "Close"),
    ]
    
    def __init__(self, flight_data: dict):
        """
        Initialize the flight info modal.

        Args:
            flight_data: Dictionary containing flight information from VATSIM data
        """
        super().__init__()
        self.flight_data = flight_data
        self.callsign = flight_data.get('callsign', '')  # Store callsign for refresh lookups
        self.altimeter_info = None  # Will be populated asynchronously
        self.altimeter_loading = True
        # Weather info: (category, color, visibility_sm, ceiling_ft)
        self.departure_weather = None
        self.arrival_weather = None
        # Alternate airports: list of (icao, category, color, distance_nm, direction)
        # None = not yet searched, [] = searched but none found
        self.departure_alternates = None
        self.arrival_alternates = None
        self.alternates_searched = False  # Track if we've completed the search
        self._pending_tasks: list = []  # Track pending async tasks
        self._refresh_timer = None  # Timer for periodic refresh
        self._refresh_in_progress = False  # Track if a background refresh is running
    
    def compose(self) -> ComposeResult:
        with Container(id="flight-info-container"):
            yield Static(self._format_title(), id="flight-info-title")
            with Vertical():
                yield Static(self._format_flight_info(), classes="info-section", id="flight-info-content")
            yield Static("Press Escape or Q to close", id="flight-info-hint")
    
    async def on_mount(self) -> None:
        """Load altimeter info asynchronously after modal is shown"""
        # Start loading altimeter info in the background (tracked for cleanup)
        task = asyncio.create_task(self._load_altimeter_info())
        self._pending_tasks.append(task)

        # Start periodic refresh timer to keep flight data current
        self._refresh_timer = self.set_interval(
            self.FLIGHT_DATA_REFRESH_INTERVAL,
            self._refresh_flight_data
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
                None,
                self._get_altimeter_info_sync
            )

            # Refresh airport weather for VFR flights
            if self._is_vfr_flight():
                flight_plan = self.flight_data.get('flight_plan')
                if flight_plan:
                    departure = flight_plan.get('departure')
                    arrival = flight_plan.get('arrival')

                    if departure:
                        self.departure_weather = await loop.run_in_executor(
                            None,
                            self._get_airport_weather_sync,
                            departure
                        )
                    if arrival:
                        self.arrival_weather = await loop.run_in_executor(
                            None,
                            self._get_airport_weather_sync,
                            arrival
                        )

                    # Refresh alternate airports if departure/arrival has IFR/LIFR
                    # Use async method for progressive updates
                    if self.departure_weather and self.departure_weather[0] in ('IFR', 'LIFR'):
                        self.departure_alternates = []  # Clear before searching
                        await self._find_vfr_alternates_async(departure, 'departure_alternates')
                    else:
                        self.departure_alternates = None

                    if self.arrival_weather and self.arrival_weather[0] in ('IFR', 'LIFR'):
                        self.arrival_alternates = []  # Clear before searching
                        await self._find_vfr_alternates_async(arrival, 'arrival_alternates')
                    else:
                        self.arrival_alternates = None
                    self.alternates_searched = True
        except asyncio.CancelledError:
            return
        except Exception:
            pass  # Keep existing data on error
        finally:
            self._refresh_in_progress = False
            self._update_display()

    def _fetch_fresh_pilot_data(self) -> dict | None:
        """Fetch fresh VATSIM data and find this pilot by callsign."""
        if not self.callsign:
            return None

        try:
            vatsim_data = download_vatsim_data()
            if not vatsim_data:
                return None

            pilots = vatsim_data.get('pilots', [])
            for pilot in pilots:
                if pilot.get('callsign') == self.callsign:
                    return pilot
        except Exception:
            pass

        return None

    def _update_display(self) -> None:
        """Update the flight info display."""
        try:
            content_widget = self.query_one("#flight-info-content", Static)
            content_widget.update(self._format_flight_info())
        except Exception:
            pass  # Widget may not be mounted

    def _is_vfr_flight(self) -> bool:
        """Check if this is a VFR flight."""
        flight_plan = self.flight_data.get('flight_plan')
        if not flight_plan:
            return False

        altitude = flight_plan.get('altitude', '')
        flight_rules = flight_plan.get('flight_rules', '')

        # VFR if altitude starts with "VFR" or flight_rules is "V"
        return str(altitude).upper().startswith('VFR') or flight_rules.upper() == 'V'

    def _get_airport_weather_sync(self, icao: str) -> tuple | None:
        """
        Get flight category and weather details for an airport.

        Returns:
            Tuple of (category, color, visibility_str, ceiling_str) or None
            where visibility_str and ceiling_str are verbatim from METAR
            (e.g., "2SM", "BKN004")
        """
        if not icao or icao == '----':
            return None
        try:
            metar = get_metar(icao)
            if metar:
                category, color = get_flight_category(metar)
                visibility_str, ceiling_str = _extract_flight_rules_weather(metar)
                return (category, color, visibility_str, ceiling_str)
        except Exception:
            pass
        return None

    # Constants for alternate airport search
    MAX_ALTERNATE_SEARCH_RADIUS_NM = 100.0

    async def _find_vfr_alternates_async(
        self,
        origin_icao: str,
        target_attr: str,
        max_results: int = 3
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
        current_time = datetime.now(timezone.utc)
        if origin_icao in _VFR_ALTERNATES_CACHE:
            cache_entry = _VFR_ALTERNATES_CACHE[origin_icao]
            time_since_cache = (current_time - cache_entry['timestamp']).total_seconds()
            if time_since_cache < _VFR_ALTERNATES_CACHE_DURATION:
                debug(f"[VFR_ALT] Cache hit for {origin_icao}")
                setattr(self, target_attr, cache_entry['result'])
                self._update_display()
                return

        if not config.UNIFIED_AIRPORT_DATA:
            return

        origin_data = config.UNIFIED_AIRPORT_DATA.get(origin_icao)
        if not origin_data:
            return

        origin_lat = origin_data.get('latitude')
        origin_lon = origin_data.get('longitude')
        if origin_lat is None or origin_lon is None:
            return

        # Find nearby airports
        nearby_all = find_airports_near_position(
            origin_lat, origin_lon,
            config.UNIFIED_AIRPORT_DATA,
            radius_nm=self.MAX_ALTERNATE_SEARCH_RADIUS_NM,
            max_results=500
        )

        nearby = [
            icao for icao in nearby_all
            if len(icao) == 4 and icao.isalpha() and icao != origin_icao
        ]

        if not nearby:
            setattr(self, target_attr, [])
            _VFR_ALTERNATES_CACHE[origin_icao] = {
                'result': [],
                'timestamp': current_time
            }
            return

        loop = asyncio.get_event_loop()
        alternates = []

        # Search airports one at a time, updating display as we find VFR/MVFR
        for icao in nearby:
            if len(alternates) >= max_results:
                break

            try:
                metar = await loop.run_in_executor(None, get_metar, icao)
            except asyncio.CancelledError:
                return

            if not metar:
                continue

            category, color = get_flight_category(metar)
            if category not in ('VFR', 'MVFR'):
                continue

            # Calculate distance and direction
            apt_data = config.UNIFIED_AIRPORT_DATA.get(icao, {})
            apt_lat = apt_data.get('latitude')
            apt_lon = apt_data.get('longitude')
            if apt_lat is None or apt_lon is None:
                continue

            distance = haversine_distance_nm(origin_lat, origin_lon, apt_lat, apt_lon)
            bearing = calculate_bearing(origin_lat, origin_lon, apt_lat, apt_lon)
            direction = bearing_to_compass(bearing)

            alternates.append((icao, category, color, distance, direction))

            # Update display immediately with new result
            setattr(self, target_attr, alternates.copy())
            self._update_display()

        # Cache the final result
        _VFR_ALTERNATES_CACHE[origin_icao] = {
            'result': alternates,
            'timestamp': datetime.now(timezone.utc)
        }

        setattr(self, target_attr, alternates)

    async def _load_altimeter_info(self) -> None:
        """Asynchronously fetch altimeter and airport weather information"""
        loop = asyncio.get_event_loop()

        # Run the blocking altimeter lookup in a thread pool
        try:
            self.altimeter_info = await loop.run_in_executor(
                None,
                self._get_altimeter_info_sync
            )

            # Update display immediately with altimeter info
            self.altimeter_loading = False
            self._update_display()

            # For VFR flights, fetch departure/arrival weather for warnings
            if self._is_vfr_flight():
                flight_plan = self.flight_data.get('flight_plan')
                if flight_plan:
                    departure = flight_plan.get('departure')
                    arrival = flight_plan.get('arrival')

                    if departure:
                        self.departure_weather = await loop.run_in_executor(
                            None,
                            self._get_airport_weather_sync,
                            departure
                        )
                    if arrival:
                        self.arrival_weather = await loop.run_in_executor(
                            None,
                            self._get_airport_weather_sync,
                            arrival
                        )

                    # Update display with weather info before searching alternates
                    self._update_display()

                    # Find alternate airports if departure/arrival has IFR/LIFR
                    # Use async method to update display progressively as alternates are found
                    if self.departure_weather and self.departure_weather[0] in ('IFR', 'LIFR'):
                        await self._find_vfr_alternates_async(departure, 'departure_alternates')
                    if self.arrival_weather and self.arrival_weather[0] in ('IFR', 'LIFR'):
                        await self._find_vfr_alternates_async(arrival, 'arrival_alternates')
                    self.alternates_searched = True

                    # Final update with alternates
                    self._update_display()
        except asyncio.CancelledError:
            return  # Clean exit on cancellation
        except Exception as e:
            print(f"Error loading altimeter info: {e}")
            self.altimeter_info = ""
            self.altimeter_loading = False
            self._update_display()
    
    def _format_title(self) -> str:
        """Format the modal title with callsign"""
        callsign = self.flight_data.get('callsign', 'Unknown')
        pilot_name = self.flight_data.get('name', 'Unknown Pilot')
        return f"Flight Info: {callsign} - {pilot_name}"
    
    def _format_flight_info(self) -> str:
        """Format all flight information for display"""
        lines = []
        
        # Flight Plan section
        flight_plan = self.flight_data.get('flight_plan')
        if flight_plan:
            # Display info as
            # DEP->ARR (Altn: ALTN) // ACFT
            # ALT // IFR/VFR
            
            # Route
            departure = flight_plan.get('departure', '----')
            arrival = flight_plan.get('arrival', '----')
            line = f"{departure} → {arrival}"
            
            # Alternate
            alternate = flight_plan.get('alternate', '')
            if alternate:
                line += f" (Altn: {alternate})"
            line += " // "
            
            # Aircraft
            aircraft_short = flight_plan.get('aircraft_short', flight_plan.get('aircraft', '----'))
            line += f"{aircraft_short} // "
                        
            # Filed altitude and flight rules
            altitude = flight_plan.get('altitude', '----')
            flight_rules = flight_plan.get('flight_rules', '?')

            # "VFR" in altitude field means VFR flight, regardless of flight_rules
            # Could be "VFR" or "VFR/105" (VFR at 10,500 feet)
            if str(altitude).upper().startswith('VFR'):
                line += altitude  # Display as filed (e.g., "VFR" or "VFR/105")
            else:
                if altitude != '----':
                    try:
                        line += f"{int(altitude):,} // "
                    except ValueError:
                        line += f"{altitude} // "
                line += "IFR" if flight_rules.upper() == 'I' else "VFR" if flight_rules.upper() == 'V' else "?"

            # Assigned squawk code (if available - "0000" means not assigned)
            assigned_squawk = flight_plan.get('assigned_transponder', '')
            if assigned_squawk and assigned_squawk != '0000':
                line += f" // SQ: {assigned_squawk}"

            # Add ETA for arrivals (in-flight or landed)
            eta_info = self._get_eta_info()
            if eta_info:
                line += f" // {eta_info}"

            lines.append(line)
            lines.append("")
            line = ""

            # Filed route
            route = flight_plan.get('route', '')
            if route:
                lines.append("[bold]ROUTE[/bold]")
                # Wrap long routes to fit in the modal
                route_lines = self._wrap_text(route, 82)
                for route_line in route_lines:
                    lines.append(f"  {route_line}")
                lines.append("")

            # VFR weather warning - show if VFR flight has non-VFR conditions
            if self._is_vfr_flight():
                groundspeed = self.flight_data.get('groundspeed', 0)
                is_on_ground = groundspeed <= 40
                warning_lines = []

                # Departure weather warning (only show alternates if still on ground)
                if self.departure_weather and self.departure_weather[0] != 'VFR':
                    cat, color, vis, ceil = self.departure_weather
                    # Build weather details string
                    details = self._format_weather_details(vis, ceil)
                    warning_lines.append(f"  {departure} is [{color} bold]{cat}[/{color} bold]{details}")

                    # Show alternate departure airports only if still on ground
                    if is_on_ground and cat in ('IFR', 'LIFR'):
                        if self.departure_alternates:
                            alt_strs = []
                            for alt_icao, _, alt_color, dist, direction in self.departure_alternates:
                                alt_strs.append(f"[{alt_color}]{alt_icao}[/{alt_color}] ({dist:.0f}nm {direction})")
                            warning_lines.append(f"    Consider: {', '.join(alt_strs)}")
                        elif self.alternates_searched:
                            warning_lines.append("    [dim]No VFR/MVFR alternates within 100nm[/dim]")
                        else:
                            warning_lines.append("    [dim]Searching for alternates...[/dim]")

                # Arrival weather warning (only show alternates if not yet landed)
                if self.arrival_weather and self.arrival_weather[0] != 'VFR':
                    cat, color, vis, ceil = self.arrival_weather
                    details = self._format_weather_details(vis, ceil)
                    warning_lines.append(f"  {arrival} is [{color} bold]{cat}[/{color} bold]{details}")

                    # Show alternate arrival airports only if still in flight
                    if not is_on_ground and cat in ('IFR', 'LIFR'):
                        if self.arrival_alternates:
                            alt_strs = []
                            for alt_icao, _, alt_color, dist, direction in self.arrival_alternates:
                                alt_strs.append(f"[{alt_color}]{alt_icao}[/{alt_color}] ({dist:.0f}nm {direction})")
                            warning_lines.append(f"    Consider: {', '.join(alt_strs)}")
                        elif self.alternates_searched:
                            warning_lines.append("    [dim]No VFR/MVFR alternates within 100nm[/dim]")
                        else:
                            warning_lines.append("    [dim]Searching for alternates...[/dim]")

                if warning_lines:
                    lines.append("[bold]VFR WEATHER WARNING[/bold]")
                    lines.extend(warning_lines)
                    lines.append("")

            # Altimeter section (display first, above flight plan)
            if self.altimeter_loading:
                lines.append("[dim]Loading nearest altimeter...[/dim]")
                lines.append("")
            elif self.altimeter_info:
                lines.append(self.altimeter_info)
                lines.append("")
            
            # Remarks (only show first part if too long)
            remarks = flight_plan.get('remarks', '')
            if remarks:
                lines.append("[bold]REMARKS[/bold]")
                remarks_lines = self._wrap_text(remarks, 82)
                # Limit to first 5 lines of remarks to avoid overflow
                for remarks_line in remarks_lines[:5]:
                    lines.append(f"  {remarks_line}")
                if len(remarks_lines) > 5:
                    lines.append(f"  ... ({len(remarks_lines) - 5} more lines)")
                lines.append("")
        else:
            # No flight plan - show nearest altimeter and airport if on ground
            lines.append("[bold]NO FLIGHT PLAN[/bold]")
            lines.append("")# Nearest airport if on ground

            groundspeed = self.flight_data.get('groundspeed', 0)
            if groundspeed <= 40:  # On ground or nearly stopped                
                # Get nearest airport info
                if config.UNIFIED_AIRPORT_DATA:
                    nearest_airport = get_nearest_airport_if_on_ground(
                        self.flight_data,
                        config.UNIFIED_AIRPORT_DATA
                    )
                    if nearest_airport:
                        airport_data = config.UNIFIED_AIRPORT_DATA.get(nearest_airport, {})
                        airport_name = airport_data.get('city', 'Unknown')
                        lines.append(f"[bold]On Ground at [/bold]{nearest_airport} - {airport_name}")
                        lines.append("")
            
            # Altimeter section
            if self.altimeter_loading:
                lines.append("[dim]Loading nearest altimeter...[/dim]")
            elif self.altimeter_info:
                lines.append(self.altimeter_info)
            else:
                lines.append("[dim]No altimeter information available[/dim]")
            lines.append("")
        
        return "\n".join(lines)
    
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

    def _format_weather_details(self, visibility_str: str | None, ceiling_str: str | None) -> str:
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
        flight_plan = self.flight_data.get('flight_plan')
        if not flight_plan:
            return None

        arrival = flight_plan.get('arrival')
        if not arrival or arrival == '----':
            return None

        groundspeed = self.flight_data.get('groundspeed', 0)
        latitude = self.flight_data.get('latitude')
        longitude = self.flight_data.get('longitude')

        if latitude is None or longitude is None:
            return None

        if config.UNIFIED_AIRPORT_DATA is None:
            return None

        # Check if the arrival airport exists in our data
        arrival_airport = config.UNIFIED_AIRPORT_DATA.get(arrival)
        if not arrival_airport:
            return None

        arrival_lat = arrival_airport.get('latitude')
        arrival_lon = arrival_airport.get('longitude')
        if arrival_lat is None or arrival_lon is None:
            return None

        # Calculate distance to arrival airport
        try:
            distance_to_arrival = haversine_distance_nm(
                latitude, longitude, arrival_lat, arrival_lon
            )
        except ValueError:
            return None

        # Check if on ground (groundspeed <= 40 knots)
        if groundspeed <= 40:
            # On ground - check if near arrival airport (within 5nm)
            if distance_to_arrival <= 5.0:
                return "LANDED"
            else:
                # On ground but not at arrival - probably at departure, no ETA needed
                return None

        # In-flight - calculate ETA
        # Prepare flight dict for calculate_eta (it expects 'arrival' at top level)
        flight_for_eta = {
            'arrival': arrival,
            'latitude': latitude,
            'longitude': longitude,
            'groundspeed': groundspeed,
            'flight_plan': flight_plan
        }

        eta_display, eta_local_time, eta_hours = calculate_eta(
            flight_for_eta,
            config.UNIFIED_AIRPORT_DATA,
            config.AIRCRAFT_APPROACH_SPEEDS
        )

        if eta_display and eta_display not in ('----', ''):
            return f"ETA {eta_display} ({eta_local_time} LT)"

        return None

    def _format_time(self, time_str: str) -> str:
        """Format departure time (HHMM format)"""
        if not time_str or time_str == '----':
            return '----'
        # Format as HH:MM
        if len(time_str) == 4:
            return f"{time_str[:2]}:{time_str[2:]}Z"
        return time_str
    
    def _format_duration(self, duration_str: str) -> str:
        """Format duration (HHMM format)"""
        if not duration_str or duration_str == '----':
            return '----'
        # Format as HH:MM
        if len(duration_str) == 4:
            hours = duration_str[:2]
            minutes = duration_str[2:]
            return f"{hours}h {minutes}m"
        return duration_str
    
    def _get_altimeter_info_sync(self) -> str:
        """Get nearest airport altimeter setting for the flight's current position (synchronous version for executor)"""
        # Check if we have position data
        latitude = self.flight_data.get('latitude')
        longitude = self.flight_data.get('longitude')

        if latitude is None or longitude is None:
            return ""

        if config.UNIFIED_AIRPORT_DATA is None:
            return ""

        try:
            # Get heading and groundspeed to bias search towards airports ahead
            heading = self.flight_data.get('heading')
            groundspeed = self.flight_data.get('groundspeed')

            # Find nearest airport with METAR (biased towards direction of flight)
            result = find_nearest_airport_with_metar(
                latitude,
                longitude,
                config.UNIFIED_AIRPORT_DATA,
                max_distance_nm=100.0,
                aircraft_heading=heading,
                aircraft_groundspeed=groundspeed
            )
            
            if result:
                airport_icao, altimeter, distance_nm = result
                
                # Get the city name from airport data
                airport_data = config.UNIFIED_AIRPORT_DATA.get(airport_icao, {})
                city_name = airport_data.get('city', airport_icao)
                
                altimeter_word = ""
                if altimeter.startswith('A'):
                    altimeter_word = "Altimeter"
                elif altimeter.startswith('Q'):
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