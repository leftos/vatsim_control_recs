"""Characterization of the verbose disambiguation trace.

Pins the exact stdout of ``verbose_trace_disambiguation`` over inputs that reach
each of its branches: an ICAO unknown to the unified data (an early return), an
airport the data manager does not know, a missing base location, a name that
matches its city, one that does not, an airport with no city, and caches that
are already populated.

The disambiguator is a fake collaborator, as in
``tests/test_area_clustering_characterization.py``: the function under test only
prints what its collaborators return.
"""

from airport_disambiguator.__main__ import verbose_trace_disambiguation

BAR = "=" * 80


def _trace(*lines: str) -> str:
    return "".join(f"{line}\n" for line in lines)


class _FakeDataManager:
    def __init__(
        self,
        details: dict[str, dict] | None,
        location: str | None,
        airports_in_location: list[str],
    ) -> None:
        self._details = details or {}
        self._location = location
        self._airports_in_location = airports_in_location

    def get_airport_details(self, icao: str) -> dict | None:
        return self._details.get(icao)

    def get_location_for_airport(self, _icao: str) -> str | None:
        return self._location

    def get_airports_in_location(self, _location: str) -> list[str]:
        return self._airports_in_location


class _FakeDisambiguator:
    def __init__(
        self,
        *,
        details: dict[str, dict] | None = None,
        location: str | None = None,
        airports_in_location: list[str] | None = None,
        pretty_results: dict[str, str] | None = None,
        full_results: dict[str, str] | None = None,
        pretty_cache: dict[str, str] | None = None,
        full_cache: dict[str, str] | None = None,
    ) -> None:
        self.data_manager = _FakeDataManager(
            details, location, airports_in_location or []
        )
        self._pretty_results = pretty_results or {}
        self._full_results = full_results or {}
        self.icao_to_pretty_name = dict(pretty_cache or {})
        self.icao_to_full_name = dict(full_cache or {})

    def get_pretty_name(self, icao: str) -> str:
        name = self._pretty_results.get(icao, icao)
        self.icao_to_pretty_name[icao] = name
        return name

    def get_full_name(self, icao: str) -> str:
        name = self._full_results.get(icao, icao)
        self.icao_to_full_name[icao] = name
        return name


KSFO = {
    "name": "San Francisco International",
    "city": "San Francisco",
    "state": "California",
    "country": "US",
}


def test_trace_of_unknown_icao_stops_after_step_one(capsys) -> None:
    result = verbose_trace_disambiguation("KZZZ", {}, _FakeDisambiguator())

    assert result == "KZZZ"
    assert capsys.readouterr().out == _trace(
        "",
        BAR,
        "VERBOSE TRACE FOR: KZZZ",
        BAR,
        "[STEP 1] ICAO KZZZ NOT FOUND in unified_data",
    )


def test_trace_of_known_icao_prints_every_step(capsys) -> None:
    disambiguator = _FakeDisambiguator(
        details={
            "KSFO": {
                "name": "San Francisco Intl",
                "city": "San Francisco",
                "state": "California",
            }
        },
        location="San Francisco",
        airports_in_location=["KSFO", "KOAK"],
        pretty_results={"KSFO": "San Francisco"},
        full_results={"KSFO": "San Francisco International"},
    )

    result = verbose_trace_disambiguation("KSFO", {"KSFO": KSFO}, disambiguator)

    assert result == "San Francisco"
    assert capsys.readouterr().out == _trace(
        "",
        BAR,
        "VERBOSE TRACE FOR: KSFO",
        BAR,
        "[STEP 1] Unified data for KSFO:",
        "         name:    'San Francisco International'",
        "         city:    'San Francisco'",
        "         state:   'California'",
        "         country: 'US'",
        "",
        "[STEP 2] Data manager airport details:",
        "         name:  'San Francisco Intl'",
        "         city:  'San Francisco'",
        "         state: 'California'",
        "",
        "[STEP 3] Base location for KSFO: 'San Francisco'",
        "",
        "[STEP 4] Airports in location 'San Francisco': ['KSFO', 'KOAK']",
        "         Count: 2",
        "",
        "[STEP 5] name_contains_location check:",
        "         airport_name: 'San Francisco Intl'",
        "         city:         'San Francisco'",
        "         state:        'California'",
        "         name_words:   ['San', 'Francisco', 'Intl']",
        "         city_words:   ['san', 'francisco']",
        "         MATCH: 'San'.lower() == 'san'",
        "",
        "[STEP 6] Disambiguation caches (before get_pretty_name):",
        "         icao_to_pretty_name has KSFO: False",
        "         icao_to_full_name has KSFO:   False",
        "",
        "[STEP 7] Final results:",
        "         get_pretty_name(KSFO): 'San Francisco'",
        "         get_full_name(KSFO):   'San Francisco International'",
        "",
        "[STEP 8] Disambiguation caches (after get_pretty_name):",
        "         icao_to_pretty_name[KSFO]: 'San Francisco'",
        "         icao_to_full_name[KSFO]:   'San Francisco International'",
        BAR,
        "",
    )


