"""Reads single values from a dotenv file without touching the environment.

The weather daemon's CARTO key and statsim's API key both come from the
repo-root ``.env`` through :func:`read_env_value`.
"""

import re
from pathlib import Path

from common import logger as log

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
        log.warning(f"Could not read {env_path}: {e}")
        return None
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(name)}\s*=\s*(.*?)\s*$")
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            return _parse_value(match.group(1))
    return None
