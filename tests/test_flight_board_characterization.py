"""Characterization of the flight board's precache, flight info and change-notification paths.

Each test constructs ``FlightBoardScreen`` directly, never mounted in a running app,
and gives it a recorder in place of its notification manager, so every toast the
board would show is pinned with its exact text and flash flag. METARs, the VATSIM
feed, VATSIM ATIS, D-ATIS and the spatial search are faked at the module boundary;
ATIS parsing and flight categories run for real on the texts below.

For ``action_show_flight_info`` the two tables are fakes answering focus, cursor
and row lookups, and the app is a recorder of the screens pushed onto it.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from textual.widgets.data_table import RowDoesNotExist

from backend.core.flights import AirportInfo, ArrivalInfo, DepartureInfo
from ui import config
from ui.modals import flight_board
from ui.modals.flight_board import FlightBoardScreen
from ui.modals.flight_info import FlightInfoScreen

NAMES = {
    "KAAA": "Alpha Intl",
    "KBBB": "Bravo Muni",
    "KCCC": "Charlie Fld",
    "KDDD": "Delta Rgnl",
    "KEEE": "Echo Co",
}

AIRPORT_DATA: dict[str, dict[str, Any]] = {
    icao: {"latitude": 37.0, "longitude": -122.0} for icao in NAMES
}

VFR = "{} 101251Z 00000KT 10SM CLR 15/05 A2992"
MVFR = "{} 101251Z 00000KT 4SM BR BKN020 15/05 A2992"
IFR = "{} 101251Z 00000KT 2SM BR OVC008 10/10 A2992"

VATSIM = {"pilots": [], "atis": []}

NO_RUNWAYS = "INFO A. WIND CALM. ALTIMETER 2992."
LDG_28R = "LDG RWY 28R."
LDG_28L = "LDG RWY 28L."
LDG_28R_DEP_10L = "LDG RWY 28R. DEP RWY 10L."
LDG_28R_DEP_10R = "LDG RWY 28R. DEP RWY 10R."
LDG_DEP_28R = "LDG/DEPTG RWY 28R."
ILS_28R = "ILS RWY 28R."
RNAV_28R = "RNAV RWY 28R."
DEP_10L = "DEP RWY 10L."


def atis(*texts: str) -> list[dict[str, str]]:
    """The ATIS entries one airport answers with, one per text."""
    return [{"callsign": "KXXX_ATIS", "text_atis": text} for text in texts]


def runways(landing: set[str], departing: set[str]) -> tuple[frozenset, frozenset]:
    return frozenset(landing), frozenset(departing)


def rwy_toast(icao: str, new: str, old: str) -> tuple[str, str, bool]:
    name = NAMES[icao]
    return (
        f"[bold]RWY:[/bold] {icao} ({name}) now [yellow bold]{new}[/yellow bold] [dim](was {old})[/dim]",
        f"[dim]RWY: {icao} ({name}) now {new} (was {old})[/dim]",
        True,
    )


def app_toast(icao: str, runway: str, new: str, old: str) -> tuple[str, str, bool]:
    name = NAMES[icao]
    return (
        f"[bold]APP:[/bold] {icao} ({name}) RWY {runway} now [cyan bold]{new}[/cyan bold] [dim](was {old})[/dim]",
        f"[dim]APP: {icao} ({name}) RWY {runway} now {new} (was {old})[/dim]",
        True,
    )


class Fakes:
    """The module's outside world: answers and records every boundary call."""

    def __init__(self) -> None:
        self.metars: dict[str, str] = {}
        self.metar_error: BaseException | None = None
        self.metar_calls: list[tuple[list[str], Any]] = []
        self.vatsim: dict[str, Any] | None = VATSIM
        self.vatsim_error: BaseException | None = None
        self.vatsim_calls = 0
        self.vatsim_atis: dict[str, list[dict[str, str]]] = {}
        self.vatsim_atis_calls: list[tuple[Any, list[str]]] = []
        self.datis: dict[str, list[dict[str, str]]] = {}
        self.datis_calls: list[list[str]] = []
        self.nearby: dict[tuple[float, float], list[str]] = {}
        self.nearby_calls: list[tuple[Any, ...]] = []

    def get_metar_batch(
        self, icaos: list[str], max_workers: Any = None
    ) -> dict[str, str]:
        self.metar_calls.append((list(icaos), max_workers))
        if self.metar_error is not None:
            raise self.metar_error
        return {icao: self.metars[icao] for icao in icaos if icao in self.metars}

    def download_vatsim_data(self) -> dict[str, Any] | None:
        self.vatsim_calls += 1
        if self.vatsim_error is not None:
            raise self.vatsim_error
        return self.vatsim

    def get_atis_for_airports(
        self, vatsim_data: Any, airports: list[str]
    ) -> dict[str, list[dict[str, str]]]:
        self.vatsim_atis_calls.append((vatsim_data, list(airports)))
        return {
            icao: self.vatsim_atis[icao]
            for icao in airports
            if icao in self.vatsim_atis
        }

    def get_datis_for_airports(
        self, airports: list[str]
    ) -> dict[str, list[dict[str, str]]]:
        self.datis_calls.append(list(airports))
        return {icao: self.datis[icao] for icao in airports if icao in self.datis}

    def find_airports_near_position(
        self, lat: float, lon: float, airports: Any, radius_nm: float, max_results: int
    ) -> list[str]:
        self.nearby_calls.append((lat, lon, airports, radius_nm, max_results))
        return list(self.nearby.get((lat, lon), []))


