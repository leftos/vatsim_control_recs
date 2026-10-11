"""An airport with no city is named from its own name, and from its ICAO last.

The disambiguator groups airports by city (or by state when the name starts with
it), so an airport whose city is empty never reaches the NER engine. Its display
name then comes from the airport's own name, and the ICAO stands in only when
that name is empty too. Airports known only from iata-icao.csv carry that name
in its "airport" column.
"""

import csv
from pathlib import Path
from typing import Any

import pytest

from airport_disambiguator.disambiguator import AirportDisambiguator
from backend.data.loaders import load_unified_airport_data

ROOT = Path(__file__).resolve().parent.parent

ETIK: dict[str, Any] = {
    "icao": "ETIK",
    "name": "Illesheim Air Base",
    "city": "",
    "state": "Bavaria",
    "country": "DE",
}
NAMELESS: dict[str, Any] = {
    "icao": "ZZZZ",
    "name": "",
    "city": "",
    "state": "",
    "country": "DE",
}


def _make_disambiguator(
    airports: dict[str, dict[str, Any]], lazy_load: bool = True
) -> AirportDisambiguator:
    return AirportDisambiguator(
        airports_file_path="unused.json", lazy_load=lazy_load, unified_data=airports
    )


def test_airport_without_city_is_named_from_its_airport_name() -> None:
    airports = {"ETIK": ETIK}
    disambiguator = AirportDisambiguator(
        airports_file_path="unused.json", unified_data=airports
    )
    name = disambiguator.get_full_name("ETIK")
    assert name.casefold() != "etik"
    assert "Illesheim" in name


def test_pretty_name_of_airport_without_city_is_its_airport_name() -> None:
    disambiguator = _make_disambiguator({"ETIK": ETIK})
    name = disambiguator.get_pretty_name("ETIK")
    assert name.casefold() != "etik"
    assert "Illesheim" in name


@pytest.mark.parametrize("lazy_load", [True, False])
def test_batch_names_of_airport_without_city_are_its_airport_name(
    lazy_load: bool,
) -> None:
    disambiguator = _make_disambiguator({"ETIK": ETIK}, lazy_load=lazy_load)
    full = disambiguator.get_full_names_batch(["ETIK"])["ETIK"]
    pretty = disambiguator.get_pretty_names_batch(["ETIK"])["ETIK"]
    assert "Illesheim" in full
    assert "Illesheim" in pretty


IATA_ICAO_ROWS = (
    '"country_code","region_name","iata","icao","airport","latitude","longitude"\n'
    '"AE","Abu Zaby","AAN","OMAL","Al Ain International Airport","24.2617","55.6092"\n'
    '"DE","Bayern","","ETIK","Illesheim Air Base","49.4739","10.3881"\n'
)


def test_iata_icao_rows_keep_their_airport_name_and_region(tmp_path: Path) -> None:
    iata_icao = tmp_path / "iata-icao.csv"
    iata_icao.write_text(IATA_ICAO_ROWS, encoding="utf-8", newline="\n")

    airports = load_unified_airport_data(
        str(tmp_path / "missing-APT_BASE.csv"),
        str(tmp_path / "missing-airports.json"),
        str(iata_icao),
    )

    assert airports["OMAL"]["name"] == "Al Ain International Airport"
    assert airports["OMAL"]["state"] == "Abu Zaby"
    assert airports["OMAL"]["city"] == ""
    assert airports["OMAL"]["iata"] == "AAN"
    assert airports["ETIK"]["name"] == "Illesheim Air Base"
    assert airports["ETIK"]["state"] == "Bayern"


def test_airport_without_any_name_keeps_its_icao() -> None:
    disambiguator = _make_disambiguator({"ZZZZ": NAMELESS})
    assert disambiguator.get_full_name("ZZZZ") == "ZZZZ"
    assert disambiguator.get_pretty_name("ZZZZ") == "ZZZZ"
    assert disambiguator.get_full_names_batch(["ZZZZ"]) == {"ZZZZ": "ZZZZ"}
    assert disambiguator.get_pretty_names_batch(["ZZZZ"]) == {"ZZZZ": "ZZZZ"}


def test_names_csv_has_no_ident_fallbacks() -> None:
    with (ROOT / "data" / "airport_names.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    fallbacks = [
        row["icao"] for row in rows if row["name"].casefold() == row["icao"].casefold()
    ]
    assert fallbacks == []
