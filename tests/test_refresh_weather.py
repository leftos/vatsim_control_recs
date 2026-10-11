"""WIND and ALT arrive per weather box, after the traffic columns have landed.

These drive the real ``VATSIMControlApp`` and the real analysis under Textual's
headless pilot on a three-airport fixture. The VATSIM feed is replaced, and the
network under the weather code is a ``FakeNet`` whose bbox requests are held per
region (west: KSFO and KOAK, east: KJFK) until the test releases them.
"""

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from textual.pilot import Pilot

from backend.cache import manager
from backend.core import analysis, spatial
from backend.data import vatsim_api, weather
from tests.test_weather_cache import FakeNet, _parse_bbox
from ui import config
from ui.app import VATSIMControlApp
from widgets.split_flap_datatable import SplitFlapDataTable

COORDINATES = {
    "KSFO": (37.62, -122.37),
    "KOAK": (37.72, -122.22),
    "KJFK": (40.64, -73.78),
}

UNIFIED = {
    icao: {"latitude": lat, "longitude": lon, "country": "US", "tower_type": "ATCT"}
    for icao, (lat, lon) in COORDINATES.items()
}

WEATHER = {
    "KSFO": ("31012KT", "A3012"),
    "KOAK": ("30008KT", "A3011"),
    "KJFK": ("22010KT", "A2998"),
}

ARGS = SimpleNamespace(
    include_all_staffed=False,
    hide_wind=False,
    include_all_arriving=False,
    disable_animations=True,
    max_eta_hours=1.0,
    groupings=None,
    refresh_interval=3600,
)

HIDE_WIND_ARGS = SimpleNamespace(**{**vars(ARGS), "hide_wind": True})

WAIT = 10.0


def _metar(icao: str, wind: str, altimeter: str) -> str:
    return f"{icao} 101251Z {wind} 10SM FEW250 18/06 {altimeter}"


class Gate:
    """Holds one region's bbox requests until released; can fail them instead."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.fail = False
        self.requests = 0

    def block(self) -> None:
        self.entered.clear()
        self.release.clear()

    def open(self) -> None:
        self.release.set()

    def pass_through(self) -> None:
        self.requests += 1
        self.entered.set()
        # Longer than any wait in a test, so a test fails before a held request lets go
        self.release.wait(3 * WAIT)
        if self.fail:
            raise TimeoutError("bbox request timed out")


class RegionNet(FakeNet):
    """A ``FakeNet`` whose bbox answers come from the fixture airports, gated per region."""

    def __init__(self) -> None:
        super().__init__()
        self.stations = {}
        for icao, (wind, altimeter) in WEATHER.items():
            self.set_weather(icao, wind, altimeter)
        self.west = Gate()
        self.east = Gate()
        self.bbox = self._gated_bbox

    def set_weather(self, icao: str, wind: str, altimeter: str) -> None:
        lat, lon = COORDINATES[icao]
        self.stations[icao] = (lat, lon, _metar(icao, wind, altimeter))

    def _gated_bbox(self, url: str) -> bytes:
        _min_lat, min_lon, _max_lat, _max_lon = _parse_bbox(url)
        gate = self.west if min_lon < -100 else self.east
        gate.pass_through()
        return self._bbox_from_stations(url)

    def open_all(self) -> None:
        self.west.open()
        self.east.open()


def _pilot(callsign: str, departure: str) -> dict[str, Any]:
    """A pilot on the ground at ``departure``, filed to an untracked airport."""
    lat, lon = COORDINATES[departure]
    return {
        "callsign": callsign,
        "latitude": lat,
        "longitude": lon,
        "groundspeed": 0,
        "altitude": 10,
        "flight_plan": {"departure": departure, "arrival": "KLAX"},
    }


class Feed:
    """The VATSIM feed both the analysis and the app download; tests edit ``pilots``."""

    def __init__(self) -> None:
        self.pilots = [_pilot("AAL1", "KSFO")]

    def __call__(self) -> dict[str, Any]:
        return {"pilots": list(self.pilots), "controllers": [], "atis": []}


class StubNames:
    """The disambiguator's batch name lookup, without the airport database."""

    def get_full_names_batch(self, icaos: list[str]) -> dict[str, str]:
        return {icao: f"{icao} Airport" for icao in icaos}


