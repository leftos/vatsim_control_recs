"""A refresh never overlaps or cancels another, and its state is reported.

These drive a real ``VATSIMControlApp`` under Textual's headless pilot on a
hand-built airport/grouping fixture. Only the analysis boundary is replaced:
the app's ``fetch_data_async`` and the VATSIM feed download that both the
refresh worker and the Go To cache warm-up call.
"""

import asyncio
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from textual.css.query import NoMatches
from textual.pilot import Pilot
from textual.widgets import Static

from backend.core.models import AirportStats, GroupingStats
from ui.app import VATSIMControlApp
from ui.modals.command_palette import COMMANDS

AIRPORT_ROWS = [
    AirportStats(
        icao="KSFO",
        name="San Francisco Intl",
        wind="",
        altimeter="",
        total=1,
        departures=1,
        arrivals=0,
        arrivals_all=0,
        next_eta="",
        staffed="N/A",
    ),
    AirportStats(
        icao="KOAK",
        name="Oakland Metro",
        wind="",
        altimeter="",
        total=1,
        departures=0,
        arrivals=1,
        arrivals_all=1,
        next_eta="",
        staffed="N/A",
    ),
]

GROUPING_ROWS = [
    GroupingStats(
        name="Bay Area",
        total=2,
        departures=1,
        arrivals=1,
        arrivals_all=1,
        next_eta="",
        staffed="N/A",
    )
]

ARGS = SimpleNamespace(
    include_all_staffed=False,
    hide_wind=False,
    include_all_arriving=False,
    disable_animations=True,
    max_eta_hours=1.0,
    groupings=None,
    refresh_interval=0,
)


class FakeFetch:
    """The analysis boundary: counts calls, blocks, records a cancellation.

    Every call blocks on ``release`` until the test lets it through, so the
    test controls exactly how long an analysis stays in flight.
    """

    def __init__(
        self, *, fail_calls: set[int] | None = None, always_fail: bool = False
    ) -> None:
        self.calls = 0
        self.cancelled = 0
        self.release = asyncio.Event()
        self._fail_calls = fail_calls or set()
        self._always_fail = always_fail

    async def __call__(self) -> tuple[list[Any], list[Any], int]:
        self.calls += 1
        if self._always_fail or self.calls in self._fail_calls:
            raise RuntimeError("analysis failed")
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return list(AIRPORT_ROWS), list(GROUPING_ROWS), len(AIRPORT_ROWS)


RunningApp = tuple[VATSIMControlApp, Pilot]


@asynccontextmanager
async def running_app(monkeypatch: pytest.MonkeyPatch, fetch: FakeFetch):
    """Mount the real app headless with the analysis boundary replaced."""
    from backend.data import vatsim_api

    monkeypatch.setattr(vatsim_api, "download_vatsim_data", lambda: {"pilots": []})

    app = VATSIMControlApp(
        airport_data=[],
        groupings_data=[],
        total_flights=0,
        args=ARGS,
        airport_allowlist=[],
    )
    app.idle_timeout = 0  # never auto-pause: the idle check always passes
    app.fetch_data_async = fetch

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        yield app, pilot


async def _wait_until(
    pilot: Pilot,
    predicate: Callable[[], bool],
    message: str,
    timeout: float = 10.0,
) -> None:
    """Let the app run until ``predicate`` holds, failing the test if it never does."""
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, message
        await pilot.pause()


def _status_text(app: VATSIMControlApp) -> str:
    return str(app.query_one("#status-bar", Static).content)


async def test_timer_does_not_start_a_second_refresh_while_one_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = FakeFetch()
    async with running_app(monkeypatch, fetch) as (app, pilot):
        app.action_refresh()
        await _wait_until(
            pilot, lambda: fetch.calls == 1, "the first refresh never started"
        )

        # The timer ticks due while the analysis is still running.
        for _ in range(3):
            app.auto_refresh_callback()
            await pilot.pause()

        assert fetch.calls == 1, (
            "the timer started a second analysis while one was running"
        )
        assert fetch.cancelled == 0, "the timer cancelled the running analysis"


