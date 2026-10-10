# VATSIM Control Recommendations

[Discord](https://discord.gg/TR3FSpzvvp)

A terminal-based application that analyzes live VATSIM flight data and provides controller staffing recommendations. Displays real-time airport statistics including departures, arrivals, ETAs, wind/weather, and staffed ATC positions.

Built with the [Textual](https://textual.textualize.io/) TUI framework.

## Quick Start

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
git clone https://github.com/your-repo/vatsim_control_recs.git
cd vatsim_control_recs
uv run main.py
```

On first run, `uv run` fetches Python 3.13 if needed, creates `.venv` and installs the locked dependencies from `uv.lock`, including the spaCy language model. Subsequent launches start immediately.

See the [User Guide](USER_GUIDE.md) for detailed usage instructions, and [`docs/README.md`](docs/README.md) for the developer docs and a glossary of the project's terms.

## Usage

```text
usage: main.py [-h] [--max-eta-hours MAX_ETA_HOURS]
               [--refresh-interval REFRESH_INTERVAL]
               [--airports AIRPORTS [AIRPORTS ...]]
               [--countries COUNTRIES [COUNTRIES ...]]
               [--groupings GROUPINGS [GROUPINGS ...]]
               [--include-all-staffed] [--disable-animations]
               [--wind-source {metar,minute}] [--hide-wind]
               [--include-all-arriving]

Analyze VATSIM flight data and controller staffing

options:
  -h, --help            show this help message and exit
  --max-eta-hours MAX_ETA_HOURS
                        Maximum ETA in hours for arrival filter (default: 1.0)
  --refresh-interval REFRESH_INTERVAL
                        Auto-refresh interval in seconds (default: 15)
  --airports AIRPORTS [AIRPORTS ...]
                        List of airport ICAO codes to include in analysis (default: all)
  --countries COUNTRIES [COUNTRIES ...]
                        List of country codes (e.g., US DE) to include all airports
                        from those countries
  --groupings GROUPINGS [GROUPINGS ...]
                        List of custom grouping names to include in analysis.
                        Groupings are recursively expanded to include all airports
                        and sub-groupings. (default: all)
  --include-all-staffed
                        Include airports with zero planes if they are staffed
                        (default: False)
  --disable-animations  Disable split-flap animations for instant text updates
                        (default: False)
  --wind-source {metar,minute}
                        Wind data source: 'metar' for METAR from aviationweather.gov
                        (default), 'minute' for up-to-the-minute from weather.gov
  --hide-wind           Hide the wind column from the main view (default: False)
  --include-all-arriving
                        Include airports with any arrivals filed, regardless of
                        max-eta-hours (default: False)
```

### Examples

```bash
# Track specific airports
uv run main.py --airports KSFO KLAX KJFK KORD

# Track all airports within an ARTCC, including separate arrival counts for time-limited ETA and all arrivals
uv run main.py --groupings "ZOA All" --include-all-arriving

# Track all US airports with staffed positions shown
uv run main.py --countries US --include-all-staffed

# Track a custom grouping with no animations
uv run main.py --groupings "Bay Area" --disable-animations

# Show arrivals up to 2 hours out with minute-by-minute wind
uv run main.py --max-eta-hours 2.0 --wind-source minute

# Combine filters
uv run main.py --airports KSFO --groupings "SoCal" --include-all-arriving
```

## Keyboard Shortcuts

### Main View

| Shortcut | Action |
|----------|--------|
| `Ctrl+Z` | Quit the application |
| `Ctrl+P` | Pause/Resume auto-refresh |
| `Ctrl+F` | Search/filter airports (airports tab only) |
| `Ctrl+G` / `Ctrl+L` | Go To - unified search for airports, flights, groupings |
| `Ctrl+E` | METAR lookup |
| `Ctrl+W` | Wind information lookup |
| `Ctrl+A` | VFR alternatives finder |
| `Ctrl+B` | Weather briefing |
| `Ctrl+S` | Historical flight statistics |
| `Ctrl+T` | Tracked Airports Manager |
| `Enter` | Open flight board for selected airport/grouping |
| `Escape` | Close modals or cancel search |
| `F1` / `?` | Help screen |
| `F2` | Command palette |
| `Tab` | Switch between Airports and Groupings tabs |

### Flight Board

| Shortcut | Action |
|----------|--------|
| `Enter` | Open detailed flight information |
| `Escape` / `Q` | Close flight board |

### Flight Info

| Shortcut | Action |
|----------|--------|
| `C` | Copy route to clipboard |
| `D` | Find diversion airports |
| `W` | View route weather |
| `Escape` / `Q` | Close |

### Go To Modal

| Shortcut | Action |
|----------|--------|
| `Tab` | Toggle pick several (multi-select) mode |
| `Space` | Pick or unpick the highlighted row (pick several) |
| `Ctrl+Enter` | Open the board for the picks (pick several) |
| `Ctrl+S` | Open the grouping builder on the picks and their filters (pick several) |
| `Ctrl+D` | Delete the highlighted ★ grouping, after a confirm |
| `E` | Open the grouping builder on the highlighted ★ grouping |
| `F` | Cycle the dep/arr filter of a picked row |
| `→` | Expand a picked grouping into its airports (pick several) |
| `←` | Collapse a picked grouping's airports |
| `@` prefix | Search airports only |
| `#` prefix | Search flights only |
| `$` prefix | Search groupings only |

### Grouping Builder

Opened from Go To (`E` on a ★ grouping, `Ctrl+S` in pick several) and from the Tracked Airports Manager (`S`).

| Shortcut | Action |
|----------|--------|
| `Tab` | Move between the Name field and the panes (from 110 columns); narrower, flip between Members and Add |
| `Enter` | Add the highlighted Add result to the members |
| `Del` | Remove the highlighted member (or exclude one airport of an expanded nested grouping) |
| `F` | Cycle the highlighted airport's filter (both, dep, arr) |
| `→` / `←` | Expand / collapse a nested grouping's airports |
| `Ctrl+S` | Save |
| `Esc` | Cancel; asks first when you changed something |

### Tracked Airports Manager (`Ctrl+T`)

The Tracked Airports Manager provides a comprehensive view of all airports being tracked and allows you to manage them.

**Features:**
- **View All Tracked Airports**: See a complete list of airports currently being tracked, with their full names
- **Select/Deselect Airports**: Use arrow keys to navigate and Space or Enter to pick airports
- **Remove Selected**: Press Delete or click the "Remove Selected" button to stop tracking selected airports
- **Quick Add/Remove**: Press 'A' or click "Add Airports" to open the quick add/remove dialog
- **Save as Grouping**: Press 'S' to open the grouping builder on the picked airports (or the whole tracked set when none is picked)

**Quick Add/Remove Dialog:**
Within the Tracked Airports Manager, you can press 'A' to open a quick dialog for adding or removing airports:
- Enter a space-separated list of airport ICAO codes with `+` or `-` prefixes
- Press `Enter` to apply changes
- Press `Escape` to cancel

**Examples:**
- `+KSFO +KOAK` - Add tracking for San Francisco Intl and Oakland Intl
- `-KSJC -KMRY` - Remove tracking for San Jose Intl and Monterey Regional
- `+KSFO +KOAK -KSJC -KMRY` - Add KSFO and KOAK, remove KSJC and KMRY

The app will automatically refresh with the updated airport list after applying changes.

## Features

### Go To Navigation (`Ctrl+G`)

Unified search modal for navigating to airports, flights, or groupings. Supports multi-select mode (Tab) to open multiple targets at once, and per-airport dep/arr filtering (F key). Your own groupings (built with Ctrl+S or the Tracked Airports Manager's S) appear with a star prefix; E edits one in the grouping builder and Ctrl+D deletes it after a confirm.

### Grouping Builder

A screen for naming a ★ grouping, adding airports and other groupings from a search over every airport, and setting each airport's dep/arr filter. Two panes sit side by side from 110 columns. Saving offers to add any untracked airports to your tracked airports.

### Weather Briefing (`Ctrl+B`)

Comprehensive weather briefings for airports, groupings, and flights. Includes METAR, TAF, ATIS, approach information (with SIMUL ILS highlighting), runway assignments, and flight category summaries. Press `P` to export as HTML and open in your browser.

### Historical Statistics (`Ctrl+S`)

View historical traffic patterns between airports using data from statsim.net. Requires a free API key:
1. Get a key at https://statsim.net/api-keys
2. Create a `.env` file in the application directory with: `STATSIM_API_KEY=your-key-here`

### Diversion Airport Finder

From flight info (press `D`), find suitable diversion airports near the aircraft's current position with weather category, distance, ETA, runway info, and ATC staffing.

### VFR Alternatives (`Ctrl+A`)

Find VFR or MVFR airports near a specified location, with flight category, distance, and direction.

### Custom Groupings

Organize airports into logical groups in `data/custom_groupings.json`:

```json
{
  "Bay Area": ["KSFO", "KOAK", "KSJC"],
  "SoCal": ["KLAX", "KSAN", "KONT"],
  "California": ["Bay Area", "SoCal"]
}
```

Groupings can nest other groupings (recursively expanded). ARTCC-based preset groupings are also loaded automatically.

**Note:** When you use `--groupings` command-line options, all airports within those groupings are automatically tracked. Groupings are recursively expanded to include all airports and sub-groupings. The groupings are used for display purposes in the Groupings tab, but all tracking and analysis works at the individual airport level.

---

**This project includes IATA/ICAO List data available from <http://www.ip2location.com>.**

IATA is a registered trademark of International Air Transport Association.
ICAO is a registered trademark of International Civil Aviation Organization.
All other product names mentioned on this repository may be trademarks or registered trademarks of their respective companies.
