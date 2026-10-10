# VATSIM Control Recommendations — architecture

A terminal app (Textual) that analyzes live VATSIM flight data and shows controller staffing recommendations per airport and grouping, plus a weather-briefing daemon that publishes web pages from the same backend.

Layers: `backend/` (data fetching and analysis, no UI), `ui/` and `widgets/` (Textual screens and the split-flap table), `airport_disambiguator/` (airport display names), `common/` (paths, logging), `scripts/` (data generators and the weather daemon); `main.py` (run with `uv run main.py`) parses arguments, expands the tracked airports and starts the app; dependencies are declared in `pyproject.toml` and locked in `uv.lock`.

Tracking happens per airport; groupings only organize the display. Tests live in `tests/` (see Test locations).

## Task Index

| Task | Files, in order | Deep doc |
|---|---|---|
| Change ETA, arrival or departure categorization | `backend/core/flights.py` → `backend/core/calculations.py` → `backend/core/route_distance.py` → `backend/data/navaids.py` → `backend/data/cifp.py` → `backend/core/analysis.py` | none |
| Change SID/STAR lookup, CIFP caching or the route cache behind ETA | `backend/core/calculations.py` (`calculate_eta`) → `backend/core/route_distance.py` (`_get_parsed_route`, `clear_route_cache`) → `backend/data/navaids.py` (`parse_route_string`, `_expand_sid_star`) → `backend/data/cifp.py` (the index, `ensure_cifp_data`) → `tests/test_cifp_index.py` | none |
| Add a column to the airport or grouping table | `backend/core/models.py` → `backend/core/analysis.py` → `ui/config.py` → `ui/tables.py` | none |
| Add or change a modal screen | `ui/modals/<name>.py` → `ui/modals/__init__.py` → `ui/app.py` (binding and action) → `ui/modals/command_palette.py` → `ui/modals/help_modal.py` → `tests/` | none |
| Change ATIS parsing, approach or runway extraction | `backend/data/atis_filter.py` → `backend/data/datis_api.py` → `backend/data/vatsim_api.py` → `ui/modals/notification_manager.py` | [`atis-filtering-and-runway-extraction.md`](atis-filtering-and-runway-extraction.md) |
| Change METAR/TAF parsing | `backend/data/weather_parsing.py` → `backend/briefing/taf_parsing.py` | none |
| Change METAR/bbox weather fetching or caching | `backend/data/weather.py` (`get_metar`, `_cached_metar`, `get_weather_for_airports_bbox`, `fetch_weather_bbox`) → `backend/cache/manager.py` (`_METAR_DATA_CACHE`, the negative cache, lock, persistence) → `backend/core/analysis.py` (`fetch_weather_for_airports`, the wind and altimeter reads, with `as_of`) → `scripts/weather_daemon/generator.py` (shares `fetch_weather_bbox` and `get_metar_batch`) → `tests/test_weather_cache.py` | none |
| Change when WIND/ALT or other weather reaches the airports table, or how a refresh waits on weather | `ui/app.py` (`_apply_refresh_data`, `_weather_pass_worker`, `_run_weather_pass`, `_apply_weather_batch`) → `backend/core/analysis.py` (`fetch_weather_for_airports`) → `backend/data/weather.py` (`get_weather_for_airports_bbox`, `_fetch_bboxes`, `_store_bbox_result`, `cached_wind_altimeter`, `_usable_entry`) → `ui/tables.py` (`populate`, `_update_efficiently`) → `tests/test_refresh_weather.py`, `tests/test_refresh_overlap.py`, `tests/test_weather_cache.py`, `tests/test_table_refresh.py` | none |
| Change weather briefings (TUI or web) | `backend/briefing/area_clustering.py` → `ui/modals/weather_briefing.py` → `scripts/weather_daemon/generator.py` → `scripts/weather_daemon/index_generator.py` | none |
| Change the weather pages' static assets (vendored Leaflet) | `scripts/weather_daemon/assets/leaflet/<version>/` (and its `README.md`) → `scripts/weather_daemon/index_generator.py` (`LEAFLET_VERSION`, `copy_leaflet_assets`) → `scripts/weather_daemon/service/nginx-weather.conf` (long cache for `/weather/assets/`) → `.pre-commit-config.yaml` (hooks that skip the vendored files) | `tests/test_weather_index_assets.py` |
| Skip or change what a daemon run regenerates when weather is unchanged | `scripts/weather_daemon/generator.py` (`compute_skip_key`, `should_skip_tile_stage`, `generate`) → `scripts/weather_daemon/config.py` (`skip_if_unchanged`) → `scripts/weather_daemon/cli.py` (`--force`, `--stages`) → `scripts/weather_daemon/tile_generator.py` (`write_tile_version`) → `tests/test_weather_stage_order.py` → `tests/test_weather_tile_version.py` | none |
| Change groupings or favorites | `backend/core/groupings.py` → `common/paths.py` → `ui/modals/goto_modal.py` → `ui/modals/save_grouping.py` | none |
| Add or change a dependency | `pyproject.toml` → `uv.lock` (`uv lock`) → `vatsim_control_recs.spec` (PyInstaller collects) → `scripts/weather_daemon/service/sync-deps.sh` | none |
| Add or change a command-line option | `main.py` (`build_arg_parser`, `resolve_airport_allowlist`) → `backend/config/constants.py` → `ui/app.py` | none |
| Fix an airport display name | `data/airport_names.csv` (edit directly) → `scripts/generate_airport_names.py` → `airport_disambiguator/disambiguator.py` | none |
| Change diversion or VFR-alternative search | `backend/core/diversions.py` → `backend/core/spatial.py` → `backend/core/aircraft_performance.py` → `ui/modals/diversion_modal.py` / `ui/modals/vfr_alternatives.py` | none |
| Change route weather or the MEA warning | `backend/core/route.py` → `backend/data/navaids.py` → `backend/data/cifp.py` → `ui/modals/route_weather.py` → `ui/modals/flight_info.py` | none |
| Change split-flap animation or flap character sets | `ui/config.py` → `widgets/split_flap_datatable.py` → `ui/tables.py` | none |
| Change how the airport, grouping or flight-board tables update on refresh, or restore cursor and scroll | `ui/app.py` (`_apply_refresh_data`, `_populate_tables_and_wait`, `_restore_table_position`) → `ui/tables.py` (`TableManager.populate`, `_update_efficiently`, `_remove_tail_rows`, `_drop_tail_rows`) → `widgets/split_flap_datatable.py` (`blank_rows`, `truncate_rows`, `recompute_column_widths`) → `tests/test_table_refresh.py` | none |
| Change historical statistics | `backend/data/statsim_api.py` → `ui/modals/historical_stats.py` | none |
| Change the weather daemon or its deployment | `scripts/weather_daemon/cli.py` → `scripts/weather_daemon/generator.py` → `scripts/weather_daemon/config.py` → `scripts/weather_daemon/service/` | none |
| Change how nginx serves /weather | `scripts/weather_daemon/service/nginx-weather.conf` → `scripts/weather_daemon/service/install-nginx.sh` → `scripts/weather_daemon/service/nginx_include.py` → `scripts/weather_daemon/service/setup.sh`, `QuickDeploy.ps1`, `Deploy.ps1`, `deploy.sh` → `tests/test_nginx_include.py` | none |
| Read a value from the repo-root `.env` | `common/env_file.py` (`read_env_value`) → `backend/data/statsim_api.py` (`_resolve_api_key`) → `scripts/weather_daemon/env_file.py` (`load_carto_basemap_key`) → `scripts/weather_daemon/service/_Env.ps1` (the PowerShell reader of `WEATHER_SERVER_IP`) → `conftest.py` → `tests/test_env_file.py`, `tests/test_weather_basemap_key.py` | none |
| Change logging or log-file start-up | `common/logger.py` (`setup_file_logging`, the pre-setup buffer) → `common/paths.py` (`get_user_logs_dir`) → `main.py` (`main`) and the other entry points that call it (`scripts/weather_daemon/cli.py`, `scripts/weather_daemon/generator.py`, `scripts/generate_airport_names.py`, `scripts/precalculate_airport_spatial_data.py`, `airport_disambiguator/__main__.py`) → `conftest.py` → `tests/test_logger.py`, `tests/test_isolation.py` | none |
| Profile or speed up a refresh | `ui/app.py` (`auto_refresh_callback`, `refresh_worker`, `_weather_pass_worker`) → `backend/core/analysis.py` → `backend/data/weather.py` (`get_weather_for_airports_bbox`) → `backend/data/cifp.py` (`_build_cifp_index`) → `backend/core/spatial.py` (`find_nearest`) | [`performance.md`](performance.md) |
| Regenerate reference data | `scripts/generate_preset_groupings.py`, `scripts/generate_simaware_boundaries.py`, `scripts/precalculate_airport_spatial_data.py` → the matching file under `data/` | none |

