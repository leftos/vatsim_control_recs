# get_dp_data and SIDs with only runway transitions: measurements, options and fix

Status: **resolved**

Root cause: `get_dp_data` (`backend/data/cifp.py:818-841`) reads waypoints only from a common route (`ALL`/blank) or else the first enroute transition. It has no branch for runway transitions (`RW*`), so a SID coded with runway transitions only leaves `all_waypoints` empty and returns None at `cifp.py:850-851`.

In cycle 2610 that is 265 of the 2029 indexed SIDs: 201 in CONUS, 64 outside it. Only 3 of the 5 PANC SIDs in the plan item have this shape. ANCH1 and POTTR3 carry no fix at all, and ANCH1 and TURN8 are never looked up, because `parse_route_string` skips SID names of 5 characters or fewer (`backend/data/navaids.py:998`).

Rig: worktree `X:/dev/vatsim_control_recs.wt/dbg-dp-shape` (main 1df8cac). The probes read the cached `%LOCALAPPDATA%/VATSIMControlRecs/cache/cifp/FAACIFP18-2610`, with `cifp.get_cifp_cache_path` pinned to it so nothing could download. They also read the cached NASR folder `cache/navaids/2026-10-01`, and checked it was there before any lookup. They ran with `X:/dev/vatsim_control_recs/.venv/Scripts/python.exe .tmp/<probe>.py <cifp path>`. Every probe has been removed.

## Measurements

### 1. SID shapes in cycle 2610, by region (only the SIDs the index holds)

"Shape" is taken from the records that carry a fix, which are the only records `parse_dp_record` keeps. "None" counts the SIDs for which the real `get_dp_data` returned None.

| region | SIDs | common route | enroute, no common | **runway transitions only** | no fix at all (vectors) | get_dp_data None |
|---|---|---|---|---|---|---|
| CONUS (SUSAP K) | 1919 | 1220 | 438 | **201** | 60 | 201 rw-only + 60 vectors + 18 common-route |
| Alaska (SCANP PA) | 59 | 8 | 6 | **43** | 2 | 43 + 2 |
| Hawaii (SPACP PH) | 30 | 9 | 11 | **10** | 0 | 10 |
| PR/USVI (SLAMP TJ/TI) | 12 | 5 | 4 | **3** | 0 | 3 |
| Pacific territories (SPACP) | 9 | 1 | 0 | **8** | 0 | 8 |
| **total** | 2029 | 1243 | 459 | **265** | 62 | |

- Today `get_dp_data` returns None for every runway-only SID: 265 of 265.
- 161 SUSAP SIDs whose ident is not a K one are outside the index and not counted here.
- PANC: FFITZ4, NOEND4 and TURN8 are runway-only. ANCH1 and POTTR3 have only VA/VM/VMY legs with an empty fix field, so there is nothing to expand and None is the correct answer for them.
- Names of 5 characters or fewer are never passed to `get_dp_data` (`navaids.py:998`, `len(part) > 5`):
  - 284 of the 2029 SIDs (runway-only 74, common route 127, enroute 46, vectors 37).
  - The 5 PANC SIDs include ANCH1 and TURN8; the sample data also files ANC1 and MKK5.
  - End to end today: `parse_route_string("TURN8 NAPTO", departure="PANC")` gives `['NAPTO']`, and `"FFITZ4 FFITZ"` gives `['FFITZ']`.

### 2. Do the runway transitions converge? Common-suffix length of the ordered fix lists, after `get_dp_data`'s own filter

| SIDs with only runway transitions | suffix 0 | suffix 1 | suffix 2 | suffix 3+ |
|---|---|---|---|---|
| one runway transition (96): the suffix is the whole path | 1* | 25 | 29 | 41 |
| several runway transitions (169) | 16* | 120 | 20 | 13 |

How many runway transitions each SID has: 1: 96, 2: 108, 3: 18, 4: 34, 6: 1, 7: 8.

- In 125 of the 169 SIDs with several transitions, the shortest transition is itself the tail of all the others.
- Where the suffix is k, the set of fixes every transition shares is exactly the k-fix suffix in all 169 SIDs (it never takes in an earlier fix).
- \* 13 of the 17 SIDs with suffix 0 come from a second defect. They converge on a VOR named like the airport (BOI3, DTA1, EKN1, DIVD2, HLN5, LAR1, LGD1, CRATR1, GRZLY4, MTJ2, ONP1, PRB2, SNS3), and the filter `not w.endswith(airport_code)` (`cifp.py:847`) drops that VOR.
  - With a filter that drops only `RW*` and the airport's own 4-character ident, the 169 split into suffix 0: 4, 1: 129, 2: 25, 3+: 11.
