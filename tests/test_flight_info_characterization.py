"""Characterization of the flight info modal: its body text, ETA and the loaders that feed them.

Each test constructs ``FlightInfoScreen`` directly, never mounted in a running app,
and replaces its ``_update_display`` with a recorder of the text the screen would
show at that moment, so every progressive update is pinned. METAR, spatial search,
VATSIM feed, nearest altimeter, ETA and on-ground lookups are faked at the
module boundary; time is a fixed clock patched over ``flight_info.datetime``.
Expected alternate distances come from ``haversine_distance_nm`` on the same
coordinates, so the pins hold whatever the platform's libm rounds in the last place.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

import pytest

from backend import haversine_distance_nm
from backend.data.navaids import MeaCheck, MeaViolation
from ui import config
from ui.modals import flight_info
from ui.modals.flight_info import FlightInfoScreen

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

# KAAA sits 30.02 nm south of KBBB and north of KDDD, 23.98 nm west of KCCC and 60.04 nm south of KEEE
AIRPORTS: dict[str, dict[str, Any]] = {
    "KAAA": {"latitude": 37.0, "longitude": -122.0, "city": "Alpha"},
    "KBBB": {"latitude": 37.5, "longitude": -122.0, "city": "Bravo"},
    "KCCC": {"latitude": 37.0, "longitude": -121.5, "city": "Charlie"},
    "KDDD": {"latitude": 36.5, "longitude": -122.0, "city": "Delta"},
    "KEEE": {"latitude": 38.0, "longitude": -122.0, "city": "Echo"},
    "KNOP": {"city": "Nopos"},
    "KNOC": {"latitude": 37.2, "longitude": -122.0},
}

VFR_METAR = "{} 101251Z 00000KT 10SM CLR 15/05 A2992"
METARS = {
    "KAAA": VFR_METAR.format("KAAA"),
    "KBBB": "KBBB 101251Z 00000KT 4SM BR BKN020 15/05 A2992",
    "KCCC": "KCCC 101251Z 00000KT 2SM BR OVC008 10/10 A2992",
    "KDDD": "KDDD 101251Z 00000KT 1/2SM FG OVC002 10/10 A2992",
    "KEEE": VFR_METAR.format("KEEE"),
    "KNOP": VFR_METAR.format("KNOP"),
}

IFR_WX = ("IFR", "red", "2SM", "OVC008")
LIFR_WX = ("LIFR", "magenta", "1/2SM", "OVC002")
MVFR_WX = ("MVFR", "#5599ff", "4SM", "BKN020")
VFR_WX = ("VFR", "#00ff00", "10SM", None)


def _distance(origin: str, to: str) -> float:
    """The distance the alternate search computes from origin to candidate, on this platform's libm."""
    start, end = AIRPORTS[origin], AIRPORTS[to]
    return haversine_distance_nm(
        start["latitude"], start["longitude"], end["latitude"], end["longitude"]
    )


KBBB_FROM_KAAA = ("KBBB", "MVFR", "#5599ff", _distance("KAAA", "KBBB"), "N")
KDDD_FROM_KAAA = ("KDDD", "VFR", "#00ff00", _distance("KAAA", "KDDD"), "S")
KAAA_FROM_KCCC = ("KAAA", "VFR", "#00ff00", _distance("KCCC", "KAAA"), "W")
KAAA_FROM_KDDD = ("KAAA", "VFR", "#00ff00", _distance("KDDD", "KAAA"), "N")

ALT = "[bold]Alpha Altimeter:[/bold] A2992 (KAAA, 4.0nm)"
LOADING_ALT = "[dim]Loading nearest altimeter...[/dim]"
CHECKING_MEA = "[dim]Checking MEA requirements...[/dim]"
WARNING = "[bold]VFR WEATHER WARNING[/bold]"
SEARCHING = "    [dim]Searching for alternates...[/dim]"
NONE_FOUND = "    [dim]No VFR/MVFR alternates within 100nm[/dim]"
KCCC_IFR = "  KCCC is [red bold]IFR[/red bold] (2SM OVC008)"
KDDD_LIFR = "  KDDD is [magenta bold]LIFR[/magenta bold] (1/2SM OVC002)"
KAAA_IFR = "  KAAA is [red bold]IFR[/red bold] (2SM OVC008)"
ETA = "ETA 45m (14:30 LT)"


