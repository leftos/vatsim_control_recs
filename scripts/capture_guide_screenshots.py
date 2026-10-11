"""Capture the user guide's screenshots as SVGs from fixture traffic and frozen weather.

Usage:
    uv run python scripts/capture_guide_screenshots.py [--record-weather]
        [--weather data/guide/weather-snapshot.json] [--out docs/images/guide]
        [--only NAME]

The traffic is data/test-vatsim-data.json, the weather the committed snapshot
data/guide/weather-snapshot.json, and the clock is frozen at the snapshot's
recording time, so a recapture after a UI change differs only where the UI did.

--record-weather drives the same screens against the live weather services
(METARs, TAFs, weather.gov wind and real-world D-ATIS), keeps every answer and
writes the snapshot instead of images; it is the only mode that touches the
network. Every other run answers each weather request from the snapshot, and
an airport the snapshot lacks answers as "no data".

The app runs headless at 140x40 with animations off, in a temporary user data
directory holding copies of the real CIFP, NASR and runway caches and one ★
grouping, so the real user data directory is never written.
"""

import argparse
import asyncio
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

from textual.pilot import Pilot
from textual.screen import Screen
from textual.widgets import Input, OptionList, TabbedContent
from textual.widgets.option_list import OptionDoesNotExist

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The project modules resolve the user data directory when called, not on
# import, so they can load before main() points it at a temporary directory.
from backend import (  # noqa: E402
    analyze_flights_data,
    ensure_cifp_data,
    ensure_runway_data,
)
from backend.cache.manager import load_aircraft_approach_speeds  # noqa: E402
from backend.config import constants as backend_constants  # noqa: E402
from backend.data import datis_api, vatsim_api, weather  # noqa: E402
from common.paths import (  # noqa: E402
    ensure_user_directories,
    get_user_data_dir,
    get_user_favorites_file,
)
from ui import config as ui_config  # noqa: E402
from ui.app import VATSIMControlApp  # noqa: E402
from ui.modals import (  # noqa: E402
    CommandPaletteScreen,
    DiversionModal,
    FlightBoardScreen,
    FlightInfoScreen,
    FlightWeatherBriefingScreen,
    GoToScreen,
    HelpScreen,
    MetarInfoScreen,
    TrackedAirportsModal,
    VfrAlternativesScreen,
    WeatherBriefingScreen,
    WindInfoScreen,
)
from ui.modals.grouping_builder import GroupingBuilderScreen  # noqa: E402
from ui.modals.route_weather import RouteWeatherScreen  # noqa: E402
from ui.utils import airport_grouping_sort_key  # noqa: E402
from widgets.split_flap_datatable import SplitFlapDataTable  # noqa: E402

SCREEN_SIZE = (140, 40)
TRAFFIC_FILE = REPO_ROOT / "data" / "test-vatsim-data.json"
DEFAULT_WEATHER = REPO_ROOT / "data" / "guide" / "weather-snapshot.json"
DEFAULT_OUT = REPO_ROOT / "docs" / "images" / "guide"

# The filter: the busiest US facility grouping in the traffic fixture
GROUPING = "N90 New York Combined"
BUSIEST_AIRPORT = "KJFK"
# An airborne KJFK arrival from KLAX with a filed route and alternate
FLIGHT = "AAL1144"
# The one ★ grouping: a nested grouping plus two airports with filters
MY_GROUPING = "JFK Ops"
MY_GROUPING_ENTRY: dict[str, Any] = {
    "airports": ["N90 Kennedy", "KEWR", "KLGA"],
    "filters": {"KEWR": "arr", "KLGA": "dep"},
}
GOTO_QUERY = "JFK"
PICK_QUERY = "N90"
PICKS = ("N90 Kennedy", "N90 LaGuardia")

# Copied from the real user cache directory so no reference data downloads
REFERENCE_CACHES = ("cifp", "navaids", "runways.csv", "runways_metadata.txt")

