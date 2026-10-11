# Performance

What a refresh costs, why Python work on a worker thread can freeze the UI, and how to measure it again.

## GIL starvation

The Python interpreter runs one thread's bytecode at a time, holding the GIL (global interpreter lock). A thread running pure-Python CPU work with no I/O gives the lock up only at the switch interval, and the Textual event loop has to win it back after every system call it makes. The UI then runs in short, late slices: ticks of a 50 ms timer arrive at a 77 ms median with gaps past 100 ms, and a full second of CPU can freeze the screen for most of that second.

Moving the work to another thread does not help, because the refresh's analysis already runs on an executor thread. Only removing the CPU helps.

Work that reads a file or waits on the network releases the GIL on every buffer or wait, so it barely disturbs the UI: a scan of a 53 MB file measured a 62 ms median tick with none over 100 ms. Python loops over large collections are the dangerous kind.

Design rules that follow:

- Keep the per-refresh Python CPU near zero: index once, test membership in sets, skip work whose answer is cached.
- Never test membership in a list of tens of thousands of entries inside a loop.
- Do not start a second analysis while one runs; each extra thread is more CPU competing for the GIL.
- Table population and updates cost 1 to 108 ms and split-flap animation frames 0.4 ms, so the UI's own work is not the bottleneck. The largest remaining stall is Textual's `DataTable` column measuring on a table rebuild (up to about 430 ms), which is why a changed row count updates rows in place; no rebuild occurs at peak traffic, where the row count changes on most refreshes.

## Weather API limits

aviationweather.gov caps a response at 400 entries and does so silently: a CONUS-sized box returned exactly 400 while a padded 10 degree box over the US northeast returned 136. A response of 400 may therefore be missing stations, and an airport absent from it proves nothing.

`BBOX_MAX_ENTRIES` in `backend/data/weather.py` is that cap. `_store_bbox_result` records a "no METAR" negative for the requested airports a box omitted only when the request succeeded and returned fewer than `BBOX_MAX_ENTRIES` entries; a box at the cap stores only the METARs it returned.

The weather briefing (`get_weather_smart`) treats a failed box differently from the weather pass: a box that fails with an HTTP error other than 429, a network error or a non-JSON-array body sends its airports to per-airport requests, while an HTTP 429 leaves them blank so no per-airport requests go out while the API is rate-limiting.

## Cost of a refresh

A refresh is `analyze_flights_data` in `backend/core/analysis.py` on an executor thread, followed by the table update on the UI thread; the analysis fetches no weather. WIND and ALT come from a separate weather pass (`fetch_weather_for_airports` in `backend/core/analysis.py`) on a daemon thread named "weather-pass", which patches the table box by box. With warm caches its main-thread CPU is a fraction of a second. The pieces that make it so:

- **One refresh at a time.** `auto_refresh_callback` in `ui/app.py` returns while `refresh_in_progress` is set, and a refresh never cancels a running one, so the period is the longer of the interval and the analysis time. Overlapping analyses would multiply CPU and memory (19 concurrent analyses reached 505 MB, against about 300 MB for one) and keep the process alive on quit.
- **Fresh boxes are skipped.** `get_weather_for_airports_bbox` (`backend/data/weather.py`) fetches a box only when `_bboxes_needing_fetch` finds one of its airports with neither a METAR younger than `METAR_CACHE_DURATION` (60 s, `backend/config/constants.py`) nor a fresh negative. Each box is fetched again once its METARs pass the 60 s mark, which at the 15 s timer is every fourth refresh in the ideal case.

  At 1,700 to 1,800 pilots the no-filter load packed 370 to 410 airports into 77 to 88 boxes and `--countries US` packed 46 airports into 10 boxes.

  Passes often outlast the 15 s interval, so the requests do not follow a fixed cycle. The no-filter load sent 0 to about 74 box requests per refresh, averaging about 13 (12 to 14 % of the boxes), because a refresh that lands mid-pass sends nothing and the expiring METARs spread out. `--countries US` sent 0 to 6.

  Only a 429 or 503 from aviationweather.gov counts as rate limiting (`_check_rate_limit_error` in `backend/data/weather.py`): `_record_rate_limit_error` then doubles the shared backoff (1 s up to 30 s), and every box request in the next 60 s (`RATE_LIMIT_RECOVERY_TIME`) first sleeps that backoff in `_wait_for_backoff`. Any other failure, a 500, 502 or 504 included, fails its own box alone, and a non-429 failure sends that box's airports to single-station fallback fetches.

  At peak a 504 box takes about 20 s to fail, and its single-station fallbacks follow, so a pass with several of them still runs well past the 15 s interval. Backing off on every 5xx instead stretched one such pass, with five 504 boxes, to 106 s.

  Every refresh also makes the VATSIM feed call (cached 15 s).
