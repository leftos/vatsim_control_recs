"""Characterization of the weather fetchers: wind parsing, minute wind, TAF, nearest METAR, smart fetch.

The network is faked at ``urllib.request.urlopen`` with routes matched by URL substring, and
every requested URL is recorded so a test can check which endpoints were hit and in what
order. Time is a fake clock patched over ``backend.data.weather.datetime``.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any

import pytest

from backend.cache import manager
from backend.data import weather
from tests.test_weather_cache import Clock, _http_error, _Response

Handler = Callable[[str], bytes]


class Net:
    """Answers each request from the first route whose marker is in the URL."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.routes: dict[str, Handler] = {}

    def route(self, marker: str, handler: Handler | bytes) -> None:
        if isinstance(handler, bytes):
            body = handler
            self.routes[marker] = lambda url: body
        else:
            self.routes[marker] = handler

    def urlopen(
        self, req: urllib.request.Request, timeout: float | None = None
    ) -> _Response:
        url = req.full_url
        self.urls.append(url)
        for marker, handler in self.routes.items():
            if marker in url:
                return _Response(handler(url))
        raise AssertionError(f"unexpected URL {url}")

    def matching(self, marker: str) -> list[str]:
        return [url for url in self.urls if marker in url]


def _ids(url: str) -> str:
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["ids"][0]


def _by_station(answers: dict[str, str]) -> Handler:
    """A handler answering an ``ids=`` request with that station's text, else empty."""
    return lambda url: answers.get(_ids(url), "").encode()


def _raises(error: Exception) -> Handler:
    def handler(url: str) -> bytes:
        raise error

    return handler


def _clear_all() -> None:
    manager.clear_wind_cache()
    manager.clear_metar_cache()
    manager.clear_taf_cache()
    weather.clear_weather_caches()
    weather.reset_rate_limit_state()


@pytest.fixture(autouse=True)
def clean_caches():
    _clear_all()
    yield
    _clear_all()


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> Net:
    fake = Net()
    monkeypatch.setattr(weather.urllib.request, "urlopen", fake.urlopen)
    return fake


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake_clock = Clock()

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return fake_clock.now

    monkeypatch.setattr(weather, "datetime", FakeDatetime)
    return fake_clock


@pytest.fixture
def logs(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[tuple[str, dict]]]:
    recorded: dict[str, list[tuple[str, dict]]] = {"debug": [], "error": []}
    for level, calls in recorded.items():

        def record(message: str, _calls=calls, **kwargs: Any) -> None:
            _calls.append((message, kwargs))

        monkeypatch.setattr(weather.debug_logger, level, record)
    return recorded


# _parse_wind_from_metar


@pytest.mark.parametrize(
    ("metar", "wind"),
    [
        ("", ""),
        ("KXYZ 101251Z 27005KT 10SM CLR 15/05 A2992", "27005KT"),
        ("KXYZ 101251Z 00000KT 10SM CLR", "00000KT"),
        ("KXYZ 101251Z 00000G10KT 10SM CLR", "00000KT"),
        ("KXYZ 101251Z VRB03KT 10SM CLR", "VRB03KT"),
        ("KXYZ 101251Z VRB00KT 10SM CLR", "VRB00KT"),
        ("KXYZ 101251Z 27015G25KT 10SM CLR", "27015G25KT"),
        ("KXYZ 101251Z 270105G120KT 10SM CLR", "270105G120KT"),
        ("UXYZ 101251Z 27020KMH 9999", "27011KT"),
        ("UXYZ 101251Z 27020G40KMH 9999", "27011G22KT"),
        ("UXYZ 101251Z 27020G40KPH 9999", "27011G22KT"),
        ("UXYZ 101251Z 27005MPS 9999", "27010KT"),
        ("UXYZ 101251Z 27005G10MPS 9999", "27010G19KT"),
        ("UXYZ 101251Z VRB02MPS 9999", "VRB04KT"),
        ("UXYZ 101251Z 00000MPS 9999", "00000KT"),
        ("KXYZ 101251Z 10SM CLR 15/05 A2992", ""),
        ("KXYZ 101251Z 2705KT 10SM", ""),
        ("KXYZ 101251Z ABC12KT 10SM", ""),
        ("KXYZ 101251Z 27005XX 10SM", ""),
        ("KXYZ 101251Z X27005KT 10SM", ""),
    ],
)
def test_parse_wind_from_metar(metar: str, wind: str) -> None:
    assert weather._parse_wind_from_metar(metar) == wind


