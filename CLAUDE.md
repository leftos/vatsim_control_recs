# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VATSIM Control Recommendations is a terminal-based application that analyzes live VATSIM flight data and provides controller staffing recommendations. It uses Textual for the TUI framework, fetches real-time flight data from the VATSIM API, and displays airport statistics including departures, arrivals, ETAs, and staffed positions.

## Development Setup

### Installation

The application auto-bootstraps on first run: creates a `.venv`, installs dependencies, and downloads the spaCy model. Just run:

```bash
python main.py
```

For manual setup:

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Download the required spaCy language model for airport name disambiguation:
```bash
python -m spacy download en_core_web_sm
```

### Dependencies

- `requests` - HTTP library for VATSIM API requests
- `textual` - Terminal UI framework
- `spacy` - NLP for airport name entity recognition
- `cachetools` - Thread-safe caching with TTL and size limits
- `Pillow` - Image processing for weather map tile generation
- `numpy` - Numerical computing for vectorized tile generation
- `scipy` - Spatial indexing for memory-efficient tile generation
- `pyperclip` - Clipboard support for copy functionality

### Environment Variables

- `STATSIM_API_KEY` - API key from statsim.net for historical flight statistics feature (optional, stored in `.env` file)

### Running the Application

Basic execution:
```bash
python main.py
```

Track specific airports:
```bash
python main.py --airports KSFO KLAX KJFK
```

Track airports by country:
```bash
python main.py --countries US DE
```

Track custom groupings (recursively expanded to include all airports and sub-groupings):
```bash
python main.py --groupings "Bay Area" "California"
```

Additional useful options:
```bash
python main.py --include-all-staffed    # Include airports with zero planes if staffed
python main.py --disable-animations     # Disable split-flap animations
python main.py --hide-wind              # Hide wind column
python main.py --include-all-arriving   # Include airports with any arrivals filed
python main.py --max-eta-hours 2.0      # Show arrivals up to 2 hours out
python main.py --refresh-interval 30    # Refresh every 30 seconds
python main.py --wind-source minute     # Use up-to-the-minute wind data
python main.py --progressive-load       # Progressive table loading (auto for 50+ airports)
python main.py --help                   # All available options
```

## Architecture

### Module Structure

The codebase is organized into distinct layers:

**`backend/`** - Core data processing and analysis

- `backend/core/analysis.py` - Main entry point (`analyze_flights_data()`) that orchestrates the entire analysis pipeline
- `backend/core/flights.py` - Flight-level calculations (ETA, ground detection, proximity)
- `backend/core/controllers.py` - Controller position parsing and staffing detection
- `backend/core/groupings.py` - Airport grouping logic (custom + ARTCC-based + user favorites)
- `backend/core/calculations.py` - Shared calculation utilities (ETA formatting, distance)
- `backend/core/models.py` - Data models (`AirportStats`, `GroupingStats`)
- `backend/core/aircraft_performance.py` - Aircraft performance calculations (approach speeds, glide slope)
- `backend/core/diversions.py` - Diversion airport finding logic
- `backend/core/route.py` - Route processing and waypoint parsing
- `backend/core/route_distance.py` - Route distance calculations using filed route
- `backend/core/spatial.py` - Spatial calculations and airport proximity search
- `backend/data/vatsim_api.py` - VATSIM API data fetching (pilots, controllers, ATIS, member stats)
- `backend/data/weather.py` - Weather data fetching (METAR, wind, altimeter)
- `backend/data/weather_parsing.py` - Weather data parsing (visibility fractions, flight categories)
- `backend/data/loaders.py` - Data loaders for unified airport data
- `backend/data/atis_filter.py` - ATIS parsing (approach info, runway assignments, SIMUL ILS)
- `backend/data/cifp.py` - CIFP (Coded Instrument Flight Procedures) data handling
- `backend/data/datis_api.py` - Real-world D-ATIS API integration (fallback source)
- `backend/data/navaids.py` - Navigation aids handling and MEA lookup for route weather
- `backend/data/runways.py` - Runway data handling
- `backend/data/statsim_api.py` - StatsIM API for historical traffic data
- `backend/briefing/area_clustering.py` - Geographic area clustering for weather briefings
- `backend/briefing/taf_parsing.py` - TAF parsing for terminal forecasts
- `backend/cache/manager.py` - Cache manager for aircraft data and ARTCC groupings
- `backend/config/constants.py` - Configuration constants (wind source, cache TTLs)