def _clear_weather() -> None:
    manager.clear_metar_cache()
    weather.clear_weather_caches()
    weather.reset_rate_limit_state()


@pytest.fixture(autouse=True)
def clean_weather():
    _clear_weather()
    yield
    _clear_weather()


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch):
    fake = RegionNet()
    monkeypatch.setattr(weather.urllib.request, "urlopen", fake.urlopen)
    monkeypatch.setattr(spatial, "_AIRPORT_SPATIAL_INDEX", None)
    monkeypatch.setattr(config, "UNIFIED_AIRPORT_DATA", UNIFIED)
    monkeypatch.setattr(config, "DISAMBIGUATOR", StubNames())
    yield fake
    fake.open_all()


@pytest.fixture
def feed(monkeypatch: pytest.MonkeyPatch) -> Feed:
    fake = Feed()
    monkeypatch.setattr(analysis, "download_vatsim_data", fake)
    monkeypatch.setattr(vatsim_api, "download_vatsim_data", fake)
    return fake


@asynccontextmanager
async def weather_app(
    net: RegionNet,
    airport_data: list | None = None,
    args: SimpleNamespace = ARGS,
) -> AsyncIterator[tuple[VATSIMControlApp, Pilot]]:
    """Mount the real app headless; on the way out, release every request and let work finish.

    A test that already failed keeps its own failure: work that does not finish
    in time then only releases the requests again instead of raising over it.
    """
    app = VATSIMControlApp(
        airport_data=airport_data or [],
        groupings_data=[],
        total_flights=0,
        args=args,
        airport_allowlist=list(COORDINATES),
    )
    async with app.run_test(size=(160, 40)) as pilot:
        body_failed = False
        try:
            await pilot.pause()
            yield app, pilot
        except BaseException:
            body_failed = True
            raise
        finally:
            net.open_all()
            try:
                await asyncio.wait_for(app.workers.wait_for_complete(), WAIT)
            except TimeoutError:
                net.open_all()
                if not body_failed:
                    raise


async def _wait_until(
    pilot: Pilot, predicate: Callable[[], bool], message: str
) -> None:
    """Let the app run until ``predicate`` holds, failing the test if it never does."""
    deadline = time.monotonic() + WAIT
    while not predicate():
        assert time.monotonic() < deadline, message
        await pilot.pause()


def _row(app: VATSIMControlApp, icao: str) -> dict[str, str] | None:
    """The airports table's WIND, ALT and TOTAL cells for ``icao``, or None without a row."""
    table = app.query_one("#airports-table", SplitFlapDataTable)
    for index in range(table.row_count):
        cells = [str(cell).strip() for cell in table.get_row_at(index)]
        if cells[0] == icao:
            return {"wind": cells[2], "alt": cells[3], "total": cells[4]}
    return None


def _total(app: VATSIMControlApp, icao: str) -> str | None:
    row = _row(app, icao)
    return row["total"] if row else None


def _shows(app: VATSIMControlApp, icao: str, wind: str, altimeter: str) -> bool:
    row = _row(app, icao)
    return row is not None and (row["wind"], row["alt"]) == (wind, altimeter)


def _model(app: VATSIMControlApp, icao: str) -> Any:
    """The app's ``AirportStats`` for ``icao`` in the current refresh."""
    return next(row for row in app.original_airport_data if row.icao == icao)


def _shows_fixture_weather(app: VATSIMControlApp, icao: str) -> bool:
    return _shows(app, icao, *WEATHER[icao])


