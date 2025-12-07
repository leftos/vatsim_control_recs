"""Flight Information Modal Screen"""

import asyncio
from textual.screen import ModalScreen
from textual.widgets import Static
from textual.containers import Container, Vertical
from textual.binding import Binding
from textual.app import ComposeResult
from backend import find_nearest_airport_with_metar
from backend.core.flights import get_nearest_airport_if_on_ground
from backend.data.vatsim_api import download_vatsim_data
from ui import config


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
        self._pending_tasks: list = []  # Track pending async tasks
        self._refresh_timer = None  # Timer for periodic refresh
    
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
        # Don't refresh if already loading
        if self.altimeter_loading:
            return

        self.altimeter_loading = True
        self._update_display()

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
        except asyncio.CancelledError:
            return
        except Exception:
            pass  # Keep existing data on error
        finally:
            self.altimeter_loading = False
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
    
    async def _load_altimeter_info(self) -> None:
        """Asynchronously fetch altimeter information"""
        loop = asyncio.get_event_loop()

        # Run the blocking altimeter lookup in a thread pool
        try:
            self.altimeter_info = await loop.run_in_executor(
                None,
                self._get_altimeter_info_sync
            )
        except asyncio.CancelledError:
            return  # Clean exit on cancellation
        except Exception as e:
            print(f"Error loading altimeter info: {e}")
            self.altimeter_info = ""
        finally:
            self.altimeter_loading = False

            # Only update display if modal is still mounted
            if not self._pending_tasks:  # Modal was dismissed
                return
            try:
                content_widget = self.query_one("#flight-info-content", Static)
                content_widget.update(self._format_flight_info())
            except Exception:
                pass
    
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
            # Find nearest airport with METAR
            result = find_nearest_airport_with_metar(
                latitude,
                longitude,
                config.UNIFIED_AIRPORT_DATA,
                max_distance_nm=100.0
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