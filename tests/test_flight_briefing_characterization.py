"""Characterization of the flight weather briefing: its fetch, synopsis, weather blocks, body text and HTML export.

The screen is built without an app, as the flight info characterization builds it: its
``_update_content`` is replaced by a recorder of the text the screen would show and its
``notify`` by a recorder of its toasts. The METAR and TAF fetchers and the enroute airport
search are faked at the module boundary, and the VATSIM and D-ATIS fetchers as tripwires
the briefing must never call; time is a fixed clock patched over
``flight_briefing.datetime``, and the export's temp file and browser are faked over
``flight_briefing.tempfile`` and ``flight_briefing.webbrowser``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, tzinfo
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

from backend.core.calculations import haversine_distance_nm
from backend.core.route import sample_route_points
from backend.data import datis_api, vatsim_api
from ui import config
from ui.config import CATEGORY_COLORS
from ui.modals import flight_briefing
from ui.modals.flight_briefing import FlightWeatherBriefingScreen

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

# KAAA, KMMM and KEEE sit on one meridian, 120 nm apart; KNOP has no position
AIRPORTS: dict[str, dict[str, Any]] = {
    "KAAA": {"latitude": 37.0, "longitude": -122.0},
    "KMMM": {"latitude": 39.0, "longitude": -122.0},
    "KEEE": {"latitude": 41.0, "longitude": -122.0},
    "KNOP": {"city": "Nopos"},
}

METARS = {
    "KAAA": "KAAA 101251Z 27010KT 10SM CLR 15/05 A2992",
    "KMMM": "KMMM 101251Z 00000KT 4SM BR BKN020 15/05 A2992",
    "KEEE": "KEEE 101251Z 18005KT 2SM BR OVC008 10/10 A2992",
}

TAF_HEAD = "TAF KEEE 101130Z 1012/1112"
TAF_IMPROVING = f"{TAF_HEAD} 18005KT 2SM BR OVC008 FM101800 27010KT P6SM SKC"
TAF_WORSENING = f"{TAF_HEAD} 27010KT P6SM SKC FM101800 18005KT 1SM BR OVC005"
TAF_STEADY_THEN_WORSENING = (
    f"{TAF_HEAD} 27010KT P6SM SKC FM101800 27010KT P6SM SKC "
    "FM102200 18005KT 2SM BR OVC008"
)
TAF_WORSENING_THIRD = (
    f"{TAF_HEAD} 27010KT P6SM SKC FM101800 27010KT P6SM SKC "
    "FM102000 27010KT P6SM SKC FM102200 18005KT 2SM BR OVC008"
)
TAF_TEMPO_WORSENING = f"{TAF_HEAD} 27010KT P6SM SKC TEMPO 1014/1018 1SM BR OVC005"
TAF_NO_GROUPS = f"{TAF_HEAD} 27010KT P6SM SKC"

TEMP_PATH = "/tmp/briefings/flight_briefing_UAL1_abc123.html"


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


class World:
    """The module's outside world: answers and records every boundary call."""

    def __init__(self) -> None:
        self.metars: dict[str, str] = dict(METARS)
        self.tafs: dict[str, str] = {"KEEE": TAF_IMPROVING}
        self.metar_error: BaseException | None = None
        self.metar_calls: list[list[str]] = []
        self.taf_calls: list[list[str]] = []
        self.vatsim: dict[str, Any] | None = {"pilots": []}
        self.vatsim_calls = 0
        self.vatsim_atis: dict[str, str] = {"KAAA": "VATSIM ATIS KAAA"}
        self.atis_calls: list[tuple[Any, list[str]]] = []
        self.datis: dict[str, str] = {"KEEE": "D-ATIS KEEE"}
        self.datis_calls: list[list[str]] = []
        self.enroute: list[dict[str, Any]] = [
            {"icao": "KMMM", "distance_nm": 120.0},
            {"icao": "KAAA", "distance_nm": 125.0},
        ]
        self.enroute_calls: list[tuple[Any, ...]] = []
        self.files: list[FakeTempFile] = []
        self.opened: list[str] = []
        self.debug: list[str] = []

    def get_metar_batch(self, airports: list[str]) -> dict[str, str]:
        self.metar_calls.append(list(airports))
        if self.metar_error is not None:
            raise self.metar_error
        return {icao: self.metars[icao] for icao in airports if icao in self.metars}

    def get_taf_batch(self, airports: list[str]) -> dict[str, str]:
        self.taf_calls.append(list(airports))
        return {icao: self.tafs[icao] for icao in airports if icao in self.tafs}

    def download_vatsim_data(self) -> dict[str, Any] | None:
        self.vatsim_calls += 1
        return self.vatsim

    def get_atis_for_airports(
        self, vatsim_data: Any, airports: list[str]
    ) -> dict[str, str]:
        self.atis_calls.append((vatsim_data, list(airports)))
        return {i: self.vatsim_atis[i] for i in airports if i in self.vatsim_atis}

    def get_datis_for_airports(self, airports: list[str]) -> dict[str, str]:
        self.datis_calls.append(list(airports))
        return {icao: self.datis[icao] for icao in airports if icao in self.datis}

    def find_enroute_airports(
        self, sample_points: Any, airports_data: Any, search_radius_nm: float
    ) -> list[dict[str, Any]]:
        self.enroute_calls.append((sample_points, airports_data, search_radius_nm))
        return [dict(point) for point in self.enroute]

    def named_temporary_file(self, **kwargs: Any) -> FakeTempFile:
        temp_file = FakeTempFile(kwargs)
        self.files.append(temp_file)
        return temp_file


