"""Characterization of the weather daemon's briefing generator.

Pins today's behaviour of ``generate`` (the daemon run's orchestration) and of
``WeatherBriefingGenerator._build_airport_card``, ``generate_html`` and
``get_airport_weather_points``.

``generate`` runs against fakes for its boundaries only: the airport and grouping
loaders, the network fetchers, the index and tile stages, the code version and the
clock. Each scenario's snapshot holds the stage calls in order, what the index and
tile stages were handed (the ARTCC groupings map in its insertion order), the files
written, the log records, the printed lines and the generated-files map. The goldens
of the stage matrix are the sha256 of the snapshot; the named scenarios keep the
snapshot in full, with files and printed lines as sha256. The airport cards are
pinned as text, the briefing HTML as sha256 and the map weather points in full.

After an intentional change to what the generator writes, logs or hands its
stages, recapture the goldens at the end of this file from the repo root with
``uv run --group dev python tests/capture_weather_generator_goldens.py`` and review
the diff.

The clock reads ``FROZEN`` plus one ``_Clock.step`` (a microsecond) per read, so a
progress rate never divides by zero while every printed time stays the same.
"""

import contextlib
import datetime as datetime_module
import hashlib
import io
import itertools
import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from backend.briefing import taf_parsing
from scripts.weather_daemon import generator, index_generator
from scripts.weather_daemon.config import DaemonConfig

FROZEN = datetime(2026, 10, 10, 12, 30, tzinfo=UTC)
FRESH_TIMESTAMP = FROZEN.isoformat()
STALE_TIMESTAMP = (FROZEN - timedelta(hours=1)).isoformat()
CODE_VERSION = "c0dec0dec0dec0de"


class _Clock(datetime):
    reads = 0
    step = timedelta(microseconds=1)

    @classmethod
    def now(cls, tz=None):
        cls.reads += 1
        return FROZEN + cls.step * cls.reads


def freeze_clock(monkeypatch: pytest.MonkeyPatch, gen: ModuleType) -> None:
    """Point every clock the generator reads at ``_Clock``."""
    _Clock.reads = 0
    monkeypatch.setattr(gen, "datetime", _Clock)
    monkeypatch.setattr(datetime_module, "datetime", _Clock)
    monkeypatch.setattr(taf_parsing, "datetime", _Clock)


PRETTY_NAMES = {
    "KSFO": "San Francisco",
    "KOAK": "Oakland",
    "KSJC": "San Jose",
    "KLAX": "Los Angeles",
    "KBUR": "Burbank",
}


class FakeDisambiguator:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def get_full_name(self, icao: str) -> str:
        return f"{icao} Field"

    def get_pretty_name(self, icao: str) -> str | None:
        return PRETTY_NAMES.get(icao)


UNIFIED: dict[str, dict[str, Any]] = {
    "KSFO": {"latitude": 37.62, "longitude": -122.38, "artcc": "ZOA"},
    "KOAK": {"latitude": 37.72, "longitude": -122.22, "artcc": "ZOA"},
    "KSJC": {"latitude": 37.36, "longitude": -121.93, "artcc": "ZOA"},
    "KLAX": {"latitude": 33.94, "longitude": -118.41, "artcc": "ZLA"},
    "KBUR": {"latitude": 34.20, "longitude": -118.36, "artcc": "ZLA"},
    "KNOC": {"artcc": "ZLA"},
    "O69": {
        "latitude": 38.26,
        "longitude": -122.60,
        "artcc": "ZOA",
        "tower_type": "NON-ATCT",
    },
    "1O2": {
        "latitude": 39.00,
        "longitude": -122.90,
        "artcc": "ZOA",
        "tower_type": "ATCT",
    },
    "2O3": {"latitude": 39.50, "longitude": -121.60, "artcc": "ZOA", "far139": "I E"},
    "3O4": {"latitude": 40.10, "longitude": -122.20, "artcc": "ZOAX", "iata": "XYZ"},
    "4O5": {"latitude": 40.50, "longitude": -122.30},
}

TAF_KSFO = (
    "TAF KSFO 101120Z 1012/1118 28010KT P6SM FEW020 "
    "FM101800 30015G25KT 3SM -RA BKN008 "
    "TEMPO 1020/1024 1SM TSRA OVC004 "
    "FM110600 VRB03KT P6SM SKC"
)

METARS = {
    "KSFO": "KSFO 101156Z 28010KT 10SM FEW020 18/10 A3001",
    "KOAK": "KOAK 101156Z 00000KT 2SM BR OVC006 14/13 A2998",
    "KSJC": "KSJC 101153Z 31008G18KT 5SM HZ BKN025 20/10 A3000",
    "KLAX": "KLAX 092355Z 25005KT 1/2SM FG OVC002 15/15 A2995",
    "KBUR": "",
    "KNOC": "KNOC 101235Z 18005KT 10SM CLR 20/10 A3000",
    "O69": "O69 101155Z AUTO 27006KT 10SM CLR 19/08 A3002",
}
TAFS = {"KSFO": TAF_KSFO}

ATIS_TEXT = (
    "SFO ATIS INFO C 1156Z. 28010KT 10SM FEW020 18/10 A3001 (THREE ZERO ZERO ONE). "
    "ILS RWY 28L APCH IN USE, VISUAL APCH IN USE. LNDG RWY 28L, 28R. "
    "DEPG RWY 1L, 1R. NOTAMS... TWY C CLSD. ADVS YOU HAVE INFO C."
)
VATSIM_ATIS = {
    "KSFO": [
        {
            "atis_code": "C",
            "type": "combined",
            "source": "vatsim",
            "text_atis": ATIS_TEXT,
        }
    ]
}
RW_ATIS: dict[str, list[dict[str, Any]]] = {
    "KLAX": [
        {
            "atis_code": "D",
            "type": "arrival",
            "source": "rw",
            "text_atis": "LAX ARR INFO D 2353Z. ILS RWY 24R APCH IN USE. LNDG RWY 24R, 25L.",
        },
        {
            "atis_code": "E",
            "type": "departure",
            "source": "rw",
            "text_atis": "LAX DEP INFO E 2353Z. DEPG RWY 24L, 25R.",
        },
    ],
    "KOAK": [],
}

ALL_GROUPINGS: dict[str, list[str]] = {
    "BAY": ["KSFO", "KOAK", "KSJC"],
    "NORTH": ["O69", "1O2", "2O3"],
    "SOLO": ["KSFO"],
    "TOWER": ["KSJC"],
    "EMPTY": [],
    "SOCAL": ["KLAX", "KBUR", "KNOC"],
    "CUSTOM_MIX": ["KSFO", "SOCAL"],
    "CUSTOM_NONE": ["3O4", "4O5"],
    "CUSTOM_EMPTY": [],
    "CUSTOM_UNLIKELY": ["O69", "4O5"],
    "CUSTOM_NONE2": ["3O4"],
}

PRESET_FILES: dict[str, str] = {
    "ZOA": json.dumps(
        {
            "BAY": {"airports": ["KSFO", "KOAK", "KSJC"], "facility_id": "NCT"},
            "NORTH": ["O69", "1O2", "2O3"],
            "SOLO": {"airports": ["KSFO"]},
            "TOWER": {"airports": ["KSJC"], "facility_id": "SJC"},
            "WEIRD": "not a grouping",
            "EMPTY": {"airports": ["ZZZ1", "ZZZ2"]},
        }
    ),
    "ZLA": json.dumps({"SOCAL": {"airports": ["KLAX", "KBUR", "KNOC"]}}),
    "ZXX": "{not json",
    "ZOA+bay": json.dumps(
        {"BAY": {"airports": ["KSFO", "KOAK", "KSJC"], "facility_id": "NCT"}}
    ),
}

BOUNDARIES = {
    "ZOA": [[(36.0, -123.5), (38.5, -123.5), (38.5, -121.0), (36.0, -121.0)]],
    "ZLA": [[(33.0, -119.0), (35.0, -119.0), (35.0, -117.0)]],
    "ZXY": [[]],
}

BBOX_WEATHER: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "ZOA": (
        {
            "KSFO": "KSFO 101256Z 29012KT 10SM FEW015 17/11 A3000",
            "KOAK": METARS["KOAK"],
            "KSJC": METARS["KSJC"],
        },
        {"KSFO": TAF_KSFO},
    ),
    "ZLA": ({"KLAX": METARS["KLAX"], "KBUR": ""}, {}),
}

RATE_STATUS = {
    "ok": {"is_rate_limited": False, "backoff_seconds": 0.0, "error_count": 0},
    "limited": {"is_rate_limited": True, "backoff_seconds": 12.5, "error_count": 3},
    "recovered": {"is_rate_limited": False, "backoff_seconds": 0.0, "error_count": 2},
}

DEFAULTS: dict[str, Any] = {
    "stages": "weather,briefings,tiles,index",
    "force": False,
    "cache": "fresh",
    "stored_key": None,
    "presets": ("ZOA", "ZLA", "ZXX"),
    "custom": ("BAY", "CUSTOM_MIX", "CUSTOM_NONE", "CUSTOM_EMPTY"),
    "include_presets": True,
    "include_custom": True,
    "artcc_filter": None,
    "failing": (),
    "real_html": False,
    "bbox_fail": False,
    "boundaries": True,
    "rate": "ok",
    "vatsim": True,
    "index_path": True,
    "network_empty": False,
}


class _SortedGlobPath(type(Path())):
    """A directory whose glob lists in name order on every filesystem."""

    def glob(self, pattern: str, **kwargs: Any):
        return iter(sorted(super().glob(pattern, **kwargs)))


