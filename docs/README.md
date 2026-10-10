# Docs

VATSIM Control Recommendations is a terminal app (Textual) that analyzes live VATSIM flight data and recommends controller staffing per airport, plus a daemon that publishes weather-briefing web pages from the same backend.

## Start here

For any code change, read [`ARCHITECTURE.md`](ARCHITECTURE.md) first: its Task Index maps a task to the files to read, and it lists the layers, the integration footguns and the test locations.

## Docs

- [`ARCHITECTURE.md`](ARCHITECTURE.md): the entry point for code changes: Task Index, layers, data flow, integration footguns, test locations.
- [`performance.md`](performance.md): GIL starvation and its design rules, the weather API's 400-entry cap, the per-refresh cost structure and how to profile a refresh.
- [`atis-filtering-and-runway-extraction.md`](atis-filtering-and-runway-extraction.md): VATSIM and D-ATIS sources, METAR stripping and runway extraction.
- [`research/`](research/): research notes, one dated file each.
- [`plans/MAIN.md`](plans/MAIN.md): the plan, the repo's single index of open work.
- [`../USER_GUIDE.md`](../USER_GUIDE.md): end-user guide to the app.
- [`../CLAUDE.md`](../CLAUDE.md): setup, command-line options, conventions, data files, keyboard shortcuts and the weather daemon workflow.
- [`../README.md`](../README.md): install, usage and the option list.

## Glossary

- **AIRAC**: the 28-day aeronautical data cycle; CIFP and NASR data are downloaded and cached once per cycle (`backend/data/cifp.py`).
- **ARTCC**: an air route traffic control center; the app builds automatic groupings from them.
- **`as_of`**: the one freshness time a refresh or weather pass takes, passed to every METAR cache read so a slow bbox phase cannot expire entries it skipped.
- **bbox / box**: one weather bounding-box request to aviationweather.gov; `calculate_airport_bboxes` makes one per occupied grid cell of tracked airports.
- **cap row**: the last, dim and disabled row of the Go To list, "showing 100 of N — type to narrow", shown when more than 100 rows match.
- **CIFP**: Coded Instrument Flight Procedures, the FAA file the SID, STAR and approach lookups read (`backend/data/cifp.py`).
- **clean empty / negative cache**: a cached "no METAR" answer for an airport, kept 60 seconds and stored only when both METAR sources (aviationweather.gov and metar.vatsim.net) answered with no report; failures are never cached.
- **collapsed row**: in Go To's pick several, the one row "▸ N airports (→ to expand)" that stands for a picked grouping's members until `→` expands them.
- **CONUS**: the contiguous United States; a CONUS-sized weather box hits the API's 400-entry cap.
- **D-ATIS**: digital ATIS, the airport information text fetched from VATSIM and real-world sources.
- **GIL**: Python's global interpreter lock; CPU-bound Python on a worker thread starves the UI thread (see `performance.md`).
- **MEA**: minimum enroute altitude; the flight info screen warns when an IFR flight is below it on a segment of its filed route.
- **my groupings** (★ grouping): a grouping the user saved in `favorites.json`; the code calls it a favorite.
- **NASR**: the FAA's National Airspace System Resources data, the source of navaids and fixes for route parsing (`backend/data/navaids.py`).
- **ratchet**: the `C901` complexity ignores per file in `ruff.toml`; a file's entry is removed when its over-limit functions are split, so the list only shrinks.
- **SCANP / SPACP / SLAMP / SUSAP**: CIFP customer areas; SUSAP is the continental US (K idents), SCANP is Alaska's, SPACP Hawaii's and the Pacific territories', SLAMP Puerto Rico's and the US Virgin Islands'.
- **SID / STAR**: standard instrument departure and standard terminal arrival routes, looked up from CIFP when parsing a filed route for the ETA.
- **skip key**: the weather daemon's weather hash and code version joined by `:` (`compute_skip_key`); when it matches the stored key, the tile stage is skipped.
- **split-flap**: the animated airport-board style of the tables, where cells flip through a character set to the new value (`widgets/split_flap_datatable.py`).
- **stage**: one generation step of a weather daemon run: `weather`, `briefings`, `tiles` or `index`.
- **tile version**: the run start time of the last completed tile set, stored in `tiles/version.txt` and used as the index page's tile `?v=`.
- **traffic columns**: the airports table's departure, arrival and ETA columns, as opposed to the WIND and ALT columns.
- **wave**: a refresh in which most weather boxes have expired, so several box requests go out and the refresh lasts as long as the slowest.
- **weather pass**: the background fetch (`fetch_weather_for_airports`, on the "weather-pass" thread) that fills WIND and ALT box by box after each traffic update; one runs at a time.
