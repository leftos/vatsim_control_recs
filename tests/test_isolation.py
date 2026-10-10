"""The test session never resolves the real user data directory."""

import logging
import os
from pathlib import Path

import common.paths
from common import logger
from conftest import REAL_DATA_DIR_VAR, SESSION_DIR_VAR


def _session_dir() -> Path:
    return Path(os.environ[SESSION_DIR_VAR])


def _assert_isolated(path: str | Path) -> None:
    resolved = Path(path).resolve()
    assert resolved.is_relative_to(_session_dir()), resolved
    assert not resolved.is_relative_to(Path(os.environ[REAL_DATA_DIR_VAR])), resolved


def test_user_data_dir_is_the_session_dir() -> None:
    _assert_isolated(common.paths.get_user_data_dir())


def test_logger_writes_under_the_session_dir() -> None:
    _assert_isolated(logger.get_log_file_path())
    assert logger._LOGS_DIR is not None
    _assert_isolated(logger._LOGS_DIR)

    file_handlers = [
        handler
        for handler in logger._logger.handlers
        if isinstance(handler, logging.FileHandler)
    ]
    for handler in file_handlers:
        _assert_isolated(handler.baseFilename)
