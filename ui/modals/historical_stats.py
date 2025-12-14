"""Historical Flight Statistics Modal Screen - View traffic patterns to/from airports"""

import asyncio
from textual.screen import ModalScreen
from textual.widgets import Static, Input, DataTable
from textual.containers import Container
from textual.binding import Binding
from textual.app import ComposeResult

from backend.data.statsim_api import (
    fetch_flights_from_origin,
    fetch_flights_to_destination,
    STATSIM_DEFAULT_DAYS_BACK,
    STATSIM_MAX_DAYS_PER_QUERY
)
from ui import config


class HistoricalStatsScreen(ModalScreen):
    """Modal screen for viewing historical flight statistics between airports"""

    CSS = """
    HistoricalStatsScreen {
        align: center middle;
    }

    #stats-container {
        width: 100;
        height: auto;
        max-height: 85%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #stats-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #stats-input-container {
        height: auto;
        margin-bottom: 1;
    }

    #stats-input {
        width: 100%;
    }

    #stats-status {
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
        height: auto;
    }

    #stats-table-container {
        height: auto;
        max-height: 60%;
        overflow-y: auto;
    }

    #stats-table {
        height: auto;
        max-height: 100%;
        width: 100%;
    }

    #stats-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("enter", "search", "Search", priority=True),
        Binding("c", "copy_results", "Copy", priority=True),
    ]

    def __init__(self, tracked_airports: list, disambiguator=None):
        """
        Initialize the historical stats modal.

        Args:
            tracked_airports: List of currently tracked airport ICAOs
            disambiguator: Airport name disambiguator for pretty names
        """
        super().__init__()
        self.tracked_airports = set(tracked_airports) if tracked_airports else set()
        self.disambiguator = disambiguator
        self._search_task: asyncio.Task | None = None
        self._search_cancelled = False
        self._results: dict = {}
        self._query_airports: list = []  # Store the airports that were queried

    def compose(self) -> ComposeResult:
        with Container(id="stats-container"):
            yield Static(f"Historical Flight Statistics ({STATSIM_DEFAULT_DAYS_BACK} days)", id="stats-title")
            with Container(id="stats-input-container"):
                yield Input(
                    placeholder="Enter airports (space-separated, e.g., KLAX KJFK KORD)",
                    id="stats-input"
                )
            yield Static("Enter airports to see which tracked airports have the most traffic to/from them", id="stats-status", markup=True)
            with Container(id="stats-table-container"):
                table = DataTable(id="stats-table")
                table.cursor_type = "row"
                yield table
            yield Static("Enter: Search | C: Copy results | Escape: Close", id="stats-hint")

    def on_mount(self) -> None:
        """Focus the input when mounted and set up the table"""
        # Set up table columns
        table = self.query_one("#stats-table", DataTable)
        table.add_column("ICAO", width=6)
        table.add_column("Name", width=35)
        table.add_column("Deps", width=8)
        table.add_column("Arrs", width=8)
        table.add_column("Total", width=8)

        # Focus the input
        stats_input = self.query_one("#stats-input", Input)
        stats_input.focus()

    def on_unmount(self) -> None:
        """Cancel any pending search when modal is closed"""
        self._search_cancelled = True
        if self._search_task and not self._search_task.done():
            self._search_task.cancel()

    def action_search(self) -> None:
        """Start searching for historical stats"""
        # Cancel any existing search
        self._search_cancelled = True
        if self._search_task and not self._search_task.done():
            self._search_task.cancel()

        self._search_cancelled = False
        self._search_task = asyncio.create_task(self._search_async())

    async def _search_async(self) -> None:
        """Async search that updates UI progressively as results come in"""
        stats_input = self.query_one("#stats-input", Input)
        status_widget = self.query_one("#stats-status", Static)
        table = self.query_one("#stats-table", DataTable)

        # Parse input
        input_text = stats_input.value.strip().upper()
        if not input_text:
            status_widget.update("[yellow]Please enter at least one airport ICAO code[/yellow]")
            return

        # Split by whitespace and filter valid ICAOs
        query_airports = [icao.strip() for icao in input_text.split() if icao.strip()]

        if not query_airports:
            status_widget.update("[yellow]Please enter at least one airport ICAO code[/yellow]")
            return

        # Validate airports exist
        valid_airports = []
        invalid_airports = []
        for icao in query_airports:
            if config.UNIFIED_AIRPORT_DATA and icao in config.UNIFIED_AIRPORT_DATA:
                valid_airports.append(icao)
            else:
                invalid_airports.append(icao)

        if invalid_airports and not valid_airports:
            status_widget.update(f"[red]Unknown airports: {', '.join(invalid_airports)}[/red]")
            return

        if not self.tracked_airports:
            status_widget.update("[yellow]No airports are currently being tracked[/yellow]")
            return

        # Calculate number of chunks needed to cover STATSIM_DEFAULT_DAYS_BACK
        num_chunks = (STATSIM_DEFAULT_DAYS_BACK + STATSIM_MAX_DAYS_PER_QUERY - 1) // STATSIM_MAX_DAYS_PER_QUERY

        # Show initial status (2 queries per airport per chunk: origin + destination)
        total_queries = len(valid_airports) * 2 * num_chunks
        warning = ""
        if invalid_airports:
            warning = f" [yellow](skipping unknown: {', '.join(invalid_airports)})[/yellow]"
        status_widget.update(f"Fetching historical data... 0/{total_queries} queries{warning}")

        # Clear existing results and store query airports
        table.clear()
        self._results = {}
        self._query_airports = valid_airports

        # Build list of all queries: (query_type, icao, days_offset)
        # Each airport needs origin + destination queries for each time chunk
        queries = []
        for icao in valid_airports:
            for chunk in range(num_chunks):
                days_offset = chunk * STATSIM_MAX_DAYS_PER_QUERY
                queries.append(("origin", icao, days_offset))
                queries.append(("destination", icao, days_offset))

        # Concurrency control with adaptive backoff
        max_concurrent = 4
        current_concurrent = max_concurrent
        error_count = 0
        error_threshold = 2  # Back off after this many errors

        loop = asyncio.get_event_loop()
        results = {}
        completed = 0
        pending_tasks = set()
        query_index = 0

        def process_flights(query_type: str, flights: list) -> None:
            """Process flight results and update results dict."""
            nonlocal results
            for flight in flights:
                if query_type == "origin":
                    # Flights FROM query airport - check destination
                    dest = flight.get('destination', '').upper() if flight.get('destination') else ''
                    if dest and dest in self.tracked_airports:
                        if dest not in results:
                            results[dest] = {"departures": 0, "arrivals": 0, "total": 0}
                        results[dest]["arrivals"] += 1
                        results[dest]["total"] += 1
                else:
                    # Flights TO query airport - check origin
                    origin = flight.get('departure', '').upper() if flight.get('departure') else ''
                    if origin and origin in self.tracked_airports:
                        if origin not in results:
                            results[origin] = {"departures": 0, "arrivals": 0, "total": 0}
                        results[origin]["departures"] += 1
                        results[origin]["total"] += 1

        async def execute_query(query_type: str, icao: str, days_offset: int) -> tuple:
            """Execute a single API query for a specific time chunk."""
            if query_type == "origin":
                flights = await loop.run_in_executor(
                    None, fetch_flights_from_origin, icao, STATSIM_MAX_DAYS_PER_QUERY, days_offset
                )
            else:
                flights = await loop.run_in_executor(
                    None, fetch_flights_to_destination, icao, STATSIM_MAX_DAYS_PER_QUERY, days_offset
                )
            return (query_type, icao, flights)

        try:
            while query_index < len(queries) or pending_tasks:
                if self._search_cancelled:
                    # Cancel all pending tasks
                    for task in pending_tasks:
                        task.cancel()
                    return

                # Start new tasks up to current concurrency limit
                while len(pending_tasks) < current_concurrent and query_index < len(queries):
                    query_type, icao, days_offset = queries[query_index]
                    task = asyncio.create_task(execute_query(query_type, icao, days_offset))
                    pending_tasks.add(task)
                    query_index += 1

                if not pending_tasks:
                    break

                # Update status
                status_widget.update(
                    f"Fetching historical data... {completed}/{total_queries} "
                    f"[dim](concurrent: {current_concurrent})[/dim]{warning}"
                )

                # Wait for at least one task to complete
                done, pending_tasks = await asyncio.wait(
                    pending_tasks, return_when=asyncio.FIRST_COMPLETED
                )

                # Process completed tasks
                for task in done:
                    try:
                        query_type, icao, flights = task.result()
                        process_flights(query_type, flights)
                        completed += 1

                        # Reset error count on success if we're throttled
                        if current_concurrent < max_concurrent and error_count > 0:
                            error_count = max(0, error_count - 1)
                            if error_count == 0:
                                current_concurrent = min(current_concurrent + 1, max_concurrent)

                    except Exception:
                        completed += 1
                        error_count += 1

                        # Back off if too many errors
                        if error_count >= error_threshold and current_concurrent > 1:
                            current_concurrent = 1
                            status_widget.update(
                                f"[yellow]Errors detected, reducing concurrency...[/yellow] "
                                f"{completed}/{total_queries}{warning}"
                            )

                # Update table with current results
                self._results = results.copy()
                self._update_table(results, completed, total_queries, warning)

            # Final update
            if not self._search_cancelled:
                if results:
                    status_widget.update(
                        f"[green]Complete! Found {len(results)} tracked airports with "
                        f"traffic to/from {', '.join(valid_airports)}[/green]{warning}"
                    )
                else:
                    status_widget.update(
                        f"[yellow]No flights found between tracked airports and "
                        f"{', '.join(valid_airports)} in the last {STATSIM_DEFAULT_DAYS_BACK} days[/yellow]{warning}"
                    )

        except asyncio.CancelledError:
            for task in pending_tasks:
                task.cancel()
            status_widget.update("[dim]Search cancelled[/dim]")
        except Exception as e:
            status_widget.update(f"[red]Error: {e}[/red]")

    def _update_table(self, results: dict, completed: int, total: int, warning: str = "") -> None:
        """Update the results table with current data"""
        table = self.query_one("#stats-table", DataTable)
        status_widget = self.query_one("#stats-status", Static)

        # Update status
        if completed < total:
            status_widget.update(f"Fetching historical data... {completed}/{total} queries{warning}")

        # Clear and repopulate table
        table.clear()

        if not results:
            return

        # Sort by total descending
        sorted_results = sorted(
            results.items(),
            key=lambda x: x[1]["total"],
            reverse=True
        )

        # Add rows
        for icao, stats in sorted_results:
            # Get pretty name
            if self.disambiguator:
                name = self.disambiguator.get_pretty_name(icao)
            else:
                name = icao

            # Truncate name if too long
            if len(name) > 35:
                name = name[:32] + "..."

            table.add_row(
                icao,
                name,
                str(stats["departures"]),
                str(stats["arrivals"]),
                str(stats["total"])
            )

    def action_close(self) -> None:
        """Close the modal"""
        self._search_cancelled = True
        self.dismiss()

    def action_copy_results(self) -> None:
        """Copy the results table to clipboard as formatted text"""
        if not self._results:
            self.notify("No results to copy", severity="warning")
            return

        # Sort results by total descending
        sorted_results = sorted(
            self._results.items(),
            key=lambda x: x[1]["total"],
            reverse=True
        )

        # Build formatted table
        lines = []
        lines.append(f"Historical Flight Statistics ({STATSIM_DEFAULT_DAYS_BACK} days)")
        if self._query_airports:
            lines.append(f"Query airports: {', '.join(self._query_airports)}")
        lines.append("")

        # Header
        lines.append(f"{'ICAO':<6} {'Name':<35} {'Deps':>8} {'Arrs':>8} {'Total':>8}")
        lines.append("-" * 69)

        # Data rows
        for icao, stats in sorted_results:
            if self.disambiguator:
                name = self.disambiguator.get_pretty_name(icao)
            else:
                name = icao

            # Truncate name if too long
            if len(name) > 35:
                name = name[:32] + "..."

            lines.append(
                f"{icao:<6} {name:<35} {stats['departures']:>8} "
                f"{stats['arrivals']:>8} {stats['total']:>8}"
            )

        # Footer with totals
        total_deps = sum(s["departures"] for s in self._results.values())
        total_arrs = sum(s["arrivals"] for s in self._results.values())
        total_all = sum(s["total"] for s in self._results.values())
        lines.append("-" * 69)
        lines.append(f"{'TOTAL':<6} {'':<35} {total_deps:>8} {total_arrs:>8} {total_all:>8}")

        # Copy to clipboard
        text = "\n".join(lines)
        self.app.copy_to_clipboard(text)
        self.notify(f"Copied {len(sorted_results)} rows to clipboard", severity="information")
