"""Weather-briefing area names: which airports name an area.

After the airports with an ATIS and the larger airports, an airport with an IATA
code names the area before one without, and ICAO order breaks the last tie.
Outside the US every airport has the same size rank, so the IATA code is what
keeps a strip or a heliport out of the name.

Areas whose names clash are renamed after their centre airport's display name;
an area whose name is unique keeps it.
"""

from typing import Any

from backend.briefing.area_clustering import AreaClusterer


def _airport(
    icao: str, city: str, lat: float, lon: float, iata: str = ""
) -> dict[str, Any]:
    return {
        "icao": icao,
        "iata": iata,
        "city": city,
        "state": "Ile-de-France",
        "country": "FR",
        "latitude": lat,
        "longitude": lon,
    }


def test_iata_airport_names_the_area_before_an_earlier_icao() -> None:
    unified = {
        "LFPG": _airport("LFPG", "Roissy", 49.0097, 2.5479, iata="CDG"),
        "LFPN": _airport("LFPN", "Toussus-le-Noble", 48.7519, 2.1061),
        "LFPO": _airport("LFPO", "Orly", 48.7233, 2.3794, iata="ORY"),
    }
    weather: dict[str, dict[str, Any]] = {
        "LFPG": {"category": "VFR", "atis": [{"callsign": "LFPG_ATIS"}]},
        "LFPN": {"category": "VFR"},
        "LFPO": {"category": "VFR"},
    }

    groups = AreaClusterer(weather, unified).create_area_groups()

    assert [group["name"] for group in groups] == ["Roissy / Orly+ Area"]
    assert groups[0]["center_icao"] == "LFPG"


def _london(icao, lat, lon):
    return {
        "icao": icao,
        "city": "London",
        "state": "England",
        "country": "GB",
        "latitude": lat,
        "longitude": lon,
    }


def _with_atis(unified: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        icao: {"category": "VFR", "atis": [{"callsign": f"{icao}_ATIS"}]}
        for icao in unified
    }


class _FakeDisambiguator:
    def __init__(self, names: dict[str, str]) -> None:
        self.names = names

    def get_pretty_name(self, icao: str) -> str:
        return self.names[icao]


LONDON_NAMES = {"EGLL": "London Heathrow", "EGKK": "London Gatwick"}


def test_two_areas_around_one_city_get_different_names() -> None:
    unified = {
        "EGLL": _london("EGLL", 51.4706, -0.4619),
        "EGKK": _london("EGKK", 51.1481, -0.1903),
    }
    weather = {
        icao: {"category": "VFR", "atis": [{"callsign": f"{icao}_ATIS"}]}
        for icao in unified
    }
    groups = AreaClusterer(weather, unified).create_area_groups()
    names = [group["name"] for group in groups]
    assert len(groups) == 2
    assert len(set(names)) == len(names), names


def test_clashing_areas_are_named_after_their_centre_display_name() -> None:
    unified = {
        "EGLL": _london("EGLL", 51.4706, -0.4619),
        "EGKK": _london("EGKK", 51.1481, -0.1903),
    }
    disambiguator = _FakeDisambiguator(LONDON_NAMES)

    clusterer = AreaClusterer(_with_atis(unified), unified, disambiguator)
    names = {
        group["center_icao"]: group["name"] for group in clusterer.create_area_groups()
    }

    assert names == {"EGLL": "London Heathrow Area", "EGKK": "London Gatwick Area"}


def test_area_with_a_unique_name_keeps_it() -> None:
    unified = {
        "EGLL": _london("EGLL", 51.4706, -0.4619),
        "EGKK": _london("EGKK", 51.1481, -0.1903),
        "LFPG": _airport("LFPG", "Paris", 49.0097, 2.5479, iata="CDG"),
        "EDDF": _airport("EDDF", "Frankfurt", 50.0333, 8.5706, iata="FRA"),
    }
    disambiguator = _FakeDisambiguator(LONDON_NAMES)

    clusterer = AreaClusterer(_with_atis(unified), unified, disambiguator)
    names = {
        group["center_icao"]: group["name"] for group in clusterer.create_area_groups()
    }

    assert names == {
        "EGLL": "London Heathrow Area",
        "EGKK": "London Gatwick Area",
        "LFPG": "Paris Area",
        "EDDF": "Frankfurt Area",
    }