async def test_refresh_lands_after_a_long_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = FakeFetch()
    async with running_app(monkeypatch, fetch) as (app, pilot):
        app.action_refresh()
        await _wait_until(pilot, lambda: fetch.calls == 1, "the refresh never started")
        await asyncio.sleep(0.05)
        blocked_at = datetime.now(timezone.utc)
        assert app.airport_data == [], "the tables updated before the analysis finished"

        fetch.release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.airport_data == AIRPORT_ROWS
        assert app.last_refresh_time > blocked_at, (
            "'Last refresh' still counts from the start of the analysis, not the landing"
        )


async def test_status_bar_shows_refreshing_while_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = FakeFetch()
    async with running_app(monkeypatch, fetch) as (app, pilot):
        app.action_refresh()
        await _wait_until(pilot, lambda: fetch.calls == 1, "the refresh never started")
        await pilot.pause()

        while_running = _status_text(app)
        assert " · Refreshing…" in while_running
        assert while_running.split("|")[1].strip().endswith("ago · Refreshing…"), (
            while_running
        )

        fetch.release.set()
        await app.workers.wait_for_complete()
        await _wait_until(
            pilot,
            lambda: "Refreshing" not in _status_text(app),
            "the status bar still says Refreshing after the refresh landed",
        )


async def test_app_request_during_refresh_queues_one_follow_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = FakeFetch()
    async with running_app(monkeypatch, fetch) as (app, pilot):
        app.action_refresh()
        await _wait_until(pilot, lambda: fetch.calls == 1, "the refresh never started")

        for _ in range(3):
            app.action_refresh()
        await pilot.pause()
        assert fetch.calls == 1, "a request during a run started its own analysis"

        fetch.release.set()
        await _wait_until(
            pilot, lambda: fetch.calls == 2, "the queued follow-up never started"
        )
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert fetch.calls == 2, "three requests queued more than one follow-up"
        assert fetch.cancelled == 0


async def test_failed_analysis_does_not_stop_refreshes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = FakeFetch(fail_calls={1})
    async with running_app(monkeypatch, fetch) as (app, pilot):
        app.action_refresh()
        await _wait_until(pilot, lambda: fetch.calls == 1, "the refresh never started")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.airport_data == [], "a failed analysis still changed the tables"

        app.auto_refresh_callback()  # the next timer tick
        await _wait_until(
            pilot, lambda: fetch.calls == 2, "no refresh started after the failed one"
        )
        await asyncio.sleep(0.05)
        blocked_at = datetime.now(timezone.utc)

        fetch.release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.airport_data == AIRPORT_ROWS, (
            "the refresh after the failure never landed"
        )
        assert app.last_refresh_time > blocked_at


async def test_ctrl_r_is_not_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = FakeFetch()
    async with running_app(monkeypatch, fetch) as (app, _pilot):
        assert "ctrl+r" not in app._bindings.key_to_bindings
        assert all(command["action"] != "refresh" for command in COMMANDS)


async def test_status_bar_update_after_teardown_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = FakeFetch()
    async with running_app(monkeypatch, fetch) as (app, pilot):
        app.query_one("#status-bar", Static).remove()
        await pilot.pause()
        with pytest.raises(NoMatches):
            app.query_one("#status-bar", Static)

        app.update_time_displays()  # must not raise


async def test_failed_analysis_retries_only_after_the_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = FakeFetch(always_fail=True)
    async with running_app(monkeypatch, fetch) as (app, pilot):
        app.refresh_interval = 30.0
        # As after a run of failures: the last landing (and so the interval's own
        # clock as it stood before this fix) is well in the past
        stale = datetime.now(timezone.utc) - timedelta(seconds=60)
        app.last_refresh_time = stale
        app.last_refresh_started_at = stale

        app.action_refresh()
        await _wait_until(
            pilot, lambda: fetch.calls == 1, "the first refresh never started"
        )
        await app.workers.wait_for_complete()
        await pilot.pause()

        # A tick just after the failure is inside the interval: no retry yet
        app.auto_refresh_callback()
        await pilot.pause()
        assert fetch.calls == 1, (
            "a failed analysis retried before refresh_interval had passed"
        )

        # Once the interval has passed the next tick retries, exactly once
        app.last_refresh_started_at = datetime.now(timezone.utc) - timedelta(seconds=60)
        app.auto_refresh_callback()
        await _wait_until(
            pilot, lambda: fetch.calls == 2, "no retry after the interval passed"
        )
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert fetch.calls == 2, "the retry after the interval did not run exactly once"
