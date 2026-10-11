"""The route weather screen parses a filed route in its SID/STAR context.

The CIFP and NASR fixtures of tests/test_short_procedure_names.py are reused
here, so neither the network nor the real caches are touched.
"""

from __future__ import annotations

import pytest

from tests.test_short_procedure_names import (  # noqa: F401  (pytest registers imported fixtures by name)
    _isolate_state,
    cifp_file,
)
from ui.modals import route_weather


@pytest.mark.usefixtures("cifp_file", "_isolate_state")
def test_route_weather_expands_a_filed_sid_before_the_next_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MKK5 APACK from PHNL: the SID expands to MKK ahead of the filed APACK.

    The screen must hand its departure and arrival to the route parser, or the
    SID name resolves to no fix and APACK is the only waypoint left.
    """
    monkeypatch.setattr(route_weather.config, "UNIFIED_AIRPORT_DATA", {})

    waypoints = route_weather.build_route_waypoints("MKK5 APACK", "PHNL", "PHOG")

    assert [waypoint.identifier for waypoint in waypoints] == ["MKK", "APACK"]