- The 4 SIDs that truly diverge are all vector SIDs that leave different runways by different fixes:
  - KSEA MONTN2: 16x leaves via PEAKK, 34x via NEZUG.
  - KELY ELY2: EPUHE or FAPGI.
  - PHNL HNL2: HAUNA or ALANA.
  - KCEZ LEDVE1: `[CEZ, LEDVE]` against `[ODROE, CEZ]`.

### 3. What the callers do with the result

- The only consumer of the result is ETA route distance:
  - `calculate_eta` (`backend/core/calculations.py:118-131`) calls `get_route_remaining_distance`, then `_get_parsed_route` (lru_cache keyed on `(route, departure, arrival)`), then `parse_route_string`, then `_expand_sid_star` (`navaids.py:826-886`), which reads `dp.waypoints` and adds each resolved fix in order before the next filed token.
  - The callers of `calculate_eta` are `analysis.py:333` (airborne flights within `max_eta_hours`), `flights.py:444` and `:470`, and `flight_info.py:919`.
- Route Weather (`ui/modals/route_weather.py:146`) calls `parse_route_string` without `departure`, so it never expands a SID. MEA does not use SIDs.
- `_calculate_route_remaining_distance` sums only the segments from the aircraft's current segment onward. SID fixes therefore matter only while the aircraft is still flying the SID. After that they are behind it.
- `calculate_eta` keeps a route distance only when it is at least the great-circle distance. An underestimate falls back to the great circle; an overestimate is accepted unchecked.

Distance from the airport through the SID, per option (NASR coordinates, airport reference point from CIFP), in nm. "today" assumes the filed route continues from the SID's last fix.

| SID | rwy | true path | today (None) | (a) common tail | (c) first sorted runway | error today / (a) / (c) |
|---|---|---|---|---|---|---|
| PANC FFITZ4 | 25B | 106.4 | 101.1 | 105.0 | 106.4 | -5.3 / -1.4 / 0.0 |
| PANC FFITZ4 | 33 | 109.2 | 101.1 | 105.0 | 106.4 | -8.1 / -4.2 / -2.8 |
| PANC NOEND4 | 33 | 62.1 | 44.4 | 62.1 | 62.1 | -17.6 / 0.0 / 0.0 |
| KBOS REVSS6 | 04R | 73.4 | 35.7 | 35.7 | 73.4 | -37.7 / -37.7 / 0.0 |
| KBOS REVSS6 | 33L | 36.9 | 35.7 | 35.7 | 73.4 | -1.2 / -1.2 / **+36.5** |
| TJVQ VEDAS5 | 27 | 17.0 | 14.0 | 14.0 | 41.1 | -3.1 / -3.1 / **+24.0** |
| PAHU HUGHE1 | 36 | 51.4 | 24.7 | 24.7 | 29.0 | -26.8 / -26.8 / -22.4 |

Across the 247 runway-only SIDs whose coordinates resolved, measured in nm:

| measure | median | p90 | max | worst case |
|---|---|---|---|---|
| (a)'s remaining shortfall (path to the convergence fix minus the direct distance) | 0.0 | 5.0 | 37.7 | KBOS REVSS6 off 04R |
| (c)'s spread (first sorted runway's whole path against the others, 155 SIDs) | 0.0 | 8.1 | 36.5 | KBOS REVSS6 |

### 4. Is the departure runway known at the call site?

No.

- `calculate_eta(flight, airports_data, speeds)` receives only the pilot record. No ATIS text reaches the ETA chain.
- `parse_approach_info` / `parse_runway_assignments` (`backend/data/atis_filter.py:279`, `:445`) are called only from `ui/modals/flight_board.py:841` and `:919`. Those calls run on that modal's ATIS fetch, VATSIM first, then a D-ATIS fallback over the network.
- `analysis.py` holds the VATSIM `atis` list but parses no runways from it.
- Option (b) would need:
  - a new parameter through `calculate_eta` (4 call sites), `get_route_remaining_distance`, `_get_parsed_route` (a 4th cache key, which churns whenever an ATIS changes), `parse_route_string`, `_expand_sid_star` and `get_dp_data`;
  - a mapping from the ATIS runway ("22L") to the CIFP transition name ("RW22B" / "RW22L");
  - a rule for ATIS that lists several departure runways, or no ATIS at all, both common on VATSIM. That rule falls back to (a) or turns into (c).

