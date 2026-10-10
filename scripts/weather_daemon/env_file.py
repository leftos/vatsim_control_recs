"""Reads the CARTO basemap key from the repo-root ``.env`` for the daemon CLI.

The index generator reads the key from the environment. The CLI calls
:func:`load_carto_basemap_key` before generating, so every entry point (the
systemd timer, QuickDeploy, the Regen scripts, LocalTest) reads the repo-root
``.env`` when the environment lacks the key.
"""

import os
from pathlib import Path

from common.env_file import read_env_value

CARTO_KEY_ENV = "CARTO_BASEMAP_KEY"


def load_carto_basemap_key(project_root: Path) -> None:
    """Set ``CARTO_BASEMAP_KEY`` from ``<project_root>/.env`` unless the environment has it."""
    if os.environ.get(CARTO_KEY_ENV):
        return
    value = read_env_value(project_root / ".env", CARTO_KEY_ENV)
    if value:
        os.environ[CARTO_KEY_ENV] = value
