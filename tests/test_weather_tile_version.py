"""The index page versions its weather tiles by the last completed tile set.

The index stage runs before the tile stage, so the page names the version that
the tile stage recorded in ``tiles/version.txt`` after its last complete run;
tile files are replaced atomically so a reader never sees a half-written PNG.
"""

import logging
from pathlib import Path

import pytest

from scripts.weather_daemon import index_generator, tile_generator
from scripts.weather_daemon.config import DaemonConfig

RUN_START = 1700000000
LAST_COMPLETED = 1690000000


def render_index(output_dir: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Generate the index page with no network and return its HTML."""
    monkeypatch.setattr(index_generator, "get_artcc_boundaries", lambda cache_dir: {})
    monkeypatch.setattr(
        index_generator, "get_all_grouping_boundaries", lambda *args, **kwargs: {}
    )
    config = DaemonConfig(output_dir=output_dir, artcc_cache_dir=output_dir / "cache")
    index_path = index_generator.generate_index_page(
        config, {}, None, fallback_tile_version=RUN_START
    )
    assert index_path is not None
    return index_path.read_text(encoding="utf-8")


def test_index_uses_the_version_of_the_last_completed_tile_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "tiles").mkdir()
    (tmp_path / "tiles" / "version.txt").write_text(f"{LAST_COMPLETED}\n")
    html = render_index(tmp_path, monkeypatch)
    assert f"tiles/{{z}}/{{x}}/{{y}}.png?v={LAST_COMPLETED}'" in html


@pytest.mark.parametrize(
    "version_text", [None, "not a number"], ids=["missing", "garbled"]
)
def test_index_falls_back_to_the_run_start_without_a_tile_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    version_text: str | None,
) -> None:
    if version_text is not None:
        (tmp_path / "tiles").mkdir()
        (tmp_path / "tiles" / "version.txt").write_text(version_text)
    with caplog.at_level(logging.INFO, logger="weather_daemon"):
        html = render_index(tmp_path, monkeypatch)
    assert f"tiles/{{z}}/{{x}}/{{y}}.png?v={RUN_START}'" in html
    assert any(
        r.levelno == logging.INFO and "version.txt" in r.getMessage()
        for r in caplog.records
    )


def test_tile_write_that_fails_before_replace_keeps_the_old_tile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tile = tmp_path / "123.png"
    tile.write_bytes(b"old tile")

    def killed_before_replace(src, dst):
        raise OSError("killed before replace")

    monkeypatch.setattr(tile_generator.os, "replace", killed_before_replace)
    with pytest.raises(OSError, match="killed before replace"):
        tile_generator.write_file_atomically(tile, b"new tile")
    assert tile.read_bytes() == b"old tile"
    assert [p.name for p in tmp_path.iterdir()] == ["123.png"]


def test_tile_write_replaces_the_file_and_leaves_no_temp_file(tmp_path: Path) -> None:
    tile = tmp_path / "123.png"
    tile.write_bytes(b"old tile")
    tile_generator.write_file_atomically(tile, b"new tile")
    assert tile.read_bytes() == b"new tile"
    assert [p.name for p in tmp_path.iterdir()] == ["123.png"]
