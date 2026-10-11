# Plan
<!-- plan-doc-hygiene: 2026-10-10 a31607e -->

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

Nothing in focus; the next session picks from the backlog.


## Backlog

### Singles

Items that share no files. Review: `code-review`. Verification: `uv run --group dev pytest -q` plus `ruff check`.

- [ ] Re-check `docs/performance.md`'s no-filter figures on a live feed at peak traffic (1,500 or more pilots): the box-request cycle, the `calculate_eta` count and cost, and the analysis CPU were last measured on a frozen 957-pilot snapshot (the VATSIM feed served one 03:08Z snapshot for over two hours), whose rig is described in `docs/performance.md` "Measuring".
- [ ] Burn down the `C901` ratchet: `ruff.toml` `[lint.per-file-ignores]` lists 38 files (67 functions) that predate the max-complexity 8 rule. Split a file's functions under 8 and take it off the list, characterization tests first (`tests/test_area_clustering_characterization.py` and `tests/test_weather_characterization.py` are the models), then an uncommitted differential fuzz that runs a copy of the original module and the split one on thousands of seeded random inputs and compares their outputs; `uvx ruff@0.16.1 check --select C901 --isolated --config 'lint.mccabe.max-complexity=8' <file>` lists them. Biggest: `ui/modals/weather_briefing.py`, `ui/modals/flight_board.py`, `scripts/weather_daemon/index_generator.py` and `scripts/weather_daemon/generator.py` (4 each); `tests/test_flight_info_characterization.py` is the model for a Textual modal.
- [ ] `parse_weather_phenomena` (`backend/data/weather_parsing.py`) stops at `RMK` but reads a non-US METAR's trend group as current weather: `EGLL 101250Z 25010KT 9999 -SHRA FEW020 BKN035 15/10 Q1012 TEMPO SH` gives `['Light Showers Rain', 'Showers']`. Stop at `TEMPO`, `BECMG` and `NOSIG` as it does at `RMK`, with a characterization row in `tests/test_weather_parsing_characterization.py`.
