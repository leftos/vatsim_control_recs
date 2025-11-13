"""METAR and TAF Information Modal Screen"""

import re
from datetime import datetime, timezone
from typing import Optional
from textual.screen import ModalScreen
from textual.widgets import Static, Input
from textual.containers import Container
from textual.binding import Binding
from textual.app import ComposeResult

from backend import get_metar, get_taf
from ui import config


class MetarInfoScreen(ModalScreen):
    """Modal screen showing full METAR and TAF for an airport"""
    
    CSS = """
    MetarInfoScreen {
        align: center middle;
    }
    
    #metar-container {
        width: 80;
        height: auto;
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
    
    def __init__(self):
        super().__init__()
        self.metar_result = ""
    
    def compose(self) -> ComposeResult:
        with Container(id="metar-container"):
            yield Static("METAR & TAF Lookup", id="metar-title")
            with Container(id="metar-input-container"):
                yield Input(placeholder="Enter airport ICAO code (e.g., KSFO)", id="metar-input")
            yield Static("", id="metar-result", markup=True)
            yield Static("Press Enter to fetch, Escape to close", id="metar-hint")
    
    def on_mount(self) -> None:
        """Focus the input when mounted"""
        metar_input = self.query_one("#metar-input", Input)
        metar_input.focus()
    
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
    
    def _colorize_taf(self, taf: str) -> str:
        """
        Colorize the TAF entry applicable to the current Zulu time.
        
        Args:
            taf: Raw TAF string
            
        Returns:
            TAF string with rich text markup for the current period
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
                
                # Check if current time is within this period
                if valid_from and valid_to and valid_from <= current_time <= valid_to:
                    # Find where the conditions start (after the time period)
                    period_end = header_match.end()
                    prefix = line[:period_end]
                    conditions = line[period_end:]
                    colorized_lines.append(f"{prefix}[bold yellow]{conditions}[/bold yellow]")
                else:
                    colorized_lines.append(line)
                continue
            
            # Check for FM (FROM) groups: FM DDHHMM
            fm_match = re.search(r'\s+(FM\d{6})\s+', line)
            if fm_match:
                fm_time_str = fm_match.group(1)[2:]  # Remove 'FM' prefix
                fm_time = self._parse_taf_time(fm_time_str, current_month, current_year)
                
                if fm_time:
                    # FM periods are valid from their time until the next FM or end of TAF
                    # For now, highlight if current time >= FM time (next FM check would require full TAF parsing)
                    # Simple heuristic: highlight if FM time is in the past but within 24 hours
                    time_diff = (current_time - fm_time).total_seconds()
                    if 0 <= time_diff <= 86400:  # Within 24 hours from FM time
                        # Highlight the conditions part (after FM time)
                        fm_end = fm_match.end()
                        prefix = line[:fm_end]
                        conditions = line[fm_end:]
                        colorized_lines.append(f"{prefix}[bold yellow]{conditions}[/bold yellow]")
                    else:
                        colorized_lines.append(line)
                else:
                    colorized_lines.append(line)
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
                
                # Check if current time is within this period
                if valid_from and valid_to and valid_from <= current_time <= valid_to:
                    # Highlight the conditions part
                    group_end = tempo_becmg_match.end()
                    prefix = line[:group_end]
                    conditions = line[group_end:]
                    colorized_lines.append(f"{prefix}[bold yellow]{conditions}[/bold yellow]")
                else:
                    colorized_lines.append(line)
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
            return
        
        # Fetch full METAR and TAF
        metar = get_metar(icao)
        taf = get_taf(icao)
        
        result_widget = self.query_one("#metar-result", Static)
        
        # Get pretty name if available
        pretty_name = config.DISAMBIGUATOR.get_pretty_name(icao) if config.DISAMBIGUATOR else icao
        
        # Build the display string
        result_lines = [f"{pretty_name} ({icao})", ""]
        
        # Add METAR
        if metar:
            result_lines.append(metar)
        else:
            result_lines.append("METAR: No data available")
        
        result_lines.append("")  # Add blank line between METAR and TAF
        
        # Add TAF with colorization
        if taf:
            colorized_taf = self._colorize_taf(taf)
            result_lines.append(colorized_taf)
        else:
            result_lines.append("TAF: No data available")
        
        result_widget.update("\n".join(result_lines))
    
    def action_close(self) -> None:
        """Close the modal"""
        self.dismiss()