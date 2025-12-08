# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

VATSIM Control Recommendations is a terminal-based application that analyzes live VATSIM flight data and provides controller staffing recommendations. It uses Textual for the TUI framework, fetches real-time flight data from the VATSIM API, and displays airport statistics including departures, arrivals, ETAs, and staffed positions.

## Development Setup

### Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Download the required spaCy language model for airport name disambiguation:
```bash
python -m spacy download en_core_web_sm
```

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

Track custom groupings:
```bash
python main.py --groupings "Bay Area" --supergroupings "California"
```

Additional useful options:
```bash
python main.py --include-all-staffed    # Include airports with zero planes if staffed
python main.py --disable-animations     # Disable split-flap animations
python main.py --hide-wind              # Hide wind column
python main.py --include-all-arriving   # Include airports with any arrivals filed
python main.py --help                   # All available options
```

## Architecture

### Module Structure

The codebase is organized into distinct layers:

**`backend/`** - Core data processing and analysis
- `backend/core/analysis.py` - Main entry point (`analyze_flights_data()`) that orchestrates the entire analysis pipeline
- `backend/core/flights.py` - Flight-level calculations (ETA, ground detection, proximity)
- `backend/core/controllers.py` - Controller position parsing and staffing detection
- `backend/core/groupings.py` - Airport grouping logic (custom + ARTCC-based)
- `backend/core/calculations.py` - Shared calculation utilities (ETA formatting, distance)
- `backend/core/models.py` - Data models (`AirportStats`, `GroupingStats`)
- `backend/data/` - External data fetching (VATSIM API, weather APIs)
- `backend/cache/` - Caching for aircraft data and ARTCC groupings
- `backend/config/` - Configuration constants

**`ui/`** - Textual-based user interface
- `ui/app.py` - Main `VATSIMControlApp` class with keyboard shortcuts and refresh logic
- `ui/tables.py` - Table management and configuration
- `ui/modals/` - Modal screens (flight boards, METAR lookup, wind info, etc.)
- `ui/config.py` - UI configuration and column definitions

**`airport_disambiguator/`** - Airport name processing
- Converts ICAO codes to human-readable names (e.g., "KSFO" → "San Francisco Intl")
- Uses spaCy NLP for entity extraction and location disambiguation
- Modular design: `disambiguator.py` (public API), `disambiguation_engine.py` (core logic), `entity_extractor.py` (NLP), `name_processor.py` (text processing)

**`widgets/`** - Custom Textual widgets
- `split_flap_datatable.py` - Animated DataTable with split-flap display effects

### Data Flow

1. **Initial Load** (`main.py`):
   - Parse command-line arguments
   - Load unified airport data (APT_BASE.csv, airports.json, iata-icao.csv)
   - Expand groupings/supergroupings/countries to individual airport ICAOs
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

**Recursive Grouping Resolution**: Groupings can contain nested groupings (e.g., "California" → "Bay Area" → ["KSFO", "KOAK"]). The `resolve_grouping_recursively()` pattern appears in both `main.py` and `analysis.py` to flatten these hierarchies with cycle detection.

**Batch Processing for Performance**: The application uses concurrent batch operations to minimize latency:
- `get_wind_info_batch()` - Parallel weather API calls
- `get_pretty_names_batch()` - Batch airport name disambiguation
- `ThreadPoolExecutor` for altimeter settings

**Separation of Tracking vs Display**: Command-line groupings are expanded to individual airports at startup for tracking, but groupings are preserved for display purposes in the UI's Groupings tab.

**Progressive Loading**: For large airport lists (50+), the UI can progressively load table rows in chunks to improve perceived startup time (`--progressive-load`).

**Split-Flap Animation System**: The `AnimatedCell` class maintains animation state per cell with configurable character sets (`ETA_FLAP_CHARS`, `ICAO_FLAP_CHARS`, etc.) and staggered delays for visual effect.

## Important Conventions

**Airport Tracking**: The application operates on a list of tracked airports (`airport_allowlist`) which can be specified via:
- `--airports` (explicit ICAO codes)
- `--countries` (expanded to all airports in those countries)
- `--groupings` (expanded to member airports)
- `--supergroupings` (recursively expanded to include sub-groupings)

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
1. Great circle distance to destination
2. Current groundspeed
3. Aircraft-specific approach speed for final 20nm (from `aircraft_data.csv`)

**Flight Categorization**:
- Departure: On ground at departure airport (groundspeed ≤ 40kt)
- Arrival: On ground at arrival airport OR in-flight within `max_eta_hours` of arrival
- Flights on ground without flight plan: Counted as departure at nearest airport

**Unified Airport Data**: Three sources merged into `unified_airport_data`:
- `APT_BASE.csv` - FAA airport data (coordinates, tower type, ARTCC)
- `airports.json` - OurAirports.com data (names, types)
- `iata-icao.csv` - IATA/ICAO code mappings

## Data Files

- `data/APT_BASE.csv` - FAA airport database
- `data/airports.json` - OurAirports.com airport database
- `data/iata-icao.csv` - IATA/ICAO code mappings
- `data/aircraft_data.csv` - Aircraft approach speeds for ETA calculation
- `data/custom_groupings.json` - User-defined airport groupings

## VATSIM Data Structure

### Working with Test Data

The file `data/test-vatsim-data.json` contains sample VATSIM API responses but is too large (~2MB) to read directly. To explore the data structure:

**Option 1: Load via Python**
```python
import json
with open('data/test-vatsim-data.json') as f:
    data = json.load(f)

# Explore structure
data.keys()                          # Top-level keys: 'general', 'pilots', 'controllers', 'atis', etc.
data['pilots'][0].keys()             # Fields available on a pilot
data['pilots'][0]['flight_plan'].keys()  # Flight plan fields
data['pilots'][0]                    # Full pilot record example
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

- **Ctrl+C**: Quit
- **Ctrl+R**: Manually refresh data
- **Ctrl+Space**: Pause/Resume auto-refresh
- **Ctrl+F**: Open search box (airports tab only)
- **Ctrl+W**: Wind information lookup
- **Ctrl+E**: METAR lookup
- **Ctrl+T**: Tracked Airports Manager (add/remove tracked airports)
- **Enter**: Open flight board for selected airport/grouping
- **Escape**: Close modals or cancel search

## Debugging

Debug logs are written to `debug_logs/debug_YYYYMMDD.log`. Logs older than 7 days are automatically cleaned on startup. Use `ui.debug_logger.debug()` for UI debugging.
