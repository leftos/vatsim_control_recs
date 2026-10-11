# Plan
<!-- plan-doc-hygiene: 2026-10-10 7e1d80a -->

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

### Wave 2: Go To and groupings

Design in [goto-ux-review.md](./goto-ux-review.md). Shared: `ui/modals/goto_modal.py`, `ui/app.py` (`_build_goto_results`, the flight-board open path, `_open_weather_briefing_callback`), `ui/modals/tracked_airports.py`, grouping file I/O. Review: `code-review` plus a UI look at each screen. Verification: Pilot tests in `tests/` are the command; the human check is screenshots of each mode from a filtered run (`--groupings O90`, the Bay TRACON preset).

Order (from the map of this wave): R3 removes the edit mode R4's wording would relabel, so R4 splits, its filters half first and its wording sweep last; the `_fill_option_list` split lands before R5, which edits the same hunks; R5 lands before R3.

- [ ] Go To grouping builder (R3 in [goto-ux-review.md](./goto-ux-review.md)): a separate screen that adds any airport from the unified data, lists every member with its filter, removes or excludes members, and asks before overwriting or discarding; grouping file I/O moves into `backend/core/groupings.py`. `branch: feat/grouping-builder` (#7).

  Settled (owner): `E` (opens the builder prefilled) and `Ctrl+D` (delete after a confirm) stay on ★ rows in Go To. Untracked members are allowed with a "not tracked" mark, and saving offers to add them to the tracked list. A name equal to a preset is refused ("that name belongs to a preset").

  Settled (owner): `branch: feat/grouping-builder`, a draft PR into `main` merged once every R3 brief is green (map: `.tmp/map-goto-r3.md`, four briefs: backend API, builder screen, wire-in, docs). Excluding a member of a nested grouping replaces the nested ref with its airports minus that one (the file shape stays; the ★ stops following the preset). Go To's pick-several `Ctrl+S` opens the builder prefilled with the picks and their filters, the only save path, so `SaveGroupingModal` goes. With an empty tracked list (track everything) there is no "not tracked" mark and no add offer.

  Settled (owner, on the mocks in `canvases/grouping-builder-mocks/`): two panes (Add left, Members right, Name on top, lane A1) when the terminal is wide enough for them, else one list whose Tab flips between Members and Add (lane A2). An untracked member carries a `○` before its ICAO, with "○ not tracked" in the hint line (lane B2). A preset's name is refused case-insensitively, as are blank and ICAO-like names; an existing ★ that already shadows a preset keeps its name until renamed. Saving with untracked members asks every time: "N of these aren't tracked (ICAOs). Add them to your tracked airports?" with Add and save / Save only / Cancel.

  Settled (owner): two panes from 110 columns (the width at which lane A1's names stop truncating). In the builder Enter adds the highlighted result, Del removes a member, F cycles its filter, → expands a nested grouping, Ctrl+S saves, Esc cancels (asking first when changed); there is no delete in the builder (Ctrl+D in Go To). Members are stored in the order added, and filters of airports no longer in the grouping are dropped on save. The Add search covers every airport in the unified data (ICAO or FAA ident, name, city) plus presets and ★ groupings, tracked ones first, capped at 50 rows with "type to narrow", with Go To's `@` and `$` prefixes. Saving from the Tracked Airports Manager's S returns to the manager with "Saved '{name}' with N airports".

  Settled (owner): confirm texts: overwrite "'{name}' is already one of my groupings. Replace it?"; rename onto an existing name "'{new}' is already one of my groupings. Replace it with this one? ('{old}' is removed)"; discard "Discard your changes to '{name}'?"; delete (Go To Ctrl+D) "Delete '{name}' from my groupings?". Settled from the code: the builder calls the backend itself and shows its errors; it dismisses with `None` on cancel, else a result carrying the saved name, the saved items and the airports to track, which the caller applies (the Tracked Airports Manager must not write `app.airport_allowlist` behind its own copy); Go To patches its local groupings from that result; the "already one of my groupings" check is case-insensitive (overwrite replaces that entry in place under the new spelling; a case-only rename is a rename of the same entry); an "ICAO-like" name is one equal, case-insensitively, to an ident (key, ICAO, FAA or IATA) in the unified airport data; dropping filters of airports no longer in the grouping is the builder's job, since a filter may key a nested grouping's member; the constructor takes `items`, `filters`, `name` and `original_name` (set means editing), so Copy-preset needs no new signature.

  "Copy to my groupings" from a preset row is a separate later item, below. The backend is strict and atomic: deleting a missing name raises (the UI shows an error toast), renaming onto an existing name raises (the builder asks first, then calls an explicit overwrite), and every write goes to a temp file that replaces `favorites.json`.

  - [x] Backend: strict grouping write API (`backend/core/groupings.py`) and `AirportSearchIndex` (`backend/core/airport_search.py`). Landed on feat/grouping-builder in 3a49c6a, ships with #7.
  - [x] Draft model (`ui/modals/grouping_draft.py`, Textual-free) and a three-way `ChoiceModal`. Landed on feat/grouping-builder in 15bf40e, ships with #7. The builder must not save before the unified airport data loads (`preset_grouping_names` would miss the ARTCC names).
  - [x] Builder screen (`ui/modals/grouping_builder.py`). Landed on feat/grouping-builder in 3a1af29, ships with #7. Two panes from 110 columns, else one list with a Tab toggle; keys, `○` mark, confirms. It drives `GroupingDraft` and acts on each `SavePlan` kind: data-not-loaded → warning toast "airport data is still loading; try again in a moment"; self-nesting → error toast `SELF_NESTING_MESSAGE`; ask-track → `ChoiceModal` (its "Cancel" value and Esc's `None` both cancel); `GroupingNameError` → inline message under the Name field; `GroupingExistsError` → the overwrite confirm; any other `GroupingError` → error toast. Empty query in the Add search shows a dim "type to search airports and groupings"; the Add list shows `○` on untracked airports and tags rows already in the grouping "member".
  - [x] Wire-in. Landed on feat/grouping-builder in cc769de, ships with #7. Owner ruling at review: an E save keeps pick-several and its picks; only the Ctrl+S save leaves it. The Tracked Airports manager now returns a `TrackingChange` delta. Go To `E`, `Ctrl+S` and `Ctrl+D`, the Tracked Airports Manager's `S`, `VATSIMControlApp.track_airports`; Go To's edit mode, `SaveGroupingModal` and the raw `favorites.json` writers (`ui/modals/save_grouping.py`, `goto_modal.py` `_remove_favorite_from_file`) removed, which makes the ARCHITECTURE "Write a ★ grouping" footgun true.
  - [x] Docs and screenshots of each builder state. Landed on feat/grouping-builder in 39ecf91, ships with #7. `CLAUDE.md`, `README.md` and `USER_GUIDE.md` describe the builder; 32 screenshots of every state were reviewed (kept untracked in the feature worktree's `.tmp/shots/`).
  - [ ] Builder fixes from the screenshot review, then the feature PR #7 merges by `/ship`. Saving checks the name rules (blank, preset name, airport ident) before self-nesting, so a name such as `O90` or `KSFO` that is also a member shows the inline name message instead of the "a grouping can't contain itself" toast (`grouping_draft.py` save plan). The overwrite confirm comes before the ask-track choice, so answering no to it does not discard a tracking answer. Long Add rows are truncated to one line instead of wrapping under the `○` and ident columns.

    Settled from the R3 ruling ("tracked ones first"): the Add search puts tracked airports first across all match tiers, then by tier (for "sac", tracked KSMF before untracked KSAC and the SAC* idents; `backend/core/airport_search.py` sorts tier first today).

    Settled (owner): the hint line drops "Tab next pane" before "← collapse", so a member row of an expanded grouping keeps "← collapse" beside the "○ not tracked" legend. Airport rows show the app's display names (the disambiguator Go To and the main table use), not the raw all-capitals FAA names. A new grouping's discard confirm is "Discard this new grouping?"; editing keeps "Discard your changes to '{name}'?" with the saved name.
- [ ] Copy a preset into my groupings: a key on a preset row in Go To opens the R3 builder prefilled with the preset's airports under a new name. After R3.

  Settled (owner): the key is `E` on a preset row (presets cannot be edited, so `E` there opens a copy instead of the "can't edit" warning), and the copy's name starts as "{preset} copy" (e.g. "O90 copy"), so `Ctrl+S` saves it at once; the members are the preset's airports.
- [ ] One name for saved groupings (R4's wording half, [goto-ux-review.md](./goto-ux-review.md)): "my groupings" (★) everywhere instead of favorite, grouping and custom grouping, in UI text and docs (`favorites.json` and `custom_groupings.json` keep their names; the weather daemon's "custom groupings" means the data file and stays).

  Settled (owner): the main tab "Custom Groupings" becomes "Groupings" (it lists presets, ARTCC groupings and ★ ones); only user-visible strings and docs change, and code identifiers keep `favorite`, bridged by a glossary line.

### Wave 3: Flight info and route checks

Shared: `backend/data/navaids.py`, `ui/modals/flight_info.py`, the ETA path in `backend/core/`. Review: `code-review` with an aviation read of each verdict. Verification: each item first reproduces on fixture or live data; a confirmed defect gets a red test before its fix.

- [ ] `parse_route_string` treats only names longer than 5 characters as SIDs or STARs (`backend/data/navaids.py`: `_is_route_endpoint`, which the airway-run look-ahead `_airway_run_ahead` shares, and the main loop's "Expand SID/STAR names" branch, a second copy of the regex), skipping 247 of 1,967 SIDs and 71 of 1,750 STARs on cycle 2610 (MKK5, TURN8, DPK3); every short id is 3-4 letters plus one digit. Map: `.tmp/map-short-procs.md` (16 of the test data's flights expand a real procedure, 0 MEA results change, HAL70's route distance becomes accepted).

  Settled (owner): one helper decides "procedure name" for the main loop, `_is_route_endpoint` and `_airway_run_ahead`: `_PROCEDURE_RE` and longer than 5 characters, or `^[A-Z]{3,4}\d$` and not a fix, navaid or airport ident (0 collisions with NASR's 70,129 fixes); a short name whose lookup finds no procedure falls through to the normal lookup. Lands on main (owner: no feature branch).

## Backlog

### Singles

Items that share no files. Review: `code-review`. Verification: `uv run --group dev pytest -q` plus `ruff check`.

- [ ] Investigate display names that fall back to the ICAO code ("ETIK - Etik" in the VFR finder) and two UK weather-briefing areas both named "LONDON AREA". Not yet confirmed.
- [ ] Docs drift found by the debuggers: Test locations miss `test_allowlist_filter.py`, `test_disambiguator_csv_names.py`, `test_spatial_find_nearest.py`; the root `README.md` Go To key table lacks the → and ← rows (`CLAUDE.md` has them).
- [ ] Re-read `docs/performance.md` against the 6 commits on its 10 named files since beb750c (newest `backend/data/weather.py`, d0bb731's first-fit box packing); found by the doc-drift scan.
- [ ] Burn down the `C901` ratchet: `ruff.toml` `[lint.per-file-ignores]` lists 45 files (102 functions) that predate the max-complexity 8 rule. Split a file's functions under 8 and take it off the list; `uvx ruff@0.16.1 check --select C901 <file>` with the entry removed lists them. Biggest: `backend/data/weather.py` and `backend/data/navaids.py` (6 each).
- [ ] Raise ruff's `target-version` from `py310` (`ruff.toml:6`) to `py313` to match `requires-python` in `pyproject.toml`, and land the UP-rule rewrites it triggers as their own commit.
