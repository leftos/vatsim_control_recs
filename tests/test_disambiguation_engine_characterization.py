"""Characterization of ``DisambiguationEngine._disambiguate_location_starts``.

Pins the pretty names the engine resolves for groups of airports whose names all
start with the location, over inputs that reach each candidate strategy: a unique
proper name (tried before generic descriptors), a generic descriptor, a
high-priority word combination, a sequential combination, and the last-resort
fallback to the airport's own name. An airport with no distinguishing words takes
the location itself.
"""

from airport_disambiguator.config import DEFAULT_CONFIG
from airport_disambiguator.disambiguation_engine import DisambiguationEngine
from airport_disambiguator.name_processor import NameProcessor


class _StubEntityExtractor:
    def extract_distinguishing_entity(self, *_args, **_kwargs):
        raise AssertionError("location starts must not consult the NER engine")


def _engine() -> DisambiguationEngine:
    return DisambiguationEngine(
        DEFAULT_CONFIG, NameProcessor(DEFAULT_CONFIG), _StubEntityExtractor()
    )


def test_location_starts_of_no_airports_is_empty() -> None:
    assert _engine()._disambiguate_location_starts([], {}, "Springfield") == {}


def test_an_airport_named_only_for_its_location_takes_the_location() -> None:
    resolved = _engine()._disambiguate_location_starts(
        ["KRNO"], {"KRNO": "Reno"}, "Reno"
    )

    assert resolved == {"KRNO": "Reno"}


def test_unique_proper_names_are_kept() -> None:
    resolved = _engine()._disambiguate_location_starts(
        ["KSTE", "KRTS"], {"KSTE": "Reno Stead", "KRTS": "Reno Tahoe"}, "Reno"
    )

    assert resolved == {"KSTE": "Reno Stead", "KRTS": "Reno Tahoe"}


def test_a_group_may_mix_an_airport_without_distinguishing_words() -> None:
    resolved = _engine()._disambiguate_location_starts(
        ["KA", "KB"], {"KA": "Reno Stead", "KB": "Reno"}, "Reno"
    )

    assert resolved == {"KA": "Reno Stead", "KB": "Reno"}


def test_generic_descriptors_split_ties_when_proper_names_are_absent() -> None:
    resolved = _engine()._disambiguate_location_starts(
        ["KA", "KB"],
        {"KA": "Springfield Municipal North", "KB": "Springfield Municipal South"},
        "Springfield",
    )

    assert resolved == {"KA": "Springfield North", "KB": "Springfield South"}


def test_a_proper_name_conflict_falls_through_to_a_generic_descriptor() -> None:
    resolved = _engine()._disambiguate_location_starts(
        ["KA", "KB"], {"KA": "Reno Stead North", "KB": "Reno Stead"}, "Reno"
    )

    assert resolved == {"KA": "Reno North", "KB": "Reno Stead"}


def test_high_priority_word_combinations_break_a_tie() -> None:
    resolved = _engine()._disambiguate_location_starts(
        ["KA", "KB", "KC"],
        {
            "KA": "Springfield Municipal North Downtown",
            "KB": "Springfield North Municipal",
            "KC": "Springfield Municipal Downtown",
        },
        "Springfield",
    )

    assert resolved == {
        "KA": "Springfield North Downtown",
        "KB": "Springfield North Municipal",
        "KC": "Springfield Municipal Downtown",
    }


def test_a_group_without_a_unique_name_falls_back_to_the_airport_name() -> None:
    resolved = _engine()._disambiguate_location_starts(
        ["KA", "KB"], {"KA": "Reno Stead", "KB": "Reno Stead North"}, "Reno"
    )

    assert resolved == {"KA": "Reno Stead", "KB": "Reno North"}


def test_a_lone_proper_name_is_preferred_over_a_generic_descriptor() -> None:
    resolved = _engine()._disambiguate_location_starts(
        ["KA", "KB"], {"KA": "Reno Stead North", "KB": "Reno Tahoe"}, "Reno"
    )

    assert resolved == {"KA": "Reno Stead", "KB": "Reno Tahoe"}


def test_a_sequential_combination_settles_a_group_of_conflicting_words() -> None:
    resolved = _engine()._disambiguate_location_starts(
        ["KA", "KB", "KC"],
        {
            "KA": "Reno Stead Tahoe Lake",
            "KB": "Reno Stead Lake",
            "KC": "Reno Tahoe Lake",
        },
        "Reno",
    )

    assert resolved == {
        "KA": "Reno Stead Tahoe",
        "KB": "Reno Stead Lake",
        "KC": "Reno Tahoe Lake",
    }


def test_a_location_written_in_another_case_takes_the_location_not_the_name() -> None:
    resolved = _engine()._disambiguate_location_starts(
        ["KRNO"], {"KRNO": "RENO"}, "Reno"
    )

    assert resolved == {"KRNO": "Reno"}
