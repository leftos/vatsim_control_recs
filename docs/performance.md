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
- Table population and updates cost 1 to 66 ms and split-flap animation frames 0.4 ms, so the UI's own work is not the bottleneck. The largest remaining stall is Textual's `DataTable` column measuring on a table rebuild (up to about 430 ms), which is why a changed row count updates rows in place.

## Weather API limits

aviationweather.gov caps a response at 400 entries and does so silently: a CONUS-sized box returned exactly 400 while a padded 10 degree box over the US northeast returned 136. A response of 400 may therefore be missing stations, and an airport absent from it proves nothing.

`BBOX_MAX_ENTRIES` in `backend/data/weather.py` is that cap. `_store_bbox_result` records a "no METAR" negative for the requested airports a box omitted only when the request succeeded and returned fewer than `BBOX_MAX_ENTRIES` entries; a box at the cap stores only the METARs it returned.

## Cost of a refresh

A refresh is `analyze_flights_data` in `backend/core/analysis.py` on an executor thread, followed by the table update on the UI thread; the analysis fetches no weather. WIND and ALT come from a separate weather pass (`fetch_weather_for_airports` in `backend/core/analysis.py`) on a daemon thread named "weather-pass", which patches the table box by box. With warm caches its main-thread CPU is a fraction of a second. The pieces that make it so:

- **One refresh at a time.** `auto_refresh_callback` in `ui/app.py` returns while `refresh_in_progress` is set, and a refresh never cancels a running one, so the period is the longer of the interval and the analysis time. Overlapping analyses would multiply CPU and memory (19 concurrent analyses reached 505 MB, against about 300 MB for one) and keep the process alive on quit.
- **Fresh boxes are skipped.** `get_weather_for_airports_bbox` (`backend/data/weather.py`) fetches a box only when `_bboxes_needing_fetch` finds one of its airports with neither a METAR younger than `METAR_CACHE_DURATION` (60 s, `backend/config/constants.py`) nor a fresh negative. Between expiry waves a refresh makes the VATSIM feed call (cached 15 s) and 0 to 3 box requests.
- **Clean negative cache.** An airport with no METAR is cached as a negative for the same 60 s, only when both aviationweather.gov and metar.vatsim.net answered empty; failures are never cached. Wind and altimeter reads then do not refetch it, and the per-station fallback in `get_metar` is rare.
- **One freshness time.** Each weather pass takes one `as_of` time (`_run_weather_pass` in `ui/app.py`) and passes it to the bbox call and the wind and altimeter reads, so a slow pass cannot expire entries it skipped and trigger one-station-at-a-time fetches.
- **One weather pass at a time.** A refresh landing mid-pass queues exactly one more pass and cancels nothing, for the same reason as one refresh at a time.
- **CIFP indexed once.** `get_dp_data`, `get_star_data` and `get_approaches_for_airport` in `backend/data/cifp.py` answer from an index built in one pass over the CIFP file (`_build_cifp_index`), about 1 s once and about +10 MB. Without it every SID or STAR lookup scanned the whole file. `_get_parsed_route` in `backend/core/route_distance.py` caches parsed routes with `lru_cache(maxsize=4096)`, above the number of simultaneous flights.
- **Set-based allowlist.** `filter_airports_by_allowlist` in `backend/core/analysis.py` converts the allowlist to a set once and tests membership in it; testing each of the roughly 38,000 airports against a 22,900-entry list took 6 to 10 s of CPU per refresh.
- **Bounded nearest-airport search.** `SpatialIndex.find_nearest` (`backend/core/spatial.py`) returns `None` when the neighbouring 1 degree cells hold no airport and a radius is given; only radius-less queries scan every airport.
- **NER skipped for CSV-covered names.** `_process_location_internal` in `airport_disambiguator/disambiguator.py` returns early when every airport of a multi-airport location already has a name from `data/airport_names.csv`, so the Go To name warm-up takes well under a second instead of running spaCy over thousands of locations.

