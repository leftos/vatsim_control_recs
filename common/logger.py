"""
Centralized logging module for VATSIM Control Recommendations.

This module provides logging to a debug file for tracking issues.
It is designed to be imported by both backend and ui modules without
causing circular imports.

File logging starts only when an entry point calls setup_file_logging();
importing a module never creates a log file or prunes an existing one. Until
then records are kept in an in-memory buffer holding the most recent 1000, and
setup_file_logging() writes them out ahead of the records that follow.
"""

import collections
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Records the pre-setup buffer keeps before dropping the oldest
BUFFER_CAPACITY = 1000

# Lazy-initialized paths (set on first use)
_LOGS_DIR: str | None = None
_LOG_FILE: str | None = None
_initialized = False

_logger = logging.getLogger("vatsim_debug")
_logger.setLevel(logging.DEBUG)
_logger.addHandler(logging.NullHandler())


class _BufferHandler(logging.Handler):
    """Hold the most recent records until file logging starts."""

    def __init__(self, capacity: int) -> None:
        super().__init__(logging.DEBUG)
        self.capacity = capacity
        self.records: collections.deque[logging.LogRecord] = collections.deque(
            maxlen=capacity
        )
        self.dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        if len(self.records) == self.capacity:
            self.dropped += 1
        # Freeze the record: it may outlive the objects its args and traceback
        # point at, and the file formatter prints exc_text on its own.
        record.msg = record.getMessage()
        record.args = None
        if record.exc_info:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
        self.records.append(record)


_buffer_handler = _BufferHandler(BUFFER_CAPACITY)
_logger.addHandler(_buffer_handler)


def _get_logs_dir() -> str:
    """Get the logs directory path (lazy import to avoid circular imports)."""
    global _LOGS_DIR
    if _LOGS_DIR is None:
        from common.paths import get_user_logs_dir

        _LOGS_DIR = str(get_user_logs_dir())
    return _LOGS_DIR


def _get_log_file() -> str:
    """Get the current log file path."""
    global _LOG_FILE
    if _LOG_FILE is None:
        # Local date, so the log file name matches the operator's calendar day
        _LOG_FILE = os.path.join(
            _get_logs_dir(),
            f"debug_{datetime.now().astimezone().strftime('%Y%m%d')}.log",
        )
    return _LOG_FILE


def setup_file_logging() -> None:
    """Start writing records to the dated debug log file.

    Creates the logs directory, prunes log files past the retention window and
    attaches the file handler, then writes out the records buffered since
    import and clears the buffer. A second call is a no-op.

    When the logs directory or file cannot be created the failure is reported
    on stderr naming the path and the error, and logging stays in memory only.
    """
    global _initialized

    if _initialized:
        return

    try:
        logs_dir = _get_logs_dir()
        os.makedirs(logs_dir, exist_ok=True)

        # Clean up old logs
        cleanup_old_logs()

        # Create file handler
        log_file = _get_log_file()
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)

        # Create formatter
        formatter = logging.Formatter(
            "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(message)s",
            datefmt="%H:%M:%S",
        )
        file_handler.setFormatter(formatter)
    except (OSError, PermissionError) as exc:
        # Can't create log directory or file - continue without file logging
        _initialized = True  # Mark as initialized to avoid repeated attempts
        print(
            f"vatsim_debug: cannot write log file {_get_log_file()}: {exc}",
            file=sys.stderr,
        )
        return

    # Snapshot under the handler's lock, so a record logged from another thread
    # cannot slip into the buffer between the snapshot and the clear.
    _buffer_handler.acquire()
    try:
        _logger.removeHandler(_buffer_handler)
        records = list(_buffer_handler.records)
        dropped = _buffer_handler.dropped
        _buffer_handler.records.clear()
    finally:
        _buffer_handler.release()

    _logger.addHandler(file_handler)
    for record in records:
        file_handler.handle(record)
    if dropped:
        _logger.warning(
            f"{dropped} log records from before file logging"
            f" started were dropped (buffer cap {BUFFER_CAPACITY})"
        )

    _initialized = True


def cleanup_old_logs(days_to_keep: int = 10) -> None:
    """Remove log files older than the specified number of days."""
    try:
        logs_dir = _get_logs_dir()
        if not os.path.exists(logs_dir):
            return

        # UTC on both sides so the comparison against st_mtime is well defined
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_to_keep)
        logs_path = Path(logs_dir)

        for log_file in logs_path.glob("debug_*.log"):
            try:
                # Get file modification time
                file_mtime = datetime.fromtimestamp(
                    log_file.stat().st_mtime, tz=timezone.utc
                )
                if file_mtime < cutoff_date:
                    log_file.unlink()
            except OSError:
                pass  # Silently skip files we can't delete
    except OSError:
        pass  # Silently handle errors during cleanup


def debug(message: str) -> None:
    """Log a debug message."""
    _logger.debug(message)


def info(message: str) -> None:
    """Log an info message."""
    _logger.info(message)


def warning(message: str) -> None:
    """Log a warning message."""
    _logger.warning(message)


def error(message: str, exc_info: bool = False) -> None:
    """Log an error message.

    Args:
        message: The error message to log.
        exc_info: If True, include exception traceback information.
    """
    _logger.error(message, exc_info=exc_info)


def get_log_file_path() -> str:
    """Get the path to the current log file."""
    return _get_log_file()


# Expose LOGS_DIR and LOG_FILE as module-level proxy objects that are lazily evaluated
class _PathProxy:
    """Proxy class to provide lazy path access."""

    def __init__(self, getter):
        self._getter = getter

    def __str__(self) -> str:
        return self._getter()

    def __repr__(self) -> str:
        return self._getter()

    def __fspath__(self) -> str:
        return self._getter()


LOGS_DIR = _PathProxy(_get_logs_dir)
LOG_FILE = _PathProxy(_get_log_file)
