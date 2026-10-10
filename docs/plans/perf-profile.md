# Performance profile under heavy traffic

Measured hotspots and ranked fixes for the Textual TUI, from headless runs against live VATSIM data. Nothing in source was changed; every gain below was measured, either in the shipped code or by patching an in-process copy of the candidate fix against one saved VATSIM snapshot.

## Context

- Date: 2026-10-10, live data captured 09:15–10:30Z (a Saturday morning in Europe, night in the US). The network had 1,441–1,484 pilots and 111 controllers online, so the US was quiet: S2 showed only 24–33 airport rows.
- Commit: `1f50951` (branch `perf-profile`).
- Machine: AMD Ryzen 7 7800X3D (8 cores, 16 threads), 63 GB RAM, Windows 11 Pro 10.0.26200. Python 3.13.15 (uv), Textual 8.2.8, Rich 15.0.0.
- App built exactly as `main.py` builds it (`load_reference_data`, `resolve_airport_allowlist`, `analyze_flights_data`, `VATSIMControlApp`), run under `app.run_test(size=(170, 48))`, with an isolated `LOCALAPPDATA` holding a warm CIFP, NASR and runway cache, so `load_reference_data` downloads nothing.
- Tools: stdlib `cProfile`/`pstats`; `uvx py-spy` (`dump` and `record --format speedscope`), which attached to the running app on Windows without admin rights; per-function wall-time wrappers; a 50 ms heartbeat timer on the app's event loop with a watchdog thread that samples every thread's stack while the heartbeat is late; peak RSS from `psutil` (`peak_wset`).
- Network time is reported apart from CPU time. "Wall" in the weather rows is network wait; "CPU" is main-thread CPU of the analysis call.
- The measuring scripts are untracked under `.tmp/perf/` in the `perf-profile` worktree (`prof_phases.py`, `drive_tui.py`, `drive_auto.py`, `exp_gains.py`, `exp_verify_eta.py`), and their raw outputs are in `.tmp/perf/out/`.

## Scenarios

| | Filter | Animations | Tracked airports | Airport rows | Grouping rows | Flights analysed |
|---|---|---|---|---|---|---|
| S1 | `--countries GB DE NL FR` | off | 1,226 | 48–50 | 16 | 581–604 |
| S2 | `--countries US` | off | 22,898 | 24–33 | 46–56 | 251–267 |
| S2anim | `--countries US` | on | 22,898 | 26 | 48 | 253–256 |
| S3 | none | off | all 38,025 with coordinates | 326–335 | 67–69 | 1,434–1,458 |

## Startup, phase by phase

Each cell is a range over the measured runs (S1: 3 runs, S2: 4 including S2anim, S3: 2). The cProfile-instrumented runs are left out because profiling inflates wall time. Sub-phase cells come from the runs that recorded a breakdown.

A third S3 startup, in the auto-refresh run below, took 378 s for the same analysis. That run recorded no breakdown; slow weather servers are the likely cause but were not measured.

| Phase | S1 | S2 | S3 |
|---|---|---|---|
| `load_reference_data` (caches warm) | ≤ 2 ms | ≤ 2 ms | ≤ 2 ms |
| Unified airport data load | 0.39 s | 0.31 s | 0.29 s (inside `analyze_flights_data`) |
| `AirportDisambiguator.__init__` | 0.13 s | 0.10 s | 0.11 s |
| Country expansion | 7 ms | 8 ms | n/a |
| `analyze_flights_data`, total wall | 19.6–25.4 s | 22.1–39.4 s | 134.2–136.8 s |
| …VATSIM download | 0.22–0.27 s | 0.22–0.23 s | 0.27 s |
| …flight categorisation and ETA (`calculate_eta` wall, almost all CPU) | 15.0–18.6 s | 9.7–12.9 s | 86.7 s |
| …allowlist filter (`analysis.py:179-184`, CPU) | 0.35–0.49 s | 6.6–9.9 s | n/a |
| …`get_nearest_airport_if_on_ground` (CPU) | 0.14 s | 0.9–1.3 s | 0.07 s |
| …weather bbox fetch (network wall) | 0.5–0.65 s (6 requests) | 3.5–4.9 s (13–15 requests) | 15.5 s (112 requests) |
| …per-station METAR fallback (network wall, sequential) | 4.5–4.9 s (9–12 calls) | 1.9–11.7 s (2–4 calls) | 28.8 s (71 calls) |
| …names via `get_full_names_batch` | 0.47 s | 1.1–1.9 s | 2.3 s |
| First table population (`populate_tables`) | 5 ms | 5–35 ms | 66 ms (398 rows) |
| Process start → first rows painted | 21.0–22.5 s | 23.3–41.2 s | 137.8 s |
| Mount → Go To cache ready | 1.1 s | 36.2–52.0 s | 0.3 s |
| Peak RSS (manual refreshes) | 275–282 MB | 288–298 MB | 290–297 MB |

