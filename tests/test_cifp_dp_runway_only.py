"""A SID coded with runway transitions only expands to the fixes every runway flies.

The records are real cycle 2610 lines: PANC FFITZ4 (two runway transitions that
meet at EVRTS), PANC NOEND4 (one runway transition), PANC ANCH1 (vectors only,
no fix) and KSEA MONTN2 (runway transitions that end at different fixes).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.data import cifp

RUNWAY_ONLY_LINES = [
    "SCANP PANCPADANCH1 TRW15  010         0        VA                     1489        + 00600     18000       TED   PAD        103452407",
    "SCANP PANCPADANCH1 TRW15  020         0 E      VM                     2010                                                 103462407",
    "SCANP PANCPADANCH1 TRW25B 010         0 E      VM                     2539                    18000                        103472407",
    "SCANP PANCPADANCH1 TRW33  010         0        VA                     3289        + 00600     18000                        103482407",
    "SCANP PANCPADANCH1 TRW33  020         0 E      VM                     3010                                                 103492407",
    "SCANP PANCPADFFITZ44RW25B 010         0        VA                     2540        + 00660     18000                        103501802",
    "SCANP PANCPADFFITZ44RW25B 020VUYWOPAPC0E       DF                                                                          103511802",
    "SCANP PANCPADFFITZ44RW25B 030OGOLYPAPC0E       TF                                                                          103521802",
    "SCANP PANCPADFFITZ44RW25B 040EVRTSPAPC0E       TF                                 + 05000                                  103531802",
    "SCANP PANCPADFFITZ44RW25B 050DCSIXPAPC0E       TF                                                                          103541802",
    "SCANP PANCPADFFITZ44RW25B 060FRIDAPAEA0E       TF                                 + 09000                                  103551802",
    "SCANP PANCPADFFITZ44RW25B 070FFITZPAEA0EE      TF                                                                          103561802",
    "SCANP PANCPADFFITZ44RW33  010         0        VA                     3289        + 00660     18000                        103571802",
    "SCANP PANCPADFFITZ44RW33  020ISACCPAPC0E       DF                                                                          103581802",
    "SCANP PANCPADFFITZ44RW33  030RRROBPAPC0E       TF                                                                          103591802",
    "SCANP PANCPADFFITZ44RW33  040EVRTSPAPC0E       TF                                 + 05000                                  103601802",
    "SCANP PANCPADFFITZ44RW33  050DCSIXPAPC0E       TF                                                                          103611802",
    "SCANP PANCPADFFITZ44RW33  060FRIDAPAEA0E       TF                                 + 09000                                  103621802",
    "SCANP PANCPADFFITZ44RW33  070FFITZPAEA0EE      TF                                                                          103631802",
    "SCANP PANCPADNOEND44RW33  010         0        VA                     3289        + 00652     18000                        103641802",
    "SCANP PANCPADNOEND44RW33  020EGKAJPAPC0E       DF                                 + 03000                                  103651802",
    "SCANP PANCPADNOEND44RW33  030LIFFEPAPC0E       TF                                                  230               -     103661802",
    "SCANP PANCPADNOEND44RW33  040OUTERPAPC0E       TF                                 + 06000                                  103671802",
    "SCANP PANCPADNOEND44RW33  050CRAFTPAPC0E       TF                                 + 08000                                  103681802",
    "SCANP PANCPADNOEND44RW33  060RAMMAPAPC0E       TF                                 + 10000                                  103691802",
    "SCANP PANCPADNOEND44RW33  070NOENDPAPC0EE      TF                                 + 12800                                  103701802",
    "SUSAP KSEAK1DMONTN21RW16C 010         0        VI                     1610                    18000                        127012207",
    "SUSAP KSEAK1DMONTN21RW16C 020PEAKKK1PC0EY      CF SEA K1      1610005016100050D   + 03000                                  127022207",
    "SUSAP KSEAK1DMONTN21RW16C 030         0 E  L   VMY                    0710                                                 127032207",
    "SUSAP KSEAK1DMONTN21RW34C 010         0        VI                     3440                    18000                        127102207",
    "SUSAP KSEAK1DMONTN21RW34C 020NEZUGK1PC0EY      CF SEA K1      3410008034100080D   + 04000                                  127112207",
    "SUSAP KSEAK1DMONTN21RW34C 030         0 E  R   VMY                    0710                                                 127122207",
]


@pytest.fixture(autouse=True)
def _isolate_cifp_state() -> Iterator[None]:
    """Give every test a clean index, before and after it runs."""
    cifp.clear_cifp_cache()
    yield
    cifp.clear_cifp_cache()


@pytest.fixture(autouse=True)
def runway_only_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CIFP lookups at the fixture file instead of the cache."""
    path = tmp_path / "FAACIFP18-2610"
    path.write_text("\n".join(RUNWAY_ONLY_LINES) + "\n", encoding="latin-1")
    monkeypatch.setattr(cifp, "get_cifp_cache_path", lambda: path)
    return path


def test_runway_transitions_that_meet_expand_their_shared_tail() -> None:
    """FFITZ4 flies VUYWO OGOLY off 25 and ISACC RRROB off 33, then EVRTS onward from both."""
    dp = cifp.get_dp_data("PANC", "FFITZ4")

    assert dp is not None
    assert dp.waypoints == ["EVRTS", "DCSIX", "FRIDA", "FFITZ"]


def test_single_runway_transition_expands_its_whole_path() -> None:
    dp = cifp.get_dp_data("PANC", "NOEND4")

    assert dp is not None
    assert dp.waypoints == ["EGKAJ", "LIFFE", "OUTER", "CRAFT", "RAMMA", "NOEND"]


def test_runway_transitions_that_never_meet_expand_nothing() -> None:
    """MONTN2 leaves 16C via PEAKK and 34C via NEZUG: no fix is flown from every runway."""
    assert cifp.get_dp_data("KSEA", "MONTN2") is None


def test_vector_only_sid_expands_nothing() -> None:
    assert cifp.get_dp_data("PANC", "ANCH1") is None
