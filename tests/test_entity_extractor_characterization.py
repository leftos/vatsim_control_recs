"""Characterization of ``EntityExtractor.extract_entities``.

Real airport names run through the real ``en_core_web_sm`` model, loaded once per
module, pin the person and location entities it returns. A stub returning chosen
entities reaches the branches real names do not: person entities, an entity that
is the city or state, a generic descriptor, entities that clean down to nothing,
an ignored label, the pattern-based locations and the cache.
"""

import pytest

from airport_disambiguator.config import DEFAULT_CONFIG
from airport_disambiguator.entity_extractor import EntityExtractor


class _StubEntity:
    def __init__(self, text: str, label: str) -> None:
        self.text = text
        self.label_ = label


class _StubDoc:
    def __init__(self, entities: list[_StubEntity]) -> None:
        self.ents = entities


class _StubNlp:
    """Stands in for the spaCy model where a real name cannot reach a branch."""

    def __init__(self, entities: list[_StubEntity]) -> None:
        self._entities = entities
        self.calls = 0

    def __call__(self, _text: str) -> _StubDoc:
        self.calls += 1
        return _StubDoc(list(self._entities))


def _extractor_with_stub(
    entities: list[_StubEntity],
) -> tuple[EntityExtractor, _StubNlp]:
    extractor = EntityExtractor(DEFAULT_CONFIG)
    stub = _StubNlp(entities)
    extractor._nlp = stub
    return extractor, stub


@pytest.fixture(scope="module")
def real_extractor() -> EntityExtractor:
    return EntityExtractor(DEFAULT_CONFIG)


@pytest.mark.parametrize(
    ("name", "city", "state", "expected"),
    [
        (
            "San Francisco International",
            "San Francisco",
            "California",
            ([], ["San Francisco"]),
        ),
        ("Boulder County", "Boulder", "Colorado", ([], ["Boulder County"])),
        ("Bob Hope Airport", "Burbank", "California", (["Bob Hope"], [])),
        ("Will Rogers World", "Oklahoma City", "Oklahoma", (["Will Rogers"], [])),
        ("Reno/Stead", "Reno", "Nevada", ([], [])),
        ("Daugherty Field", "Long Beach", "California", ([], [])),
        ("Beale AFB", "Marysville", "California", ([], [])),
        ("Chicago O'Hare International", "Chicago", "Illinois", ([], [])),
    ],
)
def test_real_airport_names(
    real_extractor: EntityExtractor,
    name: str,
    city: str,
    state: str,
    expected: tuple[list[str], list[str]],
) -> None:
    assert real_extractor.extract_entities(name, city, state) == expected


def test_a_person_entity_is_returned_cleaned() -> None:
    extractor, _ = _extractor_with_stub([_StubEntity("Bob Hope", "PERSON")])

    assert extractor.extract_entities("Bob Hope Airport", "Burbank", "CA") == (
        ["Bob Hope"],
        [],
    )


def test_a_location_entity_is_returned_cleaned() -> None:
    extractor, _ = _extractor_with_stub([_StubEntity("Springfield Municipal", "GPE")])

    assert extractor.extract_entities("Springfield Municipal", "", "") == (
        [],
        ["Springfield"],
    )


def test_an_entity_equal_to_the_city_is_skipped() -> None:
    extractor, _ = _extractor_with_stub([_StubEntity("Denver", "PERSON")])

    assert extractor.extract_entities("Denver Field", "Denver", "CO") == ([], [])


def test_an_entity_equal_to_the_city_ignores_case() -> None:
    extractor, _ = _extractor_with_stub([_StubEntity("DENVER", "PERSON")])

    assert extractor.extract_entities("Denver Field", "denver", "CO") == ([], [])


def test_an_entity_equal_to_the_state_is_skipped() -> None:
    extractor, _ = _extractor_with_stub([_StubEntity("Colorado", "GPE")])

    assert extractor.extract_entities("Colorado Field", "", "Colorado") == ([], [])


def test_a_generic_descriptor_entity_is_skipped() -> None:
    extractor, _ = _extractor_with_stub([_StubEntity("International", "GPE")])

    assert extractor.extract_entities("X International", "", "") == ([], [])