- **Few, packed boxes.** `calculate_airport_bboxes` (`backend/data/weather.py`) returns one box when the airports fit in 10 degrees on both axes, else packs them first-fit in (longitude, latitude) order through `_pack_airport_coords`, each airport joining the first box it still fits in, so the box count and the boxes do not depend on airport order.
- **ETA only inside the window.** `analyze_flights_data` takes each airborne inbound's ETA from `arrival_eta_in_window` (`backend/core/flights.py`), which skips `calculate_eta` when (great-circle distance minus 5 nm) / groundspeed already exceeds `max_eta_hours`; the ARR count and NEXT ETA use that one result, and no separate pass computes an ETA for every in-flight arrival.

  On the no-filter load at about 1,800 pilots the pre-check skipped about 640 of 1,210 airborne inbounds (about 53 %), leaving about 570 `calculate_eta` calls that took 10 to 17 ms of CPU (14 to 46 ms wall on an unsaturated machine) per refresh; the first analysis spends 1.1 to 1.3 s in it, the CIFP index build (about 0.5 s) included. With `--countries US` it is about 40 calls out of 150 inbounds, 2 to 5 ms.
- **Clean negative cache.** An airport with no METAR is cached as a negative for the same 60 s, only when both aviationweather.gov and metar.vatsim.net answered empty; failures are never cached. Wind and altimeter reads then do not refetch it, and the per-station fallback in `get_metar` is rare.
- **One freshness time.** Each weather pass takes one `as_of` time (`_run_weather_pass` in `ui/app.py`) and passes it to the bbox call and the wind and altimeter reads, so a slow pass cannot expire entries it skipped and trigger one-station-at-a-time fetches.
- **One weather pass at a time.** A refresh landing mid-pass queues exactly one more pass and cancels nothing, for the same reason as one refresh at a time.
- **CIFP indexed once.** `get_dp_data`, `get_star_data` and `get_approaches_for_airport` in `backend/data/cifp.py` answer from an index built in one pass over the CIFP file (`_build_cifp_index`), about 0.5 to 0.6 s of CPU once and about +10 MB. Without it every SID or STAR lookup scanned the whole file. `_get_parsed_route` in `backend/core/route_distance.py` caches parsed routes with `lru_cache(maxsize=4096)`, above the number of simultaneous flights.
- **Set-based allowlist.** `filter_airports_by_allowlist` in `backend/core/analysis.py` converts the allowlist to a set once and tests membership in it; testing each of the roughly 38,000 airports against a 22,900-entry list took 6 to 10 s of CPU per refresh.
- **Bounded nearest-airport search.** `SpatialIndex.find_nearest` (`backend/core/spatial.py`) returns `None` when the neighbouring 1 degree cells hold no airport and a radius is given; only radius-less queries scan every airport.
- **NER skipped for CSV-covered names.** `_process_location_internal` in `airport_disambiguator/disambiguator.py` returns early when every airport of a multi-airport location already has a name from `data/airport_names.csv`, so the Go To name warm-up takes well under a second instead of running spaCy over thousands of locations.

The traffic columns update as soon as the analysis returns, and no table update waits on weather. A weather pass lasts about as long as its slowest box (`_fetch_bboxes` waits up to 30 s per box) when requests are few; single boxes of 16 to 21 s, even for a few entries, are common at peak.

A big pass is longer, because only 5 workers serve its requests: the cold first pass of 77 to 87 boxes takes about 20 s although its slowest box takes 6 to 8.5 s. A pass with failed boxes lasts longer still (see the failures above), and one pass over the 15 s interval puts passes out of step with refreshes. Until a box returns, its airports show their previous WIND and ALT, or blank when new.

Each box patch re-sorts and diffs the table on the UI thread: a median of 1.7 to 2.7 ms, p95 2.6 to 4.3 ms and a maximum of 8 to 53 ms in steady state, and up to 178 ms in a cold first pass.

The UI pauses in steady state are 106 to 242 ms. They come from GIL contention at the start of each analysis (106 to 179 ms, an executor thread running Python while the event loop waits) and from repainting the roughly 370-row table after a refresh (111 to 242 ms, in Rich and `DataTable` rendering). No pause caught the main thread blocked in a project call. The 50 ms tick median is 50.0 to 50.2 ms.

The cold first pass shows larger pauses, up to about 1.2 s with 77 box patches arriving over 20 s on a machine at 94 % CPU, and 218 ms with the machine at about 50 %. On a loaded machine most long pauses are OS scheduling of the below-normal-priority process, not the app, so read pauses and wall times against the recorded machine CPU.

