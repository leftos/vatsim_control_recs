"""Airport search over the unified airport data (no Textual)."""

from typing import Any

import pytest

from backend.core.airport_search import AirportHit, AirportSearchIndex

UNIFIED: dict[str, dict[str, Any]] = {
    "KSFO": {
        "icao": "KSFO",
        "iata": "SFO",
        "faa": "SFO",
        "name": "San Francisco International Airport",
        "city": "San Francisco",
        "country": "US",
        "artcc": "ZOA",
    },
    "KOAK": {
        "icao": "KOAK",
        "iata": "OAK",
        "faa": "OAK",
        "name": "Metropolitan Oakland International Airport",
        "city": "Oakland",
        "country": "US",
        "artcc": "ZOA",
    },
    "KSAC": {
        "icao": "KSAC",
        "iata": "SAC",
        "faa": "SAC",
        "name": "Sacramento Executive Airport",
        "city": "Sacramento",
        "country": "US",
        "artcc": "ZOA",
    },
    "KSMF": {
        "icao": "KSMF",
        "iata": "SMF",
        "faa": "SMF",
        "name": "Sacramento International Airport",
        "city": "Sacramento",
        "country": "US",
        "artcc": "ZOA",
    },
    "KMCC": {
        "icao": "KMCC",
        "iata": "MCC",
        "faa": "MCC",
        "name": "Mc Clellan Airfield",
        "city": "Sacramento",
        "country": "US",
        "artcc": "ZOA",
    },
    "0Q5": {
        "icao": "",
        "iata": "",
        "faa": "0Q5",
        "name": "Shelter Cove Airport",
        "city": "Shelter Cove",
        "country": "US",
    },
    "T67": {
        "icao": "",
        "iata": "",
        "faa": "T67",
        "name": "Hicks Airfield",
        "city": "Fort Worth",
        "country": "US",
    },
    "OAKX": {
        "icao": "",
        "iata": "",
        "faa": "OAKX",
        "name": "Oakland Test Field",
        "city": "Oakland",
        "country": "US",
    },
}


@pytest.fixture
def index() -> AirportSearchIndex:
    """An index over the small fixture of real-shaped rows."""
    return AirportSearchIndex(UNIFIED)


def idents(hits: list[AirportHit]) -> list[str]:
    """The idents of a result list, in order."""
    return [hit.ident for hit in hits]


def test_an_empty_query_returns_nothing(index: AirportSearchIndex) -> None:
    assert index.search("", frozenset(), 50) == []
    assert index.search("   ", frozenset(), 50) == []


def test_an_exact_ident_match_is_a_tier_zero_hit(index: AirportSearchIndex) -> None:
    hits = index.search("ksfo", frozenset(), 50)

    assert idents(hits)[0] == "KSFO"


def test_an_exact_iata_match_is_a_tier_zero_hit(index: AirportSearchIndex) -> None:
    hits = index.search("sfo", frozenset(), 50)

    assert idents(hits)[0] == "KSFO"


def test_a_faa_only_ident_is_searchable(index: AirportSearchIndex) -> None:
    hits = index.search("0q5", frozenset(), 50)

    assert idents(hits)[0] == "0Q5"


def test_an_exact_ident_beats_a_tracked_prefix_match(index: AirportSearchIndex) -> None:
    hits = index.search("oak", frozenset({"OAKX"}), 50)

    assert idents(hits)[0] == "KOAK"


def test_hits_carry_the_row_fields_and_the_tracked_flag(
    index: AirportSearchIndex,
) -> None:
    hits = index.search("ksfo", frozenset({"KSFO"}), 50)

    assert hits[0] == AirportHit(
        ident="KSFO",
        name="San Francisco International Airport",
        city="San Francisco",
        country="US",
        tracked=True,
    )


def test_an_ident_prefix_beats_a_name_substring(index: AirportSearchIndex) -> None:
    hits = index.search("ks", frozenset(), 50)

    assert idents(hits) == ["KSAC", "KSFO", "KSMF", "T67"]


def test_name_and_city_substring_hits_rank_tracked_rows_first(
    index: AirportSearchIndex,
) -> None:
    hits = index.search("sacramento", frozenset({"KSAC", "KSMF"}), 50)

    assert idents(hits) == ["KSAC", "KSMF", "KMCC"]
    assert [hit.tracked for hit in hits] == [True, True, False]


def test_a_query_matching_more_rows_than_the_limit_returns_exactly_the_limit(
    index: AirportSearchIndex,
) -> None:
    hits = index.search("k", frozenset(), 2)

    assert len(hits) == 2


def test_search_with_total_counts_every_match_beyond_the_limit(
    index: AirportSearchIndex,
) -> None:
    every_hit = index.search("k", frozenset(), 1000)

    hits, total = index.search_with_total("k", frozenset(), 2)

    assert hits == every_hit[:2]
    assert total == len(every_hit)
    assert total > 2


def test_search_with_total_of_a_blank_query_is_nothing(
    index: AirportSearchIndex,
) -> None:
    assert index.search_with_total("  ", frozenset(), 50) == ([], 0)