**`ui/`** - Textual-based user interface

- `ui/app.py` - Main `VATSIMControlApp` class with keyboard shortcuts and refresh logic
- `ui/tables.py` - Table management, column configs, and sorting
- `ui/config.py` - UI configuration, column definitions, flap character sets, flight category colors
- `ui/utils.py` - UI utility functions
- `ui/debug_logger.py` - Debug logging utilities
- `ui/modals/` - 18 modal screens (see Modal Screens section below)

**`airport_disambiguator/`** - Airport name processing

- Converts ICAO codes to human-readable names (e.g., "KSFO" -> "San Francisco Intl")
- Uses spaCy NLP for entity extraction and location disambiguation
- Modular design: `disambiguator.py` (public API), `disambiguation_engine.py` (core logic), `entity_extractor.py` (NLP), `name_processor.py` (text processing)

**`common/`** - Shared utilities

- `common/paths.py` - Path resolution for data files, favorites, groupings
- `common/logger.py` - Logging configuration

**`widgets/`** - Custom Textual widgets

- `split_flap_datatable.py` - Animated DataTable with split-flap display effects

**`scripts/`** - Utility scripts

- `scripts/generate_airport_names.py` - Generate/regenerate `data/airport_names.csv` (preserves manual edits)
- `scripts/generate_preset_groupings.py` - Generate ARTCC-based preset groupings from SimAware boundaries
- `scripts/generate_simaware_boundaries.py` - Generate SimAware boundary data
- `scripts/precalculate_airport_spatial_data.py` - Pre-calculate spatial index cache
- `scripts/weather_daemon/` - Weather briefing daemon for web deployment

### Modal Screens

The UI has 18 modal screens in `ui/modals/`:

| Modal | Access | Description |
|-------|--------|-------------|
| `FlightBoardScreen` | `Enter` on airport/grouping | Departure/arrival boards with live updates |
| `FlightInfoScreen` | `Enter` on flight in board | Comprehensive flight details, VFR warnings, MEA violations |
| `GoToScreen` | `Ctrl+G` / `Ctrl+L` | Multi-target navigation with favorites, filtering, multi-select |
| `MetarInfoScreen` | `Ctrl+E` | METAR lookup with context pre-fill |
| `WindInfoScreen` | `Ctrl+W` | Wind lookup with context pre-fill |
| `WeatherBriefingScreen` | `Ctrl+B` | Sector/airport weather briefings with ATIS, TAF, approach info |
| `FlightWeatherBriefingScreen` | `Ctrl+B` on flight | Route-based pilot weather briefing |
| `RouteWeatherScreen` | `W` from flight info | Route weather along filed waypoints |
| `VfrAlternativesScreen` | `Ctrl+A` | Find VFR/MVFR airports near a location |
| `DiversionModal` | `D` from flight info | Find diversion airports with weather, runway, ATC info |
| `HistoricalStatsScreen` | `Ctrl+S` | Historical traffic patterns from StatsIM |
| `TrackedAirportsModal` | `Ctrl+T` | Manage tracked airports (add/remove/save as grouping) |
| `AirportTrackingModal` | Via tracked airports | Quick add/remove airports dialog |
| `SaveGroupingModal` | `S` in tracked airports | Save current selection as custom grouping |
| `FlightLookupScreen` | Via GoTo `#` prefix | Find flights by callsign |
| `HelpScreen` | `F1` / `?` | Help screen with keyboard shortcuts |
| `CommandPaletteScreen` | `F2` | Searchable command palette (VS Code style) |
| `ConfirmModal` | Programmatic | Confirmation dialogs for destructive actions |
| `NotificationManager` | Automatic in flight boards | Toast notifications for runway/weather changes |

### Data Flow

1. **Initial Load** (`main.py`):
   - Auto-bootstrap: create venv, install deps, download spaCy model if needed
   - Parse command-line arguments
   - Load unified airport data (APT_BASE.csv, airports.json, iata-icao.csv)
   - Expand groupings/countries to individual airport ICAOs
   - Call `analyze_flights_data()` to fetch and process initial data

2. **Analysis Pipeline** (`backend/core/analysis.py`):
   - Download VATSIM data (pilots + controllers)
   - Extract staffed controller positions
   - Filter flights by tracked airports
   - Categorize flights as departures/arrivals based on ground position and ETA
   - Batch-fetch weather data (wind, altimeter) for active airports
   - Batch-process airport names through disambiguator
   - Build `AirportStats` and `GroupingStats` objects
   - Sort and return processed data

