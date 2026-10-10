"""Reads the CARTO basemap key from the repo-root ``.env`` for the daemon CLI.

The index generator reads the key from the environment. The CLI calls
:func:`load_carto_basemap_key` before generating, so every entry point (the
systemd timer, QuickDeploy, the Regen scripts, LocalTest) reads the repo-root
``.env`` when the environment lacks the key.
"""

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("weather_daemon")

CARTO_KEY_ENV = "CARTO_BASEMAP_KEY"

_QUOTES = ("'", '"')
_INLINE_COMMENT = re.compile(r"\s+#")


def _parse_value(raw: str) -> str:
    """A quoted value's text between its quotes, else the text before a `` #`` comment."""
    if raw[:1] in _QUOTES:
        closing = raw.find(raw[0], 1)
        if closing > 0:
            return raw[1:closing]
    return _INLINE_COMMENT.split(raw, maxsplit=1)[0]


def read_env_value(env_path: Path, name: str) -> str | None:
    """Return the first ``[export ]NAME = value`` in a dotenv file, unquoted.

    In an unquoted value, whitespace then ``#`` starts a comment; inside quotes
    ``#`` is literal.

    Args:
        env_path: The dotenv file; a missing one yields None.
        name: The variable to read; no other line is interpreted.

    Returns:
        The value, or None when the file is missing, unreadable or has no such line.
        An unreadable file logs one warning naming its path.
    """
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Could not read {env_path}: {e}")
        return None
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(name)}\s*=\s*(.*?)\s*$")
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            return _parse_value(match.group(1))
    return None


def load_carto_basemap_key(project_root: Path) -> None:
    """Set ``CARTO_BASEMAP_KEY`` from ``<project_root>/.env`` unless the environment has it."""
    if os.environ.get(CARTO_KEY_ENV):
        return
    value = read_env_value(project_root / ".env", CARTO_KEY_ENV)
    if value:
        os.environ[CARTO_KEY_ENV] = value