async def _first_refresh(
    app: VATSIMControlApp, pilot: Pilot, icaos: tuple[str, ...] = ("KSFO",)
) -> None:
    """Refresh with every box open and wait until WIND/ALT have landed and all work is done."""
    app.action_refresh()
    await _wait_until(
        pilot,
        lambda: all(_shows_fixture_weather(app, icao) for icao in icaos),
        "the first refresh never showed its WIND/ALT",
    )
    await app.workers.wait_for_complete()


async def test_traffic_lands_while_a_weather_box_is_blocked(
    net: RegionNet, feed: Feed
) -> None:
    net.west.block()
    async with weather_app(net) as (app, pilot):
        app.action_refresh()
        await _wait_until(
            pilot, net.west.entered.is_set, "the weather box was never requested"
        )
        await _wait_until(
            pilot,
            lambda: _row(app, "KSFO") is not None,
            "the traffic columns waited for the weather box",
        )

        stats = next(row for row in app.airport_data if row.icao == "KSFO")
        assert stats.total == 1
        assert _row(app, "KSFO") == {"wind": "", "alt": "", "total": "1"}

        net.west.open()
        await _wait_until(
            pilot,
            lambda: _shows_fixture_weather(app, "KSFO"),
            "WIND/ALT never arrived after the box returned",
        )


async def test_previous_weather_stays_until_its_box_returns(
    net: RegionNet, feed: Feed
) -> None:
    async with weather_app(net) as (app, pilot):
        await _first_refresh(app, pilot)

        _clear_weather()
        net.set_weather("KSFO", "09005KT", "A2990")
        feed.pilots += [_pilot("AAL2", "KSFO"), _pilot("AAL3", "KOAK")]
        net.west.block()
        app.action_refresh()
        await _wait_until(
            pilot,
            lambda: _total(app, "KSFO") == "2",
            "the second refresh's traffic waited for the weather box",
        )
        await _wait_until(
            pilot, net.west.entered.is_set, "the weather box was never requested"
        )

        assert _shows_fixture_weather(app, "KSFO"), (
            "KSFO lost its previous WIND/ALT before its box returned"
        )
        assert _shows(app, "KOAK", "", ""), (
            "an airport new this refresh showed WIND/ALT before its box returned"
        )

        net.west.open()
        await _wait_until(
            pilot,
            lambda: (
                _shows(app, "KSFO", "09005KT", "A2990")
                and _shows_fixture_weather(app, "KOAK")
            ),
            "the box's new WIND/ALT never replaced the previous values",
        )


async def test_two_regions_patch_as_each_box_returns(
    net: RegionNet, feed: Feed
) -> None:
    feed.pilots.append(_pilot("DAL1", "KJFK"))
    async with weather_app(net) as (app, pilot):
        await _first_refresh(app, pilot, ("KSFO", "KJFK"))

        _clear_weather()
        net.set_weather("KSFO", "09005KT", "A2990")
        net.set_weather("KJFK", "05015KT", "A2975")
        feed.pilots.append(_pilot("AAL2", "KSFO"))
        net.west.block()
        net.east.block()
        app.action_refresh()
        await _wait_until(
            pilot,
            lambda: _total(app, "KSFO") == "2",
            "the traffic waited for the weather boxes",
        )
        await _wait_until(
            pilot,
            lambda: net.west.entered.is_set() and net.east.entered.is_set(),
            "the two weather boxes were never both requested",
        )
        assert _shows_fixture_weather(app, "KSFO")
        assert _shows_fixture_weather(app, "KJFK")

        net.east.open()
        await _wait_until(
            pilot,
            lambda: _shows(app, "KJFK", "05015KT", "A2975"),
            "KJFK's WIND/ALT waited for the other region's box",
        )
        assert _shows_fixture_weather(app, "KSFO"), (
            "KSFO changed before its own box returned"
        )

        net.west.open()
        await _wait_until(
            pilot,
            lambda: _shows(app, "KSFO", "09005KT", "A2990"),
            "KSFO's WIND/ALT never arrived after its box returned",
        )


