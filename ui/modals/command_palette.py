"""Command Palette Modal Screen - Searchable command list"""

from textual.screen import ModalScreen
from textual.widgets import Static, Input, OptionList
from textual.widgets.option_list import Option
from textual.containers import Container
from textual.binding import Binding
from textual.app import ComposeResult


# List of all available commands
COMMANDS = [
    {"name": "Refresh Data", "shortcut": "Ctrl+R", "action": "refresh"},
    {"name": "Pause/Resume Auto-refresh", "shortcut": "Ctrl+P", "action": "toggle_pause"},
    {"name": "Find Airport", "shortcut": "Ctrl+F", "action": "toggle_search"},
    {"name": "Go To", "shortcut": "Ctrl+G", "action": "show_goto"},
    {"name": "Wind Lookup", "shortcut": "Ctrl+W", "action": "show_wind_lookup"},
    {"name": "METAR Lookup", "shortcut": "Ctrl+E", "action": "show_metar_lookup"},
    {"name": "VFR Alternatives", "shortcut": "Ctrl+A", "action": "show_vfr_alternatives"},
    {"name": "Historical Stats", "shortcut": "Ctrl+S", "action": "show_historical_stats"},
    {"name": "Tracked Airports", "shortcut": "Ctrl+T", "action": "show_airport_tracking"},
    {"name": "Open Flight Board", "shortcut": "Enter", "action": "open_flight_board"},
    {"name": "Show Help", "shortcut": "?/F1", "action": "show_help"},
    {"name": "Command Palette", "shortcut": "F2", "action": "show_command_palette"},
    {"name": "Quit", "shortcut": "Ctrl+C", "action": "quit"},
]


class CommandPaletteScreen(ModalScreen):
    """Modal screen with searchable command list"""

    CSS = """
    CommandPaletteScreen {
        align: center middle;
    }

    #palette-container {
        width: 60;
        height: auto;
        max-height: 70%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #palette-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    #palette-input {
        margin-bottom: 1;
    }

    #command-list {
        height: auto;
        max-height: 15;
    }

    #palette-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
    ]

    def __init__(self):
        super().__init__()
        self.filtered_commands = list(COMMANDS)

    def compose(self) -> ComposeResult:
        with Container(id="palette-container"):
            yield Static("Command Palette", id="palette-title")
            yield Input(placeholder="Type to search commands...", id="palette-input")
            yield OptionList(*self._build_options(COMMANDS), id="command-list")
            yield Static("↑↓ Navigate  Enter Execute  Esc Close", id="palette-hint")

    def _build_options(self, commands: list) -> list:
        """Build OptionList options from command list"""
        options = []
        for cmd in commands:
            # Format: "Command Name                 Shortcut"
            label = f"{cmd['name']:<30} [{cmd['shortcut']}]"
            options.append(Option(label, id=cmd['action']))
        return options

    def on_mount(self) -> None:
        """Focus the input when mounted"""
        palette_input = self.query_one("#palette-input", Input)
        palette_input.focus()
        # Select first option
        option_list = self.query_one("#command-list", OptionList)
        if option_list.option_count > 0:
            option_list.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter commands as user types"""
        query = event.value.lower().strip()
        option_list = self.query_one("#command-list", OptionList)

        # Filter commands
        if query:
            self.filtered_commands = [
                cmd for cmd in COMMANDS
                if query in cmd['name'].lower() or query in cmd['shortcut'].lower()
            ]
        else:
            self.filtered_commands = list(COMMANDS)

        # Rebuild the option list
        option_list.clear_options()
        for opt in self._build_options(self.filtered_commands):
            option_list.add_option(opt)

        # Highlight first result
        if option_list.option_count > 0:
            option_list.highlighted = 0

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Execute command when Enter is pressed in input"""
        await self._execute_selected()

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Execute command when option is clicked/selected"""
        await self._execute_selected()

    async def _execute_selected(self) -> None:
        """Execute the currently selected command"""
        option_list = self.query_one("#command-list", OptionList)

        if option_list.highlighted is None or option_list.option_count == 0:
            return

        # Get the action from filtered commands
        if option_list.highlighted < len(self.filtered_commands):
            action = self.filtered_commands[option_list.highlighted]['action']
            self.dismiss()
            # Run the action on the app
            await self.app.run_action(action)

    def action_close(self) -> None:
        """Close the modal"""
        self.dismiss()
