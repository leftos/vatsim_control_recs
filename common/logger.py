"""
Centralized logging module for VATSIM Control Recommendations.

This module provides logging to a debug file for tracking issues.
It is designed to be imported by both backend and ui modules without
causing circular imports.

Logging is lazily initialized to avoid permission errors when imported
by processes that don't need file logging (e.g., the weather daemon).
"""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Lazy-initialized paths (set on first use)
_LOGS_DIR: Optional[str] = None
_LOG_FILE: Optional[str] = None
_initialized = False

# Configure logger (starts with NullHandler until file logging is set up)
_logger = logging.getLogger('vatsim_debug')
_logger.setLevel(logging.DEBUG)
_logger.addHandler(logging.NullHandler())


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
        _LOG_FILE = os.path.join(_get_logs_dir(), f'debug_{datetime.now().strftime("%Y%m%d")}.log')
    return _LOG_FILE


def _init_file_logging() -> bool:
    """Initialize file logging if not already done.

    Returns:
        True if file logging was successfully initialized, False otherwise.
    """
    global _initialized

    if _initialized:
        return True

    # Check if we already have a file handler
    for handler in _logger.handlers:
        if isinstance(handler, logging.FileHandler):
            _initialized = True
            return True

    try:
        logs_dir = _get_logs_dir()
        os.makedirs(logs_dir, exist_ok=True)

        # Clean up old logs
        cleanup_old_logs()

        # Create file handler
        log_file = _get_log_file()
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)

        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s.%(msecs)03d | %(levelname)-8s | %(message)s',
            datefmt='%H:%M:%S'
        )
        file_handler.setFormatter(formatter)

        # Add handler to logger
        _logger.addHandler(file_handler)
        _initialized = True
        return True

    except (OSError, PermissionError):
        # Can't create log directory or file - continue without file logging
        _initialized = True  # Mark as initialized to avoid repeated attempts
        return False


def cleanup_old_logs(days_to_keep: int = 10) -> None:
    """Remove log files older than the specified number of days."""
    try:
        logs_dir = _get_logs_dir()
        if not os.path.exists(logs_dir):
            return

        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        logs_path = Path(logs_dir)

        for log_file in logs_path.glob('debug_*.log'):
            try:
                # Get file modification time
                file_mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                if file_mtime < cutoff_date:
                    log_file.unlink()
            except (OSError, IOError):
                pass  # Silently skip files we can't delete
    except (OSError, IOError):
        pass  # Silently handle errors during cleanup


def debug(message: str) -> None:
    """Log a debug message."""
    _init_file_logging()
    _logger.debug(message)


def info(message: str) -> None:
    """Log an info message."""
    _init_file_logging()
    _logger.info(message)


def warning(message: str) -> None:
    """Log a warning message."""
    _init_file_logging()
    _logger.warning(message)


def error(message: str, exc_info: bool = False) -> None:
    """Log an error message.

    Args:
        message: The error message to log.
        exc_info: If True, include exception traceback information.
    """
    _init_file_logging()
    _logger.error(message, exc_info=exc_info)


def get_log_file_path() -> str:
    """Get the path to the current log file."""
    return _get_log_file()


# For backwards compatibility - these are now computed lazily
@property
def LOGS_DIR() -> str:
    """Get the logs directory (for backwards compatibility)."""
    return _get_logs_dir()


# Expose LOGS_DIR and LOG_FILE as module-level variables that are lazily evaluated
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
