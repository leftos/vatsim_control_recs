"""
Notification Manager - Reusable notification system for modal screens.

Provides toast-style notifications with optional flash animation
for important alerts (e.g., weather changes, runway changes at staffed airports).

Notifications are stacked vertically from the bottom-right and each has
an independent auto-dismiss timer.
"""

from typing import Optional, Dict, Tuple
from textual.screen import ModalScreen
from textual.widgets import Static
from textual.containers import VerticalScroll
from textual.timer import Timer


class Notification(Static):
    """A single notification widget with independent timer and flash animation."""

    def __init__(
        self,
        notification_id: str,
        text_bright: str,
        text_dim: str,
        flash: bool,
        flash_interval: float,
        flash_cycles: int,
    ):
        super().__init__(text_bright, id=notification_id, classes="notification-item", markup=True)
        self.text_bright = text_bright
        self.text_dim = text_dim
        self.flash = flash
        self.flash_interval = flash_interval
        self.flash_cycles = flash_cycles

        # Flash animation state
        self._flash_timer: Optional[Timer] = None
        self._flash_count = 0
        self._flash_bright = True

    def on_mount(self) -> None:
        """Start flash animation when notification is mounted."""
        if self.flash:
            self._flash_count = self.flash_cycles * 2
            self._flash_bright = True
            self._flash_timer = self.set_interval(
                self.flash_interval,
                self._do_flash,
            )

    def _do_flash(self) -> None:
        """Toggle notification between bright and dim states."""
        if self._flash_count <= 0:
            # Stop flashing, leave on bright
            if self._flash_timer:
                self._flash_timer.stop()
                self._flash_timer = None
            self.update(self.text_bright)
            return

        # Toggle state
        self._flash_bright = not self._flash_bright
        self._flash_count -= 1

        if self._flash_bright:
            self.update(self.text_bright)
        else:
            self.update(self.text_dim)

    def cleanup(self) -> None:
        """Stop flash timer."""
        if self._flash_timer:
            self._flash_timer.stop()
            self._flash_timer = None


class NotificationManager:
    """
    Manages stacked toast-style notifications for a modal screen.

    Features:
    - Multiple notifications stacked vertically from bottom-right
    - Each notification has independent auto-dismiss timer
    - Optional flash animation for high-priority notifications
    - First escape dismisses top notification, second closes modal
    """

    def __init__(
        self,
        screen: ModalScreen,
        container_id: str = "notification-container",
        dismiss_seconds: float = 15.0,
        flash_interval: float = 0.3,
        flash_cycles: int = 3,
    ):
        """
        Initialize the notification manager.

        Args:
            screen: The ModalScreen that owns this notification manager
            container_id: Widget ID for the notification container
            dismiss_seconds: Seconds before auto-dismissing each notification
            flash_interval: Seconds between flash state changes
            flash_cycles: Number of bright-dim cycles for flash animation
        """
        self.screen = screen
        self.container_id = container_id
        self.dismiss_seconds = dismiss_seconds
        self.flash_interval = flash_interval
        self.flash_cycles = flash_cycles

        # Track active notifications with their dismiss timers
        self._notifications: Dict[str, Tuple[Notification, Timer]] = {}
        self._next_id = 0

    def cleanup(self) -> None:
        """Stop all timers and remove all notifications. Call this in on_unmount()."""
        for notification, timer in self._notifications.values():
            timer.stop()
            notification.cleanup()
        self._notifications.clear()

    def show(
        self,
        text_bright: str,
        text_dim: Optional[str] = None,
        flash: bool = False,
    ) -> None:
        """
        Show a new notification, stacked with existing ones.

        Args:
            text_bright: Rich markup text for normal/bright state
            text_dim: Rich markup text for dim state (optional, for flash animation)
            flash: Whether to enable flash animation
        """
        text_dim = text_dim or f"[dim]{text_bright}[/dim]"

        try:
            container = self.screen.query_one(f"#{self.container_id}", VerticalScroll)

            # Generate unique ID for this notification
            notification_id = f"notification-{self._next_id}"
            self._next_id += 1

            # Create notification widget
            notification = Notification(
                notification_id,
                text_bright,
                text_dim,
                flash,
                self.flash_interval,
                self.flash_cycles,
            )

            # Mount notification to container
            container.mount(notification)

            # Set auto-dismiss timer for this notification
            dismiss_timer = self.screen.set_timer(
                self.dismiss_seconds,
                lambda nid=notification_id: self._dismiss_notification(nid),
            )

            # Track notification and timer
            self._notifications[notification_id] = (notification, dismiss_timer)

        except Exception as e:
            # Log error for debugging
            try:
                self.screen.notify(f"Notification error: {e}", severity="error")
            except:
                pass

    def _dismiss_notification(self, notification_id: str) -> None:
        """Dismiss a specific notification by ID."""
        if notification_id not in self._notifications:
            return

        notification, timer = self._notifications.pop(notification_id)
        timer.stop()
        notification.cleanup()

        try:
            notification.remove()
        except Exception:
            pass

    def dismiss(self) -> None:
        """Dismiss the topmost (most recent) notification."""
        if not self._notifications:
            return

        # Get the most recent notification (highest ID)
        latest_id = max(self._notifications.keys(), key=lambda x: int(x.split("-")[1]))
        self._dismiss_notification(latest_id)

    def is_visible(self) -> bool:
        """Check if any notifications are currently visible."""
        return len(self._notifications) > 0
