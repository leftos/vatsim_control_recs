"""Go To hands its picks, ★ rows and presets to the builder and applies its result.

Ctrl+S with picks opens the builder on them, E on a ★ row opens it on that
grouping, E on a preset opens it on a copy of it, and Ctrl+D deletes a ★
grouping after a confirm.
"""

import pytest

from backend.core.groupings import GroupingError
from tests.conftest import FAVORITE_NAME, BuilderHarness, GoToFactory, GoToHarness
from ui.modals import goto_modal as goto_modal_module
from ui.modals.confirm_modal import ChoiceModal, ConfirmModal

# The one preset grouping, and the preset that nests it.
PRESET = "Bay Area"
NESTED_PRESET = "California"


async def _pick_bay_area_and_ksmf(h: GoToHarness) -> None:
    """Pick several: the Bay Area grouping, then KSMF with a dep filter."""
    await h.pilot.press("tab")
    await h.type_query("Bay Area")
    await h.focus_row("grouping:Bay Area")
    await h.pilot.press("space")
    await h.type_query("KSMF")
    await h.focus_row("airport:KSMF")
    await h.pilot.press("space", "f")
    await h.pilot.pause()


async def _open_builder_on_picks(h: GoToHarness) -> BuilderHarness:
    await _pick_bay_area_and_ksmf(h)
    await h.pilot.press("ctrl+s")
    await h.pilot.pause()
    return h.builder()


async def _edit_favorite(h: GoToHarness) -> BuilderHarness:
    await h.type_query(FAVORITE_NAME)
    await h.focus_row(f"grouping:{FAVORITE_NAME}")
    await h.pilot.press("e")
    await h.pilot.pause()
    return h.builder()


async def _copy_preset(h: GoToHarness, name: str = PRESET) -> BuilderHarness:
    """E on a preset row opens the builder on a copy of it."""
    await h.type_query(name)
    await h.focus_row(f"grouping:{name}")
    await h.pilot.press("e")
    await h.pilot.pause()
    return h.builder()


async def _delete_favorite(h: GoToHarness) -> None:
    """Ctrl+D on the ★ row, then Y in the confirm."""
    await h.type_query(FAVORITE_NAME)
    await h.focus_row(f"grouping:{FAVORITE_NAME}")
    await h.pilot.press("ctrl+d")
    await h.pilot.pause()
    assert isinstance(h.app.screen, ConfirmModal)
    assert h.app.screen.message == f"Delete '{FAVORITE_NAME}' from my groupings?"
    await h.pilot.press("y")
    await h.pilot.pause()


def _option_ids(h: GoToHarness) -> list[str | None]:
    return [option.id for option in h.option_list.options]


