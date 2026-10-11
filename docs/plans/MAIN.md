# Plan
<!-- plan-doc-hygiene: 2026-10-10 a31607e -->

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

Nothing in focus; the next session picks from the backlog.


## Backlog

### Singles

Items that share no files. Review: `code-review`. Verification: `uv run --group dev pytest -q` plus `ruff check`.

- [ ] A 5xx from aviationweather.gov stalls the whole weather pass: `_check_rate_limit_error` (`backend/data/weather.py:42-44`) treats any status ≥ 500 as rate limiting, so one 504 after ~20 s doubles the shared backoff (up to 16 s) and every box request in the next 60 s waits on it (`_wait_for_backoff`, called at :293, :580, :979), then single-station fallbacks follow; at 1,750 pilots five 504s made one pass last 106 s, during which no refresh started a pass of its own (`docs/performance.md` "Cost of a refresh"). Owner ruling: back off on 429 and 503 only; 500, 502, 504 and any other status fail that request alone.
- [ ] Every second no-filter refresh re-analyses the same VATSIM snapshot: the feed updates every 15 s and is cached 15 s, so with the 15 s timer about half the `analyze_flights_data` runs (`backend/core/analysis.py:157`) see an unchanged `general.update_timestamp` and spend ~0.15 s of CPU changing nothing. Skip the analysis when the snapshot's timestamp has not moved.
- [ ] Burn down the `C901` ratchet: `ruff.toml` `[lint.per-file-ignores]` lists 31 files (48 functions) that predate the max-complexity 8 rule. Split a file's functions under 8 and take it off the list, characterization tests first (`tests/test_area_clustering_characterization.py` and `tests/test_weather_characterization.py` are the models), then an uncommitted differential fuzz that runs a copy of the original module and the split one on thousands of seeded random inputs and compares their outputs; `uvx ruff@0.16.1 check --select C901 --isolated --config 'lint.mccabe.max-complexity=8' <file>` lists them. Several files hold one offender each and bundle well into one brief (the C901 command above run per `ruff.toml` entry counts them); `tests/test_flight_info_characterization.py` is the model for a Textual modal.
- [ ] ATIS text ending in a backslash escapes the closing tag its caller appends: `[/#aaaaaa]` after `colorize_atis_text`'s output in `scripts/weather_daemon/generator.py` ~:1090 and `[/dim]` in `ui/modals/weather_briefing.py` ~:354 then show as literal text instead of closing the style (no crash). Have the callers escape or strip a trailing backslash run, or let `colorize_atis_text` take the wrapping style. In the same file, `colorize_atis_text` (`backend/data/atis_filter.py`) runs about 260 lines, past the 100-line limit; split it into its pattern passes.
- [ ] The weather and flight briefings duplicate their `P` export: each builds its own print CSS (`_PRINT_CSS` in `ui/modals/weather_briefing.py`, an inline string in `ui/modals/flight_briefing.py` `action_print`) and its own temp-file write and browser open (`_open_in_browser` and `action_print`); share one helper. In the same file, `_parse_metar_observation_time` (`ui/modals/weather_briefing.py` ~:96-99) swallows an `OSError` from `astimezone()` without logging it, and only `ui/modals/route_weather.py` calls it, so it may belong there.
- [ ] Write a weather briefing subsystem doc (`docs/` deep doc linked from `docs/ARCHITECTURE.md`'s Deep docs): the Task Index row names only code, and the areas, the REQUESTED section, fallback groups and the towered test (size priority ≤ 2 or an ATIS, `backend/briefing/area_clustering.py` ~:430) are explained nowhere; add "area", "fallback group", "REQUESTED section" and "towered" to the glossary in `docs/README.md`.
- [ ] Weather daemon leftovers: `generate_all_briefings` (`scripts/weather_daemon/generator.py` ~:2090, under a "Legacy aliases for backwards compatibility" heading) is exported from `scripts/weather_daemon/__init__.py:9-11` but nothing in the repo calls it; `CONUS_ARTCCS` (`scripts/weather_daemon/index_generator.py:36`) holds ZAN and ZHN, so its name misleads and the log line "Found N ARTCC boundaries, {len(CONUS_ARTCCS)} in CONUS" (`generator.py` ~:1952, :1955) overcounts. Remove the alias and rename the set (e.g. `MAPPED_ARTCCS`) with the log wording.