## Layers

- **`backend/core/`**: owns the analysis pipeline (`analysis.py` `analyze_flights_data()`), flight, ETA, controller, grouping, route, spatial and diversion logic, and the `AirportStats` / `GroupingStats` models. References `backend/data`, `backend/cache`, `backend/config`, `common.paths` and `airport_disambiguator`; never `ui`, except the lazy `from ui import debug_logger` in `backend/core/flights.py` (line 208).
- **`backend/data/`**: owns everything fetched or loaded from outside: VATSIM, D-ATIS, StatsIM, weather, ATIS parsing, navaids, CIFP, runways and the unified airport loader (`loaders.py`). References `backend/cache`, `backend/config`, `common.paths`, and `backend/core/calculations.py` (`weather.py` only, for distance and bearing).
- **`backend/briefing/`**: owns weather-briefing helpers (`area_clustering.py`, `taf_parsing.py`). References `backend/core` and `backend/data`.
- **`backend/cache/`** (`manager.py`): owns the wind, METAR and TAF caches, the aircraft-speeds and ARTCC-grouping caches, and the weather cache save/load. References `backend/config` and `common.paths`.
- **`backend/config/`** (`constants.py`): owns cache TTLs and the global `WIND_SOURCE`, which `main.py` sets from `--wind-source`. References nothing.
- **`airport_disambiguator/`**: owns ICAO-to-display-name resolution (`disambiguator.py` public API, `disambiguation_engine.py`, `entity_extractor.py`, `name_processor.py`, `data_manager.py`); checks `data/airport_names.csv` first, then falls back to spaCy. References `backend/data`; never `ui`.
- **`ui/`**: owns the `VATSIMControlApp` (`app.py`, which refreshes on a `set_interval` timer, 15 seconds by default via `--refresh-interval`), table config and management (`tables.py`, `config.py`) and the screens in `ui/modals/`. References `backend`, `widgets`, `common`.
- **`widgets/`** (`split_flap_datatable.py`): owns the animated DataTable. References `ui.debug_logger` only.
- **`common/`**: owns path resolution (`paths.py`), logging (`logger.py`) and the one dotenv reader (`env_file.py`, `read_env_value`). References only itself. Importing a module opens no log file: records logged before an entry point calls `setup_file_logging()` are buffered and flushed to the file when it runs.
- **`scripts/`**: owns the generators for `data/` files and `scripts/weather_daemon/` (staged `weather`, `briefings`, `tiles`, `index` generation, systemd unit and timer, nginx config, PowerShell deployment scripts). References `backend`, `airport_disambiguator` and `common` (the weather daemon uses `common.logger` and `common.env_file`); nothing references `scripts`.

