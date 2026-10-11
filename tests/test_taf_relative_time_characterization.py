"""Characterization of the TAF relative-time formatter.

Pins the output of ``format_taf_relative_time`` over FM (``DDHHMM``) and
TEMPO/BECMG (``DDHH/DDHH``) codes: past, present and future offsets across the
minute, hour and day boundaries, month and year rollovers in both directions, and
the malformed inputs that yield an empty string or raise.

The month-turn rows pin the intended reading of a day the current month lacks,
not the value the formatter produced before that reading was fixed.

The module's ``datetime`` symbol is replaced with a frozen clock, so every
expectation is exact.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from backend.briefing import taf_parsing

NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)


class _Clock:
    """Stands in for the module's ``datetime`` with one fixed ``now``."""

    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self, tz: object = None) -> datetime:
        return self._moment


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch):
    """Freeze the module clock at NOW; call the returned function to move it."""

    def freeze(moment: datetime) -> None:
        monkeypatch.setattr(taf_parsing, "datetime", _Clock(moment))

    freeze(NOW)
    return freeze


@pytest.mark.parametrize(
    ("time_str", "expected"),
    [
        ("151800", "(in  6h)"),
        ("151659", "(in  4h)"),
        ("152359", "(in 11h)"),
        ("151245", "(in 45m)"),
        ("151200", "( 0m ago)"),
        ("151159", "( 1m ago)"),
        ("151100", "( 1h ago)"),
        ("161200", "(in  1d)"),
        ("240000", "(in  8d)"),
        ("010000", "(14d ago)"),
        ("302359", "(15d ago)"),
        ("1518/1521", "(in  6h)"),
        ("1515/1518", "(in  3h)"),
        ("1512/1515", "( 0m ago)"),
        ("1511/1514", "( 1h ago)"),
    ],
)
def test_relative_time_at_frozen_now(time_str: str, expected: str) -> None:
    assert taf_parsing.format_taf_relative_time(time_str) == expected


@pytest.mark.parametrize(
    ("moment", "time_str", "expected"),
    [
        (datetime(2026, 6, 3, 12, tzinfo=UTC), "300000", "( 4d ago)"),
        (datetime(2026, 8, 15, 12, tzinfo=UTC), "310000", "(15d ago)"),
        (datetime(2026, 6, 28, 12, tzinfo=UTC), "010000", "(in  2d)"),
        (datetime(2026, 12, 28, 12, tzinfo=UTC), "010000", "(in  3d)"),
        (datetime(2026, 1, 3, 12, tzinfo=UTC), "300000", "( 4d ago)"),
        (datetime(2026, 6, 28, 12, tzinfo=UTC), "0100/0103", "(in  2d)"),
        (datetime(2026, 10, 15, 12, tzinfo=UTC), "310000", "(in 15d)"),
        (datetime(2026, 4, 1, 0, 30, tzinfo=UTC), "312300", "( 1h ago)"),
        (datetime(2026, 4, 1, 0, 30, tzinfo=UTC), "3118/0124", "( 6h ago)"),
        (datetime(2026, 3, 1, 6, tzinfo=UTC), "290000", "(in 27d)"),
    ],
)
def test_month_and_year_rollover(
    frozen_clock, moment: datetime, time_str: str, expected: str
) -> None:
    frozen_clock(moment)
    assert taf_parsing.format_taf_relative_time(time_str) == expected


@pytest.mark.parametrize(
    "time_str",
    [
        "",
        "1518",
        "15180",
        "1518000",
        "ABCDEF",
        "/1518",
        "15/18",
        "abcd/1518",
        "151500/1518",
        "000000",
        "990000",
        "156000",
        "152400",
        "1524/1603",
        "0000/0100",
    ],
)
def test_unparsable_time_strings_return_empty(time_str: str) -> None:
    assert taf_parsing.format_taf_relative_time(time_str) == ""


@pytest.mark.parametrize("bad", [None, 123456])
def test_unsized_inputs_raise_type_error(bad: Any) -> None:
    with pytest.raises(TypeError):
        taf_parsing.format_taf_relative_time(bad)


def test_attribute_error_from_non_string_sequence_returns_empty() -> None:
    bad: Any = ["a"] * 6
    assert taf_parsing.format_taf_relative_time(bad) == ""
