"""Go To list hygiene: sections, the row cap, collapsed members and the Picked line."""

from typing import Any

import pytest
from textual.widgets import Static

from tests import conftest
from tests.conftest import FAVORITE_NAME, PRESET_GROUPINGS, GoToFactory, GoToHarness
from ui.modals import flight_board as flight_board_module

# 150 flights push the list past its 100-row cap
MANY_PILOTS: list[dict[str, Any]] = [
    {"callsign": f"TST{n:03}", "flight_plan": {"departure": "KSFO", "arrival": "KLAX"}}
    for n in range(150)
]

CAP_ROW = "showing 100 of {total} — type to narrow"


def _rows(h: GoToHarness) -> list[str]:
    """Each row as its option id, or as "-- <text>" for a row the cursor skips."""
    rows = []
    for option in h.option_list.options:
        text = getattr(option.prompt, "plain", str(option.prompt))
        rows.append(f"-- {text}" if option.disabled else str(option.id))
    return rows


def _highlighted_id(h: GoToHarness) -> str | None:
    index = h.option_list.highlighted
    if index is None:
        return None
    option = h.option_list.get_option_at_index(index)
    assert not option.disabled, f"the highlight rests on {option.prompt!r}"
    return option.id


def _visible_text(h: GoToHarness, prefix: str) -> list[str]:
    """The text of every displayed Static in the modal that starts with prefix."""
    texts = [str(widget.content) for widget in h.screen.query(Static) if widget.display]
    return [text for text in texts if text.startswith(prefix)]


async def _pick(h: GoToHarness, query: str, option_id: str) -> None:
    await h.type_query(query)
    await h.focus_row(option_id)
    await h.pilot.press("space")
    await h.pilot.pause()


async def _pick_bay_area(h: GoToHarness) -> None:
    await h.pilot.press("tab")
    await _pick(h, "Bay Area", "grouping:Bay Area")


async def _expand_bay_area(h: GoToHarness) -> None:
    await h.focus_row("members:Bay Area")
    await h.pilot.press("right")
    await h.pilot.pause()


# --- Sections ----------------------------------------------------------------


async def test_my_groupings_come_first_under_their_header(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        assert _rows(h) == [
            "-- ★ MY GROUPINGS",
            f"grouping:{FAVORITE_NAME}",
            "-- AIRPORTS (tracked only)",
            "airport:KLAX",
            "airport:KOAK",
            "airport:KSAN",
            "airport:KSFO",
            "airport:KSJC",
            "airport:KSMF",
            "-- GROUPINGS",
            "grouping:Bay Area",
            "grouping:California",
            "-- FLIGHTS",
            "flight:SWA22",
            "flight:UAL1",
        ]


async def test_empty_section_has_no_header(goto_modal: GoToFactory) -> None:
    async with goto_modal() as h:
        await h.type_query("KSFO")
        assert _rows(h) == [
            "-- AIRPORTS (tracked only)",
            "airport:KSFO",
            "-- FLIGHTS",
            "flight:UAL1",
        ]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("@ksf", ["-- AIRPORTS (tracked only)", "airport:KSFO"]),
        ("#ual", ["-- FLIGHTS", "flight:UAL1"]),
        ("$bay", ["-- GROUPINGS", "grouping:Bay Area"]),
        (
            "$",
            [
                "-- ★ MY GROUPINGS",
                f"grouping:{FAVORITE_NAME}",
                "-- GROUPINGS",
                "grouping:Bay Area",
                "grouping:California",
            ],
        ),
    ],
)
async def test_prefix_shows_only_its_section(
    goto_modal: GoToFactory, query: str, expected: list[str]
) -> None:
    async with goto_modal() as h:
        await h.type_query(query)
        assert _rows(h) == expected


# --- The row cap -------------------------------------------------------------


