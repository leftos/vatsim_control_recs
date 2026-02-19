# D-ATIS Fetching, METAR Filtering, and Runway Extraction

This document describes how the application fetches D-ATIS (Digital ATIS) text from both VATSIM and real-world sources, strips embedded METAR weather data, and extracts active runway operations.

---

## 1. Data Sources

### VATSIM ATIS

**Module:** `backend/data/vatsim_api.py` - `get_atis_for_airports()`

VATSIM ATIS data is extracted from the main VATSIM v2 API response (`vatsim_data["atis"]`). Each ATIS entry includes a `text_atis` field that is an **array of line strings**, which are joined into a single string:

```python
raw_lines = atis.get("text_atis") or []
text_atis = " ".join(line.strip() for line in raw_lines)
```

**ATIS type detection** is based on the callsign pattern:

| Callsign Pattern | Type |
|---|---|
| `ICAO_ATIS` | `combined` |
| `ICAO_D_ATIS`, `ICAO_DEP_ATIS` | `departure` |
| `ICAO_A_ATIS`, `ICAO_ARR_ATIS` | `arrival` |

Cache duration: 15 seconds.

### Real-World D-ATIS

**Module:** `backend/data/datis_api.py` - `get_datis_for_airport()` / `get_datis_for_airports()`

Real-world D-ATIS is fetched from the **atis.info** API (`https://atis.info/api/{ICAO}`). This covers **US airports only** (K-prefix for continental US, P-prefix for Pacific/Hawaii).

The API returns an array of ATIS entries with type codes mapped as:
- `"arr"` -> `"arrival"`
- `"dep"` -> `"departure"`
- All others -> `"combined"`

The `text_atis` from this source is already a single string. Cache duration: 60 seconds. Fetches for multiple airports are parallelized via `ThreadPoolExecutor`.

### Fallback Logic

Both sources are normalized to a common structure with a `source` field (`"vatsim"` or `"rw"`). The application **prefers VATSIM ATIS** and falls back to real-world D-ATIS only for airports that don't have a VATSIM ATIS active:

```python
atis_data = get_atis_for_airports(vatsim_data, airports)          # Try VATSIM first
airports_without_vatsim_atis = [icao for icao in airports if icao not in atis_data]
rw_atis_data = get_datis_for_airports(airports_without_vatsim_atis)  # Fall back to RW
atis_data.update(rw_atis_data)
```

---

## 2. METAR Removal

**Module:** `backend/data/atis_filter.py` - `filter_atis_text()`

A typical ATIS broadcast contains duplicated METAR weather information followed by operational information (approaches, runways, NOTAMs). Since METAR data is available separately, the app strips it from the ATIS text to surface just the operational content.

### Strategy

1. **Detect US-style ATIS** by looking for an altimeter + spoken form pattern: `A3013 (THREE ZERO ONE THREE)`
2. **Find the boundary** between the METAR portion and the operational portion
3. **Apply METAR removal patterns only to the METAR portion**, leaving operational info intact
4. **Recombine** the cleaned METAR header with the untouched operational portion

For non-US or non-standard ATIS formats, the text is returned with only minimal whitespace cleanup to avoid accidentally removing operational information.

### Boundary Detection

`_find_metar_ops_boundary()` locates where operational info begins by:

1. Finding the altimeter + spoken form pattern (e.g., `A3013 (THREE ZERO ONE THREE)`)
2. Optionally skipping an `RMK` (remarks) section after the altimeter
3. Looking for a period followed by an operational keyword

**Operational keywords** that mark the boundary:
```
APPROACH, APCH, ARR, DEP, SIMUL, ILS, RNAV, VISUAL,
LDG, LANDING, RWY, RUNWAY, NOTAM, TAXIWAY, CAUTION, CONTACT, ...
```

### Removal Patterns

The patterns that get stripped from the METAR portion are defined in `METAR_REMOVAL_PATTERNS` and draw from shared constants in `backend/data/weather_parsing.py`:

| Category | Examples Removed |
|---|---|
| **Wind** | `27005KT`, `VRB05KT`, `27015G25KT`, `240V300` |
| **Visibility** | `10SM`, `3/4SM`, `1 1/2SM`, `VIS 10`, `9999` |
| **Clouds** | `FEW015`, `SCT025`, `BKN050`, `OVC100`, `SKY CLEAR`, `CLR` |
| **Temp/Dewpoint** | `08/08`, `M03/M07`, `T02 DP00` |
| **Altimeter** | `A2992 (TWO NINER...)`, `Q1013`, `QNH1013` |
| **Weather phenomena** | `-RA`, `+TSRA`, `BR`, `FG`, `FZFG`, `VCSH` |
| **METAR metadata** | `WEATHER AT 0100Z`, `OBSERVED AT 0130`, `RMK AO2 SLP...` |
| **TDZ winds** | `WIND RWY 28 TDZ`, `TDZ 27005KT` |
| **Trends** | `TREND TEMPO...`, `TREND BECMG...` |

After pattern removal, a final cleanup pass normalizes whitespace, removes empty parentheses, collapses multiple periods, and cleans up orphaned punctuation.

---

## 3. Runway & Approach Extraction

**Module:** `backend/data/atis_filter.py` - `parse_approach_info()` / `parse_runway_assignments()`

The parser extracts three things from the ATIS text:
- **Landing runways** (set of designators like `{"16L", "17R"}`)
- **Departing runways** (set of designators like `{"26L", "27R"}`)
- **Approach types per runway** (dict like `{"16L": {"ILS", "RNAV"}, "17R": {"VISUAL"}}`)