class Toasts:
    """Stands in for the notification manager and records every toast shown."""

    def __init__(self) -> None:
        self.shown: list[tuple[str, str, bool]] = []

    def show(self, text_bright: str, text_dim: str, flash: bool = False) -> None:
        self.shown.append((text_bright, text_dim, flash))


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch) -> Iterator[Fakes]:
    fake = Fakes()
    monkeypatch.setattr(config, "UNIFIED_AIRPORT_DATA", AIRPORT_DATA)
    monkeypatch.setattr(
        config, "DISAMBIGUATOR", SimpleNamespace(get_full_name=NAMES.__getitem__)
    )
    for name in (
        "get_metar_batch",
        "download_vatsim_data",
        "get_atis_for_airports",
        "get_datis_for_airports",
        "find_airports_near_position",
    ):
        monkeypatch.setattr(flight_board, name, getattr(fake, name))
    yield fake


def board(
    airports: str | list[str], filters: dict[str, str] | None = None
) -> tuple[FlightBoardScreen, Toasts]:
    """A board on one airport (a string) or a grouping (a list), with its toasts recorded."""
    screen = FlightBoardScreen("Board", airports, airport_filters=filters)
    toasts = Toasts()
    screen._notification_manager = toasts
    return screen, toasts


# --- _initialize_change_baseline ---


async def test_baseline_is_not_taken_for_a_single_airport_board(fakes: Fakes) -> None:
    screen, _ = board("KAAA")

    await screen._initialize_change_baseline()

    assert fakes.metar_calls == []
    assert fakes.vatsim_calls == 0
    assert fakes.datis_calls == []
    assert screen._previous_weather == {}
    assert screen._previous_runways == {}
    assert screen._previous_approaches == {}


async def test_baseline_prefers_vatsim_atis_and_asks_datis_for_the_rest(
    fakes: Fakes,
) -> None:
    fakes.vatsim_atis = {"KAAA": atis(LDG_28R_DEP_10L)}
    fakes.datis = {"KBBB": atis(ILS_28R)}
    screen, toasts = board(["KAAA", "KBBB", "KCCC"])

    await screen._initialize_change_baseline()

    assert fakes.metar_calls == [(["KAAA", "KBBB", "KCCC"], None)]
    assert fakes.vatsim_calls == 1
    assert fakes.vatsim_atis_calls == [(VATSIM, ["KAAA", "KBBB", "KCCC"])]
    assert fakes.datis_calls == [["KBBB", "KCCC"]]
    assert screen._previous_runways == {
        "KAAA": runways({"28R"}, {"10L"}),
        "KBBB": runways({"28R"}, set()),
    }
    assert screen._previous_approaches == {
        "KAAA": {},
        "KBBB": {"28R": frozenset({"ILS"})},
    }
    assert toasts.shown == []


async def test_baseline_without_a_vatsim_feed_asks_datis_for_every_airport(
    fakes: Fakes,
) -> None:
    fakes.vatsim = None
    fakes.datis = {"KAAA": atis(LDG_28R)}
    screen, _ = board(["KAAA", "KBBB"])

    await screen._initialize_change_baseline()

    assert fakes.vatsim_atis_calls == []
    assert fakes.datis_calls == [["KAAA", "KBBB"]]
    assert screen._previous_runways == {"KAAA": runways({"28R"}, set())}


async def test_baseline_skips_datis_when_every_airport_has_vatsim_atis(
    fakes: Fakes,
) -> None:
    fakes.vatsim_atis = {"KAAA": atis(LDG_28R), "KBBB": atis(LDG_28L)}
    screen, _ = board(["KAAA", "KBBB"])

    await screen._initialize_change_baseline()

    assert fakes.datis_calls == []
    assert screen._previous_runways == {
        "KAAA": runways({"28R"}, set()),
        "KBBB": runways({"28L"}, set()),
    }