async def test_failed_box_keeps_previous_values(net: RegionNet, feed: Feed) -> None:
    async with weather_app(net) as (app, pilot):
        await _first_refresh(app, pilot)

        _clear_weather()
        net.set_weather("KSFO", "09005KT", "A2990")
        net.west.fail = True
        feed.pilots.append(_pilot("AAL2", "KSFO"))
        app.action_refresh()
        await _wait_until(
            pilot, lambda: _total(app, "KSFO") == "2", "the second refresh never landed"
        )
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert net.west.requests == 2
        assert _shows_fixture_weather(app, "KSFO"), (
            "a failed box blanked or replaced its airports' previous WIND/ALT"
        )
        row = _model(app, "KSFO")
        assert (row.wind, row.altimeter) == WEATHER["KSFO"]


async def test_airport_omitted_by_a_successful_box_goes_blank(
    net: RegionNet, feed: Feed
) -> None:
    async with weather_app(net) as (app, pilot):
        await _first_refresh(app, pilot)

        _clear_weather()
        del net.stations["KSFO"]
        feed.pilots.append(_pilot("AAL2", "KSFO"))
        app.action_refresh()
        await _wait_until(
            pilot, lambda: _total(app, "KSFO") == "2", "the second refresh never landed"
        )
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert net.west.requests == 2
        assert _shows(app, "KSFO", "", ""), (
            "an airport a successful box omitted kept a stale WIND/ALT"
        )


def test_fresh_boxes_arrive_in_the_first_callback(net: RegionNet) -> None:
    airports = ["KSFO", "KOAK", "KJFK"]
    weather.get_weather_for_airports_bbox(airports, UNIFIED)
    assert (net.west.requests, net.east.requests) == (1, 1)

    metar_cache, _blacklist = manager.get_metar_cache()
    metar_cache["KJFK"]["timestamp"] -= timedelta(seconds=61)
    old_kjfk = metar_cache["KJFK"]["metar"]
    net.set_weather("KJFK", "05015KT", "A2975")
    new_kjfk = net.stations["KJFK"][2]

    calls: list[tuple[set[str], bool, int, str]] = []

    def on_box(members: set[str], ok: bool) -> None:
        cached = metar_cache["KJFK"]["metar"]
        calls.append((set(members), ok, len(net.bbox_urls()), cached))

    weather.get_weather_for_airports_bbox(airports, UNIFIED, on_box=on_box)

    assert calls == [
        ({"KSFO", "KOAK"}, True, 2, old_kjfk),
        ({"KJFK"}, True, 3, new_kjfk),
    ]


async def test_refresh_mid_pass_runs_one_more_pass_and_no_duplicate_request(
    net: RegionNet, feed: Feed, monkeypatch: pytest.MonkeyPatch
) -> None:
    passes: list[list[str]] = []
    real_bbox_fetch = weather.get_weather_for_airports_bbox

    def counting_bbox_fetch(airport_icaos: list[str], *args: Any, **kwargs: Any):
        passes.append(sorted(airport_icaos))
        return real_bbox_fetch(airport_icaos, *args, **kwargs)

    monkeypatch.setattr(analysis, "get_weather_for_airports_bbox", counting_bbox_fetch)
    net.west.block()
    async with weather_app(net) as (app, pilot):
        app.action_refresh()
        await _wait_until(
            pilot, net.west.entered.is_set, "the weather box was never requested"
        )
        await _wait_until(
            pilot,
            lambda: _total(app, "KSFO") == "1",
            "the first refresh's traffic waited for the weather box",
        )

        feed.pilots.append(_pilot("AAL2", "KOAK"))
        app.action_refresh()
        await _wait_until(
            pilot,
            lambda: _total(app, "KOAK") == "1",
            "a refresh during the weather pass never landed",
        )
        feed.pilots.append(_pilot("AAL3", "KSFO"))
        app.action_refresh()
        await _wait_until(
            pilot,
            lambda: _total(app, "KSFO") == "2",
            "a second refresh during the weather pass never landed",
        )

        assert net.west.requests == 1, "a refresh re-requested the box in flight"
        assert passes == [["KSFO"]], "a refresh started a second weather pass"

        net.west.open()
        await _wait_until(
            pilot,
            lambda: (
                _shows_fixture_weather(app, "KSFO")
                and _shows_fixture_weather(app, "KOAK")
            ),
            "the rerun pass never filled the airport the later refresh added",
        )
        await app.workers.wait_for_complete()

        assert passes == [["KSFO"], ["KOAK", "KSFO"]], (
            "the refreshes during the pass did not coalesce into exactly one more pass"
        )
        assert net.west.requests == 2


