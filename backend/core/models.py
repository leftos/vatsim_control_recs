"""
Data models for VATSIM airport and grouping statistics.
Provides structured data classes instead of fragile tuple-based access.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class AirportStats:
    """Statistics for a single airport."""
    icao: str
    name: str
    wind: str
    total: int
    departures: int
    arrivals: int
    arrivals_all: int
    next_eta: str
    staffed: str
    
    def to_tuple_with_wind(self, include_arrivals_all: bool = True) -> tuple:
        """
        Convert to tuple format for display with wind column.
        
        Args:
            include_arrivals_all: Whether to include the arrivals_all column (now combined with arrivals)
        
        Returns:
            Tuple in format: (ICAO, NAME, WIND, TOTAL, DEP, ARR, NEXT ETA, STAFFED)
            When include_arrivals_all=True: TOTAL shows "dep+arr<xH / dep+arr_all" and ARR shows "arr<xH / arr_all"
        """
        # Calculate total with all arrivals
        total_all = self.departures + self.arrivals_all
        
        # Format TOTAL column: "current / total_all" (or just current if they're the same)
        if include_arrivals_all and self.total != total_all:
            total_display = f"{self.total}/{total_all}"
        else:
            total_display = str(self.total)
        
        # Format ARR column: "arrivals / arrivals_all" (or just arrivals if they're the same)
        if include_arrivals_all and self.arrivals != self.arrivals_all:
            arr_display = f"{self.arrivals}/{self.arrivals_all}".rjust(7)
        else:
            arr_display = str(self.arrivals).rjust(3)
        
        return (
            self.icao,
            self.name,
            self.wind,
            total_display,
            str(self.departures).rjust(3),
            arr_display,
            self.next_eta,
            self.staffed
        )
    
    def to_tuple_without_wind(self, include_arrivals_all: bool = True) -> tuple:
        """
        Convert to tuple format for display without wind column.
        
        Args:
            include_arrivals_all: Whether to include the arrivals_all column (now combined with arrivals)
        
        Returns:
            Tuple in format: (ICAO, NAME, TOTAL, DEP, ARR, NEXT ETA, STAFFED)
            When include_arrivals_all=True: TOTAL shows "dep+arr<xH / dep+arr_all" and ARR shows "arr<xH / arr_all"
        """
        # Calculate total with all arrivals
        total_all = self.departures + self.arrivals_all
        
        # Format TOTAL column: "current / total_all" (or just current if they're the same)
        if include_arrivals_all and self.total != total_all:
            total_display = f"{self.total}/{total_all}"
        else:
            total_display = str(self.total)
        
        # Format ARR column: "arrivals / arrivals_all" (or just arrivals if they're the same)
        if include_arrivals_all and self.arrivals != self.arrivals_all:
            arr_display = f"{self.arrivals}/{self.arrivals_all}".rjust(7)
        else:
            arr_display = str(self.arrivals).rjust(3)
        
        return (
            self.icao,
            self.name,
            total_display,
            str(self.departures).rjust(3),
            arr_display,
            self.next_eta,
            self.staffed
        )
    
    def to_tuple(self, hide_wind: bool = False, include_arrivals_all: bool = True) -> tuple:
        """
        Convert to tuple format for display.
        
        Args:
            hide_wind: Whether to hide the wind column
            include_arrivals_all: Whether to include the arrivals_all column
        
        Returns:
            Tuple for display in the appropriate format
        """
        if hide_wind:
            return self.to_tuple_without_wind(include_arrivals_all)
        else:
            return self.to_tuple_with_wind(include_arrivals_all)


@dataclass
class GroupingStats:
    """Statistics for an airport grouping."""
    name: str
    total: int
    departures: int
    arrivals: int
    arrivals_all: int
    next_eta: str
    staffed: str
    
    def to_tuple(self, include_arrivals_all: bool = True) -> tuple:
        """
        Convert to tuple format for display.
        
        Args:
            include_arrivals_all: Whether to include the arrivals_all column (now combined with arrivals)
        
        Returns:
            Tuple in format: (NAME, TOTAL, DEP, ARR, NEXT ETA, STAFFED)
            When include_arrivals_all=True: TOTAL shows "dep+arr<xH / dep+arr_all" and ARR shows "arr<xH / arr_all"
        """
        # Calculate total with all arrivals
        total_all = self.departures + self.arrivals_all
        
        # Format TOTAL column: "current / total_all" (or just current if they're the same)
        if include_arrivals_all and self.total != total_all:
            total_display = f"{self.total}/{total_all}"
        else:
            total_display = str(self.total)
        
        # Format ARR column: "arrivals / arrivals_all" (or just arrivals if they're the same)
        if include_arrivals_all and self.arrivals != self.arrivals_all:
            arr_display = f"{self.arrivals}/{self.arrivals_all}".rjust(7)
        else:
            arr_display = str(self.arrivals).rjust(3)
        
        return (
            self.name,
            total_display,
            str(self.departures),
            arr_display,
            self.next_eta,
            self.staffed
        )