async def test_baseline_weather_holds_only_airports_with_a_metar(fakes: Fakes) -> None:
    fakes.metars = {"KAAA": VFR.format("KAAA"), "KBBB": "", "KCCC": IFR.format("KCCC")}
    screen, _ = board(["KAAA", "KBBB", "KCCC", "KDDD"])

    await screen._initialize_change_baseline()

    assert screen._previous_weather == {"KAAA": "VFR", "KCCC": "IFR"}


async def test_baseline_combines_every_atis_entry_of_an_airport(fakes: Fakes) -> None:
    fakes.vatsim_atis = {
        "KAAA": [
            *atis(DEP_10L, ""),
            {"callsign": "KAAA_ATIS"},
            *atis(ILS_28R, RNAV_28R),
        ]
    }
    screen, _ = board(["KAAA", "KBBB"])

    await screen._initialize_change_baseline()

    assert screen._previous_runways == {"KAAA": runways({"28R"}, {"10L"})}
    assert screen._previous_approaches == {"KAAA": {"28R": frozenset({"ILS", "RNAV"})}}


async def test_baseline_ignores_atis_without_runways(fakes: Fakes) -> None:
    fakes.vatsim_atis = {"KAAA": atis(NO_RUNWAYS), "KBBB": atis("")}
    screen, _ = board(["KAAA", "KBBB"])

    await screen._initialize_change_baseline()

    assert screen._previous_runways == {}
    assert screen._previous_approaches == {}


async def test_baseline_metar_fetch_error_propagates_and_stores_nothing(
    fakes: Fakes,
) -> None:
    fakes.metar_error = RuntimeError("metar service down")
    fakes.vatsim_atis = {"KAAA": atis(LDG_28R)}
    screen, _ = board(["KAAA", "KBBB"])

    with pytest.raises(RuntimeError, match="metar service down"):
        await screen._initialize_change_baseline()

    assert screen._previous_weather == {}
    assert screen._previous_runways == {}
    assert screen._previous_approaches == {}


async def test_baseline_vatsim_feed_error_propagates_and_stores_nothing(
    fakes: Fakes,
) -> None:
    fakes.vatsim_error = RuntimeError("vatsim feed down")
    fakes.metars = {"KAAA": VFR.format("KAAA")}
    fakes.datis = {"KAAA": atis(LDG_28R)}
    screen, _ = board(["KAAA", "KBBB"])

    with pytest.raises(RuntimeError, match="vatsim feed down"):
        await screen._initialize_change_baseline()

    assert fakes.vatsim_atis_calls == []
    assert fakes.datis_calls == []
    assert screen._previous_weather == {}
    assert screen._previous_runways == {}
    assert screen._previous_approaches == {}


# --- _check_for_changes ---


async def test_check_does_nothing_for_a_single_airport_board(fakes: Fakes) -> None:
    screen, toasts = board("KAAA")
    screen._previous_weather = {"KAAA": "VFR"}

    await screen._check_for_changes()

    assert fakes.metar_calls == []
    assert fakes.vatsim_calls == 0
    assert toasts.shown == []
    assert screen._previous_weather == {"KAAA": "VFR"}


async def test_check_weather_toasts_each_category_change_flashing_with_atis(
    fakes: Fakes,
) -> None:
    fakes.metars = {
        "KAAA": IFR.format("KAAA"),
        "KBBB": MVFR.format("KBBB"),
        "KCCC": VFR.format("KCCC"),
        "KDDD": MVFR.format("KDDD"),
    }
    fakes.vatsim_atis = {"KAAA": atis(NO_RUNWAYS)}
    screen, toasts = board(["KAAA", "KBBB", "KCCC", "KDDD", "KEEE"])
    screen._previous_weather = {
        "KAAA": "VFR",
        "KBBB": "VFR",
        "KDDD": "MVFR",
        "KEEE": "IFR",
    }

    await screen._check_for_changes()

    assert toasts.shown == [
        (
            "[bold]WX:[/bold] KAAA (Alpha Intl) now [red bold]IFR[/red bold] "
            "[dim](was [#00ff00]VFR[/#00ff00])[/dim]",
            "[dim]WX: KAAA (Alpha Intl) now IFR (was VFR)[/dim]",
            True,
        ),
        (
            "[bold]WX:[/bold] KBBB (Bravo Muni) now [#5599ff bold]MVFR[/#5599ff bold] "
            "[dim](was [#00ff00]VFR[/#00ff00])[/dim]",
            "[dim]WX: KBBB (Bravo Muni) now MVFR (was VFR)[/dim]",
            False,
        ),
    ]
    assert screen._previous_weather == {
        "KAAA": "IFR",
        "KBBB": "MVFR",
        "KCCC": "VFR",
        "KDDD": "MVFR",
        "KEEE": "IFR",
    }