class _ListHandler(logging.Handler):
    def __init__(
        self,
        records: list[logging.LogRecord],
        on_record: Callable[[logging.LogRecord], None],
    ) -> None:
        super().__init__(logging.DEBUG)
        self.records = records
        self.on_record = on_record

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
        self.on_record(record)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _sha12(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _write_inputs(root: Path, opts: dict[str, Any]) -> DaemonConfig:
    """Write the preset files, the weather cache and the stored key; return the config."""
    presets = root / "in" / "presets"
    presets.mkdir(parents=True)
    for name in opts["presets"]:
        artcc = name.partition("+")[0]
        (presets / f"{artcc}.json").write_text(
            PRESET_FILES[name], encoding="utf-8", newline="\n"
        )
    weather_dir = root / "weather"
    weather_dir.mkdir()
    if opts["cache"] != "none":
        timestamp = FRESH_TIMESTAMP if opts["cache"] == "fresh" else STALE_TIMESTAMP
        cache = {"timestamp": timestamp, "metars": METARS, "tafs": TAFS}
        cache["atis"] = VATSIM_ATIS
        (weather_dir / "weather_cache.json").write_text(
            json.dumps(cache), encoding="utf-8", newline="\n"
        )
    if opts["stored_key"] is not None:
        weather_hash = generator.compute_weather_hash(METARS, TAFS)
        key = {"match": weather_hash, "other": "0" * 16}[opts["stored_key"]]
        (weather_dir / "weather_hash.txt").write_text(f"{key}:{CODE_VERSION}")
    stages = set(opts["stages"].split(","))
    return DaemonConfig(
        output_dir=root / "out",
        custom_groupings_path=root / "in" / "custom.json",
        preset_groupings_dir=_SortedGlobPath(presets),
        data_dir=root / "in" / "data",
        artcc_filter=opts["artcc_filter"],
        max_workers=3,
        include_custom=opts["include_custom"],
        include_presets=opts["include_presets"],
        artcc_cache_dir=root / "cache",
        weather_cache_dir=weather_dir,
        fetch_fresh_weather="weather" in stages,
        generate_briefings="briefings" in stages,
        generate_tiles="tiles" in stages,
        generate_index="index" in stages,
        skip_if_unchanged=not opts["force"],
    )


class _Recorder:
    """The fakes of one ``generate`` run and what they were called with."""

    def __init__(self, root: Path, opts: dict[str, Any]) -> None:
        self.root = root
        self.opts = opts
        self.calls: list[str] = []
        self.bbox_calls: list[str] = []
        self.tiles: list[str] = []
        self.index: list[str] = []
        self.zla_failure_logged = threading.Event()

    def observe(self, record: logging.LogRecord) -> None:
        if record.getMessage().startswith("Failed to fetch weather for ZLA"):
            self.zla_failure_logged.set()

    def call(self, name: str, *args: Any) -> None:
        self.calls.append(":".join([name, *(str(a) for a in args)]))

    def load_unified(self, *paths: str) -> dict[str, dict[str, Any]]:
        self.call("load_unified_airport_data", *(Path(p).name for p in paths))
        return json.loads(json.dumps(UNIFIED))

    def disambiguator(self, *args: Any, **kwargs: Any) -> FakeDisambiguator:
        self.call("AirportDisambiguator", Path(args[0]).name, sorted(kwargs))
        return FakeDisambiguator()

    def load_all_groupings(self, *args: Any) -> dict[str, list[str]]:
        self.call("load_all_groupings")
        return {name: list(items) for name, items in ALL_GROUPINGS.items()}

    def load_custom_groupings(self, *args: Any) -> dict[str, list[str]] | None:
        self.call("load_custom_groupings")
        names = self.opts["custom"]
        return {name: ALL_GROUPINGS[name] for name in names} if names else None

    def artcc_boundaries(self, cache_dir: Path) -> dict[str, Any]:
        self.call("get_artcc_boundaries")
        return BOUNDARIES if self.opts["boundaries"] else {}

    def fetch_bbox(
        self, bbox: tuple[float, ...], include_taf: bool
    ) -> tuple[dict[str, str], dict[str, str]]:
        artcc = "ZLA" if bbox[0] < 34.0 else "ZOA"
        self.bbox_calls.append(f"{artcc}:{[round(v, 2) for v in bbox]}:{include_taf}")
        if self.opts["network_empty"]:
            return {}, {}
        if self.opts["bbox_fail"]:
            return self._fail_zla_first(artcc)
        metars, tafs = BBOX_WEATHER[artcc]
        return dict(metars), dict(tafs)

    def _fail_zla_first(self, artcc: str) -> tuple[dict[str, str], dict[str, str]]:
        """ZLA's fetch fails and ZOA's returns only once that failure is logged.

        The run then logs in one order however the threads are scheduled. A run
        that never fetches ZLA's box gives up waiting after five seconds.
        """
        if artcc == "ZLA":
            raise RuntimeError("bbox down")
        self.zla_failure_logged.wait(timeout=5)
        metars, tafs = BBOX_WEATHER[artcc]
        return dict(metars), dict(tafs)

    def metar_batch(self, airports: list[str], max_workers: int) -> dict[str, str]:
        self.call("get_metar_batch", sorted(airports), max_workers)
        if self.opts["network_empty"]:
            return {}
        return {a: f"{a} 101156Z 00000KT 10SM CLR 15/05 A3000" for a in airports}

    def taf_batch(self, airports: list[str], max_workers: int) -> dict[str, str]:
        self.call("get_taf_batch", sorted(airports), max_workers)
        return {}

    def rate_status(self) -> dict[str, Any]:
        self.call("get_rate_limit_status")
        return RATE_STATUS[self.opts["rate"]]

    def vatsim(self) -> dict[str, Any] | None:
        self.call("download_vatsim_data")
        return {"pilots": []} if self.opts["vatsim"] else None

    def vatsim_atis(self, vatsim_data: Any, airports: list[str]) -> dict[str, Any]:
        self.call("get_atis_for_airports", sorted(airports))
        if self.opts["vatsim"] == "all":
            return dict.fromkeys(sorted(airports), VATSIM_ATIS["KSFO"])
        return {"KSFO": VATSIM_ATIS["KSFO"]} if "KSFO" in airports else {}

    def datis(self, airports: list[str]) -> dict[str, Any]:
        self.call("get_datis_for_airports", sorted(airports))
        return {a: RW_ATIS[a] for a in sorted(airports) if a in RW_ATIS}

    def generate_tiles(self, **kwargs: Any) -> dict[int, int]:
        self.call("generate_weather_tiles")
        weather = kwargs["airport_weather"]
        payload = {
            "airport_weather": {icao: weather[icao] for icao in sorted(weather)},
            "artcc_boundaries": sorted(kwargs["artcc_boundaries"]),
            "conus_artccs": len(kwargs["conus_artccs"]),
            "max_workers": kwargs["max_workers"],
            "output_dir": kwargs["output_dir"].relative_to(self.root).as_posix(),
            "zoom_levels": list(kwargs["zoom_levels"]),
        }
        self.tiles.append(_canonical(payload))
        kwargs["output_dir"].mkdir(parents=True, exist_ok=True)
        return {4: 3, 5: 7}

    def generate_index(
        self,
        config: DaemonConfig,
        artcc_groupings_map: dict[str, list[dict[str, Any]]],
        unified_airport_data: dict[str, Any],
        fallback_tile_version: int,
    ) -> Path | None:
        self.call("generate_index_page", fallback_tile_version)
        groupings = [
            [artcc, [_sorted_grouping_info(info) for info in infos]]
            for artcc, infos in artcc_groupings_map.items()
        ]
        payload = _canonical(groupings)
        self.index.append(payload)
        if not self.opts["index_path"]:
            return None
        path = config.output_dir / "index.html"
        path.write_text(payload, encoding="utf-8", newline="\n")
        return path


def _sorted_grouping_info(info: dict[str, Any]) -> dict[str, Any]:
    """A grouping's index info with its set-ordered lists sorted."""
    return {
        **info,
        "airport_coords": sorted(info["airport_coords"]),
        "airport_weather_points": sorted(
            info["airport_weather_points"], key=lambda point: point["icao"]
        ),
    }


def _install_fakes(
    gen: ModuleType, rec: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    fakes: dict[str, Callable[..., Any]] = {
        "load_unified_airport_data": rec.load_unified,
        "AirportDisambiguator": rec.disambiguator,
        "load_all_groupings": rec.load_all_groupings,
        "load_custom_groupings": rec.load_custom_groupings,
        "get_artcc_boundaries": rec.artcc_boundaries,
        "fetch_weather_bbox": rec.fetch_bbox,
        "get_metar_batch": rec.metar_batch,
        "get_taf_batch": rec.taf_batch,
        "get_rate_limit_status": rec.rate_status,
        "download_vatsim_data": rec.vatsim,
        "get_atis_for_airports": rec.vatsim_atis,
        "get_datis_for_airports": rec.datis,
        "generate_weather_tiles": rec.generate_tiles,
        "compute_code_version": lambda *args: CODE_VERSION,
    }
    for name, fake in fakes.items():
        monkeypatch.setattr(gen, name, fake)
    monkeypatch.setattr(index_generator, "generate_index_page", rec.generate_index)

    briefing_class = gen.WeatherBriefingGenerator
    real_html = briefing_class.generate_html

    def recorded_html(self: Any) -> str:
        rec.call("generate_html", self.grouping_name)
        if self.grouping_name in rec.opts["failing"]:
            raise RuntimeError(f"briefing for {self.grouping_name} failed")
        if rec.opts["real_html"]:
            return real_html(self)
        return _canonical(
            {"airports": sorted(self.airports), "weather": self.weather_data}
        )

    monkeypatch.setattr(briefing_class, "generate_html", recorded_html)


def _file_digest(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.name == "weather_cache.json":
        cache = json.loads(text)
        if cache["timestamp"] not in (FRESH_TIMESTAMP, STALE_TIMESTAMP):
            cache["timestamp"] = "<written>"
        text = _canonical(cache)
    return text if len(text) <= 80 else _sha12(text)


def run_generate(
    gen: ModuleType,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: dict[str, Any],
) -> dict[str, Any]:
    """Run ``gen.generate`` over one scenario and return its snapshot."""
    opts = {**DEFAULTS, **scenario}
    rec = _Recorder(root, opts)
    freeze_clock(monkeypatch, gen)
    _install_fakes(gen, rec, monkeypatch)
    config = _write_inputs(root, opts)

    records: list[logging.LogRecord] = []
    handler = _ListHandler(records, rec.observe)
    log = logging.getLogger("weather_daemon")
    level = log.level
    log.setLevel(logging.DEBUG)
    log.addHandler(handler)
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            result = gen.generate(config)
    finally:
        log.removeHandler(handler)
        log.setLevel(level)

    def clean(text: str) -> str:
        return text.replace(str(root), "<root>").replace("\\", "/")

    snapshot = {
        "result": sorted([clean(path), name] for path, name in result.items()),
        "calls": rec.calls,
        "bbox_calls": sorted(rec.bbox_calls),
        "tiles": [_sha12(payload) for payload in rec.tiles],
        "index": [_sha12(payload) for payload in rec.index],
        "files": {
            path.relative_to(root).as_posix(): _file_digest(path)
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.relative_to(root).parts[0] != "in"
        },
        "logs": [
            [r.levelname, clean(r.getMessage()), r.exc_info is not None]
            for r in records
        ],
        "stdout": _sha12(clean(stdout.getvalue())),
    }
    return json.loads(json.dumps(snapshot))


def snapshot_digest(snapshot: dict[str, Any]) -> str:
    return _sha12(_canonical(snapshot))


GENERATE_SCENARIOS: dict[str, dict[str, Any]] = {
    "fresh_cache_key_matches": {"stored_key": "match"},
    "fresh_cache_key_matches_force": {"stored_key": "match", "force": True},
    "fresh_cache_key_differs": {"stored_key": "other"},
    "fresh_cache_no_stored_key": {},
    "cached_weather_key_matches": {
        "stages": "briefings,tiles,index",
        "cache": "stale",
        "stored_key": "match",
    },
    "cached_weather_missing": {"stages": "briefings,tiles,index", "cache": "none"},
    "network_fetch": {
        "cache": "none",
        "bbox_fail": True,
        "rate": "limited",
        "stored_key": "match",
    },
    "network_fetch_stale_cache": {
        "cache": "stale",
        "rate": "recovered",
        "vatsim": False,
    },
    "network_without_artcc_bboxes": {"cache": "none", "boundaries": False},
    "network_only_unlikely_airports": {
        "cache": "none",
        "include_presets": False,
        "custom": ("CUSTOM_UNLIKELY",),
    },
    "network_returns_no_weather": {"cache": "none", "network_empty": True},
    "failing_briefing": {"failing": ("BAY", "All Oakland Airports")},
    "artcc_filter": {"artcc_filter": ["ZLA"]},
    "custom_groupings_off": {"include_custom": False},
    "custom_groupings_empty": {"custom": ()},
    "index_returns_none": {"index_path": False},
    "no_groupings": {"include_presets": False, "include_custom": False},
    "real_briefing_html": {
        "real_html": True,
        "presets": ("ZOA",),
        "include_custom": False,
    },
    "briefings_only_fresh_cache": {"stages": "weather,briefings"},
    "network_bbox_covers_every_airport": {
        "cache": "none",
        "presets": ("ZOA+bay",),
        "include_custom": False,
        "vatsim": "all",
    },
    "custom_groupings_without_artcc": {
        "include_presets": False,
        "custom": ("CUSTOM_NONE", "CUSTOM_NONE2"),
    },
}

STAGE_SETS = [
    ",".join(stages)
    for size in range(1, 5)
    for stages in itertools.combinations(
        ["weather", "briefings", "tiles", "index"], size
    )
]

STAGE_MATRIX: dict[str, dict[str, Any]] = {
    f"{stages}|{cache}": {"stages": stages, "cache": cache, "stored_key": "match"}
    for stages in STAGE_SETS
    for cache in ("fresh", "none")
}


@pytest.mark.parametrize("name", sorted(GENERATE_SCENARIOS))
def test_generate_scenario(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = run_generate(generator, tmp_path, monkeypatch, GENERATE_SCENARIOS[name])
    assert snapshot == GENERATE_GOLDEN[name]


@pytest.mark.parametrize("name", sorted(STAGE_MATRIX))
def test_generate_stage_matrix(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = run_generate(generator, tmp_path, monkeypatch, STAGE_MATRIX[name])
    assert snapshot_digest(snapshot) == STAGE_MATRIX_GOLDEN[name]


def test_matching_key_skips_only_the_tile_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = run_generate(
        generator, tmp_path, monkeypatch, GENERATE_SCENARIOS["fresh_cache_key_matches"]
    )
    assert "generate_weather_tiles" not in snapshot["calls"]
    assert "generate_index_page:1791635400" in snapshot["calls"]
    assert ["INFO", "Tile stage skipped: weather and code unchanged", False] in (
        snapshot["logs"]
    )
    assert "out/tiles/version.txt" not in snapshot["files"]


def test_index_stage_runs_before_the_tile_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = run_generate(
        generator, tmp_path, monkeypatch, GENERATE_SCENARIOS["network_fetch"]
    )
    calls = snapshot["calls"]
    assert calls.index("generate_index_page:1791635400") < calls.index(
        "generate_weather_tiles"
    )
    assert snapshot["files"]["out/tiles/version.txt"] == "1791635400\n"


def test_tile_version_is_the_run_start_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clock that jumps minutes per read tells the start time from any later read."""
    monkeypatch.setattr(_Clock, "step", timedelta(minutes=7))
    scenario = {"stages": "briefings,tiles,index", "cache": "stale"}
    snapshot = run_generate(generator, tmp_path, monkeypatch, scenario)
    start = int((FROZEN + timedelta(minutes=7)).timestamp())
    assert f"generate_index_page:{start}" in snapshot["calls"]
    assert snapshot["files"]["out/tiles/version.txt"] == f"{start}\n"


TAF_CHANGES: list[dict[str, Any]] = [
    {"type": "FM", "time_str": "101400", "category": "VFR", "trend": "stable"},
    {
        "type": "FM",
        "time_str": "101800",
        "category": "IFR",
        "trend": "worsening",
        "visibility_sm": 3.0,
        "ceiling_layer": "BKN008",
        "wind": "300@15G25",
        "phenomena": ["Light Rain"],
        "is_deterioration": True,
    },
    {
        "type": "TEMPO",
        "time_str": "1020/1024",
        "category": "LIFR",
        "trend": "worsening",
        "visibility_sm": 0.5,
        "ceiling_layer": "OVC004",
        "phenomena": ["Thunderstorm Rain", "Mist"],
        "is_deterioration": True,
    },
    {
        "type": "FM",
        "time_str": "110600",
        "category": "VFR",
        "trend": "improving",
        "visibility_sm": 6.0,
        "is_improvement": True,
    },
]

ATIS_VARIANTS: list[dict[str, Any]] = [
    {"atis_code": "C", "type": "departure", "source": "vatsim", "text_atis": ATIS_TEXT},
    RW_ATIS["KLAX"][0],
    {"atis_code": "F", "type": "combined", "source": "rw", "text_atis": ATIS_TEXT},
    {"atis_code": "G", "type": "combined", "text_atis": ""},
    {},
]

CARD_CASES: dict[str, tuple[str, dict[str, Any], bool, bool]] = {
    "full": (
        "KSJC",
        {
            "category": "MVFR",
            "ceiling": "BKN025",
            "visibility": "5SM",
            "wind": "310@08G18",
            "phenomena": ["Haze"],
            "obs_time": "101153",
            "is_speci": True,
            "taf_changes": TAF_CHANGES,
            "atis": ATIS_VARIANTS,
        },
        True,
        True,
    ),
    "empty": ("KXXX", {}, False, False),
    "obs_time_missing": ("KSFO", {"category": "VFR"}, True, True),
    "obs_time_not_requested": ("KSFO", {"obs_time": "101153"}, False, True),
    "obs_time_short": ("KSFO", {"obs_time": "1153"}, True, True),
    "unknown_category": (
        "KSFO",
        {"category": "XYZ", "visibility": "10SM"},
        False,
        True,
    ),
    "taf_no_details": (
        "KSFO",
        {"taf_changes": [{"type": "BECMG", "time_str": "", "is_improvement": True}]},
        False,
        True,
    ),
    "taf_mixed_widths": (
        "KSFO",
        {
            "category": "IFR",
            "taf_changes": [
                {
                    "type": "FM",
                    "time_str": "101800",
                    "category": "MVFR",
                    "trend": "worsening",
                    "wind": "VRB03",
                    "is_deterioration": True,
                },
                {
                    "type": "TEMPO",
                    "time_str": "1014/1016",
                    "category": "VFR",
                    "visibility_sm": 7.5,
                    "is_improvement": True,
                },
                {
                    "type": "FM",
                    "time_str": "111200",
                    "category": "LIFR",
                    "visibility_sm": 0.25,
                    "is_deterioration": True,
                },
            ],
        },
        False,
        True,
    ),
    "taf_unparsable_time": (
        "KSFO",
        {
            "taf_changes": [
                {
                    "type": "PROB30",
                    "time_str": "garbage",
                    "category": "IFR",
                    "visibility_sm": 2.0,
                    "ceiling_layer": "OVC009",
                    "is_deterioration": True,
                }
            ]
        },
        False,
        True,
    ),
    "taf_only_wx": (
        "KSFO",
        {
            "taf_changes": [
                {
                    "type": "FM",
                    "time_str": "101900",
                    "phenomena": ["Snow"],
                    "is_deterioration": True,
                }
            ]
        },
        False,
        True,
    ),
    "atis_none": ("KSFO", {"atis": None, "phenomena": []}, False, True),
}


def build_card(gen: ModuleType, name: str) -> str:
    icao, data, show_obs_time, with_disambiguator = CARD_CASES[name]
    disambiguator = FakeDisambiguator() if with_disambiguator else None
    briefing = gen.WeatherBriefingGenerator("Test", [], UNIFIED, disambiguator)
    return briefing._build_airport_card(icao, data, show_obs_time=show_obs_time)


HTML_AIRPORTS = ["KSFO", "KOAK", "KSJC", "KLAX", "KBUR", "KNOC", "KXXX"]


def _fetched(gen: ModuleType, airports: list[str], grouping: str = "Bay Area") -> Any:
    briefing = gen.WeatherBriefingGenerator(
        grouping, airports, UNIFIED, FakeDisambiguator()
    )
    briefing.fetch_weather_data(METARS, TAFS, {**VATSIM_ATIS, **RW_ATIS})
    return briefing


def _no_obs_times(gen: ModuleType) -> Any:
    briefing = gen.WeatherBriefingGenerator(
        "Manual", ["KSFO", "KOAK"], UNIFIED, FakeDisambiguator()
    )
    briefing.weather_data = {
        "KSFO": {"category": "VFR", "metar": "KSFO"},
        "KOAK": {"category": "IFR", "metar": "KOAK", "obs_time": "1156"},
    }
    return briefing


def _empty_area(gen: ModuleType) -> Any:
    briefing = _fetched(gen, ["KSFO", "KOAK", "KLAX"], "Areas")
    airports = [(icao, data, 1) for icao, data in briefing.weather_data.items()]
    briefing._create_area_groups = lambda: [
        {"name": "Void", "airports": []},
        {"name": "Coast", "airports": airports},
        {"name": "Unknowns", "airports": [("KXXX", {"category": "UNK"}, 1)]},
    ]
    return briefing


HTML_CASES: dict[str, Callable[[ModuleType], Any]] = {
    "empty": lambda gen: gen.WeatherBriefingGenerator("Empty", [], UNIFIED, None),
    "bay": lambda gen: _fetched(gen, HTML_AIRPORTS),
    "no_obs_times": _no_obs_times,
    "unknown_only": lambda gen: _fetched(gen, ["KBUR", "KXXX"], "Unknown"),
    "empty_area": _empty_area,
}


def _points_mixed(gen: ModuleType) -> Any:
    briefing = gen.WeatherBriefingGenerator(
        "Points", ["KSFO", "KXXX", "KOAK"], UNIFIED, None
    )
    briefing.weather_data = {
        "KSFO": {
            "category": "IFR",
            "visibility": "2SM",
            "ceiling": "OVC006",
            "wind": "280@10",
            "phenomena": ["Mist"],
            "taf_changes": [
                *TAF_CHANGES,
                {"type": "FM", "visibility_sm": 1.5, "is_improvement": True},
            ],
        },
        "KXXX": {"category": "VFR"},
        "KOAK": {
            "taf_changes": [
                {"visibility_sm": 3.0, "is_deterioration": True},
                {"visibility_sm": 1.5, "is_improvement": True},
                {"visibility_sm": 0.0, "is_improvement": True},
            ]
        },
        "KSJC": {
            "taf_changes": [
                {"is_deterioration": True},
                {"visibility_sm": 8.0, "is_improvement": True},
            ]
        },
    }
    return briefing


POINTS_CASES: dict[str, Callable[[ModuleType], Any]] = {
    "empty": lambda gen: gen.WeatherBriefingGenerator("Empty", [], UNIFIED, None),
    "fetched": lambda gen: _fetched(gen, HTML_AIRPORTS),
    "mixed": _points_mixed,
}


@pytest.mark.parametrize("name", sorted(CARD_CASES))
def test_airport_card(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    freeze_clock(monkeypatch, generator)
    assert build_card(generator, name) == CARD_GOLDEN[name]


@pytest.mark.parametrize("name", sorted(HTML_CASES))
def test_generate_html(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    freeze_clock(monkeypatch, generator)
    html = HTML_CASES[name](generator).generate_html()
    assert _sha12(html) == HTML_GOLDEN[name]


@pytest.mark.parametrize("name", sorted(POINTS_CASES))
def test_airport_weather_points(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    freeze_clock(monkeypatch, generator)
    points = POINTS_CASES[name](generator).get_airport_weather_points()
    assert points == POINTS_GOLDEN[name]


def test_empty_briefing_html() -> None:
    briefing = generator.WeatherBriefingGenerator("Empty", [], UNIFIED, None)
    assert briefing.generate_html() == (
        "<html><body><p>No weather data available</p></body></html>"
    )


GENERATE_GOLDEN: dict[str, Any] = {
    "artcc_filter": {
        "result": [
            ["<root>/out/ZLA/SOCAL.html", "SOCAL"],
            ["<root>/out/ZLA/_all.html", "All Los Angeles Airports"],
            ["<root>/out/custom/BAY.html", "BAY"],
            ["<root>/out/custom/CUSTOM_MIX.html", "CUSTOM_MIX"],
            ["<root>/out/custom/CUSTOM_NONE.html", "CUSTOM_NONE"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "generate_html:SOCAL",
            "generate_html:BAY",
            "generate_html:CUSTOM_MIX",
            "generate_html:CUSTOM_NONE",
            "generate_html:All Los Angeles Airports",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
            "generate_weather_tiles",
        ],
        "bbox_calls": [],
        "tiles": ["sha256:bad5c942a2b4"],
        "index": ["sha256:23c6d8be02e2"],
        "files": {
            "out/custom/BAY.html": "sha256:681505cd01d7",
            "out/custom/CUSTOM_MIX.html": "sha256:57b9ad9d47ca",
            "out/custom/CUSTOM_NONE.html": "sha256:010e36c9c115",
            "out/index.html": "sha256:23c6d8be02e2",
            "out/tiles/version.txt": "1791635400\n",
            "out/ZLA/_all.html": "sha256:5d11499e9533",
            "out/ZLA/SOCAL.html": "sha256:5d11499e9533",
            "weather/weather_cache.json": "sha256:4f63ec5aae24",
            "weather/weather_hash.txt": "1b5bec45728d3760:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Using cached weather data from 2026-10-10T12:30:00+00:00", False],
            ["INFO", "Generating 4 briefings", False],
            ["DEBUG", "Briefing generation: 25% (1/4)", False],
            ["DEBUG", "Briefing generation: 50% (2/4)", False],
            ["DEBUG", "Briefing generation: 75% (3/4)", False],
            ["INFO", "Briefing generation: completed 4 items in 0.0s", False],
            ["INFO", "Generating 1 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZLA/_all.html", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 5 airports with valid weather (of 5 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            ["INFO", "Generated 10 weather tiles across 2 zoom levels", False],
            ["INFO", "Generation complete: 6 files in 0.0s", False],
        ],
        "stdout": "sha256:b34a89daddf1",
    },
    "briefings_only_fresh_cache": {
        "result": [
            ["<root>/out/ZLA/SOCAL.html", "SOCAL"],
            ["<root>/out/ZLA/_all.html", "All Los Angeles Airports"],
            ["<root>/out/ZOA/BAY.html", "BAY"],
            ["<root>/out/ZOA/NORTH.html", "NORTH"],
            ["<root>/out/ZOA/TOWER.html", "TOWER"],
            ["<root>/out/ZOA/_all.html", "All Oakland Airports"],
            ["<root>/out/custom/CUSTOM_MIX.html", "CUSTOM_MIX"],
            ["<root>/out/custom/CUSTOM_NONE.html", "CUSTOM_NONE"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "generate_html:SOCAL",
            "generate_html:BAY",
            "generate_html:NORTH",
            "generate_html:TOWER",
            "generate_html:CUSTOM_MIX",
            "generate_html:CUSTOM_NONE",
            "generate_html:All Los Angeles Airports",
            "generate_html:All Oakland Airports",
        ],
        "bbox_calls": [],
        "tiles": [],
        "index": [],
        "files": {
            "out/custom/CUSTOM_MIX.html": "sha256:57b9ad9d47ca",
            "out/custom/CUSTOM_NONE.html": "sha256:010e36c9c115",
            "out/ZLA/_all.html": "sha256:5d11499e9533",
            "out/ZLA/SOCAL.html": "sha256:5d11499e9533",
            "out/ZOA/_all.html": "sha256:1e07d4f3fc4e",
            "out/ZOA/BAY.html": "sha256:681505cd01d7",
            "out/ZOA/NORTH.html": "sha256:40ba508870b1",
            "out/ZOA/TOWER.html": "sha256:31107bdd94b1",
            "weather/weather_cache.json": "sha256:4f63ec5aae24",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Using cached weather data from 2026-10-10T12:30:00+00:00", False],
            ["INFO", "Generating 6 briefings", False],
            ["DEBUG", "Briefing generation: 16% (1/6)", False],
            ["DEBUG", "Briefing generation: 33% (2/6)", False],
            ["DEBUG", "Briefing generation: 50% (3/6)", False],
            ["DEBUG", "Briefing generation: 66% (4/6)", False],
            ["DEBUG", "Briefing generation: 83% (5/6)", False],
            ["INFO", "Briefing generation: completed 6 items in 0.0s", False],
            ["INFO", "Generating 2 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZLA/_all.html", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZOA/_all.html", False],
            ["INFO", "Skipping tile generation (--no-tiles)", False],
            ["INFO", "Generation complete: 8 files in 0.0s", False],
        ],
        "stdout": "sha256:e1f880918c51",
    },
    "cached_weather_key_matches": {
        "result": [
            ["<root>/out/ZLA/SOCAL.html", "SOCAL"],
            ["<root>/out/ZLA/_all.html", "All Los Angeles Airports"],
            ["<root>/out/ZOA/BAY.html", "BAY"],
            ["<root>/out/ZOA/NORTH.html", "NORTH"],
            ["<root>/out/ZOA/TOWER.html", "TOWER"],
            ["<root>/out/ZOA/_all.html", "All Oakland Airports"],
            ["<root>/out/custom/CUSTOM_MIX.html", "CUSTOM_MIX"],
            ["<root>/out/custom/CUSTOM_NONE.html", "CUSTOM_NONE"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "generate_html:SOCAL",
            "generate_html:BAY",
            "generate_html:NORTH",
            "generate_html:TOWER",
            "generate_html:CUSTOM_MIX",
            "generate_html:CUSTOM_NONE",
            "generate_html:All Los Angeles Airports",
            "generate_html:All Oakland Airports",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
            "generate_weather_tiles",
        ],
        "bbox_calls": [],
        "tiles": ["sha256:bad5c942a2b4"],
        "index": ["sha256:938b658a18c3"],
        "files": {
            "out/custom/CUSTOM_MIX.html": "sha256:57b9ad9d47ca",
            "out/custom/CUSTOM_NONE.html": "sha256:010e36c9c115",
            "out/index.html": "sha256:938b658a18c3",
            "out/tiles/version.txt": "1791635400\n",
            "out/ZLA/_all.html": "sha256:5d11499e9533",
            "out/ZLA/SOCAL.html": "sha256:5d11499e9533",
            "out/ZOA/_all.html": "sha256:1e07d4f3fc4e",
            "out/ZOA/BAY.html": "sha256:681505cd01d7",
            "out/ZOA/NORTH.html": "sha256:40ba508870b1",
            "out/ZOA/TOWER.html": "sha256:31107bdd94b1",
            "weather/weather_cache.json": "sha256:48f1effa01fd",
            "weather/weather_hash.txt": "1b5bec45728d3760:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Using cached weather data from 2026-10-10T11:30:00+00:00", False],
            ["INFO", "Generating 6 briefings", False],
            ["DEBUG", "Briefing generation: 16% (1/6)", False],
            ["DEBUG", "Briefing generation: 33% (2/6)", False],
            ["DEBUG", "Briefing generation: 50% (3/6)", False],
            ["DEBUG", "Briefing generation: 66% (4/6)", False],
            ["DEBUG", "Briefing generation: 83% (5/6)", False],
            ["INFO", "Briefing generation: completed 6 items in 0.0s", False],
            ["INFO", "Generating 2 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZLA/_all.html", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZOA/_all.html", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 5 airports with valid weather (of 5 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            ["INFO", "Generated 10 weather tiles across 2 zoom levels", False],
            ["INFO", "Generation complete: 9 files in 0.0s", False],
        ],
        "stdout": "sha256:06b38821c56a",
    },
    "cached_weather_missing": {
        "result": [
            ["<root>/out/ZLA/SOCAL.html", "SOCAL"],
            ["<root>/out/ZLA/_all.html", "All Los Angeles Airports"],
            ["<root>/out/ZOA/BAY.html", "BAY"],
            ["<root>/out/ZOA/NORTH.html", "NORTH"],
            ["<root>/out/ZOA/TOWER.html", "TOWER"],
            ["<root>/out/ZOA/_all.html", "All Oakland Airports"],
            ["<root>/out/custom/CUSTOM_MIX.html", "CUSTOM_MIX"],
            ["<root>/out/custom/CUSTOM_NONE.html", "CUSTOM_NONE"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "generate_html:SOCAL",
            "generate_html:BAY",
            "generate_html:NORTH",
            "generate_html:TOWER",
            "generate_html:CUSTOM_MIX",
            "generate_html:CUSTOM_NONE",
            "generate_html:All Los Angeles Airports",
            "generate_html:All Oakland Airports",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
        ],
        "bbox_calls": [],
        "tiles": [],
        "index": ["sha256:75e642585d70"],
        "files": {
            "out/custom/CUSTOM_MIX.html": "sha256:f4c464f1a26f",
            "out/custom/CUSTOM_NONE.html": "sha256:010e36c9c115",
            "out/index.html": "sha256:75e642585d70",
            "out/ZLA/_all.html": "sha256:c7d7bf861e68",
            "out/ZLA/SOCAL.html": "sha256:c7d7bf861e68",
            "out/ZOA/_all.html": "sha256:ddbd87d02fa1",
            "out/ZOA/BAY.html": "sha256:7f41c21c0a9c",
            "out/ZOA/NORTH.html": "sha256:1d8723e58cd6",
            "out/ZOA/TOWER.html": "sha256:823d63ae1811",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            [
                "WARNING",
                "No cached weather data found, continuing with empty weather",
                False,
            ],
            ["INFO", "Generating 6 briefings", False],
            ["DEBUG", "Briefing generation: 16% (1/6)", False],
            ["DEBUG", "Briefing generation: 33% (2/6)", False],
            ["DEBUG", "Briefing generation: 50% (3/6)", False],
            ["DEBUG", "Briefing generation: 66% (4/6)", False],
            ["DEBUG", "Briefing generation: 83% (5/6)", False],
            ["INFO", "Briefing generation: completed 6 items in 0.0s", False],
            ["INFO", "Generating 2 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZLA/_all.html", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZOA/_all.html", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 0 airports with valid weather (of 0 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            [
                "WARNING",
                "No airport weather data collected - skipping tile generation",
                False,
            ],
            ["INFO", "Generation complete: 9 files in 0.0s", False],
        ],
        "stdout": "sha256:5913ddf08cab",
    },
    "custom_groupings_empty": {
        "result": [
            ["<root>/out/ZLA/SOCAL.html", "SOCAL"],
            ["<root>/out/ZLA/_all.html", "All Los Angeles Airports"],
            ["<root>/out/ZOA/BAY.html", "BAY"],
            ["<root>/out/ZOA/NORTH.html", "NORTH"],
            ["<root>/out/ZOA/TOWER.html", "TOWER"],
            ["<root>/out/ZOA/_all.html", "All Oakland Airports"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "generate_html:SOCAL",
            "generate_html:BAY",
            "generate_html:NORTH",
            "generate_html:TOWER",
            "generate_html:All Los Angeles Airports",
            "generate_html:All Oakland Airports",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
            "generate_weather_tiles",
        ],
        "bbox_calls": [],
        "tiles": ["sha256:bad5c942a2b4"],
        "index": ["sha256:90be7042c313"],
        "files": {
            "out/index.html": "sha256:90be7042c313",
            "out/tiles/version.txt": "1791635400\n",
            "out/ZLA/_all.html": "sha256:5d11499e9533",
            "out/ZLA/SOCAL.html": "sha256:5d11499e9533",
            "out/ZOA/_all.html": "sha256:1e07d4f3fc4e",
            "out/ZOA/BAY.html": "sha256:681505cd01d7",
            "out/ZOA/NORTH.html": "sha256:40ba508870b1",
            "out/ZOA/TOWER.html": "sha256:31107bdd94b1",
            "weather/weather_cache.json": "sha256:4f63ec5aae24",
            "weather/weather_hash.txt": "1b5bec45728d3760:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Using cached weather data from 2026-10-10T12:30:00+00:00", False],
            ["INFO", "Generating 4 briefings", False],
            ["DEBUG", "Briefing generation: 25% (1/4)", False],
            ["DEBUG", "Briefing generation: 50% (2/4)", False],
            ["DEBUG", "Briefing generation: 75% (3/4)", False],
            ["INFO", "Briefing generation: completed 4 items in 0.0s", False],
            ["INFO", "Generating 2 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZLA/_all.html", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZOA/_all.html", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 5 airports with valid weather (of 5 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            ["INFO", "Generated 10 weather tiles across 2 zoom levels", False],
            ["INFO", "Generation complete: 7 files in 0.0s", False],
        ],
        "stdout": "sha256:6df8c38b466d",
    },
    "custom_groupings_off": {
        "result": [
            ["<root>/out/ZLA/SOCAL.html", "SOCAL"],
            ["<root>/out/ZLA/_all.html", "All Los Angeles Airports"],
            ["<root>/out/ZOA/BAY.html", "BAY"],
            ["<root>/out/ZOA/NORTH.html", "NORTH"],
            ["<root>/out/ZOA/TOWER.html", "TOWER"],
            ["<root>/out/ZOA/_all.html", "All Oakland Airports"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "generate_html:SOCAL",
            "generate_html:BAY",
            "generate_html:NORTH",
            "generate_html:TOWER",
            "generate_html:All Los Angeles Airports",
            "generate_html:All Oakland Airports",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
            "generate_weather_tiles",
        ],
        "bbox_calls": [],
        "tiles": ["sha256:bad5c942a2b4"],
        "index": ["sha256:90be7042c313"],
        "files": {
            "out/index.html": "sha256:90be7042c313",
            "out/tiles/version.txt": "1791635400\n",
            "out/ZLA/_all.html": "sha256:5d11499e9533",
            "out/ZLA/SOCAL.html": "sha256:5d11499e9533",
            "out/ZOA/_all.html": "sha256:1e07d4f3fc4e",
            "out/ZOA/BAY.html": "sha256:681505cd01d7",
            "out/ZOA/NORTH.html": "sha256:40ba508870b1",
            "out/ZOA/TOWER.html": "sha256:31107bdd94b1",
            "weather/weather_cache.json": "sha256:4f63ec5aae24",
            "weather/weather_hash.txt": "1b5bec45728d3760:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Using cached weather data from 2026-10-10T12:30:00+00:00", False],
            ["INFO", "Generating 4 briefings", False],
            ["DEBUG", "Briefing generation: 25% (1/4)", False],
            ["DEBUG", "Briefing generation: 50% (2/4)", False],
            ["DEBUG", "Briefing generation: 75% (3/4)", False],
            ["INFO", "Briefing generation: completed 4 items in 0.0s", False],
            ["INFO", "Generating 2 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZLA/_all.html", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZOA/_all.html", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 5 airports with valid weather (of 5 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            ["INFO", "Generated 10 weather tiles across 2 zoom levels", False],
            ["INFO", "Generation complete: 7 files in 0.0s", False],
        ],
        "stdout": "sha256:6df8c38b466d",
    },
    "custom_groupings_without_artcc": {
        "result": [
            ["<root>/out/custom/CUSTOM_NONE.html", "CUSTOM_NONE"],
            ["<root>/out/custom/CUSTOM_NONE2.html", "CUSTOM_NONE2"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "generate_html:CUSTOM_NONE",
            "generate_html:CUSTOM_NONE2",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
            "generate_weather_tiles",
        ],
        "bbox_calls": [],
        "tiles": ["sha256:bad5c942a2b4"],
        "index": ["sha256:d7080d6d334e"],
        "files": {
            "out/custom/CUSTOM_NONE.html": "sha256:010e36c9c115",
            "out/custom/CUSTOM_NONE2.html": "sha256:ea6c4df957b7",
            "out/index.html": "sha256:d7080d6d334e",
            "out/tiles/version.txt": "1791635400\n",
            "weather/weather_cache.json": "sha256:4f63ec5aae24",
            "weather/weather_hash.txt": "1b5bec45728d3760:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Using cached weather data from 2026-10-10T12:30:00+00:00", False],
            ["INFO", "Generating 2 briefings", False],
            ["DEBUG", "Briefing generation: 50% (1/2)", False],
            ["INFO", "Briefing generation: completed 2 items in 0.0s", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 5 airports with valid weather (of 5 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            ["INFO", "Generated 10 weather tiles across 2 zoom levels", False],
            ["INFO", "Generation complete: 3 files in 0.0s", False],
        ],
        "stdout": "sha256:54bf0e0e8b8c",
    },
    "failing_briefing": {
        "result": [
            ["<root>/out/ZLA/SOCAL.html", "SOCAL"],
            ["<root>/out/ZLA/_all.html", "All Los Angeles Airports"],
            ["<root>/out/ZOA/NORTH.html", "NORTH"],
            ["<root>/out/ZOA/TOWER.html", "TOWER"],
            ["<root>/out/custom/CUSTOM_MIX.html", "CUSTOM_MIX"],
            ["<root>/out/custom/CUSTOM_NONE.html", "CUSTOM_NONE"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "generate_html:SOCAL",
            "generate_html:BAY",
            "generate_html:NORTH",
            "generate_html:TOWER",
            "generate_html:CUSTOM_MIX",
            "generate_html:CUSTOM_NONE",
            "generate_html:All Los Angeles Airports",
            "generate_html:All Oakland Airports",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
            "generate_weather_tiles",
        ],
        "bbox_calls": [],
        "tiles": ["sha256:bad5c942a2b4"],
        "index": ["sha256:900a1eb0694b"],
        "files": {
            "out/custom/CUSTOM_MIX.html": "sha256:57b9ad9d47ca",
            "out/custom/CUSTOM_NONE.html": "sha256:010e36c9c115",
            "out/index.html": "sha256:900a1eb0694b",
            "out/tiles/version.txt": "1791635400\n",
            "out/ZLA/_all.html": "sha256:5d11499e9533",
            "out/ZLA/SOCAL.html": "sha256:5d11499e9533",
            "out/ZOA/NORTH.html": "sha256:40ba508870b1",
            "out/ZOA/TOWER.html": "sha256:31107bdd94b1",
            "weather/weather_cache.json": "sha256:4f63ec5aae24",
            "weather/weather_hash.txt": "1b5bec45728d3760:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Using cached weather data from 2026-10-10T12:30:00+00:00", False],
            ["INFO", "Generating 6 briefings", False],
            ["DEBUG", "Briefing generation: 16% (1/6)", False],
            ["ERROR", "Skipping briefing for grouping 'BAY' in ARTCC ZOA", True],
            ["DEBUG", "Briefing generation: 33% (2/6)", False],
            ["DEBUG", "Briefing generation: 50% (3/6)", False],
            ["DEBUG", "Briefing generation: 66% (4/6)", False],
            ["DEBUG", "Briefing generation: 83% (5/6)", False],
            ["INFO", "Generating 2 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZLA/_all.html", False],
            [
                "ERROR",
                "Skipping briefing for grouping 'All Oakland Airports' in ARTCC ZOA",
                True,
            ],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 5 airports with valid weather (of 5 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            ["INFO", "Generated 10 weather tiles across 2 zoom levels", False],
            ["INFO", "Generation complete: 7 files in 0.0s", False],
        ],
        "stdout": "sha256:90b5338c335c",
    },
    "fresh_cache_key_differs": {
        "result": [
            ["<root>/out/ZLA/SOCAL.html", "SOCAL"],
            ["<root>/out/ZLA/_all.html", "All Los Angeles Airports"],
            ["<root>/out/ZOA/BAY.html", "BAY"],
            ["<root>/out/ZOA/NORTH.html", "NORTH"],
            ["<root>/out/ZOA/TOWER.html", "TOWER"],
            ["<root>/out/ZOA/_all.html", "All Oakland Airports"],
            ["<root>/out/custom/CUSTOM_MIX.html", "CUSTOM_MIX"],
            ["<root>/out/custom/CUSTOM_NONE.html", "CUSTOM_NONE"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "generate_html:SOCAL",
            "generate_html:BAY",
            "generate_html:NORTH",
            "generate_html:TOWER",
            "generate_html:CUSTOM_MIX",
            "generate_html:CUSTOM_NONE",
            "generate_html:All Los Angeles Airports",
            "generate_html:All Oakland Airports",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
            "generate_weather_tiles",
        ],
        "bbox_calls": [],
        "tiles": ["sha256:bad5c942a2b4"],
        "index": ["sha256:938b658a18c3"],
        "files": {
            "out/custom/CUSTOM_MIX.html": "sha256:57b9ad9d47ca",
            "out/custom/CUSTOM_NONE.html": "sha256:010e36c9c115",
            "out/index.html": "sha256:938b658a18c3",
            "out/tiles/version.txt": "1791635400\n",
            "out/ZLA/_all.html": "sha256:5d11499e9533",
            "out/ZLA/SOCAL.html": "sha256:5d11499e9533",
            "out/ZOA/_all.html": "sha256:1e07d4f3fc4e",
            "out/ZOA/BAY.html": "sha256:681505cd01d7",
            "out/ZOA/NORTH.html": "sha256:40ba508870b1",
            "out/ZOA/TOWER.html": "sha256:31107bdd94b1",
            "weather/weather_cache.json": "sha256:4f63ec5aae24",
            "weather/weather_hash.txt": "1b5bec45728d3760:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Using cached weather data from 2026-10-10T12:30:00+00:00", False],
            ["INFO", "Generating 6 briefings", False],
            ["DEBUG", "Briefing generation: 16% (1/6)", False],
            ["DEBUG", "Briefing generation: 33% (2/6)", False],
            ["DEBUG", "Briefing generation: 50% (3/6)", False],
            ["DEBUG", "Briefing generation: 66% (4/6)", False],
            ["DEBUG", "Briefing generation: 83% (5/6)", False],
            ["INFO", "Briefing generation: completed 6 items in 0.0s", False],
            ["INFO", "Generating 2 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZLA/_all.html", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZOA/_all.html", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 5 airports with valid weather (of 5 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            ["INFO", "Generated 10 weather tiles across 2 zoom levels", False],
            ["INFO", "Generation complete: 9 files in 0.0s", False],
        ],
        "stdout": "sha256:01e1c8353d2f",
    },
    "fresh_cache_key_matches": {
        "result": [
            ["<root>/out/ZLA/SOCAL.html", "SOCAL"],
            ["<root>/out/ZLA/_all.html", "All Los Angeles Airports"],
            ["<root>/out/ZOA/BAY.html", "BAY"],
            ["<root>/out/ZOA/NORTH.html", "NORTH"],
            ["<root>/out/ZOA/TOWER.html", "TOWER"],
            ["<root>/out/ZOA/_all.html", "All Oakland Airports"],
            ["<root>/out/custom/CUSTOM_MIX.html", "CUSTOM_MIX"],
            ["<root>/out/custom/CUSTOM_NONE.html", "CUSTOM_NONE"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "generate_html:SOCAL",
            "generate_html:BAY",
            "generate_html:NORTH",
            "generate_html:TOWER",
            "generate_html:CUSTOM_MIX",
            "generate_html:CUSTOM_NONE",
            "generate_html:All Los Angeles Airports",
            "generate_html:All Oakland Airports",
            "generate_index_page:1791635400",
        ],
        "bbox_calls": [],
        "tiles": [],
        "index": ["sha256:938b658a18c3"],
        "files": {
            "out/custom/CUSTOM_MIX.html": "sha256:57b9ad9d47ca",
            "out/custom/CUSTOM_NONE.html": "sha256:010e36c9c115",
            "out/index.html": "sha256:938b658a18c3",
            "out/ZLA/_all.html": "sha256:5d11499e9533",
            "out/ZLA/SOCAL.html": "sha256:5d11499e9533",
            "out/ZOA/_all.html": "sha256:1e07d4f3fc4e",
            "out/ZOA/BAY.html": "sha256:681505cd01d7",
            "out/ZOA/NORTH.html": "sha256:40ba508870b1",
            "out/ZOA/TOWER.html": "sha256:31107bdd94b1",
            "weather/weather_cache.json": "sha256:4f63ec5aae24",
            "weather/weather_hash.txt": "1b5bec45728d3760:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Using cached weather data from 2026-10-10T12:30:00+00:00", False],
            ["INFO", "Generating 6 briefings", False],
            ["DEBUG", "Briefing generation: 16% (1/6)", False],
            ["DEBUG", "Briefing generation: 33% (2/6)", False],
            ["DEBUG", "Briefing generation: 50% (3/6)", False],
            ["DEBUG", "Briefing generation: 66% (4/6)", False],
            ["DEBUG", "Briefing generation: 83% (5/6)", False],
            ["INFO", "Briefing generation: completed 6 items in 0.0s", False],
            ["INFO", "Generating 2 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZLA/_all.html", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZOA/_all.html", False],
            ["INFO", "Tile stage skipped: weather and code unchanged", False],
            ["INFO", "Generation complete: 9 files in 0.0s", False],
        ],
        "stdout": "sha256:283cb14fc423",
    },
    "fresh_cache_key_matches_force": {
        "result": [
            ["<root>/out/ZLA/SOCAL.html", "SOCAL"],
            ["<root>/out/ZLA/_all.html", "All Los Angeles Airports"],
            ["<root>/out/ZOA/BAY.html", "BAY"],
            ["<root>/out/ZOA/NORTH.html", "NORTH"],
            ["<root>/out/ZOA/TOWER.html", "TOWER"],
            ["<root>/out/ZOA/_all.html", "All Oakland Airports"],
            ["<root>/out/custom/CUSTOM_MIX.html", "CUSTOM_MIX"],
            ["<root>/out/custom/CUSTOM_NONE.html", "CUSTOM_NONE"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "generate_html:SOCAL",
            "generate_html:BAY",
            "generate_html:NORTH",
            "generate_html:TOWER",
            "generate_html:CUSTOM_MIX",
            "generate_html:CUSTOM_NONE",
            "generate_html:All Los Angeles Airports",
            "generate_html:All Oakland Airports",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
            "generate_weather_tiles",
        ],
        "bbox_calls": [],
        "tiles": ["sha256:bad5c942a2b4"],
        "index": ["sha256:938b658a18c3"],
        "files": {
            "out/custom/CUSTOM_MIX.html": "sha256:57b9ad9d47ca",
            "out/custom/CUSTOM_NONE.html": "sha256:010e36c9c115",
            "out/index.html": "sha256:938b658a18c3",
            "out/tiles/version.txt": "1791635400\n",
            "out/ZLA/_all.html": "sha256:5d11499e9533",
            "out/ZLA/SOCAL.html": "sha256:5d11499e9533",
            "out/ZOA/_all.html": "sha256:1e07d4f3fc4e",
            "out/ZOA/BAY.html": "sha256:681505cd01d7",
            "out/ZOA/NORTH.html": "sha256:40ba508870b1",
            "out/ZOA/TOWER.html": "sha256:31107bdd94b1",
            "weather/weather_cache.json": "sha256:4f63ec5aae24",
            "weather/weather_hash.txt": "1b5bec45728d3760:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Using cached weather data from 2026-10-10T12:30:00+00:00", False],
            ["INFO", "Generating 6 briefings", False],
            ["DEBUG", "Briefing generation: 16% (1/6)", False],
            ["DEBUG", "Briefing generation: 33% (2/6)", False],
            ["DEBUG", "Briefing generation: 50% (3/6)", False],
            ["DEBUG", "Briefing generation: 66% (4/6)", False],
            ["DEBUG", "Briefing generation: 83% (5/6)", False],
            ["INFO", "Briefing generation: completed 6 items in 0.0s", False],
            ["INFO", "Generating 2 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZLA/_all.html", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZOA/_all.html", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 5 airports with valid weather (of 5 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            ["INFO", "Generated 10 weather tiles across 2 zoom levels", False],
            ["INFO", "Generation complete: 9 files in 0.0s", False],
        ],
        "stdout": "sha256:01e1c8353d2f",
    },
    "fresh_cache_no_stored_key": {
        "result": [
            ["<root>/out/ZLA/SOCAL.html", "SOCAL"],
            ["<root>/out/ZLA/_all.html", "All Los Angeles Airports"],
            ["<root>/out/ZOA/BAY.html", "BAY"],
            ["<root>/out/ZOA/NORTH.html", "NORTH"],
            ["<root>/out/ZOA/TOWER.html", "TOWER"],
            ["<root>/out/ZOA/_all.html", "All Oakland Airports"],
            ["<root>/out/custom/CUSTOM_MIX.html", "CUSTOM_MIX"],
            ["<root>/out/custom/CUSTOM_NONE.html", "CUSTOM_NONE"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "generate_html:SOCAL",
            "generate_html:BAY",
            "generate_html:NORTH",
            "generate_html:TOWER",
            "generate_html:CUSTOM_MIX",
            "generate_html:CUSTOM_NONE",
            "generate_html:All Los Angeles Airports",
            "generate_html:All Oakland Airports",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
            "generate_weather_tiles",
        ],
        "bbox_calls": [],
        "tiles": ["sha256:bad5c942a2b4"],
        "index": ["sha256:938b658a18c3"],
        "files": {
            "out/custom/CUSTOM_MIX.html": "sha256:57b9ad9d47ca",
            "out/custom/CUSTOM_NONE.html": "sha256:010e36c9c115",
            "out/index.html": "sha256:938b658a18c3",
            "out/tiles/version.txt": "1791635400\n",
            "out/ZLA/_all.html": "sha256:5d11499e9533",
            "out/ZLA/SOCAL.html": "sha256:5d11499e9533",
            "out/ZOA/_all.html": "sha256:1e07d4f3fc4e",
            "out/ZOA/BAY.html": "sha256:681505cd01d7",
            "out/ZOA/NORTH.html": "sha256:40ba508870b1",
            "out/ZOA/TOWER.html": "sha256:31107bdd94b1",
            "weather/weather_cache.json": "sha256:4f63ec5aae24",
            "weather/weather_hash.txt": "1b5bec45728d3760:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Using cached weather data from 2026-10-10T12:30:00+00:00", False],
            ["INFO", "Generating 6 briefings", False],
            ["DEBUG", "Briefing generation: 16% (1/6)", False],
            ["DEBUG", "Briefing generation: 33% (2/6)", False],
            ["DEBUG", "Briefing generation: 50% (3/6)", False],
            ["DEBUG", "Briefing generation: 66% (4/6)", False],
            ["DEBUG", "Briefing generation: 83% (5/6)", False],
            ["INFO", "Briefing generation: completed 6 items in 0.0s", False],
            ["INFO", "Generating 2 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZLA/_all.html", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZOA/_all.html", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 5 airports with valid weather (of 5 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            ["INFO", "Generated 10 weather tiles across 2 zoom levels", False],
            ["INFO", "Generation complete: 9 files in 0.0s", False],
        ],
        "stdout": "sha256:01e1c8353d2f",
    },
    "index_returns_none": {
        "result": [
            ["<root>/out/ZLA/SOCAL.html", "SOCAL"],
            ["<root>/out/ZLA/_all.html", "All Los Angeles Airports"],
            ["<root>/out/ZOA/BAY.html", "BAY"],
            ["<root>/out/ZOA/NORTH.html", "NORTH"],
            ["<root>/out/ZOA/TOWER.html", "TOWER"],
            ["<root>/out/ZOA/_all.html", "All Oakland Airports"],
            ["<root>/out/custom/CUSTOM_MIX.html", "CUSTOM_MIX"],
            ["<root>/out/custom/CUSTOM_NONE.html", "CUSTOM_NONE"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "generate_html:SOCAL",
            "generate_html:BAY",
            "generate_html:NORTH",
            "generate_html:TOWER",
            "generate_html:CUSTOM_MIX",
            "generate_html:CUSTOM_NONE",
            "generate_html:All Los Angeles Airports",
            "generate_html:All Oakland Airports",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
            "generate_weather_tiles",
        ],
        "bbox_calls": [],
        "tiles": ["sha256:bad5c942a2b4"],
        "index": ["sha256:938b658a18c3"],
        "files": {
            "out/custom/CUSTOM_MIX.html": "sha256:57b9ad9d47ca",
            "out/custom/CUSTOM_NONE.html": "sha256:010e36c9c115",
            "out/tiles/version.txt": "1791635400\n",
            "out/ZLA/_all.html": "sha256:5d11499e9533",
            "out/ZLA/SOCAL.html": "sha256:5d11499e9533",
            "out/ZOA/_all.html": "sha256:1e07d4f3fc4e",
            "out/ZOA/BAY.html": "sha256:681505cd01d7",
            "out/ZOA/NORTH.html": "sha256:40ba508870b1",
            "out/ZOA/TOWER.html": "sha256:31107bdd94b1",
            "weather/weather_cache.json": "sha256:4f63ec5aae24",
            "weather/weather_hash.txt": "1b5bec45728d3760:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Using cached weather data from 2026-10-10T12:30:00+00:00", False],
            ["INFO", "Generating 6 briefings", False],
            ["DEBUG", "Briefing generation: 16% (1/6)", False],
            ["DEBUG", "Briefing generation: 33% (2/6)", False],
            ["DEBUG", "Briefing generation: 50% (3/6)", False],
            ["DEBUG", "Briefing generation: 66% (4/6)", False],
            ["DEBUG", "Briefing generation: 83% (5/6)", False],
            ["INFO", "Briefing generation: completed 6 items in 0.0s", False],
            ["INFO", "Generating 2 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZLA/_all.html", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZOA/_all.html", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 5 airports with valid weather (of 5 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            ["INFO", "Generated 10 weather tiles across 2 zoom levels", False],
            ["INFO", "Generation complete: 8 files in 0.0s", False],
        ],
        "stdout": "sha256:b3010d1afca2",
    },
    "network_bbox_covers_every_airport": {
        "result": [
            ["<root>/out/ZOA/BAY.html", "BAY"],
            ["<root>/out/ZOA/_all.html", "All Oakland Airports"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "get_artcc_boundaries",
            "get_rate_limit_status",
            "download_vatsim_data",
            "get_atis_for_airports:['KOAK', 'KSFO', 'KSJC']",
            "generate_html:BAY",
            "generate_html:All Oakland Airports",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
            "generate_weather_tiles",
        ],
        "bbox_calls": ["ZOA:[35.5, -124.0, 39.0, -120.5]:True"],
        "tiles": ["sha256:abf98823adb0"],
        "index": ["sha256:af478bbb726b"],
        "files": {
            "out/index.html": "sha256:af478bbb726b",
            "out/tiles/version.txt": "1791635400\n",
            "out/ZOA/_all.html": "sha256:98d150ecb88a",
            "out/ZOA/BAY.html": "sha256:98d150ecb88a",
            "weather/weather_cache.json": "sha256:818fa09bdbe9",
            "weather/weather_hash.txt": "ade9bf48ee285fc1:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Fetching weather via bbox for 1 ARTCCs", False],
            ["INFO", "Weather fetch (bbox): completed 1 items in 0.0s", False],
            ["INFO", "Retrieved 3 METARs, 1 TAFs from bbox queries", False],
            ["INFO", "Fetching VATSIM ATIS data", False],
            ["INFO", "Found 3 airports with active VATSIM ATIS", False],
            ["INFO", "Total ATIS: 3 airports", False],
            [
                "DEBUG",
                "Saved weather cache to <root>/weather/weather_cache.json",
                False,
            ],
            ["INFO", "Generating 1 briefings", False],
            ["INFO", "Briefing generation: completed 1 items in 0.0s", False],
            ["INFO", "Generating 1 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZOA/_all.html", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 3 airports with valid weather (of 3 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            ["INFO", "Generated 10 weather tiles across 2 zoom levels", False],
            ["INFO", "Generation complete: 3 files in 0.0s", False],
        ],
        "stdout": "sha256:b96e68d5c8af",
    },
    "network_fetch": {
        "result": [
            ["<root>/out/ZLA/SOCAL.html", "SOCAL"],
            ["<root>/out/ZLA/_all.html", "All Los Angeles Airports"],
            ["<root>/out/ZOA/BAY.html", "BAY"],
            ["<root>/out/ZOA/NORTH.html", "NORTH"],
            ["<root>/out/ZOA/TOWER.html", "TOWER"],
            ["<root>/out/ZOA/_all.html", "All Oakland Airports"],
            ["<root>/out/custom/CUSTOM_MIX.html", "CUSTOM_MIX"],
            ["<root>/out/custom/CUSTOM_NONE.html", "CUSTOM_NONE"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "get_artcc_boundaries",
            "get_metar_batch:['1O2', '2O3', '3O4', 'KBUR', 'KLAX', 'KNOC']:3",
            "get_taf_batch:['1O2', '2O3', '3O4', 'KBUR', 'KLAX', 'KNOC']:3",
            "get_rate_limit_status",
            "download_vatsim_data",
            "get_atis_for_airports:['1O2', '2O3', '3O4', '4O5', "
            "'KBUR', 'KLAX', 'KNOC', 'KOAK', 'KSFO', 'KSJC', 'O69']",
            "get_datis_for_airports:['1O2', '2O3', '3O4', '4O5', "
            "'KBUR', 'KLAX', 'KNOC', 'KOAK', 'KSJC', 'O69']",
            "generate_html:SOCAL",
            "generate_html:BAY",
            "generate_html:NORTH",
            "generate_html:TOWER",
            "generate_html:CUSTOM_MIX",
            "generate_html:CUSTOM_NONE",
            "generate_html:All Los Angeles Airports",
            "generate_html:All Oakland Airports",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
            "generate_weather_tiles",
        ],
        "bbox_calls": [
            "ZLA:[32.5, -119.5, 35.5, -116.5]:True",
            "ZOA:[35.5, -124.0, 39.0, -120.5]:True",
        ],
        "tiles": ["sha256:b2404363b4fd"],
        "index": ["sha256:70edfaed9dbc"],
        "files": {
            "out/custom/CUSTOM_MIX.html": "sha256:e76cdaccc3da",
            "out/custom/CUSTOM_NONE.html": "sha256:4a7ea457a437",
            "out/index.html": "sha256:70edfaed9dbc",
            "out/tiles/version.txt": "1791635400\n",
            "out/ZLA/_all.html": "sha256:5586de2aa30f",
            "out/ZLA/SOCAL.html": "sha256:5586de2aa30f",
            "out/ZOA/_all.html": "sha256:8c4642eaff18",
            "out/ZOA/BAY.html": "sha256:4296016f58fd",
            "out/ZOA/NORTH.html": "sha256:1faae0e33620",
            "out/ZOA/TOWER.html": "sha256:31107bdd94b1",
            "weather/weather_cache.json": "sha256:6d1e857751e5",
            "weather/weather_hash.txt": "29145646a8ca52b7:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Fetching weather via bbox for 2 ARTCCs", False],
            ["WARNING", "Failed to fetch weather for ZLA: bbox down", False],
            ["DEBUG", "Weather fetch (bbox): 50% (1/2)", False],
            ["INFO", "Weather fetch (bbox): completed 2 items in 0.0s", False],
            ["INFO", "Retrieved 3 METARs, 1 TAFs from bbox queries", False],
            [
                "INFO",
                "Fetching 6 airports individually, skipping 2 without likely METAR",
                False,
            ],
            ["WARNING", "Rate limiting active: backoff=12.5s, errors=3", False],
            ["INFO", "Fetching VATSIM ATIS data", False],
            ["INFO", "Found 1 airports with active VATSIM ATIS", False],
            ["INFO", "Fetching RW D-ATIS for 10 airports", False],
            ["INFO", "Found 1 airports with RW D-ATIS", False],
            ["INFO", "Total ATIS: 2 airports", False],
            [
                "DEBUG",
                "Saved weather cache to <root>/weather/weather_cache.json",
                False,
            ],
            ["INFO", "Generating 6 briefings", False],
            ["DEBUG", "Briefing generation: 16% (1/6)", False],
            ["DEBUG", "Briefing generation: 33% (2/6)", False],
            ["DEBUG", "Briefing generation: 50% (3/6)", False],
            ["DEBUG", "Briefing generation: 66% (4/6)", False],
            ["DEBUG", "Briefing generation: 83% (5/6)", False],
            ["INFO", "Briefing generation: completed 6 items in 0.0s", False],
            ["INFO", "Generating 2 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZLA/_all.html", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZOA/_all.html", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 8 airports with valid weather (of 8 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            ["INFO", "Generated 10 weather tiles across 2 zoom levels", False],
            ["INFO", "Generation complete: 9 files in 0.0s", False],
        ],
        "stdout": "sha256:81ca663fab44",
    },
    "network_fetch_stale_cache": {
        "result": [
            ["<root>/out/ZLA/SOCAL.html", "SOCAL"],
            ["<root>/out/ZLA/_all.html", "All Los Angeles Airports"],
            ["<root>/out/ZOA/BAY.html", "BAY"],
            ["<root>/out/ZOA/NORTH.html", "NORTH"],
            ["<root>/out/ZOA/TOWER.html", "TOWER"],
            ["<root>/out/ZOA/_all.html", "All Oakland Airports"],
            ["<root>/out/custom/CUSTOM_MIX.html", "CUSTOM_MIX"],
            ["<root>/out/custom/CUSTOM_NONE.html", "CUSTOM_NONE"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "get_artcc_boundaries",
            "get_metar_batch:['1O2', '2O3', '3O4', 'KNOC']:3",
            "get_taf_batch:['1O2', '2O3', '3O4', 'KNOC']:3",
            "get_rate_limit_status",
            "download_vatsim_data",
            "get_datis_for_airports:['1O2', '2O3', '3O4', "
            "'4O5', 'KBUR', 'KLAX', 'KNOC', 'KOAK', "
            "'KSFO', 'KSJC', 'O69']",
            "generate_html:SOCAL",
            "generate_html:BAY",
            "generate_html:NORTH",
            "generate_html:TOWER",
            "generate_html:CUSTOM_MIX",
            "generate_html:CUSTOM_NONE",
            "generate_html:All Los Angeles Airports",
            "generate_html:All Oakland Airports",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
            "generate_weather_tiles",
        ],
        "bbox_calls": [
            "ZLA:[32.5, -119.5, 35.5, -116.5]:True",
            "ZOA:[35.5, -124.0, 39.0, -120.5]:True",
        ],
        "tiles": ["sha256:c539b25c2b66"],
        "index": ["sha256:7a4e4f43fc9f"],
        "files": {
            "out/custom/CUSTOM_MIX.html": "sha256:924bdc3d7a34",
            "out/custom/CUSTOM_NONE.html": "sha256:4a7ea457a437",
            "out/index.html": "sha256:7a4e4f43fc9f",
            "out/tiles/version.txt": "1791635400\n",
            "out/ZLA/_all.html": "sha256:17df7d8e849c",
            "out/ZLA/SOCAL.html": "sha256:17df7d8e849c",
            "out/ZOA/_all.html": "sha256:2c0985c10c79",
            "out/ZOA/BAY.html": "sha256:85ce49c2707c",
            "out/ZOA/NORTH.html": "sha256:1faae0e33620",
            "out/ZOA/TOWER.html": "sha256:31107bdd94b1",
            "weather/weather_cache.json": "sha256:69b0cbecd8ed",
            "weather/weather_hash.txt": "ac720405c9c264d9:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["DEBUG", "Cache expired (3600s old, TTL=240s)", False],
            ["INFO", "Fetching weather via bbox for 2 ARTCCs", False],
            ["DEBUG", "Weather fetch (bbox): 50% (1/2)", False],
            ["INFO", "Weather fetch (bbox): completed 2 items in 0.0s", False],
            ["INFO", "Retrieved 5 METARs, 1 TAFs from bbox queries", False],
            [
                "INFO",
                "Fetching 4 airports individually, skipping 2 without likely METAR",
                False,
            ],
            ["INFO", "Rate limit recovery: 2 errors, recovered", False],
            ["INFO", "Fetching VATSIM ATIS data", False],
            ["INFO", "Found 0 airports with active VATSIM ATIS", False],
            ["INFO", "Fetching RW D-ATIS for 11 airports", False],
            ["INFO", "Found 1 airports with RW D-ATIS", False],
            ["INFO", "Total ATIS: 1 airports", False],
            [
                "DEBUG",
                "Saved weather cache to <root>/weather/weather_cache.json",
                False,
            ],
            ["INFO", "Generating 6 briefings", False],
            ["DEBUG", "Briefing generation: 16% (1/6)", False],
            ["DEBUG", "Briefing generation: 33% (2/6)", False],
            ["DEBUG", "Briefing generation: 50% (3/6)", False],
            ["DEBUG", "Briefing generation: 66% (4/6)", False],
            ["DEBUG", "Briefing generation: 83% (5/6)", False],
            ["INFO", "Briefing generation: completed 6 items in 0.0s", False],
            ["INFO", "Generating 2 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZLA/_all.html", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZOA/_all.html", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 7 airports with valid weather (of 7 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            ["INFO", "Generated 10 weather tiles across 2 zoom levels", False],
            ["INFO", "Generation complete: 9 files in 0.0s", False],
        ],
        "stdout": "sha256:70e7eaae21a3",
    },
    "network_only_unlikely_airports": {
        "result": [
            ["<root>/out/custom/CUSTOM_UNLIKELY.html", "CUSTOM_UNLIKELY"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "get_rate_limit_status",
            "download_vatsim_data",
            "get_atis_for_airports:['4O5', 'O69']",
            "get_datis_for_airports:['4O5', 'O69']",
            "generate_html:CUSTOM_UNLIKELY",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
        ],
        "bbox_calls": [],
        "tiles": [],
        "index": ["sha256:4932fcf3005b"],
        "files": {
            "out/custom/CUSTOM_UNLIKELY.html": "sha256:8ce4b0e72b0e",
            "out/index.html": "sha256:4932fcf3005b",
            "weather/weather_cache.json": '{"atis": '
            "{}, "
            '"metars": '
            "{}, "
            '"tafs": '
            "{}, "
            '"timestamp": '
            '"<written>"}',
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Skipping 2 airports unlikely to have METAR stations", False],
            ["INFO", "Fetching VATSIM ATIS data", False],
            ["INFO", "Found 0 airports with active VATSIM ATIS", False],
            ["INFO", "Fetching RW D-ATIS for 2 airports", False],
            ["INFO", "Found 0 airports with RW D-ATIS", False],
            ["INFO", "Total ATIS: 0 airports", False],
            [
                "DEBUG",
                "Saved weather cache to <root>/weather/weather_cache.json",
                False,
            ],
            ["INFO", "Generating 1 briefings", False],
            ["INFO", "Briefing generation: completed 1 items in 0.0s", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 0 airports with valid weather (of 0 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            [
                "WARNING",
                "No airport weather data collected - skipping tile generation",
                False,
            ],
            ["INFO", "Generation complete: 2 files in 0.0s", False],
        ],
        "stdout": "sha256:6be6b7ebe6ef",
    },
    "network_returns_no_weather": {
        "result": [
            ["<root>/out/ZLA/SOCAL.html", "SOCAL"],
            ["<root>/out/ZLA/_all.html", "All Los Angeles Airports"],
            ["<root>/out/ZOA/BAY.html", "BAY"],
            ["<root>/out/ZOA/NORTH.html", "NORTH"],
            ["<root>/out/ZOA/TOWER.html", "TOWER"],
            ["<root>/out/ZOA/_all.html", "All Oakland Airports"],
            ["<root>/out/custom/CUSTOM_MIX.html", "CUSTOM_MIX"],
            ["<root>/out/custom/CUSTOM_NONE.html", "CUSTOM_NONE"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "get_artcc_boundaries",
            "get_metar_batch:['1O2', '2O3', '3O4', "
            "'KBUR', 'KLAX', 'KNOC', 'KOAK', 'KSFO', "
            "'KSJC']:3",
            "get_taf_batch:['1O2', '2O3', '3O4', 'KBUR', "
            "'KLAX', 'KNOC', 'KOAK', 'KSFO', 'KSJC']:3",
            "get_rate_limit_status",
            "download_vatsim_data",
            "get_atis_for_airports:['1O2', '2O3', '3O4', "
            "'4O5', 'KBUR', 'KLAX', 'KNOC', 'KOAK', "
            "'KSFO', 'KSJC', 'O69']",
            "get_datis_for_airports:['1O2', '2O3', "
            "'3O4', '4O5', 'KBUR', 'KLAX', 'KNOC', "
            "'KOAK', 'KSJC', 'O69']",
            "generate_html:SOCAL",
            "generate_html:BAY",
            "generate_html:NORTH",
            "generate_html:TOWER",
            "generate_html:CUSTOM_MIX",
            "generate_html:CUSTOM_NONE",
            "generate_html:All Los Angeles Airports",
            "generate_html:All Oakland Airports",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
        ],
        "bbox_calls": [
            "ZLA:[32.5, -119.5, 35.5, -116.5]:True",
            "ZOA:[35.5, -124.0, 39.0, -120.5]:True",
        ],
        "tiles": [],
        "index": ["sha256:75e642585d70"],
        "files": {
            "out/custom/CUSTOM_MIX.html": "sha256:9d02583656e2",
            "out/custom/CUSTOM_NONE.html": "sha256:010e36c9c115",
            "out/index.html": "sha256:75e642585d70",
            "out/ZLA/_all.html": "sha256:50b5b3a4fdd7",
            "out/ZLA/SOCAL.html": "sha256:50b5b3a4fdd7",
            "out/ZOA/_all.html": "sha256:9c80efe393e0",
            "out/ZOA/BAY.html": "sha256:1aeaaccc3637",
            "out/ZOA/NORTH.html": "sha256:1d8723e58cd6",
            "out/ZOA/TOWER.html": "sha256:823d63ae1811",
            "weather/weather_cache.json": "sha256:9f23dd80e798",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Fetching weather via bbox for 2 ARTCCs", False],
            ["DEBUG", "Weather fetch (bbox): 50% (1/2)", False],
            ["INFO", "Weather fetch (bbox): completed 2 items in 0.0s", False],
            ["INFO", "Retrieved 0 METARs, 0 TAFs from bbox queries", False],
            [
                "INFO",
                "Fetching 9 airports individually, skipping 2 without likely METAR",
                False,
            ],
            ["INFO", "Fetching VATSIM ATIS data", False],
            ["INFO", "Found 1 airports with active VATSIM ATIS", False],
            ["INFO", "Fetching RW D-ATIS for 10 airports", False],
            ["INFO", "Found 1 airports with RW D-ATIS", False],
            ["INFO", "Total ATIS: 2 airports", False],
            [
                "DEBUG",
                "Saved weather cache to <root>/weather/weather_cache.json",
                False,
            ],
            ["INFO", "Generating 6 briefings", False],
            ["DEBUG", "Briefing generation: 16% (1/6)", False],
            ["DEBUG", "Briefing generation: 33% (2/6)", False],
            ["DEBUG", "Briefing generation: 50% (3/6)", False],
            ["DEBUG", "Briefing generation: 66% (4/6)", False],
            ["DEBUG", "Briefing generation: 83% (5/6)", False],
            ["INFO", "Briefing generation: completed 6 items in 0.0s", False],
            ["INFO", "Generating 2 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZLA/_all.html", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZOA/_all.html", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 0 airports with valid weather (of 0 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            [
                "WARNING",
                "No airport weather data collected - skipping tile generation",
                False,
            ],
            ["INFO", "Generation complete: 9 files in 0.0s", False],
        ],
        "stdout": "sha256:d3b89c619e8e",
    },
    "network_without_artcc_bboxes": {
        "result": [
            ["<root>/out/ZLA/SOCAL.html", "SOCAL"],
            ["<root>/out/ZLA/_all.html", "All Los Angeles Airports"],
            ["<root>/out/ZOA/BAY.html", "BAY"],
            ["<root>/out/ZOA/NORTH.html", "NORTH"],
            ["<root>/out/ZOA/TOWER.html", "TOWER"],
            ["<root>/out/ZOA/_all.html", "All Oakland Airports"],
            ["<root>/out/custom/CUSTOM_MIX.html", "CUSTOM_MIX"],
            ["<root>/out/custom/CUSTOM_NONE.html", "CUSTOM_NONE"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "load_custom_groupings",
            "get_artcc_boundaries",
            "get_metar_batch:['1O2', '2O3', '3O4', "
            "'KBUR', 'KLAX', 'KNOC', 'KOAK', 'KSFO', "
            "'KSJC']:3",
            "get_taf_batch:['1O2', '2O3', '3O4', "
            "'KBUR', 'KLAX', 'KNOC', 'KOAK', 'KSFO', "
            "'KSJC']:3",
            "get_rate_limit_status",
            "download_vatsim_data",
            "get_atis_for_airports:['1O2', '2O3', "
            "'3O4', '4O5', 'KBUR', 'KLAX', 'KNOC', "
            "'KOAK', 'KSFO', 'KSJC', 'O69']",
            "get_datis_for_airports:['1O2', '2O3', "
            "'3O4', '4O5', 'KBUR', 'KLAX', 'KNOC', "
            "'KOAK', 'KSJC', 'O69']",
            "generate_html:SOCAL",
            "generate_html:BAY",
            "generate_html:NORTH",
            "generate_html:TOWER",
            "generate_html:CUSTOM_MIX",
            "generate_html:CUSTOM_NONE",
            "generate_html:All Los Angeles Airports",
            "generate_html:All Oakland Airports",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
            "generate_weather_tiles",
        ],
        "bbox_calls": [],
        "tiles": ["sha256:673c0d22fed9"],
        "index": ["sha256:e24a51e02dab"],
        "files": {
            "out/custom/CUSTOM_MIX.html": "sha256:119fbfec2a54",
            "out/custom/CUSTOM_NONE.html": "sha256:4a7ea457a437",
            "out/index.html": "sha256:e24a51e02dab",
            "out/tiles/version.txt": "1791635400\n",
            "out/ZLA/_all.html": "sha256:5586de2aa30f",
            "out/ZLA/SOCAL.html": "sha256:5586de2aa30f",
            "out/ZOA/_all.html": "sha256:8cfcd9a5f5e2",
            "out/ZOA/BAY.html": "sha256:4db7b671bf9c",
            "out/ZOA/NORTH.html": "sha256:1faae0e33620",
            "out/ZOA/TOWER.html": "sha256:f917707f03b4",
            "weather/weather_cache.json": "sha256:dd0dd245ce72",
            "weather/weather_hash.txt": "c1ec2c1147ce1248:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            [
                "INFO",
                "Fetching 9 airports individually, skipping 2 without likely METAR",
                False,
            ],
            ["INFO", "Fetching VATSIM ATIS data", False],
            ["INFO", "Found 1 airports with active VATSIM ATIS", False],
            ["INFO", "Fetching RW D-ATIS for 10 airports", False],
            ["INFO", "Found 1 airports with RW D-ATIS", False],
            ["INFO", "Total ATIS: 2 airports", False],
            [
                "DEBUG",
                "Saved weather cache to <root>/weather/weather_cache.json",
                False,
            ],
            ["INFO", "Generating 6 briefings", False],
            ["DEBUG", "Briefing generation: 16% (1/6)", False],
            ["DEBUG", "Briefing generation: 33% (2/6)", False],
            ["DEBUG", "Briefing generation: 50% (3/6)", False],
            ["DEBUG", "Briefing generation: 66% (4/6)", False],
            ["DEBUG", "Briefing generation: 83% (5/6)", False],
            ["INFO", "Briefing generation: completed 6 items in 0.0s", False],
            ["INFO", "Generating 2 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZLA/_all.html", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZOA/_all.html", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 8 airports with valid weather (of 8 total)", False],
            ["INFO", "Found 0 ARTCC boundaries, 22 in CONUS", False],
            ["INFO", "Generated 10 weather tiles across 2 zoom levels", False],
            ["INFO", "Generation complete: 9 files in 0.0s", False],
        ],
        "stdout": "sha256:fa190c060322",
    },
    "no_groupings": {
        "result": [["<root>/out/index.html", "Index"]],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
            "generate_weather_tiles",
        ],
        "bbox_calls": [],
        "tiles": ["sha256:bad5c942a2b4"],
        "index": ["sha256:4f53cda18c2b"],
        "files": {
            "out/index.html": "[]",
            "out/tiles/version.txt": "1791635400\n",
            "weather/weather_cache.json": "sha256:4f63ec5aae24",
            "weather/weather_hash.txt": "1b5bec45728d3760:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Using cached weather data from 2026-10-10T12:30:00+00:00", False],
            ["INFO", "Generating 0 briefings", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 5 airports with valid weather (of 5 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            ["INFO", "Generated 10 weather tiles across 2 zoom levels", False],
            ["INFO", "Generation complete: 1 files in 0.0s", False],
        ],
        "stdout": "sha256:58066e1a6e82",
    },
    "real_briefing_html": {
        "result": [
            ["<root>/out/ZOA/BAY.html", "BAY"],
            ["<root>/out/ZOA/NORTH.html", "NORTH"],
            ["<root>/out/ZOA/TOWER.html", "TOWER"],
            ["<root>/out/ZOA/_all.html", "All Oakland Airports"],
            ["<root>/out/index.html", "Index"],
        ],
        "calls": [
            "load_unified_airport_data:APT_BASE.csv:airports.json:iata-icao.csv",
            "AirportDisambiguator:airports.json:['lazy_load', "
            "'names_csv_path', 'unified_data']",
            "load_all_groupings",
            "generate_html:BAY",
            "generate_html:NORTH",
            "generate_html:TOWER",
            "generate_html:All Oakland Airports",
            "generate_index_page:1791635400",
            "get_artcc_boundaries",
            "generate_weather_tiles",
        ],
        "bbox_calls": [],
        "tiles": ["sha256:bad5c942a2b4"],
        "index": ["sha256:c71e2d278748"],
        "files": {
            "out/index.html": "sha256:c71e2d278748",
            "out/tiles/version.txt": "1791635400\n",
            "out/ZOA/_all.html": "sha256:7770e79795f5",
            "out/ZOA/BAY.html": "sha256:b8f5939d0255",
            "out/ZOA/NORTH.html": "sha256:ce866c954367",
            "out/ZOA/TOWER.html": "sha256:bf851be30c7a",
            "weather/weather_cache.json": "sha256:4f63ec5aae24",
            "weather/weather_hash.txt": "1b5bec45728d3760:c0dec0dec0dec0de",
        },
        "logs": [
            ["INFO", "Starting weather briefing generation", False],
            ["INFO", "Using cached weather data from 2026-10-10T12:30:00+00:00", False],
            ["INFO", "Generating 3 briefings", False],
            ["DEBUG", "Briefing generation: 33% (1/3)", False],
            ["DEBUG", "Briefing generation: 66% (2/3)", False],
            ["INFO", "Briefing generation: completed 3 items in 0.0s", False],
            ["INFO", "Generating 1 ARTCC-wide briefings", False],
            ["DEBUG", "Generated ARTCC briefing: <root>/out/ZOA/_all.html", False],
            ["INFO", "Generating weather overlay tiles", False],
            ["INFO", "Collected 5 airports with valid weather (of 5 total)", False],
            ["INFO", "Found 3 ARTCC boundaries, 22 in CONUS", False],
            ["INFO", "Generated 10 weather tiles across 2 zoom levels", False],
            ["INFO", "Generation complete: 5 files in 0.0s", False],
        ],
        "stdout": "sha256:8ed0c74d1723",
    },
}

STAGE_MATRIX_GOLDEN: dict[str, Any] = {
    "briefings,index|fresh": "sha256:c7a32913c6a6",
    "briefings,index|none": "sha256:55d6e1ae361c",
    "briefings,tiles,index|fresh": "sha256:c439ea24de57",
    "briefings,tiles,index|none": "sha256:db4ecfd52d0d",
    "briefings,tiles|fresh": "sha256:7879d39e0bfa",
    "briefings,tiles|none": "sha256:54fbb7fc8f2a",
    "briefings|fresh": "sha256:9d2549e6932b",
    "briefings|none": "sha256:295e0dba3bb9",
    "index|fresh": "sha256:a2efd39a8323",
    "index|none": "sha256:2ea4fadbb7c6",
    "tiles,index|fresh": "sha256:84acf06e230c",
    "tiles,index|none": "sha256:9704d8570230",
    "tiles|fresh": "sha256:f1e4db195579",
    "tiles|none": "sha256:e541cd1f497b",
    "weather,briefings,index|fresh": "sha256:c7a32913c6a6",
    "weather,briefings,index|none": "sha256:40a4a89a744d",
    "weather,briefings,tiles,index|fresh": "sha256:9a4cdcdae5a7",
    "weather,briefings,tiles,index|none": "sha256:62611fe28f4d",
    "weather,briefings,tiles|fresh": "sha256:fd2ea7138fd1",
    "weather,briefings,tiles|none": "sha256:eed1f682713b",
    "weather,briefings|fresh": "sha256:9d2549e6932b",
    "weather,briefings|none": "sha256:594e52e0f651",
    "weather,index|fresh": "sha256:a2efd39a8323",
    "weather,index|none": "sha256:5d27e8d07315",
    "weather,tiles,index|fresh": "sha256:b2844f108399",
    "weather,tiles,index|none": "sha256:7197ed9892a1",
    "weather,tiles|fresh": "sha256:84995b6e7907",
    "weather,tiles|none": "sha256:4e30c320ca31",
    "weather|fresh": "sha256:2d668fa8c1be",
    "weather|none": "sha256:60847a12b708",
}

CARD_GOLDEN: dict[str, Any] = {
    "atis_none": "KSFO - KSFO Field // [white bold]UNK[/white bold]\n  Ceiling: CLR",
    "empty": "KXXX - KXXX // [white bold]UNK[/white bold]\n  Ceiling: CLR",
    "full": "KSJC - KSJC Field [#999999](1153Z)[/#999999] // [#77bbff bold]MVFR[/#77bbff "
    "bold] [#ff9900 bold]SPECI[/#ff9900 bold]\n"
    "  Ceiling: BKN025 | Vis: 5SM | Wind: 310@08G18\n"
    "  Weather: Haze\n"
    "  [#ff9999 bold]▼[/#ff9999 bold] TAF FM 101800 [#999999](in  "
    "5h)[/#999999]      : [#ff9999 bold] IFR[/#ff9999 bold] -   3SM | BKN008 | "
    "300@15G25 | Light Rain\n"
    "  [#ff9999 bold]▼[/#ff9999 bold] TAF TEMPO 1020/1024 [#999999](in  "
    "7h)[/#999999]: [#ffaaff bold]LIFR[/#ffaaff bold] - 0.5SM | OVC004 "
    "|           | Thunderstorm Rain, Mist\n"
    "  [#aaaaaa][cyan]DEP:[/cyan] SFO ATIS INFO [cyan bold]C[/cyan bold] 1156Z. "
    "[yellow]ILS RWY 28L APCH IN USE[/yellow], [yellow]VISUAL APCH [/yellow]IN "
    "USE. [yellow]LNDG RWY 28L, 28R[/yellow]. [yellow]DEPG RWY 1L, 1R[/yellow]. "
    "NOTAMS... TWY C CLSD. ADVS YOU HAVE INFO [cyan bold]C[/cyan "
    "bold].[/#aaaaaa]\n"
    "  [#aaaaaa][#ffaa00](RW)[/#ffaa00] [cyan]ARR:[/cyan] LAX ARR INFO [cyan "
    "bold]D[/cyan bold] 2353Z. [yellow]ILS RWY 24R APCH IN USE[/yellow]. "
    "[yellow]LNDG RWY 24R, 25L[/yellow].[/#aaaaaa]\n"
    "  [#aaaaaa][#ffaa00](RW)[/#ffaa00] SFO ATIS INFO C 1156Z. [yellow]ILS RWY "
    "28L APCH IN USE[/yellow], [yellow]VISUAL APCH [/yellow]IN USE. [yellow]LNDG "
    "RWY 28L, 28R[/yellow]. [yellow]DEPG RWY 1L, 1R[/yellow]. NOTAMS... TWY C "
    "CLSD. ADVS YOU HAVE INFO C.[/#aaaaaa]",
    "obs_time_missing": "KSFO - KSFO Field // [#66ff66 bold]VFR[/#66ff66 bold]\n"
    "  Ceiling: CLR",
    "obs_time_not_requested": "KSFO - KSFO Field // [white bold]UNK[/white bold]\n"
    "  Ceiling: CLR",
    "obs_time_short": "KSFO - KSFO Field [#999999](1153)[/#999999] // [white "
    "bold]UNK[/white bold]\n"
    "  Ceiling: CLR",
    "taf_mixed_widths": "KSFO - KSFO Field // [#ff9999 bold]IFR[/#ff9999 bold]\n"
    "  Ceiling: CLR\n"
    "  [#ff9999 bold]▼[/#ff9999 bold] TAF FM 101800 [#999999](in  "
    "5h)[/#999999]      : [#77bbff bold]MVFR[/#77bbff bold] -      | "
    "VRB03\n"
    "  [#66ff66 bold]▲[/#66ff66 bold] TAF TEMPO 1014/1016 "
    "[#999999](in  1h)[/#999999]: [#66ff66 bold] VFR[/#66ff66 bold] - "
    "P6SM |",
    "taf_no_details": "KSFO - KSFO Field // [white bold]UNK[/white bold]\n"
    "  Ceiling: CLR\n"
    "  [#66ff66 bold]▲[/#66ff66 bold] TAF BECMG : [white "
    "bold]UNK[/white bold]",
    "taf_only_wx": "KSFO - KSFO Field // [white bold]UNK[/white bold]\n"
    "  Ceiling: CLR\n"
    "  [#66ff66 bold]▲[/#66ff66 bold] TAF FM 101900 [#999999](in  "
    "6h)[/#999999]: [white bold]UNK[/white bold] - Snow",
    "taf_unparsable_time": "KSFO - KSFO Field // [white bold]UNK[/white bold]\n"
    "  Ceiling: CLR\n"
    "  [#66ff66 bold]▲[/#66ff66 bold] TAF PROB30 garbage: [#ff9999 "
    "bold]IFR[/#ff9999 bold] - 2SM | OVC009",
    "unknown_category": "KSFO - KSFO Field // [white bold]XYZ[/white bold]\n"
    "  Ceiling: CLR | Vis: 10SM",
}

HTML_GOLDEN: dict[str, Any] = {
    "bay": "sha256:c0adeb78d64b",
    "empty": "sha256:04dfb995e1d1",
    "empty_area": "sha256:b4eb9c50677e",
    "no_obs_times": "sha256:e43076b52ab9",
    "unknown_only": "sha256:8f3da8965cfb",
}

POINTS_GOLDEN: dict[str, Any] = {
    "empty": [],
    "fetched": [
        {
            "icao": "KSFO",
            "lat": 37.62,
            "lon": -122.38,
            "category": "VFR",
            "visibility": "10SM",
            "ceiling": None,
            "wind": "28010KT",
            "phenomena": [],
            "taf_changes": [
                {
                    "type": "FM",
                    "time_str": "101800",
                    "category": "IFR",
                    "trend": "worsening",
                    "visibility": "3SM",
                    "ceiling": "BKN008",
                    "wind": "30015G25KT",
                    "phenomena": ["Light Rain"],
                },
                {
                    "type": "TEMPO",
                    "time_str": "1020/1024",
                    "category": "LIFR",
                    "trend": "worsening",
                    "visibility": "1SM",
                    "ceiling": "OVC004",
                    "wind": None,
                    "phenomena": ["Thunderstorm", "Rain"],
                },
            ],
        },
        {
            "icao": "KOAK",
            "lat": 37.72,
            "lon": -122.22,
            "category": "IFR",
            "visibility": "2SM",
            "ceiling": "OVC006",
            "wind": "00000KT",
            "phenomena": ["Mist"],
            "taf_changes": [],
        },
        {
            "icao": "KSJC",
            "lat": 37.36,
            "lon": -121.93,
            "category": "MVFR",
            "visibility": "5SM",
            "ceiling": "BKN025",
            "wind": "31008G18KT",
            "phenomena": ["Haze"],
            "taf_changes": [],
        },
        {
            "icao": "KLAX",
            "lat": 33.94,
            "lon": -118.41,
            "category": "LIFR",
            "visibility": "1/2SM",
            "ceiling": "OVC002",
            "wind": "25005KT",
            "phenomena": ["Fog"],
            "taf_changes": [],
        },
        {
            "icao": "KBUR",
            "lat": 34.2,
            "lon": -118.36,
            "category": "UNK",
            "visibility": None,
            "ceiling": None,
            "wind": None,
            "phenomena": [],
            "taf_changes": [],
        },
    ],
    "mixed": [
        {
            "icao": "KSFO",
            "lat": 37.62,
            "lon": -122.38,
            "category": "IFR",
            "visibility": "2SM",
            "ceiling": "OVC006",
            "wind": "280@10",
            "phenomena": ["Mist"],
            "taf_changes": [
                {
                    "type": "FM",
                    "time_str": "101800",
                    "category": "IFR",
                    "trend": "worsening",
                    "visibility": "3SM",
                    "ceiling": "BKN008",
                    "wind": "300@15G25",
                    "phenomena": ["Light Rain"],
                },
                {
                    "type": "TEMPO",
                    "time_str": "1020/1024",
                    "category": "LIFR",
                    "trend": "worsening",
                    "visibility": "0.5SM",
                    "ceiling": "OVC004",
                    "wind": None,
                    "phenomena": ["Thunderstorm Rain", "Mist"],
                },
            ],
        },
        {
            "icao": "KOAK",
            "lat": 37.72,
            "lon": -122.22,
            "category": "UNK",
            "visibility": None,
            "ceiling": None,
            "wind": None,
            "phenomena": [],
            "taf_changes": [
                {
                    "type": "",
                    "time_str": "",
                    "category": "UNK",
                    "trend": "stable",
                    "visibility": "3SM",
                    "ceiling": None,
                    "wind": None,
                    "phenomena": [],
                },
                {
                    "type": "",
                    "time_str": "",
                    "category": "UNK",
                    "trend": "stable",
                    "visibility": "1.5SM",
                    "ceiling": None,
                    "wind": None,
                    "phenomena": [],
                },
            ],
        },
        {
            "icao": "KSJC",
            "lat": 37.36,
            "lon": -121.93,
            "category": "UNK",
            "visibility": None,
            "ceiling": None,
            "wind": None,
            "phenomena": [],
            "taf_changes": [
                {
                    "type": "",
                    "time_str": "",
                    "category": "UNK",
                    "trend": "stable",
                    "visibility": None,
                    "ceiling": None,
                    "wind": None,
                    "phenomena": [],
                },
                {
                    "type": "",
                    "time_str": "",
                    "category": "UNK",
                    "trend": "stable",
                    "visibility": "P6SM",
                    "ceiling": None,
                    "wind": None,
                    "phenomena": [],
                },
            ],
        },
    ],
}
