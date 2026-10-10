"""The index page's CARTO basemap carries the key from ``CARTO_BASEMAP_KEY``.

CARTO serves raster tiles only with a key. The key never enters the repo: the
index generator reads it from the environment, and the daemon CLI fills the
environment from the untracked repo-root ``.env`` when it is not already set.
"""

import logging
from pathlib import Path

import pytest

from scripts.weather_daemon import index_generator
from scripts.weather_daemon.config import DaemonConfig
from scripts.weather_daemon.env_file import (
    CARTO_KEY_ENV,
    load_carto_basemap_key,
    read_env_value,
)

KEY = "abc_DEF-123"
MISSING_KEY_WARNING = (
    "CARTO_BASEMAP_KEY is not set; the map's basemap tiles will be refused"
)


@pytest.fixture
def no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start with the variable unset, and restore the outer environment after."""
    monkeypatch.setenv(CARTO_KEY_ENV, "placeholder")
    monkeypatch.delenv(CARTO_KEY_ENV)


def render_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Generate the index page with no network and return its HTML."""
    monkeypatch.setattr(index_generator, "get_artcc_boundaries", lambda cache_dir: {})
    monkeypatch.setattr(
        index_generator, "get_all_grouping_boundaries", lambda *args, **kwargs: {}
    )
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    config = DaemonConfig(output_dir=output_dir, artcc_cache_dir=tmp_path / "cache")
    index_path = index_generator.generate_index_page(
        config, {}, None, fallback_tile_version=1700000000
    )
    assert index_path is not None
    return index_path.read_text(encoding="utf-8")


def warnings_and_errors(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    return [
        (r.levelno, r.getMessage())
        for r in caplog.records
        if r.name == "weather_daemon" and r.levelno >= logging.WARNING
    ]


def test_key_from_the_environment_goes_into_the_basemap_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(CARTO_KEY_ENV, KEY)
    with caplog.at_level(logging.WARNING, logger="weather_daemon"):
        html = render_index(tmp_path, monkeypatch)
    assert f"rastertiles/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png?key={KEY}'" in html
    assert warnings_and_errors(caplog) == []


@pytest.mark.usefixtures("no_key")
def test_missing_key_renders_the_url_without_one_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="weather_daemon"):
        html = render_index(tmp_path, monkeypatch)
    assert "rastertiles/dark_all/{z}/{x}/{y}{r}.png'" in html
    assert "?key=" not in html
    assert warnings_and_errors(caplog) == [(logging.WARNING, MISSING_KEY_WARNING)]


@pytest.mark.parametrize("bad_key", ["abc'DEF", 'abc"DEF', "abc<DEF", "abc DEF"])
def test_key_with_unsafe_characters_is_omitted_with_an_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    bad_key: str,
) -> None:
    monkeypatch.setenv(CARTO_KEY_ENV, bad_key)
    with caplog.at_level(logging.WARNING, logger="weather_daemon"):
        html = render_index(tmp_path, monkeypatch)
    assert "rastertiles/dark_all/" in html
    assert "?key=" not in html
    assert bad_key not in html
    logged = warnings_and_errors(caplog)
    assert [level for level, _ in logged] == [logging.ERROR]
    assert bad_key not in logged[0][1]


@pytest.mark.usefixtures("no_key")
@pytest.mark.parametrize(
    "env_bytes",
    [
        b"\xef\xbb\xbfOTHER=1\r\nCARTO_BASEMAP_KEY=abc_DEF-123\r\n",
        b'export CARTO_BASEMAP_KEY = "abc_DEF-123"\n',
        b"CARTO_BASEMAP_KEY='abc_DEF-123'",
    ],
    ids=["bom-crlf", "export-double-quoted", "single-quoted"],
)
def test_key_from_the_project_env_file_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env_bytes: bytes
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".env").write_bytes(env_bytes)
    load_carto_basemap_key(project_root)
    html = render_index(tmp_path, monkeypatch)
    assert f"rastertiles/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png?key={KEY}'" in html


@pytest.mark.parametrize(
    ("line", "value"),
    [
        ("CARTO_BASEMAP_KEY=abc123  # localhost-locked", "abc123"),
        ("CARTO_BASEMAP_KEY=abc123\t# tab before the comment", "abc123"),
        ("CARTO_BASEMAP_KEY=abc#123", "abc#123"),
        ('CARTO_BASEMAP_KEY="abc #123"  # quoted', "abc #123"),
        ("CARTO_BASEMAP_KEY='abc #123'", "abc #123"),
    ],
)
def test_inline_comment_ends_an_unquoted_value_only(
    tmp_path: Path, line: str, value: str
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(line + "\n", encoding="utf-8")
    assert read_env_value(env_path, CARTO_KEY_ENV) == value


def test_key_already_in_the_environment_wins_over_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CARTO_KEY_ENV, "fromEnvironment1")
    (tmp_path / ".env").write_text(f"CARTO_BASEMAP_KEY={KEY}\n", encoding="utf-8")
    load_carto_basemap_key(tmp_path)
    html = render_index(tmp_path, monkeypatch)
    assert "?key=fromEnvironment1'" in html
    assert KEY not in html


@pytest.mark.usefixtures("no_key")
def test_no_env_file_leaves_the_key_unset_and_the_page_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="weather_daemon"):
        load_carto_basemap_key(tmp_path)
        html = render_index(tmp_path, monkeypatch)
    assert "?key=" not in html
    assert warnings_and_errors(caplog) == [(logging.WARNING, MISSING_KEY_WARNING)]


@pytest.mark.usefixtures("no_key")
def test_unreadable_env_file_logs_one_warning_naming_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    env_path = tmp_path / ".env"
    env_path.mkdir()
    with caplog.at_level(logging.WARNING, logger="weather_daemon"):
        load_carto_basemap_key(tmp_path)
    logged = warnings_and_errors(caplog)
    assert len(logged) == 1
    level, message = logged[0]
    assert level == logging.WARNING
    assert str(env_path) in message