async def test_check_shows_weather_toasts_before_runway_toasts(fakes: Fakes) -> None:
    fakes.metars = {"KBBB": IFR.format("KBBB")}
    fakes.vatsim_atis = {"KAAA": atis(LDG_28L)}
    screen, toasts = board(["KAAA", "KBBB"])
    screen._previous_weather = {"KBBB": "VFR"}
    screen._previous_runways = {"KAAA": runways({"28R"}, set())}

    await screen._check_for_changes()

    assert [toast[1] for toast in toasts.shown] == [
        "[dim]WX: KBBB (Bravo Muni) now IFR (was VFR)[/dim]",
        "[dim]RWY: KAAA (Alpha Intl) now L:28L (was L:28R)[/dim]",
    ]


async def test_check_toasts_a_landing_runway_change(fakes: Fakes) -> None:
    fakes.vatsim_atis = {"KAAA": atis(LDG_28L)}
    screen, toasts = board(["KAAA", "KBBB"])
    screen._previous_runways = {"KAAA": runways({"28R"}, set())}

    await screen._check_for_changes()

    assert toasts.shown == [rwy_toast("KAAA", "L:28L", "L:28R")]
    assert screen._previous_runways == {"KAAA": runways({"28L"}, set())}
    assert screen._previous_approaches == {}


async def test_check_toasts_a_departing_runway_change(fakes: Fakes) -> None:
    fakes.vatsim_atis = {"KAAA": atis(LDG_28R_DEP_10R)}
    screen, toasts = board(["KAAA", "KBBB"])
    screen._previous_runways = {"KAAA": runways({"28R"}, {"10L"})}

    await screen._check_for_changes()

    assert toasts.shown == [rwy_toast("KAAA", "L:28R D:10R", "L:28R D:10L")]
    assert screen._previous_runways == {"KAAA": runways({"28R"}, {"10R"})}


@pytest.mark.parametrize(
    ("old", "text", "new"),
    [
        (runways({"28R"}, set()), LDG_DEP_28R, runways({"28R"}, {"28R"})),
        (runways({"28R"}, {"28R"}), LDG_28R, runways({"28R"}, set())),
    ],
    ids=["landing-only-to-both", "both-to-landing-only"],
)
async def test_check_treats_landing_only_runways_as_departing_too(
    fakes: Fakes,
    old: tuple[frozenset, frozenset],
    text: str,
    new: tuple[frozenset, frozenset],
) -> None:
    fakes.vatsim_atis = {"KAAA": atis(text)}
    screen, toasts = board(["KAAA", "KBBB"])
    screen._previous_runways = {"KAAA": old}

    await screen._check_for_changes()

    assert toasts.shown == []
    assert screen._previous_runways == {"KAAA": new}


async def test_check_airport_gaining_atis_sets_its_baseline_without_a_toast(
    fakes: Fakes,
) -> None:
    fakes.datis = {"KAAA": atis(ILS_28R)}
    screen, toasts = board(["KAAA", "KBBB"])

    await screen._check_for_changes()

    assert toasts.shown == []
    assert screen._previous_runways == {"KAAA": runways({"28R"}, set())}
    assert screen._previous_approaches == {"KAAA": {"28R": frozenset({"ILS"})}}


async def test_check_airport_losing_atis_keeps_its_baseline(fakes: Fakes) -> None:
    screen, toasts = board(["KAAA", "KBBB"])
    screen._previous_runways = {"KAAA": runways({"28R"}, {"10L"})}
    screen._previous_approaches = {"KAAA": {"28R": frozenset({"ILS"})}}

    await screen._check_for_changes()

    assert toasts.shown == []
    assert fakes.datis_calls == [["KAAA", "KBBB"]]
    assert screen._previous_runways == {"KAAA": runways({"28R"}, {"10L"})}
    assert screen._previous_approaches == {"KAAA": {"28R": frozenset({"ILS"})}}