3. **UI Display** (`ui/app.py`):
   - Populate DataTables with airport/grouping statistics
   - Enable split-flap animations (optional)
   - Auto-refresh on interval (default: 15 seconds)
   - Handle keyboard shortcuts and modal interactions

### Key Design Patterns

**Recursive Grouping Resolution**: Groupings can contain nested groupings (e.g., "California" -> "Bay Area" -> ["KSFO", "KOAK"]). The `resolve_grouping_recursively()` pattern appears in both `main.py` and `analysis.py` to flatten these hierarchies with cycle detection.

**Grouping Priority Layers**: Groupings are loaded from multiple sources in priority order:
1. ARTCC-based auto-groupings (lowest)
2. Preset groupings from `data/preset_groupings/`
3. Custom groupings from `data/custom_groupings.json`
4. User favorites from `data/favorites.json` (highest)

**Batch Processing for Performance**: The application uses concurrent batch operations to minimize latency:
- `get_wind_info_batch()` - Parallel weather API calls
- `get_pretty_names_batch()` - Batch airport name disambiguation
- `get_metar_batch()` - Batch METAR fetching
- `ThreadPoolExecutor` for altimeter settings

**Separation of Tracking vs Display**: Command-line groupings are expanded to individual airports at startup for tracking, but groupings are preserved for display purposes in the UI's Groupings tab.

**Context-Aware Pre-filling**: Modal screens (METAR, wind, weather briefing) pre-fill the airport code based on context: selected airport in the main table, current flight board, or flight info screen.

**Progressive Loading**: For large airport lists (50+), the UI can progressively load table rows in chunks to improve perceived startup time (`--progressive-load`).

**Split-Flap Animation System**: The `AnimatedCell` class maintains animation state per cell with configurable character sets (`ETA_FLAP_CHARS`, `ICAO_FLAP_CHARS`, etc.) and staggered delays for visual effect.

**Favorites System**: Users can save multi-airport selections as favorites via the GoTo modal (`Ctrl+S`). Favorites support per-airport dep/arr filtering and are stored in `data/favorites.json`. Favorites appear with a star prefix in the GoTo modal and can be edited (`E`) or deleted (`Ctrl+D`).

## Important Conventions

**Airport Tracking**: The application operates on a list of tracked airports (`airport_allowlist`) which can be specified via:
- `--airports` (explicit ICAO codes)
- `--countries` (expanded to all airports in those countries)
- `--groupings` (recursively expanded to all airports and sub-groupings)

All tracking happens at the individual airport level; groupings are only used for display organization.

**Wind Data Sources**: Two modes available via `--wind-source`:
- `metar` (default): Uses METAR from aviationweather.gov
- `minute`: Uses up-to-the-minute data from weather.gov

The global `backend.config.constants.WIND_SOURCE` controls which source is used.

**Controller Position Display Logic**:
- NON-ATCT airports show "N/A" (no tower)
- ATIS-only positions show "TOP-DOWN" (top-down service from another facility)
- Multiple positions: Display comma-separated (ATIS excluded if other positions present)

**ETA Calculation**: Flight ETA is calculated using:
1. Filed route distance (when parseable) or great circle distance to destination
2. Current groundspeed
3. Aircraft-specific approach speed for final 20nm (from `aircraft_data.csv`)
4. Glide slope clamping with 3-degree model below 10,000ft

**Flight Categorization**:
- Departure: On ground at departure airport (groundspeed <= 40kt)
- Arrival: On ground at arrival airport OR in-flight within `max_eta_hours` of arrival
- Flights on ground without flight plan: Counted as departure at nearest airport

**Dual Arrival Counting** (with `--include-all-arriving`):
- `arrivals`: Flights within `max_eta_hours` window
- `arrivals_all`: All flights filed to the airport regardless of ETA
- Displayed as `arr<xH / arr_all` format when counts differ

**Unified Airport Data**: Three sources merged into `unified_airport_data`:
- `raw/APT_BASE.csv` - FAA airport data (coordinates, tower type, ARTCC)
- `raw/airports.json` - OurAirports.com data (names, types)
- `raw/iata-icao.csv` - IATA/ICAO code mappings