async def test_cap_row_counts_matching_rows(
    goto_modal: GoToFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(conftest, "PILOTS", MANY_PILOTS)
    async with goto_modal() as h:
        # 1 ★ grouping, 6 airports, 2 groupings and 150 flights match
        rows = _rows(h)
        assert rows[-1] == "-- " + CAP_ROW.format(total=159)
        assert len([row for row in rows if not row.startswith("-- ")]) == 100
        assert rows[-2] == "flight:TST090"

        await h.type_query("#tst")
        assert _rows(h)[-1] == "-- " + CAP_ROW.format(total=150)

        # Picking several hides the flights: nine rows, under the cap
        await h.type_query("")
        await h.pilot.press("tab")
        assert not any(row.startswith("-- showing") for row in _rows(h))


async def test_highlight_skips_headers_and_cap_row(
    goto_modal: GoToFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(conftest, "PILOTS", MANY_PILOTS)
    async with goto_modal() as h:
        assert _highlighted_id(h) == f"grouping:{FAVORITE_NAME}"

        await h.pilot.press("down")
        assert h.option_list.has_focus
        assert _highlighted_id(h) == f"grouping:{FAVORITE_NAME}"

        # The cursor steps over the AIRPORTS header
        await h.pilot.press("down")
        assert _highlighted_id(h) == "airport:KLAX"
        await h.pilot.press("up")
        assert _highlighted_id(h) == f"grouping:{FAVORITE_NAME}"

        # Up from the first row leaves the list, not onto the header above it
        await h.pilot.press("up")
        assert h.search.has_focus

        # Up from the search lands on the last flight, not on the cap row
        await h.pilot.press("up")
        assert h.option_list.has_focus
        assert _highlighted_id(h) == "flight:TST090"
        await h.pilot.press("down")
        assert h.search.has_focus

        await h.type_query("KSFO")
        assert _highlighted_id(h) == "airport:KSFO"


async def test_stale_order_shows_each_header_once(goto_modal: GoToFactory) -> None:
    async with goto_modal() as h:
        # A ★ grouping saved over a preset's name while the results cache is
        # stale: its row still sits among the presets
        h.screen.user_grouping_names.add("Bay Area")
        h.screen._apply_current_filter()
        await h.pilot.pause()

        rows = _rows(h)
        headers = [row for row in rows if row.startswith("-- ")]
        assert headers == [
            "-- ★ MY GROUPINGS",
            "-- AIRPORTS (tracked only)",
            "-- GROUPINGS",
            "-- FLIGHTS",
        ]
        assert rows[: rows.index("-- AIRPORTS (tracked only)")] == [
            "-- ★ MY GROUPINGS",
            f"grouping:{FAVORITE_NAME}",
            "grouping:Bay Area",
        ]


# --- Collapsed members -------------------------------------------------------

COLLAPSED_ROWS = ["-- GROUPINGS", "grouping:Bay Area", "members:Bay Area"]
BAY_AREA_MEMBERS = ["sub:Bay Area/KOAK", "sub:Bay Area/KSFO", "sub:Bay Area/KSJC"]


async def test_picked_grouping_collapses_and_expands(goto_modal: GoToFactory) -> None:
    async with goto_modal() as h:
        await _pick_bay_area(h)
        assert _rows(h) == COLLAPSED_ROWS
        assert h.row_label("members:Bay Area").strip() == "▸ 3 airports (→ to expand)"

        await h.focus_row("members:Bay Area")
        assert h.hint == (
            "→ expand · Ctrl+Enter open · Ctrl+S save · Tab browse · Esc close"
        )

        # Expanding keeps the highlight in place: on the first member
        await h.pilot.press("right")
        await h.pilot.pause()
        assert _rows(h) == ["-- GROUPINGS", "grouping:Bay Area", *BAY_AREA_MEMBERS]
        assert _highlighted_id(h) == "sub:Bay Area/KOAK"

        # Collapsing from a member returns the highlight to its grouping
        await h.focus_row("sub:Bay Area/KSFO")
        await h.pilot.press("left")
        await h.pilot.pause()
        assert _rows(h) == COLLAPSED_ROWS
        assert _highlighted_id(h) == "grouping:Bay Area"

        # → on the grouping row itself expands it too, and again does nothing
        await h.pilot.press("right", "right")
        await h.pilot.pause()
        assert _rows(h) == ["-- GROUPINGS", "grouping:Bay Area", *BAY_AREA_MEMBERS]
        assert _highlighted_id(h) == "grouping:Bay Area"
        assert h.notifications() == []


async def test_two_expanded_groupings_sharing_an_airport(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        # California nests Bay Area, so both list KOAK among their members
        await h.pilot.press("tab")
        await _pick(h, "California", "grouping:California")
        await _pick(h, "Bay Area", "grouping:Bay Area")
        await h.type_query("")
        for name in ("Bay Area", "California"):
            await h.focus_row(f"members:{name}")
            await h.pilot.press("right")
            await h.pilot.pause()

        both = ("sub:Bay Area/KOAK", "sub:California/KOAK")
        await h.focus_row(both[1])
        await h.pilot.press("f")
        await h.pilot.pause()
        for option_id in both:
            assert h.row_label(option_id).strip().startswith("[D▸] @ KOAK")

        await h.focus_row(both[0])
        await h.pilot.press("f")
        await h.pilot.pause()
        for option_id in both:
            assert h.row_label(option_id).strip().startswith("[◂A] @ KOAK")


def _in_view(h: GoToHarness) -> bool:
    """True when the highlighted row is inside the list's scroll window."""
    index = h.option_list.highlighted
    assert index is not None
    top = h.option_list.scroll_y
    return top <= index < top + h.option_list.scrollable_content_region.height


async def test_changing_a_deep_row_keeps_it_in_view(
    goto_modal: GoToFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    many = {f"G{n:03}": ["KSFO"] for n in range(80)}
    monkeypatch.setattr(conftest, "PRESET_GROUPINGS", {**PRESET_GROUPINGS, **many})
    async with goto_modal() as h:
        await h.pilot.press("tab")
        await h.focus_row("grouping:G070")
        await h.pilot.press("space")
        await h.pilot.pause()

        for key in ("f", "right", "left"):
            await h.focus_row("grouping:G070")
            await h.pilot.press(key)
            await h.pilot.pause()
            assert _highlighted_id(h) == "grouping:G070", key
            assert _in_view(h), key


async def test_f_on_expanded_member_sets_its_filter(
    goto_modal: GoToFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flight_board_module, "download_vatsim_data", dict)
    monkeypatch.setattr(flight_board_module, "get_metar_batch", lambda _a: {})
    monkeypatch.setattr(flight_board_module, "get_datis_for_airports", lambda _a: {})
    async with goto_modal() as h:
        await _pick_bay_area(h)
        await _expand_bay_area(h)
        await h.focus_row("sub:Bay Area/KOAK")
        await h.pilot.press("f")
        await h.pilot.pause()
        assert h.row_label("sub:Bay Area/KOAK").strip().startswith("[D▸] @ KOAK")
        assert h.row_label("sub:Bay Area/KSFO").strip().startswith("[  ] @ KSFO")

        # Collapsing and expanding again keeps the member's filter
        await h.pilot.press("left")
        await h.pilot.pause()
        await _expand_bay_area(h)
        assert h.row_label("sub:Bay Area/KOAK").strip().startswith("[D▸] @ KOAK")

        await h.pilot.press("ctrl+enter")
        await h.pilot.pause()
        assert h.app.active_flight_board is not None
        assert h.app.active_flight_board.airport_filters == {"KOAK": "dep"}


# --- The Picked line ---------------------------------------------------------

LONG_NAMES = [
    "Northern California TRACON Arrivals Bundle",
    "Southern California TRACON Departures Bundle",
]


async def test_picked_line_in_pick_several_only(
    goto_modal: GoToFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    presets = {**PRESET_GROUPINGS, **{name: ["KSFO"] for name in LONG_NAMES}}
    monkeypatch.setattr(conftest, "PRESET_GROUPINGS", presets)
    async with goto_modal() as h:
        await h.pilot.press("tab")
        assert _visible_text(h, "Picked:") == []

        await _pick(h, "Bay Area", "grouping:Bay Area")
        await _pick(h, "KSMF", "airport:KSMF")
        assert _visible_text(h, "Picked:") == ["Picked: Bay Area, KSMF"]

        # Names that would overflow the line give way to a "+N" count
        await _pick(h, "Northern", f"grouping:{LONG_NAMES[0]}")
        await _pick(h, "Southern", f"grouping:{LONG_NAMES[1]}")
        await _pick(h, "California", "grouping:California")
        assert _visible_text(h, "Picked:") == [
            f"Picked: Bay Area, KSMF, {LONG_NAMES[0]}, +2"
        ]

        # Browse keeps the picks but shows no Picked line
        await h.pilot.press("tab")
        assert _visible_text(h, "Picked:") == []
        assert h.screen.query_one("#goto-picked", Static).display is False


async def test_picked_line_fits_a_narrow_terminal(
    goto_modal: GoToFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    presets = {**PRESET_GROUPINGS, **{name: ["KSFO"] for name in LONG_NAMES}}
    monkeypatch.setattr(conftest, "PRESET_GROUPINGS", presets)
    async with goto_modal() as h:
        await h.pilot.press("tab")
        await _pick(h, "Bay Area", "grouping:Bay Area")
        await _pick(h, "KSMF", "airport:KSMF")
        await _pick(h, "California", "grouping:California")
        await _pick(h, "Northern", f"grouping:{LONG_NAMES[0]}")
        await _pick(h, "Southern", f"grouping:{LONG_NAMES[1]}")
        assert _visible_text(h, "Picked:") == [
            f"Picked: Bay Area, KSMF, California, {LONG_NAMES[0]}, +1"
        ]

        # At 80 columns the line holds fewer names, and the count stays in view
        await h.pilot.resize_terminal(80, 40)
        await h.pilot.pause()
        picked = h.screen.query_one("#goto-picked", Static)
        assert _visible_text(h, "Picked:") == ["Picked: Bay Area, KSMF, California, +2"]
        assert len(str(picked.content)) <= picked.content_region.width


# --- Inert rows --------------------------------------------------------------

HEADER_REASON = "the highlighted row is a section header"
MEMBERS_REASON = "the highlighted row is a grouping's collapsed members; → expands them"
CAP_REASON = "the highlighted row is the row count; type to narrow"


async def _highlight_index(h: GoToHarness, index: int) -> None:
    """Rest the highlight on a row the cursor would skip, as a click or a bug could."""
    h.option_list.focus()
    h.option_list.highlighted = index % h.option_list.option_count
    await h.pilot.pause()


async def _press_from_search(h: GoToHarness, key: str) -> None:
    h.search.focus()
    await h.pilot.press(key)


async def _keys_on_header(h: GoToHarness) -> None:
    for key in ("e", "ctrl+d"):
        await _highlight_index(h, 0)
        await h.pilot.press(key)
    await _highlight_index(h, 0)
    await _press_from_search(h, "enter")


async def _enter_on_cap_row(h: GoToHarness) -> None:
    await _highlight_index(h, -1)
    await _press_from_search(h, "enter")


async def _keys_on_collapsed_row(h: GoToHarness) -> None:
    await _pick_bay_area(h)
    await h.focus_row("members:Bay Area")
    await h.pilot.press("space", "f")


async def _arrows_on_rows_without_members(h: GoToHarness) -> None:
    await h.pilot.press("tab")
    await h.type_query("KSFO")
    await h.focus_row("airport:KSFO")
    await h.pilot.press("right")
    await h.type_query("Bay Area")
    await h.focus_row("grouping:Bay Area")
    await h.pilot.press("left")


INERT_ROW_CASES = [
    pytest.param(
        _keys_on_header,
        [
            f"E edits ★ groupings only; {HEADER_REASON}",
            f"Ctrl+D deletes ★ groupings only; {HEADER_REASON}",
            f"Enter can't open this row; {HEADER_REASON}",
        ],
        id="header",
    ),
    pytest.param(
        _enter_on_cap_row, [f"Enter can't open this row; {CAP_REASON}"], id="cap-row"
    ),
    pytest.param(
        _keys_on_collapsed_row,
        [
            f"Space can't pick this row; {MEMBERS_REASON}",
            f"F works on picked rows only; {MEMBERS_REASON}",
        ],
        id="collapsed-row",
    ),
    pytest.param(
        _arrows_on_rows_without_members,
        [
            "→ expands a picked grouping's members; KSFO is an airport",
            "← collapses a picked grouping's members; 'Bay Area' isn't picked",
        ],
        id="arrows-without-members",
    ),
]


@pytest.mark.parametrize(("press_keys", "warnings"), INERT_ROW_CASES)
async def test_key_on_header_warns(
    goto_modal: GoToFactory,
    monkeypatch: pytest.MonkeyPatch,
    press_keys: Any,
    warnings: list[str],
) -> None:
    monkeypatch.setattr(conftest, "PILOTS", MANY_PILOTS)
    async with goto_modal() as h:
        await press_keys(h)
        await h.pilot.pause()

        assert h.app.screen is h.screen
        assert h.notifications() == [("warning", message) for message in warnings]