def install(monkeypatch: pytest.MonkeyPatch) -> World:
    """Fake the module's boundary and return the fakes."""
    world = World()
    monkeypatch.setattr(config, "UNIFIED_AIRPORT_DATA", AIRPORTS)
    for name in ("get_metar_batch", "get_taf_batch", "find_enroute_airports"):
        monkeypatch.setattr(flight_briefing, name, getattr(world, name))
    # The briefing must fetch no ATIS: each fetcher is faked on the module that
    # defines it and on the briefing module, so a call from either is recorded.
    for module, name in (
        (vatsim_api, "download_vatsim_data"),
        (vatsim_api, "get_atis_for_airports"),
        (datis_api, "get_datis_for_airports"),
    ):
        monkeypatch.setattr(module, name, getattr(world, name))
        monkeypatch.setattr(flight_briefing, name, getattr(world, name), raising=False)
    monkeypatch.setattr(flight_briefing, "debug", world.debug.append)

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return NOW

    monkeypatch.setattr(flight_briefing, "datetime", FakeDatetime)
    monkeypatch.setattr(
        flight_briefing,
        "tempfile",
        SimpleNamespace(NamedTemporaryFile=world.named_temporary_file),
    )
    monkeypatch.setattr(
        flight_briefing, "webbrowser", SimpleNamespace(open=world.opened.append)
    )
    return world


@pytest.fixture(autouse=True)
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    yield install(monkeypatch)


class Briefing:
    """A screen for UAL1 to KEEE with its renders and toasts recorded."""

    def __init__(
        self,
        alternate: str | None = None,
        route: str | None = None,
        groundspeed: int | None = None,
        departure: str = "KAAA",
    ) -> None:
        self.screen = FlightWeatherBriefingScreen(
            callsign="UAL1",
            departure=departure,
            arrival="KEEE",
            alternate=alternate,
            route=route,
            cruise_altitude=35000,
            groundspeed=groundspeed,
        )
        self.renders: list[str] = []
        self.toasts: list[tuple[str, dict[str, Any]]] = []
        self.screen._update_content = self.renders.append
        self.screen.notify = self._notify

    def _notify(self, message: str, **kwargs: Any) -> None:
        self.toasts.append((message, kwargs))


def wx(
    category: str = "VFR",
    *,
    metar: str = "",
    taf: str = "",
    wind: str | None = None,
    visibility: float | None = None,
    ceiling: str | None = None,
) -> dict[str, Any]:
    """A weather entry shaped as the fetch stores it."""
    return {
        "metar": metar,
        "taf": taf,
        "category": category,
        "color": CATEGORY_COLORS.get(category, "white"),
        "visibility_sm": visibility,
        "ceiling_ft": None,
        "ceiling_layer": ceiling,
        "wind": wind,
    }


def text(*lines: str) -> str:
    return "\n".join(lines)


# _generate_synopsis


def run_synopsis(
    departure: str | None,
    enroute: list[str | None],
    arrival: str | None,
    taf: str = "",
) -> str:
    """The synopsis for these categories; None leaves that airport without weather."""
    briefing = Briefing()
    screen = briefing.screen
    if departure:
        screen.weather_data["KAAA"] = wx(departure)
    for index, category in enumerate(enroute):
        icao = f"KE{index:02d}"
        screen.enroute_points.append({"icao": icao, "distance_nm": 50.0 * index})
        if category:
            screen.weather_data[icao] = wx(category)
    if arrival:
        screen.weather_data["KEEE"] = wx(arrival, taf=taf)
    return screen._generate_synopsis()


VFR_BOTH_ENDS = "VFR departure. VFR arrival."

SYNOPSIS_CASES = [
    pytest.param(None, [], None, "", VFR_BOTH_ENDS, id="no weather at all"),
    pytest.param("VFR", [], "VFR", "", VFR_BOTH_ENDS, id="VFR both ends, no enroute"),
    pytest.param(
        "MVFR",
        ["MVFR"],
        "MVFR",
        "",
        "MVFR conditions at departure. MVFR enroute. MVFR arrival.",
        id="MVFR everywhere",
    ),
    pytest.param(
        "IFR",
        ["VFR", "IFR", "MVFR"],
        "LIFR",
        "",
        "IFR conditions at departure. IFR enroute. LIFR conditions at arrival.",
        id="IFR enroute stays after MVFR",
    ),
    pytest.param(
        "LIFR",
        ["MVFR", "LIFR"],
        "IFR",
        "",
        "LIFR conditions at departure. LIFR enroute. IFR conditions at arrival.",
        id="LIFR enroute after MVFR",
    ),
    pytest.param(
        "VFR",
        ["IFR", "LIFR"],
        "VFR",
        "",
        "VFR departure. IFR enroute. VFR arrival.",
        id="first of IFR and LIFR enroute wins",
    ),
    pytest.param(
        "UNK", [None, "VFR"], "UNK", "", VFR_BOTH_ENDS, id="unknown and missing"
    ),
    pytest.param(
        "VFR",
        [],
        "IFR",
        TAF_IMPROVING,
        "VFR departure. IFR conditions at arrival. improving to VFR.",
        id="TAF improving",
    ),
    pytest.param(
        "VFR",
        [],
        "VFR",
        TAF_WORSENING,
        "VFR departure. VFR arrival. deteriorating to IFR.",
        id="TAF worsening",
    ),
    pytest.param(
        "VFR",
        [],
        "VFR",
        TAF_STEADY_THEN_WORSENING,
        "VFR departure. VFR arrival. deteriorating to IFR.",
        id="TAF second group worsens",
    ),
    pytest.param(
        "VFR",
        [],
        "VFR",
        TAF_WORSENING_THIRD,
        VFR_BOTH_ENDS,
        id="TAF third group worsens",
    ),
    pytest.param(
        "VFR", [], "VFR", TAF_NO_GROUPS, VFR_BOTH_ENDS, id="TAF without groups"
    ),
]


