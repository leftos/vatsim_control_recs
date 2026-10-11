# Plan
<!-- plan-doc-hygiene: 2026-10-10 a31607e -->

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

Nothing in focus; the next session picks from the backlog.


## Backlog

### Singles

Items that share no files. Review: `code-review`. Verification: `uv run --group dev pytest -q` plus `ruff check`.

- [ ] Re-check `docs/performance.md`'s no-filter figures on a live feed at peak traffic (1,500 or more pilots): the box-request cycle, the `calculate_eta` count and cost, and the analysis CPU were last measured on a frozen 957-pilot snapshot (the VATSIM feed served one 03:08Z snapshot for over two hours), whose rig is described in `docs/performance.md` "Measuring".
- [ ] Burn down the `C901` ratchet: `ruff.toml` `[lint.per-file-ignores]` lists 31 files (48 functions) that predate the max-complexity 8 rule. Split a file's functions under 8 and take it off the list, characterization tests first (`tests/test_area_clustering_characterization.py` and `tests/test_weather_characterization.py` are the models), then an uncommitted differential fuzz that runs a copy of the original module and the split one on thousands of seeded random inputs and compares their outputs; `uvx ruff@0.16.1 check --select C901 --isolated --config 'lint.mccabe.max-complexity=8' <file>` lists them. Several files hold one offender each and bundle well into one brief (the C901 command above run per `ruff.toml` entry counts them); `tests/test_flight_info_characterization.py` is the model for a Textual modal.
- [ ] ATIS text holding `[/]` or another Rich tag breaks the weather briefing: `colorize_atis_text` (`backend/data/atis_filter.py:498-761`) does not escape the controller's text, so the screen shows "Error fetching weather: closing tag…" in place of the briefing and `P` ends the app with a `MarkupError`. Escape the text before inserting tags.
- [ ] The weather briefing's grouping view drops airports its header still counts: an airport with a METAR but no position is dropped once any towered airport has one (`backend/briefing/area_clustering.py:270-272`, `:159-163`), yet the header counts every airport in `weather_data` (`ui/modals/weather_briefing.py` `_update_display` ~:706, `action_print` ~:798); KSFO plus KNOP (VFR METAR, no position) shows "1 MVFR | 1 VFR" but only a KSFO card. No-METAR (UNK) airports are already out of both header and cards. Owner ruling: the header (screen and `P` export) counts only the airports that got a card; no new section, and the TUI-only change leaves the daemon pages alone.
- [ ] The weather and flight briefings duplicate their `P` export: each builds its own print CSS (`_PRINT_CSS` in `ui/modals/weather_briefing.py`, an inline string in `ui/modals/flight_briefing.py` `action_print`) and its own temp-file write and browser open (`_open_in_browser` and `action_print`); share one helper. In the same file, `_parse_metar_observation_time` (`ui/modals/weather_briefing.py` ~:96-99) swallows an `OSError` from `astimezone()` without logging it, and only `ui/modals/route_weather.py` calls it, so it may belong there.
- [ ] Write a weather briefing subsystem doc (`docs/` deep doc linked from `docs/ARCHITECTURE.md`'s Deep docs): the Task Index row names only code, and the areas, the REQUESTED section, fallback groups and the towered test (size priority ≤ 2 or an ATIS, `backend/briefing/area_clustering.py` ~:430) are explained nowhere; add "area", "fallback group", "REQUESTED section" and "towered" to the glossary in `docs/README.md`.
- [ ] `scripts/weather_daemon/generator.py` dead code: `generate_index_only`, `generate_with_cached_weather` and the `_REMOVED_OLD_IMPLEMENTATIONS` string (~:2085, :2094, :2101) have no callers, and `_generate_artcc_wide_briefings` takes 8 positional parameters (~:1211).
- [ ] `scripts/weather_daemon/index_generator.py` keeps the grouping order rule (filter with `grouping_has_metars`, sort with `grouping_sort_key`, number in order) in three places (`_grouping_polygons` and two in `build_sidebar_html`), and the sidebar's `data-grouping-id` must stay in step with the `groupingPolygons` keys; give them one shared iterator. In the same file: `generate_weather_regions` has no caller, `generate_html`'s `use_tile_layer` parameter is unused, the fallback's `if lat and lon` in `_far139_airports_without_weather` repeats `_airport_marker`'s check, and `CONUS_ARTCCS` holds ZAN and ZHN (not CONUS) while its comment names only ZUA as left out.