# get_wind_info_minute

LATEST = "api.weather.gov/stations/KXYZ/observations/latest"
HISTORY = "api.weather.gov/stations/KXYZ/observations?limit=30"


def _observation(
    direction: float | None, speed_kmh: float | None, gust_kmh: float | None = None
) -> dict[str, Any]:
    return {
        "windDirection": {"value": direction},
        "windSpeed": {"value": speed_kmh},
        "windGust": {"value": gust_kmh},
    }


def _latest(properties: dict[str, Any]) -> bytes:
    return json.dumps({"properties": properties}).encode()


def _history(*observations: dict[str, Any]) -> bytes:
    return json.dumps(
        {"features": [{"properties": props} for props in observations]}
    ).encode()


def test_minute_wind_from_latest_is_cached_for_60s(net: Net, clock: Clock) -> None:
    net.route("observations/latest", _latest(_observation(270, 18.52, 37.04)))

    assert weather.get_wind_info_minute("KXYZ") == "27010G20KT"
    clock.advance(59)
    assert weather.get_wind_info_minute("KXYZ") == "27010G20KT"
    assert len(net.urls) == 1
    assert LATEST in net.urls[0]

    clock.advance(2)
    weather.get_wind_info_minute("KXYZ")
    assert len(net.urls) == 2


def test_minute_wind_falls_back_to_history(net: Net) -> None:
    net.route("observations/latest", _latest(_observation(None, 10.0)))
    net.route(
        "observations?limit=30",
        _history(_observation(90, None), _observation(90, 9.26), _observation(180, 50)),
    )

    assert weather.get_wind_info_minute("KXYZ") == "09005KT"
    assert [LATEST in net.urls[0], HISTORY in net.urls[1]] == [True, True]
    assert len(net.urls) == 2


def test_minute_wind_calm(net: Net) -> None:
    net.route("observations/latest", _latest(_observation(0, 0.5)))
    assert weather.get_wind_info_minute("KXYZ") == "00000KT"


def test_minute_wind_without_data_caches_empty(net: Net) -> None:
    net.route("observations/latest", _latest({}))
    net.route("observations?limit=30", _history(_observation(None, None)))

    assert weather.get_wind_info_minute("KXYZ") == ""
    assert weather.get_wind_info_minute("KXYZ") == ""
    assert len(net.urls) == 2


def test_minute_wind_404_blacklists(net: Net, clock: Clock) -> None:
    net.route("observations/latest", _raises(_http_error(LATEST, 404)))

    assert weather.get_wind_info_minute("KXYZ") == ""
    clock.advance(120)
    assert weather.get_wind_info_minute("KXYZ") == ""
    assert len(net.urls) == 1


@pytest.mark.parametrize(
    "error",
    [
        _http_error(LATEST, 500),
        urllib.error.URLError("down"),
        TimeoutError("timed out"),
        ValueError("bad"),
        KeyError("x"),
    ],
)
def test_minute_wind_error_returns_stale_cache(
    net: Net, clock: Clock, error: Exception
) -> None:
    net.route("observations/latest", _latest(_observation(270, 18.52)))
    assert weather.get_wind_info_minute("KXYZ") == "27010KT"
    clock.advance(61)

    net.route("observations/latest", _raises(error))
    assert weather.get_wind_info_minute("KXYZ") == "27010KT"
    assert len(net.urls) == 2


@pytest.mark.parametrize("body", [b"not json", None])
def test_minute_wind_error_without_cache_is_empty(net: Net, body: bytes | None) -> None:
    if body is None:
        net.route("observations/latest", _raises(_http_error(LATEST, 503)))
    else:
        net.route("observations/latest", body)

    assert weather.get_wind_info_minute("KXYZ") == ""
    assert weather.get_wind_info_minute("KXYZ") == ""
    assert len(net.urls) == 2


def test_minute_wind_history_failure_returns_stale_cache(
    net: Net, clock: Clock
) -> None:
    net.route("observations/latest", _latest(_observation(270, 18.52)))
    weather.get_wind_info_minute("KXYZ")
    clock.advance(61)

    net.route("observations/latest", _latest({}))
    net.route("observations?limit=30", _raises(urllib.error.URLError("down")))
    assert weather.get_wind_info_minute("KXYZ") == "27010KT"
    assert len(net.urls) == 3


# get_taf

