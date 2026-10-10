"""METAR cache behaviour: bbox freshness, "no METAR" negatives and their limits.

The network is mocked at ``urllib.request.urlopen``; every requested URL is recorded so a
test can count requests per endpoint. Time is driven by a fake clock patched over
``backend.data.weather.datetime``.
"""

import io
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone, tzinfo
from email.message import Message

import pytest

from backend.cache import manager
from backend.data import weather

AIRPORTS = {
    "KJFK": {"latitude": 40.64, "longitude": -73.78},
    "KBOS": {"latitude": 42.36, "longitude": -71.01},
    "KLAX": {"latitude": 33.94, "longitude": -118.41},
    "KSFO": {"latitude": 37.62, "longitude": -122.37},
    "KXYZ": {"latitude": 41.00, "longitude": -73.00},
}

STATIONS = {
    icao: (
        data["latitude"],
        data["longitude"],
        f"{icao} 101251Z 31012KT 10SM FEW250 18/06 A3012",
    )
    for icao, data in AIRPORTS.items()
    if icao != "KXYZ"
}

STALE_METAR = "KXYZ 101151Z 27005KT 10SM CLR 15/05 A2992"


class _Response:
    def __init__(self, body: bytes):
        self._body = body
        self.status = 200

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url, code, f"HTTP {code}", hdrs=Message(), fp=io.BytesIO()
    )


def _parse_bbox(url: str) -> tuple[float, ...]:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return tuple(float(v) for v in query["bbox"][0].split(","))