class Fakes:
    """The module's outside world: answers and records every boundary call."""

    def __init__(self) -> None:
        self.metars: dict[str, str] = dict(METARS)
        self.metar_errors: dict[str, BaseException] = {}
        self.metar_calls: list[str] = []
        self.nearby: list[str] = []
        self.nearby_calls: list[tuple[Any, ...]] = []
        self.altimeter: tuple[str, str, float] | None = ("KAAA", "A2992", 4.04)
        self.altimeter_error: BaseException | None = None
        self.altimeter_calls: list[tuple[Any, ...]] = []
        self.vatsim: dict[str, Any] | BaseException | None = None
        self.vatsim_calls = 0
        self.eta: tuple[Any, Any, Any] = ("45m", "14:30", 0.75)
        self.eta_calls: list[tuple[Any, ...]] = []
        self.on_ground_at: str | None = None
        self.on_ground_calls: list[tuple[Any, ...]] = []
        self.debug: list[str] = []
        self.now = NOW

    def get_metar(self, icao: str) -> str:
        self.metar_calls.append(icao)
        if icao in self.metar_errors:
            raise self.metar_errors[icao]
        return self.metars.get(icao, "")

    def find_airports_near_position(
        self, lat: float, lon: float, airports: Any, radius_nm: float, max_results: int
    ) -> list[str]:
        self.nearby_calls.append((lat, lon, airports, radius_nm, max_results))
        return list(self.nearby)

    def find_nearest_airport_with_metar(
        self,
        lat: float,
        lon: float,
        airports: Any,
        max_distance_nm: float,
        aircraft_heading: Any,
        aircraft_groundspeed: Any,
    ) -> tuple[str, str, float] | None:
        self.altimeter_calls.append(
            (
                lat,
                lon,
                airports,
                max_distance_nm,
                aircraft_heading,
                aircraft_groundspeed,
            )
        )
        if self.altimeter_error is not None:
            raise self.altimeter_error
        return self.altimeter

    def download_vatsim_data(self) -> dict[str, Any] | None:
        self.vatsim_calls += 1
        if isinstance(self.vatsim, BaseException):
            raise self.vatsim
        return self.vatsim

    def calculate_eta(
        self, flight: dict[str, Any], airports: Any, speeds: Any
    ) -> tuple[Any, Any, Any]:
        self.eta_calls.append((flight, airports, speeds))
        return self.eta

    def get_nearest_airport_if_on_ground(
        self, flight: dict[str, Any], airports: Any
    ) -> str | None:
        self.on_ground_calls.append((flight, airports))
        return self.on_ground_at


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch) -> Iterator[Fakes]:
    fake = Fakes()
    monkeypatch.setattr(config, "UNIFIED_AIRPORT_DATA", AIRPORTS)
    monkeypatch.setattr(config, "AIRCRAFT_APPROACH_SPEEDS", {})
    for name in (
        "get_metar",
        "find_airports_near_position",
        "find_nearest_airport_with_metar",
        "download_vatsim_data",
        "calculate_eta",
        "get_nearest_airport_if_on_ground",
    ):
        monkeypatch.setattr(flight_info, name, getattr(fake, name))
    monkeypatch.setattr(flight_info, "debug", fake.debug.append)

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return fake.now

    monkeypatch.setattr(flight_info, "datetime", FakeDatetime)
    flight_info._VFR_ALTERNATES_CACHE.clear()
    yield fake
    flight_info._VFR_ALTERNATES_CACHE.clear()


def flight(
    plan: dict[str, Any] | None, *, at: str = "KAAA", groundspeed: int = 0
) -> dict[str, Any]:
    """A VATSIM pilot record placed on an airport of the fixture."""
    airport = AIRPORTS[at]
    return {
        "callsign": "N123AB",
        "cid": 1234567,
        "name": "Pat Pilot",
        "latitude": airport["latitude"],
        "longitude": airport["longitude"],
        "heading": 90,
        "groundspeed": groundspeed,
        "flight_plan": plan,
    }


def vfr_plan(departure: str = "KCCC", arrival: str = "KDDD") -> dict[str, Any]:
    return {
        "departure": departure,
        "arrival": arrival,
        "aircraft_short": "C172",
        "flight_rules": "V",
        "altitude": "VFR",
    }


def ifr_plan(**changes: Any) -> dict[str, Any]:
    plan = {
        "departure": "KAAA",
        "arrival": "KEEE",
        "aircraft_short": "B738",
        "flight_rules": "I",
        "altitude": "8000",
    }
    plan.update(changes)
    return plan


def screen_for(
    data: dict[str, Any], **state: Any
) -> tuple[FlightInfoScreen, list[str]]:
    """The screen with its state set, and the list every display update appends its text to."""
    screen = FlightInfoScreen(data)
    for name, value in state.items():
        setattr(screen, name, value)
    renders: list[str] = []
    screen._update_display = lambda: renders.append(screen._format_flight_info())
    return screen, renders


def text(*lines: str) -> str:
    return "\n".join(lines)


# _get_eta_info


def _moved(data: dict[str, Any], **changes: Any) -> dict[str, Any]:
    data.update(changes)
    return data