# The app packages whose clock is frozen. The reference-data modules pick the
# AIRAC cycle of their cache from today's date, so they keep the real clock.
CLOCK_PACKAGES = frozenset({"backend", "ui", "widgets"})
REAL_CLOCK_MODULES = frozenset(
    {"backend.data.cifp", "backend.data.navaids", "backend.data.runways"}
)

HASH_SEED = "0"

# The feed's people, named by placeholders in every captured screen
ANONYMOUS_NAMES = {
    "pilots": "Sample Pilot",
    "prefiles": "Sample Pilot",
    "controllers": "Sample Controller",
    "atis": "Sample Controller",
}

SETTLE_TIMEOUT_SECONDS = 90.0
POLL_SECONDS = 0.1
# Consecutive identical renders, one poll apart, that count as settled
STABLE_POLLS = 5

_ISO_UTC = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?Z")


def _parse_utc(text: str) -> datetime:
    """Read a VATSIM-style UTC time ("2025-10-25T20:03:01.9955791Z")."""
    match = _ISO_UTC.fullmatch(text)
    if match is None:
        raise ValueError(f"Not a UTC ISO 8601 time: {text!r}")
    return datetime.fromisoformat(f"{match[1]}+00:00")


def _format_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- the weather snapshot ------------------------------------------------------


@dataclass
class WeatherSnapshot:
    """The weather the screens show, as recorded from the live services."""

    recorded_at: datetime
    metars: dict[str, str] = field(default_factory=dict)
    tafs: dict[str, str] = field(default_factory=dict)
    minute_winds: dict[str, str] = field(default_factory=dict)
    datis: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "WeatherSnapshot":
        """Read a snapshot file.

        Raises:
            FileNotFoundError: The file does not exist yet.
        """
        if not path.is_file():
            raise FileNotFoundError(
                f"No weather snapshot at {path}; run with --record-weather first"
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            recorded_at=_parse_utc(raw["recorded_at"]),
            metars=raw.get("metars", {}),
            tafs=raw.get("tafs", {}),
            minute_winds=raw.get("minute_winds", {}),
            datis=raw.get("datis", {}),
        )

    def write(self, path: Path) -> None:
        """Write the snapshot as sorted, LF-terminated JSON."""
        payload = {
            "recorded_at": _format_utc(self.recorded_at),
            "metars": self.metars,
            "tafs": self.tafs,
            "minute_winds": self.minute_winds,
            "datis": self.datis,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False)
        path.write_text(text + "\n", encoding="utf-8", newline="\n")


def _in_bbox(icao: str, bbox: tuple[float, float, float, float]) -> bool:
    airport = (ui_config.UNIFIED_AIRPORT_DATA or {}).get(icao) or {}
    lat, lon = airport.get("latitude"), airport.get("longitude")
    if lat is None or lon is None:
        return False
    min_lat, min_lon, max_lat, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


class SnapshotWeather:
    """Answers the app's weather requests from a snapshot, never the network."""

    def __init__(self, snapshot: WeatherSnapshot) -> None:
        self.snapshot = snapshot

    def metar_aviationweather(self, icao: str) -> tuple[str | None, bool]:
        return self.snapshot.metars.get(icao, ""), True

    def metar_vatsim(self, icao: str) -> tuple[str, bool]:
        return "", True

    def taf(self, icao: str) -> str:
        return self.snapshot.tafs.get(icao, "")

    def minute_wind(self, icao: str) -> str:
        return self.snapshot.minute_winds.get(icao, "")

    def datis(self, icao: str, timeout: int = 10) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self.snapshot.datis.get(icao, [])]

    def bbox(
        self, bbox: tuple[float, float, float, float], include_taf: bool, timeout: int
    ) -> weather.BboxFetchResult:
        """Every snapshot station inside the box, as one successful response."""
        stations = set(self.snapshot.metars)
        if include_taf:
            stations |= set(self.snapshot.tafs)
        inside = sorted(icao for icao in stations if _in_bbox(icao, bbox))
        metars = {
            i: self.snapshot.metars[i] for i in inside if i in self.snapshot.metars
        }
        tafs = {i: self.snapshot.tafs[i] for i in inside if i in self.snapshot.tafs}
        return weather.BboxFetchResult(
            metars, tafs if include_taf else {}, True, len(inside), False
        )