TAF_URL = "https://aviationweather.gov/api/data/taf?ids=KXYZ&format=raw"
TAF = "TAF KXYZ 101130Z 1012/1112 27010KT P6SM FEW250"


def test_taf_fetch_is_cached_for_60s(net: Net, clock: Clock) -> None:
    net.route("taf?ids=", f"  {TAF}\n".encode())

    assert weather.get_taf("KXYZ") == TAF
    clock.advance(59)
    assert weather.get_taf("KXYZ") == TAF
    assert net.urls == [TAF_URL]

    clock.advance(2)
    assert weather.get_taf("KXYZ") == TAF
    assert net.urls == [TAF_URL, TAF_URL]


def test_taf_empty_response_is_neither_cached_nor_blacklisted(net: Net) -> None:
    net.route("taf?ids=", b"  ")

    assert weather.get_taf("KXYZ") == ""
    assert weather.get_taf("KXYZ") == ""
    assert len(net.urls) == 2


@pytest.mark.parametrize("body", [b"No TAF found", b"Error: bad station"])
def test_taf_explicit_no_data_blacklists(net: Net, clock: Clock, body: bytes) -> None:
    net.route("taf?ids=", body)

    assert weather.get_taf("KXYZ") == ""
    clock.advance(120)
    assert weather.get_taf("KXYZ") == ""
    assert len(net.urls) == 1


def test_taf_404_blacklists(net: Net, clock: Clock) -> None:
    net.route("taf?ids=", _raises(_http_error(TAF_URL, 404)))

    assert weather.get_taf("KXYZ") == ""
    clock.advance(120)
    assert weather.get_taf("KXYZ") == ""
    assert len(net.urls) == 1


def test_taf_other_http_error_returns_stale_cache(net: Net, clock: Clock) -> None:
    net.route("taf?ids=", TAF.encode())
    weather.get_taf("KXYZ")
    clock.advance(61)

    net.route("taf?ids=", _raises(_http_error(TAF_URL, 403)))
    assert weather.get_taf("KXYZ") == TAF
    assert "KXYZ" not in manager.get_taf_cache()[1]


def test_get_taf_rate_limited_returns_cached_taf(
    net: Net, clock: Clock, logs: dict[str, list[tuple[str, dict]]]
) -> None:
    net.route("taf?ids=", TAF.encode())
    weather.get_taf("KXYZ")
    clock.advance(61)

    net.route("taf?ids=", _raises(_http_error(TAF_URL, 429)))
    assert weather.get_taf("KXYZ") == TAF
    assert logs["debug"] == [
        ("Rate limit detected (HTTP 429) for TAF KXYZ, backoff: 1.0s", {})
    ]
    assert weather.get_rate_limit_status()["is_rate_limited"]

    weather.reset_rate_limit_state()
    net.route("taf?ids=", _raises(_http_error(TAF_URL, 500)))
    assert weather.get_taf("KQQQ") == ""
    assert "KQQQ" not in manager.get_taf_cache()[1]


# rate limit backoff

METAR_URL = "https://aviationweather.gov/api/data/metar?ids=KXYZ&format=raw"
BBOX = (39.5, -74.5, 41.5, -72.5)
BBOX_URL = (
    "https://aviationweather.gov/api/data/metar?bbox=39.5,-74.5,41.5,-72.5&format=json"
)