@pytest.mark.parametrize(
    ("data", "expected", "eta_calls"),
    [
        pytest.param(flight(None, groundspeed=250), None, 0, id="no flight plan"),
        pytest.param(flight({}, groundspeed=250), None, 0, id="empty flight plan"),
        pytest.param(
            flight(ifr_plan(arrival=""), groundspeed=250), None, 0, id="blank arrival"
        ),
        pytest.param(
            flight(ifr_plan(arrival="----"), groundspeed=250),
            None,
            0,
            id="placeholder arrival",
        ),
        pytest.param(
            _moved(flight(ifr_plan(), groundspeed=250), latitude=None),
            None,
            0,
            id="no latitude",
        ),
        pytest.param(
            _moved(flight(ifr_plan(), groundspeed=250), longitude=None),
            None,
            0,
            id="no longitude",
        ),
        pytest.param(
            flight(ifr_plan(arrival="KZZZ"), groundspeed=250),
            None,
            0,
            id="unknown arrival",
        ),
        pytest.param(
            flight(ifr_plan(arrival="KNOP"), groundspeed=250),
            None,
            0,
            id="arrival without position",
        ),
        pytest.param(
            flight(ifr_plan(), groundspeed=0), None, 0, id="stopped away from arrival"
        ),
        pytest.param(
            {k: v for k, v in flight(ifr_plan()).items() if k != "groundspeed"},
            None,
            0,
            id="no groundspeed away from arrival",
        ),
        pytest.param(
            flight(ifr_plan(), at="KEEE", groundspeed=0),
            "LANDED",
            0,
            id="stopped at arrival",
        ),
        pytest.param(
            flight(ifr_plan(), at="KEEE", groundspeed=40),
            "LANDED",
            0,
            id="40 kt at arrival",
        ),
        pytest.param(
            _moved(flight(ifr_plan(), groundspeed=10), latitude=37.95),
            "LANDED",
            0,
            id="taxiing 3 nm out",
        ),
        pytest.param(
            _moved(flight(ifr_plan(), groundspeed=10), latitude=37.9169),
            "LANDED",
            0,
            id="taxiing 4.99 nm out",
        ),
        pytest.param(
            _moved(flight(ifr_plan(), groundspeed=10), latitude=37.9165),
            None,
            0,
            id="taxiing 5.01 nm out",
        ),
        pytest.param(
            _moved(flight(ifr_plan(), groundspeed=10), latitude=37.9),
            None,
            0,
            id="taxiing 6 nm out",
        ),
        pytest.param(
            flight(ifr_plan(), at="KEEE", groundspeed=41), ETA, 1, id="41 kt at arrival"
        ),
        pytest.param(flight(ifr_plan(), groundspeed=250), ETA, 1, id="in flight"),
    ],
)
def test_eta_info(
    fakes: Fakes, data: dict[str, Any], expected: str | None, eta_calls: int
) -> None:
    assert FlightInfoScreen(data)._get_eta_info() == expected
    assert len(fakes.eta_calls) == eta_calls


def test_eta_info_passes_the_flight_with_arrival_at_top_level(fakes: Fakes) -> None:
    plan = ifr_plan()

    FlightInfoScreen(flight(plan, groundspeed=250))._get_eta_info()

    assert fakes.eta_calls == [
        (
            {
                "arrival": "KEEE",
                "latitude": 37.0,
                "longitude": -122.0,
                "groundspeed": 250,
                "flight_plan": plan,
            },
            AIRPORTS,
            {},
        )
    ]
    assert fakes.eta_calls[0][1] is AIRPORTS


def test_eta_info_without_airport_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "UNIFIED_AIRPORT_DATA", None)

    assert FlightInfoScreen(flight(ifr_plan(), groundspeed=250))._get_eta_info() is None


def test_eta_info_when_distance_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def bad_distance(*args: float) -> float:
        raise ValueError("bad coordinates")

    monkeypatch.setattr(flight_info, "haversine_distance_nm", bad_distance)

    assert FlightInfoScreen(flight(ifr_plan(), groundspeed=250))._get_eta_info() is None


@pytest.mark.parametrize(
    "eta",
    [("----", "----", 0.0), ("", "", 0.0), (None, None, None)],
    ids=["placeholder", "blank", "none"],
)
def test_eta_info_without_an_eta(fakes: Fakes, eta: tuple[Any, Any, Any]) -> None:
    fakes.eta = eta

    assert FlightInfoScreen(flight(ifr_plan(), groundspeed=250))._get_eta_info() is None


# _format_flight_info

ROUTE = " ".join(f"WPT{i:02d}" for i in range(20))
LONG_REMARKS = " ".join(letter * 40 for letter in "ABCDEFGHIJKLMN")
FIVE_LINE_REMARKS = " ".join(letter * 40 for letter in "ABCDEFGHIJ")
MEA_CHECK = MeaCheck(
    max_mea=12000,
    violations=[
        MeaViolation(
            airway="V12",
            segment_start="ABQ",
            segment_end="OTO",
            mea=12000,
            level_ft=None,
        )
    ],
    skipped=[],
)
EMPTY_MEA_CHECK = MeaCheck(max_mea=None, violations=[], skipped=[])
LOADED = {"altimeter_loading": False}
ALTERNATES_W_NW = [
    KAAA_FROM_KCCC,
    ("KBBB", "MVFR", "#5599ff", 30.4, "NW"),
]