async def test_ctrl_s_with_picks_opens_the_builder_with_picks_and_filters(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        b = await _open_builder_on_picks(h)

        assert b.screen.draft.items == ["Bay Area", "KSMF"]
        assert b.screen.draft.filters == {"KSMF": "dep"}
        assert b.screen.draft.original_name is None
        assert b.name_input.value == ""


async def test_e_on_a_preset_opens_the_builder_on_a_copy(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        b = await _copy_preset(h)

        assert b.screen.draft.original_name is None
        assert b.screen.draft.name == f"{PRESET} copy"
        assert b.name_input.value == f"{PRESET} copy"
        assert b.screen.draft.items == ["KOAK", "KSFO", "KSJC"]
        assert b.screen.draft.filters == {}


async def test_e_on_a_nested_preset_flattens_it_to_airports(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        b = await _copy_preset(h, NESTED_PRESET)

        assert b.screen.draft.original_name is None
        assert b.screen.draft.items == ["KLAX", "KOAK", "KSAN", "KSFO", "KSJC"]
        assert b.screen.draft.filters == {}


async def test_hint_on_a_preset_row_offers_e_copy(goto_modal: GoToFactory) -> None:
    async with goto_modal() as h:
        await h.type_query(PRESET)
        await h.focus_row(f"grouping:{PRESET}")

        assert h.hint == "Enter open · E copy · Tab pick several · Esc close"


async def test_e_on_an_airport_row_warns_with_the_new_wording(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        await h.type_query("KSFO")
        await h.focus_row("airport:KSFO")
        await h.pilot.press("e")
        await h.pilot.pause()

        assert h.app.screen is h.screen
        assert (
            "warning",
            "E edits ★ groupings and copies presets; KSFO is an airport",
        ) in h.notifications()


async def test_saving_from_the_builder_lists_the_name_under_my_groupings(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        b = await _open_builder_on_picks(h)

        await b.type_name("Trip")
        await b.press("ctrl+s")

        assert h.app.screen is h.screen
        assert h.saved_favorites()["Trip"] == {
            "airports": ["Bay Area", "KSMF"],
            "filters": {"KSMF": "dep"},
        }
        assert h.app.invalidate_calls == 1
        assert h.app.track_calls == []
        assert not h.screen.multi_select_mode
        assert h.screen.selected_items == []
        assert h.search.value == "Trip"
        assert h.row_label("grouping:Trip").startswith("★ Trip (4 airports)")


async def test_rename_from_the_builder_drops_the_old_name(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        b = await _edit_favorite(h)

        await b.type_name("Ops 2")
        await b.press("ctrl+s")

        assert set(h.saved_favorites()) == {"Ops 2"}
        assert FAVORITE_NAME not in h.screen.all_groupings
        assert FAVORITE_NAME not in h.screen.user_grouping_names
        assert all(row[1] != FAVORITE_NAME for row in h.screen.all_results)
        # An E save leaves the search box alone
        assert h.search.value == FAVORITE_NAME
        await h.type_query("Ops 2")
        assert h.row_label("grouping:Ops 2").startswith("★ Ops 2")


async def test_case_only_overwrite_drops_the_old_spelling(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        await h.pilot.press("tab")
        await h.type_query("KSMF")
        await h.focus_row("airport:KSMF")
        await h.pilot.press("space")
        await h.pilot.press("ctrl+s")
        await h.pilot.pause()
        b = h.builder()
        await b.type_name(FAVORITE_NAME.upper())
        await b.press("ctrl+s")
        assert isinstance(h.app.screen, ConfirmModal)
        await h.pilot.press("y")
        await h.pilot.pause()

        assert list(h.saved_favorites()) == [FAVORITE_NAME.upper()]
        assert FAVORITE_NAME not in h.screen.all_groupings
        assert FAVORITE_NAME not in h.screen.user_grouping_names
        assert all(row[1] != FAVORITE_NAME for row in h.screen.all_results)
        assert f"grouping:{FAVORITE_NAME}" not in _option_ids(h)
        assert f"grouping:{FAVORITE_NAME.upper()}" in _option_ids(h)


async def test_saving_an_e_edit_keeps_unrelated_picks(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        await h.pilot.press("tab")
        await h.type_query("KSMF")
        await h.focus_row("airport:KSMF")
        await h.pilot.press("space")
        b = await _edit_favorite(h)

        await b.press("ctrl+s")

        assert h.app.screen is h.screen
        assert h.screen.multi_select_mode
        assert h.screen.selected_identifiers == {"airport:KSMF"}
        assert h.search.value == FAVORITE_NAME
        assert h.row_label(f"grouping:{FAVORITE_NAME}").startswith("[ ] ★")


async def test_renaming_an_e_edit_repoints_its_pick(goto_modal: GoToFactory) -> None:
    async with goto_modal() as h:
        await h.pilot.press("tab")
        await h.type_query(FAVORITE_NAME)
        await h.focus_row(f"grouping:{FAVORITE_NAME}")
        await h.pilot.press("space")
        b = await _edit_favorite(h)

        await b.type_name("Ops 2")
        await b.press("ctrl+s")

        assert h.screen.multi_select_mode
        assert h.screen.selected_identifiers == {"grouping:Ops 2"}
        assert [item[1] for item in h.screen.selected_items] == ["Ops 2"]
        await h.type_query("Ops 2")
        assert h.row_label("grouping:Ops 2").startswith("[x] ★ Ops 2")


async def test_tracked_airport_appears_after_add_and_save(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        await h.type_query("KSAC")
        assert "airport:KSAC" not in _option_ids(h)
        b = await _edit_favorite(h)
        await b.type_query("KSAC")
        await b.focus_result("airport:KSAC")
        await b.press("enter")
        await b.press("ctrl+s")
        await h.pilot.press("1")
        await h.pilot.pause()

        await h.type_query("KSAC")
        assert "airport:KSAC" in _option_ids(h)
        # track_airports invalidated the cache; the handler does not again
        assert h.app.invalidate_calls == 1


async def test_builder_track_result_calls_track_airports(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        b = await _edit_favorite(h)
        await b.type_query("KSAC")
        await b.focus_result("airport:KSAC")
        await b.press("enter")
        assert "ref:KSAC" in b.member_ids()

        await b.press("ctrl+s")
        assert isinstance(h.app.screen, ChoiceModal)
        await h.pilot.press("1")
        await h.pilot.pause()

        assert h.app.track_calls == [("KSAC",)]
        assert h.saved_favorites()[FAVORITE_NAME]["airports"] == [
            "Bay Area",
            "KSMF",
            "KSAC",
        ]
        assert h.app.screen is h.screen


async def test_cancel_from_the_builder_keeps_picks(goto_modal: GoToFactory) -> None:
    async with goto_modal() as h:
        b = await _open_builder_on_picks(h)
        picks = list(h.screen.selected_items)

        await b.press("escape")

        assert h.app.screen is h.screen
        assert h.screen.multi_select_mode
        assert h.screen.selected_items == picks
        assert h.screen.selected_filters == {"airport:KSMF": "dep"}
        assert h.app.invalidate_calls == 0
        assert "Trip" not in h.saved_favorites()


async def test_ctrl_d_then_y_deletes_and_invalidates(goto_modal: GoToFactory) -> None:
    async with goto_modal() as h:
        await _delete_favorite(h)

        assert FAVORITE_NAME not in h.saved_favorites()
        assert h.app.invalidate_calls == 1
        assert f"grouping:{FAVORITE_NAME}" not in _option_ids(h)


async def test_ctrl_d_backend_failure_is_an_error_toast_and_keeps_the_row(
    goto_modal: GoToFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_delete(_name: str) -> None:
        raise GroupingError("disk full")

    monkeypatch.setattr(goto_modal_module, "delete_user_grouping", failing_delete)
    async with goto_modal() as h:
        before = h.favorites_file.read_bytes()

        await _delete_favorite(h)

        assert (
            "error",
            f"Couldn't delete '{FAVORITE_NAME}': disk full",
        ) in h.notifications()
        assert h.favorites_file.read_bytes() == before
        assert f"grouping:{FAVORITE_NAME}" in _option_ids(h)
        assert h.app.invalidate_calls == 0
