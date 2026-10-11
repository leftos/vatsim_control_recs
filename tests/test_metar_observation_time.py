"""_parse_metar_observation_time: a METAR's DDHHMMZ group read as an instant in the past.

The clock is frozen by patching ``weather_briefing.datetime``; the local part is the
host's local time of the instant the METAR names, as the function computes it.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo

import pytest

from ui.modals import weather_briefing
from ui.modals.weather_briefing import _parse_metar_observation_time


def freeze(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return now

    monkeypatch.setattr(weather_briefing, "datetime", FakeDatetime)


def local(instant: datetime) -> str:
    return instant.astimezone().strftime("%H:%M LT")


def metar(time_group: str) -> str:
    return f"KSFO {time_group} 28012KT 10SM CLR 15/10 A3001"


@pytest.mark.parametrize(
    ("now", "time_group", "observed"),
    [
        pytest.param(
            datetime(2026, 6, 15, 12, 0, tzinfo=UTC),
            "151156Z",
            datetime(2026, 6, 15, 11, 56, tzinfo=UTC),
            id="same-day",
        ),
        pytest.param(
            datetime(2026, 6, 15, 12, 0, tzinfo=UTC),
            "151400Z",
            datetime(2026, 6, 15, 14, 0, tzinfo=UTC),
            id="two-hours-ahead-is-this-month",
        ),
        pytest.param(
            datetime(2026, 6, 15, 12, 0, tzinfo=UTC),
            "151500Z",
            datetime(2026, 5, 15, 15, 0, tzinfo=UTC),
            id="three-hours-ahead-is-last-month",
        ),
        pytest.param(
            datetime(2026, 1, 1, 0, 30, tzinfo=UTC),
            "311156Z",
            datetime(2025, 12, 31, 11, 56, tzinfo=UTC),
            id="january-reads-last-december",
        ),
        pytest.param(
            datetime(2026, 11, 1, 0, 30, tzinfo=UTC),
            "311156Z",
            datetime(2026, 10, 31, 11, 56, tzinfo=UTC),
            id="day-missing-from-this-month-is-last-month",
        ),
    ],
)
def test_observation_time(
    monkeypatch: pytest.MonkeyPatch, now: datetime, time_group: str, observed: datetime
) -> None:
    freeze(monkeypatch, now)

    zulu = time_group[2:6] + "Z"
    assert _parse_metar_observation_time(metar(time_group)) == (zulu, local(observed))


def test_day_missing_from_this_and_last_month_has_no_local_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freeze(monkeypatch, datetime(2026, 3, 1, 6, 0, tzinfo=UTC))

    assert _parse_metar_observation_time(metar("300000Z")) == ("0000Z", None)


@pytest.mark.parametrize("text", ["", "KSFO 28012KT 10SM CLR 15/10 A3001"])
def test_no_time_group_gives_none(text: str) -> None:
    assert _parse_metar_observation_time(text) is None