FORMAT_CASES = [
    pytest.param(
        flight(
            ifr_plan(
                alternate="KBBB",
                aircraft_faa="H/B738/L",
                altitude="35000",
                assigned_transponder="4521",
                route=ROUTE,
                remarks="PBN/A1B1 /V/",
            ),
            groundspeed=250,
        ),
        {"altimeter_loading": False, "altimeter_info": ALT},
        text(
            f"KAAA → KEEE (Altn: KBBB) // B738/L // 35,000 // IFR // SQ: 4521 // {ETA}",
            "",
            "[bold]ROUTE[/bold]",
            "  WPT00 WPT01 WPT02 WPT03 WPT04 WPT05 WPT06 WPT07 WPT08 WPT09 WPT10 WPT11 WPT12",
            "  WPT13 WPT14 WPT15 WPT16 WPT17 WPT18 WPT19",
            "",
            ALT,
            "",
            "[bold]REMARKS[/bold]",
            "  PBN/A1B1 /V/",
            "",
        ),
        id="full IFR plan in flight",
    ),
    pytest.param(
        flight({"flight_rules": "x"}),
        {},
        text("---- → ---- // ---- // ?", "", LOADING_ALT, ""),
        id="sparse plan",
    ),
    pytest.param(
        flight(
            {
                "departure": "KAAA",
                "arrival": "KBBB",
                "aircraft": "B738/M-SDE2E3FGHIJ1RWXY/LB1",
                "aircraft_faa": "B738",
                "flight_rules": "i",
                "altitude": "FL350",
                "assigned_transponder": "0000",
            }
        ),
        {"altimeter_loading": False, "altimeter_info": "", "mea_loading": True},
        text(
            "KAAA → KBBB // B738/M-SDE2E3FGHIJ1RWXY/LB1 // FL350 // IFR",
            "",
            CHECKING_MEA,
            "",
        ),
        id="aircraft fallback, unparsed altitude, unassigned squawk, MEA loading",
    ),
    pytest.param(
        flight(
            {
                "departure": "KAAA",
                "arrival": "KBBB",
                "aircraft_short": "C172",
                "aircraft_faa": "C172/",
                "flight_rules": "V",
                "altitude": "4500",
                "assigned_transponder": "1200",
            },
            at="KBBB",
        ),
        LOADED,
        text("KAAA → KBBB // C172 // 4,500 // VFR // SQ: 1200 // LANDED", ""),
        id="empty equipment suffix, VFR rules, landed",
    ),
    pytest.param(
        flight({**vfr_plan("KAAA", "KBBB"), "altitude": "vfr", "flight_rules": "I"}),
        {"mea_loading": True},
        text("KAAA → KBBB // C172 // vfr", "", CHECKING_MEA, "", LOADING_ALT, ""),
        id="VFR altitude overrides IFR rules, MEA loading still shown",
    ),
    pytest.param(
        flight(ifr_plan(altitude="10000")),
        {**LOADED, "mea_info": MEA_CHECK},
        text(
            "KAAA → KEEE // B738 // 10,000 // IFR",
            "",
            "[bold yellow]MEA WARNING[/bold yellow]",
            "  Filed altitude 10,000 < required MEA 12,000",
            "  [dim]V12 (ABQ→OTO) requires 12,000[/dim]",
            "",
        ),
        id="IFR MEA warning",
    ),
    pytest.param(
        flight(ifr_plan()),
        {**LOADED, "mea_info": EMPTY_MEA_CHECK, "mea_loading": True},
        text("KAAA → KEEE // B738 // 8,000 // IFR", ""),
        id="IFR MEA result with nothing to show",
    ),
    pytest.param(
        flight(vfr_plan("KAAA", "KBBB")),
        {**LOADED, "mea_info": MEA_CHECK},
        text("KAAA → KBBB // C172 // VFR", ""),
        id="VFR flight ignores MEA result",
    ),
    pytest.param(
        flight(vfr_plan(), at="KCCC"),
        {
            **LOADED,
            "altimeter_info": "ALT",
            "departure_weather": IFR_WX,
            "arrival_weather": LIFR_WX,
            "departure_alternates": ALTERNATES_W_NW,
        },
        text(
            "KCCC → KDDD // C172 // VFR",
            "",
            WARNING,
            KCCC_IFR,
            "    Consider: [#00ff00]KAAA[/#00ff00] (24nm W), [#5599ff]KBBB[/#5599ff] (30nm NW)",
            KDDD_LIFR,
            SEARCHING,
            "",
            "ALT",
            "",
        ),
        id="on ground: departure alternates, arrival searching",
    ),
    pytest.param(
        flight(vfr_plan(), at="KCCC"),
        {
            **LOADED,
            "departure_weather": MVFR_WX,
            "arrival_weather": ("IFR", "red", None, None),
            "arrival_alternates": [
                ("KEEE", "VFR", "#00ff00", _distance("KAAA", "KEEE"), "N")
            ],
            "alternates_searched": True,
        },
        text(
            "KCCC → KDDD // C172 // VFR",
            "",
            WARNING,
            "  KCCC is [#5599ff bold]MVFR[/#5599ff bold] (4SM BKN020)",
            "  KDDD is [red bold]IFR[/red bold]",
            "    Consider: [#00ff00]KEEE[/#00ff00] (60nm N)",
            "",
        ),
        id="MVFR departure, IFR arrival without details and with alternates",
    ),
    pytest.param(
        flight(vfr_plan(), at="KCCC"),
        {
            **LOADED,
            "departure_weather": IFR_WX,
            "departure_alternates": [],
            "arrival_weather": VFR_WX,
            "alternates_searched": True,
        },
        text("KCCC → KDDD // C172 // VFR", "", WARNING, KCCC_IFR, NONE_FOUND, ""),
        id="on ground: no departure alternates found",
    ),
    pytest.param(
        flight(vfr_plan(), groundspeed=120),
        {
            "departure_weather": IFR_WX,
            "arrival_weather": LIFR_WX,
            "arrival_alternates": [],
            "alternates_searched": True,
        },
        text(
            f"KCCC → KDDD // C172 // VFR // {ETA}",
            "",
            WARNING,
            KCCC_IFR,
            KDDD_LIFR,
            NONE_FOUND,
            "",
            LOADING_ALT,
            "",
        ),
        id="in flight: no departure alternates, none found for arrival",
    ),
    pytest.param(
        flight(vfr_plan(), at="KDDD"),
        {**LOADED, "departure_weather": VFR_WX, "arrival_weather": LIFR_WX},
        text("KCCC → KDDD // C172 // VFR // LANDED", "", WARNING, KDDD_LIFR, ""),
        id="landed at arrival: no arrival alternates",
    ),
    pytest.param(
        flight(vfr_plan(), at="KCCC"),
        {**LOADED, "departure_weather": VFR_WX, "arrival_weather": VFR_WX},
        text("KCCC → KDDD // C172 // VFR", ""),
        id="VFR weather at both ends",
    ),
    pytest.param(
        flight(ifr_plan(remarks=LONG_REMARKS)),
        LOADED,
        text(
            "KAAA → KEEE // B738 // 8,000 // IFR",
            "",
            "[bold]REMARKS[/bold]",
            f"  {'A' * 40} {'B' * 40}",
            f"  {'C' * 40} {'D' * 40}",
            f"  {'E' * 40} {'F' * 40}",
            f"  {'G' * 40} {'H' * 40}",
            f"  {'I' * 40} {'J' * 40}",
            "  ... (2 more lines)",
            "",
        ),
        id="remarks past five lines",
    ),
    pytest.param(
        flight(ifr_plan(remarks=FIVE_LINE_REMARKS)),
        LOADED,
        text(
            "KAAA → KEEE // B738 // 8,000 // IFR",
            "",
            "[bold]REMARKS[/bold]",
            f"  {'A' * 40} {'B' * 40}",
            f"  {'C' * 40} {'D' * 40}",
            f"  {'E' * 40} {'F' * 40}",
            f"  {'G' * 40} {'H' * 40}",
            f"  {'I' * 40} {'J' * 40}",
            "",
        ),
        id="remarks of exactly five lines",
    ),
]