@pytest.mark.parametrize("code", [500, 502, 504])
@pytest.mark.parametrize("kind", ["metar", "taf", "bbox"])
def test_server_errors_other_than_503_do_not_back_off(
    net: Net, monkeypatch: pytest.MonkeyPatch, kind: str, code: int
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(weather.time, "sleep", sleeps.append)

    if kind == "metar":
        net.route("metar?ids=", _raises(_http_error(METAR_URL, code)))
        net.route("metar.vatsim.net", b"")
        assert weather.get_metar("KXYZ") == ""
        net.route("metar?ids=", b"KQQQ 101130Z 27010KT")
        assert weather.get_metar("KQQQ") == "KQQQ 101130Z 27010KT"
    elif kind == "taf":
        net.route("taf?ids=", _raises(_http_error(TAF_URL, code)))
        assert weather.get_taf("KXYZ") == ""
        net.route("taf?ids=", TAF.encode())
        assert weather.get_taf("KQQQ") == TAF
    else:
        net.route("bbox=", _raises(_http_error(BBOX_URL, code)))
        assert not weather._fetch_weather_bbox_result(BBOX, False, 5).succeeded
        net.route("bbox=", b"[]")
        assert weather._fetch_weather_bbox_result(BBOX, False, 5).succeeded

    assert weather.get_rate_limit_status()["is_rate_limited"] is False
    assert sleeps == []


def test_503_backs_off(net: Net) -> None:
    net.route("bbox=", _raises(_http_error(BBOX_URL, 503)))

    result = weather._fetch_weather_bbox_result(BBOX, False, 5)

    assert result == weather._FAILED_BBOX_FETCH
    assert weather.get_rate_limit_status()["is_rate_limited"] is True


@pytest.mark.parametrize(
    ("error", "logged"),
    [
        (urllib.error.URLError("down"), "URLError: <urlopen error down>"),
        (TimeoutError("timed out"), "TimeoutError: timed out"),
    ],
)
def test_taf_expected_error_logs_and_returns_stale_cache(
    net: Net,
    clock: Clock,
    logs: dict[str, list[tuple[str, dict]]],
    error: Exception,
    logged: str,
) -> None:
    net.route("taf?ids=", TAF.encode())
    weather.get_taf("KXYZ")
    clock.advance(61)

    net.route("taf?ids=", _raises(error))
    assert weather.get_taf("KXYZ") == TAF
    assert weather.get_taf("KQQQ") == ""
    assert logs["debug"] == [
        (f"Expected error fetching TAF for KXYZ: {logged}", {}),
        (f"Expected error fetching TAF for KQQQ: {logged}", {}),
    ]


def test_taf_unexpected_error_logs_traceback_and_returns_stale_cache(
    net: Net, clock: Clock, logs: dict[str, list[tuple[str, dict]]]
) -> None:
    net.route("taf?ids=", TAF.encode())
    weather.get_taf("KXYZ")
    clock.advance(61)

    net.route("taf?ids=", _raises(RuntimeError("boom")))
    assert weather.get_taf("KXYZ") == TAF
    assert weather.get_taf("KQQQ") == ""
    assert logs["error"] == [
        (
            "Unexpected error fetching TAF for KXYZ: RuntimeError: boom",
            {"exc_info": True},
        ),
        (
            "Unexpected error fetching TAF for KQQQ: RuntimeError: boom",
            {"exc_info": True},
        ),
    ]


# _build_metar_airport_spatial_index


def _use_persisted(
    monkeypatch: pytest.MonkeyPatch,
    persisted: dict[str, Any] | None,
    known: set[str] | None,
) -> None:
    monkeypatch.setattr(weather, "_PERSISTED_SPATIAL_CACHE_LOADED", True)
    monkeypatch.setattr(weather, "_PERSISTED_SPATIAL_CACHE", persisted)
    monkeypatch.setattr(weather, "_KNOWN_METAR_STATIONS", known)


def _ap(icao: str, lat: float, lon: float) -> dict[str, Any]:
    return {"icao": icao, "lat": lat, "lon": lon}


PERSISTED = {
    "version": 1,
    "spatial_grid": {
        "40,-74": [_ap("KAAA", 40.2, -73.5), _ap("KBBB", 40.4, -73.6)],
        "39,-74": [_ap("KCCC", 39.5, -73.9)],
        "41,-74": [_ap("KDDD", 41.5, -73.9)],
    },
}


def test_spatial_index_from_persisted_grid_filters_blacklist_and_whitelist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_persisted(monkeypatch, PERSISTED, {"KAAA", "KCCC", "KDDD"})
    manager.get_metar_cache()[1]["KDDD"] = True

    index = weather._build_metar_airport_spatial_index({"KZZZ": {}})

    assert index == {
        "grid": {
            (40, -74): [_ap("KAAA", 40.2, -73.5)],
            (39, -74): [_ap("KCCC", 39.5, -73.9)],
        },
        "airports": [_ap("KAAA", 40.2, -73.5), _ap("KCCC", 39.5, -73.9)],
        "cell_size": 1.0,
    }


def test_spatial_index_from_persisted_grid_without_whitelist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_persisted(monkeypatch, PERSISTED, None)

    index = weather._build_metar_airport_spatial_index({})

    assert [a["icao"] for a in index["airports"]] == ["KAAA", "KBBB", "KCCC", "KDDD"]
    assert list(index["grid"]) == [(40, -74), (39, -74), (41, -74)]


SCRATCH_AIRPORTS = {
    "KAAA": {"latitude": 40.2, "longitude": -73.5},
    "KBBB": {"latitude": 40.7, "longitude": -73.2},
    "KNOL": {"latitude": None, "longitude": -73.2},
    "KNOO": {"latitude": 40.0},
    "KBAN": {"latitude": 40.1, "longitude": -73.1},
    "KCCC": {"latitude": -33.9, "longitude": 151.2},
}


@pytest.mark.parametrize(
    "persisted", [None, {"version": 1, "metar_stations": ["KAAA"]}]
)
def test_spatial_index_from_scratch(
    monkeypatch: pytest.MonkeyPatch, persisted: dict[str, Any] | None
) -> None:
    _use_persisted(monkeypatch, persisted, None)
    manager.get_metar_cache()[1]["KBAN"] = True

    index = weather._build_metar_airport_spatial_index(SCRATCH_AIRPORTS)

    assert index == {
        "grid": {
            (40, -73): [_ap("KAAA", 40.2, -73.5), _ap("KBBB", 40.7, -73.2)],
            (-33, 151): [_ap("KCCC", -33.9, 151.2)],
        },
        "airports": [
            _ap("KAAA", 40.2, -73.5),
            _ap("KBBB", 40.7, -73.2),
            _ap("KCCC", -33.9, 151.2),
        ],
        "cell_size": 1.0,
    }


def test_spatial_index_from_scratch_with_whitelist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_persisted(monkeypatch, None, {"KBBB", "KCCC"})

    index = weather._build_metar_airport_spatial_index(SCRATCH_AIRPORTS)

    assert index["airports"] == [_ap("KBBB", 40.7, -73.2), _ap("KCCC", -33.9, 151.2)]


# find_nearest_airport_with_metar

ORIGIN = (40.0, -74.0)
NEAREST_AIRPORTS = {
    "KNTH": {"latitude": 40.4, "longitude": -74.0},
    "KSTH": {"latitude": 39.7, "longitude": -74.0},
    "KFAR": {"latitude": 45.0, "longitude": -74.0},
}


def _metar_for(icao: str) -> str:
    return f"{icao} 101251Z 27005KT 10SM CLR 15/05 A2992"


@pytest.fixture
def nearest_net(net: Net, monkeypatch: pytest.MonkeyPatch) -> Net:
    _use_persisted(monkeypatch, None, None)
    net.route("metar.vatsim.net", b"")
    return net


def _metar_ids(net: Net) -> list[str]:
    return [_ids(url) for url in net.matching("metar?ids=")]


def _distance(icao: str) -> float:
    airport = NEAREST_AIRPORTS[icao]
    return weather.haversine_distance_nm(
        *ORIGIN, airport["latitude"], airport["longitude"]
    )


def test_nearest_is_the_airport_itself(nearest_net: Net) -> None:
    nearest_net.route("metar?ids=", _by_station({"KNTH": _metar_for("KNTH")}))

    result = weather.find_nearest_airport_with_metar(40.4, -74.0, NEAREST_AIRPORTS)

    on_site = weather.haversine_distance_nm(40.4, -74.0, 40.4, -74.0)
    assert result == ("KNTH", "A2992", on_site)
    assert _metar_ids(nearest_net) == ["KNTH"]


def test_nearest_skips_an_airport_without_metar(nearest_net: Net) -> None:
    nearest_net.route("metar?ids=", _by_station({"KNTH": _metar_for("KNTH")}))

    result = weather.find_nearest_airport_with_metar(*ORIGIN, NEAREST_AIRPORTS)

    assert result == ("KNTH", "A2992", _distance("KNTH"))
    assert _metar_ids(nearest_net) == ["KSTH", "KNTH"]
    assert nearest_net.matching("metar.vatsim.net") == ["https://metar.vatsim.net/KSTH"]


@pytest.mark.parametrize(
    ("heading", "groundspeed", "expected"),
    [
        (None, None, "KSTH"),
        (0.0, 300.0, "KNTH"),
        (0.0, None, "KNTH"),
        (0.0, 40.0, "KSTH"),
        (180.0, 300.0, "KSTH"),
        (None, 300.0, "KSTH"),
    ],
)
def test_nearest_heading_bias(
    nearest_net: Net,
    heading: float | None,
    groundspeed: float | None,
    expected: str,
) -> None:
    stations = {icao: _metar_for(icao) for icao in NEAREST_AIRPORTS}
    nearest_net.route("metar?ids=", _by_station(stations))

    result = weather.find_nearest_airport_with_metar(
        *ORIGIN,
        NEAREST_AIRPORTS,
        aircraft_heading=heading,
        aircraft_groundspeed=groundspeed,
    )

    assert result == (expected, "A2992", _distance(expected))
    assert _metar_ids(nearest_net) == [expected]


def test_nearest_none_within_radius(nearest_net: Net) -> None:
    nearest_net.route("metar?ids=", _by_station({"KFAR": _metar_for("KFAR")}))
    far_only = {"KFAR": NEAREST_AIRPORTS["KFAR"]}

    assert weather.find_nearest_airport_with_metar(*ORIGIN, far_only) is None
    assert nearest_net.urls == []


def test_nearest_none_with_metar_tries_at_most_20(nearest_net: Net) -> None:
    nearest_net.route("metar?ids=", b"")
    many = {
        f"K{n:03d}": {"latitude": 40.0 + n * 0.01, "longitude": -74.0}
        for n in range(25)
    }

    assert weather.find_nearest_airport_with_metar(*ORIGIN, many) is None
    assert _metar_ids(nearest_net) == [f"K{n:03d}" for n in range(20)]


def test_nearest_result_is_cached_per_position_for_60s(
    nearest_net: Net, clock: Clock
) -> None:
    nearest_net.route("metar?ids=", _by_station({"KNTH": _metar_for("KNTH")}))
    first = weather.find_nearest_airport_with_metar(*ORIGIN, NEAREST_AIRPORTS)
    manager.clear_metar_cache()
    requests = len(nearest_net.urls)

    clock.advance(59)
    assert weather.find_nearest_airport_with_metar(40.01, -74.01, {}) == first
    assert len(nearest_net.urls) == requests

    clock.advance(2)
    assert weather.find_nearest_airport_with_metar(*ORIGIN, NEAREST_AIRPORTS) == first
    assert len(nearest_net.urls) > requests


def test_nearest_none_result_is_cached(nearest_net: Net) -> None:
    nearest_net.route("metar?ids=", b"")
    assert weather.find_nearest_airport_with_metar(*ORIGIN, NEAREST_AIRPORTS) is None
    requests = len(nearest_net.urls)

    nearest_net.route("metar?ids=", _by_station({"KNTH": _metar_for("KNTH")}))
    manager.clear_metar_cache()
    assert weather.find_nearest_airport_with_metar(*ORIGIN, NEAREST_AIRPORTS) is None
    assert len(nearest_net.urls) == requests


def test_nearest_spatial_index_is_rebuilt_after_300s(
    nearest_net: Net, clock: Clock
) -> None:
    nearest_net.route("metar?ids=", _by_station({"KNTH": _metar_for("KNTH")}))
    weather.find_nearest_airport_with_metar(*ORIGIN, NEAREST_AIRPORTS)

    clock.advance(120)
    moved = {"KMOV": {"latitude": 40.1, "longitude": -74.0}}
    assert weather.find_nearest_airport_with_metar(*ORIGIN, moved) == (
        "KNTH",
        "A2992",
        _distance("KNTH"),
    )

    clock.advance(200)
    nearest_net.route("metar?ids=", _by_station({"KMOV": _metar_for("KMOV")}))
    result = weather.find_nearest_airport_with_metar(*ORIGIN, moved)
    assert result is not None
    assert result[0] == "KMOV"


# find_airports_near_position

NEAR_AIRPORTS = {
    "KNEA": {"latitude": 40.1, "longitude": -74.0},
    "KNEB": {"latitude": 40.3, "longitude": -74.0},
    "KNEC": {"latitude": 40.6, "longitude": -74.0},
    "KNED": {"latitude": 40.8, "longitude": -74.0},
    "KNEE": {"latitude": 40.85, "longitude": -74.0},
}


def test_near_position_sorted_by_distance_and_capped(nearest_net: Net) -> None:
    assert weather.find_airports_near_position(*ORIGIN, NEAR_AIRPORTS) == [
        "KNEA",
        "KNEB",
        "KNEC",
        "KNED",
    ]
    assert weather.find_airports_near_position(
        *ORIGIN, NEAR_AIRPORTS, max_results=2
    ) == ["KNEA", "KNEB"]


def test_near_position_excludes_airports_beyond_radius(nearest_net: Net) -> None:
    near = {"KNEA": NEAR_AIRPORTS["KNEA"], "KNEE": NEAR_AIRPORTS["KNEE"]}

    assert weather.find_airports_near_position(*ORIGIN, near) == ["KNEA"]


def test_near_position_reuses_index_then_rebuilds_after_300s(
    nearest_net: Net, clock: Clock
) -> None:
    first = weather.find_airports_near_position(*ORIGIN, NEAR_AIRPORTS)
    assert first == ["KNEA", "KNEB", "KNEC", "KNED"]

    clock.advance(120)
    moved = {"KMOV": {"latitude": 40.1, "longitude": -74.0}}
    assert weather.find_airports_near_position(*ORIGIN, moved) == first

    clock.advance(200)
    assert weather.find_airports_near_position(*ORIGIN, moved) == ["KMOV"]


# get_weather_smart

DENSE = {
    "KD01": (40.0, -74.0),
    "KD02": (40.25, -73.75),
    "KD03": (40.5, -73.5),
    "KD04": (40.75, -73.25),
    "KD05": (41.0, -73.0),
}
SPARSE = {"KS01": (34.0, -118.0), "KS02": (34.25, -118.25)}
COORDS = {**DENSE, **SPARSE}
AIRPORTS = [*DENSE, *SPARSE, "KNOC"]
DENSE_BBOX = "bbox=39.5,-74.5,41.5,-72.5&format=json"


def _bbox_body(icaos: list[str]) -> bytes:
    return json.dumps(
        [
            {"icaoId": icao, "rawOb": _metar_for(icao), "rawTaf": f"TAF {icao}"}
            for icao in icaos
        ]
    ).encode()


@pytest.fixture
def smart_net(net: Net) -> Net:
    net.route("bbox=", _bbox_body(["KD01", "KD02", "KD03", "KD04", "KXTR"]))
    net.route("metar?ids=", _by_station({"KS01": _metar_for("KS01")}))
    net.route("taf?ids=", _by_station({"KS01": "TAF KS01"}))
    net.route("metar.vatsim.net", b"")
    return net


def _station_ids(net: Net, marker: str) -> list[str]:
    return sorted(_ids(url) for url in net.matching(marker))


def _smart(
    **kwargs: Any,
) -> tuple[tuple[dict[str, str], dict[str, str]], list[tuple[int, int]]]:
    progress: list[tuple[int, int]] = []
    result = weather.get_weather_smart(
        AIRPORTS,
        COORDS,
        progress_callback=lambda done, total: progress.append((done, total)),
        **kwargs,
    )
    return result, progress


def test_smart_empty_request(net: Net) -> None:
    assert weather.get_weather_smart([], COORDS) == ({}, {})
    assert net.urls == []


def test_smart_dense_cluster_by_bbox_sparse_and_unknown_individually(
    smart_net: Net,
) -> None:
    (metars, tafs), progress = _smart()

    assert list(metars) == AIRPORTS
    assert metars == {
        **{icao: _metar_for(icao) for icao in ("KD01", "KD02", "KD03", "KD04")},
        "KD05": "",
        "KS01": _metar_for("KS01"),
        "KS02": "",
        "KNOC": "",
    }
    assert tafs == {
        **{icao: f"TAF {icao}" for icao in ("KD01", "KD02", "KD03", "KD04")},
        "KD05": "",
        "KS01": "TAF KS01",
        "KS02": "",
        "KNOC": "",
    }
    assert DENSE_BBOX + "&taf=true" in smart_net.urls[0]
    assert len(smart_net.matching("bbox=")) == 1
    assert _station_ids(smart_net, "metar?ids=") == ["KNOC", "KS01", "KS02"]
    assert _station_ids(smart_net, "taf?ids=") == ["KNOC", "KS01", "KS02"]
    last_metar = max(i for i, u in enumerate(smart_net.urls) if "metar?ids=" in u)
    first_taf = min(i for i, u in enumerate(smart_net.urls) if "taf?ids=" in u)
    assert last_metar < first_taf
    assert progress == [(5, 8), (6, 8), (7, 8), (8, 8)]


def test_smart_without_taf(smart_net: Net) -> None:
    (metars, tafs), progress = _smart(include_taf=False)

    assert tafs == {}
    assert metars["KD01"] == _metar_for("KD01")
    bbox_url = smart_net.matching("bbox=")[0]
    assert bbox_url.endswith(DENSE_BBOX)
    assert smart_net.matching("taf?ids=") == []
    assert progress == [(5, 8), (6, 8), (7, 8), (8, 8)]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_airports_for_bbox": 6},
        {"max_bbox_area_per_airport": 0.5},
        {"cluster_radius_nm": 1.0},
    ],
)
def test_smart_cluster_too_small_or_spread_goes_individual(
    smart_net: Net, kwargs: dict[str, Any]
) -> None:
    (metars, _tafs), progress = _smart(**kwargs)

    assert smart_net.matching("bbox=") == []
    assert _station_ids(smart_net, "metar?ids=") == sorted(AIRPORTS)
    assert metars["KD01"] == ""
    assert progress == [(n, 8) for n in range(1, 9)]