def test_trace_of_airport_the_data_manager_lacks(capsys) -> None:
    disambiguator = _FakeDisambiguator(pretty_results={}, full_results={})

    result = verbose_trace_disambiguation("KZZZ", {"KZZZ": KSFO}, disambiguator)

    assert result == "KZZZ"
    assert capsys.readouterr().out == _trace(
        "",
        BAR,
        "VERBOSE TRACE FOR: KZZZ",
        BAR,
        "[STEP 1] Unified data for KZZZ:",
        "         name:    'San Francisco International'",
        "         city:    'San Francisco'",
        "         state:   'California'",
        "         country: 'US'",
        "",
        "[STEP 2] Data manager airport details:",
        "         NOT FOUND in data_manager",
        "",
        "[STEP 3] Base location for KZZZ: None",
        "",
        "[STEP 6] Disambiguation caches (before get_pretty_name):",
        "         icao_to_pretty_name has KZZZ: False",
        "         icao_to_full_name has KZZZ:   False",
        "",
        "[STEP 7] Final results:",
        "         get_pretty_name(KZZZ): 'KZZZ'",
        "         get_full_name(KZZZ):   'KZZZ'",
        "",
        "[STEP 8] Disambiguation caches (after get_pretty_name):",
        "         icao_to_pretty_name[KZZZ]: 'KZZZ'",
        "         icao_to_full_name[KZZZ]:   'KZZZ'",
        BAR,
        "",
    )


def test_trace_without_a_name_match(capsys) -> None:
    disambiguator = _FakeDisambiguator(
        details={"KAAA": {"name": "Alpha Field", "city": "Beta", "state": "ZZ"}},
        location="Beta",
        airports_in_location=["KAAA", "KBBB"],
    )

    verbose_trace_disambiguation("KAAA", {"KAAA": KSFO}, disambiguator)

    assert capsys.readouterr().out == _trace(
        "",
        BAR,
        "VERBOSE TRACE FOR: KAAA",
        BAR,
        "[STEP 1] Unified data for KAAA:",
        "         name:    'San Francisco International'",
        "         city:    'San Francisco'",
        "         state:   'California'",
        "         country: 'US'",
        "",
        "[STEP 2] Data manager airport details:",
        "         name:  'Alpha Field'",
        "         city:  'Beta'",
        "         state: 'ZZ'",
        "",
        "[STEP 3] Base location for KAAA: 'Beta'",
        "",
        "[STEP 4] Airports in location 'Beta': ['KAAA', 'KBBB']",
        "         Count: 2",
        "",
        "[STEP 5] name_contains_location check:",
        "         airport_name: 'Alpha Field'",
        "         city:         'Beta'",
        "         state:        'ZZ'",
        "         name_words:   ['Alpha', 'Field']",
        "         city_words:   ['beta']",
        "         NO MATCH found between name_words and city_words",
        "",
        "[STEP 6] Disambiguation caches (before get_pretty_name):",
        "         icao_to_pretty_name has KAAA: False",
        "         icao_to_full_name has KAAA:   False",
        "",
        "[STEP 7] Final results:",
        "         get_pretty_name(KAAA): 'KAAA'",
        "         get_full_name(KAAA):   'KAAA'",
        "",
        "[STEP 8] Disambiguation caches (after get_pretty_name):",
        "         icao_to_pretty_name[KAAA]: 'KAAA'",
        "         icao_to_full_name[KAAA]:   'KAAA'",
        BAR,
        "",
    )


def test_trace_matches_a_city_word_split_on_separators(capsys) -> None:
    disambiguator = _FakeDisambiguator(
        details={
            "KRNO": {"name": "Reno Stead", "city": "Reno/Stead", "state": "Nevada"}
        },
        location="",
    )

    verbose_trace_disambiguation("KRNO", {"KRNO": KSFO}, disambiguator)

    out = capsys.readouterr().out
    assert "         city_words:   ['reno', 'stead']" in out
    assert "         MATCH: 'Reno'.lower() == 'reno'" in out


