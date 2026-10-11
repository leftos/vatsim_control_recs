"""Characterization of the shared METAR parsing helpers in ``backend/data/weather_parsing.py``.

Pins the exact output of ``parse_visibility_sm``, ``_parse_single_weather``,
``get_flight_category`` and ``parse_weather_phenomena`` (directly, and through
``parse_taf_forecast_details``) over inputs that reach every branch of each, so that
splitting those functions cannot change behaviour without failing here.
"""

import pytest

from backend.briefing.taf_parsing import parse_taf_forecast_details
from backend.data import weather_parsing

# parse_visibility_sm: every form it reads, plus the shapes it falls through on.


@pytest.mark.parametrize(
    ("metar", "expected"),
    [
        # Empty and whitespace-only input.
        ("", None),
        ("   ", None),
        # Whole-number statute miles, with and without trailing components.
        ("KSFO 101251Z 27005KT 10SM CLR 15/05 A2992", 10.0),
        ("KSFO 101251Z 27005KT 3SM BR", 3.0),
        # "P6SM" means more than 6 miles.
        ("KSFO 101251Z 27005KT P6SM SKC", 7),
        # Mixed fraction, whole plus fraction.
        ("KSFO 101251Z 27005KT 1 1/2SM BR", 1.5),
        ("KSFO 101251Z 27005KT 2 1/4SM BR", 2.25),
        # Less-than fraction ("M") comes back slightly under the fraction.
        ("KSFO 101251Z 27005KT M1/4SM FG", 0.24),
        # Simple fraction.
        ("KSFO 101251Z 27005KT 3/4SM BR", 0.75),
        # Meters: 9999 is treated as 10 km or more, anything else is converted.
        ("KSFO 101251Z 27005KT 9999 SKC", 7.0),
        ("KSFO 101251Z 27005KT 0800 BR", 0.49709818931984545),
        # No visibility token at all.
        ("KSFO 101251Z 27005KT", None),
        # "10SM" is taken by the whole-mile pattern, so the 5-digit number is never reached.
        ("KSFO 101251Z 12345 10SM", 10.0),
        # A lone 5-digit number is not read as meters.
        ("KSFO 101251Z 27005KT 12345", None),
        # A "P" elsewhere in the METAR (here "SLP123") is not the "P6SM" prefix.
        ("KSFO 101251Z 27005KT 3SM SLP123", 3.0),
        # The whole-mile pattern also accepts an "M" prefix; only a real "P6SM" adds a mile.
        ("KSFO 101251Z 27005KT M10SM CLR", 10.0),
        # A zero denominator is malformed: refused, not read as its "0SM" tail.
        ("KSFO 101251Z 27005KT 1/0SM BR", None),
        ("KSFO 101251Z 27005KT M1/0SM BR", None),
        ("KSFO 101251Z 27005KT 1 1/0SM BR", None),
        # More than one zero in the denominator is equally malformed.
        ("KSFO 101251Z 27005KT 1/00SM BR", None),
        ("KSFO 101251Z 27005KT M1/00SM BR", None),
        ("KSFO 101251Z 27005KT 1 1/00SM BR", None),
    ],
)
def test_parse_visibility_sm(metar: str, expected: float | None) -> None:
    assert weather_parsing.parse_visibility_sm(metar) == expected


def test_parse_visibility_p6sm_returns_int() -> None:
    """P6SM comes back as the int 7 (the "more than" branch adds 1), not the float 7.0."""
    result = weather_parsing.parse_visibility_sm("KSFO 101251Z 27005KT P6SM SKC")
    assert result == 7
    assert isinstance(result, int)


