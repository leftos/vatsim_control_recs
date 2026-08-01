"""
Debug Logger Module - Re-exports from common.logger for backwards compatibility.

The actual logging implementation is in common/logger.py to avoid
circular imports between backend and ui packages.
"""

# Re-export everything from common.logger for backwards compatibility
from common.logger import (
    LOG_FILE,
    LOGS_DIR,
    cleanup_old_logs,
    debug,
    error,
    get_log_file_path,
    info,
    warning,
)

__all__ = [
    "LOGS_DIR",
    "LOG_FILE",
    "cleanup_old_logs",
    "debug",
    "error",
    "get_log_file_path",
    "info",
    "warning",
]
