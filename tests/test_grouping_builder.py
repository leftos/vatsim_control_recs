"""The grouping builder screen: its panes, keys, Add search and every save path.

Headless Pilot tests on the `grouping_builder` fixture (tests/conftest.py): five
airports, the "Bay Area" preset and the ★ FAVORITE_NAME from the favorites file,
which lives in a per-test user data directory.
"""

import pytest
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Static

import ui.modals.grouping_draft as grouping_draft_module
from backend.core.groupings import GroupingError, UserGrouping
from tests.conftest import (
    BUILDER_PRESET,
    FAVORITE_NAME,
    FAVORITES,
    BuilderFactory,
    BuilderHarness,
)
from ui.modals.confirm_modal import ChoiceModal, ConfirmModal
from ui.modals.grouping_draft import (
    OVERWRITE_CONFIRM,
    RENAME_CONFIRM,
    SELF_NESTING_MESSAGE,
    BuilderResult,
    discard_message,
    track_offer_message,
)

SEARCH_PROMPT = "type to search airports and groupings"


def panes(harness: BuilderHarness) -> tuple[Vertical, Vertical]:
    """The Add pane and the Members pane."""
    screen = harness.screen
    return (
        screen.query_one("#builder-add-pane", Vertical),
        screen.query_one("#builder-members-pane", Vertical),
    )


# --- layout ------------------------------------------------------------------