@pytest.mark.parametrize(("data", "state", "expected"), FORMAT_CASES)
def test_format_flight_info(
    data: dict[str, Any], state: dict[str, Any], expected: str
) -> None:
    screen, _renders = screen_for(data, **state)

    assert screen._format_flight_info() == expected


@pytest.mark.parametrize(
    ("data", "state", "on_ground_at", "expected", "looked_up"),
    [
        pytest.param(
            flight(None),
            {},
            "KAAA",
            text(
                "[bold]NO FLIGHT PLAN[/bold]",
                "",
                "[bold]On Ground at [/bold]KAAA - Alpha",
                "",
                LOADING_ALT,
                "",
            ),
            True,
            id="on ground at a known airport, altimeter loading",
        ),
        pytest.param(
            flight({}, groundspeed=40),
            {"altimeter_loading": False, "altimeter_info": "ALT"},
            "KNOC",
            text(
                "[bold]NO FLIGHT PLAN[/bold]",
                "",
                "[bold]On Ground at [/bold]KNOC - Unknown",
                "",
                "ALT",
                "",
            ),
            True,
            id="empty plan, airport without a city",
        ),
        pytest.param(
            {k: v for k, v in flight(None).items() if k != "groundspeed"},
            {"altimeter_loading": False, "altimeter_info": ""},
            None,
            text(
                "[bold]NO FLIGHT PLAN[/bold]",
                "",
                "[dim]No altimeter information available[/dim]",
                "",
            ),
            True,
            id="no groundspeed, not near an airport",
        ),
        pytest.param(
            flight(None, groundspeed=41),
            {"altimeter_loading": False},
            "KAAA",
            text(
                "[bold]NO FLIGHT PLAN[/bold]",
                "",
                "[dim]No altimeter information available[/dim]",
                "",
            ),
            False,
            id="moving",
        ),
    ],
)
def test_format_flight_info_without_plan(
    fakes: Fakes,
    data: dict[str, Any],
    state: dict[str, Any],
    on_ground_at: str | None,
    expected: str,
    looked_up: bool,
) -> None:
    fakes.on_ground_at = on_ground_at
    screen, _renders = screen_for(data, **state)

    assert screen._format_flight_info() == expected
    assert fakes.on_ground_calls == ([(data, AIRPORTS)] if looked_up else [])


def test_format_flight_info_without_plan_or_airport_data(
    fakes: Fakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "UNIFIED_AIRPORT_DATA", None)
    fakes.on_ground_at = "KAAA"
    screen, _renders = screen_for(
        flight(None), altimeter_loading=False, altimeter_info="ALT"
    )

    assert screen._format_flight_info() == text(
        "[bold]NO FLIGHT PLAN[/bold]", "", "ALT", ""
    )
    assert fakes.on_ground_calls == []


# _find_vfr_alternates_async

ALTERNATES_HEAD = ("KAAA → KEEE // C172 // VFR", "", WARNING, KAAA_IFR)


def _alternates_screen() -> tuple[FlightInfoScreen, list[str]]:
    return screen_for(
        flight(vfr_plan("KAAA", "KEEE")),
        altimeter_loading=False,
        departure_weather=IFR_WX,
    )


async def test_alternates_search_reports_each_find_and_caches(fakes: Fakes) -> None:
    fakes.metars["KDDD"] = VFR_METAR.format("KDDD")
    fakes.nearby = [
        "KAAA",
        "K1AB",
        "KAB",
        "KXXX",
        "KCCC",
        "KNOP",
        "KBBB",
        "KDDD",
        "KEEE",
    ]
    screen, renders = _alternates_screen()

    await screen._find_vfr_alternates_async(
        "KAAA", "departure_alternates", max_results=2
    )

    assert fakes.nearby_calls == [(37.0, -122.0, AIRPORTS, 100.0, 500)]
    assert fakes.metar_calls == ["KXXX", "KCCC", "KNOP", "KBBB", "KDDD"]
    assert screen.departure_alternates == [KBBB_FROM_KAAA, KDDD_FROM_KAAA]
    assert renders == [
        text(*ALTERNATES_HEAD, "    Consider: [#5599ff]KBBB[/#5599ff] (30nm N)", ""),
        text(
            *ALTERNATES_HEAD,
            "    Consider: [#5599ff]KBBB[/#5599ff] (30nm N), [#00ff00]KDDD[/#00ff00] (30nm S)",
            "",
        ),
    ]
    assert flight_info._VFR_ALTERNATES_CACHE == {
        "KAAA": {"result": [KBBB_FROM_KAAA, KDDD_FROM_KAAA], "timestamp": NOW}
    }
    assert (
        flight_info._VFR_ALTERNATES_CACHE["KAAA"]["result"]
        is screen.departure_alternates
    )