async def test_check_parse_without_runways_neither_toasts_nor_overwrites(
    fakes: Fakes,
) -> None:
    fakes.vatsim_atis = {"KAAA": atis(NO_RUNWAYS, "")}
    screen, toasts = board(["KAAA", "KBBB"])
    screen._previous_runways = {"KAAA": runways({"28R"}, {"10L"})}
    screen._previous_approaches = {"KAAA": {"28R": frozenset({"ILS"})}}

    await screen._check_for_changes()

    assert toasts.shown == []
    assert screen._previous_runways == {"KAAA": runways({"28R"}, {"10L"})}
    assert screen._previous_approaches == {"KAAA": {"28R": frozenset({"ILS"})}}


async def test_check_baseline_with_empty_runways_is_replaced_without_a_toast(
    fakes: Fakes,
) -> None:
    fakes.vatsim_atis = {"KAAA": atis(LDG_28L)}
    screen, toasts = board(["KAAA", "KBBB"])
    screen._previous_runways = {"KAAA": runways(set(), set())}

    await screen._check_for_changes()

    assert toasts.shown == []
    assert screen._previous_runways == {"KAAA": runways({"28L"}, set())}


async def test_check_toasts_an_approach_change_on_an_unchanged_runway(
    fakes: Fakes,
) -> None:
    fakes.vatsim_atis = {"KAAA": atis(RNAV_28R)}
    screen, toasts = board(["KAAA", "KBBB"])
    screen._previous_runways = {"KAAA": runways({"28R"}, set())}
    screen._previous_approaches = {"KAAA": {"28R": frozenset({"ILS"})}}

    await screen._check_for_changes()

    assert toasts.shown == [app_toast("KAAA", "28R", "RNAV", "ILS")]
    assert screen._previous_approaches == {"KAAA": {"28R": frozenset({"RNAV"})}}


async def test_check_approaches_compare_only_runways_present_on_both_sides(
    fakes: Fakes,
) -> None:
    fakes.vatsim_atis = {"KAAA": atis("RNAV RWY 28R. RNAV RWY 10L.")}
    screen, toasts = board(["KAAA", "KBBB"])
    screen._previous_runways = {"KAAA": runways({"28R", "28L"}, set())}
    screen._previous_approaches = {
        "KAAA": {"28R": frozenset({"ILS"}), "28L": frozenset({"ILS"})}
    }

    await screen._check_for_changes()

    assert toasts.shown == [
        rwy_toast("KAAA", "L:10L,28R", "L:28L,28R"),
        app_toast("KAAA", "28R", "RNAV", "ILS"),
    ]
    assert screen._previous_runways == {"KAAA": runways({"28R", "10L"}, set())}
    assert screen._previous_approaches == {
        "KAAA": {"28R": frozenset({"RNAV"}), "10L": frozenset({"RNAV"})}
    }


async def test_check_approaches_without_a_baseline_set_one_without_a_toast(
    fakes: Fakes,
) -> None:
    fakes.vatsim_atis = {"KAAA": atis(ILS_28R)}
    screen, toasts = board(["KAAA", "KBBB"])
    screen._previous_runways = {"KAAA": runways({"28R"}, set())}
    screen._previous_approaches = {"KAAA": {}}

    await screen._check_for_changes()

    assert toasts.shown == []
    assert screen._previous_approaches == {"KAAA": {"28R": frozenset({"ILS"})}}


async def test_check_toasts_only_runways_whose_approaches_changed(
    fakes: Fakes,
) -> None:
    fakes.vatsim_atis = {"KAAA": atis("ILS RWY 28R. RNAV RWY 28L.")}
    screen, toasts = board(["KAAA", "KBBB"])
    screen._previous_runways = {"KAAA": runways({"28R", "28L"}, set())}
    screen._previous_approaches = {
        "KAAA": {"28R": frozenset({"ILS"}), "28L": frozenset({"ILS"})}
    }

    await screen._check_for_changes()

    assert toasts.shown == [app_toast("KAAA", "28L", "RNAV", "ILS")]
    assert screen._previous_approaches == {
        "KAAA": {"28R": frozenset({"ILS"}), "28L": frozenset({"RNAV"})}
    }


async def test_check_parse_without_approaches_keeps_the_approach_baseline(
    fakes: Fakes,
) -> None:
    fakes.vatsim_atis = {"KAAA": atis(LDG_28R)}
    screen, toasts = board(["KAAA", "KBBB"])
    screen._previous_runways = {"KAAA": runways({"28R"}, set())}
    screen._previous_approaches = {"KAAA": {"28R": frozenset({"ILS"})}}

    await screen._check_for_changes()

    assert toasts.shown == []
    assert screen._previous_approaches == {"KAAA": {"28R": frozenset({"ILS"})}}