Peak RSS is 283 to 296 MB in every load. Quitting waits for an in-flight analysis because `asyncio.run` joins the executor threads. A quit during a weather pass starts no queued box request or per-station fallback, but requests already in flight may finish: Python's `concurrent.futures` exit handler joins pool workers mid-request, up to 30 s each. A box returning after shutdown is dropped.

## Measuring

Run the real app headless and instrument it; no source change is needed.

- **Rig.** Build the app as `main.py` does (`load_reference_data`, `resolve_airport_allowlist`, `analyze_flights_data`, `VATSIMControlApp`) and run it under `app.run_test(size=(170, 48))`, with animations disabled. Give the run its own `LOCALAPPDATA` holding a copy of the CIFP, NASR and runway caches so nothing downloads during the run, and no weather cache so the first refresh is cold.

  Record what the data directory's `custom_groupings.json` and `favorites.json` hold, because they change the Groupings tab.
- **Loads.** Three comparable loads: a European filter (`--countries GB DE NL FR`, about 1,200 tracked airports), `--countries US` (about 21,900 tracked airports, the allowlist-heavy case) and no filter (about 38,000 airports, the flight-heavy case). Live traffic changes with the time of day, so compare runs taken at similar times and record the pilot count.

  The US allowlist holds about 22,900 entries; 21,857 of them pass the filter and are tracked.
- **Running the rig.** Run it through `pwsh tools/gate.ps1 -Slot heavy`, which runs the command at below-normal priority. Record the machine-wide CPU load (`psutil.cpu_percent`) beside every figure: other work on the machine inflates every wall time and stall, and a run at 90 % or more is only good for CPU figures.
- **Event-loop health.** Start a 50 ms timer on the app's event loop and log the gap between ticks; a gap over 100 ms is a stall. Add a watchdog thread that, while the tick is late, samples every thread's stack with `sys._current_frames()`: a main thread found idle in the event loop's poll or sleep while another thread is busy is GIL starvation, not a blocking call on the UI thread.
- **Per-phase wall time.** Wrap `analyze_flights_data`, `calculate_eta`, `fetch_weather_for_airports`, `get_weather_for_airports_bbox`, `get_metar` and the table update. Report network wait apart from CPU (CPU of the analysis thread via its cycle count, `QueryThreadCycleTime` on Windows, because `time.thread_time` there moves in 15.6 ms steps and rounds a 10 ms `calculate_eta` cost to 0 or 15.6 ms), and record each weather box's request duration.
- **Refreshes.** Use the default 15 s timer for steady state, and record each refresh's bbox requests apart from its analysis, because the count varies from 0 to about 74 with no cycle at peak, and one average hides that.

  Note which refreshes start no pass because the previous one is still running, and that a refresh whose feed timestamp equals the previous one's analyses identical data (every second refresh, at 0.13 to 0.17 s). The weather runs on its own thread, so the analysis CPU does not depend on the requests. For a single isolated refresh call `action_refresh()` with the timer pushed out of the way.
- **Profilers.** `cProfile` and `pstats` for CPU hotspots; on Python 3.13 cProfile includes the bbox worker threads, so cumulative times of waits exceed wall time. `uvx py-spy dump` and `uvx py-spy record --format speedscope` attach to the running app on Windows without admin rights.
- **Memory.** Peak RSS from `psutil.Process(pid).memory_info().peak_wset` on Windows. Under uv on Windows the venv's `python.exe` is a small launcher shim (about 4.6 MB working set), so poll the child's children too.
- **What a current run gives.** A refresh has 0.13 to 0.30 s of analysis CPU (median about 0.2 s) on the no-filter load at 1,700 to 1,800 pilots and 0.13 to 0.22 s on `--countries US`.

  Cold startup to first rows (traffic, WIND and ALT blank) is about 4 to 5 s without a filter (a 2.1 to 2.3 s startup analysis, 1.7 to 1.8 s of it CPU) and about 3 s longer on `--countries US` for the allowlist, whose first rows took 18.6 s on a machine at 91 to 99 % CPU. The first WIND and ALT patch follows 0.3 to 1.0 s after the first rows; the whole cold pass takes about 20 s without a filter (77 to 87 boxes) and 6.7 s on `--countries US` (10 boxes).

  On `--countries US` the steady-state stalls reached 697 ms on a machine at 78 to 100 % CPU, and most were OS scheduling; whether it is free of stalls over a few hundred milliseconds on a quiet machine is unmeasured.

Measured and not worth optimising: table population and in-place updates, split-flap animation, the unified airport data load (about 0.3 s once), disambiguator construction (about 0.1 s once), NASR loads (under 1 s once), the VATSIM download (0.2 to 0.5 s) and the Go To rebuild on each refresh (1 to 5 ms).