def test_a_person_entity_that_cleans_to_nothing_is_skipped() -> None:
    extractor, _ = _extractor_with_stub(
        [_StubEntity("International Regional", "PERSON")]
    )

    assert extractor.extract_entities("X", "", "") == ([], [])


def test_a_location_entity_that_cleans_to_nothing_is_skipped() -> None:
    extractor, _ = _extractor_with_stub([_StubEntity("Regional National", "FAC")])

    assert extractor.extract_entities("X", "", "") == ([], [])


def test_an_unhandled_label_is_ignored() -> None:
    extractor, _ = _extractor_with_stub([_StubEntity("Foo", "ORG")])

    assert extractor.extract_entities("Foo Field", "", "") == ([], [])


def test_a_pattern_location_is_extracted_without_entities() -> None:
    extractor, _ = _extractor_with_stub([])

    assert extractor.extract_entities("Boulder County", "", "") == (
        [],
        ["Boulder County"],
    )


def test_a_pattern_word_in_the_first_position_is_not_a_location() -> None:
    extractor, _ = _extractor_with_stub([])

    assert extractor.extract_entities("County Line", "", "") == ([], [])


def test_a_pattern_word_preceded_by_a_generic_descriptor_is_not_a_location() -> None:
    extractor, _ = _extractor_with_stub([])

    assert extractor.extract_entities("Regional County", "", "") == ([], [])


def test_a_pattern_location_duplicate_of_an_entity_is_removed() -> None:
    extractor, _ = _extractor_with_stub([_StubEntity("Boulder County", "GPE")])

    assert extractor.extract_entities("Boulder County", "", "") == (
        [],
        ["Boulder County"],
    )


def test_duplicate_person_entities_keep_their_first_order() -> None:
    extractor, _ = _extractor_with_stub(
        [
            _StubEntity("Kelly", "PERSON"),
            _StubEntity("Adams", "PERSON"),
            _StubEntity("Kelly", "PERSON"),
        ]
    )

    assert extractor.extract_entities("X", "", "") == (["Kelly", "Adams"], [])


def test_a_repeated_name_is_served_from_the_cache() -> None:
    extractor, stub = _extractor_with_stub([])

    first = extractor.extract_entities("Alpha Field", "Alpha", "AA")
    second = extractor.extract_entities("Alpha Field", "Alpha", "AA")

    assert first == second == ([], [])
    assert stub.calls == 1


def test_the_cache_key_covers_city_and_state() -> None:
    extractor, stub = _extractor_with_stub([])

    extractor.extract_entities("Alpha Field", "Alpha", "AA")
    extractor.extract_entities("Alpha Field", "Beta", "AA")

    assert stub.calls == 2


def test_the_cache_evicts_its_oldest_entry() -> None:
    extractor, _ = _extractor_with_stub([])
    extractor.MAX_CACHE_SIZE = 2

    extractor.extract_entities("Alpha Field", "", "")
    extractor.extract_entities("Bravo Field", "", "")
    extractor.extract_entities("Charlie Field", "", "")

    assert list(extractor._entity_cache) == ["Bravo Field||", "Charlie Field||"]


def test_a_loc_entity_is_returned_as_a_location() -> None:
    extractor, _ = _extractor_with_stub([_StubEntity("Portland", "LOC")])

    assert extractor.extract_entities("Portland Field", "", "") == ([], ["Portland"])


def test_a_cached_entry_is_refreshed_on_access() -> None:
    extractor, stub = _extractor_with_stub([])
    extractor.MAX_CACHE_SIZE = 2

    extractor.extract_entities("Alpha Field", "", "")
    extractor.extract_entities("Bravo Field", "", "")
    extractor.extract_entities("Alpha Field", "", "")
    extractor.extract_entities("Charlie Field", "", "")

    assert stub.calls == 3
    assert list(extractor._entity_cache) == ["Alpha Field||", "Charlie Field||"]


def test_a_ner_location_and_a_pattern_location_are_both_returned(
    real_extractor: EntityExtractor,
) -> None:
    assert real_extractor.extract_entities("Springfield Boulder County", "", "") == (
        [],
        ["Springfield", "Boulder County", "Springfield Boulder County"],
    )
