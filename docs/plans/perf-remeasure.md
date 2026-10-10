# Re-measure of the TUI after the six perf fixes (perf-remeasure @ 3fa9c0a)

Status: **resolved**. All six fixes work as intended. Every refresh in S1, S2 and S3 updated the tables, only one analysis ever ran at a time, refresh CPU fell from 10–98 s to 0.05–0.33 s, and S3 now shows rows 17 s after launch instead of 138 s.

The cost that remains is aviationweather.gov's response time. On top of that there is a new weather-cache expiry race. A box is skipped because its METARs are still fresh when the refresh starts, but they expire while the slower boxes are being fetched. The wind and altimeter reads that follow then fetch those stations one at a time. This turned one S3 refresh into 83.5 s, and it repeats in-process without any slow server.

## Live conditions

| | Profile run | This run |
|---|---|---|
| Time (feed stamps) | 09:15–10:30Z | S2 11:28–11:31Z, S3 11:32–11:37Z, S1 11:40–11:43Z, cProfile S3 about 11:44–11:47Z |
| Pilots | 1,441–1,484 | S2 1,701–1,722; S3 1,726–1,744; S1 1,747–1,765 (about 18–20 % more) |
| Controllers | 111 | 115–124 |
| S1 rows / groupings / flights | 48–50 / 16 / 581–604 | 51–52 / **0** / 718–725 |
| S2 rows / groupings / flights | 24–33 / 46–56 / 251–267 | 35–37 / 64–67 / 316–325 |
| S3 rows / groupings / flights | 326–335 / 67–69 / 1,434–1,458 | 357–365 / 68–72 / 1,704–1,730 |

The machine, Python (3.13 via uv) and Textual 8.2.8 are the same as in the profile run. The rig mirrors the profile's: main.py's build path (`load_reference_data`, `resolve_airport_allowlist`, `analyze_flights_data`, `VATSIMControlApp`) under `app.run_test(size=(170, 48))`, with `--disable-animations`. Each run had its own fresh LOCALAPPDATA holding a copy of the real CIFP, NASR and runway caches (no weather cache), plus a 50 ms heartbeat and a watchdog thread.

Unlike the profile, the refresh figures below come from the **default 15 s auto-refresh timer**, not from manual `action_refresh` calls.

## Startup

| Phase | S1 old → new | S2 old → new | S3 old → new |
|---|---|---|---|
| `load_reference_data` | ≤ 2 ms → 1.5 ms | ≤ 2 ms → 2.3 ms | ≤ 2 ms → 1.5 ms |
| Unified airport data load | 0.39 → 0.30 s | 0.31 → 0.34 s | 0.29 → 0.43 s (inside analysis) |
| `AirportDisambiguator.__init__` | 0.13 → 0.10 s | 0.10 → 0.12 s | 0.11 → 0.16 s |
| Country expansion | 7 → 6 ms | 8 → 8 ms | n/a |
| `analyze_flights_data` wall | 19.6–25.4 → **6.02 s** | 22.1–39.4 → **3.93 s** | 134.2–136.8 → **15.92 s** |
| …main-thread CPU | not tabled → 0.91 s | not tabled → 1.03 s | not tabled → 2.02 s |
| …VATSIM download | 0.22–0.27 → 0.30 s | 0.22–0.23 → 0.21 s | 0.27 → 0.22 s |
| …`calculate_eta` (includes the one-time CIFP index build) | 15.0–18.6 → 0.83 s (232 calls) | 9.7–12.9 → 0.88 s (138) | 86.7 → 1.20 s (1,143) |
| …allowlist filter | 0.35–0.49 s → 2 ms | 6.6–9.9 s → 3 ms | n/a |
| …`get_nearest_airport_if_on_ground` | 0.14 → 0.015 s | 0.9–1.3 → 0.023 s | 0.07 → 0.063 s |
| …bbox weather (wall) | 0.5–0.65 s, 6 req → **4.83 s, 5 req** | 3.5–4.9 s, 13–15 req → 2.74 s, 13 req | 15.5 s, 112 req → 13.70 s, 119 req (66.4 s summed) |
| …per-station METAR fallback | 4.5–4.9 s, 9–12 calls → 0 calls | 1.9–11.7 s, 2–4 → 0 | 28.8 s, 71 → 0 |
| …names (`get_full_names_batch`) | 0.47 s → < 1 ms | 1.1–1.9 s → < 1 ms | 2.3 s → 1 ms |
| First table population | 5 → 5.5 ms (51 rows) | 5–35 → 3.7 + 4.4 ms (35 + 64 rows) | 66 ms (398 rows) → 34.3 + 8.9 ms (365 + 70 rows) |
| Process start → first rows painted | 21.0–22.5 → **7.44 s** | 23.3–41.2 → **5.62 s** | 137.8 → **17.41 s** |
| Mount → Go To cache ready | 1.1 → 0.34 s | 36.2–52.0 → **0.43 s** | 0.3 → 0.61 s |

