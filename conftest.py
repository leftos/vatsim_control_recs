"""Keep the test session out of the real user data directory.

This runs when pytest loads the root conftest, before tests/conftest.py imports
the app. Writable state a test touches - caches, favorites and any log file a
test starts through setup_file_logging() - must not reach the real user data
directory, so every variable common/paths.py derives that directory from points
at one session temp dir before any project module is imported.
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

SESSION_DIR_VAR = "VCR_TEST_SESSION_DIR"
REAL_DATA_DIR_VAR = "VCR_TEST_REAL_DATA_DIR"


def _isolate_user_dirs() -> None:
    """Point LOCALAPPDATA, XDG_DATA_HOME and HOME at a fresh session temp dir.

    The real user data directory is recorded first so a test can check that
    nothing resolved to it.
    """
    from common.paths import get_user_data_dir

    os.environ[REAL_DATA_DIR_VAR] = str(get_user_data_dir())

    session_dir = Path(tempfile.mkdtemp(prefix="vcr-tests-")).resolve()
    os.environ[SESSION_DIR_VAR] = str(session_dir)
    for variable in ("LOCALAPPDATA", "XDG_DATA_HOME", "HOME"):
        os.environ[variable] = str(session_dir)

    # statsim_api reads only STATSIM_API_KEY from the repo's .env, and only
    # when the environment lacks it, so the blank value keeps .env out
    os.environ.setdefault("STATSIM_API_KEY", "")


_isolate_user_dirs()


def pytest_unconfigure(config: pytest.Config) -> None:
    # The logger's FileHandler stays open until interpreter exit, so on Windows
    # its log file cannot be removed yet; the rest of the session dir is.
    shutil.rmtree(os.environ[SESSION_DIR_VAR], ignore_errors=True)