@pytest.mark.parametrize(
    ("departure", "enroute", "arrival", "taf", "expected"), SYNOPSIS_CASES
)
def test_generate_synopsis(
    departure: str | None,
    enroute: list[str | None],
    arrival: str | None,
    taf: str,
    expected: str,
) -> None:
    assert run_synopsis(departure, enroute, arrival, taf) == expected


# _format_weather_block


def run_weather_block(data: dict[str, Any], indent: str | None = None) -> list[str]:
    screen = Briefing().screen
    if indent is None:
        return screen._format_weather_block("KEEE", data)
    return screen._format_weather_block("KEEE", data, indent)


LONG_METAR = (
    "KEEE 101251Z 18005KT 2SM BR OVC008 10/10 A2992 RMK AO2 SLP132 T01000100 "
    + ("X" * 30)
)
TAF_UP_VFR = "    [dim]TAF: ↑ VFR (FM)[/dim]"
TAF_DOWN_IFR = "    [dim]TAF: ↓ IFR (FM)[/dim]"
SHORT_METAR = "    [dim]101251Z 18005KT 2SM[/dim]"

WEATHER_BLOCK_CASES = [
    pytest.param({}, None, [], id="empty entry"),
    pytest.param(wx(), None, [], id="nothing to show"),
    pytest.param(wx(wind="27010KT"), None, ["    [dim]27010KT[/dim]"], id="wind only"),
    pytest.param(
        wx(visibility=10.0), None, ["    [dim]10.0SM[/dim]"], id="visibility only"
    ),
    pytest.param(
        wx(visibility=0.0), None, ["    [dim]0.0SM[/dim]"], id="zero visibility"
    ),
    pytest.param(
        wx(ceiling="OVC008"), None, ["    [dim]OVC008[/dim]"], id="ceiling only"
    ),
    pytest.param(
        wx(wind="18005KT", visibility=2.0, ceiling="OVC008"),
        "  ",
        ["  [dim]18005KT 2.0SM OVC008[/dim]"],
        id="indent",
    ),
    pytest.param(
        wx(metar="METAR KEEE 101251Z 18005KT 2SM"),
        None,
        [SHORT_METAR],
        id="METAR prefix",
    ),
    pytest.param(
        wx(metar="KEEE 101251Z 18005KT 2SM"), None, [SHORT_METAR], id="ICAO prefix"
    ),
    pytest.param(
        wx(metar="METAR KXXX 101251Z 18005KT 2SM"),
        None,
        ["    [dim]KXXX 101251Z 18005KT 2SM[/dim]"],
        id="other ICAO",
    ),
    pytest.param(
        wx(metar="SPECI KEEE 101251Z 18005KT 2SM"),
        None,
        ["    [dim]SPECI KEEE 101251Z 18005KT 2SM[/dim]"],
        id="SPECI kept",
    ),
    pytest.param(
        wx(metar=LONG_METAR),
        None,
        [
            "    [dim]101251Z 18005KT 2SM BR OVC008 10/10 A2992 RMK AO2 SLP132"
            " T01000100 XXXXXXXXXXXXX[/dim]"
        ],
        id="long METAR cut at 80",
    ),
    pytest.param(wx("IFR", taf=TAF_IMPROVING), None, [TAF_UP_VFR], id="TAF improving"),
    pytest.param(
        wx("VFR", taf=TAF_WORSENING), None, [TAF_DOWN_IFR], id="TAF worsening"
    ),
    pytest.param(
        wx("VFR", taf=TAF_TEMPO_WORSENING),
        None,
        ["    [dim]TAF: ↓ IFR (TEMPO)[/dim]"],
        id="TAF TEMPO",
    ),
    pytest.param(
        wx("VFR", taf=TAF_STEADY_THEN_WORSENING), None, [], id="TAF first steady"
    ),
    pytest.param(wx("VFR", taf=TAF_NO_GROUPS), None, [], id="TAF without groups"),
    pytest.param(
        {"taf": TAF_WORSENING}, None, [TAF_DOWN_IFR], id="TAF without category"
    ),
    pytest.param(
        wx(
            "IFR",
            metar=METARS["KEEE"],
            taf=TAF_IMPROVING,
            wind="18005KT",
            visibility=2.0,
            ceiling="OVC008",
        ),
        None,
        [
            "    [dim]18005KT 2.0SM OVC008[/dim]",
            "    [dim]101251Z 18005KT 2SM BR OVC008 10/10 A2992[/dim]",
            TAF_UP_VFR,
        ],
        id="everything",
    ),
]


@pytest.mark.parametrize(("data", "indent", "expected"), WEATHER_BLOCK_CASES)
def test_format_weather_block(
    data: dict[str, Any], indent: str | None, expected: list[str]
) -> None:
    assert run_weather_block(data, indent) == expected


# Briefing states shared by _update_display and action_print

