"""Edit mode restores a ★ favorite's saved dep/arr filters onto the rows that show them."""

from tests.conftest import FAVORITE_NAME, GoToFactory, GoToHarness


async def _edit_favorite(h: GoToHarness) -> None:
    await h.type_query(FAVORITE_NAME)
    await h.focus_row(f"grouping:{FAVORITE_NAME}")
    await h.pilot.press("e")
    await h.pilot.pause()


async def test_edit_shows_saved_filter_on_individually_picked_airport(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        await _edit_favorite(h)

        await h.type_query("KSMF")
        assert h.row_label("airport:KSMF").startswith("[x D▸] @ KSMF")


async def test_edit_shows_saved_filter_on_grouping_member(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        await _edit_favorite(h)

        await h.type_query("Bay Area")
        # Members show collapsed under their grouping until → expands them
        await h.focus_row("grouping:Bay Area")
        await h.pilot.press("right")
        await h.pilot.pause()
        assert h.row_label("sub:Bay Area/KOAK").strip().startswith("[◂A] @ KOAK")
        assert h.row_label("sub:Bay Area/KSFO").strip().startswith("[  ] @ KSFO")


async def test_f_on_edited_airport_changes_the_saved_filter(
    goto_modal: GoToFactory,
) -> None:
    async with goto_modal() as h:
        await _edit_favorite(h)
        await h.type_query("KSMF")
        await h.focus_row("airport:KSMF")

        await h.pilot.press("f")
        assert h.row_label("airport:KSMF").startswith("[x ◂A] @ KSMF")

        await h.pilot.press("ctrl+s")
        await h.pilot.press("enter")
        await h.pilot.pause()

        saved = h.saved_favorites()[FAVORITE_NAME]
        assert saved["filters"] == {"KOAK": "arr", "KSMF": "arr"}
