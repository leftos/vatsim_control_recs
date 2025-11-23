"""
Debug Logger Module
Provides centralized logging to a debug file for tracking issues
"""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

# Create logs directory if it doesn't exist
LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)

# Clean up old log files (older than 10 days)
def cleanup_old_logs(days_to_keep: int = 10):
    """Remove log files older than the specified number of days"""
    try:
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        logs_path = Path(LOGS_DIR)
        
        for log_file in logs_path.glob('debug_*.log'):
            try:
                # Get file modification time
                file_mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                if file_mtime < cutoff_date:
                    log_file.unlink()
                    print(f"Deleted old log file: {log_file.name}")
            except Exception as e:
                print(f"Error deleting log file {log_file.name}: {e}")
    except Exception as e:
        print(f"Error during log cleanup: {e}")

# Clean up old logs on module initialization
cleanup_old_logs()

# Create debug log file with date (one file per day)
LOG_FILE = os.path.join(LOGS_DIR, f'debug_{datetime.now().strftime("%Y%m%d")}.log')

# Configure logger
logger = logging.getLogger('vatsim_debug')
logger.setLevel(logging.DEBUG)

# Create file handler
file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)

# Create formatter
formatter = logging.Formatter('%(asctime)s.%(msecs)03d | %(levelname)-8s | %(message)s', 
                              datefmt='%H:%M:%S')
file_handler.setFormatter(formatter)

# Add handler to logger
logger.addHandler(file_handler)

def debug(message: str):
    """Log a debug message"""
    logger.debug(message)

def info(message: str):
    """Log an info message"""
    logger.info(message)

def warning(message: str):
    """Log a warning message"""
    logger.warning(message)

def error(message: str):
    """Log an error message"""
    logger.error(message)

def get_log_file_path() -> str:
    """Get the path to the current log file"""
    return LOG_FILE