## Data flow

1. `main.py` parses arguments (`build_arg_parser`), loads the unified airport data with `load_unified_airport_data` only when countries or groupings must be expanded, expands them to the tracked airport list (`resolve_airport_allowlist`), then calls `analyze_flights_data()` and hands the result to the app, which shows traffic with blank WIND/ALT until the weather pass fills them.
2. `analyze_flights_data()` in `backend/core/analysis.py` loads the unified airport data itself if `main.py` did not pass it (`data/raw/APT_BASE.csv`, `data/raw/airports.json`, `data/raw/iata-icao.csv`), downloads VATSIM data (`download_vatsim_data`), extracts staffed positions, filters flights to the tracked airports and splits them into departures and arrivals by ground position and ETA.
3. It fetches no weather: `AirportStats` come back with empty `wind` and `altimeter`, so the first paint never waits on the network.

   Display names come from one `get_full_names_batch` call on the disambiguator. It returns `AirportStats` and `GroupingStats` lists, sorted.
4. `ui/app.py` fills the DataTables from those lists and repeats the analysis on the refresh timer, one refresh at a time: the timer skips a tick while one runs, so the period is the longer of the interval and the analysis time.

   After the traffic columns update, the app runs a weather pass on a daemon thread named "weather-pass", one at a time: `fetch_weather_for_airports` (`backend/core/analysis.py`) calls `get_weather_for_airports_bbox`, which fills the METAR cache per box and requests a box only when one of its airports has neither a fresh (60 seconds) METAR nor a fresh "no METAR" entry. Boxes still fresh are delivered first in one call, the others as each returns, and `_apply_weather_batch` patches those airports' WIND/ALT cells on the UI thread.

   An airport keeps its previous values until its box returns, a failed box leaves them, and an airport a successful box omitted goes blank. A box at the 400-entry cap and the airports of failed boxes get one per-station fallback after all boxes. Only clean empties are cached as negatives, and a stale METAR keeps showing while a fresh negative stands. Wind (`get_wind_from_metar`, skipped under `--hide-wind`) and altimeter (`get_altimeter_setting`) are read back from that cache. A refresh landing mid-pass queues exactly one more pass; nothing is cancelled.

   An app-triggered request during a run, such as closing the Tracked Airports manager after a change, queues exactly one follow-up, and a failed analysis is logged as an ERROR while the app keeps running. Groupings are expanded to airports for tracking at startup but stay as groupings in the Groupings tab.
