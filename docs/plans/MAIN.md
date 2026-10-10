# Plan
<!-- plan-doc-hygiene: 2026-10-10 3dc6deb -->

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

### Wave 1: Refresh performance

The six fixes from [perf-profile.md](./perf-profile.md) have landed; [perf-remeasure.md](./perf-remeasure.md) measured them (every refresh lands, one analysis at a time, refresh CPU 0.05–0.33 s, S3 rows at 17 s) and found what is below. Shared: `backend/data/weather.py`, `backend/core/analysis.py`, `ui/tables.py`. Review: `code-review`.

Verification: `uv run --group dev pytest -q` (Pilot tests on fixture data) is the command; the human check is a run with `--countries US` and an unfiltered run, re-measured as the two docs did.

- [ ] A refresh's table update waits for its slowest weather box (18–20 s per slow box in the re-measure, `backend/core/analysis.py:349-358`).

  Settled (owner, lane B of the [refresh mock](https://claude.ai/artifact/NswGm7DKtQdjXRBZifXVo5)): the traffic columns update as soon as traffic is ready; each region's WIND and ALT keep their previous values until that region's box returns, and an airport new this refresh shows them blank until then. No dimming or pending marker. After the table rebuild item below (same `ui/app.py` refresh region).
- [ ] The largest UI stall is now a full table rebuild (102–427 ms in S3, `ui/tables.py:254`, the row count changes on most refreshes): update rows in place when the count changes. Map: `.tmp/map-table-rebuild.md` (untracked; rerun an explorer if gone).

  Settled from the code: rows stay positional (the equal-count branch's cell diff over the first min(old, new) rows, extra rows added with `add_row(animate=True)`), since Textual has no insert-at and `animated_cells` is keyed by row index; the cursor and scroll restore runs after the update task lands (today `ui/app.py:728` restores before it runs).

  Settled (owner, section 2 of the refresh mock, lane 3): a leftover last row flaps to blank and is then removed (the fade takes as long as the cells' own flap animation, about 1.8 s for visible cells, not the mock's 0.5 s), reviving the widget's soft-removal path (`widgets/split_flap_datatable.py:445`) with its index-keyed bookkeeping fixed; an airport that joins shifts the rows below and a new last row flaps in from blank.
- [ ] Promote the two perf docs' lasting findings (the GIL-starvation mechanism, the weather API's 400-entry cap, the measuring method and its Windows/uv notes) into `docs/`, then delete [perf-profile.md](./perf-profile.md) and [perf-remeasure.md](./perf-remeasure.md).

## Next up

### Weather site (leftos.dev/weather)

Reported by leftos.dev's inbox (20261010-122612, 20261010-122640) while it builds a page about the site; send its inbox a note when both are fixed so its screenshots can be retaken. Shared: `scripts/weather_daemon/` and the droplet `leftos-dev-droplet-1`. Verification: `scripts/weather_daemon/service/LocalTest.ps1`, then the live page after `QuickDeploy.ps1`.

- [ ] The index page has not been regenerated since 2026-08-30 05:58 UTC: the unit is `Type=oneshot` with `TimeoutStartSec=300` (`scripts/weather_daemon/service/weather-daemon.service:8`, `:15`), every run since reaches "Processing 1806 potential tiles with 2 workers" and is killed there (`Result=signal`, status 15; 257 tile-stage starts in the 2026-10-09 log, none finishing), and the index stage runs after tiles (`scripts/weather_daemon/generator.py:1546`, `:1624`). Briefings, written before tiles, stay current. Find why the tile stage got slow, and make the index stage not depend on tiles finishing within the unit's limit.

  Measured (debugger): since a869313 (2026-02-02) added ZAN and ZHN to `CONUS_ARTCCS`, the tile bbox spans the Pacific (465 → 1806 potential tiles) and `_generate_tile` tests every pixel against all 22 polygons (`scripts/weather_daemon/tile_generator.py:228-232`); the stage went from 85 s to 262–338 s on the 1-vCPU droplet. Fix in flight: skip polygons whose bbox cannot reach the tile (pixel-identical), run the index stage before tiles, bundled with the CARTO key below.
- [ ] The map's CARTO basemap tiles now come back stamped "API KEY REQUIRED" (`scripts/weather_daemon/index_generator.py:1500`, `basemaps.cartocdn.com/dark_all`). Research: [basemap-tiles](../research/2026-10-10-basemap-tiles.md).

  Settled (owner): keep CARTO with a free non-commercial key locked to `leftos.dev` and `www.leftos.dev`; the URL becomes `https://{s}.basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}{r}.png?key=…`. Settled by the orchestrator: the generator reads the key from the environment (`CARTO_BASEMAP_KEY`, from the server's untracked `.env` through the unit, and a separate localhost key in the local `.env` for `LocalTest.ps1`), never from the repo. Waits on the owner creating both keys; bundle with the stale-index fix (same unit and generator files). The privacy policy keeps naming CARTO, so leftos.dev only needs the "fixed" note.
- [ ] The daemon regenerates everything when the weather has not changed: the `if not weather_changed:` branch logs "Weather unchanged … skipping regeneration" and falls through (`scripts/weather_daemon/generator.py:1394-1399`), so 138 of 257 runs on 2026-10-09 rebuilt briefings and tiles for nothing on the 1-vCPU droplet. Make it skip what the unchanged weather cannot change.
- [ ] `setup.sh` never updates an existing weather nginx config: it inserts its own inline copy of the `/weather/` block only when `/etc/nginx/sites-available/leftos.dev` has no `/weather` location (`scripts/weather_daemon/service/setup.sh:92-119`), and nginx loads `/etc/nginx/sites-enabled/leftos.dev`, which on the droplet is a separate regular file, not a symlink, so setup.sh's edit would not reach the live config at all; so a change to `nginx-weather.conf` (the `^~ /weather/assets/` block, afab8e7) reaches the server only by hand, and the inline copy can drift from the file. Make it install or update the blocks from `nginx-weather.conf`.

### Wave 2: Go To and groupings

Design in [goto-ux-review.md](./goto-ux-review.md). Shared: `ui/modals/goto_modal.py`, `ui/app.py` (`_build_goto_results`, the flight-board open path, `_open_weather_briefing_callback`), `ui/modals/tracked_airports.py`, grouping file I/O. Review: `code-review` plus a UI look at each screen. Verification: Pilot tests in `tests/` are the command; the human check is screenshots of each mode from a filtered run (`--groupings "Bay Area"`).

Order (from the map of this wave): R3 removes the edit mode R4's wording would relabel, so R4 splits, its filters half first and its wording sweep last; the `_fill_option_list` split lands before R5, which edits the same hunks; R5 lands before R3.

- [ ] Split `GoToScreen._filter_results` (complexity 11) and `_execute_selected` (10) in `ui/modals/goto_modal.py`, then enable ruff's `C901` with `max-complexity = 8` in `ruff.toml` (no complexity rule is enforced today) and split whatever else it reports or backlog it.
- [ ] Tracked Airports manager opens with no highlighted row (`on_mount` calls `populate_list()`, whose `ListView.clear()` sets `index` to `None`, `ui/modals/tracked_airports.py`), and the highlight drops again after a removal or a quick-add (`action_remove_selected`, `handle_quick_tracking_result` rebuild through `populate_list()`).
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
- [ ] Add `docs/README.md` as the docs' start page, linking `docs/ARCHITECTURE.md`, `docs/atis-filtering-and-runway-extraction.md`, `USER_GUIDE.md`, `CLAUDE.md` and this plan: the repo has no docs index, so the architecture entry point is reachable only from `CLAUDE.md` (line 83). Give it a glossary for the project's terms: AIRAC, bbox (a weather bounding-box request), CIFP, clean empty and negative cache (a cached "no METAR" answer), MEA, SID/STAR.
- [ ] Raise ruff's `target-version` from `py310` (`ruff.toml:6`) to `py313` to match `requires-python` in `pyproject.toml`, and land the UP-rule rewrites it triggers as their own commit.