def test_smart_bbox_that_raises_falls_back_to_individual(
    smart_net: Net, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crashing_bbox(*_args: Any) -> weather.BboxFetchResult:
        raise RuntimeError("bbox crashed")

    monkeypatch.setattr(weather, "_fetch_weather_bbox_result", crashing_bbox)
    smart_net.route(
        "metar?ids=",
        _by_station({"KS01": _metar_for("KS01"), "KD03": _metar_for("KD03")}),
    )

    (metars, _tafs), progress = _smart()

    assert _station_ids(smart_net, "metar?ids=") == sorted(AIRPORTS)
    assert metars["KD03"] == _metar_for("KD03")
    assert metars["KD01"] == ""
    assert progress == [(n, 8) for n in range(1, 9)]


@pytest.mark.parametrize(
    "bbox_body",
    [
        _raises(_http_error("bbox", 403)),
        _raises(_http_error("bbox", 503)),
        _raises(urllib.error.URLError("down")),
        b"{}",
    ],
    ids=["403", "503", "network", "non-array"],
)
def test_smart_bbox_failure_falls_back_to_individual(
    smart_net: Net, bbox_body: Handler
) -> None:
    smart_net.route("bbox=", bbox_body)
    smart_net.route(
        "metar?ids=",
        _by_station({"KS01": _metar_for("KS01"), "KD03": _metar_for("KD03")}),
    )

    (metars, _tafs), progress = _smart()

    assert _station_ids(smart_net, "metar?ids=") == sorted(AIRPORTS)
    assert metars["KD03"] == _metar_for("KD03")
    assert metars["KD01"] == ""
    assert progress == [(n, 8) for n in range(1, 9)]


def test_smart_bbox_rate_limited_leaves_cluster_empty(smart_net: Net) -> None:
    smart_net.route("bbox=", _raises(_http_error("bbox", 429)))

    (metars, tafs), progress = _smart()

    assert all(metars[icao] == "" for icao in DENSE)
    assert all(tafs[icao] == "" for icao in DENSE)
    assert _station_ids(smart_net, "metar?ids=") == ["KNOC", "KS01", "KS02"]
    assert progress == [(5, 8), (6, 8), (7, 8), (8, 8)]


def test_smart_individual_failures_are_logged_and_left_empty(
    smart_net: Net,
    monkeypatch: pytest.MonkeyPatch,
    logs: dict[str, list[tuple[str, dict]]],
) -> None:
    def failing(icao: str) -> str:
        raise RuntimeError(f"no {icao}")

    monkeypatch.setattr(weather, "get_metar", failing)
    monkeypatch.setattr(weather, "get_taf", failing)

    (metars, tafs), progress = _smart()

    assert metars["KS01"] == ""
    assert tafs["KS01"] == ""
    assert sorted(message for message, _kwargs in logs["debug"]) == [
        "METAR fetch failed for KNOC: no KNOC",
        "METAR fetch failed for KS01: no KS01",
        "METAR fetch failed for KS02: no KS02",
        "TAF fetch failed for KNOC: no KNOC",
        "TAF fetch failed for KS01: no KS01",
        "TAF fetch failed for KS02: no KS02",
    ]
    assert progress == [(5, 8), (6, 8), (7, 8), (8, 8)]


@pytest.mark.parametrize("code", [429, 503])
def test_smart_individual_taf_error_returns_stale_cached_taf(
    smart_net: Net, clock: Clock, code: int
) -> None:
    (_metars, tafs), _progress = _smart()
    assert tafs["KS01"] == "TAF KS01"

    clock.advance(61)
    smart_net.route("taf?ids=", _raises(_http_error(TAF_URL, code)))

    (_metars, tafs), _progress = _smart()

    assert tafs["KS01"] == "TAF KS01"
    assert "KS01" not in manager.get_taf_cache()[1]
