# Plan
<!-- plan-doc-hygiene: 2026-10-10 a31607e -->

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

Nothing in focus; the next session picks from the backlog.


## Backlog

### Singles

Items that share no files. Review: `code-review`. Verification: `uv run --group dev pytest -q` plus `ruff check`.

- [ ] Re-check `docs/performance.md`'s no-filter figures on a live feed at peak traffic (1,500 or more pilots): the box-request cycle, the `calculate_eta` count and cost, and the analysis CPU were last measured on a frozen 957-pilot snapshot (the VATSIM feed served one 03:08Z snapshot for over two hours), whose rig is described in `docs/performance.md` "Measuring".
- [ ] Burn down the `C901` ratchet: `ruff.toml` `[lint.per-file-ignores]` lists 36 files (59 functions) that predate the max-complexity 8 rule. Split a file's functions under 8 and take it off the list, characterization tests first (`tests/test_area_clustering_characterization.py` and `tests/test_weather_characterization.py` are the models), then an uncommitted differential fuzz that runs a copy of the original module and the split one on thousands of seeded random inputs and compares their outputs; `uvx ruff@0.16.1 check --select C901 --isolated --config 'lint.mccabe.max-complexity=8' <file>` lists them. Biggest: `ui/modals/weather_briefing.py` and `scripts/weather_daemon/generator.py` (4 each); `tests/test_flight_info_characterization.py` is the model for a Textual modal.
- [ ] Weather briefing `P` on a preset grouping whose name holds "/" ends the app: 31 preset names do (e.g. "ABQ Albuquerque ATCT/TRACON", `data/preset_groupings/ZAB.json`), Go To passes the name through as `grouping_name` (`ui/modals/goto_modal.py:1379` → `ui/app.py:1427`), and `NamedTemporaryFile(prefix=…)` in `action_print` (`ui/modals/weather_briefing.py` ~:770-776) raises `FileNotFoundError`, which Textual turns into an exit (reproduced headless: return code 1). Sanitize the prefix and catch `OSError` with an error toast; check `ui/modals/flight_briefing.py`'s `action_print` for the same.
- [ ] ATIS text holding `[/]` or another Rich tag breaks the weather briefing: `colorize_atis_text` (`backend/data/atis_filter.py:498-761`) does not escape the controller's text, so the screen shows "Error fetching weather: closing tag…" in place of the briefing and `P` ends the app with a `MarkupError`. Escape the text before inserting tags.
- [ ] The weather briefing's grouping view drops airports its header still counts: airports without a METAR (UNK) are skipped (`backend/briefing/area_clustering.py:424`, `:368`) and airports without a position are dropped once any towered airport has one (`:270-272`, `:159-163`); KSFO plus KNOP (VFR METAR, no position) shows "1 MVFR | 1 VFR" but only a KSFO card. Show them in a fallback section or leave them out of the summary.
- [ ] Weather briefing smaller defects (`ui/modals/weather_briefing.py`): no try/finally around the `weather_data` swap for clustering (~:650-661), so a raising `AreaClusterer` leaves it filtered and a later `P` exports a bare REQUESTED header; `_fetch_vatsim` (~:594-601) can overwrite the red "Error" summary; a fetch failure is never logged though the comment at ~:521 says it re-raises to log; the export's Rich highlighter colours numbers and "//" the screen does not (`highlight=False`); `_parse_metar_observation_time` (~:91) fails for an observation day that does not exist in the current month (a "31…Z" METAR on 1 November).
- [ ] `scripts/weather_daemon/generator.py` dead code: `generate_index_only`, `generate_with_cached_weather` and the `_REMOVED_OLD_IMPLEMENTATIONS` string (~:2085, :2094, :2101) have no callers, and `_generate_artcc_wide_briefings` takes 8 positional parameters (~:1211).
- [ ] `scripts/weather_daemon/index_generator.py` keeps the grouping order rule (filter with `grouping_has_metars`, sort with `grouping_sort_key`, number in order) in three places (`_grouping_polygons` and two in `build_sidebar_html`), and the sidebar's `data-grouping-id` must stay in step with the `groupingPolygons` keys; give them one shared iterator. In the same file: `generate_weather_regions` has no caller, `generate_html`'s `use_tile_layer` parameter is unused, the fallback's `if lat and lon` in `_far139_airports_without_weather` repeats `_airport_marker`'s check, and `CONUS_ARTCCS` holds ZAN and ZHN (not CONUS) while its comment names only ZUA as left out.