KAAA_WX = wx("VFR", metar=METARS["KAAA"], wind="27010KT", visibility=10.0)
KMMM_WX = wx(
    "MVFR", metar=METARS["KMMM"], wind="00000KT", visibility=4.0, ceiling="BKN020"
)
KEEE_WX = wx(
    "IFR",
    metar=METARS["KEEE"],
    taf=TAF_IMPROVING,
    wind="18005KT",
    visibility=2.0,
    ceiling="OVC008",
)

STATES: dict[str, dict[str, Any]] = {
    "full": {
        "alternate": "KALT",
        "route_waypoints": ["ALPHA", "BRAVO", "CHARL", "DELTA", "ECHOO"],
        "enroute_points": [
            {"icao": "KMMM", "distance_nm": 80.4},
            {"icao": "KGAP", "distance_nm": 120.0},
            {"icao": "KNNN", "distance_nm": 160.7},
        ],
        "weather_data": {
            "KAAA": KAAA_WX,
            "KMMM": KMMM_WX,
            "KNNN": wx("VFR", wind="27010KT"),
            "KEEE": KEEE_WX,
            "KALT": wx("UNK"),
        },
        "total_distance": 240.1,
    },
    "enroute without waypoints": {
        "alternate": None,
        "route_waypoints": [],
        "enroute_points": [
            {"icao": "KMMM", "distance_nm": 60.0},
            {"icao": "KQQQ", "distance_nm": 120.0},
            {"icao": "KRRR", "distance_nm": 180.0},
        ],
        "weather_data": {
            "KAAA": KAAA_WX,
            "KMMM": wx("MVFR", ceiling="BKN020"),
            "KQQQ": wx("VFR"),
            "KRRR": wx("IFR", ceiling="OVC008"),
            "KEEE": KEEE_WX,
        },
        "total_distance": 240.1,
    },
    "fewer waypoints than segments": {
        "alternate": None,
        "route_waypoints": ["ALPHA", "BRAVO"],
        "enroute_points": [
            {"icao": "KMMM", "distance_nm": 80.0},
            {"icao": "KQQQ", "distance_nm": 160.0},
        ],
        "weather_data": {"KMMM": KMMM_WX, "KQQQ": wx("VFR")},
        "total_distance": 240.1,
    },
    "waypoints only": {
        "alternate": None,
        "route_waypoints": ["ALPHA", "BRAVO"],
        "enroute_points": [],
        "weather_data": {"KAAA": KAAA_WX, "KEEE": KEEE_WX},
        "total_distance": 240.1,
    },
    "bare": {
        "alternate": "KALT",
        "route_waypoints": [],
        "enroute_points": [],
        "weather_data": {"KZZZ": wx("VFR")},
        "total_distance": 0.0,
    },
}


def briefing_in(state_name: str, groundspeed: int | None = None) -> Briefing:
    """A briefing whose screen holds one of the shared states."""
    state = STATES[state_name]
    briefing = Briefing(alternate=state["alternate"], groundspeed=groundspeed)
    screen = briefing.screen
    screen.route_waypoints = list(state["route_waypoints"])
    screen.enroute_points = [dict(point) for point in state["enroute_points"]]
    screen.weather_data = dict(state["weather_data"])
    screen.total_distance = state["total_distance"]
    return briefing


H_SYNOPSIS = "[bold cyan]SYNOPSIS[/bold cyan]"
H_DEP = "[bold cyan]━━━ DEPARTURE ━━━[/bold cyan]"
H_ENR = "[bold cyan]━━━ ENROUTE ━━━[/bold cyan]"
H_ARR = "[bold cyan]━━━ ARRIVAL ━━━[/bold cyan]"
H_ALT = "[bold cyan]━━━ ALTERNATE ━━━[/bold cyan]"

KAAA_DEP = (
    "DEP KAAA (   0nm       ) ............................... [#00ff00]VFR[/#00ff00]",
    "    [dim]27010KT 10.0SM[/dim]",
    "    [dim]101251Z 27010KT 10SM CLR 15/05 A2992[/dim]",
)
KEEE_DETAILS = (
    "    [dim]18005KT 2.0SM OVC008[/dim]",
    "    [dim]101251Z 18005KT 2SM BR OVC008 10/10 A2992[/dim]",
    TAF_UP_VFR,
)
KEEE_ARR = (
    "ARR KEEE ( 240nm  +0:32) .......................... [red]IFR[/red]",
    *KEEE_DETAILS,
)
KALT_ALT = "ALT KALT (   0nm       ) ............................... [white]UNK[/white]"
KMMM_80 = "ENR KMMM (  80nm  +0:10) .......................... [#5599ff]MVFR[/#5599ff]"
KMMM_COND = "    [dim]00000KT 4.0SM BKN020[/dim]"
KNNN_160 = "ENR KNNN ( 160nm  +0:21) .......................... [#00ff00]VFR[/#00ff00]"
KQQQ_160 = "ENR KQQQ ( 160nm  +0:21) .......................... [#00ff00]VFR[/#00ff00]"
WIND_ONLY = "    [dim]27010KT[/dim]"
FULL_SYNOPSIS = (
    "VFR departure. MVFR enroute. IFR conditions at arrival. improving to VFR."
)
ENROUTE_ONLY_SYNOPSIS = (
    "VFR departure. IFR enroute. IFR conditions at arrival. improving to VFR."
)
FEW_WAYPOINTS_SYNOPSIS = "VFR departure. MVFR enroute. VFR arrival."
WAYPOINTS_ONLY_SYNOPSIS = "VFR departure. IFR conditions at arrival. improving to VFR."
KMMM_60 = "ENR KMMM (  60nm  +0:08) .......................... [#5599ff]MVFR[/#5599ff]"
KQQQ_120 = "ENR KQQQ ( 120nm  +0:16) .......................... [#00ff00]VFR[/#00ff00]"
KRRR_180 = "ENR KRRR ( 180nm  +0:24) .......................... [red]IFR[/red]"


