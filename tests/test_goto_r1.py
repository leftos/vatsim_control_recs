"""Go To shows its mode in the title, its live keys in the hint, and explains no-ops."""

from typing import Any

import pytest

from tests.conftest import FAVORITE_NAME, GoToFactory, GoToHarness

PICKER_TITLE = "Weather Briefing: select an airport, grouping or flight"

# A setup step: ("query", text) types a query, ("row", option_id) focuses the
# list on that row, ("press", key) presses a key.
Step = tuple[str, str]


async def _run(h: GoToHarness, steps: list[Step]) -> None:
    for kind, value in steps:
        if kind == "query":
            await h.type_query(value)
        elif kind == "row":
            await h.focus_row(value)
        elif kind == "focus":
            h.search.focus()
            await h.pilot.pause()
        else:
            await h.pilot.press(value)
    await h.pilot.pause()


# A picked grouping's members show collapsed; → on its row expands them
EXPAND_BAY_AREA: list[Step] = [("row", "grouping:Bay Area"), ("press", "right")]


def _ignore_pick(_result: Any) -> None:
    """Picker-mode callback; these tests never complete a pick."""


# --- Title -------------------------------------------------------------------


async def test_title_in_browse_mode(goto_modal: GoToFactory) -> None:
    async with goto_modal() as h:
        assert h.title == "Go To"


