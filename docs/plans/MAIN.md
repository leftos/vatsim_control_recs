# Plan
<!-- plan-doc-hygiene: 2026-10-10 7e1d80a -->

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

Nothing in focus; the next session picks from the backlog.


## Backlog

### Singles

Items that share no files. Review: `code-review`. Verification: `uv run --group dev pytest -q` plus `ruff check`.

- [ ] Re-measure two `docs/performance.md` figures on a headless run: the "wave" refresh definition ("5 or more bbox requests") and the "0 to 3 box requests" between waves were measured with grid-cell boxes, before d0bb731 packed them first-fit; and the per-refresh `calculate_eta` cost on the no-filter load is no longer stated since `arrival_eta_in_window` (826dc6b) skips it for flights past the window.
- [ ] Burn down the `C901` ratchet: `ruff.toml` `[lint.per-file-ignores]` lists 43 files (89 functions) that predate the max-complexity 8 rule. Split a file's functions under 8 and take it off the list, characterization tests first (`tests/test_area_clustering_characterization.py` and `tests/test_weather_characterization.py` are the models); `uvx ruff@0.16.1 check --select C901 --isolated --config 'lint.mccabe.max-complexity=8' <file>` lists them. Biggest: `ui/modals/flight_info.py`, `ui/modals/flight_briefing.py` and `scripts/weather_daemon/simaware_boundaries.py` (5 each).
- [ ] `backend/briefing/area_clustering.py`: `create_area_groups` keeps branches `kmeans_clustering` makes unreachable (the empty-cluster skip and `None` centroid in `_attach_untowered` / `_nearest_index`, `centers[0][0] if centers else None`, the `else 99` sort key), and `calculate_optimal_k`'s `num_total` parameter is unused; remove them with their callers' arguments (`scripts/weather_daemon/generator.py`, `ui/modals/weather_briefing.py`).
- [ ] Raise ruff's `target-version` from `py310` (`ruff.toml:6`) to `py313` to match `requires-python` in `pyproject.toml`, and land the UP-rule rewrites it triggers as their own commit.
- [ ] `tests/test_table_refresh.py::test_column_narrows_when_its_widest_row_is_removed[True]` and `::test_populate_tables_cancels_the_old_managers_updates` failed once in a full-suite run under load ("the cells did not stop flapping", an animation deadline) and passed on rerun, alone and in the full suite. Find what the deadline races against and make it hold under load.
