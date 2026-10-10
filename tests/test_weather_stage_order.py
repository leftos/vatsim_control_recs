"""The index stage runs before the tile stage.

The tile stage is the slow one; when systemd kills a run inside it, the index
page must already have been written so the site does not freeze on an old one.

It is also the only stage a run may skip: when the weather and the daemon's code
are unchanged since the last completed tile stage, a run that fetched fresh
weather leaves the tiles, ``version.txt`` and the site's ``?v=`` alone while
briefings and the index still regenerate.
"""

from pathlib import Path

import pytest

from scripts.weather_daemon import generator, index_generator
from scripts.weather_daemon.config import DaemonConfig


class TileStageKilledError(RuntimeError):
    """Stands in for the run being killed inside the tile stage."""


def kill_tile_stage(**kwargs):
    raise TileStageKilledError("tile stage killed")


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DaemonConfig:
    """A cached-weather run over one airport, with no network and no data files."""
    monkeypatch.setattr(
        generator,
        "load_unified_airport_data",
        lambda *paths: {"KSFO": {"latitude": 37.62, "longitude": -122.38}},
    )
    monkeypatch.setattr(generator, "AirportDisambiguator", lambda *args, **kwargs: None)
    monkeypatch.setattr(generator, "load_all_groupings", lambda *args: {})
    monkeypatch.setattr(generator, "get_artcc_boundaries", lambda cache_dir: {})
    monkeypatch.setattr(generator, "generate_weather_tiles", kill_tile_stage)
    monkeypatch.setattr(index_generator, "get_artcc_boundaries", lambda cache_dir: {})
    monkeypatch.setattr(
        index_generator, "get_all_grouping_boundaries", lambda *args, **kwargs: {}
    )
    weather_cache_dir = tmp_path / "weather"
    generator._save_weather_cache(
        weather_cache_dir, {"KSFO": "KSFO 101256Z 28010KT 10SM CLR 18/10 A3001"}, {}, {}
    )
    return DaemonConfig(
        output_dir=tmp_path / "out",
        artcc_cache_dir=tmp_path / "cache",
        weather_cache_dir=weather_cache_dir,
        fetch_fresh_weather=False,
        generate_briefings=False,
        include_presets=False,
        include_custom=False,
    )


def test_index_is_written_when_the_tile_stage_dies(config: DaemonConfig) -> None:
    with pytest.raises(TileStageKilledError):
        generator.generate(config)
    assert (config.output_dir / "index.html").is_file()


def successful_tile_stage(calls: list[dict]) -> object:
    """A tile stage that records its calls and creates the tiles directory it is given."""

    def stub(**kwargs):
        calls.append(kwargs)
        kwargs["output_dir"].mkdir(parents=True, exist_ok=True)
        return {4: 1}

    return stub


def test_completed_tile_stage_writes_tile_version(
    config: DaemonConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generator, "generate_weather_tiles", successful_tile_stage([]))
    generator.generate(config)
    version_path = config.output_dir / "tiles" / "version.txt"
    assert version_path.is_file()
    assert int(version_path.read_text(encoding="utf-8").strip()) > 0


def test_killed_tile_stage_leaves_tile_version_unchanged(config: DaemonConfig) -> None:
    tiles_dir = config.output_dir / "tiles"
    tiles_dir.mkdir(parents=True)
    version_path = tiles_dir / "version.txt"
    version_path.write_text("1690000000\n", encoding="utf-8")
    with pytest.raises(TileStageKilledError):
        generator.generate(config)
    assert version_path.read_text(encoding="utf-8") == "1690000000\n"


@pytest.fixture
def fresh_config(config: DaemonConfig) -> DaemonConfig:
    """The fixture config as a run that fetched fresh weather (its cache is fresh)."""
    config.fetch_fresh_weather = True
    return config


def test_unchanged_weather_and_code_skip_the_tile_stage(
    fresh_config: DaemonConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        generator, "generate_weather_tiles", successful_tile_stage(calls)
    )
    generator.generate(fresh_config)
    assert len(calls) == 1
    version_path = fresh_config.output_dir / "tiles" / "version.txt"
    version_before = version_path.read_text(encoding="utf-8")

    index_path = fresh_config.output_dir / "index.html"
    index_path.unlink()

    generator.generate(fresh_config)

    assert len(calls) == 1, "unchanged weather and code must skip the tile stage"
    assert version_path.read_text(encoding="utf-8") == version_before
    assert index_path.is_file(), "the index stage still runs on a skipped tile stage"


def test_changed_metar_runs_the_tile_stage(
    fresh_config: DaemonConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        generator, "generate_weather_tiles", successful_tile_stage(calls)
    )
    generator.generate(fresh_config)
    generator._save_weather_cache(
        fresh_config.weather_cache_dir,
        {"KSFO": "KSFO 101256Z 28018KT 3SM BR OVC006 16/14 A2985"},
        {},
        {},
    )
    generator.generate(fresh_config)
    assert len(calls) == 2


def test_changed_code_version_runs_the_tile_stage(
    fresh_config: DaemonConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        generator, "generate_weather_tiles", successful_tile_stage(calls)
    )
    code_version = ["0" * 16]
    monkeypatch.setattr(
        generator, "compute_code_version", lambda *args: code_version[0]
    )
    generator.generate(fresh_config)
    code_version[0] = "1" * 16
    generator.generate(fresh_config)
    assert len(calls) == 2


def test_killed_tile_stage_does_not_save_the_key(
    fresh_config: DaemonConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []

    def killing_stage(**kwargs):
        calls.append(kwargs)
        raise TileStageKilledError("tile stage killed")

    monkeypatch.setattr(generator, "generate_weather_tiles", killing_stage)
    with pytest.raises(TileStageKilledError):
        generator.generate(fresh_config)
    assert generator.load_weather_hash(fresh_config.weather_cache_dir) is None

    monkeypatch.setattr(
        generator, "generate_weather_tiles", successful_tile_stage(calls)
    )
    generator.generate(fresh_config)
    assert len(calls) == 2


def test_cached_weather_run_never_skips(
    config: DaemonConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        generator, "generate_weather_tiles", successful_tile_stage(calls)
    )
    generator.generate(config)
    generator.generate(config)
    assert len(calls) == 2


def test_force_runs_the_tile_stage(
    fresh_config: DaemonConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        generator, "generate_weather_tiles", successful_tile_stage(calls)
    )
    generator.generate(fresh_config)
    fresh_config.skip_if_unchanged = False
    generator.generate(fresh_config)
    assert len(calls) == 2


def test_code_version_follows_python_files_under_the_directory(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "service").mkdir()
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    (tmp_path / "nested" / "b.py").write_text("y = 2\n", encoding="utf-8", newline="\n")
    (tmp_path / "service" / "deploy.ps1").write_text(
        "Write-Host 1\n", encoding="utf-8", newline="\n"
    )
    version = generator.compute_code_version(tmp_path)
    assert version == generator.compute_code_version(tmp_path)
    (tmp_path / "service" / "deploy.ps1").write_text(
        "Write-Host 2\n", encoding="utf-8", newline="\n"
    )
    assert generator.compute_code_version(tmp_path) == version
    (tmp_path / "nested" / "b.py").write_text("y = 3\n", encoding="utf-8", newline="\n")
    assert generator.compute_code_version(tmp_path) != version