def wp(name: str) -> str:
    return f"    [dim]  · {name}[/dim]"


def valid_header(route_nm: int, ete: str) -> str:
    return (
        f"[bold]Briefing valid: 101200Z[/bold]  |  Route: {route_nm}nm  |  ETE: {ete}"
    )


# _update_display


def run_update_display(state_name: str, groundspeed: int | None = None) -> list[str]:
    briefing = briefing_in(state_name, groundspeed)
    briefing.screen._update_display()
    return briefing.renders


DISPLAY_CASES = [
    pytest.param(
        "full",
        None,
        text(
            valid_header(240, "+0:32"),
            "",
            H_SYNOPSIS,
            FULL_SYNOPSIS,
            "",
            H_DEP,
            *KAAA_DEP,
            "",
            H_ENR,
            wp("ALPHA"),
            KMMM_80,
            KMMM_COND,
            wp("BRAVO"),
            wp("CHARL"),
            KNNN_160,
            WIND_ONLY,
            wp("DELTA"),
            wp("ECHOO"),
            "",
            H_ARR,
            *KEEE_ARR,
            "",
            H_ALT,
            KALT_ALT,
        ),
        id="full",
    ),
    pytest.param(
        "full",
        300,
        text(
            valid_header(240, "+0:48"),
            "",
            H_SYNOPSIS,
            FULL_SYNOPSIS,
            "",
            H_DEP,
            *KAAA_DEP,
            "",
            H_ENR,
            wp("ALPHA"),
            "ENR KMMM (  80nm  +0:16) .......................... [#5599ff]MVFR[/#5599ff]",
            KMMM_COND,
            wp("BRAVO"),
            wp("CHARL"),
            "ENR KNNN ( 160nm  +0:32) .......................... [#00ff00]VFR[/#00ff00]",
            WIND_ONLY,
            wp("DELTA"),
            wp("ECHOO"),
            "",
            H_ARR,
            "ARR KEEE ( 240nm  +0:48) .......................... [red]IFR[/red]",
            *KEEE_DETAILS,
            "",
            H_ALT,
            KALT_ALT,
        ),
        id="full at 300 kt",
    ),
    pytest.param(
        "enroute without waypoints",
        None,
        text(
            valid_header(240, "+0:32"),
            "",
            H_SYNOPSIS,
            ENROUTE_ONLY_SYNOPSIS,
            "",
            H_DEP,
            *KAAA_DEP,
            "",
            H_ENR,
            KMMM_60,
            "    [dim]BKN020[/dim]",
            KQQQ_120,
            KRRR_180,
            "    [dim]OVC008[/dim]",
            "",
            H_ARR,
            *KEEE_ARR,
        ),
        id="enroute only",
    ),
    pytest.param(
        "fewer waypoints than segments",
        None,
        text(
            valid_header(240, "+0:32"),
            "",
            H_SYNOPSIS,
            FEW_WAYPOINTS_SYNOPSIS,
            "",
            H_DEP,
            "",
            H_ENR,
            KMMM_80,
            KMMM_COND,
            KQQQ_160,
            wp("ALPHA"),
            wp("BRAVO"),
            "",
            H_ARR,
        ),
        id="few waypoints",
    ),
    pytest.param(
        "waypoints only",
        None,
        text(
            valid_header(240, "+0:32"),
            "",
            H_SYNOPSIS,
            WAYPOINTS_ONLY_SYNOPSIS,
            "",
            H_DEP,
            *KAAA_DEP,
            "",
            H_ENR,
            wp("ALPHA"),
            wp("BRAVO"),
            "",
            H_ARR,
            *KEEE_ARR,
        ),
        id="waypoints only",
    ),
    pytest.param(
        "bare",
        None,
        text(
            valid_header(0, "+0:00"),
            "",
            H_SYNOPSIS,
            VFR_BOTH_ENDS,
            "",
            H_DEP,
            "",
            H_ARR,
            "",
            H_ALT,
        ),
        id="bare",
    ),
]


@pytest.mark.parametrize(("state_name", "groundspeed", "expected"), DISPLAY_CASES)
def test_update_display(
    state_name: str, groundspeed: int | None, expected: str
) -> None:
    assert run_update_display(state_name, groundspeed) == [expected]


# action_print


def exported_html(lines: list[str]) -> str:
    """The page the export writes for these printed lines, rendered by Rich itself."""
    console = Console(record=True, force_terminal=True, width=500)
    for line in lines:
        console.print(line)
    return console.export_html(inline_styles=True).replace(
        "</style>",
        "pre { margin: 0; padding: 0; white-space: pre-wrap; word-wrap: break-word; }\n"
        "body { margin: 20px; font-family: monospace; }\n"
        "</style>",
    )


def run_print(world: World, state_name: str) -> tuple[Briefing, list[str]]:
    """Export a state; returns the briefing and the text of each file it wrote."""
    briefing = briefing_in(state_name)
    briefing.screen.action_print()
    return briefing, ["".join(temp_file.written) for temp_file in world.files]


def test_print_without_weather_warns_and_exports_nothing(world: World) -> None:
    briefing = Briefing()

    briefing.screen.action_print()

    assert briefing.toasts == [("No weather data to export", {"severity": "warning"})]
    assert world.files == []
    assert world.opened == []


