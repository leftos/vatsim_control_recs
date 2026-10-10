"""VATSIMControlApp's tracked list: track_airports, and the manager's close."""

import pytest

import backend.data
from ui.app import VATSIMControlApp
from ui.modals.tracked_airports import TrackedAirportsModal


def _app(
    monkeypatch: pytest.MonkeyPatch, allowlist: list[str]
) -> tuple[VATSIMControlApp, list[str]]:
    """An unmounted app whose refresh tail records its calls instead of running."""
    calls: list[str] = []
    monkeypatch.setattr(
        backend.data, "clear_weather_caches", lambda: calls.append("weather")
    )
    app = VATSIMControlApp(airport_allowlist=allowlist)
    monkeypatch.setattr(app, "invalidate_goto_cache", lambda: calls.append("goto"))
    monkeypatch.setattr(app, "action_refresh", lambda: calls.append("refresh"))
    return app, calls


async def test_track_airports_extends_sorted_and_refreshes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, calls = _app(monkeypatch, ["KSFO", "KOAK"])

    assert app.track_airports(["KSMF", "KOAK", "KAPC"])

    assert app.airport_allowlist == ["KAPC", "KOAK", "KSFO", "KSMF"]
    assert sorted(calls) == ["goto", "refresh", "weather"]


@pytest.mark.parametrize(
    ("allowlist", "icaos"),
    [
        pytest.param([], ["KSMF"], id="tracking-everything"),
        pytest.param(["KSFO"], [], id="no-airports"),
        pytest.param(["KSFO"], ["KSFO"], id="already-tracked"),
    ],
)
async def test_track_airports_does_nothing_without_a_new_airport_to_list(
    monkeypatch: pytest.MonkeyPatch, allowlist: list[str], icaos: list[str]
) -> None:
    app, calls = _app(monkeypatch, allowlist)

    assert not app.track_airports(icaos)

    assert app.airport_allowlist == allowlist
    assert calls == []


async def test_track_from_go_to_survives_closing_the_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, calls = _app(monkeypatch, ["KSFO", "KOAK"])
    manager = TrackedAirportsModal(app.airport_allowlist)
    # A quick-add in the manager, then "Add and save" in Go To opened over it
    manager.airport_allowlist.append("KSJC")
    app.track_airports(["KSAC"])
    calls.clear()

    app.handle_tracking_result(manager.tracking_change())

    assert app.airport_allowlist == ["KOAK", "KSAC", "KSFO", "KSJC"]
    assert sorted(calls) == ["goto", "refresh", "weather"]


async def test_closing_the_manager_unchanged_does_not_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, calls = _app(monkeypatch, ["KSFO", "KOAK"])
    manager = TrackedAirportsModal(app.airport_allowlist)

    app.handle_tracking_result(manager.tracking_change())

    assert app.airport_allowlist == ["KSFO", "KOAK"]
    assert calls == []