class WeatherRecorder:
    """Passes the app's weather requests to the live services and keeps each answer."""

    def __init__(self, recorded_at: datetime) -> None:
        self.snapshot = WeatherSnapshot(recorded_at)
        # Taken before any patching, so these are the live fetches
        self._live = {name: getattr(weather, name) for name in WEATHER_LEAVES}
        self._live_datis = datis_api.get_datis_for_airport

    def metar_aviationweather(self, icao: str) -> tuple[str | None, bool]:
        metar, answered = self._live["_fetch_metar_from_aviationweather"](icao)
        if metar:
            self.snapshot.metars[icao] = metar
        return metar, answered

    def metar_vatsim(self, icao: str) -> tuple[str, bool]:
        metar, answered = self._live["_fetch_metar_from_vatsim"](icao)
        if metar:
            self.snapshot.metars[icao] = metar
        return metar, answered

    def taf(self, icao: str) -> str:
        taf = self._live["_fetch_taf_text"](icao)
        if taf and not taf.startswith(("No TAF", "Error")):
            self.snapshot.tafs[icao] = taf
        return taf

    def minute_wind(self, icao: str) -> str:
        wind = self._live["_fetch_minute_wind"](icao)
        if wind:
            self.snapshot.minute_winds[icao] = wind
        return wind

    def datis(self, icao: str, timeout: int = 10) -> list[dict[str, Any]]:
        entries = self._live_datis(icao, timeout)
        if entries:
            self.snapshot.datis[icao] = entries
        return entries

    def bbox(
        self, bbox: tuple[float, float, float, float], include_taf: bool, timeout: int
    ) -> weather.BboxFetchResult:
        result = self._live["_fetch_weather_bbox_result"](bbox, include_taf, timeout)
        self.snapshot.metars.update(result.metars)
        self.snapshot.tafs.update(result.tafs)
        return result


# The weather module's network leaves and the source method answering each
WEATHER_LEAVES = {
    "_fetch_metar_from_aviationweather": "metar_aviationweather",
    "_fetch_metar_from_vatsim": "metar_vatsim",
    "_fetch_taf_text": "taf",
    "_fetch_minute_wind": "minute_wind",
    "_fetch_weather_bbox_result": "bbox",
}

WeatherSource = SnapshotWeather | WeatherRecorder


# --- traffic, clock and network patches --------------------------------------


def _shifted(text: str, delta: timedelta) -> str:
    """A VATSIM UTC time moved by delta, keeping its fraction digits as they were."""
    match = _ISO_UTC.fullmatch(text)
    if match is None:
        return text
    moment = datetime.fromisoformat(f"{match[1]}+00:00") + delta
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + (match[2] or "") + "Z"


def _load_traffic(now: datetime) -> str:
    """The traffic fixture as JSON text, its times moved so the feed is taken at now."""
    data = json.loads(TRAFFIC_FILE.read_text(encoding="utf-8"))
    general = data["general"]
    feed_time = _parse_utc(general["update_timestamp"])
    delta = timedelta(seconds=round((now - feed_time).total_seconds()))
    general["update_timestamp"] = _shifted(general["update_timestamp"], delta)
    general["update"] = (feed_time + delta).strftime("%Y%m%d%H%M%S")
    for section in ("pilots", "controllers", "atis", "prefiles"):
        for entry in data.get(section, []):
            for key in ("logon_time", "last_updated"):
                if key in entry:
                    entry[key] = _shifted(entry[key], delta)
    _anonymize(data)
    return json.dumps(data)


def _anonymize(data: dict[str, Any]) -> None:
    """Replace the real people's names in the feed, so no image publishes one."""
    for section, placeholder in ANONYMOUS_NAMES.items():
        for entry in data.get(section, []):
            if "name" in entry:
                entry["name"] = placeholder


def _app_modules() -> list[ModuleType]:
    """The loaded modules of the app's own packages."""
    return [
        module
        for name, module in list(sys.modules.items())
        if module is not None and name.split(".")[0] in CLOCK_PACKAGES
    ]


