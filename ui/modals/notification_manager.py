"""
Notification Manager - Reusable notification system for modal screens.

Provides toast-style notifications with optional flash animation
for important alerts (e.g., weather changes, runway changes at staffed airports).
"""

from typing import Optional, Callable
from textual.screen import ModalScreen
from textual.widgets import Static
from textual.timer import Timer


class NotificationManager:
    """
    Manages toast-style notifications for a modal screen.

    Features:
    - Auto-dismiss after configurable duration
    - Optional flash animation for high-priority notifications
    - First escape dismisses notification, second closes modal
    """

    def __init__(
        self,
        screen: ModalScreen,
        notification_id: str = "notification-toast",
        dismiss_seconds: float = 15.0,
        flash_interval: float = 0.3,
        flash_cycles: int = 3,
    ):
        """
        Initialize the notification manager.

        Args:
            screen: The ModalScreen that owns this notification manager
            notification_id: Widget ID for the notification Static widget
            dismiss_seconds: Seconds before auto-dismissing notification
            flash_interval: Seconds between flash state changes
            flash_cycles: Number of bright-dim cycles for flash animation
        """
        self.screen = screen
        self.notification_id = notification_id
        self.dismiss_seconds = dismiss_seconds
        self.flash_interval = flash_interval
        self.flash_cycles = flash_cycles

        # Timer references
        self._dismiss_timer: Optional[Timer] = None
        self._flash_timer: Optional[Timer] = None

        # Flash animation state
        self._flash_count = 0
        self._flash_bright = True
        self._text_bright = ""
        self._text_dim = ""

    def cleanup(self) -> None:
        """Stop all timers. Call this in on_unmount()."""
        if self._dismiss_timer:
            self._dismiss_timer.stop()
            self._dismiss_timer = None
        if self._flash_timer:
            self._flash_timer.stop()
            self._flash_timer = None

    def show(
        self,
        text_bright: str,
        text_dim: Optional[str] = None,
        flash: bool = False,
    ) -> None:
        """
        Show a notification.

        Args:
            text_bright: Rich markup text for normal/bright state
            text_dim: Rich markup text for dim state (optional, for flash animation)
            flash: Whether to enable flash animation
        """
        self._text_bright = text_bright
        self._text_dim = text_dim or f"[dim]{text_bright}[/dim]"

        try:
            notification = self.screen.query_one(f"#{self.notification_id}", Static)
            notification.update(text_bright)
            notification.remove_class("hidden")

            # Cancel existing timers
            if self._dismiss_timer:
                self._dismiss_timer.stop()
            if self._flash_timer:
                self._flash_timer.stop()
                self._flash_timer = None

            # Start flash animation if requested
            if flash:
                # flash_cycles * 2 for bright-dim-bright-dim pattern
                self._flash_count = self.flash_cycles * 2
                self._flash_bright = True
                self._flash_timer = self.screen.set_interval(
                    self.flash_interval,
                    self._do_flash,
                )

            # Auto-dismiss timer
            self._dismiss_timer = self.screen.set_timer(
                self.dismiss_seconds,
                self.dismiss,
            )
        except Exception:
            pass

    def _do_flash(self) -> None:
        """Toggle notification between bright and dim states."""
        if self._flash_count <= 0:
            # Stop flashing, leave on bright
            if self._flash_timer:
                self._flash_timer.stop()
                self._flash_timer = None
            try:
                notification = self.screen.query_one(f"#{self.notification_id}", Static)
                notification.update(self._text_bright)
            except Exception:
                pass
            return

        # Toggle state
        self._flash_bright = not self._flash_bright
        self._flash_count -= 1

        try:
            notification = self.screen.query_one(f"#{self.notification_id}", Static)
            if self._flash_bright:
                notification.update(self._text_bright)
            else:
                notification.update(self._text_dim)
        except Exception:
            pass

    def dismiss(self) -> None:
        """Hide the notification and stop all timers."""
        if self._flash_timer:
            self._flash_timer.stop()
            self._flash_timer = None
        if self._dismiss_timer:
            self._dismiss_timer.stop()
            self._dismiss_timer = None

        try:
            notification = self.screen.query_one(f"#{self.notification_id}", Static)
            notification.add_class("hidden")
        except Exception:
            pass

    def is_visible(self) -> bool:
        """Check if notification is currently visible."""
        try:
            notification = self.screen.query_one(f"#{self.notification_id}", Static)
            return "hidden" not in notification.classes
        except Exception:
            return False