def test_print_writes_the_page_and_opens_it(world: World) -> None:
    briefing, written = run_print(world, "full")

    assert written == [FULL_HTML]
    (temp_file,) = world.files
    assert temp_file.kwargs == {
        "mode": "w",
        "suffix": ".html",
        "prefix": "flight_briefing_UAL1_",
        "delete": False,
        "encoding": "utf-8",
    }
    assert temp_file.closed is True
    assert world.opened == [f"file://{TEMP_PATH}"]
    assert briefing.toasts == [
        ("Opened in browser: flight_briefing_UAL1_abc123.html", {})
    ]


TITLE = "[bold]Flight Weather Briefing: UAL1[/bold]"
ROUTE_LINE = "KAAA → KEEE"
GENERATED_240 = "Generated: 2026-10-10 1200Z  |  Route: 240nm  |  ETE: +0:32"

PRINT_CASES = [
    pytest.param(
        "full",
        [
            TITLE,
            ROUTE_LINE,
            "Alternate: KALT",
            GENERATED_240,
            "",
            H_SYNOPSIS,
            FULL_SYNOPSIS,
            "",
            H_DEP,
            *KAAA_DEP,
            "",
            H_ENR,
            wp("ALPHA"),
            KMMM_80,
            KMMM_COND,
            wp("BRAVO"),
            wp("CHARL"),
            KNNN_160,
            WIND_ONLY,
            wp("DELTA"),
            wp("ECHOO"),
            "",
            H_ARR,
            *KEEE_ARR,
            "",
            H_ALT,
            KALT_ALT,
        ],
        id="full",
    ),
    pytest.param(
        "enroute without waypoints",
        [
            TITLE,
            ROUTE_LINE,
            GENERATED_240,
            "",
            H_SYNOPSIS,
            ENROUTE_ONLY_SYNOPSIS,
            "",
            H_DEP,
            *KAAA_DEP,
            "",
            H_ENR,
            KMMM_60,
            "    [dim]BKN020[/dim]",
            KQQQ_120,
            KRRR_180,
            "    [dim]OVC008[/dim]",
            "",
            H_ARR,
            *KEEE_ARR,
            "",
        ],
        id="enroute only",
    ),
    pytest.param(
        "fewer waypoints than segments",
        [
            TITLE,
            ROUTE_LINE,
            GENERATED_240,
            "",
            H_SYNOPSIS,
            FEW_WAYPOINTS_SYNOPSIS,
            "",
            H_DEP,
            "",
            H_ENR,
            KMMM_80,
            KMMM_COND,
            KQQQ_160,
            wp("ALPHA"),
            wp("BRAVO"),
            "",
            H_ARR,
            "",
        ],
        id="few waypoints",
    ),
    pytest.param(
        "waypoints only",
        [
            TITLE,
            ROUTE_LINE,
            GENERATED_240,
            "",
            H_SYNOPSIS,
            WAYPOINTS_ONLY_SYNOPSIS,
            "",
            H_DEP,
            *KAAA_DEP,
            "",
            H_ENR,
            wp("ALPHA"),
            wp("BRAVO"),
            "",
            H_ARR,
            *KEEE_ARR,
            "",
        ],
        id="waypoints only",
    ),
    pytest.param(
        "bare",
        [
            TITLE,
            ROUTE_LINE,
            "Alternate: KALT",
            "Generated: 2026-10-10 1200Z  |  Route: 0nm  |  ETE: +0:00",
            "",
            H_SYNOPSIS,
            VFR_BOTH_ENDS,
            "",
            H_DEP,
            "",
            H_ARR,
            "",
            H_ALT,
        ],
        id="bare",
    ),
]


@pytest.mark.parametrize(("state_name", "lines"), PRINT_CASES)
def test_print_lines(world: World, state_name: str, lines: list[str]) -> None:
    _briefing, written = run_print(world, state_name)

    assert written == [exported_html(lines)]


# Pinned to Rich's export format (uv.lock pins Rich 15.0.0); regenerate it on a Rich upgrade
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
pre { margin: 0; padding: 0; white-space: pre-wrap; word-wrap: break-word; }
body { margin: 20px; font-family: monospace; }
</style>
</head>
<body>
    <pre style="font-family:Menlo,'DejaVu Sans Mono',consolas,'Courier New',monospace"><code style="font-family:inherit"><span style="font-weight: bold">Flight Weather Briefing: UAL1</span>
KAAA → KEEE
Alternate: KALT
Generated: <span style="color: #008080; text-decoration-color: #008080; font-weight: bold">2026</span>-<span style="color: #008080; text-decoration-color: #008080; font-weight: bold">10</span>-<span style="color: #008080; text-decoration-color: #008080; font-weight: bold">10</span> 1200Z  |  Route: 240nm  |  ETE: +<span style="color: #00ff00; text-decoration-color: #00ff00; font-weight: bold">0:32</span>

<span style="color: #008080; text-decoration-color: #008080; font-weight: bold">SYNOPSIS</span>
VFR departure. MVFR enroute. IFR conditions at arrival. improving to VFR.