def test_trace_without_a_city_skips_the_city_check(capsys) -> None:
    disambiguator = _FakeDisambiguator(
        details={"KAAA": {"name": "Alpha Field", "city": "", "state": "ZZ"}},
        location="",
    )

    verbose_trace_disambiguation("KAAA", {"KAAA": KSFO}, disambiguator)

    assert capsys.readouterr().out == _trace(
        "",
        BAR,
        "VERBOSE TRACE FOR: KAAA",
        BAR,
        "[STEP 1] Unified data for KAAA:",
        "         name:    'San Francisco International'",
        "         city:    'San Francisco'",
        "         state:   'California'",
        "         country: 'US'",
        "",
        "[STEP 2] Data manager airport details:",
        "         name:  'Alpha Field'",
        "         city:  ''",
        "         state: 'ZZ'",
        "",
        "[STEP 3] Base location for KAAA: ''",
        "",
        "[STEP 5] name_contains_location check:",
        "         airport_name: 'Alpha Field'",
        "         city:         ''",
        "         state:        'ZZ'",
        "         name_words:   ['Alpha', 'Field']",
        "",
        "[STEP 6] Disambiguation caches (before get_pretty_name):",
        "         icao_to_pretty_name has KAAA: False",
        "         icao_to_full_name has KAAA:   False",
        "",
        "[STEP 7] Final results:",
        "         get_pretty_name(KAAA): 'KAAA'",
        "         get_full_name(KAAA):   'KAAA'",
        "",
        "[STEP 8] Disambiguation caches (after get_pretty_name):",
        "         icao_to_pretty_name[KAAA]: 'KAAA'",
        "         icao_to_full_name[KAAA]:   'KAAA'",
        BAR,
        "",
    )


def test_trace_reports_caches_that_are_already_populated(capsys) -> None:
    disambiguator = _FakeDisambiguator(
        details={"KSFO": {"name": "San Francisco Intl", "city": "San", "state": "CA"}},
        location="San",
        pretty_cache={"KSFO": "Cached Pretty"},
        full_cache={"KSFO": "Cached Full"},
    )

    verbose_trace_disambiguation("KSFO", {"KSFO": KSFO}, disambiguator)

    assert capsys.readouterr().out == _trace(
        "",
        BAR,
        "VERBOSE TRACE FOR: KSFO",
        BAR,
        "[STEP 1] Unified data for KSFO:",
        "         name:    'San Francisco International'",
        "         city:    'San Francisco'",
        "         state:   'California'",
        "         country: 'US'",
        "",
        "[STEP 2] Data manager airport details:",
        "         name:  'San Francisco Intl'",
        "         city:  'San'",
        "         state: 'CA'",
        "",
        "[STEP 3] Base location for KSFO: 'San'",
        "",
        "[STEP 4] Airports in location 'San': []",
        "         Count: 0",
        "",
        "[STEP 5] name_contains_location check:",
        "         airport_name: 'San Francisco Intl'",
        "         city:         'San'",
        "         state:        'CA'",
        "         name_words:   ['San', 'Francisco', 'Intl']",
        "         city_words:   ['san']",
        "         MATCH: 'San'.lower() == 'san'",
        "",
        "[STEP 6] Disambiguation caches (before get_pretty_name):",
        "         icao_to_pretty_name has KSFO: True",
        "         icao_to_full_name has KSFO:   True",
        "         cached pretty_name: 'Cached Pretty'",
        "         cached full_name:   'Cached Full'",
        "",
        "[STEP 7] Final results:",
        "         get_pretty_name(KSFO): 'KSFO'",
        "         get_full_name(KSFO):   'KSFO'",
        "",
        "[STEP 8] Disambiguation caches (after get_pretty_name):",
        "         icao_to_pretty_name[KSFO]: 'KSFO'",
        "         icao_to_full_name[KSFO]:   'KSFO'",
        BAR,
        "",
    )


def test_trace_reports_a_partially_populated_cache(capsys) -> None:
    disambiguator = _FakeDisambiguator(
        details={"KSFO": {"name": "San Francisco Intl", "city": "San", "state": "CA"}},
        location="San",
        pretty_cache={"KSFO": "Cached Pretty"},
    )

    verbose_trace_disambiguation("KSFO", {"KSFO": KSFO}, disambiguator)

    out = capsys.readouterr().out
    assert "         icao_to_pretty_name has KSFO: True" in out
    assert "         icao_to_full_name has KSFO:   False" in out
    assert "         cached pretty_name: 'Cached Pretty'" in out
    assert "         cached full_name:   " not in out
