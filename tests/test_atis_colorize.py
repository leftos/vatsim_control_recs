"""Colorized ATIS stays valid Rich markup whatever the controller typed.

ATIS is free text. A backslash right before a highlight that colorize_atis_text
inserts escapes the opening tag in Rich, so the closing tag matches nothing and
the whole briefing render raises.
"""

from rich.markup import render
from rich.style import Style

from backend.data.atis_filter import colorize_atis_text

ATIS_WITH_BACKSLASHES = "LDG VISUAL\\ILS\\RNP-Z RWY 16R, VISUAL/ILS RWY 17."
ATIS_WITH_CYAN_BACKSLASH = "WIND 240\\8, INFO \\ZULU, LDG RWY 16L."
ATIS_WITH_DOUBLE_BACKSLASH = "VISUAL\\\\ILS RWY 16R."
ATIS_WITH_UNHIGHLIGHTED_BACKSLASH = "WIND 240\\10KT, LDG RWY 16L."


def test_backslash_before_a_highlight_renders_literally_and_keeps_the_highlight() -> (
    None
):
    text = render(colorize_atis_text(ATIS_WITH_BACKSLASHES, "Z"))

    assert text.plain == ATIS_WITH_BACKSLASHES
    highlighted = [
        text.plain[s.start : s.end] for s in text.spans if s.style == "yellow"
    ]
    assert "RNP-Z RWY 16R" in highlighted


def test_backslash_before_a_cyan_bold_highlight_renders_literally() -> None:
    text = render(colorize_atis_text(ATIS_WITH_CYAN_BACKSLASH, "Z"))

    assert text.plain == ATIS_WITH_CYAN_BACKSLASH
    # Rich normalizes the tag "cyan bold" to the span style "bold cyan", so
    # compare both sides as parsed styles.
    highlighted = [
        text.plain[s.start : s.end]
        for s in text.spans
        if Style.parse(s.style) == Style.parse("cyan bold")
    ]
    assert "ZULU" in highlighted


def test_double_backslash_before_a_highlight_renders_both_backslashes() -> None:
    text = render(colorize_atis_text(ATIS_WITH_DOUBLE_BACKSLASH, "Z"))

    assert text.plain == ATIS_WITH_DOUBLE_BACKSLASH
    highlighted = [
        text.plain[s.start : s.end] for s in text.spans if s.style == "yellow"
    ]
    assert "ILS RWY 16R" in highlighted


def test_backslash_not_before_a_highlight_is_left_alone() -> None:
    colorized = colorize_atis_text(ATIS_WITH_UNHIGHLIGHTED_BACKSLASH, "Z")
    text = render(colorized)

    assert text.plain == ATIS_WITH_UNHIGHLIGHTED_BACKSLASH
    assert "240\\10KT" in colorized
    highlighted = [
        text.plain[s.start : s.end] for s in text.spans if s.style == "yellow"
    ]
    assert "LDG RWY 16L" in highlighted
