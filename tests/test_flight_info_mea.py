"""Flight info's MEA lines: each segment against the level flown on it, and the airways not checked."""

from __future__ import annotations

from backend.data.navaids import MeaCheck, MeaViolation, SkippedAirway
from ui.modals.flight_info import format_mea_lines


def _segment(
    airway: str, start: str, end: str, mea: int, level_ft: int | None
) -> MeaViolation:
    return MeaViolation(
        airway=airway, segment_start=start, segment_end=end, mea=mea, level_ft=level_ft
    )


def test_flight_info_shows_mea_not_checked_line() -> None:
    """A skipped airway gets one dim line naming it and the fix not on it, after the MEA warning."""
    skipped_only = MeaCheck(
        max_mea=None,
        violations=[],
        skipped=[SkippedAirway(airway="V12", missing_fix="ONM")],
    )
    with_warning = MeaCheck(
        max_mea=12000,
        violations=[_segment("V12", "ABQ", "OTO", 12000, None)],
        skipped=[SkippedAirway(airway="V12", missing_fix="ONM")],
    )

    assert format_mea_lines(skipped_only, "30000") == [
        "[dim]MEA not checked: V12 (ONM not on it)[/dim]",
        "",
    ]
    assert format_mea_lines(with_warning, "10000") == [
        "[bold yellow]MEA WARNING[/bold yellow]",
        "  Filed altitude 10,000 < required MEA 12,000",
        "  [dim]V12 (ABQ→OTO) requires 12,000[/dim]",
        "[dim]MEA not checked: V12 (ONM not on it)[/dim]",
        "",
    ]


def test_flight_info_compares_violation_to_level_at_entry() -> None:
    """Filed 30,000: J107 flown at 40,000 is clear, J80 at the filed 30,000 and J60 at 20,000 are not."""
    check = MeaCheck(
        max_mea=33000,
        violations=[
            _segment("J107", "MLF", "URNUW", 33000, 40000),
            _segment("J80", "OAL", "ILC", 31000, None),
            _segment("J60", "ABC", "DEF", 25000, 20000),
        ],
        skipped=[],
    )

    assert format_mea_lines(check, "30000") == [
        "[bold yellow]MEA WARNING[/bold yellow]",
        "  Filed altitude 30,000 < required MEA 31,000",
        "  [dim]J80 (OAL→ILC) requires 31,000[/dim]",
        "  [dim]J60 (ABC→DEF) requires 25,000[/dim]",
        "",
    ]
    step_level_only = MeaCheck(
        max_mea=25000,
        violations=[_segment("J60", "ABC", "DEF", 25000, 20000)],
        skipped=[],
    )
    assert format_mea_lines(step_level_only, "30000")[1] == (
        "  Filed level 20,000 on J60 < required MEA 25,000"
    )
