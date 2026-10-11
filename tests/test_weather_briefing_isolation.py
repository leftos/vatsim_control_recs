"""A briefing that raises for one grouping must not stop the daemon's run.

The daemon regenerates every briefing on a timer, from controller-typed text. One
grouping whose briefing raises used to take the whole run down with it, leaving
that run's index and tiles unwritten. It is now logged with its traceback and
skipped, and the run goes on to the remaining groupings, the ARTCC briefings, the
index and the tiles.
"""

import json
import logging
from pathlib import Path

import pytest

from scripts.weather_daemon import generator, index_generator
from scripts.weather_daemon.config import DaemonConfig

FLAKY_GROUPING = "GRP_FLAKY"
GOOD_GROUPING = "GRP_GOOD"
ARTCC = "ZOA"


class BriefingFailedError(RuntimeError):
    """Stands in for a briefing that raises for one grouping."""


def stub_generate_html(self) -> str:
    """A briefing that raises for one grouping and writes a marker for the rest."""
    if self.grouping_name == FLAKY_GROUPING:
        raise BriefingFailedError("briefing generation failed")
    return f"<html>{self.grouping_name}</html>"


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DaemonConfig:
    """A cached-weather run over two groupings of one ARTCC, with no network."""
    monkeypatch.setattr(
        generator,
        "load_unified_airport_data",
        lambda *paths: {"KSFO": {"latitude": 37.62, "longitude": -122.38}},
    )
    monkeypatch.setattr(generator, "AirportDisambiguator", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        generator,
        "load_all_groupings",
        lambda *args: {
            FLAKY_GROUPING: ["KSFO", "KOAK"],
            GOOD_GROUPING: ["KSJC", "KSMF"],
        },
    )
    monkeypatch.setattr(generator, "get_artcc_boundaries", lambda cache_dir: {})
    monkeypatch.setattr(index_generator, "get_artcc_boundaries", lambda cache_dir: {})
    monkeypatch.setattr(
        index_generator, "get_all_grouping_boundaries", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr(
        generator.WeatherBriefingGenerator, "generate_html", stub_generate_html
    )

    preset_dir = tmp_path / "preset_groupings"
    preset_dir.mkdir()
    (preset_dir / f"{ARTCC}.json").write_text(
        json.dumps(
            {
                FLAKY_GROUPING: {"airports": ["KSFO", "KOAK"]},
                GOOD_GROUPING: {"airports": ["KSJC", "KSMF"]},
            }
        ),
        encoding="utf-8",
        newline="\n",
    )

    weather_cache_dir = tmp_path / "weather"
    generator._save_weather_cache(
        weather_cache_dir, {"KSFO": "KSFO 101256Z 28010KT 10SM CLR 18/10 A3001"}, {}, {}
    )
    return DaemonConfig(
        output_dir=tmp_path / "out",
        preset_groupings_dir=preset_dir,
        artcc_cache_dir=tmp_path / "cache",
        weather_cache_dir=weather_cache_dir,
        fetch_fresh_weather=False,
        generate_briefings=True,
        generate_tiles=False,
        include_presets=True,
        include_custom=False,
    )


def test_a_failing_briefing_does_not_stop_the_run(
    config: DaemonConfig, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="weather_daemon")

    generator.generate(config)

    assert (config.output_dir / ARTCC / f"{GOOD_GROUPING}.html").is_file()
    assert (config.output_dir / ARTCC / "_all.html").is_file()
    assert (config.output_dir / "index.html").is_file()
    assert not (config.output_dir / ARTCC / f"{FLAKY_GROUPING}.html").exists()

    failures = [
        record
        for record in caplog.records
        if record.name == "weather_daemon" and record.levelno >= logging.ERROR
    ]
    assert failures, "the failed briefing must be logged"
    assert any(FLAKY_GROUPING in record.getMessage() for record in failures)
    assert any(ARTCC in record.getMessage() for record in failures)
    assert any(record.exc_info is not None for record in failures), (
        "the failing briefing's traceback must be logged"
    )
