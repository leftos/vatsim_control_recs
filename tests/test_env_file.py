"""``common.env_file`` reads single values from a dotenv file.

The reader serves both the weather daemon's CARTO key and statsim's API key;
it parses one named value and never writes to the process environment.
"""

import importlib
import logging
import os
from pathlib import Path

import pytest

import common.paths
from backend.data import statsim_api
from common.env_file import read_env_value

NAME = "SOME_KEY"
STATSIM_KEY_ENV = "STATSIM_API_KEY"


@pytest.mark.parametrize(
    ("line", "value"),
    [
        ("SOME_KEY=abc123  # localhost-locked", "abc123"),
        ("SOME_KEY=abc123\t# tab before the comment", "abc123"),
        ("SOME_KEY=abc#123", "abc#123"),
        ('SOME_KEY="abc #123"  # quoted', "abc #123"),
        ("SOME_KEY='abc #123'", "abc #123"),
    ],
)
def test_inline_comment_ends_an_unquoted_value_only(
    tmp_path: Path, line: str, value: str
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(line + "\n", encoding="utf-8")
    assert read_env_value(env_path, NAME) == value


def test_unreadable_env_file_logs_one_warning_naming_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    env_path = tmp_path / ".env"
    env_path.mkdir()
    with caplog.at_level(logging.WARNING, logger="vatsim_debug"):
        value = read_env_value(env_path, NAME)
    assert value is None
    warnings = [r for r in caplog.records if r.name == "vatsim_debug"]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    assert str(env_path) in warnings[0].getMessage()


def test_statsim_key_from_environment_wins_even_when_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(STATSIM_KEY_ENV, "")
    env_path = tmp_path / ".env"
    env_path.write_text(f"{STATSIM_KEY_ENV}=from_the_file\n", encoding="utf-8")
    assert statsim_api._resolve_api_key(env_path) == ""


def test_statsim_key_read_from_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(STATSIM_KEY_ENV, raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(f"{STATSIM_KEY_ENV}=from_the_file\n", encoding="utf-8")
    assert statsim_api._resolve_api_key(env_path) == "from_the_file"


def test_statsim_key_missing_everywhere_is_blank(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(STATSIM_KEY_ENV, raising=False)
    assert statsim_api._resolve_api_key(tmp_path / ".env") == ""


def test_importing_statsim_sets_no_environment_variables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The reload re-imports get_project_root, so patch it where it is defined.
    monkeypatch.setattr(common.paths, "get_project_root", lambda: tmp_path)
    (tmp_path / ".env").write_text("OTHER_KEY=x\n", encoding="utf-8")
    try:
        importlib.reload(statsim_api)
        assert "OTHER_KEY" not in os.environ
    finally:
        # Undo the patch and reload once more, so the module keeps the real root.
        monkeypatch.undo()
        importlib.reload(statsim_api)
    assert statsim_api.get_project_root is common.paths.get_project_root
