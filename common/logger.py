"""
Centralized logging module for VATSIM Control Recommendations.

This module provides logging to a debug file for tracking issues.
It is designed to be imported by both backend and ui modules without
causing circular imports.
"""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

# Create logs directory if it doesn't exist
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(_SCRIPT_DIR, 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)


def cleanup_old_logs(days_to_keep: int = 10) -> None:
    """Remove log files older than the specified number of days."""
    try:
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        logs_path = Path(LOGS_DIR)

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


# Clean up old logs on module initialization
cleanup_old_logs()

# Create debug log file with date (one file per day)
LOG_FILE = os.path.join(LOGS_DIR, f'debug_{datetime.now().strftime("%Y%m%d")}.log')

# Configure logger
_logger = logging.getLogger('vatsim_debug')
_logger.setLevel(logging.DEBUG)

# Only add handler if not already added (prevents duplicate handlers on reimport)
if not _logger.handlers:
    # Create file handler
    _file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    _file_handler.setLevel(logging.DEBUG)

    # Create formatter
    _formatter = logging.Formatter(
        '%(asctime)s.%(msecs)03d | %(levelname)-8s | %(message)s',
        datefmt='%H:%M:%S'
    )
    _file_handler.setFormatter(_formatter)

    # Add handler to logger
    _logger.addHandler(_file_handler)


def debug(message: str) -> None:
    """Log a debug message."""
    _logger.debug(message)


def info(message: str) -> None:
    """Log an info message."""
    _logger.info(message)


def warning(message: str) -> None:
    """Log a warning message."""
    _logger.warning(message)


def error(message: str) -> None:
    """Log an error message."""
    _logger.error(message)


def get_log_file_path() -> str:
    """Get the path to the current log file."""
    return LOG_FILE