def _patch_everywhere(
    stack: ExitStack, original: Callable[..., Any], replacement: Callable[..., Any]
) -> None:
    """Replace a function in every app module holding it under any name."""
    for module in _app_modules():
        for name, value in list(vars(module).items()):
            if value is original:
                stack.enter_context(patch.object(module, name, replacement))


def _frozen_datetime(moment: datetime) -> type[datetime]:
    """A datetime class whose now() is always the given moment."""

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            if tz is None:
                return moment.astimezone().replace(tzinfo=None)
            return moment.astimezone(tz)

    return FrozenDatetime


def _freeze_clock(stack: ExitStack, moment: datetime) -> None:
    frozen = _frozen_datetime(moment)
    for module in _app_modules():
        if module.__name__ in REAL_CLOCK_MODULES:
            continue
        if getattr(module, "datetime", None) is datetime:
            stack.enter_context(patch.object(module, "datetime", frozen))


def _no_member_stats(cid: int, timeout: int = 5) -> None:
    """The VATSIM member statistics are left out: they are live, not fixture data."""
    return None


def _patch_network(stack: ExitStack, source: WeatherSource, traffic: str) -> None:
    """Answer the feed from the traffic fixture and every weather fetch from source."""

    def feed(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return json.loads(traffic)

    _patch_everywhere(stack, vatsim_api.download_vatsim_data, feed)
    _patch_everywhere(stack, vatsim_api.get_member_stats, _no_member_stats)
    _patch_everywhere(stack, datis_api.get_datis_for_airport, source.datis)
    for leaf, method in WEATHER_LEAVES.items():
        stack.enter_context(patch.object(weather, leaf, getattr(source, method)))


# --- app setup -----------------------------------------------------------------


def copy_reference_caches(source_dir: Path, user_data_dir: Path) -> None:
    """Copy the CIFP, NASR and runway caches that exist under source_dir/cache."""
    source_cache = source_dir / "cache"
    target_cache = user_data_dir / "cache"
    target_cache.mkdir(parents=True, exist_ok=True)
    for name in REFERENCE_CACHES:
        source = source_cache / name
        if source.is_dir():
            shutil.copytree(source, target_cache / name, dirs_exist_ok=True)
        elif source.is_file():
            shutil.copy2(source, target_cache / name)


def _app_args() -> argparse.Namespace:
    """The app's own command line for the capture: the grouping filter, no animations."""
    # main checks sys.argv for --help when imported, so it loads only after this
    # script has parsed its own arguments
    import main

    return main.build_arg_parser().parse_args(
        ["--groupings", GROUPING, "--disable-animations"]
    )


def _write_my_grouping() -> None:
    """Make favorites.json hold just the ★ grouping the my-groupings screens use."""
    path = get_user_favorites_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({MY_GROUPING: MY_GROUPING_ENTRY}, indent=2)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def _load_reference_data(args: argparse.Namespace) -> list[str]:
    """Load reference data and resolve the tracked airports as main.py does.

    The persistent weather cache is not loaded: weather comes only from the
    snapshot or the recording.
    """
    import main

    ensure_user_directories()
    backend_constants.WIND_SOURCE = args.wind_source
    if ensure_cifp_data(quiet=True) is None:
        print("CIFP data unavailable; approach data will not be shown")
    if not ensure_runway_data(quiet=True):
        print("Runway data unavailable; runway lengths will not be shown")
    ui_config.AIRCRAFT_APPROACH_SPEEDS = load_aircraft_approach_speeds(
        str(REPO_ROOT / "data" / "aircraft_data.csv")
    )
    return main.resolve_airport_allowlist(args, str(REPO_ROOT))


def _keep_globals(stack: ExitStack) -> None:
    """Restore the module globals the setup assigns once the capture ends."""
    for name in ("UNIFIED_AIRPORT_DATA", "DISAMBIGUATOR", "AIRCRAFT_APPROACH_SPEEDS"):
        stack.enter_context(patch.object(ui_config, name, getattr(ui_config, name)))
    stack.enter_context(
        patch.object(backend_constants, "WIND_SOURCE", backend_constants.WIND_SOURCE)
    )


def _build_app(args: argparse.Namespace, allowlist: list[str]) -> VATSIMControlApp:
    """Analyze the traffic and build the app, as main.py does."""
    (
        airport_data,
        groupings_data,
        total_flights,
        ui_config.UNIFIED_AIRPORT_DATA,
        ui_config.DISAMBIGUATOR,
    ) = analyze_flights_data(
        max_eta_hours=args.max_eta_hours,
        airport_allowlist=allowlist or None,
        groupings_allowlist=args.groupings,
        include_all_staffed=args.include_all_staffed,
        include_all_arriving=args.include_all_arriving,
        unified_airport_data=ui_config.UNIFIED_AIRPORT_DATA,
        disambiguator=ui_config.DISAMBIGUATOR,
    )
    if airport_data is None:
        raise RuntimeError("The traffic analysis returned no airport data")
    return VATSIMControlApp(
        airport_data, groupings_data, total_flights or 0, args, allowlist or None
    )


# --- driving the app ------------------------------------------------------------


def svg_text(svg: str) -> str:
    """The text an SVG screenshot shows, without markup or entities."""
    return html.unescape(re.sub(r"<[^>]+>", "", svg)).replace("\xa0", " ")


def _has_option(option_list: OptionList, option_id: str) -> bool:
    try:
        option_list.get_option_index(option_id)
    except OptionDoesNotExist:
        return False
    return True


def _tasks_done(screen: Screen) -> bool:
    """Whether a screen's pending fetch tasks have all started and finished."""
    tasks = getattr(screen, "_pending_tasks", [])
    return bool(tasks) and all(task.done() for task in tasks)


@dataclass
class Driver:
    """The running app and the pilot a screen driver works it through."""

    app: VATSIMControlApp
    pilot: Pilot

    async def press(self, *keys: str) -> None:
        await self.pilot.press(*keys)
        await self.pilot.pause()

    async def wait_until(self, condition: Callable[[], bool], what: str) -> None:
        """Poll condition until it holds.

        Raises:
            TimeoutError: It did not hold within SETTLE_TIMEOUT_SECONDS.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SETTLE_TIMEOUT_SECONDS
        while not condition():
            if loop.time() > deadline:
                raise TimeoutError(
                    f"Timed out after {SETTLE_TIMEOUT_SECONDS:.0f} s waiting for {what}"
                )
            await self.pilot.pause(POLL_SECONDS)

    async def screen_of[S: Screen](self, screen_type: type[S]) -> S:
        """Wait for a screen of this type to be on top and return it."""
        await self.wait_until(
            lambda: isinstance(self.app.screen, screen_type), screen_type.__name__
        )
        screen = self.app.screen
        assert isinstance(screen, screen_type)
        return screen

    async def settle(self, ready: Callable[[], bool], name: str) -> str:
        """Wait for ready, then for a render free of loading text that stops changing.

        Toasts are cleared and input cursors stop blinking on every poll, so
        neither depends on when the capture happens. The user's last keypress
        is put an idle timeout back on every poll, so the status bar shows
        auto-refresh active, as it does once the user stops typing; the frozen
        clock would otherwise keep it auto-paused.

        Returns:
            The settled screen as SVG.
        """
        await self.wait_until(ready, f"the {name} screen's data")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SETTLE_TIMEOUT_SECONDS
        idle = timedelta(seconds=self.app.idle_timeout)
        previous, stable = "", 0
        while stable < STABLE_POLLS:
            if loop.time() > deadline:
                raise TimeoutError(f"The {name} screen never stopped changing")
            self.app.last_activity_time = self.app.last_refresh_time - idle
            self.app.clear_notifications()
            for field_widget in self.app.screen.query(Input):
                field_widget.cursor_blink = False
            await self.pilot.pause(POLL_SECONDS)
            current = self.app.export_screenshot()
            settled = current == previous and "Loading" not in svg_text(current)
            stable = stable + 1 if settled else 0
            previous = current
        return previous

    async def select_airport_row(self, icao: str) -> SplitFlapDataTable:
        """Put the Individual Airports cursor on an airport's row and focus the table."""
        rows = sorted(self.app.airport_data, key=airport_grouping_sort_key)
        index = next((i for i, row in enumerate(rows) if row.icao == icao), None)
        if index is None:
            raise LookupError(f"{icao} is not on the Individual Airports tab")
        table = self.app.query_one("#airports-table", SplitFlapDataTable)
        table.move_cursor(row=index)
        table.focus()
        await self.pilot.pause()
        return table

    async def open_goto(self) -> GoToScreen:
        await self.press("ctrl+g")
        return await self.loaded_goto()

    async def loaded_goto(self) -> GoToScreen:
        goto = await self.screen_of(GoToScreen)
        await self.wait_until(lambda: goto.data_loaded, "Go To's results")
        return goto

    async def highlight_row(self, goto: GoToScreen, query: str, option_id: str) -> None:
        """Type a Go To query, then focus the list on one of its rows."""
        goto.query_one("#goto-input", Input).value = query
        option_list = goto.query_one("#goto-list", OptionList)
        await self.wait_until(
            lambda: _has_option(option_list, option_id), f"Go To row {option_id}"
        )
        option_list.focus()
        option_list.highlighted = option_list.get_option_index(option_id)
        await self.pilot.pause()

    async def open_flight_info(self) -> FlightInfoScreen:
        goto = await self.open_goto()
        await self.highlight_row(goto, f"#{FLIGHT}", f"flight:{FLIGHT}")
        await self.press("enter")
        info = await self.screen_of(FlightInfoScreen)
        await self.wait_until(
            lambda: not info.altimeter_loading and not info.mea_loading,
            "the flight info's altimeter and MEA",
        )
        return info


def _always() -> bool:
    return True


# --- one driver per screen ------------------------------------------------------

Ready = Callable[[], bool]


async def drive_main_airports(d: Driver) -> Ready:
    return _always


async def drive_main_groupings(d: Driver) -> Ready:
    d.app.query_one("#tabs", TabbedContent).active = "groupings"
    table = d.app.query_one("#groupings-table", SplitFlapDataTable)
    return lambda: table.row_count > 0


async def drive_flight_board(d: Driver) -> Ready:
    await d.select_airport_row(BUSIEST_AIRPORT)
    await d.press("enter")
    board = await d.screen_of(FlightBoardScreen)
    arrivals = board.query_one("#arrivals-table", SplitFlapDataTable)
    return lambda: arrivals.row_count > 0


async def drive_flight_info(d: Driver) -> Ready:
    await d.open_flight_info()
    return _always


async def drive_goto_browse(d: Driver) -> Ready:
    goto = await d.open_goto()
    goto.query_one("#goto-input", Input).value = GOTO_QUERY
    option_list = goto.query_one("#goto-list", OptionList)
    return lambda: _has_option(option_list, f"airport:{BUSIEST_AIRPORT}")


async def drive_goto_pick_several(d: Driver) -> Ready:
    goto = await d.open_goto()
    await d.press("tab")
    for name in PICKS:
        await d.highlight_row(goto, PICK_QUERY, f"grouping:{name}")
        await d.press("space")
    await d.highlight_row(goto, PICK_QUERY, f"members:{PICKS[0]}")
    await d.press("right")
    option_list = goto.query_one("#goto-list", OptionList)
    return lambda: _has_option(option_list, f"sub:{PICKS[0]}/{BUSIEST_AIRPORT}")


async def drive_grouping_builder(d: Driver) -> Ready:
    goto = await d.open_goto()
    await d.highlight_row(goto, MY_GROUPING, f"grouping:{MY_GROUPING}")
    await d.press("e")
    builder = await d.screen_of(GroupingBuilderScreen)
    members = builder.query_one("#builder-members", OptionList)
    return lambda: members.option_count > 0


async def drive_weather_briefing(d: Driver) -> Ready:
    await d.press("ctrl+b")
    picker = await d.loaded_goto()
    await d.highlight_row(picker, f"${GROUPING}", f"grouping:{GROUPING}")
    await d.press("enter")
    briefing = await d.screen_of(WeatherBriefingScreen)
    return lambda: _tasks_done(briefing)


async def drive_flight_briefing(d: Driver) -> Ready:
    await d.press("ctrl+b")
    picker = await d.loaded_goto()
    await d.highlight_row(picker, f"#{FLIGHT}", f"flight:{FLIGHT}")
    await d.press("enter")
    briefing = await d.screen_of(FlightWeatherBriefingScreen)
    return lambda: _tasks_done(briefing)


async def drive_route_weather(d: Driver) -> Ready:
    await d.open_flight_info()
    await d.press("w")
    route = await d.screen_of(RouteWeatherScreen)
    return lambda: _tasks_done(route)


async def drive_diversions(d: Driver) -> Ready:
    await d.open_flight_info()
    await d.press("d")
    diversions = await d.screen_of(DiversionModal)
    return lambda: diversions._search_completed


async def drive_metar_lookup(d: Driver) -> Ready:
    await d.select_airport_row(BUSIEST_AIRPORT)
    await d.press("ctrl+e")
    lookup = await d.screen_of(MetarInfoScreen)
    return lambda: _tasks_done(lookup)


async def drive_wind_info(d: Driver) -> Ready:
    await d.select_airport_row(BUSIEST_AIRPORT)
    await d.press("ctrl+w")
    await d.screen_of(WindInfoScreen)
    return _always


async def drive_vfr_alternatives(d: Driver) -> Ready:
    await d.press("ctrl+a")
    finder = await d.screen_of(VfrAlternativesScreen)
    finder.query_one("#vfr-input", Input).value = BUSIEST_AIRPORT
    await d.press("enter")
    return lambda: finder._search_task is not None and finder._search_task.done()


async def drive_tracked_airports(d: Driver) -> Ready:
    await d.press("ctrl+t")
    await d.screen_of(TrackedAirportsModal)
    return _always


async def drive_help(d: Driver) -> Ready:
    await d.press("f1")
    await d.screen_of(HelpScreen)
    return _always


async def drive_command_palette(d: Driver) -> Ready:
    await d.press("f2")
    await d.screen_of(CommandPaletteScreen)
    return _always


@dataclass(frozen=True)
class GuideScreen:
    """One screenshot: its file name stem and the driver that opens the screen."""

    name: str
    drive: Callable[[Driver], Awaitable[Ready]]


# Historical Statistics is left out: it needs a statsim.net API key.
SCREENS: tuple[GuideScreen, ...] = (
    GuideScreen("main-airports", drive_main_airports),
    GuideScreen("main-groupings", drive_main_groupings),
    GuideScreen("flight-board", drive_flight_board),
    GuideScreen("flight-info", drive_flight_info),
    GuideScreen("goto-browse", drive_goto_browse),
    GuideScreen("goto-pick-several", drive_goto_pick_several),
    GuideScreen("grouping-builder", drive_grouping_builder),
    GuideScreen("weather-briefing", drive_weather_briefing),
    GuideScreen("flight-briefing", drive_flight_briefing),
    GuideScreen("route-weather", drive_route_weather),
    GuideScreen("metar-lookup", drive_metar_lookup),
    GuideScreen("wind-info", drive_wind_info),
    GuideScreen("vfr-alternatives", drive_vfr_alternatives),
    GuideScreen("diversions", drive_diversions),
    GuideScreen("tracked-airports", drive_tracked_airports),
    GuideScreen("help", drive_help),
    GuideScreen("command-palette", drive_command_palette),
)

SCREEN_NAMES = tuple(screen.name for screen in SCREENS)


# --- the capture -----------------------------------------------------------------


def _main_view_ready(app: VATSIMControlApp) -> bool:
    """The startup traffic is shown, its weather pass is done and Go To is warm."""
    table = app.query_one("#airports-table", SplitFlapDataTable)
    return (
        table.row_count > 0 and app.goto_cache_ready and not app._weather_pass_running
    )


def _write_svg(path: Path, svg: str) -> None:
    """Write an SVG with LF endings, no trailing spaces and one final newline."""
    lines = [line.rstrip() for line in svg.splitlines()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


async def _capture_screen(
    screen: GuideScreen,
    args: argparse.Namespace,
    allowlist: list[str],
    out_dir: Path | None,
) -> None:
    """Start a fresh app, drive it to the screen, settle and save it when out_dir is set."""
    app = _build_app(args, allowlist)
    async with app.run_test(size=SCREEN_SIZE) as pilot:
        driver = Driver(app, pilot)
        await driver.wait_until(lambda: _main_view_ready(app), "the main view")
        ready = await screen.drive(driver)
        svg = await driver.settle(ready, screen.name)
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_svg(out_dir / f"{screen.name}.svg", svg)
    print(f"{screen.name}: {'captured' if out_dir else 'driven'}")


async def _run(
    names: Sequence[str], source: WeatherSource, now: datetime, out_dir: Path | None
) -> None:
    """Drive each named screen with the network and clock patched, then undo every patch."""
    screens = [screen for screen in SCREENS if screen.name in names]
    with ExitStack() as stack:
        stack.callback(weather.clear_weather_caches)
        _keep_globals(stack)
        args = _app_args()
        _write_my_grouping()
        allowlist = _load_reference_data(args)
        _patch_network(stack, source, _load_traffic(now))
        _freeze_clock(stack, now)
        for screen in screens:
            await _capture_screen(screen, args, allowlist, out_dir)


async def capture_screens(
    names: Sequence[str], weather_file: Path, out_dir: Path
) -> None:
    """Capture the named screens as SVGs from the weather snapshot.

    The caller points the user data directory at a temporary one first (see
    main), since the capture writes favorites.json there.

    Raises:
        FileNotFoundError: The weather snapshot does not exist yet.
    """
    snapshot = WeatherSnapshot.load(weather_file)
    await _run(names, SnapshotWeather(snapshot), snapshot.recorded_at, out_dir)


async def record_weather(names: Sequence[str], weather_file: Path) -> None:
    """Drive the named screens on live weather and write what they fetched."""
    recorder = WeatherRecorder(datetime.now(UTC).replace(microsecond=0))
    await _run(names, recorder, recorder.snapshot.recorded_at, None)
    recorder.snapshot.write(weather_file)
    print(f"Wrote {weather_file} ({len(recorder.snapshot.metars)} METARs)")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture the user guide's screenshots as SVGs"
    )
    parser.add_argument(
        "--record-weather",
        action="store_true",
        help="Fetch live weather for the captured screens and write the snapshot",
    )
    parser.add_argument("--weather", type=Path, default=DEFAULT_WEATHER)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--only", choices=SCREEN_NAMES, help="Capture one screen")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = _parse_args(arguments)
    if os.environ.get("PYTHONHASHSEED") != HASH_SEED:
        # The app orders some lists by iterating sets of strings, whose order
        # follows the per-process hash seed; one fixed seed keeps every capture
        # identical, so the script runs itself again under it
        rerun = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), *arguments],
            env={**os.environ, "PYTHONHASHSEED": HASH_SEED},
            check=False,
        )
        sys.exit(rerun.returncode)
    names = [args.only] if args.only else list(SCREEN_NAMES)
    real_data_dir = get_user_data_dir()
    with tempfile.TemporaryDirectory(
        prefix="vcr-guide-", ignore_cleanup_errors=True
    ) as temp:
        for variable in ("LOCALAPPDATA", "XDG_DATA_HOME", "HOME"):
            os.environ[variable] = temp
        user_data_dir = get_user_data_dir()
        if user_data_dir == real_data_dir:
            raise RuntimeError(f"Could not move the user data dir off {real_data_dir}")
        copy_reference_caches(real_data_dir, user_data_dir)
        if args.record_weather:
            asyncio.run(record_weather(names, args.weather.resolve()))
        else:
            asyncio.run(
                capture_screens(names, args.weather.resolve(), args.out.resolve())
            )


if __name__ == "__main__":
    main()
