"""Help Modal Screen - Shows all keyboard shortcuts"""

from textual.screen import ModalScreen
from textual.widgets import Static
from textual.containers import Container, Horizontal
from textual.binding import Binding
from textual.app import ComposeResult


LEFT_COLUMN = """\
[bold]General[/bold]
 Ctrl+C   Quit
 Ctrl+R   Refresh data
 Ctrl+P   Pause/Resume

[bold]Navigation[/bold]
 Ctrl+F   Find/filter
 Enter    Flight board
 Escape   Cancel/Close
"""

RIGHT_COLUMN = """\
[bold]Lookups[/bold]
 Ctrl+G   Go To
 Ctrl+W   Wind info
 Ctrl+E   METAR
 Ctrl+A   VFR alternatives
 Ctrl+S   Historical stats
 Ctrl+B   Weather briefing

[bold]Management[/bold]
 Ctrl+T   Tracked airports

[bold]Meta[/bold]
 ?/F1     This help
 F2       Commands
"""


class HelpScreen(ModalScreen):
    """Modal screen showing all keyboard shortcuts"""

    CSS = """
    HelpScreen {
        align: center middle;
    }

    #help-container {
        width: 72;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #help-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #help-columns {
        height: auto;
    }

    .help-column {
        width: 1fr;
        height: auto;
    }

    #help-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="help-container"):
            yield Static("Keyboard Shortcuts", id="help-title")
            with Horizontal(id="help-columns"):
                yield Static(LEFT_COLUMN, classes="help-column")
                yield Static(RIGHT_COLUMN, classes="help-column")
            yield Static("Press Escape to close", id="help-hint")

    def action_close(self) -> None:
        """Close the modal"""
        self.dismiss()
