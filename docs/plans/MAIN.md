# Plan
<!-- plan-doc-hygiene: 2026-10-10 7e1d80a -->

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

- [ ] The leftos.dev/weather index stopped updating at 2026-10-11 00:04:12Z (`Last-Modified` and the page's "Updated" line) while the briefings keep moving (`ZTL/A80_Atlanta_TRACON.html` current as of 0120Z), so the index stage is not writing on the server; reported by the leftos.dev session through the repo inbox. Read the timer's runs since 00:04Z (`scripts/weather_daemon/service/Status.ps1`, `Logs.ps1`) for a timeout or an index error, then fix and redeploy.

### Wave 2: Go To and groupings

Design in [goto-ux-review.md](./goto-ux-review.md). Shared: `ui/modals/goto_modal.py`, `ui/app.py` (`_build_goto_results`, the flight-board open path, `_open_weather_briefing_callback`), `ui/modals/tracked_airports.py`, grouping file I/O. Review: `code-review` plus a UI look at each screen. Verification: Pilot tests in `tests/` are the command; the human check is screenshots of each mode from a filtered run (`--groupings O90`, the Bay TRACON preset).

- [ ] One name for saved groupings (R4's wording half, [goto-ux-review.md](./goto-ux-review.md)): "my groupings" (★) everywhere instead of favorite, grouping and custom grouping, in UI text and docs (`favorites.json` and `custom_groupings.json` keep their names; the weather daemon's "custom groupings" means the data file and stays).

  Settled (owner): the main tab "Custom Groupings" becomes "Groupings" (it lists presets, ARTCC groupings and ★ ones); only user-visible strings and docs change, and code identifiers keep `favorite`, bridged by a glossary line.


## Backlog

### Singles

Items that share no files. Review: `code-review`. Verification: `uv run --group dev pytest -q` plus `ruff check`.

- [ ] Re-measure two `docs/performance.md` figures on a headless run: the "wave" refresh definition ("5 or more bbox requests") and the "0 to 3 box requests" between waves were measured with grid-cell boxes, before d0bb731 packed them first-fit; and the per-refresh `calculate_eta` cost on the no-filter load is no longer stated since `arrival_eta_in_window` (826dc6b) skips it for flights past the window.
- [ ] Burn down the `C901` ratchet: `ruff.toml` `[lint.per-file-ignores]` lists 44 files (94 functions) that predate the max-complexity 8 rule. Split a file's functions under 8 and take it off the list, characterization tests first (`tests/test_weather_characterization.py` is the model); `uvx ruff@0.16.1 check --select C901 --isolated --config 'lint.mccabe.max-complexity=8' <file>` lists them. Biggest: `ui/modals/flight_info.py`, `ui/modals/flight_briefing.py`, `scripts/weather_daemon/simaware_boundaries.py` and `backend/briefing/area_clustering.py` (5 each).
- [ ] `get_weather_smart` (`backend/data/weather.py`) falls back to per-airport fetches only when a bbox request raises, but `fetch_weather_bbox` (`weather.py:900`) returns `({}, {})` on an HTTP error, so a failed box leaves its airports without weather until the next refresh; `test_smart_bbox_http_error_leaves_cluster_empty` in `tests/test_weather_characterization.py` pins today's behaviour. Decide whether a failed box falls back per airport (mind the 400-entry cap and rate limits in `docs/performance.md`).
- [ ] `find_airports_near_position` (`backend/data/weather.py:1340`) keeps its own copy of the METAR spatial-index rebuild with double-checked locking; call `_current_metar_spatial_index` instead.
- [ ] Raise ruff's `target-version` from `py310` (`ruff.toml:6`) to `py313` to match `requires-python` in `pyproject.toml`, and land the UP-rule rewrites it triggers as their own commit.