<span style="color: #008080; text-decoration-color: #008080; font-weight: bold">━━━ DEPARTURE ━━━</span>
DEP KAAA <span style="font-weight: bold">(</span>   0nm       <span style="font-weight: bold">)</span> <span style="color: #808000; text-decoration-color: #808000">..............................</span>. <span style="color: #00ff00; text-decoration-color: #00ff00">VFR</span>
    <span style="color: #7f7f7f; text-decoration-color: #7f7f7f">27010KT </span><span style="color: #7fbfbf; text-decoration-color: #7fbfbf; font-weight: bold">10.</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f">0SM</span>
    <span style="color: #7f7f7f; text-decoration-color: #7f7f7f">101251Z 27010KT 10SM CLR </span><span style="color: #7fbfbf; text-decoration-color: #7fbfbf; font-weight: bold">15</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f">/</span><span style="color: #7fbfbf; text-decoration-color: #7fbfbf; font-weight: bold">05</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f"> A2992</span>

<span style="color: #008080; text-decoration-color: #008080; font-weight: bold">━━━ ENROUTE ━━━</span>
    <span style="color: #7f7f7f; text-decoration-color: #7f7f7f">  · ALPHA</span>
ENR KMMM <span style="font-weight: bold">(</span>  80nm  +<span style="color: #00ff00; text-decoration-color: #00ff00; font-weight: bold">0:10</span><span style="font-weight: bold">)</span> <span style="color: #808000; text-decoration-color: #808000">........................</span>.. <span style="color: #5599ff; text-decoration-color: #5599ff">MVFR</span>
    <span style="color: #7f7f7f; text-decoration-color: #7f7f7f">00000KT </span><span style="color: #7fbfbf; text-decoration-color: #7fbfbf; font-weight: bold">4.</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f">0SM BKN020</span>
    <span style="color: #7f7f7f; text-decoration-color: #7f7f7f">  · BRAVO</span>
    <span style="color: #7f7f7f; text-decoration-color: #7f7f7f">  · CHARL</span>
ENR KNNN <span style="font-weight: bold">(</span> 160nm  +<span style="color: #00ff00; text-decoration-color: #00ff00; font-weight: bold">0:21</span><span style="font-weight: bold">)</span> <span style="color: #808000; text-decoration-color: #808000">........................</span>.. <span style="color: #00ff00; text-decoration-color: #00ff00">VFR</span>
    <span style="color: #7f7f7f; text-decoration-color: #7f7f7f">27010KT</span>
    <span style="color: #7f7f7f; text-decoration-color: #7f7f7f">  · DELTA</span>
    <span style="color: #7f7f7f; text-decoration-color: #7f7f7f">  · ECHOO</span>

<span style="color: #008080; text-decoration-color: #008080; font-weight: bold">━━━ ARRIVAL ━━━</span>
ARR KEEE <span style="font-weight: bold">(</span> 240nm  +<span style="color: #00ff00; text-decoration-color: #00ff00; font-weight: bold">0:32</span><span style="font-weight: bold">)</span> <span style="color: #808000; text-decoration-color: #808000">........................</span>.. <span style="color: #800000; text-decoration-color: #800000">IFR</span>
    <span style="color: #7f7f7f; text-decoration-color: #7f7f7f">18005KT </span><span style="color: #7fbfbf; text-decoration-color: #7fbfbf; font-weight: bold">2.</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f">0SM OVC008</span>
    <span style="color: #7f7f7f; text-decoration-color: #7f7f7f">101251Z 18005KT 2SM BR OVC008 </span><span style="color: #7fbfbf; text-decoration-color: #7fbfbf; font-weight: bold">10</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f">/</span><span style="color: #7fbfbf; text-decoration-color: #7fbfbf; font-weight: bold">10</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f"> A2992</span>
    <span style="color: #7f7f7f; text-decoration-color: #7f7f7f">TAF: ↑ VFR </span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f; font-weight: bold">(</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f">FM</span><span style="color: #7f7f7f; text-decoration-color: #7f7f7f; font-weight: bold">)</span>

