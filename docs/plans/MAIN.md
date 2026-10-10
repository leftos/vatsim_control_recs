# Plan
<!-- plan-doc-hygiene: 2026-10-10 d84f1fc -->

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

### Wave 1: Refresh performance

The refresh's cost structure and how to measure it are in [performance.md](../performance.md). Shared: `backend/data/weather.py`, `backend/core/analysis.py`, `ui/tables.py`. Review: `code-review`.

Verification: `uv run --group dev pytest -q` (Pilot tests on fixture data) is the command; the human check is a run with `--countries US` and an unfiltered run, measured as performance.md's "Measuring" section gives.

- [ ] `tests/test_table_refresh.py::test_shrinking_table_fades_then_removes_tail_rows` failed once in a heavy-slot full suite ("assert 12 == 25, the tail rows go only after their fade") and passed in isolation and on later full runs: a load-sensitive timing wait in a Pilot test (`ui/tables.py` fade). Make it wait on the fade's completion, not elapsed time.

## Next up

### Wave 2: Go To and groupings

Design in [goto-ux-review.md](./goto-ux-review.md). Shared: `ui/modals/goto_modal.py`, `ui/app.py` (`_build_goto_results`, the flight-board open path, `_open_weather_briefing_callback`), `ui/modals/tracked_airports.py`, grouping file I/O. Review: `code-review` plus a UI look at each screen. Verification: Pilot tests in `tests/` are the command; the human check is screenshots of each mode from a filtered run (`--groupings "Bay Area"`).

Order (from the map of this wave): R3 removes the edit mode R4's wording would relabel, so R4 splits, its filters half first and its wording sweep last; the `_fill_option_list` split lands before R5, which edits the same hunks; R5 lands before R3.

- [ ] Go To list hygiene (R5 in [goto-ux-review.md](./goto-ux-review.md)): ★ groupings first with section headers, a "showing 100 of N" row, collapsed members for selected groupings, the "Selected: …" summary line (it repeats the title's pick counts and stays visible after Tab back to browse), and the dead `filter_type="$"` path removed (`ui/app.py` `_build_goto_results`, `ui/modals/goto_modal.py`).

  Settled (owner): a picked grouping shows one collapsed "▸ N airports (→ to expand)" row, and → expands it to member rows, where `F` works per member. The "Selected: …" line is replaced, in pick several only, by one dim line naming the picks, truncated ("Picked: Bay Area Arrivals, KLAX, +2").

  Section headers are upper-case and dim ("★ MY GROUPINGS", "AIRPORTS (tracked only)", "GROUPINGS", "FLIGHTS"), a section with no rows has none, and a `@`/`#`/`$` prefix shows only its own. Past 100 rows the last row reads "showing 100 of N — type to narrow", N counting matching rows after the prefix and flight hiding, headers and member rows excluded.

  Settled from the code: the ★-first ordering is computed app side in `_build_goto_results`, whose cache save and delete already invalidate; header rows never sit at index 0 or the last index (`_handle_focus_move`), and `_display_items` stays 1:1 with the options.
- [ ] Go To grouping builder (R3 in [goto-ux-review.md](./goto-ux-review.md)): a separate screen that adds any airport from the unified data, lists every member with its filter, removes or excludes members, and asks before overwriting or discarding; grouping file I/O moves into `backend/core/groupings.py`.

  Settled (owner): `E` (opens the builder prefilled) and `Ctrl+D` (delete after a confirm) stay on ★ rows in Go To. Untracked members are allowed with a "not tracked" mark, and saving offers to add them to the tracked list. A name equal to a preset is refused ("that name belongs to a preset").

  "Copy to my groupings" from a preset row is a separate later item, below. The backend is strict and atomic: deleting a missing name raises (the UI shows an error toast), renaming onto an existing name raises (the builder asks first, then calls an explicit overwrite), and every write goes to a temp file that replaces `favorites.json`.
- [ ] Copy a preset into my groupings: a key on a preset row in Go To opens the R3 builder prefilled with the preset's airports under a new name. After R3.
- [ ] One name for saved groupings (R4's wording half, [goto-ux-review.md](./goto-ux-review.md)): "my groupings" (★) everywhere instead of favorite, grouping and custom grouping, in UI text and docs (`favorites.json` and `custom_groupings.json` keep their names; the weather daemon's "custom groupings" means the data file and stays).

  Settled (owner): the main tab "Custom Groupings" becomes "Groupings" (it lists presets, ARTCC groupings and ★ ones); only user-visible strings and docs change, and code identifiers keep `favorite`, bridged by a glossary line.

### Wave 3: Flight info and route checks

Shared: `backend/data/navaids.py`, `ui/modals/flight_info.py`, the ETA path in `backend/core/`. Review: `code-review` with an aviation read of each verdict. Verification: each item first reproduces on fixture or live data; a confirmed defect gets a red test before its fix.

- [ ] Fix the MEA check's airway walk (`backend/data/navaids.py` `get_max_mea_for_route`, `_parse_awy_record`, `_parse_awy1_record`, `load_airways`, `load_airway_restrictions`; shown by `ui/modals/flight_info.py`). Measured by a debugger run against NASR 2026-10-01 and live data, three faults: (a) a missing entry or exit fix checks the whole airway (`ABQ V12 ONM`: ONM is not on V12), and endpoint tokens keep their `/N0490F400` speed/level text so they never match; (b) the airway type (column 10: blank federal, A Alaska, H Hawaii) is ignored, so Hawaii V12 overwrites federal V12 sequences (the "California 29,000 ft" segment) and the AWY2 regex misreads all 1,716 Alaska/Hawaii point records; (c) the AWY1 record at sequence N is the segment leaving point N, but the code treats it as the one ending there (UAE82M's 33,000 ft was MLF→URNUW, a segment it never flew). The minimal fix and a red test (`tests/test_mea_check.py`) are in [wave3-route-checks.md](./wave3-route-checks.md).

  Settled (owner): a missing endpoint skips that airway with a log line and shows a dim "MEA not checked: V12 (ONM not on it)" line in flight info, only when one end was found. The same missing-endpoint rule goes into the ETA path (`get_airway_fixes`, `navaids.py:714`), which today expands a missing endpoint to the whole airway (`ABQ V12 ONM` → 3,082 nm vs 42 nm direct). Settled from the code: key airways by (designation, type) and use the type whose points contain both endpoints; strip speed/level text from endpoint tokens as `parse_route_string` does.
