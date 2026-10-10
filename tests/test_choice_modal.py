"""The ChoiceModal: a button per choice, each choice's key, Enter and Esc."""

from textual.app import App
from textual.widgets import Button, Static

from ui.modals import Choice, ChoiceModal

MESSAGE = "2 of these aren't tracked (KHWD, KCCR). Add them to your tracked airports?"
CHOICES = (
    Choice(key="1", label="Add and save", value="add"),
    Choice(key="2", label="Save only", value="save"),
    Choice(key="3", label="Cancel", value="cancel"),
)


class ChoiceHost(App):
    """The smallest app the modal can run under."""


async def press_in_modal(key: str) -> list[str | None]:
    """Push the modal, press one key, and return every result its callback got."""
    app = ChoiceHost()
    results: list[str | None] = []
    async with app.run_test() as pilot:
        app.push_screen(ChoiceModal(MESSAGE, CHOICES), callback=results.append)
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()
    return results


async def test_each_key_takes_its_choice() -> None:
    assert await press_in_modal("1") == ["add"]
    assert await press_in_modal("2") == ["save"]
    assert await press_in_modal("3") == ["cancel"]


async def test_enter_takes_the_first_choice() -> None:
    assert await press_in_modal("enter") == ["add"]


async def test_escape_dismisses_with_nothing() -> None:
    assert await press_in_modal("escape") == [None]


async def test_tab_then_enter_takes_the_focused_choice() -> None:
    """Tab twice moves focus to Cancel; Enter must take it, not the first choice."""
    app = ChoiceHost()
    results: list[str | None] = []
    async with app.run_test() as pilot:
        app.push_screen(ChoiceModal(MESSAGE, CHOICES), callback=results.append)
        await pilot.pause()

        await pilot.press("tab")
        await pilot.press("tab")
        await pilot.press("enter")
        await pilot.pause()

    assert results == ["cancel"]


async def test_one_button_per_choice_in_order_under_the_message() -> None:
    app = ChoiceHost()
    async with app.run_test() as pilot:
        app.push_screen(ChoiceModal(MESSAGE, CHOICES))
        await pilot.pause()

        assert str(app.screen.query_one("#choice-message", Static).content) == MESSAGE
        assert [str(button.label) for button in app.screen.query(Button)] == [
            "1  Add and save",
            "2  Save only",
            "3  Cancel",
        ]


async def test_clicking_a_button_takes_its_choice() -> None:
    app = ChoiceHost()
    results: list[str | None] = []
    async with app.run_test(size=(120, 40)) as pilot:
        app.push_screen(ChoiceModal(MESSAGE, CHOICES), callback=results.append)
        await pilot.pause()

        await pilot.click("#choice-1")
        await pilot.pause()

    assert results == ["save"]
