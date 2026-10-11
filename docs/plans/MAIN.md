# Plan
<!-- plan-doc-hygiene: 2026-10-10 a31607e -->

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

Nothing in focus; the next session picks from the backlog.


## Backlog

### Singles

Items that share no files. Review: `code-review`. Verification: `uv run --group dev pytest -q` plus `ruff check`.

- [ ] Re-check `docs/performance.md`'s no-filter figures on a live feed at peak traffic (1,500 or more pilots): the box-request cycle, the `calculate_eta` count and cost, and the analysis CPU were last measured on a frozen 957-pilot snapshot (the VATSIM feed served one 03:08Z snapshot for over two hours), whose rig is described in `docs/performance.md` "Measuring".
- [ ] Burn down the `C901` ratchet: `ruff.toml` `[lint.per-file-ignores]` lists 37 files (63 functions) that predate the max-complexity 8 rule. Split a file's functions under 8 and take it off the list, characterization tests first (`tests/test_area_clustering_characterization.py` and `tests/test_weather_characterization.py` are the models), then an uncommitted differential fuzz that runs a copy of the original module and the split one on thousands of seeded random inputs and compares their outputs; `uvx ruff@0.16.1 check --select C901 --isolated --config 'lint.mccabe.max-complexity=8' <file>` lists them. Biggest: `ui/modals/weather_briefing.py`, `scripts/weather_daemon/index_generator.py` and `scripts/weather_daemon/generator.py` (4 each); `tests/test_flight_info_characterization.py` is the model for a Textual modal.
- [ ] The flight board's change check blocks the UI: `_fetch_conditions` (`ui/modals/flight_board.py`) calls `get_datis_for_airports` synchronously on the event loop after its `gather`, and that makes blocking `requests.get` calls with a 10 s timeout (`backend/data/datis_api.py:27`, `:67`) and waits on its thread pool (`:179-185`), so a grouping board can freeze for up to about 10 s every 60 s. Run it in the executor like the METAR fetch.
- [ ] A failed flight-board change check can end the app: an exception from `_check_for_changes` (a METAR or VATSIM feed fetch error, pinned as propagating by `test_check_metar_fetch_error_propagates_and_changes_nothing` and `test_check_vatsim_feed_error_propagates_and_changes_nothing`) reaches Textual's `Timer._tick`, which hands it to `app._handle_exception`. Check whether `get_metar_batch` raises in practice; log and skip the check on an error instead.
- [ ] Flight board `Enter` with neither table focused opens the departures row even when departures is empty (a real `DataTable` reports cursor row 0 when empty), so an arrivals highlight is ignored and nothing opens (`_selected_table` in `ui/modals/flight_board.py`).