**Airport Name Resolution**: The disambiguator checks `data/airport_names.csv` first for pre-computed display names. Airports not in the CSV fall back to on-demand NER-based disambiguation. To fix a name, edit the CSV directly. To regenerate the CSV (preserving manual edits): `python scripts/generate_airport_names.py`

**ATIS Parsing**: The `atis_filter.py` module parses D-ATIS and VATSIM ATIS for:
- Approach type (ILS, RNAV, Visual, SIMUL ILS highlighted)
- Runway assignments (departure/arrival runway parsing)
- Formatted runway summaries for weather briefings

## Data Files

- `data/airport_names.csv` - Human-editable airport display names (authoritative source)
- `data/raw/APT_BASE.csv` - FAA airport database
- `data/raw/airports.json` - OurAirports.com airport database
- `data/raw/iata-icao.csv` - IATA/ICAO code mappings
- `data/aircraft_data.csv` - Aircraft approach speeds for ETA calculation
- `data/custom_groupings.json` - User-defined airport groupings
- `data/favorites.json` - User-saved favorites with per-airport dep/arr filters (auto-created)
- `data/airport_spatial_cache.json` - Pre-calculated spatial index for proximity searches
- `data/runways.csv` - Runway data cache
- `data/runways_metadata.txt` - Runway data metadata (AIRAC cycle info)
- `data/preset_groupings/` - ARTCC-based preset groupings (23 ARTCCs: ZAB, ZAN, ZAU, ZBW, ZDC, ZDV, ZFW, ZHN, ZHU, ZID, ZJX, ZKC, ZLA, ZLC, ZMA, ZME, ZMP, ZNY, ZOA, ZOB, ZSE, ZSU, ZTL)
- `data/simaware_boundaries/` - Facility boundary polygons for spatial matching (600+ facilities worldwide)
- `data/cifp/` - CIFP data caches organized by AIRAC cycle
- `data/test-vatsim-data.json` - Sample VATSIM API response for development (~2MB)

## VATSIM Data Structure

### Working with Test Data

The file `data/test-vatsim-data.json` contains sample VATSIM API responses but is too large (~2MB) to read directly. To explore the data structure:

**Option 1: Load via Python**
```python
import json

with open("data/test-vatsim-data.json") as f:
    data = json.load(f)

# Explore structure
data.keys()  # Top-level keys: 'general', 'pilots', 'controllers', 'atis', etc.
data["pilots"][0].keys()  # Fields available on a pilot
data["pilots"][0]["flight_plan"].keys()  # Flight plan fields
data["pilots"][0]  # Full pilot record example
```

**Option 2: Use grep for quick searches**
```bash
# Find available fields
grep -o '"[a-z_]*":' data/test-vatsim-data.json | sort -u | head -30

# Search for specific field values
grep -o '"assigned_transponder":"[^"]*"' data/test-vatsim-data.json | head -5
```

### Pilot Data Fields

The raw VATSIM API pilot data (accessed via `vatsim_data['pilots']`) contains:

**Identification:**
- `cid` - VATSIM Client ID
- `callsign` - Flight callsign (e.g., "AAL123")
- `name` - Pilot name

**Position:**
- `latitude`, `longitude` - Current position
- `altitude` - Current altitude (feet)
- `heading` - Current heading (degrees)
- `groundspeed` - Current groundspeed (knots)

**Transponder & Pressure:**
- `transponder` - Current transponder code being squawked
- `qnh_i_hg`, `qnh_mb` - Altimeter settings

**Flight Plan** (`flight_plan` nested object):
- `departure`, `arrival`, `alternate` - ICAO codes
- `aircraft_short` - Aircraft type (e.g., "B738")
- `aircraft`, `aircraft_faa` - Full aircraft codes with equipment
- `flight_rules` - "I" (IFR) or "V" (VFR)
- `altitude` - Filed cruise altitude
- `route` - Filed route string
- `remarks` - Pilot remarks
- `deptime`, `enroute_time`, `fuel_time` - Time fields (HHMM format)
- `assigned_transponder` - ATC-assigned squawk code ("0000" means not assigned)

**Session:**
- `logon_time`, `last_updated` - ISO 8601 timestamps
- `server` - Connected server

## UI Keyboard Shortcuts

### Main View

