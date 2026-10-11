"""The user guide capture script writes its SVGs offline, from a weather fixture.

Runs the capture mode for two screens into a temp dir with a small weather
snapshot written there. Every HTTP entry point is patched to record and refuse
its request, so a weather, D-ATIS or VATSIM request that leaks past the
fixture fails the test. Reference-data downloads (CIFP, NASR, runways) are
refused too; the real caches are copied in when the machine has them.
"""

import html
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
import requests

from conftest import REAL_DATA_DIR_VAR
from scripts import capture_guide_screenshots as capture

WEATHER_SNAPSHOT: dict[str, Any] = {
    "recorded_at": "2025-10-25T20:03:01Z",
    "metars": {"KJFK": "KJFK 252000Z 31012KT 10SM FEW250 12/M03 A3012"},
    "tafs": {},
    "minute_winds": {},
    "datis": {},
}

# Hosts only the fixtures may answer for
LIVE_DATA_HOSTS = (
    "aviationweather.gov",
    "metar.vatsim.net",
    "api.weather.gov",
    "atis.info",
    "data.vatsim.net",
    "api.vatsim.net",
)


def _svg_text(path: Path) -> str:
    """The text an SVG screenshot shows, with its markup and entities removed."""
    svg = path.read_text(encoding="utf-8")
    return html.unescape(re.sub(r"<[^>]+>", "", svg)).replace("\xa0", " ")


@pytest.fixture
def refused_requests(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Refuse every urllib and requests call, recording each URL asked for."""
    urls: list[str] = []

    def refuse_urlopen(request: Any, *args: Any, **kwargs: Any) -> Any:
        url = getattr(request, "full_url", str(request))
        urls.append(url)
        raise urllib.error.URLError(f"network refused in tests: {url}")

    def refuse_session(
        self: requests.Session, method: str, url: str, *args: Any, **kwargs: Any
    ) -> Any:
        urls.append(url)
        raise requests.ConnectionError(f"network refused in tests: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", refuse_urlopen)
    monkeypatch.setattr(requests.Session, "request", refuse_session)
    return urls


async def test_capture_writes_each_screen_as_svg_without_live_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refused_requests: list[str]
) -> None:
    user_dir = tmp_path / "user"
    for variable in ("LOCALAPPDATA", "XDG_DATA_HOME", "HOME"):
        monkeypatch.setenv(variable, str(user_dir))
    capture.copy_reference_caches(
        Path(os.environ[REAL_DATA_DIR_VAR]), user_dir / "VATSIMControlRecs"
    )
    weather_file = tmp_path / "weather-snapshot.json"
    weather_file.write_text(json.dumps(WEATHER_SNAPSHOT), encoding="utf-8")
    out_dir = tmp_path / "images"

    await capture.capture_screens(["main-airports", "help"], weather_file, out_dir)

    airports = out_dir / "main-airports.svg"
    help_screen = out_dir / "help.svg"
    assert airports.read_text(encoding="utf-8").startswith("<svg")
    assert help_screen.read_text(encoding="utf-8").startswith("<svg")
    assert "ICAO" in _svg_text(airports)
    assert "Keyboard Shortcuts" in _svg_text(help_screen)
    leaks = [url for url in refused_requests if any(h in url for h in LIVE_DATA_HOSTS)]
    assert leaks == []
