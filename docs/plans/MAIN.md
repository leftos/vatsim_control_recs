# Plan
<!-- plan-doc-hygiene: 2026-10-10 3dc6deb -->

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

### Wave 1: Refresh performance

Performance fixes from [perf-profile.md](./perf-profile.md) (measured hotspots, gains and risks per fix there), in its rank order. Shared: `ui/app.py` refresh path, `backend/data/` (`cifp.py`, `weather.py`), `backend/core/` (`analysis.py`, `spatial.py`), `airport_disambiguator/`. Review: `code-review`.

Verification: `uv run --group dev pytest -q` (Pilot tests on fixture data) is the command; the human check is a run with `--countries US` showing refreshes land every interval, re-measured as the profile did.

- [ ] Re-measure with fixes 1-6 landed: a `--countries US` run and an unfiltered run under the profile's method (refreshes landing per interval, CPU per refresh, UI stalls, peak RSS), record the new figures beside the old in [perf-profile.md](./perf-profile.md), then promote its lasting findings into `docs/` and retire the subplan.

## Next up

### Wave 2: Go To and groupings

Design in [goto-ux-review.md](./goto-ux-review.md). Shared: `ui/modals/goto_modal.py`, `ui/app.py` (`_build_goto_results`, the flight-board open path, `_open_weather_briefing_callback`), `ui/modals/tracked_airports.py`, grouping file I/O. Review: `code-review` plus a UI look at each screen. Verification: Pilot tests in `tests/` are the command; the human check is screenshots of each mode from a filtered run (`--groupings "Bay Area"`).

Order (from the map of this wave): R3 removes the edit mode R4's wording would relabel, so R4 splits, its filters half first and its wording sweep last; the `_fill_option_list` split lands before R5, which edits the same hunks; R5 lands before R3.

- [ ] Split `GoToScreen._filter_results` (complexity 11) and `_execute_selected` (10) in `ui/modals/goto_modal.py`, then enable ruff's `C901` with `max-complexity = 8` in `ruff.toml` (no complexity rule is enforced today) and split whatever else it reports or backlog it.
- [ ] Remove the dead `name == "airports"` branch of `_open_weather_briefing_callback` (`ui/app.py:1207-1209`): Go To's callback sends only `(grouping, airports)`, `(icao, [icao])` and `("flight", data)` (`ui/modals/goto_modal.py:1119-1130`), so typed ICAO lists never reach it; the picker title at `:1195` still promises "Enter airports".

  Settled (owner): the picker title becomes "Weather Briefing: select an airport, grouping or flight" (with the `PICKER_TITLE` constant in `tests/test_goto_r1.py:14` and the user guide's mention, in the same commit).
- [ ] Tracked Airports Manager: the hint offers Space to select but the `ListView` toggles only on Enter (`ui/modals/tracked_airports.py:117`), and `S` saves the whole tracked list, ignoring the picked rows (`:243`), with a status count at `:251` that is wrong too.

  Settled (owner): Space toggles the highlighted row (Enter keeps working); `S` saves only the picked rows, or the whole list when none is picked, and the status names the count saved.
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
- [ ] Decide whether ETAs use the CIFP procedures of Alaska, Hawaii and the territories: they sit under the `SCANP` area prefix (818 SID/STAR lines for PANC, PHNL, TJSJ, PAFA and others), which the `SUSAP K…` match in `backend/data/cifp.py:633`, `:727` never reads, so those airports' routes skip SID/STAR expansion.
- [ ] Investigate the overview's NEXT ETA showing `52h06m` for LFBO, from one long-haul flight from Melbourne. Not yet confirmed.

## Backlog

### Singles

Items that share no files. Review: `code-review`. Verification: `uv run --group dev pytest -q` plus `ruff check`.

- [ ] Deploy the self-hosted Leaflet (f4e8083): push, run `scripts/weather_daemon/service/QuickDeploy.ps1`, reinstall `nginx-weather.conf` on the server (`setup.sh`), check `https://leftos.dev/weather/assets/leaflet/1.9.4/leaflet.js` returns 200 with the long `Cache-Control`, then send leftos.dev's inbox the "deployed" note so its policy can drop the unpkg line. Needs the owner's go-ahead (it changes the live site).
- [ ] Remove `--progressive-load` and `--progressive-chunk-size` and the dead progressive path: the flags are parsed (`main.py:54-64`) but never read, and nothing calls `populate(progressive=True)` (`ui/tables.py:34-41`, `:140-166`, `_populate_progressive` at `:194-208`). Settled (owner): remove, flags and code, with their docs (`CLAUDE.md:76`, `README.md:32-33`, `:59-62`, `USER_GUIDE.md:151-152`, `:694-703`, `:740`, `:800`, `docs/ARCHITECTURE.md:40`).
- [ ] `common/logger.py` opens the log file and runs `cleanup_old_logs()` on the first log call (`:50`, `:72`), which happens at import time: `backend/data/statsim_api.py:42`, `:53` log while loading `.env`, reached through `import ui.modals` (`ui/modals/historical_stats.py:12`), so tests and tools write to and prune the user's real log folder; the repo-root `conftest.py` works around it.

  Make file logging start from the entry points (`main.py` `main()`, `scripts/weather_daemon/cli.py`, `scripts/generate_airport_names.py`, `scripts/precalculate_airport_spatial_data.py`). Lands after the progressive-flags item (both edit `main.py`).

  Settled (owner): records logged before file logging starts are buffered in memory, capped, and written when it starts; the weather daemon starts it too, as today. Settled from the code: the `conftest.py` redirect stays as defence in depth and `statsim_api` keeps loading `.env` at import; `tests/test_isolation.py` drops its reads of the logger's private state.
- [ ] Investigate display names that fall back to the ICAO code ("ETIK - Etik" in the VFR finder) and two UK weather-briefing areas both named "LONDON AREA". Not yet confirmed.
- [ ] Cut cold-start weather boxes: `calculate_airport_bboxes` (`backend/data/weather.py`) makes one box per occupied grid cell sized to the airports' overall span, not the 10° maximum, so 30 US airports took 14 bbox requests and 17.8 s on a cold start (live check while landing perf fix 5).
- [ ] Add `docs/README.md` as the docs' start page, linking `docs/ARCHITECTURE.md`, `docs/atis-filtering-and-runway-extraction.md`, `USER_GUIDE.md`, `CLAUDE.md` and this plan: the repo has no docs index, so the architecture entry point is reachable only from `CLAUDE.md` (line 83). Give it a glossary for the project's terms: AIRAC, bbox (a weather bounding-box request), CIFP, clean empty and negative cache (a cached "no METAR" answer), MEA, SID/STAR.
- [ ] Raise ruff's `target-version` from `py310` (`ruff.toml:6`) to `py313` to match `requires-python` in `pyproject.toml`, and land the UP-rule rewrites it triggers as their own commit.
