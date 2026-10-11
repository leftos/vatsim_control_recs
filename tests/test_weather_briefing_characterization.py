"""Characterization of the grouping weather briefing: its fetch, airport cards, screen text and HTML export.

The screen is built without an app, as the flight briefing characterization builds it:
its ``query_one`` is replaced by a lookup of recording widgets, so every update of the
summary and the content is kept, and its ``notify`` by a recorder of its toasts. The
METAR, TAF, VATSIM and D-ATIS fetchers are faked at the module boundary, the airport
data and the disambiguator on ``ui.config``; time is a fixed clock patched over
``weather_briefing.datetime`` and ``taf_parsing.datetime``, and the export's temp file
and browser are faked over ``weather_briefing.tempfile`` and
``weather_briefing.webbrowser``.
"""

from __future__ import annotations

import asyncio
import html
import random
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, tzinfo
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from backend.briefing import taf_parsing
from ui import config
from ui.modals import weather_briefing
from ui.modals.weather_briefing import WeatherBriefingScreen

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
LOCAL = NOW.astimezone().strftime("%H:%M LT")
TIMESTAMP = f"[dim]1200Z ({LOCAL})[/dim]"

# KSFO, KOAK and KHAF sit around the bay, KLAX and KSMO in Los Angeles; KNOP has no
# position. KHAF and KSMO are too small to centre an area of their own.
AIRPORTS: dict[str, dict[str, Any]] = {
    "KSFO": {
        "latitude": 37.62,
        "longitude": -122.38,
        "city": "San Francisco",
        "far139": "I E",
    },
    "KOAK": {
        "latitude": 37.72,
        "longitude": -122.22,
        "city": "Oakland",
        "far139": "I D",
    },
    "KHAF": {
        "latitude": 37.51,
        "longitude": -122.50,
        "city": "Half Moon Bay",
        "tower_type": "NON-ATCT",
    },
    "KLAX": {
        "latitude": 33.94,
        "longitude": -118.41,
        "city": "Los Angeles",
        "far139": "I E",
    },
    "KSMO": {
        "latitude": 34.02,
        "longitude": -118.45,
        "city": "Santa Monica",
        "tower_type": "ATCT",
    },
    "KNOP": {"city": "Nopos"},
}

NAMES = {
    "KSFO": "San Francisco Intl",
    "KOAK": "Oakland Intl",
    "KHAF": "Half Moon Bay",
    "KLAX": "Los Angeles Intl",
    "KSMO": "Santa Monica",
    "KNOP": "Nopos Field",
}

METARS = {
    "KSFO": "KSFO 101156Z 28012G22KT 10SM FEW008 BKN015 15/10 A3001",
    "KOAK": "KOAK 101153Z 00000KT 10SM CLR 14/09 A3002",
    "KHAF": "KHAF 101155Z AUTO 2SM BR OVC006 12/12 A3001",
    "KLAX": "KLAX 101153Z 25008KT 1/2SM FG VV002 13/13 A2998",
    "KSMO": "SPECI KSMO 101205Z 26006KT 5SM HZ SCT030 18/09 A2999",
}

TAFS = {
    "KSFO": (
        "TAF KSFO 101120Z 1012/1112 28012KT P6SM BKN015 FM101800 29015G25KT P6SM "
        "SCT030 TEMPO 1020/1024 3SM BR BKN008"
    ),
    "KHAF": "TAF KHAF 101120Z 1012/1112 2SM BR OVC006 FM101600 P6SM SKC",
    "KNOP": "TAF KNOP 101120Z 1012/1112 27010KT P6SM SKC FM101900 1SM BR OVC004",
}

GROUPING = ["KSFO", "KOAK", "KHAF", "KLAX", "KSMO", "KNOP"]

SFO_ATIS = [
    {
        "atis_code": "A",
        "type": "combined",
        "source": "vatsim",
        "text_atis": "SFO ATIS INFO A. ILS RWY 28L APCH IN USE. DEPG RWY 1L.",
    }
]
LAX_DATIS = [
    {
        "atis_code": "K",
        "type": "departure",
        "source": "rw",
        "text_atis": "LAX DEP INFO K. DEPG RWY 25R 24L.",
    },
    {
        "atis_code": "L",
        "type": "arrival",
        "source": "rw",
        "text_atis": "LAX ARR INFO L. ILS RWY 24R 25L APCHS IN USE.",
    },
]

TEMP_PATH = "/tmp/briefings/weather_briefing_Bay Area_abc123.html"


class FakeTempFile:
    """A named temp file that keeps what is written to it."""

    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs
        self.name = TEMP_PATH
        self.written: list[str] = []
        self.closed = False

    def __enter__(self) -> FakeTempFile:
        return self

    def __exit__(self, *exc: object) -> None:
        self.closed = True

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)


class FakeDisambiguator:
    """Display names from a table."""

    def get_full_name(self, icao: str) -> str:
        return NAMES.get(icao, icao)

    def get_pretty_name(self, icao: str) -> str:
        return NAMES.get(icao, icao)


def answer(table: dict[str, Any], airports: list[str]) -> dict[str, Any]:
    return {icao: table[icao] for icao in airports if icao in table}


class World:
    """The module's outside world: answers and records every boundary call."""

    def __init__(self) -> None:
        self.metars: dict[str, str] = dict(METARS)
        self.tafs: dict[str, str] = dict(TAFS)
        self.weather_error: BaseException | None = None
        self.smart_calls: list[tuple[list[str], dict[str, Any], bool]] = []
        self.metar_calls: list[list[str]] = []
        self.taf_calls: list[list[str]] = []
        self.vatsim: dict[str, Any] | None = {"atis": []}
        self.vatsim_calls = 0
        self.vatsim_atis: dict[str, Any] = {"KSFO": SFO_ATIS}
        self.atis_calls: list[tuple[Any, list[str]]] = []
        self.datis: dict[str, Any] = {"KLAX": LAX_DATIS}
        self.datis_calls: list[list[str]] = []
        self.files: list[FakeTempFile] = []
        self.opened: list[str] = []
        self.debug: list[str] = []
        self.call_order: list[str] = []

    def get_weather_smart(
        self,
        airports: list[str],
        coords: dict[str, Any],
        include_taf: bool,
        progress_callback: Any,
    ) -> tuple[dict[str, str], dict[str, str]]:
        self.call_order.append("smart")
        self.smart_calls.append((list(airports), dict(coords), include_taf))
        if self.weather_error is not None:
            raise self.weather_error
        progress_callback(len(airports), len(airports))
        return answer(self.metars, airports), answer(self.tafs, airports)

    def get_metar_batch(
        self, airports: list[str], progress_callback: Any
    ) -> dict[str, str]:
        self.call_order.append("metar")
        self.metar_calls.append(list(airports))
        if self.weather_error is not None:
            raise self.weather_error
        progress_callback(len(airports), len(airports))
        return answer(self.metars, airports)

    def get_taf_batch(self, airports: list[str]) -> dict[str, str]:
        self.call_order.append("taf")
        self.taf_calls.append(list(airports))
        return answer(self.tafs, airports)

    def download_vatsim_data(self) -> dict[str, Any] | None:
        self.call_order.append("vatsim")
        self.vatsim_calls += 1
        return self.vatsim

    def get_atis_for_airports(
        self, vatsim_data: Any, airports: list[str]
    ) -> dict[str, Any]:
        self.call_order.append("atis")
        self.atis_calls.append((vatsim_data, list(airports)))
        return answer(self.vatsim_atis, airports)

    def get_datis_for_airports(self, airports: list[str]) -> dict[str, Any]:
        self.call_order.append("datis")
        self.datis_calls.append(list(airports))
        return answer(self.datis, airports)

    def named_temporary_file(self, **kwargs: Any) -> FakeTempFile:
        temp_file = FakeTempFile(kwargs)
        self.files.append(temp_file)
        return temp_file


def install(monkeypatch: pytest.MonkeyPatch) -> World:
    """Fake the module's boundary and return the fakes."""
    world = World()
    monkeypatch.setattr(config, "UNIFIED_AIRPORT_DATA", AIRPORTS)
    monkeypatch.setattr(config, "DISAMBIGUATOR", FakeDisambiguator())
    for name in (
        "get_weather_smart",
        "get_metar_batch",
        "get_taf_batch",
        "download_vatsim_data",
        "get_atis_for_airports",
        "get_datis_for_airports",
    ):
        monkeypatch.setattr(weather_briefing, name, getattr(world, name))
    monkeypatch.setattr(weather_briefing, "debug", world.debug.append)

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return NOW

    monkeypatch.setattr(weather_briefing, "datetime", FakeDatetime)
    monkeypatch.setattr(taf_parsing, "datetime", FakeDatetime)
    monkeypatch.setattr(
        weather_briefing,
        "tempfile",
        SimpleNamespace(NamedTemporaryFile=world.named_temporary_file),
    )
    monkeypatch.setattr(
        weather_briefing, "webbrowser", SimpleNamespace(open=world.opened.append)
    )
    return world


@pytest.fixture(autouse=True)
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    yield install(monkeypatch)


class Widget:
    """A Static that keeps every text it is given."""

    def __init__(self) -> None:
        self.updates: list[str] = []

    def update(self, text: str) -> None:
        self.updates.append(text)


class WidgetsNotReadyError(LookupError):
    """Stands in for the lookup failure of a screen that is not mounted."""