## Options with worst cases

- **(a) Expand the common tail every runway transition shares.**
  - Every fix it adds is one the aircraft flies from any runway. Its path is therefore never longer than the true path (triangle inequality), and never shorter than today's when the filed route continues from the SID's exit fix. It cannot be worse than today.
  - Worst case: it gains nothing where the tail is only the exit fix, which the filed route usually names anyway (120 of 169 SIDs with several transitions). It leaves today's underestimate there, up to 37.7 nm on KBOS REVSS6 off 04R, which the great-circle guard partly absorbs.
  - For the 96 SIDs with one runway transition it gives the full path (NOEND4: error 17.6 nm becomes 0).
  - The 4 SIDs that truly diverge stay None.
- **(b) The runway transition that matches the ATIS departure runway, else (a).**
  - It is the only option that is exact for KBOS-type SIDs, and only during the SID phase.
  - The cost is the 6-layer plumbing above, plus a runway mapping and an ambiguity rule. Its worst case on a wrong or ambiguous runway is (c)'s: an overestimate of up to +36.5 nm, which the ETA guard lets through.
- **(c) The first sorted runway transition, as `get_star_data` does (`cifp.py:895-905`).**
  - Worst case: an overestimate of +36.5 nm (KBOS REVSS6 off 33L) or +24.0 nm (TJVQ VEDAS5 off 27). The `>= lateral` guard accepts both, so a wrong runway is worse than today's None.
  - On divergent SIDs (MONTN2) it inserts a fix that a departure off the other runway never flies.

**Recommendation: (a).** It is the only choice that is never worse than today, it needs no new input at the call site, and it gives full paths for the 96 SIDs with a single transition. Plan (b) only if a measurement shows that ETA error during the SID phase matters to a user.

## Minimal fix

- **What to add.** In `backend/data/cifp.py`, `get_dp_data`, add a third fallback after the enroute-transition branch (`cifp.py:829-841`) and before the filter (`cifp.py:843-848`), taken when `all_waypoints` is still empty. Put it in a helper, e.g. `_shared_runway_tail(dp_records: dict[str, list[tuple[str, int]]]) -> list[str]`, so that `get_dp_data` stays within complexity 8.
- **What the helper returns.** For every transition whose name starts with `RW`, it takes the fixes sorted by sequence and deduplicated, as the other branches do. It returns their longest common suffix:
  - one runway transition gives its whole list;
  - no shared suffix, or no runway transition, gives `[]`, and `get_dp_data` returns None as it does today.
  The existing `RW*`/airport filter then runs unchanged.
- **Docs to update.** The `CifpDP.waypoints` comment (`cifp.py:96`), the `get_dp_data` docstring and the `parse_route_string` docstring (`navaids.py:898`, "common route waypoints") gain "or the tail all runway transitions share".
- **What it must not touch:**
  - `get_star_data`'s first-runway fallback;
  - the order common route first, then enroute;
  - the `endswith(airport_code)` filter;
  - the `len(part) > 5` gate in `navaids.py`.
  The last two are separate defects, listed under the leads below.

## Failing test

`tests/test_cifp_dp_runway_only.py`, functions:

- `test_runway_transitions_that_meet_expand_their_shared_tail`: **red today**, `assert None is not None`.
- `test_single_runway_transition_expands_its_whole_path`: **red today**.
- `test_runway_transitions_that_never_meet_expand_nothing`: green today; it guards against (c).
- `test_vector_only_sid_expands_nothing`: green today.

Run from `.tmp/` with `python -m pytest -q -p no:cacheprovider .tmp/test_cifp_dp_runway_only.py`, the result was `2 failed, 2 passed in 1.38s`. The expected lists come from the CIFP records' sequence order, not from the code under test. The first test also fails for option (c), which would give `VUYWO OGOLY ...`. The third fails for option (c) and passes for (a).

```python
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
```

## Hypotheses ruled out