The traffic columns update as soon as the analysis returns, and no table update waits on weather. A weather pass lasts as long as its slowest box (`_fetch_bboxes` waits up to 30 s per box), which has reached about 20 s when most boxes expired; until a box returns, its airports show their previous WIND and ALT, or blank when new. Each box patch re-sorts and diffs the table on the UI thread, 1.4 to 29 ms measured per patch.

Peak RSS is about 270 to 310 MB in every load. Quitting waits for an in-flight analysis because `asyncio.run` joins the executor threads. A quit during a weather pass starts no queued box request or per-station fallback, but requests already in flight may finish: Python's `concurrent.futures` exit handler joins pool workers mid-request, up to 30 s each. A box returning after shutdown is dropped.

## Measuring

Run the real app headless and instrument it; no source change is needed.

- **Rig.** Build the app as `main.py` does (`load_reference_data`, `resolve_airport_allowlist`, `analyze_flights_data`, `VATSIMControlApp`) and run it under `app.run_test(size=(170, 48))`, with animations disabled. Give the run its own `LOCALAPPDATA` holding a copy of the CIFP, NASR and runway caches so nothing downloads during the run, and no weather cache so the first refresh is cold.

  Record what the data directory's `custom_groupings.json` and `favorites.json` hold, because they change the Groupings tab.
- **Loads.** Three comparable loads: a European filter (`--countries GB DE NL FR`, about 1,200 tracked airports), `--countries US` (about 22,900 tracked airports, the allowlist-heavy case) and no filter (about 38,000 airports, the flight-heavy case). Live traffic changes with the time of day, so compare runs taken at similar times and record the pilot count.
- **Event-loop health.** Start a 50 ms timer on the app's event loop and log the gap between ticks; a gap over 100 ms is a stall. Add a watchdog thread that, while the tick is late, samples every thread's stack with `sys._current_frames()`: a main thread found idle in the event loop's poll or sleep while another thread is busy is GIL starvation, not a blocking call on the UI thread.
- **Per-phase wall time.** Wrap `analyze_flights_data`, `calculate_eta`, `fetch_weather_for_airports`, `get_weather_for_airports_bbox`, `get_metar` and the table update. Report network wait apart from CPU (main-thread CPU of the analysis call via `time.thread_time`), and record each weather box's request duration.
- **Refreshes.** Use the default 15 s timer for steady state, and split the refreshes into "wave" refreshes (most boxes expired, 5 or more bbox requests) and non-wave ones, because one average hides both. For a single isolated refresh call `action_refresh()` with the timer pushed out of the way.
- **Profilers.** `cProfile` and `pstats` for CPU hotspots; on Python 3.13 cProfile includes the bbox worker threads, so cumulative times of waits exceed wall time. `uvx py-spy dump` and `uvx py-spy record --format speedscope` attach to the running app on Windows without admin rights.
- **Memory.** Peak RSS from `psutil.Process(pid).memory_info().peak_wset` on Windows. Under uv on Windows the venv's `python.exe` is a small launcher shim (about 4.6 MB working set), so poll the child's children too.
- **What a current run gives.** On `--countries US` a non-wave refresh has about 0.1 to 0.25 s of analysis CPU and no stalls over a few hundred milliseconds; on the no-filter load, 0.2 to 0.3 s of CPU and `calculate_eta` at 20 to 40 ms per refresh. Cold startup to first rows (traffic, WIND and ALT blank) is a few seconds on the filtered loads and under 20 s without a filter; the weather boxes then fill WIND and ALT in, about 1.6 s after the first screen on `--countries US` live.

Measured and not worth optimising: table population and in-place updates, split-flap animation, the unified airport data load (about 0.3 s once), disambiguator construction (about 0.1 s once), NASR loads (under 1 s once), the VATSIM download (0.2 to 0.3 s) and the Go To rebuild on each refresh (2 to 30 ms).