### Shared Pattern Components

```python
RWY_NUM_PATTERN  # Matches: "17R", "17 R", "17R AND LEFT", "10L OR 10R"
APPROACH_TYPES   # ILS, RNAV, VISUAL, VIS, VA, LOC, VOR, NDB, GPS, LDA, SDF, RNP
RWY_PREFIX       # RWY, RWYS, RUNWAY, RUNWAYS
LANDING_KW       # LANDING, LDG, LNDG, ARRIVING, ARR
DEPARTING_KW     # DEPARTING, DEPARTURES, DEPTG, DEPG, DEP
```

### Detection Patterns (7 patterns)

| # | Pattern | Example Input | Extracted |
|---|---|---|---|
| 1 | **Compound LDG/DEPTG** | `"LDG/DEPTG 4/8"`, `"ARR/DEP RWY 36"` | Both landing + departing |
| 2 | **Landing runways** | `"LDG RWY 16L"`, `"ARR RWY 35"` | Landing only |
| 3 | **Departing runways** | `"DEP RWY 16R"`, `"DEPTG RWYS 26L, 27R"` | Departing only |
| 4 | **Approach type + runway** | `"ILS RWY 22R"`, `"RNAV-Y RWY 35"`, `"VISUAL APPROACH RWY 26"` | Landing + approach type |
| 5 | **EXPECT approach** | `"EXPECT ILS RWY 35L"`, `"ARRIVALS EXPECT ILS APCH RWY 10L"` | Landing + approach type |
| 6 | **RWY FOR ARR/DEP** (intl.) | `"RWY 03 FOR ARR"`, `"RWY 06 FOR DEP"` | Landing or departing |
| 7 | **SIMUL/INSTR operations** | `"SIMUL DEPARTURES RWYS 24 AND 25"` | Landing or departing |

### Runway Number Extraction

`_extract_runway_numbers()` handles various formats including spoken directional forms:

- `"17R"` -> `{"17R"}`
- `"17R AND LEFT"` -> `{"17R", "17L"}` (spoken "LEFT" maps to last number's `L` suffix)
- `"28L, 28R"` -> `{"28L", "28R"}`
- `"17 RIGHT AND LEFT"` -> `{"17R", "17L"}`

The suffix map: `LEFT` -> `L`, `RIGHT` -> `R`, `CENTER` -> `C`.

### Format Summary

`format_runway_summary()` produces compact display strings:

- Same runways for both ops: `"L/D:27"` or `"L/D:16L,16R"`
- Different runways: `"L:16L,17R D:26L,27R"`

---

## 4. Display Colorization

**Module:** `backend/data/atis_filter.py` - `colorize_atis_text()`

The filtered ATIS text is colorized with [Rich](https://github.com/Textualize/rich) markup for display in the TUI:

- **ATIS letter** -> `[cyan bold]` (both the phonetic word like "KILO" and standalone letter after INFORMATION/INFO/ATIS)
- **Approach types, runways, and operational phrases** -> `[yellow]` (10+ patterns covering all approach/runway formats)

Nested yellow tags are cleaned up in a loop to prevent overlapping markup.

---

## 5. Downstream Usage

### Flight Board (`ui/modals/flight_board.py`)

The flight board uses the parsed runway and approach data for **change detection**:

1. On initial load (`_initialize_weather_and_runways`), it fetches ATIS for all displayed airports, parses runway assignments and approach types, and stores them as a baseline.
2. On each refresh (`_check_for_changes`), it re-fetches and re-parses, then compares against the baseline using frozensets.
3. If landing/departing runways or approach types have changed, it fires a notification to the user.

Dual ATIS (separate departure + arrival) is handled by combining data from both entries.

Normalization rule: if only landing runways are listed (no departing), they're assumed to be the same for departing when comparing.

### METAR/ATIS Lookup (`ui/modals/metar_info.py`)

Displays filtered + colorized ATIS text. Shows source indicator `(RW)` for real-world D-ATIS. Handles dual ATIS by labeling entries with `DEP:` or `ARR:` prefixes.

Fallback: VATSIM ATIS -> Real-world D-ATIS from atis.info.

### Weather Briefing (`ui/modals/weather_briefing.py`)

Batch-fetches ATIS for multiple airports and displays filtered text alongside METAR data in the briefing view.

### Flight Briefing (`ui/modals/flight_briefing.py`)

Shows relevant ATIS information as part of a pilot briefing for a specific flight.

---

## 6. Data Flow Diagram

```
                    VATSIM API (v2)                    atis.info API
                         |                                  |
              download_vatsim_data()              get_datis_for_airports()
                         |                                  |
              get_atis_for_airports()              Parallel fetch via
              (parse callsign for type,            ThreadPoolExecutor
               join text_atis lines)                        |
                         |                                  |
                         v                                  v
                   {source: "vatsim"}                {source: "rw"}
                         |                                  |
                         +---> Prefer VATSIM <---+          |
                         |     Fall back to RW   |----------+
                         v
              atis_data: Dict[ICAO, List[ATIS]]
                         |
              +----------+----------+
              |                     |
              v                     v
      filter_atis_text()    parse_approach_info()
              |                     |
              v                     v
      Cleaned text           {landing, departing,
      (METAR stripped)        approaches}
              |                     |
              v                     v
      colorize_atis_text()   Change detection
              |              (frozenset comparison)
              v                     |
      Display in TUI         Runway/approach change
      (Rich markup)          notifications
```
