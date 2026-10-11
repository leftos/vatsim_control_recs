"""safe_filename_part: a free-text name made safe to put in a file name."""

import pytest

from common.paths import safe_filename_part


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "ABQ Albuquerque ATCT/TRACON", "ABQ Albuquerque ATCT_TRACON", id="slash"
        ),
        pytest.param("Bay Area", "Bay Area", id="spaces-kept"),
        pytest.param("Zürich-Nord (1).a", "Zürich-Nord (1).a", id="others-kept"),
        pytest.param("", "", id="empty"),
    ],
)
def test_examples(text: str, expected: str) -> None:
    assert safe_filename_part(text, 60) == expected


@pytest.mark.parametrize("character", list('/\\:*?"<>|'))
def test_each_forbidden_character_becomes_an_underscore(character: str) -> None:
    assert safe_filename_part(f"a{character}b", 60) == "a_b"


@pytest.mark.parametrize("character", ["\x00", "\t", "\n", "\x1f"])
def test_a_control_character_becomes_an_underscore(character: str) -> None:
    assert safe_filename_part(f"a{character}b", 60) == "a_b"


def test_a_space_the_first_code_point_past_the_controls_is_kept() -> None:
    assert safe_filename_part("a b", 60) == "a b"


def test_a_long_name_is_cut_to_the_limit() -> None:
    assert safe_filename_part("x" * 59 + "/yz", 60) == "x" * 59 + "_"


def test_a_name_at_the_limit_is_kept_whole() -> None:
    assert safe_filename_part("x" * 60, 60) == "x" * 60
