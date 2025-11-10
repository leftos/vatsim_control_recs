"""Flight Information Modal Screen"""

from textual.screen import ModalScreen
from textual.widgets import Static
from textual.containers import Container, Vertical
from textual.binding import Binding
from textual.app import ComposeResult


class FlightInfoScreen(ModalScreen):
    """Modal screen showing detailed flight information"""
    
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
    
    def compose(self) -> ComposeResult:
        with Container(id="flight-info-container"):
            yield Static(self._format_title(), id="flight-info-title")
            with Vertical():
                yield Static(self._format_flight_info(), classes="info-section")
            yield Static("Press Escape or Q to close", id="flight-info-hint")
    
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
                line += f" (Altn: {alternate}) // "
            
            # Aircraft
            aircraft_short = flight_plan.get('aircraft_short', flight_plan.get('aircraft', '----'))
            line += f"{aircraft_short} // "
                        
            # Filed altitude
            altitude = flight_plan.get('altitude', '----')
            if altitude != '----':
                line += f"{int(altitude):,} // "
            
            # Flight rules
            flight_rules = flight_plan.get('flight_rules', '?')
            line += "IFR" if flight_rules.upper() == 'I' else "VFR" if flight_rules.upper() == 'V' else "?"
            
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
    
    def action_close(self) -> None:
        """Close the modal"""
        self.dismiss()