async def test_alternates_search_runs_out_of_candidates(fakes: Fakes) -> None:
    fakes.nearby = ["KBBB"]
    screen, renders = _alternates_screen()

    await screen._find_vfr_alternates_async("KAAA", "arrival_alternates")

    assert screen.arrival_alternates == [KBBB_FROM_KAAA]
    assert screen.departure_alternates is None
    assert len(renders) == 1
    assert flight_info._VFR_ALTERNATES_CACHE == {
        "KAAA": {"result": [KBBB_FROM_KAAA], "timestamp": NOW}
    }


async def test_alternates_search_stops_when_cancelled(fakes: Fakes) -> None:
    fakes.nearby = ["KBBB", "KDDD", "KEEE"]
    fakes.metar_errors["KDDD"] = asyncio.CancelledError()
    screen, renders = _alternates_screen()

    await screen._find_vfr_alternates_async("KAAA", "departure_alternates")

    assert fakes.metar_calls == ["KBBB", "KDDD"]
    assert screen.departure_alternates == [KBBB_FROM_KAAA]
    assert renders == [
        text(*ALTERNATES_HEAD, "    Consider: [#5599ff]KBBB[/#5599ff] (30nm N)", "")
    ]
    assert flight_info._VFR_ALTERNATES_CACHE == {}


async def test_alternates_search_uses_a_fresh_cache_entry(fakes: Fakes) -> None:
    cached = [KBBB_FROM_KAAA]
    flight_info._VFR_ALTERNATES_CACHE["KAAA"] = {
        "result": cached,
        "timestamp": NOW - timedelta(seconds=59),
    }
    screen, renders = _alternates_screen()

    await screen._find_vfr_alternates_async("KAAA", "departure_alternates")

    assert screen.departure_alternates is cached
    assert renders == [
        text(*ALTERNATES_HEAD, "    Consider: [#5599ff]KBBB[/#5599ff] (30nm N)", "")
    ]
    assert fakes.nearby_calls == []
    assert fakes.metar_calls == []
    assert fakes.debug == ["[VFR_ALT] Cache hit for KAAA"]


async def test_alternates_search_ignores_a_stale_cache_entry_and_finds_none(
    fakes: Fakes,
) -> None:
    flight_info._VFR_ALTERNATES_CACHE["KAAA"] = {
        "result": [KBBB_FROM_KAAA],
        "timestamp": NOW - timedelta(seconds=60),
    }
    fakes.nearby = ["KAAA", "K1AB", "KAB"]
    screen, renders = _alternates_screen()

    await screen._find_vfr_alternates_async("KAAA", "departure_alternates")

    assert screen.departure_alternates == []
    assert renders == []
    assert fakes.metar_calls == []
    assert flight_info._VFR_ALTERNATES_CACHE == {
        "KAAA": {"result": [], "timestamp": NOW}
    }


@pytest.mark.parametrize(
    ("origin", "airports"),
    [
        pytest.param("", AIRPORTS, id="no origin"),
        pytest.param("KAAA", None, id="no airport data"),
        pytest.param("KAAA", {}, id="empty airport data"),
        pytest.param("KZZZ", AIRPORTS, id="unknown origin"),
        pytest.param("KNOP", AIRPORTS, id="origin without position"),
    ],
)
async def test_alternates_search_gives_up_without_an_origin_position(
    fakes: Fakes,
    monkeypatch: pytest.MonkeyPatch,
    origin: str,
    airports: dict[str, Any] | None,
) -> None:
    monkeypatch.setattr(config, "UNIFIED_AIRPORT_DATA", airports)
    fakes.nearby = ["KBBB"]
    screen, renders = screen_for(flight(None), altimeter_loading=False)

    await screen._find_vfr_alternates_async(origin, "departure_alternates")

    assert screen.departure_alternates is None
    assert renders == []
    assert fakes.nearby_calls == []
    assert flight_info._VFR_ALTERNATES_CACHE == {}


# _load_altimeter_info

VFR_HEAD = ("KCCC → KDDD // C172 // VFR", "")
CONSIDER_KAAA_W = "    Consider: [#00ff00]KAAA[/#00ff00] (24nm W)"
CONSIDER_KAAA_N = "    Consider: [#00ff00]KAAA[/#00ff00] (30nm N)"
BOTH_SEARCHING = (WARNING, KCCC_IFR, SEARCHING, KDDD_LIFR, SEARCHING, "")
DEPARTURE_FOUND = (WARNING, KCCC_IFR, CONSIDER_KAAA_W, KDDD_LIFR, SEARCHING, "")
BOTH_FOUND = (WARNING, KCCC_IFR, CONSIDER_KAAA_W, KDDD_LIFR, CONSIDER_KAAA_N, "")


