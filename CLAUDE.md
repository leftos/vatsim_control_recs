# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VATSIM Control Recommendations is a terminal-based application that analyzes live VATSIM flight data and provides controller staffing recommendations. It uses Textual for the TUI framework, fetches real-time flight data from the VATSIM API, and displays airport statistics including departures, arrivals, ETAs, and staffed positions.

## Development Setup

### Installation

The project uses [uv](https://docs.astral.sh/uv/). `uv run main.py` creates `.venv` on Python 3.13 and installs the locked dependencies from `uv.lock` before starting; `uv sync` does the install alone. Never use pip: dependencies live in `pyproject.toml` (exact `==` pins) and `uv.lock`, so add or change one with `uv add` / `uv lock`. The PyInstaller build tool is in the `build` dependency group (`uv run --group build pyinstaller vatsim_control_recs.spec --noconfirm`).

### Tests

```bash
uv run --group dev pytest -q
```

Tests live under `tests/` and run in the `dev` dependency group (`pytest`, `pytest-asyncio`, pinned in `pyproject.toml`). They are headless Textual Pilot tests on fixture data and never touch the network. CI runs them in the "Run tests" step of `.github/workflows/build-release.yml` before the PyInstaller build.

Tests isolate the user data directory for the whole session: the repo-root `conftest.py` points `LOCALAPPDATA`, `XDG_DATA_HOME` and `HOME` at a temp dir before any project import (module imports log, and the first log call opens the log file there), and blanks `STATSIM_API_KEY` so `.env` is not read. `tests/test_isolation.py` checks it.

### Dependencies

Declared in `pyproject.toml`:

- `en-core-web-sm` - spaCy English model, pinned to its release wheel URL so `uv sync` installs it (no runtime download)
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
uv run main.py
```

When an agent runs the app (to check a change, take screenshots, or drive it with Textual's `run_test`), it always passes a filter: `--airports`, `--groupings`, or `--countries` with one country or a few. With no filter the app tracks every airport with traffic on the network, and that load bogs it down badly.

Track specific airports:
```bash
uv run main.py --airports KSFO KLAX KJFK
```

Track airports by country:
```bash
uv run main.py --countries US DE
```

Track custom groupings (recursively expanded to include all airports and sub-groupings):
```bash
uv run main.py --groupings "Bay Area" "California"
```

Additional useful options:
```bash
uv run main.py --include-all-staffed    # Include airports with zero planes if staffed
uv run main.py --disable-animations     # Disable split-flap animations
uv run main.py --hide-wind              # Hide wind column
uv run main.py --include-all-arriving   # Include airports with any arrivals filed
uv run main.py --max-eta-hours 2.0      # Show arrivals up to 2 hours out
uv run main.py --refresh-interval 30    # Refresh every 30 seconds
uv run main.py --wind-source minute     # Use up-to-the-minute wind data
uv run main.py --progressive-load       # Progressive table loading (auto for 50+ airports)
uv run main.py --help                   # All available options
```

## Architecture

The architecture entry point is [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): Task Index, layers, integration footguns, test locations and the deep docs.

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
- `favorites.json` in the user data directory (`get_user_data_dir()` in `common/paths.py`, not `data/`) - User-saved favorites with per-airport dep/arr filters (auto-created)
- `data/airport_spatial_cache.json` - Pre-calculated spatial index for proximity searches
- `data/runways.csv` - Runway data cache
- `data/runways_metadata.txt` - Runway data metadata (AIRAC cycle info)
- `data/preset_groupings/` - ARTCC-based preset groupings (23 ARTCCs: ZAB, ZAN, ZAU, ZBW, ZDC, ZDV, ZFW, ZHN, ZHU, ZID, ZJX, ZKC, ZLA, ZLC, ZMA, ZME, ZMP, ZNY, ZOA, ZOB, ZSE, ZSU, ZTL)
- `data/simaware_boundaries/` - Facility boundary polygons for spatial matching (600+ facilities worldwide)
- `cache/cifp/` in the user data directory (`get_user_cache_dir()` in `common/paths.py`, not `data/`) - CIFP data caches organized by AIRAC cycle
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
| `F` | Cycle direction (dep only / arr only / both) |
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
| `Tab` | Toggle "pick several" (multi-select) mode; entering it moves focus to the list |
| `Enter` | Open the highlighted item (browse), or pick it (pick several) |
| `Space` | Pick or unpick the highlighted row (pick several, list focused) |
| `Ctrl+Enter` | Open the board for the picks (pick several) |
| `Ctrl+S` | Save the picks as a grouping; while editing a favorite, save it |
| `Ctrl+D` | Delete the highlighted ★ favorite (not in picker mode) |
| `E` | Edit the highlighted ★ favorite (list focused) |
| `F` | Cycle the dep/arr filter of a picked row (pick several, list focused; both -> dep -> arr) |
| `Esc` | Close; while editing a favorite, this discards the edit |
| `@` prefix | Search airports only |
| `#` prefix | Search flights only |
| `$` prefix | Search groupings only |

The modal is 96 columns wide. The title names the mode: "Go To", "Go To · Pick several · {n} picked → {m} airports", or "Go To · Editing ★ {name}"; the picker (callback) mode keeps its caller's title.

A context hint line under the list shows only the keys that act in the current mode, focus and row, joined by " · ", at most six items; when more apply, Ctrl+D, E, F, Ctrl+S and Ctrl+Enter give way in that order, and Tab and Esc always show (e.g. a picked row: "Space pick · F dep/arr/both · Ctrl+Enter open · Ctrl+S save · Tab browse · Esc close").

Focus: with the list focused, a printable key moves focus to the search box and is appended to the query. In pick several, `E`, `F` and `Space` act on the list instead; in browse only `E` does; in picker (callback) or grouping-only mode all three are typed. The search box does not select its text on focus.

A key that cannot act (for example `E` on a preset, `F` on an unpicked row, `Ctrl+D` in picker mode, `Enter` or `Space` with no highlighted row, `Ctrl+Enter` with no picks) shows a warning toast saying why. A failed favorite delete shows an error toast, and a rename whose old entry cannot be removed says the new one was saved.
In edit mode a saved filter shows on an individually picked airport's row, and `F` there changes what is saved.

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

Debug logs are written to `logs/` in the user data directory (`get_user_logs_dir()` in `common/paths.py`). Logs older than 10 days are cleaned on the first log call (`cleanup_old_logs()` in `common/logger.py`). Use `ui.debug_logger.debug()` for UI debugging.

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
- `sync-deps.sh` - Runs on the server: installs uv into `/usr/local/bin` if it is missing, then the locked dependencies with `uv sync --frozen` into a Python the `www-data` daemon user can read; `setup.sh` and every deploy script call it, and a deploy stops if it fails

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
