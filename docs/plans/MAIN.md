# Plan
<!-- plan-doc-hygiene: 2026-10-10 d84f1fc -->

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

### Wave 1: Refresh performance

The refresh's cost structure and how to measure it are in [performance.md](../performance.md). Shared: `backend/data/weather.py`, `backend/core/analysis.py`, `ui/tables.py`. Review: `code-review`.

Verification: `uv run --group dev pytest -q` (Pilot tests on fixture data) is the command; the human check is a run with `--countries US` and an unfiltered run, measured as performance.md's "Measuring" section gives.

- [ ] A refresh's table update waits for its slowest weather box (18–20 s per slow box in the re-measure, `backend/core/analysis.py:349-358`).

  Settled (owner, lane B of the [refresh mock](https://claude.ai/artifact/NswGm7DKtQdjXRBZifXVo5)): the traffic columns update as soon as traffic is ready; each region's WIND and ALT keep their previous values until that region's box returns, and an airport new this refresh shows them blank until then. No dimming or pending marker. It builds on the in-place table refresh (`ui/app.py` `_apply_refresh_data`, `ui/tables.py`).

  Settled (owner): lands on `main`. A failed box (timeout, 429) keeps its airports' previous WIND/ALT; an airport a successful box omits goes blank. Fresh (cached) boxes are delivered by the weather pass's first callback, just after the traffic update. Startup is two-stage too: the first paint shows traffic with blank WIND/ALT, then fills them in. The second VATSIM download and the Go To rebuild stay before the table update.
- [ ] `tests/test_table_refresh.py::test_shrinking_table_fades_then_removes_tail_rows` failed once in a heavy-slot full suite ("assert 12 == 25, the tail rows go only after their fade") and passed in isolation and on later full runs: a load-sensitive timing wait in a Pilot test (`ui/tables.py` fade). Make it wait on the fade's completion, not elapsed time.

## Next up

### Wave 2: Go To and groupings

Design in [goto-ux-review.md](./goto-ux-review.md). Shared: `ui/modals/goto_modal.py`, `ui/app.py` (`_build_goto_results`, the flight-board open path, `_open_weather_briefing_callback`), `ui/modals/tracked_airports.py`, grouping file I/O. Review: `code-review` plus a UI look at each screen. Verification: Pilot tests in `tests/` are the command; the human check is screenshots of each mode from a filtered run (`--groupings "Bay Area"`).

Order (from the map of this wave): R3 removes the edit mode R4's wording would relabel, so R4 splits, its filters half first and its wording sweep last; the `_fill_option_list` split lands before R5, which edits the same hunks; R5 lands before R3.

- [ ] Split `GoToScreen._filter_results` (complexity 11) and `_execute_selected` (10) in `ui/modals/goto_modal.py`, then enable ruff's `C901` with `max-complexity = 8` in `ruff.toml` (no complexity rule is enforced today) and split whatever else it reports or backlog it.

  Settled (owner): a ratchet. Enable `C901` at 8 with a `per-file-ignores` list in `ruff.toml` naming every other file that still has an offender (98 functions in 44 files when measured), and **add** a backlog item to split them and shrink the list; no source file outside `goto_modal.py` changes.
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

- [ ] Investigate the MEA check: when an airway's entry or exit fix is not found, every segment of the airway is checked (`backend/data/navaids.py:1165-1175`), so `ABQ V12 ONM` reported a California V12 segment at 29,000 ft. Seen by the screenshot run, not yet confirmed.
- [ ] Investigate the MEA warning comparing only the first filed altitude, so step climbs are ignored (`ui/modals/flight_info.py:661-668`); live UAE82M filed step climbs to FL400 and was warned against a 33,000 ft MEA. Not yet confirmed.
- [ ] Index the CIFP procedures of Alaska, Hawaii and the territories: they sit under the `SCANP` area prefix (818 SID/STAR lines for PANC, PHNL, TJSJ, PAFA and others), which the `SUSAP K…` match in `backend/data/cifp.py:674` (and the `SUSAP` checks at `:403`, `:531`, `:568`) never reads, so those airports' routes skip SID/STAR expansion.

  Settled (owner): index `SCANP` records whose airport ident starts with `P` or `T` (Alaska, Hawaii, Puerto Rico, Pacific territories); the Canadian records in that area stay out.
- [ ] Investigate the overview's NEXT ETA showing `52h06m` for LFBO, from one long-haul flight from Melbourne. Not yet confirmed.

## Backlog

### Singles

Items that share no files. Review: `code-review`. Verification: `uv run --group dev pytest -q` plus `ruff check`.

- [ ] Investigate display names that fall back to the ICAO code ("ETIK - Etik" in the VFR finder) and two UK weather-briefing areas both named "LONDON AREA". Not yet confirmed.
- [ ] Cut cold-start weather boxes: `calculate_airport_bboxes` (`backend/data/weather.py`) makes one box per occupied grid cell sized to the airports' overall span, not the 10° maximum, so 30 US airports took 14 bbox requests and 17.8 s on a cold start (live check while landing perf fix 5).
- [ ] Add `docs/README.md` as the docs' start page, linking `docs/ARCHITECTURE.md`, `docs/atis-filtering-and-runway-extraction.md`, `USER_GUIDE.md`, `CLAUDE.md` and this plan: the repo has no docs index, so the architecture entry point is reachable only from `CLAUDE.md` (line 83). Give it a glossary for the project's terms: AIRAC, bbox (a weather bounding-box request), CIFP, clean empty and negative cache (a cached "no METAR" answer), MEA, SID/STAR, and the terms the explorers found undefined: box, `as_of`, wave (a refresh where most boxes expired), traffic (columns), weather hash, stage (daemon), tile version.
- [ ] `ruff` is in no dependency group (`pyproject.toml` `[dependency-groups]`), so `uv run ruff check .` fails with "program not found" although `ruff.toml` and the prek ruff hook (`.pre-commit-config.yaml`, v0.16.1) exist; implementers fall back to `uvx ruff@0.16.1`. Add `ruff` to the `dev` group at the hook's version.
- [ ] Raise ruff's `target-version` from `py310` (`ruff.toml:6`) to `py313` to match `requires-python` in `pyproject.toml`, and land the UP-rule rewrites it triggers as their own commit.