async def test_altimeter_load_for_ifr_flight(fakes: Fakes) -> None:
    screen, renders = screen_for(flight(ifr_plan(), groundspeed=250))

    await screen._load_altimeter_info()

    assert screen.altimeter_info == ALT
    assert screen.altimeter_loading is False
    assert fakes.altimeter_calls == [(37.0, -122.0, AIRPORTS, 100.0, 90, 250)]
    assert renders == [
        text(f"KAAA → KEEE // B738 // 8,000 // IFR // {ETA}", "", ALT, "")
    ]
    assert fakes.metar_calls == []
    assert screen.alternates_searched is False


async def test_altimeter_load_for_vfr_flight_in_vfr_weather(fakes: Fakes) -> None:
    screen, renders = screen_for(flight(vfr_plan("KAAA", "KEEE")))

    await screen._load_altimeter_info()

    assert fakes.metar_calls == ["KAAA", "KEEE"]
    assert screen.departure_weather == VFR_WX
    assert screen.arrival_weather == VFR_WX
    assert screen.alternates_searched is True
    assert renders == [text("KAAA → KEEE // C172 // VFR", "", ALT, "")] * 3
    assert fakes.nearby_calls == []


async def test_altimeter_load_for_vfr_flight_without_airports(fakes: Fakes) -> None:
    screen, renders = screen_for(
        flight({"aircraft_short": "C172", "flight_rules": "V", "altitude": "VFR"})
    )

    await screen._load_altimeter_info()

    assert fakes.metar_calls == []
    assert screen.departure_weather is None
    assert screen.arrival_weather is None
    assert screen.alternates_searched is True
    assert renders == [text("---- → ---- // C172 // VFR", "", ALT, "")] * 3


async def test_altimeter_load_searches_alternates_for_ifr_ends(fakes: Fakes) -> None:
    fakes.nearby = ["KCCC", "KDDD", "KAAA"]
    screen, renders = screen_for(flight(vfr_plan(), at="KCCC"))

    await screen._load_altimeter_info()

    assert fakes.metar_calls == ["KCCC", "KDDD", "KDDD", "KAAA", "KCCC", "KAAA"]
    assert fakes.nearby_calls == [
        (37.0, -121.5, AIRPORTS, 100.0, 500),
        (36.5, -122.0, AIRPORTS, 100.0, 500),
    ]
    assert screen.departure_weather == IFR_WX
    assert screen.arrival_weather == LIFR_WX
    assert screen.departure_alternates == [KAAA_FROM_KCCC]
    assert screen.arrival_alternates == [KAAA_FROM_KDDD]
    assert screen.alternates_searched is True
    assert renders == [
        text(*VFR_HEAD, ALT, ""),
        text(*VFR_HEAD, *BOTH_SEARCHING, ALT, ""),
        text(*VFR_HEAD, *DEPARTURE_FOUND, ALT, ""),
        text(*VFR_HEAD, *BOTH_FOUND, ALT, ""),
        text(*VFR_HEAD, *BOTH_FOUND, ALT, ""),
    ]


async def test_altimeter_load_failure_clears_the_altimeter(
    fakes: Fakes, capsys: pytest.CaptureFixture[str]
) -> None:
    fakes.nearby = ["KAAA"]
    fakes.metar_errors["KAAA"] = RuntimeError("metar feed down")
    screen, renders = screen_for(flight(vfr_plan(), at="KCCC"))

    await screen._load_altimeter_info()

    assert screen.altimeter_info == ""
    assert screen.altimeter_loading is False
    assert screen.alternates_searched is False
    assert renders == [
        text(*VFR_HEAD, ALT, ""),
        text(*VFR_HEAD, *BOTH_SEARCHING, ALT, ""),
        text(*VFR_HEAD, *BOTH_SEARCHING),
    ]
    assert capsys.readouterr().out == "Error loading altimeter info: metar feed down\n"


async def test_altimeter_load_cancelled(fakes: Fakes) -> None:
    fakes.altimeter_error = asyncio.CancelledError()
    screen, renders = screen_for(flight(vfr_plan(), at="KCCC"))

    await screen._load_altimeter_info()

    assert screen.altimeter_info is None
    assert screen.altimeter_loading is True
    assert renders == []
    assert fakes.metar_calls == []


# _load_fresh_flight_data


def _refreshing(
    data: dict[str, Any], **state: Any
) -> tuple[FlightInfoScreen, list[str]]:
    return screen_for(data, altimeter_loading=False, _refresh_in_progress=True, **state)


async def test_refresh_takes_the_fresh_pilot_record(fakes: Fakes) -> None:
    fresh = _moved(flight(ifr_plan(), groundspeed=300), latitude=37.5)
    fakes.vatsim = {"pilots": [{"callsign": "OTHER1"}, fresh]}
    screen, renders = _refreshing(flight(ifr_plan(), groundspeed=250))

    await screen._load_fresh_flight_data()

    assert screen.flight_data is fresh
    assert screen.altimeter_info == ALT
    assert fakes.altimeter_calls == [(37.5, -122.0, AIRPORTS, 100.0, 90, 300)]
    assert screen._refresh_in_progress is False
    assert renders == [
        text(f"KAAA → KEEE // B738 // 8,000 // IFR // {ETA}", "", ALT, "")
    ]
    assert fakes.metar_calls == []