async def test_startup_first_paint_has_traffic_without_weather(
    net: RegionNet, feed: Feed
) -> None:
    airport_data, _groupings, _total_flights, _unified, _names = (
        analysis.analyze_flights_data(
            max_eta_hours=1.0,
            airport_allowlist=list(COORDINATES),
            groupings_allowlist=None,
            include_all_staffed=False,
            include_all_arriving=False,
            unified_airport_data=UNIFIED,
            disambiguator=config.DISAMBIGUATOR,
        )
    )
    assert net.bbox_urls() == [], "the traffic-only analysis requested weather"

    net.west.block()
    async with weather_app(net, airport_data) as (app, pilot):
        assert _row(app, "KSFO") == {"wind": "", "alt": "", "total": "1"}
        await _wait_until(
            pilot, net.west.entered.is_set, "startup never started a weather pass"
        )

        net.west.open()
        await _wait_until(
            pilot,
            lambda: _shows_fixture_weather(app, "KSFO"),
            "the startup weather pass never filled WIND/ALT",
        )


@pytest.mark.parametrize("failure", ["populate_raises", "update_task_fails"])
async def test_weather_patch_failure_is_logged(
    net: RegionNet,
    feed: Feed,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    async with weather_app(net) as (app, pilot):
        await _first_refresh(app, pilot)
        table_manager = app.airports_manager
        assert table_manager is not None

        async def failing_update() -> None:
            raise RuntimeError("table update failed")

        def failing_populate(_data: list) -> asyncio.Task:
            if failure == "populate_raises":
                raise RuntimeError("table update failed")
            return asyncio.create_task(failing_update())

        monkeypatch.setattr(table_manager, "populate", failing_populate)
        with caplog.at_level(logging.ERROR, logger="vatsim_debug"):
            app._apply_weather_batch({"KSFO": ("09005KT", "A2990")})
            for _ in range(3):
                await pilot.pause()

        logged = [
            record
            for record in caplog.records
            if record.name == "vatsim_debug"
            and "Weather patch failed" in record.message
        ]
        assert len(logged) == 1, "a failed weather patch did not reach the app's log"
        # common.logger's buffer freezes a record's traceback into exc_text
        assert logged[0].exc_info or logged[0].exc_text, "the traceback was not logged"


async def test_patch_after_exit_is_dropped(
    net: RegionNet, feed: Feed, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with weather_app(net) as (app, pilot):
        await _first_refresh(app, pilot)
    table_manager = app.airports_manager
    assert table_manager is not None
    populated: list[list] = []
    monkeypatch.setattr(table_manager, "populate", populated.append)

    app._apply_weather_batch({"KSFO": ("09005KT", "A2990")})

    row = _model(app, "KSFO")
    assert (row.wind, row.altimeter) == WEATHER["KSFO"], (
        "a box returning after exit patched the model"
    )
    assert populated == [], "a box returning after exit updated the torn-down table"


def test_capped_box_defers_missing_airports_to_the_fallback(net: RegionNet) -> None:
    lat, lon = COORDINATES["KSFO"]
    for n in range(weather.BBOX_MAX_ENTRIES - 1):
        filler = f"F{n:03d}"
        net.stations[filler] = (
            lat + 0.01,
            lon + 0.01,
            _metar(filler, "00000KT", "A3000"),
        )
    del net.stations["KOAK"]  # the west box answers at the cap, without KOAK
    koak_metar = _metar("KOAK", "18004KT", "A3001")
    net.ids = lambda url: koak_metar.encode() if "KOAK" in url else b""
    net.east.block()

    deliveries: list[tuple[dict[str, tuple[str, str]], int]] = []

    def on_box(values: dict[str, tuple[str, str]]) -> None:
        deliveries.append((dict(values), net.count("ids=")))
        net.east.open()  # the east box returns only after the west box was delivered

    analysis.fetch_weather_for_airports(
        ["KSFO", "KOAK", "KJFK"],
        UNIFIED,
        hide_wind=False,
        as_of=datetime.now(UTC),
        on_box=on_box,
        stop=threading.Event(),
    )

    assert deliveries == [
        ({"KSFO": WEATHER["KSFO"]}, 0),
        ({"KJFK": WEATHER["KJFK"]}, 0),
        ({"KOAK": ("18004KT", "A3001")}, 1),
    ], "a per-station fetch ran before a later box's patch"


def test_quit_cancels_queued_boxes(net: RegionNet) -> None:
    spread = {
        f"X{n:03d}": {"latitude": 0.0, "longitude": -170.0 + 45.0 * n} for n in range(8)
    }
    gate = Gate()
    gate.block()

    def gated_bbox(_url: str) -> bytes:
        gate.pass_through()
        return b"[]"

    net.bbox = gated_bbox
    stop = threading.Event()
    worker = threading.Thread(
        target=analysis.fetch_weather_for_airports,
        args=(list(spread), spread),
        kwargs={
            "hide_wind": False,
            "as_of": datetime.now(UTC),
            "on_box": lambda _values: None,
            "stop": stop,
        },
        daemon=True,
    )
    worker.start()
    try:
        deadline = time.monotonic() + WAIT
        while len(net.bbox_urls()) < 5:
            assert time.monotonic() < deadline, "five box requests never started"
            time.sleep(0.01)
        stop.set()
    finally:
        gate.open()
    worker.join(WAIT)

    assert not worker.is_alive(), "the weather fetch did not end after the stop"
    assert len(net.bbox_urls()) == 5, "a queued box was requested after the stop"
    assert net.count("ids=") == 0, "the per-station fallback ran after the stop"


async def test_hide_wind_sets_only_altimeter(net: RegionNet, feed: Feed) -> None:
    async with weather_app(net, args=HIDE_WIND_ARGS) as (app, pilot):
        app.action_refresh()
        await _wait_until(
            pilot,
            lambda: any(
                row.icao == "KSFO" and row.altimeter == WEATHER["KSFO"][1]
                for row in app.original_airport_data
            ),
            "the weather pass never set KSFO's altimeter",
        )
        await app.workers.wait_for_complete()

        row = _model(app, "KSFO")
        assert (row.wind, row.altimeter) == ("", WEATHER["KSFO"][1])


async def test_failed_box_fallback_value_is_delivered(
    net: RegionNet, feed: Feed
) -> None:
    async with weather_app(net) as (app, pilot):
        await _first_refresh(app, pilot)

        _clear_weather()
        net.west.fail = True
        fallback_metar = _metar("KSFO", "09005KT", "A2990")
        net.ids = lambda url: fallback_metar.encode() if "KSFO" in url else b""
        app.action_refresh()
        await _wait_until(
            pilot,
            lambda: _shows(app, "KSFO", "09005KT", "A2990"),
            "the per-station value after a failed box never reached the table",
        )
        assert net.west.requests == 2


def test_box_future_that_raises_is_a_failed_box(
    net: RegionNet, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crashing_request(*_args: Any) -> weather.BboxFetchResult:
        raise RuntimeError("bbox worker crashed")

    monkeypatch.setattr(weather, "_fetch_weather_bbox_result", crashing_request)
    calls: list[tuple[set[str], bool]] = []

    weather.get_weather_for_airports_bbox(
        ["KSFO", "KOAK"],
        UNIFIED,
        on_box=lambda members, ok: calls.append((set(members), ok)),
    )

    assert calls == [({"KSFO", "KOAK"}, False)]
