"""Simple confirmation modal screen."""

from textual.screen import ModalScreen
from textual.widgets import Static
from textual.containers import Container
from textual.binding import Binding
from textual.app import ComposeResult


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

    BINDINGS = [
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