async def test_wide_terminal_shows_both_panes(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(size=(110, 40)) as h:
        add, members = panes(h)

        assert add.display and members.display
        assert add.region.y == members.region.y
        assert add.region.right <= members.region.x


async def test_narrow_terminal_shows_one_pane(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(size=(109, 40)) as h:
        add, members = panes(h)

        assert members.display
        assert not add.display


async def test_narrow_tab_flips_between_members_and_add(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(size=(100, 40), items=("KSFO",)) as h:
        add, members = panes(h)
        assert h.members_list.has_focus

        await h.press("tab")
        assert add.display and not members.display
        assert h.search.has_focus

        await h.press("tab")
        assert members.display and not add.display
        assert h.members_list.has_focus


def shown(widget: Widget | None) -> bool:
    """Whether a widget is on screen: it and every container above it display."""
    if widget is None:
        return False
    return all(
        node.display for node in widget.ancestors_with_self if isinstance(node, Widget)
    )


async def test_wide_tab_cycles_name_add_members(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSFO",)) as h:
        assert h.search.has_focus

        await h.press("tab")
        assert h.members_list.has_focus
        await h.press("tab")
        assert h.name_input.has_focus
        await h.press("tab")
        assert h.search.has_focus


async def test_resize_to_narrow_keeps_focus_visible(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(size=(120, 40), items=("KSFO",)) as h:
        await h.type_query("KSAC")
        await h.focus_result("airport:KSAC")

        await h.pilot.resize_terminal(100, 40)
        await h.pilot.pause()

        add, members = panes(h)
        assert add.display and not members.display
        assert h.screen.focused is h.results_list
        assert shown(h.screen.focused)


async def test_resize_to_narrow_from_members_shows_members(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(size=(100, 40), items=("KSFO",)) as h:
        await h.press("tab")
        await h.pilot.resize_terminal(120, 40)
        await h.pilot.pause()
        await h.press("tab")
        assert h.members_list.has_focus

        await h.pilot.resize_terminal(100, 40)
        await h.pilot.pause()

        add, members = panes(h)
        assert members.display and not add.display
        assert shown(h.screen.focused)


async def test_resize_under_a_confirm_restores_visible_focus(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(size=(120, 40), items=("KSFO",), name="Trip") as h:
        await h.type_name("Trips")
        await h.type_query("KSAC")
        await h.focus_result("airport:KSAC")
        await h.press("escape")
        assert isinstance(h.app.screen, ConfirmModal)

        await h.pilot.resize_terminal(100, 40)
        await h.pilot.pause()
        await h.press("n")

        assert h.is_open
        assert h.screen.focused is h.results_list
        assert shown(h.screen.focused)


async def test_hint_fits_one_line_at_narrow_width(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(
        size=(90, 40), items=(BUILDER_PRESET, "KSAC"), tracked=("KSFO",)
    ) as h:
        width = h.screen.query_one("#builder-hint", Static).size.width
        await h.focus_member(f"ref:{BUILDER_PRESET}")
        await h.press("right")
        hints = []
        for option_id in h.member_ids():
            await h.focus_member(str(option_id))
            hints.append(h.hint)
        await h.press("tab")
        await h.type_query("sac")
        hints.append(h.hint)
        await h.focus_result(str(h.result_ids()[0]))
        hints.append(h.hint)

        assert [hint for hint in hints if len(hint) > width] == []
        assert all(hint.endswith("Esc cancel · ○ not tracked") for hint in hints)


async def test_narrow_name_field_is_reached_from_the_top_of_the_pane(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(size=(100, 40), items=("KSFO", "KOAK")) as h:
        await h.focus_member("ref:KSFO")

        await h.press("up")
        assert h.name_input.has_focus

        await h.press("down")
        assert h.members_list.has_focus


# --- the Members list --------------------------------------------------------


async def test_prefilled_members_show_filters_and_collapsed_grouping(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(
        items=(BUILDER_PRESET, "KSMF", "KSJC"),
        filters={"KSMF": "dep", "KOAK": "arr"},
    ) as h:
        assert h.member_ids() == [
            f"ref:{BUILDER_PRESET}",
            f"collapsed:{BUILDER_PRESET}",
            "ref:KSMF",
            "ref:KSJC",
        ]
        assert BUILDER_PRESET in h.member_label(f"ref:{BUILDER_PRESET}")
        assert "▸ 3 airports (→ to expand)" in h.member_label(
            f"collapsed:{BUILDER_PRESET}"
        )
        smf = h.member_label("ref:KSMF")
        assert "KSMF" in smf
        assert "Sacramento Intl" in smf
        assert smf.rstrip().endswith("dep")
        assert not h.member_label("ref:KSJC").rstrip().endswith(("dep", "arr"))


async def test_unknown_ref_has_no_circle(grouping_builder: BuilderFactory) -> None:
    async with grouping_builder(items=("Gone Grouping", "KSFO")) as h:
        label = h.member_label("ref:Gone Grouping")

        assert "○" not in label
        assert label.strip().startswith("Gone Grouping")
        assert label.rstrip().endswith("unknown")
        assert "not tracked" not in h.hint


async def test_untracked_member_shows_circle_and_hint_legend(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSAC", "KSFO")) as h:
        await h.type_query("sac")

        assert h.member_label("ref:KSAC").startswith("○ KSAC")
        assert "○" not in h.member_label("ref:KSFO")
        assert h.result_label("airport:KSAC").startswith("○ KSAC")
        assert "○" not in h.result_label("airport:KSMF")
        assert h.hint.endswith("○ not tracked")


async def test_empty_tracked_list_shows_no_circle(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSAC",), tracked=()) as h:
        await h.type_query("sac")

        assert not any("○" in text for text in h.member_texts())
        assert not any("○" in text for text in h.result_texts())
        assert "not tracked" not in h.hint


async def test_del_removes_top_level_member(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSFO", BUILDER_PRESET, "KOAK")) as h:
        await h.focus_member("ref:KSFO")
        await h.press("delete")
        assert h.member_ids() == [
            f"ref:{BUILDER_PRESET}",
            f"collapsed:{BUILDER_PRESET}",
            "ref:KOAK",
        ]

        await h.focus_member(f"ref:{BUILDER_PRESET}")
        await h.press("delete")
        assert h.member_ids() == ["ref:KOAK"]


async def test_del_on_expanded_member_excludes_it(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=(BUILDER_PRESET, "KSMF")) as h:
        await h.focus_member(f"ref:{BUILDER_PRESET}")
        await h.press("right")

        await h.focus_member(f"sub:{BUILDER_PRESET}:KOAK")
        await h.press("delete")

        assert h.member_ids() == ["ref:KSFO", "ref:KSJC", "ref:KSMF"]


async def test_f_cycles_filter_and_save_writes_it(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSFO", "KOAK"), name="Trip") as h:
        await h.focus_member("ref:KOAK")

        await h.press("f")
        assert h.member_label("ref:KOAK").rstrip().endswith("dep")
        await h.press("f")
        assert h.member_label("ref:KOAK").rstrip().endswith("arr")

        await h.press("ctrl+s")

        assert h.saved_favorites()["Trip"] == {
            "airports": ["KSFO", "KOAK"],
            "filters": {"KOAK": "arr"},
        }
        assert h.results[0].saved.filters == {"KOAK": "arr"}


async def test_f_on_expanded_member_cycles_its_filter(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=(BUILDER_PRESET,)) as h:
        await h.focus_member(f"collapsed:{BUILDER_PRESET}")
        await h.press("right")

        await h.focus_member(f"sub:{BUILDER_PRESET}:KOAK")
        await h.press("f")

        assert h.member_label(f"sub:{BUILDER_PRESET}:KOAK").rstrip().endswith("dep")
        assert h.screen.draft.filters == {"KOAK": "dep"}


@pytest.mark.parametrize("row", ["ref", "collapsed"])
async def test_f_on_grouping_row_warns(
    grouping_builder: BuilderFactory, row: str
) -> None:
    async with grouping_builder(items=(BUILDER_PRESET,)) as h:
        await h.focus_member(f"{row}:{BUILDER_PRESET}")

        await h.press("f")

        severity, message = h.notifications()[-1]
        assert severity == "warning"
        assert message.startswith("F cycles an airport's filter; ")
        assert h.screen.draft.filters == {}


async def test_arrow_keys_expand_and_collapse_nested_grouping(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=(BUILDER_PRESET, "KSMF")) as h:
        await h.focus_member(f"collapsed:{BUILDER_PRESET}")

        await h.press("right")
        assert h.member_ids() == [
            f"ref:{BUILDER_PRESET}",
            f"sub:{BUILDER_PRESET}:KSFO",
            f"sub:{BUILDER_PRESET}:KOAK",
            f"sub:{BUILDER_PRESET}:KSJC",
            "ref:KSMF",
        ]
        assert h.highlighted_member_id() == f"sub:{BUILDER_PRESET}:KSFO"

        await h.focus_member(f"sub:{BUILDER_PRESET}:KSJC")
        await h.press("left")
        assert h.member_ids() == [
            f"ref:{BUILDER_PRESET}",
            f"collapsed:{BUILDER_PRESET}",
            "ref:KSMF",
        ]
        assert h.highlighted_member_id() == f"ref:{BUILDER_PRESET}"
        assert h.notifications() == []


async def test_hint_names_only_the_keys_for_the_focused_row(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSFO", BUILDER_PRESET)) as h:
        await h.focus_member("ref:KSFO")
        assert h.hint == (
            "Del remove · F dep/arr/both · Tab next pane · Ctrl+S save · Esc cancel"
        )

        await h.focus_member(f"collapsed:{BUILDER_PRESET}")
        assert h.hint == ("→ expand · Tab next pane · Ctrl+S save · Esc cancel")


# --- the Add search ----------------------------------------------------------


async def test_enter_adds_highlighted_result_and_tags_it_member(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSFO",)) as h:
        await h.type_query("KSAC")
        assert h.result_ids()[0] == "airport:KSAC"
        assert h.search.has_focus

        await h.press("enter")
        assert h.member_ids() == ["ref:KSFO", "ref:KSAC"]
        assert h.result_label("airport:KSAC").rstrip().endswith("member")

        await h.type_query("$bay")
        await h.focus_result(f"grouping:{BUILDER_PRESET}")
        await h.press("enter")
        assert h.member_ids() == [
            "ref:KSFO",
            "ref:KSAC",
            f"ref:{BUILDER_PRESET}",
            f"collapsed:{BUILDER_PRESET}",
        ]


async def test_enter_on_member_result_warns(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSFO",)) as h:
        await h.type_query("KSFO")
        await h.focus_result("airport:KSFO")

        await h.press("enter")

        assert h.notifications() == [("warning", "'KSFO' is already in this grouping")]
        assert h.member_ids() == ["ref:KSFO"]


async def test_adding_the_grouping_to_itself_warns(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(
        items=("KSMF",), name=FAVORITE_NAME, original_name=FAVORITE_NAME
    ) as h:
        await h.type_query("$my")
        await h.focus_result(f"grouping:{FAVORITE_NAME}")

        await h.press("enter")

        assert h.notifications() == [
            (
                "warning",
                f"'{FAVORITE_NAME}' can't be added: a grouping can't contain itself",
            )
        ]
        assert h.member_ids() == ["ref:KSMF"]


async def test_empty_query_shows_search_prompt(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder() as h:
        assert h.result_texts() == [SEARCH_PROMPT]
        assert h.results_list.get_option_at_index(0).disabled

        await h.type_query("@")
        assert h.result_texts() == [SEARCH_PROMPT]


async def test_search_caps_at_50_with_narrow_row(
    grouping_builder: BuilderFactory,
) -> None:
    airports = {
        f"K{n:03d}": {
            "icao": f"K{n:03d}",
            "name": f"Test Field {n}",
            "city": "Testville",
        }
        for n in range(60)
    }
    async with grouping_builder(airports=airports, tracked=()) as h:
        await h.type_query("test")

        options = h.results_list.options
        assert len(options) == 51
        assert all(str(option.id).startswith("airport:") for option in options[:50])
        assert h.result_texts()[-1] == "showing 50 of 60 — type to narrow"
        assert options[-1].disabled


async def test_at_and_dollar_prefixes_filter_kinds(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder() as h:
        await h.type_query("o")
        ids = h.result_ids()
        assert "airport:KOAK" in ids
        assert f"grouping:{FAVORITE_NAME}" in ids

        await h.type_query("@o")
        ids = h.result_ids()
        assert "airport:KOAK" in ids
        assert not any(str(option_id).startswith("grouping:") for option_id in ids)

        await h.type_query("$y")
        assert h.result_ids() == [
            f"grouping:{FAVORITE_NAME}",
            f"grouping:{BUILDER_PRESET}",
        ]
        assert h.result_label(f"grouping:{FAVORITE_NAME}").startswith("★")


async def test_search_without_index_says_loading(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(loaded=False) as h:
        await h.type_query("o")

        assert h.result_texts()[0] == "airport data is still loading"
        assert h.results_list.get_option_at_index(0).disabled
        assert f"grouping:{FAVORITE_NAME}" in h.result_ids()
        assert not any(
            str(option_id).startswith("airport:") for option_id in h.result_ids()
        )


# --- saving ------------------------------------------------------------------


async def test_save_new_grouping_writes_file_and_dismisses_with_result(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSFO", "KOAK")) as h:
        await h.type_name("Trip")

        await h.press("ctrl+s")

        assert h.results == [
            BuilderResult(
                saved=UserGrouping(name="Trip", items=("KSFO", "KOAK"), filters={}),
                original_name=None,
                track=(),
            )
        ]
        assert h.saved_favorites() == {**FAVORITES, "Trip": ["KSFO", "KOAK"]}
        assert not h.is_open


async def test_save_over_existing_asks_and_no_keeps_file(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSFO",), name=FAVORITE_NAME) as h:
        await h.press("ctrl+s")
        assert isinstance(h.app.screen, ConfirmModal)
        assert h.app.screen.message == OVERWRITE_CONFIRM.format(name=FAVORITE_NAME)

        await h.press("n")

        assert h.is_open
        assert h.saved_favorites() == FAVORITES
        assert h.results == []


async def test_save_over_existing_yes_overwrites(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSFO",), name=FAVORITE_NAME) as h:
        await h.press("ctrl+s")
        await h.press("y")

        assert h.saved_favorites() == {FAVORITE_NAME: ["KSFO"]}
        assert h.results[0].saved.name == FAVORITE_NAME
        assert not h.is_open


async def test_rename_onto_existing_uses_rename_confirm(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(
        favorites={**FAVORITES, "Trip": ["KOAK"]},
        items=("KOAK",),
        name=FAVORITE_NAME,
        original_name="Trip",
    ) as h:
        await h.press("ctrl+s")
        assert isinstance(h.app.screen, ConfirmModal)
        assert h.app.screen.message == RENAME_CONFIRM.format(
            new=FAVORITE_NAME, old="Trip"
        )

        await h.press("y")

        assert h.saved_favorites() == {FAVORITE_NAME: ["KOAK"]}
        assert h.results[0].original_name == "Trip"


async def test_preset_name_shows_inline_error_and_stays_open(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSFO",)) as h:
        assert not h.name_error.display

        await h.type_name(BUILDER_PRESET.lower())
        await h.press("ctrl+s")

        assert h.name_error.display
        assert str(h.name_error.content) == "that name belongs to a preset"
        assert h.is_open
        assert h.saved_favorites() == FAVORITES
        assert h.notifications() == []

        await h.type_name(f"{BUILDER_PRESET} 2")
        assert not h.name_error.display
        assert str(h.name_error.content) == ""


async def test_save_before_data_loaded_warns(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSFO",), name="Trip", loaded=False) as h:
        await h.press("ctrl+s")

        assert h.notifications() == [
            ("warning", "airport data is still loading; try again in a moment")
        ]
        assert h.is_open
        assert h.saved_favorites() == FAVORITES


async def test_self_nesting_shows_error_toast(
    grouping_builder: BuilderFactory,
) -> None:
    # The ★ grouping nests the preset, so naming this one after the preset
    # would make it contain itself
    async with grouping_builder(items=(FAVORITE_NAME,), name=BUILDER_PRESET) as h:
        await h.press("ctrl+s")

        assert h.notifications() == [("error", SELF_NESTING_MESSAGE)]
        assert h.is_open
        assert h.saved_favorites() == FAVORITES


async def test_track_offer_add_and_save_returns_track(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSFO", "KSAC"), name="Trip") as h:
        await h.press("ctrl+s")
        assert isinstance(h.app.screen, ChoiceModal)
        assert h.app.screen.message == track_offer_message(["KSAC"])

        await h.press("1")

        assert h.results[0].track == ("KSAC",)
        assert h.saved_favorites()["Trip"] == ["KSFO", "KSAC"]
        assert not h.is_open


async def test_track_offer_then_overwrite_keeps_track(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSAC",), name=FAVORITE_NAME) as h:
        await h.press("ctrl+s")
        assert isinstance(h.app.screen, ChoiceModal)
        await h.press("1")
        assert isinstance(h.app.screen, ConfirmModal)

        await h.press("y")

        assert h.results[0].track == ("KSAC",)
        assert h.saved_favorites() == {FAVORITE_NAME: ["KSAC"]}


async def test_save_with_empty_tracked_list_writes_without_asking(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSAC",), name="Trip", tracked=()) as h:
        await h.press("ctrl+s")

        assert h.results == [
            BuilderResult(
                saved=UserGrouping(name="Trip", items=("KSAC",), filters={}),
                original_name=None,
                track=(),
            )
        ]
        assert h.saved_favorites()["Trip"] == ["KSAC"]
        assert not h.is_open


async def test_name_error_shows_before_the_track_question(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSAC",)) as h:
        await h.press("ctrl+s")

        assert h.is_open
        assert h.name_error.display
        assert str(h.name_error.content) == "a name is required"
        assert h.saved_favorites() == FAVORITES


async def test_track_offer_save_only_returns_no_track(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSFO", "KSAC"), name="Trip") as h:
        await h.press("ctrl+s")
        await h.press("2")

        assert h.results[0].track == ()
        assert h.saved_favorites()["Trip"] == ["KSFO", "KSAC"]


async def test_track_offer_cancel_and_esc_write_nothing(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSFO", "KSAC"), name="Trip") as h:
        await h.press("ctrl+s")
        await h.press("3")
        assert h.is_open

        await h.press("ctrl+s")
        assert isinstance(h.app.screen, ChoiceModal)
        await h.press("escape")

        assert h.is_open
        assert h.results == []
        assert h.saved_favorites() == FAVORITES


async def test_other_grouping_error_is_error_toast(
    grouping_builder: BuilderFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = "couldn't write 'favorites.json': disk full"

    def fail(*_args: object, **_kwargs: object) -> None:
        raise GroupingError(message)

    monkeypatch.setattr(grouping_draft_module, "save_user_grouping", fail)
    async with grouping_builder(items=("KSFO",), name="Trip") as h:
        await h.press("ctrl+s")

        assert h.notifications() == [("error", message)]
        assert h.is_open
        assert h.results == []


# --- cancelling --------------------------------------------------------------


async def test_esc_unchanged_closes_with_none(
    grouping_builder: BuilderFactory,
) -> None:
    async with grouping_builder(items=("KSFO",), name="Trip") as h:
        await h.press("escape")

        assert h.results == [None]
        assert not h.is_open


async def test_esc_changed_asks_discard(grouping_builder: BuilderFactory) -> None:
    async with grouping_builder(items=("KSFO",), name="Trip") as h:
        await h.type_name("Trips")

        await h.press("escape")
        assert isinstance(h.app.screen, ConfirmModal)
        assert h.app.screen.message == discard_message("Trips")
        await h.press("n")
        assert h.is_open
        assert h.results == []

        await h.press("escape")
        await h.press("y")
        assert h.results == [None]
        assert not h.is_open