- **"All five PANC SIDs need a runway-transition fallback."** ANCH1 and POTTR3 have no fix-bearing record in 2610 (raw lines `SCANP PANCPADANCH1 T...`, `...POTTR3T...` are VA/VM/VMY legs only), so None is correct for them.
- **"Fixing get_dp_data makes TURN8 and ANCH1 expand."** They are 5 characters long and `navaids.py:998` requires more than 5, so `parse_route_string("TURN8 NAPTO", departure="PANC")` never calls `get_dp_data`.
- **"Runway transitions often diverge, so a common tail rarely exists."** Only 4 of the 169 SIDs with several transitions diverge once the co-named-VOR filter is set aside. 165 share at least their last fix.
- **"Picking the first runway transition, as for STARs, is safe."** It overestimates by up to +36.5 nm, and the ETA guard accepts overestimates.
- **"The departure runway is available where the SID is expanded."** No ATIS reaches `calculate_eta` or `_get_parsed_route`.

## Other leads found (not part of this item)

1. **`cifp.py:847` `not w.endswith(airport_code)` drops navaids named like the airport.**
   - 41 distinct (airport, fix) pairs and 71 SIDs are affected: ABQ, DEN, MCI, MEM, MSP, SAT, STL, RDU and others.
   - It makes 18 CONUS common-route SIDs return None, e.g. KDEN PLAIN2 with common route `['DEN']`. The enroute fallback is skipped because the list was not empty before the filter.
   - It also empties 13 runway-only SIDs.
   - Dropping only `RW*` and `w == ident` (the 4-character airport ident, e.g. `KASE`, `KSFO`) would fix it.
   - `get_star_data` has the same filter at `cifp.py:911`.
2. **`navaids.py:998` (and the airway look-ahead at `:967`) skips SID and STAR names of 5 characters or fewer.**
   - 284 of 2029 SIDs, e.g. TURN8, ANCH1, MKK5, ANC1.
   - The STAR count is unmeasured.
3. **`tests/test_cifp_index.py::test_airport_cifp_cannot_hold_never_opens_the_file` (line 204) takes no `cifp_file` fixture.**
   - Its first `get_dp_data("KOAK", "CNDEL5")` goes through `ensure_cifp_data` to the FAA download in the session temp dir.
   - With `HTTPS_PROXY=http://127.0.0.1:9` it fails: `assert None is not None` at line 207.
   - So the test needs the network, against CLAUDE.md's "never touch the network". Its name is also stale: PANC and PAFA are indexed now.

## DOC DRIFT

- `docs/plans/MAIN.md:44` says that all five PANC SIDs return None "so their routes skip SID expansion".
  - ANCH1 and POTTR3 have no fixes, so None is correct for them.
  - ANCH1 and TURN8 skip expansion because of the name-length gate in `navaids.py:998`, not because of `get_dp_data`.
- `tests/test_cifp_index.py:204`: the test name "airport CIFP cannot hold" no longer matches the code; PANC and PAFA are indexed since the P/T areas were added.
- `CLAUDE.md` "Tests ... never touch the network" does not hold for the test above.
- `docs/README.md` glossary has "SID / STAR" but no "common route", "runway transition" or "enroute transition". The fix's docstrings will use these terms, so it adds entries for them.

## Tree state

- `git status --short` in the worktree printed nothing (no tracked changes).
- Scratch files written and then removed:
  - `.tmp/dp_shape.py`, `.tmp/dp_shape.json`, `.tmp/dp_shape.log`;
  - `.tmp/dp_shape_report.py`, `.tmp/report.log`;
  - `.tmp/dp_filter_probe.py`, `.tmp/dp_filter.log`;
  - `.tmp/dp_e2e_probe.py`, `.tmp/dp_e2e.log`;
  - `.tmp/dp_dist_probe.py`, `.tmp/dp_dist.log`;
  - `.tmp/fixture_lines.txt`, `.tmp/test_cifp_dp_runway_only.py`, `.tmp/red.log`, `.tmp/net.log`.
- No runtime was launched beyond the probe processes, which have exited.
- No download happened: CIFP was pinned to the 2610 cache, and NASR existence was asserted before use.

## Lessons for the project's docs

- **For `docs/ARCHITECTURE.md`, CIFP lookups entry or integration footguns:**
  - In the CIFP SID records, column 19 is the route type, which is why the raw id reads e.g. `FFITZ44`.
  - Vector legs (VA/VI/VM) have an empty fix field and are dropped by `parse_dp_record`.
  - SIDs coded only as runway transitions are common outside the busiest airports: 265 of 2029.
- **For the testing section of `CLAUDE.md` or `docs/ARCHITECTURE.md`:** to measure against the real cycle without downloading, pin `cifp.get_cifp_cache_path` to the cached `FAACIFP18-<cycle>`, and check that `navaids.get_nasr_cache_path()` holds NAV, FIX and AWY before calling `parse_route_string`.