5. Modal screens fetch their own data on demand through `backend` (for example `get_metar_batch` and `get_taf_batch`, run in an executor) and pre-fill the airport from the selected table row, the open flight board or the open flight info screen.

## Integration Footguns

- **Add or rename a keyboard shortcut** → the binding in `ui/app.py` `BINDINGS`, the `COMMANDS` list in `ui/modals/command_palette.py`, the text in `ui/modals/help_modal.py`, and the shortcut tables in `README.md`, `USER_GUIDE.md` and `CLAUDE.md` are separate copies; nothing enforces agreement.
- **Change grouping resolution** → `resolve_grouping_recursively()` in `backend/core/groupings.py` is used by `main.py`, `backend/core/analysis.py`, `ui/modals/goto_modal.py`, `ui/app.py` (`open_grouping_board`) and `scripts/weather_daemon/generator.py`.
- **Change how a grouping's flight board opens** → `VATSIMControlApp.open_grouping_board` in `ui/app.py` serves both the Groupings tab and Go To's `_open_grouping`, applying a ★ grouping's saved dep/arr filters; Go To's multi-pick board (`goto_modal.py`) builds its own filters for the current picks.
- **Add a grouping source** → priority is ARTCC auto-groupings, `data/preset_groupings/`, `data/custom_groupings.json`, then the user's `favorites.json` in the user data directory (highest, `get_user_data_dir()`); loading spans `common/paths.py` (`load_merged_groupings`, `get_user_favorites_file`) and `backend/core/groupings.py`; favorites (multi-airport selections with per-airport departure/arrival filters) are written by `ui/modals/goto_modal.py` and `ui/modals/save_grouping.py`.
- **Change wind source handling** → `WIND_SOURCE` in `backend/config/constants.py` is a module global mutated by `main.py`, and read by `ui/modals/wind_info.py`, `ui/modals/flight_board.py` and `backend/data/weather.py`; import the module, never the value.
- **Add a table column** → `ui/tables.py` builds `TableConfig` from `ui/config.py` `ColumnConfig`, and the animated cells need a flap character set in `ui/config.py` (`ETA_FLAP_CHARS`, `ICAO_FLAP_CHARS`); `widgets/split_flap_datatable.py` `AnimatedCell` holds the per-cell animation state.
- **Remove or reorder table rows** → `SplitFlapDataTable` keys its per-cell state (`animated_cells` and the fading set) by row index, so every row removal or reorder goes through `truncate_rows`. `recompute_column_widths` relies on Textual 8.2.8 private members (named in its docstring), so a Textual upgrade must run the width tests in `tests/test_table_refresh.py`.
- **Change a shared data file** (`data/airport_names.csv`, `data/aircraft_data.csv`, `data/airport_spatial_cache.json`, `data/preset_groupings/`, `data/simaware_boundaries/`) → the file is generated by a script in `scripts/`; regenerating overwrites unless the script preserves edits (`generate_airport_names.py` does).
- **Change weather caching or `fetch_weather_bbox`** → the daemon (`scripts/weather_daemon/generator.py`) and the TUI share `get_metar` and `fetch_weather_bbox`, so a change to their caching or return value reaches both.

  The "no METAR" negative cache (`get_metar_negative_cache` in `backend/cache/manager.py`) holds only clean empties (both aviationweather.gov and metar.vatsim.net answered with no METAR) for 60 seconds and is never persisted by `save_weather_cache`; failures (timeouts, 429, 5xx) are never cached.

  A reader of the METAR cache inside the refresh path (`get_metar`, `get_wind_from_metar`, `get_altimeter_setting`) must pass the weather pass's one `as_of` (taken in `ui/app.py` `_run_weather_pass`), or the per-station refetch race returns.

  A bbox response under 400 entries (`BBOX_MAX_ENTRIES`, the API cap) records negatives for the requested airports it omitted.