<span style="color: #008080; text-decoration-color: #008080; font-weight: bold">━━━ ALTERNATE ━━━</span>
ALT KALT <span style="font-weight: bold">(</span>   0nm       <span style="font-weight: bold">)</span> <span style="color: #808000; text-decoration-color: #808000">..............................</span>. <span style="color: #c0c0c0; text-decoration-color: #c0c0c0">UNK</span>
</code></pre>
</body>
</html>
"""


# _fetch_weather_async


async def run_fetch(world: World, **briefing_args: Any) -> Briefing:
    briefing = Briefing(**briefing_args)
    await briefing.screen._fetch_weather_async()
    return briefing


def calls(world: World) -> dict[str, Any]:
    """Every boundary call the fetch made."""
    return {
        "metar": world.metar_calls,
        "taf": world.taf_calls,
        "vatsim": world.vatsim_calls,
        "atis": world.atis_calls,
        "datis": world.datis_calls,
        "enroute": world.enroute_calls,
    }


def state(briefing: Briefing) -> dict[str, Any]:
    """The screen state the fetch leaves behind."""
    screen = briefing.screen
    return {
        "route_waypoints": screen.route_waypoints,
        "enroute_points": screen.enroute_points,
        "total_distance": screen.total_distance,
        "weather_data": screen.weather_data,
    }


KAAA_FETCHED = {
    "metar": METARS["KAAA"],
    "taf": "",
    "category": "VFR",
    "color": "#00ff00",
    "visibility_sm": 10.0,
    "ceiling_ft": None,
    "ceiling_layer": None,
    "wind": "27010KT",
}
KMMM_FETCHED = {
    "metar": METARS["KMMM"],
    "taf": "",
    "category": "MVFR",
    "color": "#5599ff",
    "visibility_sm": 4.0,
    "ceiling_ft": 2000,
    "ceiling_layer": "BKN020",
    "wind": "00000KT",
}
KEEE_FETCHED = {
    "metar": METARS["KEEE"],
    "taf": TAF_IMPROVING,
    "category": "IFR",
    "color": "red",
    "visibility_sm": 2.0,
    "ceiling_ft": 800,
    "ceiling_layer": "OVC008",
    "wind": "18005KT",
}
NO_METAR_FETCHED = {
    "metar": "",
    "taf": "",
    "category": "UNK",
    "color": "white",
    "visibility_sm": None,
    "ceiling_ft": None,
    "ceiling_layer": None,
    "wind": None,
}


async def test_fetch_full_route(world: World) -> None:
    briefing = await run_fetch(
        world, alternate="KALT", route="KAAA1 DCT ALPHA J12 BRAVO N0450F350 CHARL"
    )

    # Trig results can differ in the last bit across platforms' libm, so the exact
    # values come from the production functions and only their size is pinned here
    route_nm = haversine_distance_nm(37.0, -122.0, 41.0, -122.0)
    midpoint = sample_route_points(
        37.0, -122.0, 41.0, -122.0, interval_nm=200.0, min_points=1, max_points=6
    )
    assert route_nm == pytest.approx(240.1618429)
    assert midpoint == [pytest.approx((39.0, -122.0, 120.0809215))]
    fetched = ["KAAA", "KEEE", "KALT", "KMMM"]
    assert calls(world) == {
        "metar": [fetched],
        "taf": [fetched],
        "vatsim": 0,
        "atis": [],
        "datis": [],
        "enroute": [(midpoint, AIRPORTS, 100.0)],
    }
    assert world.enroute_calls[0][1] is AIRPORTS
    assert state(briefing) == {
        "route_waypoints": ["KAAA1", "ALPHA", "BRAVO", "CHARL"],
        "enroute_points": [
            {"icao": "KMMM", "distance_nm": 120.0},
            {"icao": "KAAA", "distance_nm": 125.0},
        ],
        "total_distance": route_nm,
        "weather_data": {
            "KAAA": KAAA_FETCHED,
            "KEEE": KEEE_FETCHED,
            "KALT": NO_METAR_FETCHED,
            "KMMM": KMMM_FETCHED,
        },
    }
    assert briefing.renders == [
        text(
            valid_header(240, "+0:32"),
            "",
            H_SYNOPSIS,
            FULL_SYNOPSIS,
            "",
            H_DEP,
            *KAAA_DEP,
            "",
            H_ENR,
            wp("KAAA1"),
            "ENR KMMM ( 120nm  +0:16) .......................... [#5599ff]MVFR[/#5599ff]",
            KMMM_COND,
            wp("ALPHA"),
            "ENR KAAA ( 125nm  +0:16) .......................... [#00ff00]VFR[/#00ff00]",
            "    [dim]27010KT 10.0SM[/dim]",
            wp("BRAVO"),
            wp("CHARL"),
            "",
            H_ARR,
            *KEEE_ARR,
            "",
            H_ALT,
            KALT_ALT,
        )
    ]


async def test_fetch_without_airport_data(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "UNIFIED_AIRPORT_DATA", None)

    briefing = await run_fetch(world)

    assert calls(world) == {
        "metar": [["KAAA", "KEEE"]],
        "taf": [["KAAA", "KEEE"]],
        "vatsim": 0,
        "atis": [],
        "datis": [],
        "enroute": [],
    }
    assert state(briefing) == {
        "route_waypoints": [],
        "enroute_points": [],
        "total_distance": 0.0,
        "weather_data": {"KAAA": KAAA_FETCHED, "KEEE": KEEE_FETCHED},
    }
    assert briefing.renders == [
        text(
            valid_header(0, "+0:00"),
            "",
            H_SYNOPSIS,
            WAYPOINTS_ONLY_SYNOPSIS,
            "",
            H_DEP,
            *KAAA_DEP,
            "",
            H_ARR,
            "ARR KEEE (   0nm       ) ............................... [red]IFR[/red]",
            *KEEE_DETAILS,
        )
    ]


async def test_fetch_with_empty_airport_data(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "UNIFIED_AIRPORT_DATA", {})

    briefing = await run_fetch(world)

    assert calls(world) == {
        "metar": [["KAAA", "KEEE"]],
        "taf": [["KAAA", "KEEE"]],
        "vatsim": 0,
        "atis": [],
        "datis": [],
        "enroute": [],
    }
    assert state(briefing) == {
        "route_waypoints": [],
        "enroute_points": [],
        "total_distance": 0.0,
        "weather_data": {"KAAA": KAAA_FETCHED, "KEEE": KEEE_FETCHED},
    }


async def test_fetch_departure_without_position(world: World) -> None:
    briefing = await run_fetch(world, departure="KNOP", alternate="KEEE")

    assert calls(world) == {
        "metar": [["KNOP", "KEEE"]],
        "taf": [["KNOP", "KEEE"]],
        "vatsim": 0,
        "atis": [],
        "datis": [],
        "enroute": [],
    }
    assert state(briefing) == {
        "route_waypoints": [],
        "enroute_points": [],
        "total_distance": 0.0,
        "weather_data": {"KNOP": NO_METAR_FETCHED, "KEEE": KEEE_FETCHED},
    }


async def test_fetch_failure_shows_the_error(world: World) -> None:
    world.metar_error = RuntimeError("metar feed down")

    briefing = await run_fetch(world)

    assert briefing.renders == ["[red]Error fetching weather: metar feed down[/red]"]
    assert briefing.screen.weather_data == {}