async def test_title_in_multi_select_counts_picks_and_airports(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        await h.pilot.press("tab")
        assert h.title == "Go To · Pick several · 0 picked → 0 airports"

        # California nests Bay Area: KSFO, KOAK, KSJC, KLAX, KSAN, plus KSMF
        await _run(
            h,
            [
                ("query", "California"),
                ("row", "grouping:California"),
                ("press", "space"),
                ("query", "KSMF"),
                ("row", "airport:KSMF"),
                ("press", "space"),
            ],
        )
        assert h.title == "Go To · Pick several · 2 picked → 6 airports"

        await h.pilot.press("tab")
        assert h.title == "Go To"


async def test_picker_mode_keeps_its_own_title(goto_modal: GoToFactory) -> None:
    async with goto_modal(title=PICKER_TITLE, callback=_ignore_pick) as h:
        assert h.title == PICKER_TITLE
        await h.pilot.press("tab")
        assert h.title == PICKER_TITLE


# --- Hint line ---------------------------------------------------------------


async def test_hint_browse_on_airport_row(goto_modal: GoToFactory) -> None:
    async with goto_modal() as h:
        await _run(h, [("query", "KSFO"), ("row", "airport:KSFO")])
        assert h.hint == "Enter open · Tab pick several · Esc close"


async def test_hint_browse_on_favorite_row(goto_modal: GoToFactory) -> None:
    async with goto_modal() as h:
        await _run(h, [("query", FAVORITE_NAME), ("row", f"grouping:{FAVORITE_NAME}")])
        assert h.hint == (
            "Enter open · E edit · Ctrl+D delete · Tab pick several · Esc close"
        )


async def test_hint_browse_on_favorite_row_from_the_input_omits_e(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        await h.type_query(FAVORITE_NAME)
        assert h.search.has_focus
        assert h.hint == ("Enter open · Ctrl+D delete · Tab pick several · Esc close")


async def test_hint_multi_select_on_picked_row(goto_modal: GoToFactory) -> None:
    async with goto_modal() as h:
        await _run(h, [("press", "tab"), ("query", "KSFO"), ("row", "airport:KSFO")])
        assert h.hint == "Space pick · Tab browse · Esc close"

        await h.pilot.press("space")
        assert h.hint == PICKED_ROW_HINT


PICKED_ROW_HINT = (
    "Space pick · F dep/arr/both · Ctrl+Enter open · Ctrl+S save"
    " · Tab browse · Esc close"
)


async def test_hint_over_the_cap_drops_e_and_ctrl_d_first(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        await _run(
            h,
            [
                ("press", "tab"),
                ("query", FAVORITE_NAME),
                ("row", f"grouping:{FAVORITE_NAME}"),
                ("press", "space"),
            ],
        )
        # A picked ★ row adds E edit and Ctrl+D delete: eight items for six
        # slots, so those two give way and Tab and Esc stay
        assert h.hint == PICKED_ROW_HINT


async def test_hint_multi_select_on_member_row(goto_modal: GoToFactory) -> None:
    async with goto_modal() as h:
        await _run(
            h,
            [
                ("press", "tab"),
                ("query", "Bay Area"),
                ("row", "grouping:Bay Area"),
                ("press", "space"),
                *EXPAND_BAY_AREA,
                ("row", "sub:Bay Area/KOAK"),
            ],
        )
        assert h.hint == (
            "F dep/arr/both · ← collapse · Ctrl+Enter open · Ctrl+S save"
            " · Tab browse · Esc close"
        )


async def test_hint_picker_mode_lists_only_its_keys(goto_modal: GoToFactory) -> None:
    async with goto_modal(title=PICKER_TITLE, callback=_ignore_pick) as h:
        await _run(h, [("query", FAVORITE_NAME), ("row", f"grouping:{FAVORITE_NAME}")])
        assert h.hint == "Enter select · Esc close"


# --- Focus -------------------------------------------------------------------


async def test_tab_into_multi_select_focuses_the_list(goto_modal: GoToFactory) -> None:
    async with goto_modal() as h:
        assert h.search.has_focus
        await h.pilot.press("tab")
        assert h.option_list.has_focus


@pytest.mark.parametrize("enter_list", ["down", "tab"])
async def test_letter_typed_in_the_list_goes_to_the_search(
    goto_modal: GoToFactory, enter_list: str
) -> None:
    async with goto_modal() as h:
        await h.pilot.press(enter_list)
        assert h.option_list.has_focus

        await h.pilot.press("k", "s")
        await h.pilot.pause()

        assert h.search.has_focus
        assert h.search.value == "ks"
        # The first row under the AIRPORTS header, where the highlight rests
        highlighted = h.option_list.highlighted
        assert highlighted is not None
        assert h.option_list.get_option_at_index(highlighted).id == "airport:KSAN"


async def test_f_and_space_in_the_browse_list_go_to_the_search(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        await _run(
            h,
            [
                ("row", "airport:KLAX"),
                ("press", "f"),
                ("press", "space"),
                ("press", "o"),
            ],
        )

        assert h.search.has_focus
        assert h.search.value == "f o"
        assert h.notifications() == []


# --- Precondition toasts -----------------------------------------------------

TOAST_CASES: list[tuple[str, list[Step], str]] = [
    (
        "e-on-preset",
        [("query", "Bay Area"), ("row", "grouping:Bay Area"), ("press", "e")],
        "E edits ★ groupings only; 'Bay Area' is a preset",
    ),
    (
        "e-on-airport",
        [("query", "KSFO"), ("row", "airport:KSFO"), ("press", "e")],
        "E edits ★ groupings only; KSFO is an airport",
    ),
    (
        "ctrl-d-on-preset",
        [("query", "Bay Area"), ("row", "grouping:Bay Area"), ("press", "ctrl+d")],
        "Ctrl+D deletes ★ groupings only; 'Bay Area' is a preset",
    ),
    (
        "f-on-unpicked-row",
        [("press", "tab"), ("query", "KSFO"), ("row", "airport:KSFO"), ("press", "f")],
        "F works on picked rows only; KSFO isn't picked",
    ),
    (
        "ctrl-s-in-browse",
        [("press", "ctrl+s")],
        "Ctrl+S saves picks; press Tab to pick several",
    ),
    (
        "ctrl-s-without-picks",
        [("press", "tab"), ("press", "ctrl+s")],
        "Ctrl+S needs at least one pick",
    ),
    (
        "ctrl-enter-in-browse",
        [("press", "ctrl+enter")],
        "Ctrl+Enter opens picks; press Tab to pick several",
    ),
    (
        "ctrl-enter-without-picks",
        [("press", "tab"), ("press", "ctrl+enter")],
        "Ctrl+Enter needs at least one pick",
    ),
    (
        "enter-without-a-row-in-browse",
        [("query", "zzzz"), ("press", "enter")],
        "Enter needs a highlighted row",
    ),
    (
        "space-without-a-row",
        [("press", "tab"), ("query", "zzzz"), ("press", "space")],
        "Space needs a highlighted row",
    ),
    (
        "enter-without-a-row",
        [("press", "tab"), ("query", "zzzz"), ("focus", "search"), ("press", "enter")],
        "Enter needs a highlighted row",
    ),
    (
        "space-on-member-row",
        [
            ("press", "tab"),
            ("query", "Bay Area"),
            ("row", "grouping:Bay Area"),
            ("press", "space"),
            *EXPAND_BAY_AREA,
            ("row", "sub:Bay Area/KOAK"),
            ("press", "space"),
        ],
        "Space can't pick a member row; pick or unpick its grouping",
    ),
]


@pytest.mark.parametrize(
    ("steps", "message"),
    [pytest.param(steps, message, id=case) for case, steps, message in TOAST_CASES],
)
async def test_ignored_key_explains_itself(
    goto_modal: GoToFactory, steps: list[Step], message: str
) -> None:
    async with goto_modal() as h:
        await _run(h, steps)
        assert ("warning", message) in h.notifications()


async def test_ctrl_s_in_picker_mode_explains_itself(goto_modal: GoToFactory) -> None:
    async with goto_modal(title=PICKER_TITLE, callback=_ignore_pick) as h:
        await _run(h, [("press", "ctrl+s")])
        assert (
            "warning",
            "Ctrl+S saves picks; picking isn't available here",
        ) in h.notifications()


async def test_ctrl_d_in_picker_mode_leaves_favorites_alone(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal(title=PICKER_TITLE, callback=_ignore_pick) as h:
        before = h.favorites_file.read_bytes()
        await _run(
            h,
            [
                ("query", FAVORITE_NAME),
                ("row", f"grouping:{FAVORITE_NAME}"),
                ("press", "ctrl+d"),
                ("press", "y"),
            ],
        )

        assert ("warning", "Ctrl+D isn't available here") in h.notifications()
        assert h.app.screen is h.screen
        assert h.favorites_file.read_bytes() == before


async def test_working_keys_raise_no_warning(goto_modal: GoToFactory) -> None:
    async with goto_modal() as h:
        await _run(
            h,
            [
                ("press", "tab"),
                ("query", "KSMF"),
                ("row", "airport:KSMF"),
                ("press", "space"),
                ("press", "f"),
                ("press", "space"),
            ],
        )
        assert h.notifications() == []