- **Change the `backend` package surface** → `scripts/weather_daemon/generator.py` imports through `backend/__init__.py` and `backend.briefing`, and runs on a Linux server without the TUI, so keep `ui` imports out of the modules it uses.
- **Change the weather tile stage** → the systemd unit kills a run at 300 s (`TimeoutStartSec`) on a 1-vCPU droplet, after ~30 s of briefings or ~180 s of a fresh weather fetch; time a tile-stage change against that.
- **Change a dependency** → edit `pyproject.toml` (exact `==` pins) and run `uv lock`, never pip: CI (`.github/workflows/build-release.yml`) syncs with `uv sync --locked`, and the weather-daemon server installs only through `scripts/weather_daemon/service/sync-deps.sh` (`uv sync --frozen` with a Python install dir the `www-data` daemon user can read). `en_core_web_sm` is pinned to its wheel URL in `pyproject.toml` and must match the spaCy minor version.
- **Change CIFP lookups or their caches** → `get_dp_data`, `get_star_data` and `get_approaches_for_airport` in `backend/data/cifp.py` answer from an in-process index built lazily in one pass over the CIFP file; the index is rebuilt when `ensure_cifp_data` returns another file, and `clear_cifp_cache()` drops it. `_get_parsed_route` in `backend/core/route_distance.py` caches routes with SID/STAR results embedded, so every index rebuild must clear it (`cifp.py` calls `clear_route_cache`).
- **Change the nginx config for /weather** → a location added to `scripts/weather_daemon/service/nginx-weather.conf` reaches the server only through `install-nginx.sh` on the next deploy, and `nginx.conf` includes every file in `sites-enabled/`, so nothing but `leftos.dev` may be written there (backups go to `/etc/nginx/weather-backup/`).
- **Lint and formatting** → `ruff.toml` and `.pre-commit-config.yaml` (run with `prek run`) gate commits; the tests run in CI only, in the "Run tests" step of `.github/workflows/build-release.yml` before the PyInstaller build.

## Test locations

`tests/`, run with `uv run --group dev pytest -q` (the `dev` dependency group; pytest settings are in `pyproject.toml`). The tests are headless Textual Pilot tests on fixture data and never use the network.

