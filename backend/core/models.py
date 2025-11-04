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
            include_arrivals_all: Whether to include the arrivals_all column
        
        Returns:
            Tuple in format: (ICAO, NAME, WIND, TOTAL, DEP, ARR, [ARR(all)], NEXT ETA, STAFFED)
        """
        base = (
            self.icao,
            self.name,
            self.wind,
            str(self.total),
            str(self.departures).rjust(3),
            str(self.arrivals).rjust(3),
        )
        
        if include_arrivals_all:
            return base + (str(self.arrivals_all).rjust(3), self.next_eta, self.staffed)
        else:
            return base + (self.next_eta, self.staffed)
    
    def to_tuple_without_wind(self, include_arrivals_all: bool = True) -> tuple:
        """
        Convert to tuple format for display without wind column.
        
        Args:
            include_arrivals_all: Whether to include the arrivals_all column
        
        Returns:
            Tuple in format: (ICAO, NAME, TOTAL, DEP, ARR, [ARR(all)], NEXT ETA, STAFFED)
        """
        base = (
            self.icao,
            self.name,
            str(self.total),
            str(self.departures).rjust(3),
            str(self.arrivals).rjust(3),
        )
        
        if include_arrivals_all:
            return base + (str(self.arrivals_all).rjust(3), self.next_eta, self.staffed)
        else:
            return base + (self.next_eta, self.staffed)
    
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
            include_arrivals_all: Whether to include the arrivals_all column
        
        Returns:
            Tuple in format: (NAME, TOTAL, DEP, ARR, [ARR(all)], NEXT ETA, STAFFED)
        """
        base = (
            self.name,
            str(self.total),
            str(self.departures),
            str(self.arrivals),
        )
        
        if include_arrivals_all:
            return base + (str(self.arrivals_all), self.next_eta, self.staffed)
        else:
            return base + (self.next_eta, self.staffed)