async def test_check_combines_every_atis_entry_of_an_airport(fakes: Fakes) -> None:
    fakes.vatsim_atis = {
        "KAAA": [*atis(DEP_10L, ILS_28R), {"callsign": "KAAA_ATIS"}, *atis(RNAV_28R)]
    }
    screen, toasts = board(["KAAA", "KBBB"])
    screen._previous_runways = {"KAAA": runways({"28R"}, {"10L"})}
    screen._previous_approaches = {"KAAA": {"28R": frozenset({"ILS"})}}

    await screen._check_for_changes()

    assert toasts.shown == [app_toast("KAAA", "28R", "ILS/RNAV", "ILS")]
    assert screen._previous_approaches == {"KAAA": {"28R": frozenset({"ILS", "RNAV"})}}


async def test_check_without_a_vatsim_feed_asks_datis_for_every_airport(
    fakes: Fakes,
) -> None:
    fakes.vatsim = None
    fakes.datis = {"KBBB": atis(LDG_28L)}
    screen, toasts = board(["KAAA", "KBBB"])
    screen._previous_runways = {"KBBB": runways({"28R"}, set())}

    await screen._check_for_changes()

    assert fakes.vatsim_atis_calls == []
    assert fakes.datis_calls == [["KAAA", "KBBB"]]
    assert toasts.shown == [rwy_toast("KBBB", "L:28L", "L:28R")]


async def test_check_skips_datis_when_every_airport_has_vatsim_atis(
    fakes: Fakes,
) -> None:
    fakes.vatsim_atis = {"KAAA": atis(LDG_28R), "KBBB": atis(LDG_28R)}
    screen, _ = board(["KAAA", "KBBB"])

    await screen._check_for_changes()

    assert fakes.vatsim_atis_calls == [(VATSIM, ["KAAA", "KBBB"])]
    assert fakes.datis_calls == []


async def test_check_metar_fetch_error_propagates_and_changes_nothing(
    fakes: Fakes,
) -> None:
    fakes.metar_error = RuntimeError("metar service down")
    fakes.vatsim_atis = {"KAAA": atis(LDG_28L)}
    screen, toasts = board(["KAAA", "KBBB"])
    screen._previous_runways = {"KAAA": runways({"28R"}, set())}

    with pytest.raises(RuntimeError, match="metar service down"):
        await screen._check_for_changes()

    assert toasts.shown == []
    assert screen._previous_runways == {"KAAA": runways({"28R"}, set())}


async def test_check_vatsim_feed_error_propagates_and_changes_nothing(
    fakes: Fakes,
) -> None:
    fakes.vatsim_error = RuntimeError("vatsim feed down")
    fakes.metars = {"KAAA": IFR.format("KAAA")}
    fakes.datis = {"KAAA": atis(LDG_28L)}
    screen, toasts = board(["KAAA", "KBBB"])
    screen._previous_weather = {"KAAA": "VFR"}
    screen._previous_runways = {"KAAA": runways({"28R"}, set())}
    screen._previous_approaches = {"KAAA": {"28R": frozenset({"ILS"})}}

    with pytest.raises(RuntimeError, match="vatsim feed down"):
        await screen._check_for_changes()

    assert fakes.datis_calls == []
    assert toasts.shown == []
    assert screen._previous_weather == {"KAAA": "VFR"}
    assert screen._previous_runways == {"KAAA": runways({"28R"}, set())}
    assert screen._previous_approaches == {"KAAA": {"28R": frozenset({"ILS"})}}


# --- _precache_flight_altimeters ---


def airport(icao: str) -> AirportInfo:
    return AirportInfo(pretty_name=NAMES.get(icao, icao), icao_code=icao)


def departure(callsign: str, to: str, at: str | None) -> DepartureInfo:
    return DepartureInfo(
        callsign=callsign,
        destination=airport(to),
        departure=airport(at) if at else None,
    )


def arrival(callsign: str, origin: str, at: str | None) -> ArrivalInfo:
    return ArrivalInfo(
        callsign=callsign,
        origin=airport(origin),
        eta_display="10m",
        eta_local_time="12:10",
        arrival=airport(at) if at else None,
    )


def precache_board(
    vatsim_data: dict[str, Any] | None,
    departures: list[DepartureInfo],
    arrivals: list[ArrivalInfo],
) -> FlightBoardScreen:
    screen, _ = board(["KAAA", "KBBB"])
    screen.vatsim_data = vatsim_data
    screen.departures_data = departures
    screen.arrivals_data = arrivals
    return screen


def cached_airports(fakes: Fakes) -> list[tuple[list[str], Any]]:
    return [(sorted(icaos), workers) for icaos, workers in fakes.metar_calls]