@pytest.mark.parametrize(
    ("callsign", "vatsim", "downloads", "logged"),
    [
        pytest.param(
            "N123AB", {"pilots": [{"callsign": "OTHER1"}]}, 1, [], id="pilot gone"
        ),
        pytest.param("N123AB", None, 1, [], id="no feed"),
        pytest.param(
            "N123AB",
            RuntimeError("feed down"),
            1,
            ["Could not fetch fresh pilot data for N123AB: feed down"],
            id="feed error",
        ),
        pytest.param("", {"pilots": [{"callsign": ""}]}, 0, [], id="no callsign"),
    ],
)
async def test_refresh_keeps_the_record_without_a_fresh_one(
    fakes: Fakes, callsign: str, vatsim: Any, downloads: int, logged: list[str]
) -> None:
    fakes.vatsim = vatsim
    data = {**flight(ifr_plan(), groundspeed=250), "callsign": callsign}
    screen, renders = _refreshing(data)

    await screen._load_fresh_flight_data()

    assert screen.flight_data is data
    assert fakes.vatsim_calls == downloads
    assert fakes.debug == logged
    assert screen.altimeter_info == ALT
    assert screen._refresh_in_progress is False
    assert renders == [
        text(f"KAAA → KEEE // B738 // 8,000 // IFR // {ETA}", "", ALT, "")
    ]


async def test_refresh_searches_alternates_for_ifr_ends(fakes: Fakes) -> None:
    moved = flight(vfr_plan(), at="KCCC")
    fakes.vatsim = {"pilots": [moved]}
    fakes.nearby = ["KCCC", "KDDD", "KAAA"]
    screen, renders = _refreshing(
        flight(vfr_plan()), departure_alternates=[("OLD", "VFR", "#00ff00", 1.0, "N")]
    )

    await screen._load_fresh_flight_data()

    assert screen.flight_data is moved
    assert fakes.metar_calls == ["KCCC", "KDDD", "KDDD", "KAAA", "KCCC", "KAAA"]
    assert screen.departure_alternates == [KAAA_FROM_KCCC]
    assert screen.arrival_alternates == [KAAA_FROM_KDDD]
    assert screen.alternates_searched is True
    assert screen._refresh_in_progress is False
    assert renders == [
        text(*VFR_HEAD, *DEPARTURE_FOUND, ALT, ""),
        text(*VFR_HEAD, *BOTH_FOUND, ALT, ""),
        text(*VFR_HEAD, *BOTH_FOUND, ALT, ""),
    ]


async def test_refresh_clears_alternates_when_weather_is_vfr(fakes: Fakes) -> None:
    old = [("OLD", "VFR", "#00ff00", 1.0, "N")]
    screen, renders = _refreshing(
        flight(vfr_plan("KAAA", "KEEE")),
        departure_alternates=old,
        arrival_alternates=old,
    )

    await screen._load_fresh_flight_data()

    assert fakes.metar_calls == ["KAAA", "KEEE"]
    assert screen.departure_weather == VFR_WX
    assert screen.arrival_weather == VFR_WX
    assert screen.departure_alternates is None
    assert screen.arrival_alternates is None
    assert screen.alternates_searched is True
    assert renders == [text("KAAA → KEEE // C172 // VFR", "", ALT, "")]


async def test_refresh_without_departure_keeps_its_old_weather(fakes: Fakes) -> None:
    plan = {
        "arrival": "KEEE",
        "aircraft_short": "C172",
        "flight_rules": "V",
        "altitude": "VFR",
    }
    screen, renders = _refreshing(flight(plan), departure_weather=IFR_WX)

    await screen._load_fresh_flight_data()

    assert fakes.metar_calls == ["KEEE"]
    assert fakes.nearby_calls == []
    assert screen.departure_weather == IFR_WX
    assert screen.departure_alternates == []
    assert screen.arrival_alternates is None
    assert renders == [
        text(
            "---- → KEEE // C172 // VFR",
            "",
            WARNING,
            "  ---- is [red bold]IFR[/red bold] (2SM OVC008)",
            NONE_FOUND,
            "",
            ALT,
            "",
        )
    ]


async def test_refresh_failure_keeps_existing_data(fakes: Fakes) -> None:
    fakes.nearby = ["KAAA"]
    fakes.metar_errors["KAAA"] = RuntimeError("metar feed down")
    screen, renders = _refreshing(flight(vfr_plan(), at="KCCC"))

    await screen._load_fresh_flight_data()

    assert fakes.debug == [
        "Flight info refresh failed, keeping existing data: metar feed down"
    ]
    assert fakes.metar_calls == ["KCCC", "KDDD", "KAAA"]
    assert screen.departure_alternates == []
    assert screen.arrival_alternates is None
    assert screen.alternates_searched is False
    assert screen._refresh_in_progress is False
    assert renders == [text(*VFR_HEAD, *BOTH_SEARCHING, ALT, "")]


async def test_refresh_cancelled_still_ends_the_refresh(fakes: Fakes) -> None:
    fakes.vatsim = asyncio.CancelledError()
    screen, renders = _refreshing(
        flight(ifr_plan(), groundspeed=250), altimeter_info="OLD ALT"
    )

    await screen._load_fresh_flight_data()

    assert screen.altimeter_info == "OLD ALT"
    assert fakes.altimeter_calls == []
    assert screen._refresh_in_progress is False
    assert renders == [
        text(f"KAAA → KEEE // B738 // 8,000 // IFR // {ETA}", "", "OLD ALT", "")
    ]