"Process start" is the launcher's stamp just before `Popen`, so it includes interpreter start and `import main` (0.78–0.95 s). Each startup is one run, so these are single samples where the profile gave ranges.

How the S3 startup CPU splits, from the cProfile startup run (27.3 s under the profiler):

- `calculate_eta` 2.68 s cumulative: `parse_route_string` 2.60 s, which includes `_expand_sid_star` 1.38 s and `get_star_data` 1.24 s.
- The one-time `_build_cifp_index` (`cifp.py:657`) takes 1.06 s of that, and `load_fixes` (NASR, once) 0.77 s.
- The rest is the bbox network wait.

S1's 4.83 s of bbox wall is one box over southern France (42.9–49.95 N, 0.86–7.72 E) that took 4.79 s. It is a single sample of server latency, not a code change.

## Steady-state refresh, default 15 s timer

S2 ran for 185 s and S1 for 185 s after mount; S3 ran for 303 s. A "wave" is a refresh in which the 60 s METAR cache had expired for most boxes (5 or more bbox requests).

| | S1 new | S2 old → new | S3 old → new |
|---|---|---|---|
| Refreshes started | 12 | 11 → 11 | 19 → 15 |
| Refreshes that updated the tables | 11 (the 12th was in flight at quit) | **0 → 11** | **0 → 14** (the 15th was in flight at quit) |
| Cancelled by the next one | 0 | 11 → 0 | 19 → 0 |
| Max concurrent analyses | 1 | 5 → 1 | 19 → 1 |
| Timer ticks skipped while a refresh ran | 0 | 24 | 107 |
| Trigger → tables updated, non-wave | 0.06–0.83 s (old manual: 2.5–8.9 s) | 12.2–18.5 → 0.15–0.67 s | 129.8–157.7 → 0.36–1.23 s |
| Trigger → tables updated, wave | none (S1's 5 boxes stagger, 0–2 per refresh) | 5.58 s, **19.14 s** | 12.99, **83.49**, 4.67, 7.51 s |
| Analysis main-thread CPU | 0.05–0.11 s (old 0.6–1.1) | 10.4–11.8 → 0.11–0.25 s | 88.9–98.0 → 0.17–0.33 s |
| `calculate_eta` per refresh | ≤ 7 ms (old 0.01–0.3 s) | ≤ 0.1 s → ≤ 12 ms | 90.1–99.8 s → 20–40 ms |
| Network calls / summed wall, non-wave | 0–3 / 0–1.13 s (old 11–15 / 2.9–9.6 s) | 13–20 / 5.0–10.8 → 0–2 / 0–0.38 s | 214–238 / 86–105 → 1–4 / 0.25–1.12 s |
| Network calls / summed wall, wave | n/a | 14–15 / 16.95–23.27 s (13 boxes) | 9–152 / 4.39–161.34 s |
| First refresh (15 s after mount) | 3 calls, 0.64 s (feed + 2 boxes for a new airport) | 1 call, 0.19 s (feed only) | 1 call, 0.25 s (feed only) |
| Table update, airports (`_update_efficiently`) | 0.3–3.0 ms (old 1–6) | 1–6 → 0.2–3.2 ms | 27 → 1.4–28.9 ms |
| Go To rebuild (`_build_goto_results`) | 2–3 ms (old 2) | 19–30 → 16–29 ms | 2 → 1–3 ms |

The weather cache works:

- Between waves, a refresh makes only the VATSIM feed call (the feed is cached for 15 s, so every other refresh) and 0–3 box requests.
- No per-station call happens anywhere except the race refresh below and one S3 station at 255 s (0.17 s).
- In S1 the boxes refetch on a staggered ~61 s cycle. A new airport at the first refresh pulled two boxes forward, and they stayed offset after that.

The waves are pure network wait: analysis CPU stays at 0.14–0.25 s in them. Their wall time is set by the slowest box, because a refresh lands only when every box has returned (`_fetch_bboxes`, `weather.py:1806-1821`, timeout 30 s).

- S2 at 140 s: one southwest-US box took 18.48 s; the other 12 took 0.14–1.23 s.
- S3 at 140 s: four boxes took 19.6–20.2 s, one of which returned HTTP 504.

## Event-loop health (50 ms heartbeat)

A stall is a heartbeat gap over 100 ms. "Total" is the sum of those gaps. A refresh window runs from the trigger to the end of that refresh's last table update.

| Window | Old: stalls / total / max / p99 | New: stalls / total / max / p99 |
|---|---|---|
| S1 refresh windows | 3–4 / 0.4–0.6 s / 171–248 ms / 98–218 ms per refresh | 0 / 0 s / 50–86 ms / 50–86 ms per refresh |
| S2 refresh windows | 53–65 / 8.8–10.3 s / 247–276 ms / 203–249 ms per refresh | 0–2 / 0–0.28 s / 67–279 ms per refresh; across all 11: 9 stalls, 1.34 s, pooled p99 105 ms |
| S3 refresh windows | 1 / 0.1 s / 143 ms / 52 ms (one 135 s refresh) | 0–3 / 0–0.70 s / 98–427 ms per refresh; across all 14: 19 stalls, 2.87 s, pooled p99 97 ms |
| S3 refresh at 140 s (83 s long) | n/a | 3 / 0.51 s / 216 ms / 53 ms |
| Startup, mount → Go To ready | S2: 0–18 / 0–2.6 s / 72–363 ms / 51–110 ms | S1, S2, S3: 0 / 0 / 50 ms / 50 ms (warm-up done in 0.34–0.61 s) |
| Whole run | not given | S1: 0 stalls (max 86 ms); S2: 24 stalls, 3.08 s, max 279 ms; S3: 20 stalls, 2.98 s, max 427 ms |

The GIL starvation the profile described is gone. In S3 the 20 stalls fall into three groups:

- **8 at a table rebuild (102–427 ms).** They end 0.02–0.06 s after an airports-table update. The watchdog found the main thread in Rich `cells.py:cell_len` under `Text.__rich_measure__`, which is DataTable's column measuring; S2's 279 ms stall was caught in `_data_table.py:1391 _update_column_widths`. The airports table rebuilt fully on 11 of 15 S3 refreshes ("Rebuilding table (count_changed=True)" in the run log), because the row count changes on most refreshes.
- **11 at the start of the analysis (104–178 ms).** They fall 0.1–0.5 s after the trigger, during the analysis's short pure-Python burst in the executor thread.
- **1 other.**

The UI does not freeze. The 83 s refresh caused only 3 stalls.

## Memory and quit

| | Old | New |
|---|---|---|
| Peak RSS (psutil `peak_wset`) | 275–306 MB with one refresh in flight; **505 MB** in S3 under the auto timer | S1 267 MB, S2 293 MB, S3 **305 MB**, all under the auto timer |
| Quit → `asyncio.run` returned → process end | S3: process still alive 60 s after the driver finished; had to be killed | S1 (refresh in flight): 0.41 / 0.85 s. S2 (idle): 0.01 / 0.43 s. S3 (a 6-box weather fetch in flight): **3.17 / 3.62 s** |

Quitting still waits for an analysis that is in flight. The S3 watchdog caught `Thread-1 (_do_shutdown)` joining the default executor while 5 bbox workers were in `ssl.read`.

So in the worst case, quitting waits for the rest of the in-flight refresh, which could be up to the ~83 s seen above. This is inferred from that mechanism; I did not quit during a long refresh.

The `NoMatches: '#status-bar'` error at teardown that the profile reported did not recur in any of the 4 runs.

## Top 3 hotspots by cProfile cumulative time, one S3 refresh

These come from an in-process copy of the S3 analysis outside the TUI. The profiled refresh came 75 s after startup, so most boxes were stale (18.58 s under cProfile, 107 box requests). On Python 3.13 the profile includes the bbox worker threads, so the cumulative times of threading waits exceed wall time.

| # | Function | file:line | Cumulative | Calls |
|---|---|---|---|---|
| 1 | `get_weather_for_airports_bbox` → `_fetch_bboxes` → `_fetch_weather_bbox_result` | `backend/data/weather.py:1686`, `:1794`, `:894` | 9.69 s | 1 / 1 / 107 |
| 2 | `get_wind_from_metar` → `get_metar` → `_fetch_metar_from_aviationweather` (sequential per-station fallback) | `backend/data/weather.py:633`, `:342`, `:259` | 8.33 s | 359 / 132 / 15, plus 3 `_fetch_metar_from_vatsim` (0.44 s) |
| 3 | `download_vatsim_data` | `backend/data/vatsim_api.py:29` | 0.22 s | 1 |

The only CPU left is `get_nearest_airport_if_on_ground` → `find_nearest` at 0.08 s and `calculate_eta` at 0.07 s.

For contrast, a refresh 15 s after startup with the cache fresh took 0.876 s: 0.30 s of bbox (5 requests), 0.22 s of VATSIM download and 0.08 s of nearest-airport search.

## Regressions and surprises

1. **Weather-cache expiry race (the main finding; measured and pinned to lines).** Rig: `.tmp/perf/runs/s3/drive.json`, viewed with `box_check.py` and `detail.py`.
   - The decision is made when the refresh starts: `get_weather_for_airports_bbox` calls `_bboxes_needing_fetch(..., datetime.now(...))` at `weather.py:1726`. The METARs are read later, when `analysis.py:349-358` calls `get_wind_from_metar` and `get_altimeter_setting` after the whole bbox phase. `_cached_metar` judges freshness at that moment (`weather.py:421-425`), and a stale entry goes to the per-station fetch at `weather.py:369`.
   - The central-European box (43.04–52.92 N, 9.89–19.76 E) was fetched at 93.6 s. When the 140 s refresh decided, it was 46 s old, so it was skipped. The bbox phase then took 29.3 s, and when the wind loop read LOWW at 170.8 s the entry was 77 s old. The OIIE box was 32 s old at the decision and 81 s old at the read.
   - Result: 31 stations were fetched one after another from aviationweather.gov, plus 4 from metar.vatsim.net, between 170.8 and 223.4 s (`get_wind_from_metar` 51.1 s). The refresh took 83.5 s.
   - The 1.1 s spacing between those calls is `_wait_for_backoff` (`weather.py:112-116`). The Indonesia box's HTTP 504 counted as a rate-limit error (`weather.py:44`), which set a 1.0 s backoff for 60 s.
   - The cProfile refresh at +75 s shows the same pattern with no slow server: 15 station fetches, 8.3 s.
   - Fix (one line): judge freshness for the wind and altimeter reads against the time the bbox decision was made, or refetch boxes whose entries will expire within the fetch timeout.
2. **An empty station answer is fetched twice per refresh** when the airport has a stale entry. `_cached_metar` lets a stale entry win over a fresh negative (`weather.py:421-427`), so the altimeter read fetches again after the wind read. The failing test below shows 4 fetches for 2 airports. Fix (one line): consult the negative before refetching an airport whose entry is stale.
3. **The slowest box gates every wave.** Single box requests took 18.5 s in S2 and 19.6–20.2 s in S3, and the tables do not land until they return. Fix (one line): land the tables with the previous weather and fill in wind and altimeter when the boxes return.
4. **The largest UI stall is now a DataTable rebuild** (up to 427 ms in S3, against the profile's 143–195 ms in the same code). It happens because the row count changes on most S3 refreshes. `ui/tables.py` is unchanged by the six fixes. Fix (one line): update rows in place when only the count changes, instead of rebuilding the whole table.
5. **S2 had a stall cluster outside every refresh window.** 15 stalls of 100–161 ms came at 39–53 s with no tracked work running, and the watchdog (150 ms threshold) caught none of them. Unattributed. Other worktrees on this machine may have been busy; I did not measure that.
6. **S1 shows 0 grouping rows; the profile reported 16.** The six fixes do not explain this: `backend/core/groupings.py` and `data/` are unchanged between `1f50951` and `3fa9c0a`, and none of the 296 loaded groupings contains a GB, DE, NL or FR airport (`.tmp/perf/check_groupings.py`). Most likely the profile's run environment differed. Unresolved.

Nothing measured is slower than before because of the code. The only figures worse than the profile are S1's startup bbox wall (one slow box) and the S3 maximum UI stall (item 4).

## Making METAR fetching async (your mid-task question)

Measured, async fetching would shorten refreshes but would not make the UI more responsive:

- **The UI is already not blocked.** The METAR fetches run on the refresh's executor thread. During the 83 s refresh the event loop had only 3 stalls (0.51 s in total, max 216 ms).
- **The bbox phase is already parallel** (5 workers). Its wall time is the slowest box, 0.1–0.4 s outside a wave and 2.7–29 s in one, depending on aviationweather.gov.
- **The sequential part is the per-station fallback.** Measured, it runs only in the race above: 31 stations for 52 s in S3, and 15 stations for 8.3 s in the cProfile run.
  - Making it concurrent would cut that to roughly the slowest single call (5 s timeout).
  - But the 1.1 s spacing in that run is the deliberate backoff after a 504, and parallel requests would get around it.
  - Fixing the race (item 1) removes those calls altogether, which is the bigger and safer win.
- **The async change that would help most is item 3:** don't make the table update wait for weather. That separates the refresh period from aviationweather.gov's latency, which was up to 20 s per box in these runs.

## Failing test for item 1

It belongs in `tests/test_weather_cache.py` and uses that module's `net`, `clock`, `_age_entry` and `AIRPORTS`. I ran a scratch copy (`.tmp/perf/test_race.py`, which imports those fixtures) against 3fa9c0a: `uv run --group dev pytest .tmp/perf/test_race.py -q -p no:cacheprovider` → **1 failed**, `assert 4 == 0` (KJFK and KBOS were each fetched once for wind and once for altimeter).

It fails because the east box is skipped at 50 s and its entries expire during the simulated 20 s west fetch. It is not tautological: it asserts the outcome (no per-station fetch for airports that were fresh when the refresh began), and any fix that removes the race makes it pass.

```python
def test_slow_bbox_phase_does_not_expire_boxes_it_skipped(net: FakeNet, clock: Clock) -> None:
    airports = ["KJFK", "KBOS", "KLAX", "KSFO"]
    weather.get_weather_for_airports_bbox(airports, AIRPORTS)
    for icao in ("KJFK", "KBOS"):
        _age_entry(icao, 50)
    for icao in ("KLAX", "KSFO"):
        _age_entry(icao, 61)

    def slow_bbox(url: str) -> bytes:
        clock.advance(20)
        return net._bbox_from_stations(url)

    net.bbox = slow_bbox
    weather.get_weather_for_airports_bbox(airports, AIRPORTS)
    assert len(net.bbox_urls()) == 3

    for icao in airports:
        weather.get_wind_from_metar(icao)
        weather.get_altimeter_setting(icao)

    assert net.count("ids=") == 0
```

## Hypotheses ruled out

- **The per-station calls in the 140 s refresh came from boxes truncated at 400 entries.** Ruled out: no box covering LOWW, EDDM, LKPR, LHBP, EPWR, LIPZ or OIIE was requested at all in 140–170 s (`box_check.py s3 140 170`). Those boxes were fetched at 93.6 s and 108.3 s and skipped as fresh.
- **The 1.1 s spacing between station calls was request latency.** Ruled out: the requests themselves took 0.11–2.17 s (5.05–5.07 s for the two timeouts). The gaps match the 1.0 s backoff that the HTTP 504 at 146.7 + 20.1 s triggered.
- **The S1 grouping rows were lost to the allowlist-set fix.** Ruled out: the groupings code and data are unchanged, and no grouping contains an S1 airport.

## Tree state

- `git status --short`: empty in `X:/dev/vatsim_control_recs.wt/perf-remeasure` and in `X:/dev/vatsim_control_recs`. No tracked file was edited.
- Kept, as the brief asked (all under the gitignored `X:/dev/vatsim_control_recs.wt/perf-remeasure/.tmp/perf/`):
  - Scripts: `run.py`, `drive.py`, `prof_s3.py`, `summarize.py`, `ranges.py`, `detail.py`, `box_check.py`, `projtop.py`, `check_groupings.py`, `test_race.py`.
  - Summaries and logs: `s1/s2/s3.summary.txt`, `s3.analyses.txt`, `s3.net140.txt`, `s3.stalls.txt`, `test_race.log`.
  - Per run, in `runs/{s1,s2,s3,prof_s3}/`: `drive.json` (prof_s3 has the `.prof` and `.txt` profiles instead), `launcher.json`, `child.log`, and the app's debug log under `appdata/VATSIMControlRecs/logs/`.
- Removed (Recycle Bin): the copied cache template and per-run cache copies (about 1.9 GB), the smoke run, and `__pycache__`.
- `uv sync` created the worktree's `.venv`, which is gitignored; I left it in place.
- Every process I launched has exited, and no other runtime was touched.

## Lessons for the project's docs

These belong in `docs/plans/perf-profile.md` under "Context" (Tools):

- Under uv on Windows, the venv's `python.exe` is a launcher shim (4.6 MB working set), so to measure RSS from outside, poll the child's children too.
- On Python 3.13, cProfile includes worker threads, so cumulative times of waits exceed wall time.
- Record per-box request durations: wave refreshes swing from 4.7 s to 83 s on aviationweather.gov latency alone.
- A rig that re-measures this should name its refreshes "wave" and "non-wave"; a single average hides both.
