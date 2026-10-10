"""The weather-briefing picker callback routes each pick to its own briefing.

The picker hands back one of three shapes: a flight record, a single airport,
or a grouping name with its resolved airports. This pins each one's route; no
screen is mounted, only the pushes are recorded.
"""

import types
from typing import Any

from ui.app import VATSIMControlApp
from ui.modals.flight_briefing import FlightWeatherBriefingScreen
from ui.modals.weather_briefing import WeatherBriefingScreen


class BriefingHost:
    """Stands in for VATSIMControlApp: records the screen pushes and toasts."""

    def __init__(self) -> None:
        self.screens: list[Any] = []
        self.toasts: list[tuple[str, str]] = []

    def push_screen(self, screen: Any) -> None:
        self.screens.append(screen)

    def notify(
        self,
        message: str,
        *,
        title: str = "",
        severity: str = "information",
        timeout: float | None = None,
        markup: bool = True,
    ) -> None:
        self.toasts.append((severity, message))


def _host() -> BriefingHost:
    """A host with the app's real briefing helpers bound to it."""
    host = BriefingHost()
    for name in (
        "_open_weather_briefing_callback",
        "_open_airport_weather_briefing",
        "_open_flight_weather_briefing",
        "_parse_altitude",
    ):
        setattr(host, name, types.MethodType(getattr(VATSIMControlApp, name), host))
    return host


FLIGHT: dict[str, Any] = {
    "callsign": "UAL1",
    "groundspeed": 450,
    "flight_plan": {
        "departure": "KSFO",
        "arrival": "KLAX",
        "alternate": "KLAX",
        "route": "DCT",
        "altitude": "FL350",
    },
}


def test_flight_pick_opens_the_flight_briefing() -> None:
    host = _host()
    host._open_weather_briefing_callback(("flight", FLIGHT))
    (screen,) = host.screens
    assert isinstance(screen, FlightWeatherBriefingScreen)
    assert screen.callsign == "UAL1"
    assert (screen.departure, screen.arrival) == ("KSFO", "KLAX")


def test_single_airport_pick_opens_the_airport_briefing() -> None:
    host = _host()
    host._open_weather_briefing_callback(("KSFO", ["KSFO"]))
    (screen,) = host.screens
    assert isinstance(screen, WeatherBriefingScreen)
    assert "KSFO" in screen.airports
    assert screen.primary_airports == ["KSFO"]


def test_grouping_pick_opens_the_grouping_briefing() -> None:
    host = _host()
    host._open_weather_briefing_callback(("Bay Area", ["KSFO", "KOAK", "KSJC"]))
    (screen,) = host.screens
    assert isinstance(screen, WeatherBriefingScreen)
    assert screen.grouping_name == "Bay Area"
    assert screen.airports == ["KSFO", "KOAK", "KSJC"]
