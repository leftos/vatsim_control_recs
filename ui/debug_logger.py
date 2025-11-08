"""
Debug Logger Module
Provides centralized logging to a debug file for tracking issues
"""

import logging
import os
from datetime import datetime

# Create logs directory if it doesn't exist
LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)

# Create debug log file with timestamp
LOG_FILE = os.path.join(LOGS_DIR, f'debug_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')

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