- [ ] Compare each MEA segment against the level filed for it: a `FIX/N0490F400` group sets the level from that fix on (FAA Appendix A, Item 15), but the warning reads only `flight_plan["altitude"]` (`ui/modals/flight_info.py:660-682`), so a step climb before an airway is ignored. Give `MeaViolation` the level in force at the airway's entry. After the airway-walk fix (same function).
- [ ] 854 AWY1 segment records carry a different MEA for the opposite direction (V1 sequence 160: 2,100 ft one way, 5,000 the other); `get_max_mea_for_route` always reads the first value. Read the direction of travel. After the airway-walk fix.
- [ ] NEXT ETA takes the minimum over every airborne inbound, ignoring `max_eta_hours` (`backend/core/analysis.py:239-247`), so LFBO showed `52h06m` from a YMML departure seconds after takeoff while ARR showed 0. Settled (owner): NEXT ETA comes only from the flights the ARR column counts: move the update into the `is_flight_flying_near_arrival` branch (~:265). The fix and a red test (`tests/test_next_eta_window.py`) are in [wave3-route-checks.md](./wave3-route-checks.md).
- [ ] The overview's ARR count and the flight board pick arrivals with different predicates: great circle over groundspeed (`backend/core/flights.py:130-145`) vs `calculate_eta <= max_eta_hours` (`flights.py:448`, `:474`); sample BAW372 is counted at 0.93 h and left off the board at 1.0002 h. Use one predicate.
- [ ] Non-US filed routes resolve idents against US NASR only (`navaids.py:416`, `:446`, `:1039`): AIB256's `CAN` resolves to 47.41N 122.84W and its `TNT T420 WELIN` splices in US T420; a garbled route longer than the great circle is accepted (`calculations.py:130`, 10,992 nm vs 4,900). Skip route distance when the departure or arrival is outside NASR coverage, or bound route over great circle.
- [ ] `get_dp_data` (`backend/data/cifp.py`) returns None for a SID whose records carry only runway transitions (no `ALL`/blank common route and no enroute transition): all five PANC SIDs in cycle 2610 (ANCH1, FFITZ4, NOEND4, POTTR3, TURN8), so their routes skip SID expansion. Measure how many CONUS SIDs share the shape, then decide which runway transition to expand (or the common fixes they share).

## Backlog

### Singles

Items that share no files. Review: `code-review`. Verification: `uv run --group dev pytest -q` plus `ruff check`.

- [ ] Investigate display names that fall back to the ICAO code ("ETIK - Etik" in the VFR finder) and two UK weather-briefing areas both named "LONDON AREA". Not yet confirmed.
- [ ] Cut cold-start weather boxes: `calculate_airport_bboxes` (`backend/data/weather.py`) makes one box per occupied grid cell sized to the airports' overall span, not the 10° maximum, so 30 US airports took 14 bbox requests and 17.8 s on a cold start (live check while landing perf fix 5).
- [ ] Docs drift found by the debuggers: `CLAUDE.md` "ETA Calculation" says the approach speed covers the final 20 nm (the code uses 5 nm, `backend/core/calculations.py:189`) and says nothing of which flights feed NEXT ETA; "Flight Categorization" implies the ETA model decides the arrivals window (the overview uses distance/groundspeed); the `docs/ARCHITECTURE.md` "MEA warning" Task Index row names `route.py`/`cifp.py`/`route_weather.py` instead of `navaids.py` → `flight_info.py`; Test locations miss `test_allowlist_filter.py`, `test_disambiguator_csv_names.py`, `test_spatial_find_nearest.py`; `--groupings "Bay Area"` (CLAUDE.md example, Wave 2 verification) names no grouping in the data, so it tracks every airport (use `O90`).
- [ ] Burn down the `C901` ratchet: `ruff.toml` `[lint.per-file-ignores]` lists 45 files (102 functions) that predate the max-complexity 8 rule. Split a file's functions under 8 and take it off the list; `uvx ruff@0.16.1 check --select C901 <file>` with the entry removed lists them. Biggest: `backend/data/weather.py` and `backend/data/navaids.py` (6 each).
- [ ] Raise ruff's `target-version` from `py310` (`ruff.toml:6`) to `py313` to match `requires-python` in `pyproject.toml`, and land the UP-rule rewrites it triggers as their own commit.
