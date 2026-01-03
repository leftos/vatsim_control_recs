# VATSIM Control Recommendations - User Guide

A comprehensive guide for installing and using the VATSIM Control Recommendations terminal application.

## Table of Contents

- [Introduction](#introduction)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Command-Line Options](#command-line-options)
- [User Interface Overview](#user-interface-overview)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Features](#features)
  - [Flight Boards](#flight-boards)
  - [Flight Information](#flight-information)
  - [Weather Lookups](#weather-lookups)
  - [Weather Briefings](#weather-briefings)
  - [VFR Alternatives Finder](#vfr-alternatives-finder)
  - [Diversion Airport Finder](#diversion-airport-finder)
  - [Go To Navigation](#go-to-navigation)
  - [Tracked Airports Manager](#tracked-airports-manager)
  - [Command Palette](#command-palette)
- [Custom Groupings](#custom-groupings)
- [Weather Data Sources](#weather-data-sources)
- [Visual Features](#visual-features)
- [Tips & Tricks](#tips--tricks)
- [Troubleshooting](#troubleshooting)

---

## Introduction

VATSIM Control Recommendations is a terminal-based application that analyzes live VATSIM flight data and provides controller staffing recommendations. It displays real-time airport statistics including:

- **Departures**: Aircraft on the ground ready to depart
- **Arrivals**: Aircraft inbound with ETAs
- **Wind & Weather**: Current conditions from aviation weather sources
- **Staffed Positions**: Which ATC positions are online

The application is built using the Textual framework, providing a rich terminal UI with features like split-flap animations, searchable tables, and detailed flight information modals.

---

## Installation

### Prerequisites

- **Python 3.10 or higher** ([Download Python](https://www.python.org/downloads/))
- **Terminal** with UTF-8 support (Windows Terminal, iTerm2, or similar)

### Step 1: Clone or Download the Repository

```bash
git clone https://github.com/your-repo/vatsim_control_recs.git
cd vatsim_control_recs
```

### Step 2: Run the Application

```bash
python main.py
```

**That's it!** The application handles everything else automatically:

1. **Creates a virtual environment** (`.venv`) if not already running in one
2. **Installs all dependencies** from `requirements.txt`
3. **Downloads the spaCy language model** (`en_core_web_sm`) for airport name processing

On first run, you'll see progress messages as dependencies are installed. Subsequent launches will start immediately.

### Verifying Installation

To verify everything is set up correctly without launching the full app:

```bash
python main.py --help
```

You should see a list of available command-line options (this runs instantly without setup).

---

## Quick Start

### Track All Airports (Default)

```bash
python main.py
```

This launches the application tracking all airports with active traffic.

### Track Specific Airports

```bash
python main.py --airports KSFO KLAX KJFK KORD
```

### Track All Airports in Specific Countries

```bash
python main.py --countries US CA
```

### Track Custom Groupings

```bash
python main.py --groupings "Bay Area" "SoCal"
```

### Combine Multiple Filters

```bash
python main.py --airports KSFO --groupings "SoCal" --include-all-staffed
```

---

## Command-Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--airports ICAO [ICAO ...]` | (all) | Track specific airports by ICAO code |
| `--countries CODE [CODE ...]` | (none) | Track all airports in specified countries (e.g., `US`, `DE`, `CA`) |
| `--groupings NAME [NAME ...]` | (all) | Track airports from custom or preset groupings |
| `--max-eta-hours HOURS` | 1.0 | Maximum ETA in hours for arrival filter (flights further out are excluded) |
| `--refresh-interval SECS` | 15 | Auto-refresh interval in seconds |
| `--include-all-staffed` | false | Include airports with zero traffic if they have ATC online |
| `--include-all-arriving` | false | Include airports with any filed arrivals, regardless of ETA |
| `--disable-animations` | false | Disable split-flap animations for instant updates |
| `--progressive-load` | auto | Enable progressive table loading (auto-enabled for 50+ airports) |
| `--progressive-chunk-size N` | 20 | Rows to load per chunk in progressive mode |
| `--wind-source SOURCE` | metar | Wind data source: `metar` or `minute` |
| `--hide-wind` | false | Hide the wind column from the main table |

### Examples

**Quick startup with no animations:**
```bash
python main.py --disable-animations --airports KSFO KOAK
```

**Include staffed airports even with no traffic:**
```bash
python main.py --include-all-staffed --countries US
```

**Longer ETA window (show arrivals up to 2 hours out):**
```bash
python main.py --max-eta-hours 2.0
```

**Use up-to-the-minute wind data:**
```bash
python main.py --wind-source minute
```

---

## User Interface Overview

The application has several main components:

### Header Bar

The top bar displays:
- **Application title**: "VATSIM Control Recommendations"
- **Local time**: Your local timezone
- **UTC time**: Zulu time for aviation reference

### Main Tables

The interface has two tabs:

#### Individual Airports Tab
Shows all tracked airports with columns:
- **Airport**: ICAO code
- **Name**: Human-readable airport name
- **Dep**: Number of departures (aircraft on ground)
- **Arr**: Number of arrivals (inbound aircraft within ETA window)
- **Next ETA**: Time until next arrival (MM:SS format)
- **Wind**: Current wind conditions (direction/speed, gusts if present)
- **Altim**: Current altimeter setting (inches Hg)
- **Positions**: Staffed ATC positions

#### Custom Groupings Tab
Shows configured airport groupings with aggregated statistics.

### Status Bar

The bottom bar shows:
- **Refresh status**: Active, Paused, or Auto-paused (idle)
- **Time since last refresh**: Countdown to next refresh
- **Tracking counts**: Number of airports and groupings being tracked

### Table Sorting

Click column headers to sort by that column. Click again to reverse sort order.

### Flight Categories (Color Coding)

When viewing weather information, airports are color-coded by flight category:
- **Green**: VFR (Visual Flight Rules - good conditions)
- **Blue**: MVFR (Marginal VFR - reduced visibility)
- **Red**: IFR (Instrument Flight Rules - poor conditions)
- **Magenta**: LIFR (Low IFR - very poor conditions)

---

## Keyboard Shortcuts

### General Navigation

| Shortcut | Action |
|----------|--------|
| `Ctrl+C` | Quit the application |
| `Ctrl+R` | Refresh data immediately |
| `Ctrl+P` | Pause/Resume auto-refresh |
| `Tab` | Switch between tabs |
| `Up/Down` | Navigate table rows |
| `Page Up/Down` | Navigate table pages |
| `Home/End` | Jump to first/last row |

### Search & Navigation

| Shortcut | Action |
|----------|--------|
| `Ctrl+F` | Filter/search airports (airports tab only) |
| `Ctrl+G` or `Ctrl+L` | Open Go To modal (unified search) |
| `Enter` | Open flight board for selected airport/grouping |
| `Escape` | Close modal or cancel search |

### Weather & Information

| Shortcut | Action |
|----------|--------|
| `Ctrl+E` | METAR lookup |
| `Ctrl+W` | Wind information lookup |
| `Ctrl+A` | VFR alternatives finder |
| `Ctrl+B` | Weather briefing |
| `Ctrl+S` | Historical statistics |

### Airport Management

| Shortcut | Action |
|----------|--------|
| `Ctrl+T` | Open Tracked Airports Manager |

### Help

| Shortcut | Action |
|----------|--------|
| `?` or `F1` | Show help screen |
| `F2` | Open command palette |

---

## Features

### Flight Boards

**Access:** Select an airport or grouping and press `Enter`

The flight board shows two columns:
- **Departures**: Aircraft on the ground at the airport
- **Arrivals**: Aircraft inbound to the airport

Each flight displays:
- Callsign (e.g., `AAL123`)
- Destination/Origin airport
- ETA (for arrivals)

**Grouping Flight Boards** aggregate flights from all airports in the group and show which specific airport each flight is associated with.

**Flight Board Shortcuts:**
| Shortcut | Action |
|----------|--------|
| `Enter` | Open detailed flight information |
| `Escape` or `Q` | Close flight board |
| Double-click | Open flight information |

For grouping flight boards, weather and runway change notifications appear as flashing toasts when conditions change at member airports.

---

### Flight Information

**Access:** From a flight board, select a flight and press `Enter`

Displays comprehensive flight details:

**Route Information:**
- Departure and arrival airports (with names)
- Alternate airport if filed
- Aircraft type with equipment suffix (e.g., `B738/L`)

**Flight Plan:**
- Filed altitude and flight rules (IFR/VFR)
- Assigned squawk code
- ETA to destination
- Full filed route string (word-wrapped)

**Pilot Information:**
- Nearest altimeter setting with distance from airport
- Pilot and ATC hours (from VATSIM stats)

**VFR Weather Warning:**
For VFR flights, shows warnings if departure or arrival has non-VFR conditions and suggests nearby VFR/MVFR alternate airports within 100nm.

**Flight Info Shortcuts:**
| Shortcut | Action |
|----------|--------|
| `D` | Find diversion airports |
| `W` | View route weather |
| `Escape` or `Q` | Close |

The flight info screen auto-refreshes every 15 seconds.

---

### Weather Lookups

#### METAR Lookup (`Ctrl+E`)

Look up current METAR for any airport by ICAO code. Shows:
- Raw METAR text with highlighted phenomena
- Flight category (VFR/MVFR/IFR/LIFR) with color coding
- Decoded weather information

Pre-fills the airport code based on context (selected airport, current flight info, etc.).

#### Wind Information (`Ctrl+W`)

Look up current wind conditions for any airport. Shows:
- Wind direction (degrees)
- Wind speed (knots)
- Gust speed if applicable

Uses the configured wind source (METAR or minute-by-minute data).

---

### Weather Briefings

#### Weather Briefing (`Ctrl+B`)

**Access:** Press `Ctrl+B` from the main table or any flight board

Works for airports, groupings, and flights:

**For Airports/Groupings:**
- **METAR data** for each airport
- **TAF data** (Terminal Aerodrome Forecast) when available
- **ATIS information** if online
- **Flight category summary** (counts of VFR/MVFR/IFR/LIFR)
- **TAF trend indicators** showing improving/worsening conditions

For groupings, airports are grouped by geographic area, centered around staffed airports.

**For Flights:**
Provides a pilot-style weather briefing along the route:
- **Synopsis** of departure, enroute, and arrival conditions
- **Departure weather** (METAR/TAF)
- **Enroute weather** (airports sampled every ~200nm along route)
- **Arrival weather** (METAR/TAF)
- **Alternate weather** if filed

**Print to Browser:** Press `P` to export the briefing as HTML and open it in your default browser for printing or reference.

#### Route Weather (from Flight Info)

**Access:** From flight info screen, press `W`

Shows weather along a flight's filed route by parsing waypoints:
- Departure airport weather
- Enroute airports near filed waypoints
- Arrival airport weather
- Alternate airport weather (if filed)

---

### VFR Alternatives Finder

**Access:** `Ctrl+A`

Find VFR or MVFR airports near a specified location:
- Enter an airport ICAO code
- Configure search radius
- View list of nearby airports with good weather

Each result shows:
- Flight category (VFR/MVFR)
- Distance and direction from search point
- Airport name

---

### Diversion Airport Finder

**Access:** From flight info screen, press `D`

For in-flight aircraft, finds suitable diversion airports:
- Airports within configurable range of current position
- Weather category for each option
- Distance, direction, and ETA
- Runway lengths and approach types available
- Highlights staffed airports with controller information

---

### Go To Navigation

**Access:** `Ctrl+G` or `Ctrl+L`

Unified search modal for quickly navigating to:
- **Airports**: Type airport ICAO or name
- **Flights**: Type callsign to find specific flights
- **Groupings**: Type grouping name

**Filter Prefixes:**
- `@` - Search airports only (e.g., `@SFO`)
- `#` - Search flights only (e.g., `#AAL123`)
- `$` - Search groupings only (e.g., `$Bay`)

Uses a pre-built cache for instant search responsiveness.

---

### Tracked Airports Manager

**Access:** `Ctrl+T`

View and manage all currently tracked airports:

**Manager Shortcuts:**
| Shortcut | Action |
|----------|--------|
| `A` | Add airports |
| `Delete` | Remove selected airport(s) |
| `Space` | Select/deselect multiple airports |
| `S` | Save current set as custom grouping |
| `Escape` | Close manager |

**Adding Airports:**
Use the quick-add syntax:
- `+KSFO +KOAK` - Add airports
- `-KSJC -KMRY` - Remove airports
- Mix additions and removals in one command

---

### Command Palette

**Access:** `F2`

A searchable list of all available commands (similar to VS Code):
- Type to filter commands
- Press `Enter` to execute
- Shows keyboard shortcuts for each command

---

## Custom Groupings

Custom groupings let you organize airports into logical groups (e.g., by sector, region, or facility).

### Location

Groupings are stored in `data/custom_groupings.json`

### Format

```json
{
  "Bay Area": ["KSFO", "KOAK", "KSJC", "KHWD", "KPAO"],
  "SoCal": ["KLAX", "KSAN", "KONT", "KBUR", "KSNA"],
  "California": ["Bay Area", "SoCal"]
}
```

### Nesting Groupings

Groupings can contain other groupings! In the example above, "California" contains both "Bay Area" and "SoCal", which are recursively expanded.

### Preset Groupings

The application includes preset groupings in `data/preset_groupings/` organized by ARTCC. These are loaded automatically.

### ARTCC Groupings

Auto-generated groupings like "ZOA All", "ZLA All" contain all airports in that ARTCC.

### Creating Groupings from UI

1. Use the Tracked Airports Manager (`Ctrl+T`)
2. Add all desired airports
3. Press `S` to save as a custom grouping
4. Enter a name for the grouping

---

## Weather Data Sources

### METAR Source (Default)

```bash
python main.py --wind-source metar
```

- Uses Aviation Weather Center (aviationweather.gov)
- Standard METAR observations
- Updated approximately every hour (or more frequently at busy airports)
- 60-second cache to reduce API calls

### Minute Source

```bash
python main.py --wind-source minute
```

- Uses weather.gov
- Up-to-the-minute wind observations
- More frequent updates than METAR
- Useful for rapidly changing conditions

---

## Visual Features

### Split-Flap Animations

The application features split-flap display animations (like airport departure boards):
- Characters flip through intermediate values when updating
- Different character sets for different columns (ETAs count down, codes flip alphabetically)
- Staggered delays create a wave effect
- Performance optimized (skips off-screen rows)

**Disable animations:**
```bash
python main.py --disable-animations
```

### Progressive Loading

For large airport lists, tables load progressively:
- Shows rows in chunks for faster perceived startup
- Auto-enabled when tracking 50+ airports
- Configurable chunk size

```bash
python main.py --progressive-load --progressive-chunk-size 30
```

---

## Tips & Tricks

### Efficient Monitoring

1. **Use groupings** to organize airports by sector or facility
2. **Enable `--include-all-staffed`** to see positions even at quiet airports
3. **Pause auto-refresh** (`Ctrl+P`) when analyzing specific flights

### Quick Navigation

1. Use `Ctrl+G` (Go To) for fastest navigation to any airport, flight, or grouping
2. Use filter prefixes (`@`, `#`, `$`) to narrow searches
3. Press `Enter` directly from the main table to open flight boards

### Weather Analysis

1. Open sector weather briefings (`Ctrl+B`) for comprehensive coverage
2. Use VFR alternatives finder (`Ctrl+A`) to suggest diversions to VFR pilots
3. Check route weather (`W` from flight info) to anticipate pilot requests

### Performance

1. **Disable animations** for faster updates on slower systems
2. **Use progressive loading** for large airport lists
3. **Filter airports** rather than tracking entire countries for better responsiveness

### Common Workflows

**Sector Controller:**
1. Start with your sector grouping: `python main.py --groupings "NCT A+C"`
2. Use `Ctrl+B` for sector weather briefing
3. Monitor flight boards for specific airports

**Approach Controller:**
1. Track your approach airports: `python main.py --airports KSFO KOAK KSJC`
2. Use `--include-all-staffed` to see when tower/ground positions open
3. Check VFR alternatives when weather deteriorates

**Flight Following:**
1. Use Go To (`Ctrl+G`) to find specific flights by callsign
2. Press `W` to view route weather
3. Press `D` to find diversion airports if needed

---

## Troubleshooting

### "Command not found" or "python not found"

Ensure Python is installed and in your PATH. Try:
```bash
python3 main.py --help
# or on Windows:
py main.py --help
```

### Dependency Installation Fails

The app auto-installs dependencies, but if this fails:

1. **Upgrade Python**: Ensure you're using Python 3.10+ (has pre-built wheels)
2. **Check internet**: Dependencies are downloaded from PyPI
3. **Windows users**: You may need [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) for native compilation
4. **Manual install**: If auto-install fails, you can manually run:
   ```bash
   .venv/Scripts/pip install -r requirements.txt  # Windows
   .venv/bin/pip install -r requirements.txt      # macOS/Linux
   ```

### spaCy Model Download Fails

The app auto-downloads the spaCy model, but if this fails:
```bash
.venv/Scripts/python -m spacy download en_core_web_sm  # Windows
.venv/bin/python -m spacy download en_core_web_sm      # macOS/Linux
```

### Display Issues

- Ensure your terminal supports UTF-8
- Use a modern terminal (Windows Terminal, iTerm2, etc.)
- Try increasing terminal size (minimum 80x24 recommended)
- On Windows, Windows Terminal is strongly recommended over CMD

### No Data Appearing

1. Check internet connectivity
2. VATSIM API may be temporarily unavailable
3. Try manual refresh with `Ctrl+R`
4. Check debug logs in `debug_logs/` directory

### Slow Performance

1. Reduce tracked airports or use specific groupings
2. Enable `--disable-animations`
3. Increase `--refresh-interval` to reduce API calls
4. Use progressive loading for large airport lists

### Debug Logs

Debug information is written to `debug_logs/debug_YYYYMMDD.log`. Logs older than 7 days are automatically cleaned on startup.

---

## Version Information

- **Application**: VATSIM Control Recommendations
- **Framework**: Textual (Terminal UI)
- **Data Sources**: VATSIM API, Aviation Weather Center, weather.gov

For bug reports and feature requests, please open an issue on the project repository.