S3 loads completely: all 331 airport rows were on screen 137.8 s after process start, well inside the 10-minute cap. The cost is the wait, not a hang.

With animations on (S2anim), the initial split-flap animation settled 1.7 s after the first rows. `_animate_cells` ran 70 frames for 27 ms in total (0.4 ms per frame, 2 ms at most).

`analyze_flights_data` calls `get_full_names_batch`, not `get_pretty_names_batch` as `docs/ARCHITECTURE.md` (Data flow, step 3) says.

## One refresh cycle, steady state

Measured by calling `action_refresh()` from the driver (the timer's own call), with the auto-refresh timer pushed out of the way, so each refresh ran alone.

| | S1 | S2 | S2anim | S3 |
|---|---|---|---|---|
| Refresh, trigger → tables updated | 2.5–8.9 s (one outlier 17.7 s) | 12.2–18.5 s | 10.2–12.9 s | 129.8–157.7 s |
| …analysis main-thread CPU | 0.6–1.1 s | 10.4–11.8 s | not measured | 88.9–98.0 s |
| …`calculate_eta` | 0.01–0.3 s (route cache hits) | ≤ 0.1 s (route cache hits) | ≤ 0.1 s | 90.1–99.8 s (950–1,001 calls, all misses) |
| …network calls / summed wall | 11–15 / 2.9–9.6 s | 13–20 / 5.0–10.8 s | 14–16 / 3.6–4.7 s | 214–238 / 86–105 s |
| …table update (`_update_efficiently`) | 1–6 ms | 1–6 ms | 0–4 ms | 27 ms |
| …Go To rebuild (`_build_goto_results`, warm) | 2 ms | 19–30 ms | 19–20 ms | 2 ms |
| Animations settled after the worker | n/a | n/a | 0–0.9 s | n/a |

The S1 outlier (17.7 s) came in the first TUI run, before the driver recorded a per-call breakdown, so its cause is not attributed. The other S1 spreads track network wait: a sequential per-station METAR fetch costs 2–3 s when a server is slow.

### With the default 15 s auto-refresh timer

`auto_refresh_callback` (`ui/app.py:416-438`) calls `action_refresh` once 15 s have passed since the last trigger, whether or not the previous refresh has finished. `action_refresh` starts `refresh_worker` with `run_worker(..., exclusive=True)` (`ui/app.py:598-610`), which cancels the running refresh. The cancelled worker's `run_in_executor` thread keeps running `analyze_flights_data`, and its result is thrown away.

| | S2, 185 s after mount | S3, 303 s after mount |
|---|---|---|
| Refreshes started | 11 | 19 |
| Refreshes that updated the tables | 0 | 0 |
| Refreshes cancelled by the next one | 11 | 19 |
| Analyses running at once (max) | 5 | 19 |
| Analyses finished | not counted | 0 |
| Peak RSS | not recorded | 505 MB |

In S2 and S3 the tables never update after startup. Each cancelled analysis keeps burning CPU and slows the next, so the pile-up feeds itself. Quitting also stalls: `asyncio.run` waits for the abandoned executor threads, and the S3 process was still alive 60 s after the driver finished and had to be stopped. This is the most likely reason the owner sees the no-filter app bog down.

## Top hotspots

| # | Function | file:line | Cumulative | Calls |
|---|---|---|---|---|
| 1 | `get_dp_data`, `get_star_data` | `backend/data/cifp.py:606`, `:703` | Under cProfile: 84.2 s + 83.8 s in one S3 refresh; 26.7 s + 16.6 s in the S1 cold start | 469 + 467 (S3), 121 + 78 (S1) |
| 2 | `analyze_flights_data` (own time), the allowlist filter | `backend/core/analysis.py:179-184` | S2: 6.6–9.9 s CPU per call; S1: 0.35–0.49 s | 1 per refresh |
| 3 | `Disambiguator._process_location_internal` | `airport_disambiguator/disambiguator.py:134-162` | S2: 36.1–51.7 s | 10,450 locations |
| 4 | `get_metar` (per-station fallback) | `backend/data/weather.py:331`, from `analysis.py:334-344` | S3: 28.8–50.0 s wall per refresh; S1/S2: 0.3–11.7 s | S3: 68–75 per refresh; S1/S2: 1–12 |
| 5 | `fetch_weather_bbox` | `backend/data/weather.py:787`, via `:1585` | S3: 7.5–15.5 s wall per refresh (63.9–68.9 s summed over 5 workers); S2: 2.1–4.9 s; S1: 0.65–1.5 s | S3: 106–115; S2: 12–15; S1: 6 |
| 6 | `SpatialIndex.find_nearest` | `backend/core/spatial.py:85`, fallback `:113-116` | S2: 0.8–1.7 s CPU per refresh | 62–85 |

Why each is slow:

1. **CIFP lookups.** Each call reads and scans the whole 53 MB CIFP file line by line (`cifp.py:638-640`, `:732-734`): 371.8 M `str.startswith` calls in one S3 refresh, 0.16–0.22 s per call. The file holds FAA US data only (`SUSAP K…` records), yet every non-US SID or STAR still scans it to find nothing.

   The caches are smaller than the working set: `lru_cache(maxsize=100)` on both functions, and `lru_cache(maxsize=512)` on `_get_parsed_route` (`backend/core/route_distance.py:55`) against about 950 routes in S3. So the S3 hit rate is zero on every refresh.

   Reached through `calculate_eta` (`backend/core/calculations.py:117-128`) → `parse_route_string` → `_expand_sid_star` (`backend/data/navaids.py:998-999`, `:826`). py-spy agrees: 59% of the main thread's busy samples during S3 startup sit on `cifp.py:639` and `:733`.
2. **Allowlist filter.** `icao in airport_allowlist` tests membership in a list: 38,025 airports, each checked against up to 22,898 list entries. The same filter over a set takes 3–4 ms.
3. **Go To name warm-up.** `_build_goto_results` (`ui/app.py:321-351`, run at mount by `_warm_up_goto_cache`, `ui/app.py:293`) asks for every tracked airport's name. For a location with several airports, the multi-airport branch (`disambiguator.py:151-162`) runs spaCy NER over all of them even when `data/airport_names.csv` already holds every name, then throws the result away.
4. **Per-station METAR.** Airports missing from the bbox results are fetched one at a time, in order, through `get_wind_from_metar` (`weather.py:580`) and `get_altimeter_setting` (`weather.py:934`).

   An empty result is not cached (`weather.py:378-383`), so wind and altimeter each refetch it. That is 2 aviationweather.gov calls plus 2 metar.vatsim.net calls per station per refresh, for 25 such stations in one S3 refresh.
5. **Bbox weather.** Every refresh refetches every 10° box, though the METAR cache it fills lasts 60 s (`backend/config/constants.py:19`) and the refresh runs every 15 s.
6. **Nearest-airport fallback.** A ground flight with no tracked airport in its 3×3 cell neighbourhood falls back to scanning every tracked airport: 884,826 haversine calls in one S2 refresh. With `max_distance_nm=6` and 1° cells, that fallback can never find a match.

## Event-loop health

A 50 ms heartbeat timer ran on the app's event loop for the whole run. A gap over 100 ms means the UI thread could not run for that long.

| Window | Stalls > 100 ms | Total stall time | Max gap | p99 gap |
|---|---|---|---|---|
| S1 refresh (2.5–9 s each) | 3–4 | 0.4–0.6 s | 171–248 ms | 98–218 ms |
| S2 refresh (12–19 s each) | 53–65 | 8.8–10.3 s | 247–276 ms | 203–249 ms |
| S2anim refresh | 52 | 8.1–9.2 s | 234–310 ms | 232–276 ms |
| S3 refresh (135 s) | 1 | 0.1 s | 143 ms | 52 ms |
| S2 startup, Go To warm-up running | 0–18 | 0–2.6 s | 72–363 ms | 51–110 ms |

The UI's own work is small. Table population and updates take 1–66 ms, and the only main-thread stall caught in UI code was 143–195 ms in Textual's `DataTable._on_idle` → `_update_column_widths` / `_update_dimensions` → `rich.Console.size`, once per table rebuild.

The long stalls happen while the main thread is idle: the watchdog found it in the proactor `_poll` or Textual's `_win_sleep.wait`. In those same samples the busy thread was the refresh's executor thread at `analysis.py:181` (the list-membership filter) or in `find_nearest`. For many stalls the watchdog got no sample at all, because it was waiting for the GIL too.

These are signs of GIL starvation, not blocking calls on the UI thread. A worker thread running pure-Python CPU work, with no I/O, hands the GIL back only at the switch interval. The event loop has to win it back after every system call it makes.

A controlled test on a bare asyncio loop confirms the pattern (`exp_gains.py`, E5). With the US list filter running in a thread, 50 ms ticks came at a 77 ms median, 12 over 100 ms and up to 138 ms. A pure-CPU set loop gave the same 77 ms median, with one gap of 470 ms.

The CIFP scan reads a file, so it releases the GIL on every buffer: it gave a 62 ms median and none over 100 ms. That is why the 90 s of CPU in an S3 refresh hardly disturbs the UI, while 10 s of list scanning in S2 freezes it for 9 s.

Removing the CPU work fixes this; moving it to another thread does not. Fixes 3, 4 and 6 below take that CPU out of S2 almost entirely.

## Memory

Peak RSS stayed between 275 and 306 MB in every run with a single refresh in flight, animations on or off. The only growth came from the piled-up refreshes at the default interval: 505 MB with 19 concurrent analyses in S3. Holding a parsed SID/STAR index from CIFP costs +10 MB (a raw-line index +44 MB), measured in a fresh process.

## Ranked fix proposals

1. **Don't start a refresh while one is running.** In `auto_refresh_callback`, skip the tick while `refresh_worker` is still running, and stop `action_refresh` from cancelling an analysis already under way (fold a manual Ctrl+R into the running one).
   - Expected gain: S2 and S3 tables update at all, today 0 of 11 and 0 of 19 timer refreshes land. Concurrent analyses drop from 19 to 1, peak RSS from 505 MB to the 290 MB baseline, and quitting no longer waits on abandoned threads.
   - Size S. Files: `ui/app.py` (`auto_refresh_callback`, `action_refresh`, `refresh_worker`).
   - Risk: when an analysis outlasts the interval, the real refresh period becomes the analysis time. The status bar's "Last refresh" should then count from the last completed refresh, not the last start.
2. **Index CIFP procedures once.** Build the SID/STAR records into a dict keyed by airport (and procedure) in one pass over the file, the first time one is needed. Skip the lookup for airports CIFP cannot contain (anything not reached as `SUSAP K…`). Make the route and procedure caches unbounded, or size them to the flight count.
   - Expected gain (measured, same snapshot, identical ETAs for all 227 S1 and 109 S2 arrivals): the S3 ETA pass falls from 80.7 s cold / 82.3 s warm to 0.31 s / 0.03 s, S1 cold from 17.6 s to 0.06 s, S2 cold from 11.0 s to 0.09 s. Building the index takes 0.1–0.2 s.
   - That removes about 85–100 s of CPU from every S3 refresh and 10–18 s from every cold start.
   - Size S–M. Files: `backend/data/cifp.py` (`get_dp_data`, `get_star_data`), `backend/core/route_distance.py:55`.
   - Risk: +10 MB RSS. The index must be rebuilt when `ensure_cifp_data` switches AIRAC cycle.
3. **Use a set for the allowlist.** Convert `airport_allowlist` to a set once, in `analyze_flights_data` (`analysis.py:179-184`), or when the app stores it (`ui/app.py:194`).
   - Expected gain: 6.6–9.9 s of CPU per S2 refresh down to 4 ms, and 0.35–0.49 s per S1 refresh. Most of the 53–65 UI stalls in each S2 refresh go with it.
   - Size S. Files: `backend/core/analysis.py`. The same list-membership pattern is in `get_airport_flight_details` (`backend/core/flights.py:233-237`, `:280`, `:344`, `:377`, `:538`), which flight boards call; check it while there.
   - Risk: none seen; same output by construction.
4. **Skip NER for locations the names CSV already covers.** In `_process_location_internal`, return early when every airport of the location already has a name.
   - Expected gain (measured): `get_full_names_batch` for the US allowlist falls from 42.4 s to 0.03 s, with identical names for all 22,898 airports. Go To readiness in S2 drops from 35–52 s after mount to about 1 s, and up to 18 startup UI stalls go away.
   - Size S. Files: `airport_disambiguator/disambiguator.py`.
   - Risk: a location with one CSV-less airport still runs NER for the whole group; that path is unchanged.
5. **Stop refetching weather that is still fresh, and cache "no METAR".** In `get_weather_for_airports_bbox`, fetch boxes only for airports whose METAR cache entry is older than `METAR_CACHE_DURATION`. In `get_metar`, cache an empty result for the same duration. Skip the per-station fallback for airports a successful bbox call covered.
   - Expected gain (network): per S3 refresh, the 68–75 sequential station calls (28.8–50.0 s) and, on 3 refreshes in 4, the 106–115 bbox requests (7.5–15.5 s wall). Per S2/S1 refresh, 2–5 s of bbox wall on 3 in 4 refreshes, and the 2–3 s per slow station.
   - Size M. Files: `backend/data/weather.py` (`get_weather_for_airports_bbox`, `get_metar`), `backend/core/analysis.py:322-344`.
   - Risks: a METAR that appears mid-minute shows up to 60 s later. A CONUS-sized bbox request returned exactly 400 stations, which suggests the API caps a response. If 10° boxes over dense areas hit that cap, some reporting stations are missing from the bbox results and today land in the per-station fallback. Check the cap before skipping that fallback for bbox-covered airports.
6. **Drop the full-scan fallback in `find_nearest` when a radius is given.** Return `None` when the 3×3 neighbourhood is empty and `max_distance_nm` is set (`backend/core/spatial.py:113-116`).
   - Expected gain (measured, identical results): S2 `get_nearest_airport_if_on_ground` for 256 flights falls from 0.78 s to 0.013 s, and S1 from 0.068 s to 0.010 s. This is pure-Python CPU, so it also adds to the GIL stalls.
   - Size S. Files: `backend/core/spatial.py`.
   - Risk: none while the cell size (1°) exceeds the radius (6 nm). Keep the fallback for radius-less callers (`find_nearest_airport`).

Together, fixes 2–4 and 6 remove almost all CPU from a refresh. Fix 5 removes most of the network wait. Fix 1 makes sure whatever is left can never pile up.

## Not worth it (measured cheap)

- Table population and updates: `populate_tables` 5–66 ms (398 rows in S3), `_update_efficiently` 1–27 ms, `add_row` 0.04–0.15 ms per row.
- Split-flap animations: 0.4 ms per frame at S2's row counts, settling in 0–1.7 s. Turning them off changed no measured cost.
- Progressive table loading: first population is already under 70 ms. The `--progressive-load` and `--progressive-chunk-size` options (`main.py:55-63`) are parsed but never read, and `TableManager.populate(progressive=...)` (`ui/tables.py:140`) is never passed `True`.
- Unified airport data load (0.29–0.39 s once), disambiguator construction (0.10–0.13 s once), NASR loads (`load_navaids` 0.03 s, `load_fixes` 0.40 s, `load_airways` 0.12 s, once), `load_reference_data` with warm caches (≤ 2 ms).
- VATSIM download (0.2–0.3 s), `get_staffed_positions`, `load_all_groupings` (≤ 20 ms), `format_eta_display` (22,169–38,337 calls, 20–30 ms).
- The Go To rebuild on each refresh once names are cached (2–30 ms).
- Memory: 275–306 MB peak in every non-piled-up run.

## Pre-existing issues seen while measuring

- At `run_test` teardown after the S2 auto-refresh run, the 0.25 s `update_time_displays` timer raised `NoMatches: '#status-bar'` from `update_status_bar` (`ui/app.py:472`, `:486`): the timer fired after the screen was torn down.
- `docs/ARCHITECTURE.md` (Data flow, step 3) and the current-focus item in `docs/plans/MAIN.md` name `get_pretty_names_batch`; the code calls `get_full_names_batch` (`backend/core/analysis.py:350`, `ui/app.py:330`).
- `--progressive-load` / `--progressive-chunk-size` are dead options (see above).
