"""Simple confirmation modal screens: yes/no, and a fixed set of choices."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmModal(ModalScreen):
    """Modal that asks the user to confirm or cancel an action."""

    CSS = """
    ConfirmModal {
        align: center middle;
    }

    #confirm-container {
        width: 50;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #confirm-message {
        text-align: center;
        margin-bottom: 1;
    }

    #confirm-hint {
        text-align: center;
        color: $text-muted;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("y", "confirm", "Yes", priority=True),
        Binding("enter", "confirm", "Yes", priority=True),
        Binding("n", "cancel", "No", priority=True),
        Binding("escape", "cancel", "No", priority=True),
    ]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Container(id="confirm-container"):
            yield Static(self.message, id="confirm-message")
            yield Static("Y/Enter: Yes | N/Esc: No", id="confirm-hint")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


@dataclass(frozen=True)
class Choice:
    """One choice of a `ChoiceModal`.

    Attributes:
        key: The key that takes the choice.
        label: The choice's button text.
        value: What the modal dismisses with when the choice is taken.
    """

    key: str
    label: str
    value: str


class ChoiceModal(ModalScreen[str | None]):
    """Modal that offers a fixed set of labelled choices, one button each.

    Each choice's key takes it, Enter takes the first and Esc dismisses with None.
    """

    CSS = """
    ChoiceModal {
        align: center middle;
    }

    #choice-container {
        width: 60;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #choice-message {
        text-align: center;
        margin-bottom: 1;
    }

    #choice-buttons {
        height: auto;
        align: center middle;
    }

    #choice-hint {
        text-align: center;
        color: $text-muted;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "first", "First choice"),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(self, message: str, choices: Sequence[Choice]):
        super().__init__()
        self.message = message
        self.choices: tuple[Choice, ...] = tuple(choices)

    def compose(self) -> ComposeResult:
        with Container(id="choice-container"):
            yield Static(self.message, id="choice-message")
            with Horizontal(id="choice-buttons"):
                for index, choice in enumerate(self.choices):
                    yield Button(f"{choice.key}  {choice.label}", id=f"choice-{index}")
            yield Static("Esc: cancel", id="choice-hint")

    def on_key(self, event: events.Key) -> None:
        """Take the choice whose key was pressed."""
        for choice in self.choices:
            if event.key == choice.key:
                event.stop()
                self.dismiss(choice.value)
                return

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Take the choice whose button was clicked."""
        self.dismiss(self.choices[self._button_index(event.button)].value)

    def action_first(self) -> None:
        """Take the first choice."""
        self.dismiss(self.choices[0].value if self.choices else None)

    def action_cancel(self) -> None:
        """Dismiss with nothing taken."""
        self.dismiss(None)

    def _button_index(self, button: Button) -> int:
        """The choice index a button stands for, from its `choice-<n>` id."""
        return int(str(button.id).removeprefix("choice-"))
