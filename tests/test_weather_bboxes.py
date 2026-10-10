"""Airport bbox packing: the box count, coverage, the size limit and determinism.

``calculate_airport_bboxes`` packs the airports into as few boxes as fit
``MAX_BBOX_SIZE_DEGREES`` per axis, so a cold start makes fewer weather requests.
The coordinates are the real OurAirports ones, hard-coded so the test reads no data file.
"""

from backend.data import weather

MAX_SPAN = weather.MAX_BBOX_SIZE_DEGREES
PADDING = weather.BBOX_PADDING_DEGREES

# 30 large US airports, real coordinates, from data/raw/airports.json
US_AIRPORTS = {
    "KATL": (33.6366996765, -84.4281005859),
    "KBOS": (42.36429977, -71.00520325),
    "KBWI": (39.17539978, -76.66829681),
    "KCLT": (35.2140007019, -80.9430999756),
    "KDCA": (38.8521003723, -77.0376968384),
    "KDEN": (39.8616981506, -104.672996521),
    "KDFW": (32.8968009949, -97.0380020142),
    "KDTW": (42.2123985291, -83.3534011841),
    "KEWR": (40.6925010681, -74.1687011719),
    "KFLL": (26.072599411, -80.1527023315),
    "KIAD": (38.94449997, -77.45580292),
    "KIAH": (29.9843997955, -95.3414001465),
    "KJFK": (40.63980103, -73.77890015),
    "KLAS": (36.08010101, -115.1520004),
    "KLAX": (33.94250107, -118.4079971),
    "KLGA": (40.77719879, -73.87259674),
    "KMCO": (28.4293994904, -81.3089981079),
    "KMDW": (41.7859992981, -87.7524032593),
    "KMIA": (25.7931995392, -80.2906036377),
    "KMSP": (44.8819999695, -93.2218017578),
    "KORD": (41.97859955, -87.90480042),
    "KPDX": (45.58869934, -122.5979996),
    "KPHL": (39.8718986511, -75.2410964966),
    "KPHX": (33.434299469, -112.0120010376),
    "KSAN": (32.7336006165, -117.1900024414),
    "KSEA": (47.4490013123, -122.3089981079),
    "KSFO": (37.6189994812, -122.375),
    "KSLC": (40.7883987427, -111.9779968262),
    "KTPA": (27.9755001068, -82.533203125),
    "KSTL": (38.7486991882, -90.3700027466),
}

# Three Bay Area airports: a span small enough for one box
BAY_AREA = {
    "KSFO": (37.6189994812, -122.375),
    "KOAK": (37.7212982178, -122.2210006714),
    "KSJC": (37.3625984192, -121.9290008545),
}


def _airports(coords: dict[str, tuple[float, float]]) -> dict[str, dict[str, float]]:
    """Airport data in the shape ``calculate_airport_bboxes`` takes."""
    return {
        icao: {"latitude": lat, "longitude": lon} for icao, (lat, lon) in coords.items()
    }


def _boxes(coords: dict[str, tuple[float, float]]) -> dict[str, tuple[float, ...]]:
    return weather.calculate_airport_bboxes(list(coords), _airports(coords))


def test_us_spread_packs_into_six_boxes() -> None:
    assert len(_boxes(US_AIRPORTS)) == 6


def test_every_airport_is_inside_a_box() -> None:
    boxes = _boxes(US_AIRPORTS)
    for icao, (lat, lon) in US_AIRPORTS.items():
        assert any(
            min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
            for min_lat, min_lon, max_lat, max_lon in boxes.values()
        ), f"{icao} is inside no box"


def test_no_box_exceeds_the_maximum_plus_padding() -> None:
    limit = MAX_SPAN + 2 * PADDING
    for bbox_id, (min_lat, min_lon, max_lat, max_lon) in _boxes(US_AIRPORTS).items():
        assert max_lat - min_lat <= limit, f"{bbox_id} is taller than the limit"
        assert max_lon - min_lon <= limit, f"{bbox_id} is wider than the limit"


def test_small_span_is_one_box() -> None:
    boxes = _boxes(BAY_AREA)
    assert set(boxes) == {"bbox_0"}
    lats = [lat for lat, _lon in BAY_AREA.values()]
    lons = [lon for _lat, lon in BAY_AREA.values()]
    assert boxes["bbox_0"] == (
        min(lats) - PADDING,
        min(lons) - PADDING,
        max(lats) + PADDING,
        max(lons) + PADDING,
    )


def test_airports_without_coordinates_are_skipped() -> None:
    data = _airports(BAY_AREA)
    data["KXXX"] = {}
    data["KYYY"] = {"latitude": None, "longitude": None}
    boxes = weather.calculate_airport_bboxes([*BAY_AREA, "KXXX", "KYYY", "KZZZ"], data)
    assert boxes == _boxes(BAY_AREA)


def test_no_airports_gives_no_boxes() -> None:
    assert weather.calculate_airport_bboxes([], {}) == {}
    assert weather.calculate_airport_bboxes(["KXXX"], {}) == {}
    assert (
        weather.calculate_airport_bboxes(
            ["KXXX"], {"KXXX": {"latitude": None, "longitude": None}}
        )
        == {}
    )


def test_packing_is_deterministic_for_input_order() -> None:
    data = _airports(US_AIRPORTS)
    forward = weather.calculate_airport_bboxes(list(US_AIRPORTS), data)
    backward = weather.calculate_airport_bboxes(list(reversed(US_AIRPORTS)), data)
    assert forward == backward
