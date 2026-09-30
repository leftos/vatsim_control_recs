# VATSIM Control Recommendations — architecture

A terminal app (Textual) that analyzes live VATSIM flight data and shows controller staffing recommendations per airport and grouping, plus a weather-briefing daemon that publishes web pages from the same backend. Layers: `backend/` (data fetching and analysis, no UI), `ui/` and `widgets/` (Textual screens and the split-flap table), `airport_disambiguator/` (airport display names), `common/` (paths, logging), `scripts/` (data generators and the weather daemon); `main.py` bootstraps the venv, parses arguments, expands the tracked airports and starts the app. Tracking happens per airport; groupings only organize the display. There is no test suite in the repo.

## Task Index

| Task | Files, in order | Deep doc |
|---|---|---|
| Change ETA, arrival or departure categorization | `backend/core/flights.py` → `backend/core/calculations.py` → `backend/core/route_distance.py` → `backend/core/analysis.py` | none |
| Add a column to the airport or grouping table | `backend/core/models.py` → `backend/core/analysis.py` → `ui/config.py` → `ui/tables.py` | none |
| Add or change a modal screen | `ui/modals/<name>.py` → `ui/modals/__init__.py` → `ui/app.py` (binding and action) → `ui/modals/command_palette.py` → `ui/modals/help_modal.py` | none |
| Change ATIS parsing, approach or runway extraction | `backend/data/atis_filter.py` → `backend/data/datis_api.py` → `backend/data/vatsim_api.py` → `ui/modals/notification_manager.py` | [`atis-filtering-and-runway-extraction.md`](atis-filtering-and-runway-extraction.md) |
| Change weather fetching or METAR/TAF parsing | `backend/data/weather.py` → `backend/data/weather_parsing.py` → `backend/briefing/taf_parsing.py` → `backend/cache/manager.py` | none |
| Change weather briefings (TUI or web) | `backend/briefing/area_clustering.py` → `ui/modals/weather_briefing.py` → `scripts/weather_daemon/generator.py` → `scripts/weather_daemon/index_generator.py` | none |
| Change groupings or favorites | `backend/core/groupings.py` → `common/paths.py` → `ui/modals/goto_modal.py` → `ui/modals/save_grouping.py` | none |
| Add or change a command-line option | `main.py` (`build_arg_parser`, `resolve_airport_allowlist`) → `backend/config/constants.py` → `ui/app.py` | none |
| Fix an airport display name | `data/airport_names.csv` (edit directly) → `scripts/generate_airport_names.py` → `airport_disambiguator/disambiguator.py` | none |
| Change diversion or VFR-alternative search | `backend/core/diversions.py` → `backend/core/spatial.py` → `backend/core/aircraft_performance.py` → `ui/modals/diversion_modal.py` / `ui/modals/vfr_alternatives.py` | none |
| Change route weather or the MEA warning | `backend/core/route.py` → `backend/data/navaids.py` → `backend/data/cifp.py` → `ui/modals/route_weather.py` → `ui/modals/flight_info.py` | none |
| Change split-flap animation or flap character sets | `ui/config.py` → `widgets/split_flap_datatable.py` → `ui/tables.py` | none |
| Change historical statistics | `backend/data/statsim_api.py` → `ui/modals/historical_stats.py` | none |
| Change the weather daemon or its deployment | `scripts/weather_daemon/cli.py` → `scripts/weather_daemon/generator.py` → `scripts/weather_daemon/config.py` → `scripts/weather_daemon/service/` | none |
| Regenerate reference data | `scripts/generate_preset_groupings.py`, `scripts/generate_simaware_boundaries.py`, `scripts/precalculate_airport_spatial_data.py` → the matching file under `data/` | none |

## Layers

