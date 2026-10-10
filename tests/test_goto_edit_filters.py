"""E on a ★ row opens the grouping builder on that grouping, its saved filters shown."""

from tests.conftest import FAVORITE_NAME, BuilderHarness, GoToFactory, GoToHarness


async def _edit_favorite(h: GoToHarness) -> BuilderHarness:
    await h.type_query(FAVORITE_NAME)
    await h.focus_row(f"grouping:{FAVORITE_NAME}")
    await h.pilot.press("e")
    await h.pilot.pause()
    return h.builder()


async def test_e_opens_the_builder_prefilled(goto_modal: GoToFactory) -> None:
    async with goto_modal() as h:
        b = await _edit_favorite(h)

        assert b.screen.draft.items == ["Bay Area", "KSMF"]
        assert b.screen.draft.filters == {"KOAK": "arr", "KSMF": "dep"}
        assert b.screen.draft.original_name == FAVORITE_NAME
        assert b.name_input.value == FAVORITE_NAME


async def test_saved_filter_shows_on_an_airport_member(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        b = await _edit_favorite(h)

        assert b.member_label("ref:KSMF").rstrip().endswith("dep")


async def test_saved_filter_shows_on_a_nested_groupings_member(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        b = await _edit_favorite(h)

        await b.focus_member("ref:Bay Area")
        await b.press("right")

        assert b.member_label("sub:Bay Area:KOAK").rstrip().endswith("arr")
        assert not b.member_label("sub:Bay Area:KSFO").rstrip().endswith(("dep", "arr"))


async def test_f_then_ctrl_s_saves_the_changed_filter(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        b = await _edit_favorite(h)
        await b.focus_member("ref:KSMF")

        await b.press("f")
        assert b.member_label("ref:KSMF").rstrip().endswith("arr")
        await b.press("ctrl+s")

        saved = h.saved_favorites()[FAVORITE_NAME]
        assert saved["filters"] == {"KOAK": "arr", "KSMF": "arr"}
        assert h.app.screen is h.screen