- `conftest.py` (repo root): points `LOCALAPPDATA`, `XDG_DATA_HOME` and `HOME` at a session temp dir before any project import, so caches, favorites and any log file a test starts through `setup_file_logging()` land in the temp dir, not the user's; it also blanks `STATSIM_API_KEY`, the only key the app reads from `.env` and only when the environment lacks it, so tests never read `.env` through statsim. `tests/test_isolation.py` checks this.
- `tests/conftest.py`: the fixture that mounts `GoToScreen` on fixture airports, groupings and favorites.
- `tests/test_goto_edit_filters.py`: saved per-airport filters when editing a favorite.
- `tests/test_goto_r1.py`: Go To modal titles, hints, focus moves, and warning and error toasts.
- `tests/test_goto_r5.py`: Go To section headers, the cap row, collapsed and expanded grouping rows, the "Picked:" line, hint drop order, and highlight skipping.
- `tests/test_cifp_index.py`: CIFP SID, STAR and approach lookups on a small fixture file: matches, the single file pass, and the index and route-cache rebuild when another file is returned.
- `tests/test_weather_cache.py`: bbox freshness, "no METAR" negatives and the failures that are never cached.
- `tests/test_refresh_weather.py`: the weather pass: traffic columns before weather, previous WIND/ALT kept until a box returns, per-box patches, failed versus omitted airports, fresh boxes first, a mid-pass refresh queuing one rerun, two-stage startup, the capped-box fallback, quit cancelling queued boxes, and patch failures logged.
- `tests/test_tracked_airports.py`: Space and Enter pick a row in the Tracked Airports manager with the cursor kept in place, and `S` saves the picks (or the whole list when none).
- `tests/test_weather_briefing_callback.py`: the weather-briefing picker routes a flight, a single airport and a grouping to their briefings.
- `tests/test_grouping_board_filters.py`: a ★ grouping's saved dep/arr filters apply when its board opens from the Groupings tab or Go To; a preset opens unfiltered.
- `tests/test_cli_options.py`: the CLI parser rejects the removed progressive flags.
- `tests/test_table_refresh.py`: a refresh that changes the row count updates the tables in place (rows keep their slots, extra rows flap to blank then go, new rows append), the cursor and scroll stay on the same row, and column widths grow and shrink with the values.
- `tests/test_refresh_overlap.py`: refreshes never overlap or cancel, the one queued follow-up, a failed analysis keeping the app running, and the status bar's "Refreshing…" text.
- `tests/test_weather_stage_order.py`: the index page is written even when the tile stage dies, `tiles/version.txt` is written after a completed tile stage and left unchanged after a killed one, and the tile stage is skipped (briefings and index still run) when the weather and daemon code match the stored key.
- `tests/test_weather_tile_version.py`: the index page versions its tiles by the last completed tile set (falling back to the run start), and a tile write is atomic.
- `tests/test_weather_basemap_key.py`: the CARTO key from the environment or the `.env` file goes into the basemap URL, the environment wins, and a missing or unsafe key is omitted with a log message.
- `tests/test_weather_index_assets.py`: the index page loads Leaflet from its own copied tree and no remote scripts or stylesheets.
- `tests/test_weather_tile_polygon_reach.py`: a weather tile ray-casts only the boundary polygons whose box reaches it, with output unchanged.
- `tests/test_nginx_include.py`: `nginx_include.py` removes the legacy inline weather blocks and ensures one include line in the `leftos.dev` TLS server block only, idempotently, and exits 1 unless exactly one server block matches.
- `tests/test_env_file.py`: `read_env_value` cases (quotes, inline comments, the unreadable-file warning) and the statsim key rules (the environment wins even when empty, `.env` only when absent, nothing written to the environment).

CI runs the suite in the "Run tests" step of `.github/workflows/build-release.yml`, before the PyInstaller build. Outside `tests/`, behavior is checked by running `uv run main.py` against live data or `data/test-vatsim-data.json` (a sample VATSIM response), and the weather daemon by `scripts/weather_daemon/service/LocalTest.ps1`.

## Deep docs

- [`atis-filtering-and-runway-extraction.md`](atis-filtering-and-runway-extraction.md): VATSIM and D-ATIS sources, METAR stripping and runway extraction.
- [`performance.md`](performance.md): GIL starvation and its design rules, the weather API's 400-entry cap, the per-refresh cost structure and how to profile a refresh.
- [`../CLAUDE.md`](../CLAUDE.md): setup, command-line options, conventions, data files, keyboard shortcuts and the weather daemon workflow.
- [`../USER_GUIDE.md`](../USER_GUIDE.md): end-user guide to the app.
