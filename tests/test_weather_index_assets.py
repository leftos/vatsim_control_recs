"""The generated weather index page loads Leaflet from its own tree.

The site promises no third-party JavaScript, so Leaflet ships under
``assets/leaflet/<version>/`` beside ``index.html`` and the page references it
relative to itself. The basemap tiles stay on CARTO/OpenStreetMap, with their
attribution.
"""

import hashlib
import re
from pathlib import Path

import pytest

from scripts.weather_daemon import index_generator
from scripts.weather_daemon.config import DaemonConfig

# Upstream https://unpkg.com/leaflet@1.9.4/dist/leaflet.js
LEAFLET_JS_SHA256 = "db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a"

ASSET_DIR = Path("assets") / "leaflet" / "1.9.4"


@pytest.fixture
def index_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """Generate the index page with no network; return its output dir and HTML."""
    monkeypatch.setattr(index_generator, "get_artcc_boundaries", lambda cache_dir: {})
    monkeypatch.setattr(
        index_generator, "get_all_grouping_boundaries", lambda *args, **kwargs: {}
    )
    config = DaemonConfig(output_dir=tmp_path, artcc_cache_dir=tmp_path / "cache")
    index_path = index_generator.generate_index_page(config, {}, None)
    assert index_path == tmp_path / "index.html"
    return tmp_path, index_path.read_text(encoding="utf-8")


def test_index_page_loads_no_remote_scripts_or_stylesheets(
    index_page: tuple[Path, str],
) -> None:
    _, html = index_page
    assert "unpkg.com" not in html
    assert '<script src="http' not in html
    assert not re.search(r'<script[^>]+src="https?://', html)
    assert not re.search(r'<link[^>]+href="https?://[^"]*\.css', html)


def test_index_page_references_the_local_leaflet(index_page: tuple[Path, str]) -> None:
    _, html = index_page
    assert 'href="assets/leaflet/1.9.4/leaflet.css"' in html
    assert 'src="assets/leaflet/1.9.4/leaflet.js"' in html


def test_index_page_keeps_the_tile_layer_and_attribution(
    index_page: tuple[Path, str],
) -> None:
    _, html = index_page
    assert "basemaps.cartocdn.com" in html
    assert "https://www.openstreetmap.org/copyright" in html
    assert "https://carto.com/attributions" in html


def test_leaflet_assets_are_copied_into_the_output(
    index_page: tuple[Path, str],
) -> None:
    output_dir, _ = index_page
    asset_dir = output_dir / ASSET_DIR
    for name in ("leaflet.js", "leaflet.css", "LICENSE", "images/layers.png"):
        assert (asset_dir / name).is_file(), f"missing vendored asset {name}"
    digest = hashlib.sha256((asset_dir / "leaflet.js").read_bytes()).hexdigest()
    assert digest == LEAFLET_JS_SHA256
