"""The index stage runs before the tile stage.

The tile stage is the slow one; when systemd kills a run inside it, the index
page must already have been written so the site does not freeze on an old one.
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