class Briefing:
    """A briefing screen with its widget updates and toasts recorded."""

    def __init__(
        self,
        airports: list[str] | None = None,
        primary: list[str] | None = None,
        route: bool = False,
    ) -> None:
        self.screen = WeatherBriefingScreen(
            "Bay Area",
            list(GROUPING if airports is None else airports),
            primary_airports=primary,
            is_flight_route=route,
        )
        self.summary = Widget()
        self.content = Widget()
        self.widgets_ready = True
        self.toasts: list[tuple[str, dict[str, Any]]] = []
        self.screen.query_one = self._query_one
        self.screen.notify = self._notify

    def _query_one(self, selector: str, expect_type: Any = None) -> Widget:
        if not self.widgets_ready:
            raise WidgetsNotReadyError(selector)
        return {"#briefing-summary": self.summary, "#briefing-content": self.content}[
            selector
        ]

    def _notify(self, message: str, **kwargs: Any) -> None:
        self.toasts.append((message, kwargs))


def text(*lines: str) -> str:
    return "\n".join(lines)


def dim(message: str) -> str:
    return f"[dim]{message}[/dim]"


WEATHER_DONE = "[green]Weather ✓[/green]"
ATIS_DONE = "[green]ATIS ✓[/green]"
ATIS_WAIT = "[dim]ATIS...[/dim]"
EXTRACTING = dim("Extracting ATIS information...")
BUILDING = dim("Building area groups...")


def fetching(weather: str, atis: str) -> str:
    return dim(f"Fetching: {weather} | {atis}")


def weather_count(done: int, total: int) -> str:
    return f"[dim]Weather {done}/{total}[/dim]"


def processing(filled: int, pct: int) -> str:
    bar = f"[green]{'█' * filled}[/green][dim]{'░' * (20 - filled)}[/dim]"
    return dim(f"Processing weather: {bar} {pct}%")


# _fetch_all_weather_async


