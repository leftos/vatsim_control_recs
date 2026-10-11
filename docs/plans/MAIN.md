# Plan
<!-- plan-doc-hygiene: 2026-10-10 a31607e -->

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

Nothing in focus; the next session picks from the backlog.


## Backlog

### Singles

Items that share no files. Review: `code-review`. Verification: `uv run --group dev pytest -q` plus `ruff check`.

- [ ] Re-check `docs/performance.md`'s no-filter figures on a live feed at peak traffic (1,500 or more pilots): the box-request cycle, the `calculate_eta` count and cost, and the analysis CPU were last measured on a frozen 957-pilot snapshot (the VATSIM feed served one 03:08Z snapshot for over two hours), whose rig is described in `docs/performance.md` "Measuring".
- [ ] Burn down the `C901` ratchet: `ruff.toml` `[lint.per-file-ignores]` lists 39 files (71 functions) that predate the max-complexity 8 rule. Split a file's functions under 8 and take it off the list, characterization tests first (`tests/test_area_clustering_characterization.py` and `tests/test_weather_characterization.py` are the models); `uvx ruff@0.16.1 check --select C901 --isolated --config 'lint.mccabe.max-complexity=8' <file>` lists them. Biggest: `ui/modals/weather_briefing.py`, `ui/modals/flight_board.py`, `scripts/weather_daemon/service/nginx_include.py`, `scripts/weather_daemon/index_generator.py` and `scripts/weather_daemon/generator.py` (4 each); `tests/test_flight_info_characterization.py` is the model for a Textual modal.
- [ ] Flight briefing (`ui/modals/flight_briefing.py`) enroute conditions, found pinning it for C901: an enroute airport with wind but no ceiling (any station reporting CLR) shows "27010KT None", and one with a ceiling but no wind "None BKN020", on screen and in the HTML export (`test_update_display` and `test_print_lines` in `tests/test_flight_briefing_characterization.py` pin it); the export keeps a leading space when wind is empty (" OVC008") where the screen strips it; a visibility of 0.0 drops out of the conditions line (a truthiness test).

  Settled (owner, mock `docs/plans/canvases/enroute-conditions/`): enroute lines use the DEP/ARR/ALT builder `_conditions_text`, so they read wind, visibility, ceiling with a missing part dropped and no "CLR" ("27010KT 10.0SM", "6.0SM BKN025"); visibility 0.0 shows as "0.0SM"; calm and VRB winds stay verbatim; `strip_conditions` goes. `action_print`'s `Console` gets `file=StringIO()` here and in `ui/modals/weather_briefing.py:700`: inside the app Textual captures stdout, so the cp1252 `UnicodeEncodeError` is reachable only outside it, but the 500-wide copy is wasted work.
- [ ] Flight briefing fetches ATIS it never shows: `_collect_atis` (`ui/modals/flight_briefing.py:56-62`, called at `:313`) fetches VATSIM ATIS and, for every airport without one, real-world D-ATIS, and `_weather_entry` stores it as `"atis"` (`:77`), but nothing reads it. Settled (owner): stop fetching it; remove `_collect_atis`, the `"atis"` field and the VATSIM feed download that only feeds it.
- [ ] `parse_weather_phenomena` (`backend/data/weather_parsing.py`) stops at `RMK` but reads a non-US METAR's trend group as current weather: `EGLL 101250Z 25010KT 9999 -SHRA FEW020 BKN035 15/10 Q1012 TEMPO SH` gives `['Light Showers Rain', 'Showers']`. Stop at `TEMPO`, `BECMG` and `NOSIG` as it does at `RMK`, with a characterization row in `tests/test_weather_parsing_characterization.py`.
- [ ] Flight briefing fetches ATIS it never shows: `FlightBriefingScreen` calls `_collect_atis` (`ui/modals/flight_briefing.py:56-62`, called at `:313`), which fetches VATSIM ATIS and, for every airport without one, real-world D-ATIS over the network, and `_weather_entry` stores it as `"atis"` (`:77`), but nothing in the modal or its HTML export reads it. Decide whether the briefing shows ATIS (the departure and arrival airports' runways and approaches, as the flight board does) or stops fetching it.
- [ ] `tests/test_table_refresh.py::test_column_narrows_when_its_widest_row_is_removed[True]` and `::test_populate_tables_cancels_the_old_managers_updates` failed once in a full-suite run under load ("the cells did not stop flapping", an animation deadline) and passed on rerun, alone and in the full suite.

  Cause (measured): the split-flap animation always takes 41 to 49 frames (at most 10 stagger frames, one lap of the 39-character flap set, one finishing frame), but `_wait_for_animations` (`tests/test_table_refresh.py:221`) and `_wait_for_fades` (`:90-97`) wait on a 10 s wall clock (`FADE_TIMEOUT`, `:28`), and Textual's interval timer drops late ticks (`skip=True`), so under CPU load each 50 ms frame stretched to 160 to 1,660 ms and the waits took up to 73 s. Fix, test-only: a `_step_animations(table)` helper that calls `table._animate_cells()` up to `2 * max(delay_frames + len(flap_chars)) + 1` times and asserts nothing still flaps (as `test_retarget_to_the_padded_frame_ends_on_the_unpadded_value` already drives frames); `_wait_for_animations` becomes a pause, the stepping, then its two pauses, and `_wait_for_fades` steps before its `asyncio.wait`, with `FADE_TIMEOUT` kept as a hang guard. Red test first: `test_animation_wait_is_bounded_by_frames_not_wall_time`, which sets `FADE_TIMEOUT` to 0.5 s and populates 25 rows (failed 6 of 6 on current code).