- **`backend/core/`**: owns the analysis pipeline (`analysis.py` `analyze_flights_data()`), flight, ETA, controller, grouping, route, spatial and diversion logic, and the `AirportStats` / `GroupingStats` models. References `backend/data`, `backend/cache`, `backend/config`, `common.paths` and `airport_disambiguator`; never `ui`, except the lazy `from ui import debug_logger` in `backend/core/flights.py` (line 208).
- **`backend/data/`**: owns everything fetched or loaded from outside: VATSIM, D-ATIS, StatsIM, weather, ATIS parsing, navaids, CIFP, runways and the unified airport loader (`loaders.py`). References `backend/cache`, `backend/config`, `common.paths`, and `backend/core/calculations.py` (`weather.py` only, for distance and bearing).
- **`backend/briefing/`**: owns weather-briefing helpers (`area_clustering.py`, `taf_parsing.py`). References `backend/core` and `backend/data`.
- **`backend/cache/`** (`manager.py`): owns the wind, METAR and TAF caches, the aircraft-speeds and ARTCC-grouping caches, and the weather cache save/load. References `backend/config` and `common.paths`.
- **`backend/config/`** (`constants.py`): owns cache TTLs and the global `WIND_SOURCE`, which `main.py` sets from `--wind-source`. References nothing.
- **`airport_disambiguator/`**: owns ICAO-to-display-name resolution (`disambiguator.py` public API, `disambiguation_engine.py`, `entity_extractor.py`, `name_processor.py`, `data_manager.py`); checks `data/airport_names.csv` first, then falls back to spaCy. References `backend/data`; never `ui`.
- **`ui/`**: owns the `VATSIMControlApp` (`app.py`), table config and management (`tables.py`, `config.py`) and the screens in `ui/modals/`. References `backend`, `widgets`, `common`.
- **`widgets/`** (`split_flap_datatable.py`): owns the animated DataTable. References `ui.debug_logger` only.
- **`common/`**: owns path resolution (`paths.py`) and logging (`logger.py`). References only itself.
- **`scripts/`**: owns the generators for `data/` files and `scripts/weather_daemon/` (staged `weather`, `briefings`, `tiles`, `index` generation, systemd unit and timer, nginx config, PowerShell deployment scripts). References `backend` and `airport_disambiguator`; nothing references `scripts`.

## Integration Footguns

- **Add or rename a keyboard shortcut** → the binding in `ui/app.py` `BINDINGS`, the `COMMANDS` list in `ui/modals/command_palette.py`, the text in `ui/modals/help_modal.py`, and the shortcut tables in `README.md`, `USER_GUIDE.md` and `CLAUDE.md` are separate copies; nothing enforces agreement.
- **Change grouping resolution** → `resolve_grouping_recursively()` in `backend/core/groupings.py` is used by `main.py`, `backend/core/analysis.py`, `ui/modals/goto_modal.py` and `scripts/weather_daemon/generator.py`; `ui/app.py` (line 890) still carries its own nested copy, so a cycle-detection or priority change must be made there too.
- **Add a grouping source** → priority is ARTCC auto-groupings, `data/preset_groupings/`, `data/custom_groupings.json`, then `data/favorites.json` (highest); loading spans `common/paths.py` (`load_merged_groupings`, `get_user_favorites_file`) and `backend/core/groupings.py`.
- **Change wind source handling** → `WIND_SOURCE` in `backend/config/constants.py` is a module global mutated by `main.py`, and read by `ui/modals/wind_info.py`, `ui/modals/flight_board.py` and `backend/data/weather.py`; import the module, never the value.
- **Add a table column** → `ui/tables.py` builds `TableConfig` from `ui/config.py` `ColumnConfig`, and the animated cells need a flap character set in `ui/config.py`.
- **Change a shared data file** (`data/airport_names.csv`, `data/aircraft_data.csv`, `data/airport_spatial_cache.json`, `data/preset_groupings/`, `data/simaware_boundaries/`) → the file is generated by a script in `scripts/`; regenerating overwrites unless the script preserves edits (`generate_airport_names.py` does).
- **Change the `backend` package surface** → `scripts/weather_daemon/generator.py` imports through `backend/__init__.py` and `backend.briefing`, and runs on a Linux server without the TUI, so keep `ui` imports out of the modules it uses.
- **Lint and formatting** → `ruff.toml` and `.pre-commit-config.yaml` (run with `prek run`) gate commits; there is no test job in `.github/workflows/build-release.yml`.

## Test locations

None: the repository has no test directory or test runner. Behavior is checked by running `python main.py` against live data or `data/test-vatsim-data.json` (a sample VATSIM response), and the weather daemon by `scripts/weather_daemon/service/LocalTest.ps1`.

## Deep docs

- [`atis-filtering-and-runway-extraction.md`](atis-filtering-and-runway-extraction.md): VATSIM and D-ATIS sources, METAR stripping and runway extraction.
- [`../CLAUDE.md`](../CLAUDE.md): setup, command-line options, data files, modal screens, keyboard shortcuts and the weather daemon workflow.
- [`../USER_GUIDE.md`](../USER_GUIDE.md): end-user guide to the app.
