"""
Data models for VATSIM airport and grouping statistics.
Provides structured data classes instead of fragile tuple-based access.
"""

from dataclasses import dataclass


@dataclass
class AirportStats:
    """Statistics for a single airport."""

    icao: str
    name: str
    wind: str
    altimeter: str
    total: int
    departures: int
    arrivals: int
    arrivals_all: int
    next_eta: str
    staffed: str

    def _format_total_display(self, include_arrivals_all: bool) -> str:
        """Format the TOTAL column display."""
        total_all = self.departures + self.arrivals_all
        if include_arrivals_all and self.total != total_all:
            return f"{self.total}/{total_all}"
        return str(self.total)

    def _format_arrivals_display(self, include_arrivals_all: bool) -> str:
        """Format the ARR column display."""
        if include_arrivals_all and self.arrivals != self.arrivals_all:
            return f"{self.arrivals}/{self.arrivals_all}".rjust(7)
        return str(self.arrivals).rjust(3)

    def to_tuple_with_wind(self, include_arrivals_all: bool = True) -> tuple:
        """
        Convert to tuple format for display with wind column.

        Args:
            include_arrivals_all: Whether to include the arrivals_all column (now combined with arrivals)

        Returns:
            Tuple in format: (ICAO, NAME, WIND, ALT, TOTAL, DEP, ARR, NEXT ETA, STAFFED)
            When include_arrivals_all=True: TOTAL shows "dep+arr<xH / dep+arr_all" and ARR shows "arr<xH / arr_all"
        """
        return (
            self.icao,
            self.name,
            self.wind,
            self.altimeter,
            self._format_total_display(include_arrivals_all),
            str(self.departures).rjust(3),
            self._format_arrivals_display(include_arrivals_all),
            self.next_eta,
            self.staffed,
        )

    def to_tuple_without_wind(self, include_arrivals_all: bool = True) -> tuple:
        """
        Convert to tuple format for display without wind column.

        Args:
            include_arrivals_all: Whether to include the arrivals_all column (now combined with arrivals)

        Returns:
            Tuple in format: (ICAO, NAME, ALT, TOTAL, DEP, ARR, NEXT ETA, STAFFED)
            When include_arrivals_all=True: TOTAL shows "dep+arr<xH / dep+arr_all" and ARR shows "arr<xH / arr_all"
        """
        return (
            self.icao,
            self.name,
            self.altimeter,
            self._format_total_display(include_arrivals_all),
            str(self.departures).rjust(3),
            self._format_arrivals_display(include_arrivals_all),
            self.next_eta,
            self.staffed,
        )

    def to_tuple(
        self, hide_wind: bool = False, include_arrivals_all: bool = True
    ) -> tuple:
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

    def _format_total_display(self, include_arrivals_all: bool) -> str:
        """Format the TOTAL column display."""
        total_all = self.departures + self.arrivals_all
        if include_arrivals_all and self.total != total_all:
            return f"{self.total}/{total_all}"
        return str(self.total)

    def _format_arrivals_display(self, include_arrivals_all: bool) -> str:
        """Format the ARR column display."""
        if include_arrivals_all and self.arrivals != self.arrivals_all:
            return f"{self.arrivals}/{self.arrivals_all}".rjust(7)
        return str(self.arrivals).rjust(3)

    def to_tuple(self, include_arrivals_all: bool = True) -> tuple:
        """
        Convert to tuple format for display.

        Args:
            include_arrivals_all: Whether to include the arrivals_all column (now combined with arrivals)

        Returns:
            Tuple in format: (NAME, TOTAL, DEP, ARR, NEXT ETA, STAFFED)
            When include_arrivals_all=True: TOTAL shows "dep+arr<xH / dep+arr_all" and ARR shows "arr<xH / arr_all"
        """
        return (
            self.name,
            self._format_total_display(include_arrivals_all),
            str(self.departures),
            self._format_arrivals_display(include_arrivals_all),
            self.next_eta,
            self.staffed,
        )