def test_precache_needs_vatsim_data(fakes: Fakes) -> None:
    screen = precache_board(None, [departure("DAL1", "KBBB", "KAAA")], [])

    screen._precache_flight_altimeters()

    assert fakes.metar_calls == []


def test_precache_needs_airport_data(
    fakes: Fakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "UNIFIED_AIRPORT_DATA", {})
    screen = precache_board(VATSIM, [departure("DAL1", "KBBB", "KAAA")], [])

    screen._precache_flight_altimeters()

    assert fakes.metar_calls == []


def test_precache_fetches_nothing_without_flights(fakes: Fakes) -> None:
    screen = precache_board(VATSIM, [], [])

    screen._precache_flight_altimeters()

    assert fakes.metar_calls == []
    assert fakes.nearby_calls == []


def test_precache_departures_warm_their_departure_and_destination(fakes: Fakes) -> None:
    screen = precache_board(
        VATSIM,
        [departure("DAL1", "KBBB", "KAAA"), departure("DAL2", "KCCC", None)],
        [],
    )

    screen._precache_flight_altimeters()

    assert cached_airports(fakes) == [(["KAAA", "KBBB", "KCCC"], 10)]
    assert fakes.nearby_calls == []


def test_precache_arrivals_warm_airports_near_their_position(fakes: Fakes) -> None:
    pilots = [
        {"callsign": "UAL1", "latitude": 37.5, "longitude": -122.5},
        {"callsign": "UAL2", "latitude": None, "longitude": -122.0},
        {"callsign": "UAL3", "latitude": 38.0, "longitude": None},
    ]
    fakes.nearby = {(37.5, -122.5): ["KDDD", "KEEE"]}
    screen = precache_board(
        {"pilots": pilots},
        [],
        [
            arrival("UAL1", "KAAA", "KBBB"),
            arrival("UAL2", "KCCC", None),
            arrival("UAL3", "KCCC", None),
            arrival("UAL9", "KAAA", None),
        ],
    )

    screen._precache_flight_altimeters()

    assert fakes.nearby_calls == [(37.5, -122.5, AIRPORT_DATA, 75, 3)]
    assert cached_airports(fakes) == [(["KAAA", "KBBB", "KCCC", "KDDD", "KEEE"], 10)]


# --- action_show_flight_info ---


class FakeTable:
    """A board table as the action reads it: focus, cursor row and row lookup.

    A cursor row of -1 is a characterization shape for "no row selected" that the
    action guards against; a real DataTable reports 0 even when it is empty.
    """

    def __init__(
        self,
        rows: list[list[Any]],
        cursor_row: int = -1,
        has_focus: bool = False,
        missing: bool = False,
    ) -> None:
        self.rows = rows
        self.cursor_row = cursor_row
        self.has_focus = has_focus
        self.missing = missing

    def get_row_at(self, row_index: int) -> list[Any]:
        if self.missing:
            raise RowDoesNotExist(f"Row index {row_index!r} is not valid.")
        return self.rows[row_index]


class FakeApp:
    def __init__(self) -> None:
        self.pushed: list[Any] = []

    def push_screen(self, screen: Any) -> None:
        self.pushed.append(screen)


DAL1 = {"callsign": "DAL1", "latitude": 37.0, "longitude": -122.0}
UAL2 = {"callsign": " UAL2 ", "latitude": 37.5, "longitude": -122.0}
PILOTS = {"pilots": [DAL1, UAL2]}


@pytest.fixture
def fake_app(monkeypatch: pytest.MonkeyPatch) -> FakeApp:
    recorder = FakeApp()
    monkeypatch.setattr(FlightBoardScreen, "app", property(lambda _self: recorder))
    return recorder


def info_board(
    departures: FakeTable,
    arrivals: FakeTable,
    vatsim_data: dict[str, Any] | None = PILOTS,
    filters: dict[str, str] | None = None,
) -> FlightBoardScreen:
    screen, _ = board(["KAAA", "KBBB"], filters)
    screen.vatsim_data = vatsim_data
    tables = {"#departures-table": departures, "#arrivals-table": arrivals}
    screen.query_one = lambda selector, _type=None: tables[selector]
    return screen


def pushed_flights(app: FakeApp) -> list[dict[str, Any]]:
    assert all(isinstance(screen, FlightInfoScreen) for screen in app.pushed)
    return [screen.flight_data for screen in app.pushed]


