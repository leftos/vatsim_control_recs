"""A weather tile ray-casts only the boundary polygons whose box reaches it.

The CONUS boundary set spans Hawaii to Alaska, so most tiles meet only a few
polygons. Testing every pixel against every polygon made the tile stage outlast
the daemon's systemd start timeout; a polygon whose bounding box misses the tile
cannot contain any of its pixels, so skipping it must not change a pixel.
"""

import math
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scripts.weather_daemon import tile_generator
from scripts.weather_daemon.tile_generator import (
    CATEGORY_RGBA,
    TILE_SIZE,
    WeatherTileGenerator,
    lat_to_tile_y,
    lon_to_tile_x,
)

MAINLAND = [(30.0, -100.0), (40.0, -100.0), (40.0, -90.0), (30.0, -90.0)]
HAWAII = [(18.0, -160.0), (22.0, -160.0), (22.0, -155.0), (18.0, -155.0)]
ZOOM = 5


@pytest.fixture
def generator(tmp_path: Path) -> WeatherTileGenerator:
    """A generator over two square polygons, one VFR airport inside each."""
    return WeatherTileGenerator(
        artcc_boundaries={"ZXA": [MAINLAND], "ZXH": [HAWAII]},
        airport_weather={
            "KMID": {"lat": 35.0, "lon": -95.0, "category": "VFR"},
            "PHAW": {"lat": 20.0, "lon": -157.5, "category": "IFR"},
        },
        output_dir=tmp_path,
        conus_artccs={"ZXA", "ZXH"},
        zoom_levels=(ZOOM,),
    )


@pytest.fixture
def polygon_spy(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the max latitude of every polygon ray-cast, then run the real test."""
    seen: list[float] = []
    real = tile_generator.points_in_polygon

    def spy(lats, lons, polygon):
        seen.append(float(np.asarray(polygon)[:, 0].max()))
        return real(lats, lons, polygon)

    monkeypatch.setattr(tile_generator, "points_in_polygon", spy)
    return seen


def tile_containing(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """The (x, y) of the tile that contains a point."""
    return lon_to_tile_x(lon, zoom), lat_to_tile_y(lat, zoom)


def pixel_in_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """The (row, column) of a point's pixel inside its tile."""
    n = 2**zoom
    x_frac = (lon + 180.0) / 360.0 * n
    y_frac = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    column = int((x_frac - int(x_frac)) * TILE_SIZE)
    row = int((y_frac - int(y_frac)) * TILE_SIZE)
    return row, column


def test_mainland_tile_skips_a_polygon_whose_box_does_not_reach_it(
    generator: WeatherTileGenerator, polygon_spy: list[float]
) -> None:
    tile_x, tile_y = tile_containing(35.0, -95.0, ZOOM)
    generator._generate_tile(tile_x, tile_y, ZOOM)
    assert polygon_spy == [40.0]


def test_ocean_tile_between_the_polygons_tests_no_pixels(
    generator: WeatherTileGenerator, polygon_spy: list[float]
) -> None:
    tile_x, tile_y = tile_containing(28.0, -130.0, ZOOM)
    assert generator._generate_tile(tile_x, tile_y, ZOOM) is None
    assert polygon_spy == []


def test_mainland_tile_still_colours_pixels_inside_its_polygon(
    generator: WeatherTileGenerator,
) -> None:
    tile_x, tile_y = tile_containing(35.0, -95.0, ZOOM)
    png = generator._generate_tile(tile_x, tile_y, ZOOM)
    assert png is not None
    pixels = np.asarray(Image.open(BytesIO(png)).convert("RGBA"))
    row, column = pixel_in_tile(35.0, -95.0, ZOOM)
    assert tuple(int(v) for v in pixels[row, column]) == CATEGORY_RGBA["VFR"]


EDGE_POINTS = [
    (35.0, -95.0),
    (30.0, -100.0),
    (40.0, -90.0),
    (30.0, -90.0),
    (40.0, -100.0),
    (18.0, -160.0),
    (22.0, -155.0),
    (20.0, -157.5),
]


@pytest.mark.parametrize("zoom", [4, 5, 6, 7])
def test_reach_filter_keeps_output_identical(
    generator: WeatherTileGenerator, monkeypatch: pytest.MonkeyPatch, zoom: int
) -> None:
    tiles = sorted({tile_containing(lat, lon, zoom) for lat, lon in EDGE_POINTS})
    # The neighbours east and south of each edge point share its polygon's edge
    tiles = sorted(
        {(x + dx, y + dy) for x, y in tiles for dx in (0, 1) for dy in (0, 1)}
    )
    filtered = [generator._generate_tile(x, y, zoom) for x, y in tiles]

    def every_polygon(tile_bounds):
        return [p for p in generator.all_boundaries if len(p) >= 3]

    monkeypatch.setattr(generator, "_polygons_reaching", every_polygon)
    unfiltered = [generator._generate_tile(x, y, zoom) for x, y in tiles]

    assert any(png is not None for png in unfiltered)
    assert filtered == unfiltered
