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
- [ ] Flight briefing (`ui/modals/flight_briefing.py`) enroute conditions, found pinning it for C901: an enroute airport with wind but no ceiling (any station reporting CLR) shows "27010KT None", and one with a ceiling but no wind "None BKN020", on screen and in the HTML export (`test_update_display` and `test_print_lines` in `tests/test_flight_briefing_characterization.py` pin it); the export keeps a leading space when wind is empty (" OVC008") where the screen strips it; a visibility of 0.0 drops out of the conditions line (a truthiness test).

  Settled (owner, mock `docs/plans/canvases/enroute-conditions/`): enroute lines use the DEP/ARR/ALT builder `_conditions_text`, so they read wind, visibility, ceiling with a missing part dropped and no "CLR" ("27010KT 10.0SM", "6.0SM BKN025"); visibility 0.0 shows as "0.0SM"; calm and VRB winds stay verbatim; `strip_conditions` goes. `action_print`'s `Console` gets `file=StringIO()` here and in `ui/modals/weather_briefing.py:700`: inside the app Textual captures stdout, so the cp1252 `UnicodeEncodeError` is reachable only outside it, but the 500-wide copy is wasted work.
- [ ] Flight briefing fetches ATIS it never shows: `_collect_atis` (`ui/modals/flight_briefing.py:56-62`, called at `:313`) fetches VATSIM ATIS and, for every airport without one, real-world D-ATIS, and `_weather_entry` stores it as `"atis"` (`:77`), but nothing reads it. Settled (owner): stop fetching it; remove `_collect_atis`, the `"atis"` field and the VATSIM feed download that only feeds it.
- [ ] `scripts/weather_daemon/service/nginx_include.py` matches the `leftos.dev` TLS block loosely in one way and strictly in another: the port test is a substring (`_TLS_PORT in _normalize(args)`), so `listen 8443;` or `listen 4430;` counts as TLS, and a quoted `server_name "leftos.dev";` does not match because the token keeps its quotes. Both found by the C901 split's review; pin the intended behaviour in `tests/test_nginx_include.py` before changing it, since the script edits the production site files.
- [ ] `parse_weather_phenomena` (`backend/data/weather_parsing.py`) stops at `RMK` but reads a non-US METAR's trend group as current weather: `EGLL 101250Z 25010KT 9999 -SHRA FEW020 BKN035 15/10 Q1012 TEMPO SH` gives `['Light Showers Rain', 'Showers']`. Stop at `TEMPO`, `BECMG` and `NOSIG` as it does at `RMK`, with a characterization row in `tests/test_weather_parsing_characterization.py`.