def test_info_needs_vatsim_data(fake_app: FakeApp) -> None:
    screen = info_board(
        FakeTable([["DAL1"]], cursor_row=0, has_focus=True),
        FakeTable([]),
        vatsim_data=None,
    )

    screen.action_show_flight_info()

    assert fake_app.pushed == []


@pytest.mark.parametrize(
    ("departures", "arrivals", "expected"),
    [
        (
            FakeTable([["DAL1"]], cursor_row=0, has_focus=True),
            FakeTable([["UAL2"]], cursor_row=0, has_focus=True),
            DAL1,
        ),
        (
            FakeTable([["DAL1"]], cursor_row=0),
            FakeTable([["UAL2"]], cursor_row=0, has_focus=True),
            UAL2,
        ),
        (
            FakeTable([["DAL1"]], cursor_row=0),
            FakeTable([["UAL2"]], cursor_row=0),
            DAL1,
        ),
        (FakeTable([["DAL1"]]), FakeTable([["UAL2"]], cursor_row=0), UAL2),
    ],
    ids=[
        "departures-focused",
        "arrivals-focused",
        "departures-cursor",
        "arrivals-cursor",
    ],
)
def test_info_opens_the_flight_of_the_chosen_table(
    fake_app: FakeApp,
    departures: FakeTable,
    arrivals: FakeTable,
    expected: dict[str, Any],
) -> None:
    screen = info_board(departures, arrivals)

    screen.action_show_flight_info()

    assert pushed_flights(fake_app) == [expected]


def test_info_ignores_a_focused_table_the_direction_mode_hides(
    fake_app: FakeApp,
) -> None:
    screen = info_board(
        FakeTable([["DAL1"]], cursor_row=0),
        FakeTable([["UAL2"]], cursor_row=0, has_focus=True),
        filters={"KAAA": "dep", "KBBB": "dep"},
    )

    screen.action_show_flight_info()

    assert pushed_flights(fake_app) == [DAL1]


def test_info_ignores_a_cursor_on_a_hidden_departures_table(fake_app: FakeApp) -> None:
    screen = info_board(
        FakeTable([["DAL1"]], cursor_row=0, has_focus=True),
        FakeTable([["UAL2"]]),
        filters={"KAAA": "arr", "KBBB": "arr"},
    )

    screen.action_show_flight_info()

    assert fake_app.pushed == []


def test_info_ignores_a_cursor_on_a_hidden_arrivals_table(fake_app: FakeApp) -> None:
    screen = info_board(
        FakeTable([["DAL1"]]),
        FakeTable([["UAL2"]], cursor_row=0),
        filters={"KAAA": "dep", "KBBB": "dep"},
    )

    screen.action_show_flight_info()

    assert fake_app.pushed == []


@pytest.mark.parametrize(
    ("departures", "arrivals"),
    [
        (FakeTable([["DAL1"]]), FakeTable([["UAL2"]])),
        (FakeTable([["DAL1"]], has_focus=True), FakeTable([["UAL2"]], cursor_row=0)),
        (
            FakeTable([["DAL1"]], cursor_row=0, has_focus=True, missing=True),
            FakeTable([]),
        ),
        (FakeTable([[]], cursor_row=0, has_focus=True), FakeTable([])),
        (FakeTable([["AAL9"]], cursor_row=0, has_focus=True), FakeTable([])),
    ],
    ids=[
        "no-row",
        "focused-without-cursor",
        "row-gone",
        "empty-row",
        "flight-not-in-feed",
    ],
)
def test_info_opens_nothing_without_a_flight(
    fake_app: FakeApp, departures: FakeTable, arrivals: FakeTable
) -> None:
    screen = info_board(departures, arrivals)

    screen.action_show_flight_info()

    assert fake_app.pushed == []


def test_info_matches_callsigns_with_surrounding_spaces(fake_app: FakeApp) -> None:
    screen = info_board(
        FakeTable([]), FakeTable([[" UAL2  ", "KAAA"]], cursor_row=0, has_focus=True)
    )

    screen.action_show_flight_info()

    assert pushed_flights(fake_app) == [UAL2]


def test_info_opens_the_first_pilot_sharing_a_callsign(fake_app: FakeApp) -> None:
    first = {"callsign": "DAL1 ", "latitude": 37.0, "longitude": -122.0}
    second = {"callsign": "DAL1", "latitude": 38.0, "longitude": -121.0}
    screen = info_board(
        FakeTable([["DAL1"]], cursor_row=0, has_focus=True),
        FakeTable([]),
        vatsim_data={"pilots": [UAL2, first, second]},
    )

    screen.action_show_flight_info()

    assert pushed_flights(fake_app) == [first]
    assert fake_app.pushed[0].flight_data is first
