# Plan
<!-- plan-doc-hygiene: 2026-10-10 a31607e -->

The repo's single plan index. Every task is a checkbox here; detail goes into a linked subplan beside this file. A finished item's line is deleted in the commit that lands it.

## Current focus

Nothing in focus; the next session picks from the backlog.


## Backlog

### Singles

Items that share no files. Review: `code-review`. Verification: `uv run --group dev pytest -q` plus `ruff check`.

- [ ] Every second no-filter refresh re-analyses the same VATSIM snapshot: the feed updates every 15 s and is cached 15 s, so with the 15 s timer about half the `analyze_flights_data` runs (`backend/core/analysis.py:157`) see an unchanged `general.update_timestamp` and spend ~0.15 s of CPU changing nothing. Skip the analysis when the snapshot's timestamp has not moved.
- [ ] Burn down the `C901` ratchet: `ruff.toml` `[lint.per-file-ignores]` lists 28 files (45 functions) that predate the max-complexity 8 rule. Split a file's functions under 8 and take it off the list, characterization tests first (`tests/test_area_clustering_characterization.py` and `tests/test_weather_characterization.py` are the models), then an uncommitted differential fuzz that runs a copy of the original module and the split one on thousands of seeded random inputs and compares their outputs; `uvx ruff@0.16.1 check --select C901 --isolated --config 'lint.mccabe.max-complexity=8' <file>` lists them. Several files hold one offender each and bundle well into one brief (the C901 command above run per `ruff.toml` entry counts them); `tests/test_flight_info_characterization.py` is the model for a Textual modal.
- [ ] Remove wall-clock time assertions from the tests (`load-proof-tests`, "No test asserts a time"): no test may assert an elapsed time, beat a short timeout, or rely on a fixed sleep; the only wall-clock bound left is a hang guard sized in minutes that fails as "hung". Sites:

  `tests/test_allowlist_filter.py:35-40` asserts filtering 38,000 airports takes under 0.5 s; replace with an operation count (the allowlist is turned into a set once, no per-airport list scan) or delete it.

  `tests/test_refresh_overlap.py:129-139` `_wait_until` fails after 10 s, and `tests/test_refresh_weather.py:59` `WAIT = 10.0` bounds `_wait_until` (`:217-224`), the fixture's `asyncio.wait_for(app.workers.wait_for_complete(), WAIT)` (`:210`), the box-request poll (`:657-660`) and `worker.join(WAIT)` (`:664`); make each a minutes-long hang guard whose message says "hung", and replace the `:657-660` `time.sleep(0.01)` poll with an event the fake's `gated_bbox` sets on the fifth request.

  `tests/test_table_refresh.py:30` `FADE_TIMEOUT = 10.0` is already a hang guard; raise it to minutes.

  `tests/test_refresh_overlap.py:174` and `:251` sleep 50 ms so `datetime.now()` moves before `blocked_at`, then assert `last_refresh_time > blocked_at` (`:183`, `:261`); give the app a clock seam for `last_refresh_time` and step it instead.

  `tests/test_weather_generator_characterization.py:373` lets ZOA's fake fetch go on after 5 s when ZLA's failure is never logged; wait without the window and fail as hung under a minutes-long guard.

  `tests/test_logger.py:74` and `:222` (`subprocess.run(..., timeout=180)`) are minutes-long hang guards already and stay. No profile or doc routes a test run to `--slot critical`.
- [ ] A grouping's STAFFED cell orders its airports by iterating a set of strings, so the order changes with Python's per-process hash seed ("KJFK, KEWR, KISP" one run, "KISP, KEWR, KJFK" the next; the Groupings tab, `backend/core/analysis.py` grouping stats). Sort it (e.g. by airport, or busiest first); `scripts/capture_guide_screenshots.py` pins `PYTHONHASHSEED=0` to work around it.
- [ ] Shortcut and screen defects the user guide audit found: the help screen (`ui/modals/help_modal.py:11-37`) and the command palette (`ui/modals/command_palette.py:13-47`) omit `Ctrl+L`, which `ui/app.py:220` binds to Go To; in the Tracked Airports Manager the quick-add `+` does nothing while tracking everything (a bare `pass` at `ui/modals/tracked_airports.py:264-268`); `FlightLookupScreen` (`ui/app.py:1576`, `ui/modals/flight_lookup.py`) has no binding or palette entry and is dead; `--airports` codes are not upper-cased (`main.py:209`) and the allowlist match is case-sensitive, so a lowercase ICAO likely tracks nothing (unmeasured in the running app; the guide tells users to type capitals).
- [ ] `airport_disambiguator/disambiguation_engine.py` dead code: `_try_combinations_with_priority` (~:274), `_try_sequential_combinations` (~:306) and `_is_unique_in_group` (~:326) are called only by each other, superseded by the `_optimized` pair (whose docstrings say only "Optimized version using pre-computed data."). In `airport_disambiguator/entity_extractor.py`, `extract_entities` compares a raw entity with the city and state before stripping descriptors, so "San Francisco International" (labelled `FAC`) yields location "San Francisco", the city itself; `tests/test_entity_extractor_characterization.py` pins that.
- [ ] ATIS text ending in a backslash escapes the closing tag its caller appends: `[/#aaaaaa]` after `colorize_atis_text`'s output in `scripts/weather_daemon/generator.py` ~:1090 and `[/dim]` in `ui/modals/weather_briefing.py` ~:354 then show as literal text instead of closing the style (no crash). Have the callers escape or strip a trailing backslash run, or let `colorize_atis_text` take the wrapping style. In the same file, `colorize_atis_text` (`backend/data/atis_filter.py`) runs about 260 lines, past the 100-line limit; split it into its pattern passes.
- [ ] The weather and flight briefings duplicate their `P` export: each builds its own print CSS (`_PRINT_CSS` in `ui/modals/weather_briefing.py`, an inline string in `ui/modals/flight_briefing.py` `action_print`) and its own temp-file write and browser open (`_open_in_browser` and `action_print`); share one helper. In the same file, `_parse_metar_observation_time` (`ui/modals/weather_briefing.py` ~:96-99) swallows an `OSError` from `astimezone()` without logging it, and only `ui/modals/route_weather.py` calls it, so it may belong there.
- [ ] Write a weather briefing subsystem doc (`docs/` deep doc linked from `docs/ARCHITECTURE.md`'s Deep docs): the Task Index row names only code, and the areas, the REQUESTED section, fallback groups and the towered test (size priority ≤ 2 or an ATIS, `backend/briefing/area_clustering.py` ~:430) are explained nowhere; add "area", "fallback group", "REQUESTED section" and "towered" to the glossary in `docs/README.md`.
