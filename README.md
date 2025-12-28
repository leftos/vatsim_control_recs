# VATSIM Control Recommendations

[Discord](https://discord.gg/TR3FSpzvvp)

```text
usage: main.py [-h] [--max-eta-hours MAX_ETA_HOURS] [--refresh-interval REFRESH_INTERVAL] [--airports AIRPORTS [AIRPORTS ...]] [--countries COUNTRIES [COUNTRIES ...]] [--groupings GROUPINGS [GROUPINGS ...]]
               [--include-all-staffed] [--disable-animations] [--progressive-load] [--progressive-chunk-size PROGRESSIVE_CHUNK_SIZE] [--wind-source {metar,minute}]

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
                        List of country codes (e.g., US DE) to include all airports from those countries
  --groupings GROUPINGS [GROUPINGS ...]
                        List of custom grouping names to include in analysis. Groupings are recursively expanded to include all airports and sub-groupings. (default: all)
  --include-all-staffed
                        Include airports with zero planes if they are staffed (default: False)
  --disable-animations  Disable split-flap animations for instant text updates (default: False)
  --progressive-load    Enable progressive loading for faster perceived startup (default: auto for 50+ airports)
  --progressive-chunk-size PROGRESSIVE_CHUNK_SIZE
                        Number of rows to load per chunk in progressive mode (default: 20)
  --wind-source {metar,minute}
                        Wind data source: 'metar' for METAR from aviationweather.gov (default), 'minute' for up-to-the-minute from weather.gov
  --hide-wind           Hide the wind column from the main view (default: False)
  --include-all-arriving
                        Include airports with any arrivals filed, regardless of max-eta-hours (default: False)

```

## Keyboard Shortcuts

While the app is running, you can use these keyboard shortcuts:

- **Ctrl+C**: Quit the application
- **Ctrl+R**: Manually refresh data from VATSIM
- **Ctrl+Space**: Pause/Resume auto-refresh
- **Ctrl+F**: Open search box to filter airports (airports tab only)
- **Ctrl+W**: Wind information lookup
- **Ctrl+E**: METAR lookup
- **Ctrl+T**: Tracked Airports Manager - View all tracked airports and add/remove airports
- **Enter**: Open flight board for selected airport/grouping
- **Escape**: Close modals or cancel search

### Tracked Airports Manager (Ctrl+T)

The **Ctrl+T** keyboard shortcut opens the **Tracked Airports Manager** modal, which provides a comprehensive view of all airports being tracked and allows you to manage them.

**Features:**
- **View All Tracked Airports**: See a complete list of airports currently being tracked, with their full names
- **Select/Deselect Airports**: Use arrow keys to navigate and Space to select airports for removal
- **Remove Selected**: Press Delete or click the "Remove Selected" button to stop tracking selected airports
- **Quick Add/Remove**: Press 'A' or click "Add Airports" to open the quick add/remove dialog

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

**Note:** When you use `--groupings` command-line options, all airports within those groupings are automatically tracked. Groupings are recursively expanded to include all airports and sub-groupings. The groupings are used for display purposes in the Groupings tab, but all tracking and analysis works at the individual airport level.

**This project includes IATA/ICAO List data available from <http://www.ip2location.com>.**

IATA is a registered trademark of International Air Transport Association.  
ICAO is a registered trademark of International Civil Aviation Organization.  
All other product names mentioned on this repository may be trademarks or registered trademarks of their respective companies.  