class SeededExecutor:
    """Stands in for the loop's thread pool, finishing calls in a seeded order.

    Each call runs on the loop itself. Calls wait until the loop's next pass, and
    each pass finishes one waiting call picked by a seeded random choice, so which
    of the weather and VATSIM fetches finishes first is fixed by the seed.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, seed: int) -> None:
        self.loop = loop
        self.rng = random.Random(seed)
        self.waiting: list[tuple[Callable[[], Any], asyncio.Future[Any]]] = []
        self.finishing = False

    def run_in_executor(
        self, executor: Any, func: Callable[..., Any], *args: Any
    ) -> asyncio.Future[Any]:
        future = self.loop.create_future()
        self.waiting.append((lambda: func(*args), future))
        if not self.finishing:
            self.finishing = True
            self.loop.call_soon(self._finish_one)
        return future

    def _finish_one(self) -> None:
        call, future = self.waiting.pop(self.rng.randrange(len(self.waiting)))
        try:
            result = call()
        except Exception as error:
            future.set_exception(error)
        else:
            future.set_result(result)
        if self.waiting:
            self.loop.call_soon(self._finish_one)
        else:
            self.finishing = False


# The seeds' first picks: the first call submitted (the weather fetch), or the second
WEATHER_FIRST = 1
VATSIM_FIRST = 0


async def run_fetch(briefing: Briefing, seed: int = WEATHER_FIRST) -> Briefing:
    loop = asyncio.get_running_loop()
    loop.run_in_executor = SeededExecutor(loop, seed).run_in_executor
    try:
        await briefing.screen._fetch_all_weather_async()
    finally:
        del loop.run_in_executor
    return briefing


def calls(world: World) -> dict[str, Any]:
    """Every boundary call the fetch made."""
    return {
        "smart": world.smart_calls,
        "metar": world.metar_calls,
        "taf": world.taf_calls,
        "vatsim": world.vatsim_calls,
        "atis": world.atis_calls,
        "datis": world.datis_calls,
    }


def fetch_phase(total: int, *, vatsim_first: bool) -> list[str]:
    """The progress messages while the weather and VATSIM fetches run."""
    start = weather_count(0, total) if total else WEATHER_DONE
    middle = (
        fetching(start, ATIS_DONE)
        if vatsim_first
        else fetching(WEATHER_DONE, ATIS_WAIT)
    )
    return [fetching(start, ATIS_WAIT), middle, fetching(WEATHER_DONE, ATIS_DONE)]


COORDS = {
    icao: (info["latitude"], info["longitude"])
    for icao, info in AIRPORTS.items()
    if "latitude" in info
}


@pytest.mark.parametrize(
    ("seed", "call_order"),
    [
        pytest.param(
            WEATHER_FIRST, ["smart", "vatsim", "atis", "datis"], id="weather-first"
        ),
        pytest.param(
            VATSIM_FIRST, ["vatsim", "smart", "atis", "datis"], id="vatsim-first"
        ),
    ],
)
async def test_fetch_grouping_with_positions_uses_the_smart_fetch(
    world: World, seed: int, call_order: list[str]
) -> None:
    briefing = await run_fetch(Briefing(), seed)

    assert world.call_order == call_order
    assert calls(world) == {
        "smart": [(GROUPING, COORDS, True)],
        "metar": [],
        "taf": [],
        "vatsim": 1,
        "atis": [({"atis": []}, GROUPING)],
        "datis": [["KOAK", "KHAF", "KLAX", "KSMO", "KNOP"]],
    }
    assert briefing.screen.weather_data == FETCHED
    assert list(briefing.screen.weather_data) == GROUPING
    assert briefing.summary.updates == [
        *fetch_phase(6, vatsim_first=seed == VATSIM_FIRST),
        EXTRACTING,
        processing(0, 0),
        processing(3, 16),
        processing(6, 33),
        processing(10, 50),
        processing(13, 66),
        processing(16, 83),
        BUILDING,
        f"{TIMESTAMP} | {GROUPING_SUMMARY}",
    ]
    assert briefing.content.updates == [AREAS_ONLY_CONTENT]
    assert world.debug == []


async def test_fetch_with_exactly_four_in_five_positioned_uses_the_smart_fetch(
    world: World,
) -> None:
    airports = ["KSFO", "KOAK", "KHAF", "KLAX", "KNOP"]

    await run_fetch(Briefing(airports))

    assert world.call_order == ["smart", "vatsim", "atis", "datis"]
    assert [call[0] for call in world.smart_calls] == [airports]
    assert world.smart_calls[0][1] == {icao: COORDS[icao] for icao in airports[:4]}
    assert world.metar_calls == []
    assert world.taf_calls == []


async def test_fetch_with_fewer_than_four_in_five_positioned_uses_batches(
    world: World,
) -> None:
    airports = ["KSFO", "KOAK", "KHAF", "KNOP"]

    briefing = await run_fetch(Briefing(airports))

    assert world.call_order == ["metar", "vatsim", "taf", "atis", "datis"]
    assert calls(world) == {
        "smart": [],
        "metar": [airports],
        "taf": [airports],
        "vatsim": 1,
        "atis": [({"atis": []}, airports)],
        "datis": [["KOAK", "KHAF", "KNOP"]],
    }
    assert briefing.screen.weather_data == {icao: FETCHED[icao] for icao in airports}
    assert briefing.summary.updates == [
        *fetch_phase(4, vatsim_first=False),
        EXTRACTING,
        processing(0, 0),
        processing(5, 25),
        processing(10, 50),
        processing(15, 75),
        BUILDING,
        f"{TIMESTAMP} | [red]1 IFR[/red] | [#5599ff]1 MVFR[/#5599ff] | [#00ff00]1 VFR[/#00ff00]",
    ]


async def test_fetch_without_airport_data_uses_batches(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "UNIFIED_AIRPORT_DATA", None)

    briefing = await run_fetch(Briefing(["KSFO", "KLAX"], primary=["KSFO"]))

    assert world.call_order == ["metar", "vatsim", "taf", "atis", "datis"]
    assert world.metar_calls == [["KSFO", "KLAX"]]
    assert briefing.screen.weather_data == {
        "KSFO": FETCHED["KSFO"],
        "KLAX": FETCHED["KLAX"],
    }
    assert briefing.content.updates == [
        text("[bold cyan]━━━ REQUESTED ━━━[/bold cyan]", CARD_KSFO_PRIMARY)
    ]


async def test_fetch_without_vatsim_data_asks_datis_for_every_airport(
    world: World,
) -> None:
    world.vatsim = None

    briefing = await run_fetch(Briefing(["KSFO", "KLAX"]))

    assert world.call_order == ["smart", "vatsim", "datis"]
    assert world.datis_calls == [["KSFO", "KLAX"]]
    assert briefing.screen.weather_data["KSFO"]["atis"] is None
    assert briefing.screen.weather_data["KLAX"]["atis"] == LAX_DATIS


async def test_fetch_with_vatsim_atis_everywhere_skips_datis(world: World) -> None:
    world.vatsim_atis = {"KSFO": SFO_ATIS, "KLAX": LAX_DATIS}

    briefing = await run_fetch(Briefing(["KSFO", "KLAX"]))

    assert world.call_order == ["smart", "vatsim", "atis"]
    assert briefing.screen.weather_data["KLAX"]["atis"] == LAX_DATIS


async def test_fetch_of_no_airports(world: World) -> None:
    briefing = await run_fetch(Briefing([]))

    assert world.call_order == ["smart", "vatsim", "atis"]
    assert calls(world) == {
        "smart": [([], {}, True)],
        "metar": [],
        "taf": [],
        "vatsim": 1,
        "atis": [({"atis": []}, [])],
        "datis": [],
    }
    assert briefing.screen.weather_data == {}
    assert briefing.summary.updates == [
        *fetch_phase(0, vatsim_first=False),
        EXTRACTING,
        BUILDING,
        f"{TIMESTAMP} | No weather data",
    ]
    assert briefing.content.updates == ["[dim]No weather data available[/dim]"]


async def test_fetch_of_forty_airports_reports_progress_every_second_airport(
    world: World,
) -> None:
    airports = [f"K{n:03d}" for n in range(40)]

    briefing = await run_fetch(Briefing(airports))

    assert briefing.summary.updates == [
        *fetch_phase(40, vatsim_first=False),
        EXTRACTING,
        *[processing(pct // 5, pct) for pct in range(0, 100, 5)],
        BUILDING,
        f"{TIMESTAMP} | No weather data",
    ]
    assert briefing.screen.weather_data["K000"] == NO_METAR_FETCHED


async def test_fetch_yields_after_each_progress_message(world: World) -> None:
    world.vatsim_atis = {"KSFO": SFO_ATIS, "KLAX": LAX_DATIS}
    briefing = Briefing(["KSFO", "KLAX"])
    fetch = asyncio.create_task(run_fetch(briefing))

    # This task runs only while the fetch is suspended: each summary it sees
    # was on screen while the loop was free to repaint.
    seen: list[str] = []
    while True:
        updates = briefing.summary.updates
        if updates and (not seen or seen[-1] != updates[-1]):
            seen.append(updates[-1])
        if fetch.done():
            break
        await asyncio.sleep(0)
    await fetch

    assert world.call_order == ["smart", "vatsim", "atis"]
    assert seen == [
        *fetch_phase(2, vatsim_first=False),
        EXTRACTING,
        processing(0, 0),
        processing(10, 50),
        BUILDING,
        f"{TIMESTAMP} | [magenta]1 LIFR[/magenta] | [#5599ff]1 MVFR[/#5599ff]",
    ]


async def test_fetch_failure_shows_the_error_and_raises(world: World) -> None:
    world.weather_error = RuntimeError("weather feed down")
    briefing = Briefing()

    with pytest.raises(RuntimeError, match="weather feed down"):
        await run_fetch(briefing)

    assert world.call_order == ["smart", "vatsim"]
    assert briefing.content.updates == [
        "[red]Error fetching weather: weather feed down[/red]"
    ]
    assert briefing.summary.updates == [
        fetching(weather_count(0, 6), ATIS_WAIT),
        fetching(weather_count(0, 6), ATIS_DONE),
        "[red]Error[/red]",
    ]
    assert briefing.screen.weather_data == {}


async def test_fetch_before_the_widgets_exist_logs_and_raises(world: World) -> None:
    briefing = Briefing(["KSFO"])
    briefing.widgets_ready = False

    with pytest.raises(WidgetsNotReadyError, match="#briefing-summary"):
        await run_fetch(briefing)

    progress_logs = [m for m in world.debug if m.startswith("Could not update")]
    assert progress_logs
    assert set(progress_logs) == {
        "Could not update briefing progress, widget not ready: #briefing-summary"
    }
    assert world.debug[-1] == (
        "Could not display weather fetch error: #briefing-content"
    )
    assert briefing.screen.weather_data == {"KSFO": FETCHED["KSFO"]}


# _build_airport_card


def taf_change(
    trend: str,
    *,
    kind: str = "FM",
    time_str: str = "101800",
    category: str = "VFR",
    vis: float | None = None,
    ceiling: str | None = None,
    wind: str | None = None,
    wx: list[str] | None = None,
) -> dict[str, Any]:
    """A TAF change shaped as parse_taf_changes returns it."""
    return {
        "type": kind,
        "time_str": time_str,
        "category": category,
        "visibility_sm": vis,
        "ceiling_ft": None,
        "ceiling_layer": ceiling,
        "wind": wind,
        "phenomena": wx or [],
        "trend": trend,
        "is_improvement": trend == "improving",
        "is_deterioration": trend == "worsening",
    }


IMPROVING_VFR = taf_change(
    "improving", vis=10.0, ceiling="SCT030", wind="29015G25KT", category="VFR"
)
WORSENING_TEMPO = taf_change(
    "worsening",
    kind="TEMPO",
    time_str="1020/1024",
    category="IFR",
    vis=3.0,
    ceiling="BKN008",
    wx=["BR"],
)

CARD_CASES: dict[str, tuple[str, dict[str, Any], bool]] = {
    "vfr_clear": ("KOAK", {"category": "VFR"}, False),
    "mvfr_all_conditions": (
        "KSFO",
        {
            "category": "MVFR",
            "ceiling": "BKN015",
            "visibility": "10SM",
            "wind": "28012G22KT",
        },
        False,
    ),
    "ifr_visibility_no_wind": (
        "KHAF",
        {"category": "IFR", "ceiling": "OVC006", "visibility": "2SM", "wind": None},
        False,
    ),
    "lifr_wind_no_visibility": (
        "KLAX",
        {"category": "LIFR", "ceiling": "VV002", "wind": "25008KT"},
        False,
    ),
    "empty_data": ("KNOP", {}, False),
    "unknown_category": ("KNOP", {"category": "XYZ"}, False),
    "unknown_airport_name": ("KZZZ", {"category": "VFR"}, False),
    "speci": ("KSMO", {"category": "MVFR", "is_speci": True}, False),
    "primary": ("KSFO", {"category": "VFR"}, True),
    "primary_speci": ("KSFO", {"category": "IFR", "is_speci": True}, True),
    "phenomena": ("KHAF", {"category": "IFR", "phenomena": ["-RA", "BR"]}, False),
    "phenomena_empty": ("KHAF", {"category": "IFR", "phenomena": []}, False),
    "taf_stable_only": (
        "KSFO",
        {"category": "VFR", "taf_changes": [taf_change("stable", vis=10.0)]},
        False,
    ),
    "taf_one_improving": (
        "KSFO",
        {"category": "MVFR", "taf_changes": [IMPROVING_VFR]},
        False,
    ),
    "taf_one_worsening_tempo": (
        "KSFO",
        {"category": "VFR", "taf_changes": [WORSENING_TEMPO]},
        False,
    ),
    "taf_first_two_significant": (
        "KSFO",
        {
            "category": "MVFR",
            "taf_changes": [
                taf_change("stable", vis=10.0, ceiling="BKN040"),
                WORSENING_TEMPO,
                taf_change("stable", vis=10.0),
                IMPROVING_VFR,
                taf_change("worsening", time_str="110600", category="LIFR", vis=0.25),
            ],
        },
        False,
    ),
    "taf_visibility_p6sm_and_fraction": (
        "KSFO",
        {
            "category": "MVFR",
            "taf_changes": [
                taf_change("improving", vis=6.0),
                taf_change("worsening", time_str="102100", category="IFR", vis=2.5),
            ],
        },
        False,
    ),
    "taf_visibility_whole_and_rounded": (
        "KSFO",
        {
            "category": "MVFR",
            "taf_changes": [
                taf_change("improving", vis=3.0),
                taf_change("worsening", time_str="102100", category="LIFR", vis=1.25),
            ],
        },
        False,
    ),
    "taf_no_details": (
        "KSFO",
        {"category": "VFR", "taf_changes": [taf_change("worsening")]},
        False,
    ),
    "taf_missing_keys": (
        "KSFO",
        {"category": "VFR", "taf_changes": [{"is_improvement": True}]},
        False,
    ),
    "taf_flag_and_trend_disagree": (
        "KSFO",
        {
            "category": "VFR",
            "taf_changes": [
                {
                    **taf_change("improving", wind="18005KT"),
                    "is_improvement": False,
                    "is_deterioration": True,
                }
            ],
        },
        False,
    ),
    "taf_unparsable_and_past_times": (
        "KSFO",
        {
            "category": "VFR",
            "taf_changes": [
                taf_change("worsening", time_str="garbage", wx=["TSRA", "BR"]),
                taf_change("improving", time_str="100600", wind="00000KT"),
            ],
        },
        False,
    ),
    "taf_column_present_in_one_row": (
        "KSFO",
        {
            "category": "VFR",
            "taf_changes": [
                taf_change("worsening", category="MVFR", ceiling="BKN025"),
                taf_change("improving", time_str="102200", wind="27010KT"),
            ],
        },
        False,
    ),
    "atis_none": ("KSFO", {"category": "VFR", "atis": None}, False),
    "atis_empty": ("KSFO", {"category": "VFR", "atis": []}, False),
    "atis_one_vatsim": ("KSFO", {"category": "VFR", "atis": SFO_ATIS}, False),
    "atis_rw_departure_and_arrival": (
        "KLAX",
        {"category": "LIFR", "atis": LAX_DATIS},
        False,
    ),
    "atis_blank_text_skipped_and_defaults": (
        "KOAK",
        {
            "category": "VFR",
            "atis": [{"text_atis": ""}, {"text_atis": "RWY 30 IN USE."}],
        },
        False,
    ),
    "everything": (
        "KSFO",
        {
            "category": "IFR",
            "is_speci": True,
            "ceiling": "OVC008",
            "visibility": "1 1/2SM",
            "wind": "18015G30KT",
            "phenomena": ["+RA", "BR"],
            "taf_changes": [WORSENING_TEMPO, IMPROVING_VFR],
            "atis": SFO_ATIS + LAX_DATIS,
        },
        True,
    ),
}


@pytest.mark.parametrize("case", list(CARD_CASES))
def test_build_airport_card(case: str) -> None:
    icao, data, is_primary = CARD_CASES[case]

    card = Briefing().screen._build_airport_card(icao, data, is_primary=is_primary)

    assert card == CARD_EXPECTED[case]


def test_build_airport_card_without_disambiguator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "DISAMBIGUATOR", None)

    card = Briefing().screen._build_airport_card("KSFO", {"category": "VFR"})

    assert card == text(
        "KSFO - KSFO // [#00ff00 bold]VFR[/#00ff00 bold]", "  Ceiling: CLR"
    )


# _update_display


def display(
    weather: dict[str, dict[str, Any]],
    airports: list[str] | None = None,
    primary: list[str] | None = None,
    route: bool = False,
) -> Briefing:
    briefing = Briefing(airports, primary=primary, route=route)
    briefing.screen.weather_data = weather
    briefing.screen._update_display()
    return briefing


def test_display_grouping_with_requested_airports() -> None:
    weather = dict(FETCHED)

    briefing = display(weather, primary=["KSFO", "KZZZ"])

    assert briefing.summary.updates == [f"{TIMESTAMP} | {GROUPING_SUMMARY}"]
    assert briefing.content.updates == [GROUPING_CONTENT]
    assert briefing.screen.weather_data is weather


def test_display_grouping_without_requested_airports() -> None:
    briefing = display(dict(FETCHED))

    assert briefing.summary.updates == [f"{TIMESTAMP} | {GROUPING_SUMMARY}"]
    assert briefing.content.updates == [AREAS_ONLY_CONTENT]


def test_display_requested_airport_without_weather_omits_the_section() -> None:
    briefing = display(dict(FETCHED), primary=["KZZZ"])

    assert briefing.content.updates == [AREAS_ONLY_CONTENT]


def test_display_flight_route_keeps_route_order() -> None:
    weather = {**FETCHED, "KOAK": {}}

    briefing = display(
        weather,
        airports=["KLAX", "KZZZ", "KOAK", "KHAF", "KSFO"],
        primary=["KLAX", "KSFO"],
        route=True,
    )

    assert briefing.summary.updates == [f"{TIMESTAMP} | {ROUTE_SUMMARY}"]
    assert briefing.content.updates == [ROUTE_CONTENT]


def test_display_without_weather() -> None:
    briefing = display({}, primary=["KSFO"])

    assert briefing.summary.updates == [f"{TIMESTAMP} | No weather data"]
    assert briefing.content.updates == ["[dim]No weather data available[/dim]"]


def test_display_unknown_categories_only() -> None:
    briefing = display({"KNOP": FETCHED["KNOP"], "KZZZ": NO_METAR_FETCHED})

    assert briefing.summary.updates == [f"{TIMESTAMP} | No weather data"]
    assert briefing.content.updates == ["[dim]No weather data available[/dim]"]


def test_display_without_airport_data_shows_only_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "UNIFIED_AIRPORT_DATA", None)

    briefing = display(dict(FETCHED), primary=["KSFO"])

    assert briefing.content.updates == [
        text("[bold cyan]━━━ REQUESTED ━━━[/bold cyan]", CARD_KSFO_PRIMARY)
    ]


class FakeClusterer:
    """An area clusterer whose areas are set by the test."""

    areas: ClassVar[list[dict[str, Any]]] = []
    seen: ClassVar[list[tuple[dict[str, Any], Any, Any]]] = []

    def __init__(
        self,
        weather_data: dict[str, Any],
        unified_airport_data: dict[str, Any],
        disambiguator: Any,
    ) -> None:
        FakeClusterer.seen.append(
            (dict(weather_data), unified_airport_data, disambiguator)
        )

    def create_area_groups(self) -> list[dict[str, Any]]:
        return FakeClusterer.areas


@pytest.fixture
def fake_clusterer(monkeypatch: pytest.MonkeyPatch) -> type[FakeClusterer]:
    monkeypatch.setattr(weather_briefing, "AreaClusterer", FakeClusterer)
    FakeClusterer.areas = [
        {"name": "Empty Area", "airports": [], "center_icao": "KZZZ"},
        {
            "name": "Unknown Area",
            "airports": [("KNOP", FETCHED["KNOP"], 9)],
            "center_icao": "KNOP",
        },
        {
            "name": "Bay Area",
            "airports": [
                ("KOAK", FETCHED["KOAK"], 1),
                ("KHAF", FETCHED["KHAF"], 8),
            ],
            "center_icao": "KOAK",
        },
    ]
    FakeClusterer.seen = []
    return FakeClusterer


def test_display_areas_skip_empty_ones_and_unsummarised_ones_have_no_summary(
    fake_clusterer: type[FakeClusterer],
) -> None:
    weather = dict(FETCHED)

    briefing = display(weather, primary=["KSFO", "KLAX"])

    ((seen_weather, unified, disambiguator),) = fake_clusterer.seen
    assert list(seen_weather) == ["KOAK", "KHAF", "KSMO", "KNOP"]
    assert unified is AIRPORTS
    assert disambiguator is config.DISAMBIGUATOR
    assert briefing.screen.weather_data is weather
    assert briefing.content.updates == [FAKE_AREAS_CONTENT]


# action_print


def page_lines(page: str) -> list[str]:
    """The text lines of an exported page."""
    body = page[page.index("<pre") : page.index("</pre>")]
    return html.unescape(re.sub(r"<[^>]+>", "", body)).split("\n")


def run_print(world: World, briefing: Briefing, weather: dict[str, Any]) -> str:
    """Export a screen; returns the one page it wrote."""
    briefing.screen.weather_data = weather
    briefing.screen.action_print()
    (temp_file,) = world.files
    return "".join(temp_file.written)


def test_print_without_weather_warns_and_exports_nothing(world: World) -> None:
    briefing = Briefing()

    briefing.screen.action_print()

    assert briefing.toasts == [("No weather data to export", {"severity": "warning"})]
    assert world.files == []
    assert world.opened == []


def test_print_writes_the_page_and_opens_it(world: World) -> None:
    briefing = Briefing(primary=["KSFO", "KZZZ"])

    page = run_print(world, briefing, dict(FETCHED))

    assert page == FULL_HTML
    (temp_file,) = world.files
    assert temp_file.kwargs == {
        "mode": "w",
        "suffix": ".html",
        "prefix": "weather_briefing_Bay Area_",
        "delete": False,
        "encoding": "utf-8",
    }
    assert temp_file.closed is True
    assert world.opened == [f"file://{TEMP_PATH}"]
    assert briefing.toasts == [
        ("Opened in browser: weather_briefing_Bay Area_abc123.html", {})
    ]


def test_print_grouping_without_requested_airports(world: World) -> None:
    page = run_print(world, Briefing(), dict(FETCHED))

    assert page_lines(page) == AREAS_ONLY_PAGE


def test_print_requested_airport_without_weather_keeps_the_header(
    world: World,
) -> None:
    page = run_print(world, Briefing(primary=["KZZZ"]), dict(FETCHED))

    assert page_lines(page) == [
        *AREAS_ONLY_PAGE[:5],
        "━━━ REQUESTED ━━━",
        "",
        *AREAS_ONLY_PAGE[5:],
    ]


def test_print_flight_route(world: World) -> None:
    briefing = Briefing(
        ["KLAX", "KZZZ", "KOAK", "KHAF", "KSFO"], primary=["KLAX", "KSFO"], route=True
    )

    page = run_print(world, briefing, {**FETCHED, "KOAK": {}})

    assert page_lines(page) == ROUTE_PAGE


def test_print_unknown_categories_only(world: World) -> None:
    page = run_print(world, Briefing(), {"KNOP": FETCHED["KNOP"]})

    assert page_lines(page) == UNKNOWN_ONLY_PAGE


def test_print_areas_skip_empty_ones(
    world: World, fake_clusterer: type[FakeClusterer]
) -> None:
    weather = dict(FETCHED)
    briefing = Briefing(primary=["KSFO", "KLAX"])

    page = run_print(world, briefing, weather)

    assert page_lines(page) == FAKE_AREAS_PAGE
    assert briefing.screen.weather_data is weather
    ((seen_weather, _, _),) = fake_clusterer.seen
    assert list(seen_weather) == ["KOAK", "KHAF", "KSMO", "KNOP"]


# Pinned output

FETCHED: dict[str, dict[str, Any]] = {
    "KSFO": {
        "metar": "KSFO 101156Z 28012G22KT 10SM FEW008 BKN015 15/10 A3001",
        "taf": "TAF KSFO 101120Z 1012/1112 28012KT P6SM BKN015 FM101800 29015G25KT P6SM SCT030 TEMPO 1020/1024 3SM BR BKN008",
        "category": "MVFR",
        "color": "#5599ff",
        "visibility": "10SM",
        "visibility_sm": 10.0,
        "ceiling": "BKN015",
        "ceiling_ft": 1500,
        "wind": "28012G22KT",
        "atis": SFO_ATIS,
        "phenomena": [],
        "taf_changes": [
            {
                "type": "FM",
                "time_str": "101800",
                "category": "VFR",
                "visibility_sm": 7,
                "ceiling_ft": None,
                "ceiling_layer": None,
                "wind": "29015G25KT",
                "phenomena": [],
                "trend": "improving",
                "is_improvement": True,
                "is_deterioration": False,
            },
            {
                "type": "TEMPO",
                "time_str": "1020/1024",
                "category": "IFR",
                "visibility_sm": 3.0,
                "ceiling_ft": 800,
                "ceiling_layer": "BKN008",
                "wind": None,
                "phenomena": ["Mist"],
                "trend": "worsening",
                "is_improvement": False,
                "is_deterioration": True,
            },
        ],
        "is_speci": False,
    },
    "KOAK": {
        "metar": "KOAK 101153Z 00000KT 10SM CLR 14/09 A3002",
        "taf": "",
        "category": "VFR",
        "color": "#00ff00",
        "visibility": "10SM",
        "visibility_sm": 10.0,
        "ceiling": None,
        "ceiling_ft": None,
        "wind": "00000KT",
        "atis": None,
        "phenomena": [],
        "taf_changes": [],
        "is_speci": False,
    },
    "KHAF": {
        "metar": "KHAF 101155Z AUTO 2SM BR OVC006 12/12 A3001",
        "taf": "TAF KHAF 101120Z 1012/1112 2SM BR OVC006 FM101600 P6SM SKC",
        "category": "IFR",
        "color": "red",
        "visibility": "2SM",
        "visibility_sm": 2.0,
        "ceiling": "OVC006",
        "ceiling_ft": 600,
        "wind": None,
        "atis": None,
        "phenomena": ["Mist"],
        "taf_changes": [
            {
                "type": "FM",
                "time_str": "101600",
                "category": "VFR",
                "visibility_sm": 7,
                "ceiling_ft": None,
                "ceiling_layer": None,
                "wind": None,
                "phenomena": [],
                "trend": "improving",
                "is_improvement": True,
                "is_deterioration": False,
            }
        ],
        "is_speci": False,
    },
    "KLAX": {
        "metar": "KLAX 101153Z 25008KT 1/2SM FG VV002 13/13 A2998",
        "taf": "",
        "category": "LIFR",
        "color": "magenta",
        "visibility": "1/2SM",
        "visibility_sm": 0.5,
        "ceiling": "VV002",
        "ceiling_ft": 200,
        "wind": "25008KT",
        "atis": LAX_DATIS,
        "phenomena": ["Fog"],
        "taf_changes": [],
        "is_speci": False,
    },
    "KSMO": {
        "metar": "SPECI KSMO 101205Z 26006KT 5SM HZ SCT030 18/09 A2999",
        "taf": "",
        "category": "MVFR",
        "color": "#5599ff",
        "visibility": "5SM",
        "visibility_sm": 5.0,
        "ceiling": None,
        "ceiling_ft": None,
        "wind": "26006KT",
        "atis": None,
        "phenomena": ["Haze"],
        "taf_changes": [],
        "is_speci": True,
    },
    "KNOP": {
        "metar": "",
        "taf": "TAF KNOP 101120Z 1012/1112 27010KT P6SM SKC FM101900 1SM BR OVC004",
        "category": "UNK",
        "color": "white",
        "visibility": None,
        "visibility_sm": None,
        "ceiling": None,
        "ceiling_ft": None,
        "wind": None,
        "atis": None,
        "phenomena": [],
        "taf_changes": [
            {
                "type": "FM",
                "time_str": "101900",
                "category": "LIFR",
                "visibility_sm": 1.0,
                "ceiling_ft": 400,
                "ceiling_layer": "OVC004",
                "wind": None,
                "phenomena": ["Mist"],
                "trend": "worsening",
                "is_improvement": False,
                "is_deterioration": True,
            }
        ],
        "is_speci": False,
    },
}
NO_METAR_FETCHED: dict[str, Any] = {
    "metar": "",
    "taf": "",
    "category": "UNK",
    "color": "white",
    "visibility": None,
    "visibility_sm": None,
    "ceiling": None,
    "ceiling_ft": None,
    "wind": None,
    "atis": None,
    "phenomena": [],
    "taf_changes": [],
    "is_speci": False,
}
GROUPING_SUMMARY = "[magenta]1 LIFR[/magenta] | [red]1 IFR[/red] | [#5599ff]2 MVFR[/#5599ff] | [#00ff00]1 VFR[/#00ff00]"
GROUPING_CONTENT = text(
    "[bold cyan]━━━ REQUESTED ━━━[/bold cyan]",
    "[bold]KSFO[/bold] - San Francisco Intl // [#5599ff bold]MVFR[/#5599ff bold]",
    "  Ceiling: BKN015 | Vis: 10SM | Wind: 28012G22KT",
    "  [green bold]▲[/green bold] TAF FM 101800 [dim](in  6h)[/dim]      : [#00ff00 bold]VFR[/#00ff00 bold] - P6SM |        | 29015G25KT |",
    "  [red bold]▼[/red bold] TAF TEMPO 1020/1024 [dim](in  8h)[/dim]: [red bold]IFR[/red bold] -  3SM | BKN008 |            | Mist",
    "  [dim]SFO ATIS INFO [cyan bold]A[/cyan bold]. [yellow]ILS RWY 28L APCH IN USE[/yellow]. [yellow]DEPG RWY 1L[/yellow].[/dim]",
    "",
    "[bold cyan]━━━ LOS ANGELES / SANTA MONICA AREA ━━━[/bold cyan]",
    "[magenta]1 LIFR[/magenta] | [#5599ff]1 MVFR[/#5599ff]",
    "KLAX - Los Angeles Intl // [magenta bold]LIFR[/magenta bold]",
    "  Ceiling: VV002 | Vis: 1/2SM | Wind: 25008KT",
    "  Weather: Fog",
    "  [dim][#ffaa00](RW)[/#ffaa00] [cyan]DEP:[/cyan] LAX DEP INFO [cyan bold]K[/cyan bold]. [yellow]DEPG RWY 25R[/yellow] 24L.[/dim]",
    "  [dim][#ffaa00](RW)[/#ffaa00] [cyan]ARR:[/cyan] LAX ARR INFO [cyan bold]L[/cyan bold]. [yellow]ILS RWY 24R[/yellow] 25L APCHS IN USE.[/dim]",
    "KSMO - Santa Monica // [#5599ff bold]MVFR[/#5599ff bold] [#ff9900 bold]SPECI[/#ff9900 bold]",
    "  Ceiling: CLR | Vis: 5SM | Wind: 26006KT",
    "  Weather: Haze",
    "",
    "[bold cyan]━━━ OAKLAND / HALF MOON BAY AREA ━━━[/bold cyan]",
    "[red]1 IFR[/red] | [#00ff00]1 VFR[/#00ff00]",
    "KOAK - Oakland Intl // [#00ff00 bold]VFR[/#00ff00 bold]",
    "  Ceiling: CLR | Vis: 10SM | Wind: 00000KT",
    "KHAF - Half Moon Bay // [red bold]IFR[/red bold]",
    "  Ceiling: OVC006 | Vis: 2SM",
    "  Weather: Mist",
    "  [green bold]▲[/green bold] TAF FM 101600 [dim](in  4h)[/dim]: [#00ff00 bold]VFR[/#00ff00 bold] - P6SM",
)
AREAS_ONLY_CONTENT = text(
    "[bold cyan]━━━ LOS ANGELES / SANTA MONICA AREA ━━━[/bold cyan]",
    "[magenta]1 LIFR[/magenta] | [#5599ff]1 MVFR[/#5599ff]",
    "KLAX - Los Angeles Intl // [magenta bold]LIFR[/magenta bold]",
    "  Ceiling: VV002 | Vis: 1/2SM | Wind: 25008KT",
    "  Weather: Fog",
    "  [dim][#ffaa00](RW)[/#ffaa00] [cyan]DEP:[/cyan] LAX DEP INFO [cyan bold]K[/cyan bold]. [yellow]DEPG RWY 25R[/yellow] 24L.[/dim]",
    "  [dim][#ffaa00](RW)[/#ffaa00] [cyan]ARR:[/cyan] LAX ARR INFO [cyan bold]L[/cyan bold]. [yellow]ILS RWY 24R[/yellow] 25L APCHS IN USE.[/dim]",
    "KSMO - Santa Monica // [#5599ff bold]MVFR[/#5599ff bold] [#ff9900 bold]SPECI[/#ff9900 bold]",
    "  Ceiling: CLR | Vis: 5SM | Wind: 26006KT",
    "  Weather: Haze",
    "",
    "[bold cyan]━━━ SAN FRANCISCO / OAKLAND+ AREA ━━━[/bold cyan]",
    "[red]1 IFR[/red] | [#5599ff]1 MVFR[/#5599ff] | [#00ff00]1 VFR[/#00ff00]",
    "KSFO - San Francisco Intl // [#5599ff bold]MVFR[/#5599ff bold]",
    "  Ceiling: BKN015 | Vis: 10SM | Wind: 28012G22KT",
    "  [green bold]▲[/green bold] TAF FM 101800 [dim](in  6h)[/dim]      : [#00ff00 bold]VFR[/#00ff00 bold] - P6SM |        | 29015G25KT |",
    "  [red bold]▼[/red bold] TAF TEMPO 1020/1024 [dim](in  8h)[/dim]: [red bold]IFR[/red bold] -  3SM | BKN008 |            | Mist",
    "  [dim]SFO ATIS INFO [cyan bold]A[/cyan bold]. [yellow]ILS RWY 28L APCH IN USE[/yellow]. [yellow]DEPG RWY 1L[/yellow].[/dim]",
    "KOAK - Oakland Intl // [#00ff00 bold]VFR[/#00ff00 bold]",
    "  Ceiling: CLR | Vis: 10SM | Wind: 00000KT",
    "KHAF - Half Moon Bay // [red bold]IFR[/red bold]",
    "  Ceiling: OVC006 | Vis: 2SM",
    "  Weather: Mist",
    "  [green bold]▲[/green bold] TAF FM 101600 [dim](in  4h)[/dim]: [#00ff00 bold]VFR[/#00ff00 bold] - P6SM",
)
ROUTE_SUMMARY = (
    "[magenta]1 LIFR[/magenta] | [red]1 IFR[/red] | [#5599ff]2 MVFR[/#5599ff]"
)
ROUTE_CONTENT = text(
    "[bold]KLAX[/bold] - Los Angeles Intl // [magenta bold]LIFR[/magenta bold]",
    "  Ceiling: VV002 | Vis: 1/2SM | Wind: 25008KT",
    "  Weather: Fog",
    "  [dim][#ffaa00](RW)[/#ffaa00] [cyan]DEP:[/cyan] LAX DEP INFO [cyan bold]K[/cyan bold]. [yellow]DEPG RWY 25R[/yellow] 24L.[/dim]",
    "  [dim][#ffaa00](RW)[/#ffaa00] [cyan]ARR:[/cyan] LAX ARR INFO [cyan bold]L[/cyan bold]. [yellow]ILS RWY 24R[/yellow] 25L APCHS IN USE.[/dim]",
    "",
    "KHAF - Half Moon Bay // [red bold]IFR[/red bold]",
    "  Ceiling: OVC006 | Vis: 2SM",
    "  Weather: Mist",
    "  [green bold]▲[/green bold] TAF FM 101600 [dim](in  4h)[/dim]: [#00ff00 bold]VFR[/#00ff00 bold] - P6SM",
    "",
    "[bold]KSFO[/bold] - San Francisco Intl // [#5599ff bold]MVFR[/#5599ff bold]",
    "  Ceiling: BKN015 | Vis: 10SM | Wind: 28012G22KT",
    "  [green bold]▲[/green bold] TAF FM 101800 [dim](in  6h)[/dim]      : [#00ff00 bold]VFR[/#00ff00 bold] - P6SM |        | 29015G25KT |",
    "  [red bold]▼[/red bold] TAF TEMPO 1020/1024 [dim](in  8h)[/dim]: [red bold]IFR[/red bold] -  3SM | BKN008 |            | Mist",
    "  [dim]SFO ATIS INFO [cyan bold]A[/cyan bold]. [yellow]ILS RWY 28L APCH IN USE[/yellow]. [yellow]DEPG RWY 1L[/yellow].[/dim]",
)
CARD_KSFO_PRIMARY = text(
    "[bold]KSFO[/bold] - San Francisco Intl // [#5599ff bold]MVFR[/#5599ff bold]",
    "  Ceiling: BKN015 | Vis: 10SM | Wind: 28012G22KT",
    "  [green bold]▲[/green bold] TAF FM 101800 [dim](in  6h)[/dim]      : [#00ff00 bold]VFR[/#00ff00 bold] - P6SM |        | 29015G25KT |",
    "  [red bold]▼[/red bold] TAF TEMPO 1020/1024 [dim](in  8h)[/dim]: [red bold]IFR[/red bold] -  3SM | BKN008 |            | Mist",
    "  [dim]SFO ATIS INFO [cyan bold]A[/cyan bold]. [yellow]ILS RWY 28L APCH IN USE[/yellow]. [yellow]DEPG RWY 1L[/yellow].[/dim]",
)
CARD_EXPECTED: dict[str, str] = {
    "vfr_clear": text(
        "KOAK - Oakland Intl // [#00ff00 bold]VFR[/#00ff00 bold]",
        "  Ceiling: CLR",
    ),
    "mvfr_all_conditions": text(
        "KSFO - San Francisco Intl // [#5599ff bold]MVFR[/#5599ff bold]",
        "  Ceiling: BKN015 | Vis: 10SM | Wind: 28012G22KT",
    ),
    "ifr_visibility_no_wind": text(
        "KHAF - Half Moon Bay // [red bold]IFR[/red bold]",
        "  Ceiling: OVC006 | Vis: 2SM",
    ),
    "lifr_wind_no_visibility": text(
        "KLAX - Los Angeles Intl // [magenta bold]LIFR[/magenta bold]",
        "  Ceiling: VV002 | Wind: 25008KT",
    ),
    "empty_data": text(
        "KNOP - Nopos Field // [white bold]UNK[/white bold]",
        "  Ceiling: CLR",
    ),
    "unknown_category": text(
        "KNOP - Nopos Field // [white bold]XYZ[/white bold]",
        "  Ceiling: CLR",
    ),
    "unknown_airport_name": text(
        "KZZZ - KZZZ // [#00ff00 bold]VFR[/#00ff00 bold]",
        "  Ceiling: CLR",
    ),
    "speci": text(
        "KSMO - Santa Monica // [#5599ff bold]MVFR[/#5599ff bold] [#ff9900 bold]SPECI[/#ff9900 bold]",
        "  Ceiling: CLR",
    ),
    "primary": text(
        "[bold]KSFO[/bold] - San Francisco Intl // [#00ff00 bold]VFR[/#00ff00 bold]",
        "  Ceiling: CLR",
    ),
    "primary_speci": text(
        "[bold]KSFO[/bold] - San Francisco Intl // [red bold]IFR[/red bold] [#ff9900 bold]SPECI[/#ff9900 bold]",
        "  Ceiling: CLR",
    ),
    "phenomena": text(
        "KHAF - Half Moon Bay // [red bold]IFR[/red bold]",
        "  Ceiling: CLR",
        "  Weather: -RA, BR",
    ),
    "phenomena_empty": text(
        "KHAF - Half Moon Bay // [red bold]IFR[/red bold]",
        "  Ceiling: CLR",
    ),
    "taf_stable_only": text(
        "KSFO - San Francisco Intl // [#00ff00 bold]VFR[/#00ff00 bold]",
        "  Ceiling: CLR",
    ),
    "taf_one_improving": text(
        "KSFO - San Francisco Intl // [#5599ff bold]MVFR[/#5599ff bold]",
        "  Ceiling: CLR",
        "  [green bold]▲[/green bold] TAF FM 101800 [dim](in  6h)[/dim]: [#00ff00 bold]VFR[/#00ff00 bold] - P6SM | SCT030 | 29015G25KT",
    ),
    "taf_one_worsening_tempo": text(
        "KSFO - San Francisco Intl // [#00ff00 bold]VFR[/#00ff00 bold]",
        "  Ceiling: CLR",
        "  [red bold]▼[/red bold] TAF TEMPO 1020/1024 [dim](in  8h)[/dim]: [red bold]IFR[/red bold] - 3SM | BKN008 | BR",
    ),
    "taf_first_two_significant": text(
        "KSFO - San Francisco Intl // [#5599ff bold]MVFR[/#5599ff bold]",
        "  Ceiling: CLR",
        "  [red bold]▼[/red bold] TAF TEMPO 1020/1024 [dim](in  8h)[/dim]: [red bold]IFR[/red bold] -  3SM | BKN008 |            | BR",
        "  [green bold]▲[/green bold] TAF FM 101800 [dim](in  6h)[/dim]      : [#00ff00 bold]VFR[/#00ff00 bold] - P6SM | SCT030 | 29015G25KT |",
    ),
    "taf_visibility_p6sm_and_fraction": text(
        "KSFO - San Francisco Intl // [#5599ff bold]MVFR[/#5599ff bold]",
        "  Ceiling: CLR",
        "  [green bold]▲[/green bold] TAF FM 101800 [dim](in  6h)[/dim]: [#00ff00 bold]VFR[/#00ff00 bold] -  P6SM",
        "  [red bold]▼[/red bold] TAF FM 102100 [dim](in  9h)[/dim]: [red bold]IFR[/red bold] - 2.5SM",
    ),
    "taf_visibility_whole_and_rounded": text(
        "KSFO - San Francisco Intl // [#5599ff bold]MVFR[/#5599ff bold]",
        "  Ceiling: CLR",
        "  [green bold]▲[/green bold] TAF FM 101800 [dim](in  6h)[/dim]: [#00ff00 bold] VFR[/#00ff00 bold] -   3SM",
        "  [red bold]▼[/red bold] TAF FM 102100 [dim](in  9h)[/dim]: [magenta bold]LIFR[/magenta bold] - 1.2SM",
    ),
    "taf_no_details": text(
        "KSFO - San Francisco Intl // [#00ff00 bold]VFR[/#00ff00 bold]",
        "  Ceiling: CLR",
        "  [red bold]▼[/red bold] TAF FM 101800 [dim](in  6h)[/dim]: [#00ff00 bold]VFR[/#00ff00 bold]",
    ),
    "taf_missing_keys": text(
        "KSFO - San Francisco Intl // [#00ff00 bold]VFR[/#00ff00 bold]",
        "  Ceiling: CLR",
        "  [green bold]▲[/green bold] TAF  : [white bold]UNK[/white bold]",
    ),
    "taf_flag_and_trend_disagree": text(
        "KSFO - San Francisco Intl // [#00ff00 bold]VFR[/#00ff00 bold]",
        "  Ceiling: CLR",
        "  [green bold]▲[/green bold] TAF FM 101800 [dim](in  6h)[/dim]: [#00ff00 bold]VFR[/#00ff00 bold] - 18005KT",
    ),
    "taf_unparsable_and_past_times": text(
        "KSFO - San Francisco Intl // [#00ff00 bold]VFR[/#00ff00 bold]",
        "  Ceiling: CLR",
        "  [red bold]▼[/red bold] TAF FM garbage         : [#00ff00 bold]VFR[/#00ff00 bold] -         | TSRA, BR",
        "  [green bold]▲[/green bold] TAF FM 100600 [dim]( 6h ago)[/dim]: [#00ff00 bold]VFR[/#00ff00 bold] - 00000KT |",
    ),
    "taf_column_present_in_one_row": text(
        "KSFO - San Francisco Intl // [#00ff00 bold]VFR[/#00ff00 bold]",
        "  Ceiling: CLR",
        "  [red bold]▼[/red bold] TAF FM 101800 [dim](in  6h)[/dim]: [#5599ff bold]MVFR[/#5599ff bold] - BKN025 |",
        "  [green bold]▲[/green bold] TAF FM 102200 [dim](in 10h)[/dim]: [#00ff00 bold] VFR[/#00ff00 bold] -        | 27010KT",
    ),
    "atis_none": text(
        "KSFO - San Francisco Intl // [#00ff00 bold]VFR[/#00ff00 bold]",
        "  Ceiling: CLR",
    ),
    "atis_empty": text(
        "KSFO - San Francisco Intl // [#00ff00 bold]VFR[/#00ff00 bold]",
        "  Ceiling: CLR",
    ),
    "atis_one_vatsim": text(
        "KSFO - San Francisco Intl // [#00ff00 bold]VFR[/#00ff00 bold]",
        "  Ceiling: CLR",
        "  [dim]SFO ATIS INFO [cyan bold]A[/cyan bold]. [yellow]ILS RWY 28L APCH IN USE[/yellow]. [yellow]DEPG RWY 1L[/yellow].[/dim]",
    ),
    "atis_rw_departure_and_arrival": text(
        "KLAX - Los Angeles Intl // [magenta bold]LIFR[/magenta bold]",
        "  Ceiling: CLR",
        "  [dim][#ffaa00](RW)[/#ffaa00] [cyan]DEP:[/cyan] LAX DEP INFO [cyan bold]K[/cyan bold]. [yellow]DEPG RWY 25R[/yellow] 24L.[/dim]",
        "  [dim][#ffaa00](RW)[/#ffaa00] [cyan]ARR:[/cyan] LAX ARR INFO [cyan bold]L[/cyan bold]. "
        "[yellow]ILS RWY 24R[/yellow] 25L APCHS IN USE.[/dim]",
    ),
    "atis_blank_text_skipped_and_defaults": text(
        "KOAK - Oakland Intl // [#00ff00 bold]VFR[/#00ff00 bold]",
        "  Ceiling: CLR",
        "  [dim]RWY 30 IN USE.[/dim]",
    ),
    "everything": text(
        "[bold]KSFO[/bold] - San Francisco Intl // [red bold]IFR[/red bold] [#ff9900 bold]SPECI[/#ff9900 bold]",
        "  Ceiling: OVC008 | Vis: 1 1/2SM | Wind: 18015G30KT",
        "  Weather: +RA, BR",
        "  [red bold]▼[/red bold] TAF TEMPO 1020/1024 [dim](in  8h)[/dim]: [red bold]IFR[/red bold] -  3SM | BKN008 |            | BR",
        "  [green bold]▲[/green bold] TAF FM 101800 [dim](in  6h)[/dim]      : [#00ff00 bold]VFR[/#00ff00 bold] - P6SM | SCT030 | 29015G25KT |",
        "  [dim]SFO ATIS INFO [cyan bold]A[/cyan bold]. [yellow]ILS RWY 28L APCH IN USE[/yellow]. [yellow]DEPG RWY 1L[/yellow].[/dim]",
        "  [dim][#ffaa00](RW)[/#ffaa00] [cyan]DEP:[/cyan] LAX DEP INFO [cyan bold]K[/cyan bold]. [yellow]DEPG RWY 25R[/yellow] 24L.[/dim]",
        "  [dim][#ffaa00](RW)[/#ffaa00] [cyan]ARR:[/cyan] LAX ARR INFO [cyan bold]L[/cyan bold]. "
        "[yellow]ILS RWY 24R[/yellow] 25L APCHS IN USE.[/dim]",
    ),
}
FULL_HTML = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>

body {
    color: #000000;
    background-color: #ffffff;
}
pre { margin: 0; padding: 0; white-space: pre-wrap; word-wrap: break-word; max-width: 100ch; }
body { margin: 20px; }
</style>
</head>
<body>
<pre style="font-family:Menlo,'DejaVu Sans Mono',consolas,'Courier New',monospace"><code style="font-family:inherit"><span style="font-weight: bold">Weather Briefing: Bay Area</span>
Generated: <span style="color: #008080; text-decoration-color: #008080; font-weight: bold">2026</span>-<span style="color: #008080; text-decoration-color: #008080; font-weight: bold">10</span>-<span style="color: #008080; text-decoration-color: #008080; font-weight: bold">10</span> 1200Z

<span style="color: #800080; text-decoration-color: #800080; font-weight: bold">1</span><span style="color: #800080; text-decoration-color: #800080"> LIFR</span> | <span style="color: #800000; text-decoration-color: #800000; font-weight: bold">1</span><span style="color: #800000; text-decoration-color: #800000"> IFR</span> | <span style="color: #5599ff; text-decoration-color: #5599ff; font-weight: bold">2</span><span style="color: #5599ff; text-decoration-color: #5599ff"> MVFR</span> | <span style="color: #00ff00; text-decoration-color: #00ff00; font-weight: bold">1</span><span style="color: #00ff00; text-decoration-color: #00ff00"> VFR</span>

<span style="color: #008080; text-decoration-color: #008080; font-weight: bold">━━━ REQUESTED ━━━</span>
<span style="font-weight: bold">KSFO</span> - San Francisco Intl <span style="color: #800080; text-decoration-color: #800080">//</span> <span style="color: #5599ff; text-decoration-color: #5599ff; font-weight: bold">MVFR</span>
  Ceiling: BKN015 | Vis: 10SM | Wind: 28012G22KT
  <span style="color: #008000; text-decoration-color: #008000; font-weight: bold">▲</span> TAF FM <span style="color: #008080; text-decoration-color: #008080; font-weight: bold">101800</span> <span style="color: #7f7f7f; text-decoration-color: #7f7f7f; font-weight: bold">(</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f">in  6h</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f; font-weight: bold">)</span>      : <span style="color: #00ff00; text-decoration-color: #00ff00; font-weight: bold">VFR</span> - P6SM |        | 29015G25KT |
  <span style="color: #800000; text-decoration-color: #800000; font-weight: bold">▼</span> TAF TEMPO <span style="color: #008080; text-decoration-color: #008080; font-weight: bold">1020</span>/<span style="color: #008080; text-decoration-color: #008080; font-weight: bold">1024</span> <span style="color: #7f7f7f; text-decoration-color: #7f7f7f; font-weight: bold">(</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f">in  8h</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f; font-weight: bold">)</span>: <span style="color: #800000; text-decoration-color: #800000; font-weight: bold">IFR</span> -  3SM | BKN008 |            | Mist
  <span style="color: #7f7f7f; text-decoration-color: #7f7f7f">SFO ATIS INFO </span><span style="color: #7fbfbf; text-decoration-color: #7fbfbf; font-weight: bold">A</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f">. </span><span style="color: #bfbf7f; text-decoration-color: #bfbf7f">ILS RWY 28L APCH IN USE</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f">. </span><span style="color: #bfbf7f; text-decoration-color: #bfbf7f">DEPG RWY 1L</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f">.</span>

<span style="color: #008080; text-decoration-color: #008080; font-weight: bold">━━━ LOS ANGELES / SANTA MONICA AREA ━━━</span>
<span style="color: #800080; text-decoration-color: #800080; font-weight: bold">1</span><span style="color: #800080; text-decoration-color: #800080"> LIFR</span> | <span style="color: #5599ff; text-decoration-color: #5599ff; font-weight: bold">1</span><span style="color: #5599ff; text-decoration-color: #5599ff"> MVFR</span>
KLAX - Los Angeles Intl <span style="color: #800080; text-decoration-color: #800080">//</span> <span style="color: #800080; text-decoration-color: #800080; font-weight: bold">LIFR</span>
  Ceiling: VV002 | Vis: <span style="color: #008080; text-decoration-color: #008080; font-weight: bold">1</span>/2SM | Wind: 25008KT
  Weather: Fog
  <span style="color: #ffd47f; text-decoration-color: #ffd47f; font-weight: bold">(</span><span style="color: #ffd47f; text-decoration-color: #ffd47f">RW</span><span style="color: #ffd47f; text-decoration-color: #ffd47f; font-weight: bold">)</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f"> </span><span style="color: #7fbfbf; text-decoration-color: #7fbfbf">DEP:</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f"> LAX DEP INFO </span><span style="color: #7fbfbf; text-decoration-color: #7fbfbf; font-weight: bold">K</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f">. </span><span style="color: #bfbf7f; text-decoration-color: #bfbf7f">DEPG RWY 25R</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f"> 24L.</span>
  <span style="color: #ffd47f; text-decoration-color: #ffd47f; font-weight: bold">(</span><span style="color: #ffd47f; text-decoration-color: #ffd47f">RW</span><span style="color: #ffd47f; text-decoration-color: #ffd47f; font-weight: bold">)</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f"> </span><span style="color: #7fbfbf; text-decoration-color: #7fbfbf">ARR:</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f"> LAX ARR INFO </span><span style="color: #7fbfbf; text-decoration-color: #7fbfbf; font-weight: bold">L</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f">. </span><span style="color: #bfbf7f; text-decoration-color: #bfbf7f">ILS RWY 24R</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f"> 25L APCHS IN USE.</span>
KSMO - Santa Monica <span style="color: #800080; text-decoration-color: #800080">//</span> <span style="color: #5599ff; text-decoration-color: #5599ff; font-weight: bold">MVFR</span> <span style="color: #ff9900; text-decoration-color: #ff9900; font-weight: bold">SPECI</span>
  Ceiling: CLR | Vis: 5SM | Wind: 26006KT
  Weather: Haze

<span style="color: #008080; text-decoration-color: #008080; font-weight: bold">━━━ OAKLAND / HALF MOON BAY AREA ━━━</span>
<span style="color: #800000; text-decoration-color: #800000; font-weight: bold">1</span><span style="color: #800000; text-decoration-color: #800000"> IFR</span> | <span style="color: #00ff00; text-decoration-color: #00ff00; font-weight: bold">1</span><span style="color: #00ff00; text-decoration-color: #00ff00"> VFR</span>
KOAK - Oakland Intl <span style="color: #800080; text-decoration-color: #800080">//</span> <span style="color: #00ff00; text-decoration-color: #00ff00; font-weight: bold">VFR</span>
  Ceiling: CLR | Vis: 10SM | Wind: 00000KT
KHAF - Half Moon Bay <span style="color: #800080; text-decoration-color: #800080">//</span> <span style="color: #800000; text-decoration-color: #800000; font-weight: bold">IFR</span>
  Ceiling: OVC006 | Vis: 2SM
  Weather: Mist
  <span style="color: #008000; text-decoration-color: #008000; font-weight: bold">▲</span> TAF FM <span style="color: #008080; text-decoration-color: #008080; font-weight: bold">101600</span> <span style="color: #7f7f7f; text-decoration-color: #7f7f7f; font-weight: bold">(</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f">in  4h</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f; font-weight: bold">)</span>: <span style="color: #00ff00; text-decoration-color: #00ff00; font-weight: bold">VFR</span> - P6SM

</code></pre>
</body>
</html>
"""
AREAS_ONLY_PAGE = [
    "Weather Briefing: Bay Area",
    "Generated: 2026-10-10 1200Z",
    "",
    "1 LIFR | 1 IFR | 2 MVFR | 1 VFR",
    "",
    "━━━ LOS ANGELES / SANTA MONICA AREA ━━━",
    "1 LIFR | 1 MVFR",
    "KLAX - Los Angeles Intl // LIFR",
    "  Ceiling: VV002 | Vis: 1/2SM | Wind: 25008KT",
    "  Weather: Fog",
    "  (RW) DEP: LAX DEP INFO K. DEPG RWY 25R 24L.",
    "  (RW) ARR: LAX ARR INFO L. ILS RWY 24R 25L APCHS IN USE.",
    "KSMO - Santa Monica // MVFR SPECI",
    "  Ceiling: CLR | Vis: 5SM | Wind: 26006KT",
    "  Weather: Haze",
    "",
    "━━━ SAN FRANCISCO / OAKLAND+ AREA ━━━",
    "1 IFR | 1 MVFR | 1 VFR",
    "KSFO - San Francisco Intl // MVFR",
    "  Ceiling: BKN015 | Vis: 10SM | Wind: 28012G22KT",
    "  ▲ TAF FM 101800 (in  6h)      : VFR - P6SM |        | 29015G25KT |",
    "  ▼ TAF TEMPO 1020/1024 (in  8h): IFR -  3SM | BKN008 |            | Mist",
    "  SFO ATIS INFO A. ILS RWY 28L APCH IN USE. DEPG RWY 1L.",
    "KOAK - Oakland Intl // VFR",
    "  Ceiling: CLR | Vis: 10SM | Wind: 00000KT",
    "KHAF - Half Moon Bay // IFR",
    "  Ceiling: OVC006 | Vis: 2SM",
    "  Weather: Mist",
    "  ▲ TAF FM 101600 (in  4h): VFR - P6SM",
    "",
    "",
]
ROUTE_PAGE = [
    "Weather Briefing: Bay Area",
    "Generated: 2026-10-10 1200Z",
    "",
    "1 LIFR | 1 IFR | 2 MVFR",
    "",
    "KLAX - Los Angeles Intl // LIFR",
    "  Ceiling: VV002 | Vis: 1/2SM | Wind: 25008KT",
    "  Weather: Fog",
    "  (RW) DEP: LAX DEP INFO K. DEPG RWY 25R 24L.",
    "  (RW) ARR: LAX ARR INFO L. ILS RWY 24R 25L APCHS IN USE.",
    "",
    "KHAF - Half Moon Bay // IFR",
    "  Ceiling: OVC006 | Vis: 2SM",
    "  Weather: Mist",
    "  ▲ TAF FM 101600 (in  4h): VFR - P6SM",
    "",
    "KSFO - San Francisco Intl // MVFR",
    "  Ceiling: BKN015 | Vis: 10SM | Wind: 28012G22KT",
    "  ▲ TAF FM 101800 (in  6h)      : VFR - P6SM |        | 29015G25KT |",
    "  ▼ TAF TEMPO 1020/1024 (in  8h): IFR -  3SM | BKN008 |            | Mist",
    "  SFO ATIS INFO A. ILS RWY 28L APCH IN USE. DEPG RWY 1L.",
    "",
    "",
]
UNKNOWN_ONLY_PAGE = [
    "Weather Briefing: Bay Area",
    "Generated: 2026-10-10 1200Z",
    "",
    "",
    "",
]
FAKE_AREAS_CONTENT = text(
    "[bold cyan]━━━ REQUESTED ━━━[/bold cyan]",
    "[bold]KSFO[/bold] - San Francisco Intl // [#5599ff bold]MVFR[/#5599ff bold]",
    "  Ceiling: BKN015 | Vis: 10SM | Wind: 28012G22KT",
    "  [green bold]▲[/green bold] TAF FM 101800 [dim](in  6h)[/dim]      : [#00ff00 bold]VFR[/#00ff00 bold] - P6SM |        | 29015G25KT |",
    "  [red bold]▼[/red bold] TAF TEMPO 1020/1024 [dim](in  8h)[/dim]: [red bold]IFR[/red bold] -  3SM | BKN008 |            | Mist",
    "  [dim]SFO ATIS INFO [cyan bold]A[/cyan bold]. [yellow]ILS RWY 28L APCH IN USE[/yellow]. [yellow]DEPG RWY 1L[/yellow].[/dim]",
    "[bold]KLAX[/bold] - Los Angeles Intl // [magenta bold]LIFR[/magenta bold]",
    "  Ceiling: VV002 | Vis: 1/2SM | Wind: 25008KT",
    "  Weather: Fog",
    "  [dim][#ffaa00](RW)[/#ffaa00] [cyan]DEP:[/cyan] LAX DEP INFO [cyan bold]K[/cyan bold]. [yellow]DEPG RWY 25R[/yellow] 24L.[/dim]",
    "  [dim][#ffaa00](RW)[/#ffaa00] [cyan]ARR:[/cyan] LAX ARR INFO [cyan bold]L[/cyan bold]. [yellow]ILS RWY 24R[/yellow] 25L APCHS IN USE.[/dim]",
    "",
    "[bold cyan]━━━ UNKNOWN AREA ━━━[/bold cyan]",
    "KNOP - Nopos Field // [white bold]UNK[/white bold]",
    "  Ceiling: CLR",
    "  [red bold]▼[/red bold] TAF FM 101900 [dim](in  7h)[/dim]: [magenta bold]LIFR[/magenta bold] - 1SM | OVC004 | Mist",
    "",
    "[bold cyan]━━━ BAY AREA ━━━[/bold cyan]",
    "[red]1 IFR[/red] | [#00ff00]1 VFR[/#00ff00]",
    "KOAK - Oakland Intl // [#00ff00 bold]VFR[/#00ff00 bold]",
    "  Ceiling: CLR | Vis: 10SM | Wind: 00000KT",
    "KHAF - Half Moon Bay // [red bold]IFR[/red bold]",
    "  Ceiling: OVC006 | Vis: 2SM",
    "  Weather: Mist",
    "  [green bold]▲[/green bold] TAF FM 101600 [dim](in  4h)[/dim]: [#00ff00 bold]VFR[/#00ff00 bold] - P6SM",
)
FAKE_AREAS_PAGE = [
    "Weather Briefing: Bay Area",
    "Generated: 2026-10-10 1200Z",
    "",
    "1 LIFR | 1 IFR | 2 MVFR | 1 VFR",
    "",
    "━━━ REQUESTED ━━━",
    "KSFO - San Francisco Intl // MVFR",
    "  Ceiling: BKN015 | Vis: 10SM | Wind: 28012G22KT",
    "  ▲ TAF FM 101800 (in  6h)      : VFR - P6SM |        | 29015G25KT |",
    "  ▼ TAF TEMPO 1020/1024 (in  8h): IFR -  3SM | BKN008 |            | Mist",
    "  SFO ATIS INFO A. ILS RWY 28L APCH IN USE. DEPG RWY 1L.",
    "KLAX - Los Angeles Intl // LIFR",
    "  Ceiling: VV002 | Vis: 1/2SM | Wind: 25008KT",
    "  Weather: Fog",
    "  (RW) DEP: LAX DEP INFO K. DEPG RWY 25R 24L.",
    "  (RW) ARR: LAX ARR INFO L. ILS RWY 24R 25L APCHS IN USE.",
    "",
    "━━━ UNKNOWN AREA ━━━",
    "KNOP - Nopos Field // UNK",
    "  Ceiling: CLR",
    "  ▼ TAF FM 101900 (in  7h): LIFR - 1SM | OVC004 | Mist",
    "",
    "━━━ BAY AREA ━━━",
    "1 IFR | 1 VFR",
    "KOAK - Oakland Intl // VFR",
    "  Ceiling: CLR | Vis: 10SM | Wind: 00000KT",
    "KHAF - Half Moon Bay // IFR",
    "  Ceiling: OVC006 | Vis: 2SM",
    "  Weather: Mist",
    "  ▲ TAF FM 101600 (in  4h): VFR - P6SM",
    "",
    "",
]