class FakeNet:
    """Routes aviationweather bbox, aviationweather ids= and metar.vatsim.net requests."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.stations: dict[str, tuple[float, float, str]] = dict(STATIONS)
        self.bbox: Callable[[str], bytes] = self._bbox_from_stations
        self.ids: Callable[[str], bytes] = lambda url: b""
        self.vatsim: Callable[[str], bytes] = lambda url: b""

    def _bbox_from_stations(self, url: str) -> bytes:
        min_lat, min_lon, max_lat, max_lon = _parse_bbox(url)
        entries = [
            {"icaoId": icao, "rawOb": raw}
            for icao, (lat, lon, raw) in self.stations.items()
            if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
        ]
        return json.dumps(entries).encode()

    def urlopen(
        self, req: urllib.request.Request, timeout: float | None = None
    ) -> _Response:
        url = req.full_url
        self.urls.append(url)
        if "metar.vatsim.net" in url:
            return _Response(self.vatsim(url))
        if "bbox=" in url:
            return _Response(self.bbox(url))
        if "ids=" in url:
            return _Response(self.ids(url))
        raise AssertionError(f"unexpected URL {url}")

    def count(self, marker: str) -> int:
        return sum(1 for url in self.urls if marker in url)

    def bbox_urls(self) -> list[str]:
        return [url for url in self.urls if "bbox=" in url]


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(timezone.utc)

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _clear_all() -> None:
    manager.clear_metar_cache()
    weather.clear_weather_caches()
    weather.reset_rate_limit_state()


@pytest.fixture(autouse=True)
def clean_caches():
    _clear_all()
    yield
    _clear_all()


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> FakeNet:
    fake = FakeNet()
    monkeypatch.setattr(weather.urllib.request, "urlopen", fake.urlopen)
    return fake


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake_clock = Clock()

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return fake_clock.now

    monkeypatch.setattr(weather, "datetime", FakeDatetime)
    return fake_clock


def _age_entry(icao: str, seconds: float) -> None:
    metar_data_cache, _blacklist = manager.get_metar_cache()
    metar_data_cache[icao]["timestamp"] -= timedelta(seconds=seconds)


def test_second_bbox_call_within_60s_requests_nothing(
    net: FakeNet, clock: Clock
) -> None:
    airports = ["KJFK", "KBOS", "KLAX", "KSFO"]
    weather.get_weather_for_airports_bbox(airports, AIRPORTS)
    first = len(net.urls)
    assert first == 2

    clock.advance(30)
    result = weather.get_weather_for_airports_bbox(airports, AIRPORTS)

    assert len(net.urls) == first
    assert result["KJFK"] == STATIONS["KJFK"][2]


def test_bbox_refetches_only_boxes_with_a_stale_airport(
    net: FakeNet, clock: Clock
) -> None:
    airports = ["KJFK", "KBOS", "KLAX", "KSFO"]
    weather.get_weather_for_airports_bbox(airports, AIRPORTS)
    assert len(net.bbox_urls()) == 2

    _age_entry("KJFK", 61)
    weather.get_weather_for_airports_bbox(airports, AIRPORTS)

    refetched = net.bbox_urls()[2:]
    assert len(refetched) == 1
    min_lat, min_lon, max_lat, max_lon = _parse_bbox(refetched[0])
    assert min_lat <= AIRPORTS["KJFK"]["latitude"] <= max_lat
    assert min_lon <= AIRPORTS["KJFK"]["longitude"] <= max_lon
    assert not min_lon <= AIRPORTS["KLAX"]["longitude"] <= max_lon


def test_clean_empty_is_fetched_once_for_wind_and_altimeter(
    net: FakeNet, clock: Clock
) -> None:
    assert weather.get_metar("KQQQ") == ""
    assert weather.get_wind_from_metar("KQQQ") == ""
    assert weather.get_altimeter_setting("KQQQ") is None

    assert net.count("ids=KQQQ") == 1
    assert net.count("metar.vatsim.net/KQQQ") == 1


def test_clean_empty_is_refetched_after_60s(net: FakeNet, clock: Clock) -> None:
    weather.get_metar("KQQQ")
    clock.advance(59)
    weather.get_metar("KQQQ")
    assert net.count("ids=KQQQ") == 1

    clock.advance(2)
    weather.get_metar("KQQQ")
    assert net.count("ids=KQQQ") == 2


def test_timeout_and_429_are_not_cached(net: FakeNet, clock: Clock) -> None:
    def timeout(url: str) -> bytes:
        raise TimeoutError("timed out")

    net.ids = timeout
    weather.get_metar("KQQQ")
    weather.get_metar("KQQQ")
    assert net.count("ids=KQQQ") == 2

    def too_many(url: str) -> bytes:
        raise _http_error(url, 429)

    net.ids = too_many
    weather.get_metar("KQQQ")
    weather.reset_rate_limit_state()
    weather.get_metar("KQQQ")
    assert net.count("ids=KQQQ") == 4


def test_vatsim_failure_after_clean_primary_empty_is_not_cached(
    net: FakeNet, clock: Clock
) -> None:
    def timeout(url: str) -> bytes:
        raise TimeoutError("timed out")

    net.vatsim = timeout
    weather.get_metar("KQQQ")
    weather.get_metar("KQQQ")

    assert net.count("ids=KQQQ") == 2
    assert net.count("metar.vatsim.net/KQQQ") == 2


def test_negative_never_hides_a_stale_metar(net: FakeNet, clock: Clock) -> None:
    metar_data_cache, _blacklist = manager.get_metar_cache()
    metar_data_cache["KXYZ"] = {
        "metar": STALE_METAR,
        "wind": "27005KT",
        "altimeter": "A2992",
        "timestamp": clock.now - timedelta(seconds=120),
    }

    assert weather.get_metar("KXYZ") == STALE_METAR
    assert weather.get_metar("KXYZ") == STALE_METAR

    weather.get_weather_for_airports_bbox(["KJFK", "KXYZ"], AIRPORTS)
    assert weather.get_metar("KXYZ") == STALE_METAR
    assert weather.get_altimeter_setting("KXYZ") == "A2992"


def test_404_still_blacklists(net: FakeNet, clock: Clock) -> None:
    def not_found(url: str) -> bytes:
        raise _http_error(url, 404)

    net.ids = not_found
    assert weather.get_metar("KQQQ") == ""
    clock.advance(120)
    assert weather.get_metar("KQQQ") == ""

    assert net.count("ids=KQQQ") == 1
    assert net.count("metar.vatsim.net") == 0


def test_bbox_omission_skips_per_station_fetch(net: FakeNet, clock: Clock) -> None:
    result = weather.get_weather_for_airports_bbox(["KJFK", "KXYZ"], AIRPORTS)
    assert result == {"KJFK": STATIONS["KJFK"][2], "KXYZ": ""}

    assert weather.get_wind_from_metar("KXYZ") == ""
    assert weather.get_altimeter_setting("KXYZ") is None
    assert weather.get_metar("KXYZ") == ""

    assert net.count("ids=") == 0
    assert net.count("metar.vatsim.net") == 0
    assert weather.get_wind_from_metar("KJFK") == "31012KT"


def test_failed_or_400_entry_bbox_does_not_write_negatives(
    net: FakeNet, clock: Clock
) -> None:
    def server_error(url: str) -> bytes:
        raise _http_error(url, 500)

    net.bbox = server_error
    weather.get_weather_for_airports_bbox(["KXYZ"], AIRPORTS)
    weather.reset_rate_limit_state()
    weather.get_metar("KXYZ")
    assert net.count("ids=KXYZ") == 1

    _clear_all()
    lat, lon = AIRPORTS["KXYZ"]["latitude"], AIRPORTS["KXYZ"]["longitude"]
    net.stations = {
        f"F{n:03d}": (lat + 0.1, lon + 0.1, f"F{n:03d} 101251Z 00000KT A3000")
        for n in range(400)
    }
    net.bbox = net._bbox_from_stations
    weather.get_weather_for_airports_bbox(["KXYZ"], AIRPORTS)
    weather.get_metar("KXYZ")
    assert net.count("ids=KXYZ") == 2


def test_negatives_are_not_persisted(
    net: FakeNet, clock: Clock, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    cache_file = tmp_path / "weather_cache.json"
    monkeypatch.setattr(manager, "_WEATHER_CACHE_FILE", str(cache_file))

    weather.get_weather_for_airports_bbox(["KJFK", "KXYZ"], AIRPORTS)
    weather.get_metar("KQQQ")
    manager.save_weather_cache()

    saved = json.loads(cache_file.read_text(encoding="utf-8"))["metar"]
    assert "KJFK" in saved
    assert "KXYZ" not in saved
    assert "KQQQ" not in saved


def test_bbox_http_error_is_logged(
    net: FakeNet, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(weather.debug_logger, "warning", warnings.append)

    def forbidden(url: str) -> bytes:
        raise _http_error(url, 403)

    net.bbox = forbidden
    weather.get_weather_for_airports_bbox(["KXYZ"], AIRPORTS)

    bbox_url = net.bbox_urls()[0]
    bbox_str = urllib.parse.parse_qs(urllib.parse.urlparse(bbox_url).query)["bbox"][0]
    logged = [w for w in warnings if "403" in w]
    assert len(logged) == 1
    assert bbox_str in logged[0]
    assert bbox_url in logged[0]