| Shortcut | Action |
|----------|--------|
| `Ctrl+Z` | Quit |
| `Ctrl+R` | Refresh data |
| `Ctrl+P` | Pause/Resume auto-refresh |
| `Ctrl+F` | Search/filter airports (airports tab only) |
| `Ctrl+G` / `Ctrl+L` | Go To (unified navigation) |
| `Ctrl+E` | METAR lookup |
| `Ctrl+W` | Wind information lookup |
| `Ctrl+A` | VFR alternatives finder |
| `Ctrl+B` | Weather briefing |
| `Ctrl+S` | Historical statistics |
| `Ctrl+T` | Tracked Airports Manager |
| `Enter` | Open flight board for selected airport/grouping |
| `Escape` | Close modal or cancel search |
| `F1` / `?` | Help screen |
| `F2` | Command palette |
| `Tab` | Switch between tabs |

### Flight Board

| Shortcut | Action |
|----------|--------|
| `Enter` | Open flight info for selected flight |
| `Escape` / `Q` | Close flight board |
| Double-click | Open flight info |

### Flight Info

| Shortcut | Action |
|----------|--------|
| `C` | Copy route to clipboard |
| `D` | Find diversion airports |
| `W` | Route weather |
| `Escape` / `Q` | Close |

### GoTo Modal

| Shortcut | Action |
|----------|--------|
| `Tab` | Toggle multi-select mode |
| `Enter` / `Space` | Select/toggle item (multi-select) |
| `Ctrl+Enter` | Open selection (multi-select) |
| `Ctrl+S` | Save selection as favorite |
| `Ctrl+D` | Delete favorite |
| `E` | Edit favorite |
| `F` | Cycle per-airport filter (both -> dep -> arr) |
| `@` prefix | Search airports only |
| `#` prefix | Search flights only |
| `$` prefix | Search groupings only |

### Weather Briefing

| Shortcut | Action |
|----------|--------|
| `P` | Print/export to browser as HTML |
| `Escape` / `Q` | Close |

### Tracked Airports Manager

| Shortcut | Action |
|----------|--------|
| `A` | Add airports |
| `Delete` | Remove selected |
| `Space` | Select/deselect |
| `S` | Save as custom grouping |
| `Escape` | Close |

### Diversion Modal

| Shortcut | Action |
|----------|--------|
| `R` | Refresh |
| `1` | Sort by position |
| `2` | Sort by destination |
| `3` | Sort by runway |
| `Escape` | Close |

### Historical Stats

| Shortcut | Action |
|----------|--------|
| `Enter` | Search |
| `C` | Copy results |
| `Escape` | Close |

## Debugging

Debug logs are written to `debug_logs/debug_YYYYMMDD.log`. Logs older than 7 days are automatically cleaned on startup. Use `ui.debug_logger.debug()` for UI debugging.

## Weather Daemon

The weather daemon generates weather briefing pages for the website at `https://leftos.dev/weather/`. It runs on a Linux server and is managed via PowerShell scripts from Windows.

### PowerShell Scripts (`scripts/weather_daemon/service/`)

**Testing locally:**
- `LocalTest.ps1 [stages]` - Generate weather briefings locally and open in browser
  - `.\LocalTest.ps1` - Full generation (all stages)
  - `.\LocalTest.ps1 index` - Index page only (fastest for UI changes)
  - `.\LocalTest.ps1 tiles,index` - Tiles and index only
  - Output goes to `test_output/`

**Deployment to server:**
- `QuickDeploy.ps1` - Git pull on server and regenerate (requires changes to be pushed first)
- `Deploy.ps1` - Full deployment via rsync (for Linux/WSL only, uses bash)

**Server management:**
- `Status.ps1` - Check daemon status and recent logs
- `Logs.ps1` - View daemon logs
- `Restart.ps1` - Restart the daemon timer
- `RunNow.ps1` - Trigger immediate regeneration on server

**Regeneration scripts (on server):**
- `RegenIndex.ps1` - Regenerate only the index page
- `RegenHtml.ps1` - Regenerate HTML briefings
- `RegenTiles.ps1` - Regenerate map tiles
- `RegenCached.ps1` - Regenerate using cached weather data

### Typical Development Workflow

1. Make changes to generator code (`index_generator.py`, `generator.py`, etc.)
2. Test locally: `.\LocalTest.ps1 index` (for UI changes) or `.\LocalTest.ps1` (full test)
3. Commit and push changes
4. Deploy: `.\QuickDeploy.ps1`
