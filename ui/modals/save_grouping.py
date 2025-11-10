"""Save Grouping Modal Screen"""

import json
import os

from textual.screen import ModalScreen
from textual.widgets import Static, Input
from textual.containers import Container
from textual.binding import Binding
from textual.app import ComposeResult


class SaveGroupingModal(ModalScreen):
    """Modal screen for saving tracked airports as a custom grouping"""
    
    CSS = """
    SaveGroupingModal {
        align: center middle;
    }
    
    #save-grouping-container {
        width: 60;
        height: auto;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    
    #save-grouping-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    
    #save-grouping-input-container {
        height: auto;
        margin-bottom: 1;
    }
    
    #save-grouping-result {
        text-align: center;
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }
    
    #save-grouping-hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """
    
    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("enter", "save_grouping", "Save", priority=True),
    ]
    
    def __init__(self, airport_list: list):
        super().__init__()
        self.airport_list = airport_list
    
    def compose(self) -> ComposeResult:
        with Container(id="save-grouping-container"):
            yield Static("Save as Custom Grouping", id="save-grouping-title")
            with Container(id="save-grouping-input-container"):
                yield Input(
                    placeholder="Enter grouping name (e.g., My Airports)",
                    id="save-grouping-input"
                )
            yield Static("", id="save-grouping-result")
            yield Static(
                f"This will save {len(self.airport_list)} airports to custom_groupings.json\nPress Enter to save, Escape to cancel",
                id="save-grouping-hint"
            )
    
    def on_mount(self) -> None:
        """Focus the input when mounted"""
        grouping_input = self.query_one("#save-grouping-input", Input)
        grouping_input.focus()
    
    def action_save_grouping(self) -> None:
        """Save the grouping to custom_groupings.json"""
        grouping_input = self.query_one("#save-grouping-input", Input)
        grouping_name = grouping_input.value.strip()
        
        if not grouping_name:
            result_widget = self.query_one("#save-grouping-result", Static)
            result_widget.update("Please enter a grouping name")
            return
        
        # Get the path to custom_groupings.json
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        groupings_file = os.path.join(script_dir, 'data', 'custom_groupings.json')
        
        try:
            # Load existing groupings
            with open(groupings_file, 'r', encoding='utf-8') as f:
                groupings_data = json.load(f)
            
            # Add or update the grouping
            groupings_data[grouping_name] = sorted(self.airport_list)
            
            # Save back to file
            with open(groupings_file, 'w', encoding='utf-8') as f:
                json.dump(groupings_data, f, indent=2, ensure_ascii=False)
            
            self.dismiss(f"Saved '{grouping_name}' with {len(self.airport_list)} airports")
        except Exception as e:
            result_widget = self.query_one("#save-grouping-result", Static)
            result_widget.update(f"Error saving grouping: {str(e)}")
    
    def action_close(self) -> None:
        """Close without saving"""
        self.dismiss(None)