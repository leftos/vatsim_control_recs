# Plan
<!-- plan-doc-hygiene: 2026-10-10 7e1d80a -->

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

Nothing in focus; the next session picks from the backlog.


## Backlog

### Singles

Items that share no files. Review: `code-review`. Verification: `uv run --group dev pytest -q` plus `ruff check`.

- [ ] Re-check `docs/performance.md`'s no-filter figures on a live feed at peak traffic (1,500 or more pilots): the box-request cycle, the `calculate_eta` count and cost, and the analysis CPU were last measured on a frozen 957-pilot snapshot (the VATSIM feed served one 03:08Z snapshot for over two hours), whose rig is described in `docs/performance.md` "Measuring".
- [ ] Burn down the `C901` ratchet: `ruff.toml` `[lint.per-file-ignores]` lists 39 files (71 functions) that predate the max-complexity 8 rule. Split a file's functions under 8 and take it off the list, characterization tests first (`tests/test_area_clustering_characterization.py` and `tests/test_weather_characterization.py` are the models); `uvx ruff@0.16.1 check --select C901 --isolated --config 'lint.mccabe.max-complexity=8' <file>` lists them. Biggest: `ui/modals/weather_briefing.py`, `ui/modals/flight_board.py` and `scripts/weather_daemon/service/nginx_include.py` (4 each); `tests/test_flight_info_characterization.py` is the model for a Textual modal.
- [ ] Raise ruff's `target-version` from `py310` (`ruff.toml:6`) to `py313` to match `requires-python` in `pyproject.toml`, and land the UP-rule rewrites it triggers as their own commit.
- [ ] Flight briefing (`ui/modals/flight_briefing.py`) enroute conditions, found pinning it for C901: an enroute airport with wind but no ceiling (any station reporting CLR) shows "27010KT None", and one with a ceiling but no wind "None BKN020", on screen and in the HTML export (`test_fetch_full_route` in `tests/test_flight_briefing_characterization.py` pins it); the export keeps a leading space when wind is empty (" OVC008") where the screen strips it; a visibility of 0.0 drops out of the conditions line (a truthiness test). Also, `action_print`'s Rich `Console` writes the briefing to stdout too, which raised `UnicodeEncodeError` on "→" under a cp1252 console outside the app; check whether the app can hit it.
- [ ] `_parse_single_weather` (`backend/data/weather_parsing.py`) returns `[]` for "VCSH": "VC" is taken as the intensity, then "SH" as a descriptor with no phenomenon after it, so showers in the vicinity are dropped while "VCFG" parses. The characterization test pins `("VCSH", [])` (`tests/test_weather_parsing_characterization.py`); fix it and the pin together. In the same file, `parse_visibility_sm` raises `ZeroDivisionError` on a zero denominator (`1/0SM`, `M1/0SM`, `1 1/0SM`).
- [ ] `tests/test_table_refresh.py::test_column_narrows_when_its_widest_row_is_removed[True]` and `::test_populate_tables_cancels_the_old_managers_updates` failed once in a full-suite run under load ("the cells did not stop flapping", an animation deadline) and passed on rerun, alone and in the full suite. Find what the deadline races against and make it hold under load.
