# Plan
<!-- plan-doc-hygiene: 2026-10-10 3dc6deb -->

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

### Wave 1: Refresh performance

Performance fixes from [perf-profile.md](./perf-profile.md) (measured hotspots, gains and risks per fix there), in its rank order. Shared: `ui/app.py` refresh path, `backend/data/` (`cifp.py`, `weather.py`), `backend/core/` (`analysis.py`, `spatial.py`), `airport_disambiguator/`. Review: `code-review`.

Verification: `uv run --group dev pytest -q` (Pilot tests on fixture data) is the command; the human check is a run with `--countries US` showing refreshes land every interval, re-measured as the profile did.

- [ ] Never start a refresh while one is running, and never cancel one under way: with the US filter or no filter every 15 s tick cancels the running analysis and starts another, so no refresh ever lands and cancelled analyses pile up (`ui/app.py:416-438`, `exclusive=True` at `:609`). Perf fix 1.

  Settled (owner): the Ctrl+R binding is removed entirely (`ui/app.py:151`, with its help, command-palette and docs entries), so only the timer and the app's own triggers refresh. The status bar's "Last refresh" counts from the last refresh that landed and adds "· Refreshing…" while one runs.
- [ ] Stop refetching still-fresh bbox weather each refresh and cache "no METAR" results (`backend/data/weather.py`, `get_weather_for_airports_bbox`, `get_metar`). Perf fix 5.

  Settled (owner): only a clean empty (a successful response with no METAR) is cached as "no METAR" for 60 s, never a timeout or 429; a stale METAR already in cache keeps showing, as today. Settled from the code: the API caps a response at 400 entries (aviationweather.gov/data/api), so a bbox call is trusted for absent airports only when it succeeded with fewer than 400; negatives live apart from the METAR cache and are never persisted; a box is skipped only when all its airports are fresh.
- [ ] A 0.25 s timer raised `NoMatches '#status-bar'` at shutdown after a US auto-refresh run (`ui/app.py:472`, `:486`).

## Next up

### Wave 2: Go To and groupings

Design in [goto-ux-review.md](./goto-ux-review.md). Shared: `ui/modals/goto_modal.py`, `ui/app.py` (`_build_goto_results`, the flight-board open path, `_open_weather_briefing_callback`), `ui/modals/tracked_airports.py`, grouping file I/O. Review: `code-review` plus a UI look at each screen. Verification: Pilot tests in `tests/` are the command; the human check is screenshots of each mode from a filtered run (`--groupings "Bay Area"`).

- [ ] One name for saved groupings, and filters that follow them (R4 in [goto-ux-review.md](./goto-ux-review.md)): "my groupings" (★) everywhere instead of favorite, grouping and custom grouping, and saved dep/arr filters applied wherever the grouping is opened; today they apply only when it is opened from Go To, not from Enter on the Custom Groupings tab (`ui/app.py` flight-board open path).
- [ ] Go To list hygiene (R5 in [goto-ux-review.md](./goto-ux-review.md)): ★ groupings first with section headers, a "showing 100 of N" row, collapsed members for selected groupings, the "Selected: …" summary line (it repeats the title's pick counts and stays visible after Tab back to browse), and the dead `filter_type="$"` path removed (`ui/app.py` `_build_goto_results`, `ui/modals/goto_modal.py`).
- [ ] Split `GoToScreen._fill_option_list` (`ui/modals/goto_modal.py:449`, the old `_update_option_list` body): its cyclomatic complexity is about 11, over the limit of 8.
- [ ] Remove the dead `name == "airports"` branch of `_open_weather_briefing_callback` (`ui/app.py:1135`): Go To's callback sends only `(grouping, airports)`, `(icao, [icao])` and `("flight", data)` (`ui/modals/goto_modal.py:1119-1130`), so typed ICAO lists never reach it.
- [ ] Tracked Airports Manager: the hint offers Space to select but the `ListView` toggles only on Enter (`ui/modals/tracked_airports.py:117`), and `S` saves the whole tracked list, ignoring the picked rows (`:243`).
- [ ] Go To grouping builder (R3 in [goto-ux-review.md](./goto-ux-review.md)): a separate screen that adds any airport from the unified data, lists every member with its filter, removes or excludes members, and asks before overwriting, shadowing a preset or discarding; grouping file I/O moves into `backend/core/groupings.py`.

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

- [ ] `--progressive-load` and `--progressive-chunk-size` are parsed (`main.py:55-63`) but never reach the app, and nothing calls `populate(progressive=True)` (`ui/tables.py:140`): remove the flags and the dead progressive path, or wire them up.
- [ ] `common/logger.py` opens the log file and runs `cleanup_old_logs()` on the first log call (`:72`), which happens at import time (`backend/data/statsim_api.py` logs while loading `.env`), so any importer of `backend` writes to and prunes the user's real log folder; the tests work around it with a repo-root `conftest.py`. Make file logging start from the app's entry points instead.
- [ ] Investigate display names that fall back to the ICAO code ("ETIK - Etik" in the VFR finder) and two UK weather-briefing areas both named "LONDON AREA". Not yet confirmed.
- [ ] Add `docs/README.md` as the docs' start page, linking `docs/ARCHITECTURE.md`, `docs/atis-filtering-and-runway-extraction.md`, `USER_GUIDE.md`, `CLAUDE.md` and this plan: the repo has no docs index, so the architecture entry point is reachable only from `CLAUDE.md` (line 83).
- [ ] Raise ruff's `target-version` from `py310` (`ruff.toml:6`) to `py313` to match `requires-python` in `pyproject.toml`, and land the UP-rule rewrites it triggers as their own commit.