# _parse_single_weather: empty, each intensity prefix, descriptors, combinations, rejects.


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        # Empty and too short to hold a phenomenon.
        ("", []),
        ("B", []),
        # Bare phenomena.
        ("RA", ["Rain"]),
        ("BR", ["Mist"]),
        ("DZ", ["Drizzle"]),
        ("GR", ["Hail"]),
        # Intensity prefixes.
        ("-RA", ["Light Rain"]),
        ("+RA", ["Heavy Rain"]),
        # Thunderstorm is a descriptor that also becomes a result of its own.
        ("TS", ["Thunderstorm"]),
        ("-TSRA", ["Light Thunderstorm", "Rain"]),
        ("+TSRA", ["Heavy Thunderstorm", "Rain"]),
        # Other descriptors.
        ("FZFG", ["Freezing Fog"]),
        ("FZRA", ["Freezing Rain"]),
        ("SHRA", ["Showers Rain"]),
        ("-SHRA", ["Light Showers Rain"]),
        # A descriptor with no phenomenon after it names itself.
        ("SH", ["Showers"]),
        ("-SH", ["Light Showers"]),
        # Vicinity intensity, with and without a following phenomenon.
        ("VCFG", ["Vicinity Fog"]),
        ("VCSH", ["Vicinity Showers"]),
        # Multiple phenomena in one group, intensity and descriptor applied once.
        ("RAGR", ["Rain", "Hail"]),
        ("+TSRAGR", ["Heavy Thunderstorm", "Rain", "Hail"]),
        # The same phenomenon twice is listed once.
        ("RARA", ["Rain"]),
        # Unknown trailing code stops the parse but keeps what came before.
        ("RAXX", ["Rain"]),
        ("XX", []),
    ],
)
def test_parse_single_weather(code: str, expected: list[str]) -> None:
    assert weather_parsing._parse_single_weather(code) == expected


# get_flight_category: each boundary at and around its limit, and missing components.


@pytest.mark.parametrize(
    ("metar", "expected"),
    [
        # Empty METAR.
        ("", "UNK"),
        # Neither visibility nor a ceiling is known: both stay VFR.
        ("KSFO 101251Z 27005KT", "VFR"),
        ("KSFO 101251Z 27005KT 10SM CLR", "VFR"),
        # Visibility boundaries: LIFR below 1, IFR from 1 to below 3,
        # MVFR from 3 to 5, VFR above 5.
        ("KSFO 101251Z 27005KT 1/2SM SKC", "LIFR"),
        ("KSFO 101251Z 27005KT M1/4SM SKC", "LIFR"),
        ("KSFO 101251Z 27005KT 1SM SKC", "IFR"),
        ("KSFO 101251Z 27005KT 2 3/4SM SKC", "IFR"),
        ("KSFO 101251Z 27005KT 3SM SKC", "MVFR"),
        ("KSFO 101251Z 27005KT 5SM SKC", "MVFR"),
        ("KSFO 101251Z 27005KT 6SM SKC", "VFR"),
        # Ceiling boundaries: LIFR below 500 ft, IFR 500 to below 1000,
        # MVFR 1000 to 3000, VFR above 3000.
        ("KSFO 101251Z 27005KT 10SM BKN004", "LIFR"),
        ("KSFO 101251Z 27005KT 10SM VV002", "LIFR"),
        ("KSFO 101251Z 27005KT 10SM BKN005", "IFR"),
        ("KSFO 101251Z 27005KT 10SM OVC009", "IFR"),
        ("KSFO 101251Z 27005KT 10SM BKN010", "MVFR"),
        ("KSFO 101251Z 27005KT 10SM BKN030", "MVFR"),
        ("KSFO 101251Z 27005KT 10SM BKN031", "VFR"),
        # A ceiling without visibility still decides the category.
        ("KSFO 101251Z 27005KT BKN005", "IFR"),
        # The most restrictive of the two wins, whichever side it came from.
        ("KSFO 101251Z 27005KT 3SM BKN005", "IFR"),
        ("KSFO 101251Z 27005KT 1/2SM BKN004", "LIFR"),
        ("KSFO 101251Z 27005KT 5SM BKN030", "MVFR"),
    ],
)
def test_get_flight_category(metar: str, expected: str) -> None:
    assert weather_parsing.get_flight_category(metar) == expected


# parse_weather_phenomena, and the TAF path that shares it: a bare descriptor reaches both.


@pytest.mark.parametrize(
    ("metar", "expected"),
    [
        # "VCSH" is reported by the full walk, and the remarks section ends the walk.
        (
            "KSFO 101251Z 27005KT 10SM VCSH SCT040 15/05 A2992 RMK AO2 SH DSNT W",
            ["Vicinity Showers"],
        ),
    ],
)
def test_parse_weather_phenomena(metar: str, expected: list[str]) -> None:
    assert weather_parsing.parse_weather_phenomena(metar) == expected


@pytest.mark.parametrize(
    ("conditions", "expected"),
    [("P6SM VCSH SCT040", ["Vicinity Showers"])],
)
def test_parse_taf_forecast_details_phenomena(
    conditions: str, expected: list[str]
) -> None:
    assert parse_taf_forecast_details(conditions)["phenomena"] == expected
