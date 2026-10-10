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

- [ ] Saved dep/arr filters follow a ★ grouping wherever it opens (R4's filters half, [goto-ux-review.md](./goto-ux-review.md)): Enter on the Custom Groupings tab (`ui/app.py:937-1010`, `FlightBoardScreen` at `:999`) passes no `airport_filters`, while Go To's `_open_grouping` (`ui/modals/goto_modal.py:1164-1192`) does; one shared `open_grouping_board(name)` serves both and drops the nested resolver copy at `ui/app.py:966-989`.
- [ ] Split `GoToScreen._fill_option_list` (`ui/modals/goto_modal.py:449`, the old `_update_option_list` body): its cyclomatic complexity is about 11, over the limit of 8. A pure refactor; the Go To tests stay green with no assertion edited.
- [ ] Remove the dead `name == "airports"` branch of `_open_weather_briefing_callback` (`ui/app.py:1207-1209`): Go To's callback sends only `(grouping, airports)`, `(icao, [icao])` and `("flight", data)` (`ui/modals/goto_modal.py:1119-1130`), so typed ICAO lists never reach it; the picker title at `:1195` still promises "Enter airports".
- [ ] Tracked Airports Manager: the hint offers Space to select but the `ListView` toggles only on Enter (`ui/modals/tracked_airports.py:117`), and `S` saves the whole tracked list, ignoring the picked rows (`:243`), with a status count at `:251` that is wrong too.
- [ ] Go To list hygiene (R5 in [goto-ux-review.md](./goto-ux-review.md)): ★ groupings first with section headers, a "showing 100 of N" row, collapsed members for selected groupings, the "Selected: …" summary line (it repeats the title's pick counts and stays visible after Tab back to browse), and the dead `filter_type="$"` path removed (`ui/app.py` `_build_goto_results`, `ui/modals/goto_modal.py`).
- [ ] Go To grouping builder (R3 in [goto-ux-review.md](./goto-ux-review.md)): a separate screen that adds any airport from the unified data, lists every member with its filter, removes or excludes members, and asks before overwriting, shadowing a preset or discarding; grouping file I/O moves into `backend/core/groupings.py`.
- [ ] One name for saved groupings (R4's wording half, [goto-ux-review.md](./goto-ux-review.md)): "my groupings" (★) everywhere instead of favorite, grouping and custom grouping, in UI text and docs (`favorites.json` and `custom_groupings.json` keep their names; the weather daemon's "custom groupings" means the data file and stays).

### Wave 3: Flight info and route checks

Shared: `backend/data/navaids.py`, `ui/modals/flight_info.py`, the ETA path in `backend/core/`. Review: `code-review` with an aviation read of each verdict. Verification: each item first reproduces on fixture or live data; a confirmed defect gets a red test before its fix.

- [ ] Investigate the MEA check: when an airway's entry or exit fix is not found, every segment of the airway is checked (`backend/data/navaids.py:1165-1175`), so `ABQ V12 ONM` reported a California V12 segment at 29,000 ft. Seen by the screenshot run, not yet confirmed.
- [ ] Investigate the MEA warning comparing only the first filed altitude, so step climbs are ignored (`ui/modals/flight_info.py:661-668`); live UAE82M filed step climbs to FL400 and was warned against a 33,000 ft MEA. Not yet confirmed.
- [ ] Decide whether ETAs use the CIFP procedures of Alaska, Hawaii and the territories: they sit under the `SCANP` area prefix (818 SID/STAR lines for PANC, PHNL, TJSJ, PAFA and others), which the `SUSAP K…` match in `backend/data/cifp.py:633`, `:727` never reads, so those airports' routes skip SID/STAR expansion.
- [ ] Investigate the overview's NEXT ETA showing `52h06m` for LFBO, from one long-haul flight from Melbourne. Not yet confirmed.

## Backlog

### Singles

Items that share no files. Review: `code-review`. Verification: `uv run --group dev pytest -q` plus `ruff check`.

- [ ] Serve Leaflet's JS and CSS from the weather output (pinned to 1.9.4, beside the pages or under `/weather/assets/`) instead of unpkg.com (`scripts/weather_daemon/index_generator.py:782`, `:1430`); map tiles stay on CARTO/OpenStreetMap with their attribution visible. Asked by leftos.dev, whose privacy policy promises no third-party JavaScript: send its inbox a note once the pages no longer load unpkg.

  Settled (owner): `nginx-weather.conf` gets a long-cache `location` for `/weather/assets/`, whose path carries the version. Settled from the code: the files are vendored byte-identical under `scripts/weather_daemon/assets/leaflet/` with Leaflet's BSD-2-Clause `LICENSE` and its `images/`, referenced relative to the page (`assets/leaflet/…`, so LocalTest's `file://` keeps working), and `generate_index_page` copies them into the output on every index run.
- [ ] Remove `--progressive-load` and `--progressive-chunk-size` and the dead progressive path: the flags are parsed (`main.py:54-64`) but never read, and nothing calls `populate(progressive=True)` (`ui/tables.py:34-41`, `:140-166`, `_populate_progressive` at `:194-208`). Settled (owner): remove, flags and code, with their docs (`CLAUDE.md:76`, `README.md:32-33`, `:59-62`, `USER_GUIDE.md:151-152`, `:694-703`, `:740`, `:800`, `docs/ARCHITECTURE.md:40`).
- [ ] `common/logger.py` opens the log file and runs `cleanup_old_logs()` on the first log call (`:50`, `:72`), which happens at import time: `backend/data/statsim_api.py:42`, `:53` log while loading `.env`, reached through `import ui.modals` (`ui/modals/historical_stats.py:12`), so tests and tools write to and prune the user's real log folder; the repo-root `conftest.py` works around it. Make file logging start from the entry points (`main.py` `main()`, `scripts/weather_daemon/cli.py`, `scripts/generate_airport_names.py`, `scripts/precalculate_airport_spatial_data.py`). Lands after the progressive-flags item (both edit `main.py`).

  Settled (owner): records logged before file logging starts are buffered in memory, capped, and written when it starts; the weather daemon starts it too, as today. Settled from the code: the `conftest.py` redirect stays as defence in depth and `statsim_api` keeps loading `.env` at import; `tests/test_isolation.py` drops its reads of the logger's private state.
- [ ] Investigate display names that fall back to the ICAO code ("ETIK - Etik" in the VFR finder) and two UK weather-briefing areas both named "LONDON AREA". Not yet confirmed.
- [ ] Cut cold-start weather boxes: `calculate_airport_bboxes` (`backend/data/weather.py`) makes one box per occupied grid cell sized to the airports' overall span, not the 10° maximum, so 30 US airports took 14 bbox requests and 17.8 s on a cold start (live check while landing perf fix 5).
- [ ] Add `docs/README.md` as the docs' start page, linking `docs/ARCHITECTURE.md`, `docs/atis-filtering-and-runway-extraction.md`, `USER_GUIDE.md`, `CLAUDE.md` and this plan: the repo has no docs index, so the architecture entry point is reachable only from `CLAUDE.md` (line 83). Give it a glossary for the project's terms: AIRAC, bbox (a weather bounding-box request), CIFP, clean empty and negative cache (a cached "no METAR" answer), MEA, SID/STAR.
- [ ] Raise ruff's `target-version` from `py310` (`ruff.toml:6`) to `py313` to match `requires-python` in `pyproject.toml`, and land the UP-rule rewrites it triggers as their own commit.
