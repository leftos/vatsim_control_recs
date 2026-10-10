"""A location the names CSV fully covers never runs the NER engine.

`_process_location_internal` discards every NER result for an airport the CSV
already named, so a location where the CSV covers every airport can return
before the engine is asked at all. A location with one CSV-less airport keeps
today's behaviour: NER runs for the whole group.
"""

import csv
from pathlib import Path
from typing import Any

from airport_disambiguator.disambiguator import AirportDisambiguator

LOCATION = "Testville"

# Two airports sharing a city, so they land in one location group. The state is
# set but does not prefix the names, so the base location is the city.
AIRPORTS: dict[str, dict[str, Any]] = {
    "KAAA": {
        "icao": "KAAA",
        "name": "Alpha Field",
        "city": LOCATION,
        "state": "ZZ",
        "country": "US",
    },
    "KBBB": {
        "icao": "KBBB",
        "name": "Bravo Field",
        "city": LOCATION,
        "state": "ZZ",
        "country": "US",
    },
}


def _write_names_csv(path: Path, names: dict[str, str]) -> str:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        writer = csv.writer(handle)
        writer.writerow(["icao", "name"])
        writer.writerows(names.items())
    return str(path)


def _make_disambiguator(names_csv_path: str) -> AirportDisambiguator:
    return AirportDisambiguator(
        airports_file_path="unused.json",
        unified_data=AIRPORTS,
        names_csv_path=names_csv_path,
    )


def test_location_fully_covered_by_csv_skips_ner(tmp_path: Path, monkeypatch) -> None:
    csv_path = _write_names_csv(
        tmp_path / "names.csv",
        {"KAAA": "Testville Alpha", "KBBB": "Testville Bravo"},
    )
    disambiguator = _make_disambiguator(csv_path)

    def fail_if_called(*_args: object, **_kwargs: object) -> dict[str, str]:
        raise AssertionError("NER ran for a location the names CSV fully covers")

    monkeypatch.setattr(
        disambiguator.disambiguation_engine,
        "disambiguate_multiple_airports",
        fail_if_called,
    )

    assert disambiguator.get_full_names_batch(["KAAA", "KBBB"]) == {
        "KAAA": "Testville Alpha",
        "KBBB": "Testville Bravo",
    }


def test_location_partly_covered_by_csv_still_runs_ner(
    tmp_path: Path, monkeypatch
) -> None:
    csv_path = _write_names_csv(tmp_path / "names.csv", {"KAAA": "Testville Alpha"})
    disambiguator = _make_disambiguator(csv_path)

    calls: list[tuple[list[str], str]] = []

    def spy(icaos: list[str], _airports_data: dict, location: str) -> dict[str, str]:
        calls.append((list(icaos), location))
        return {"KBBB": "Testville Spy"}

    monkeypatch.setattr(
        disambiguator.disambiguation_engine, "disambiguate_multiple_airports", spy
    )

    names = disambiguator.get_full_names_batch(["KAAA", "KBBB"])

    assert len(calls) == 1
    assert calls[0][1] == LOCATION
    assert names == {"KAAA": "Testville Alpha", "KBBB": "Testville Spy"}
