"""Textual terminal driver selection helpers."""

from __future__ import annotations

import os
import sys

from textual.driver import Driver


def get_textual_driver_class() -> type[Driver] | None:
    """Return a Textual driver override for terminals that can probe DEC 2026."""
    if not _should_probe_synchronized_output():
        return None

    from textual.drivers.windows_driver import WindowsDriver

    class SynchronizedOutputWindowsDriver(WindowsDriver):
        """Windows driver that asks the terminal about DEC synchronized output."""

        def start_application_mode(self) -> None:
            super().start_application_mode()
            self._request_terminal_sync_mode_support()

        def _request_terminal_sync_mode_support(self) -> None:
            self.write("\033[?2026$p")
            self.flush()

    return SynchronizedOutputWindowsDriver


def _should_probe_synchronized_output() -> bool:
    if sys.platform != "win32":
        return False

    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    return bool(os.environ.get("WT_SESSION")) or term_program == "vscode"
