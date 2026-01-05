"""
Shared weather parsing constants and utilities.

Used by both the UI weather briefing modal and the weather daemon generator.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

# Category priority for trend comparison (lower = worse conditions)
CATEGORY_PRIORITY = {"LIFR": 0, "IFR": 1, "MVFR": 2, "VFR": 3}

# FAR 139 certification priority (lower = larger/more significant)
# "I E" = Index E (wide-body scheduled service) = Class B airports
# "I D" = Index D (large jets) = major Class C airports
# "I C" = Index C (medium jets) = Class C airports
FAR139_PRIORITY = {
    "I E": 0,  # Class B (KSFO, KLAX, KJFK, etc.)
    "I D": 1,  # Major Class C (KOAK, KSJC, KSAN, etc.)
    "I C": 2,  # Class C
    "I B": 3,  # Smaller scheduled service
    "I A": 4,  # Smallest scheduled service
}

# Tower type priority for sorting (lower = larger/more significant airport)
# Used as fallback when FAR 139 data is not available
# Values start at 5 to sort after FAR 139 airports
TOWER_TYPE_PRIORITY = {
    "ATCT-TRACON": 5,
    "ATCT-RAPCON": 5,
    "ATCT-RATCF": 5,
    "ATCT-A/C": 6,
    "ATCT": 7,
    "NON-ATCT": 8,
    "": 9,
}

# Weather phenomena codes mapping
# Format: code -> (readable name, is_significant)
WEATHER_PHENOMENA = {
    # Precipitation
    "RA": ("Rain", False),
    "SN": ("Snow", True),
    "DZ": ("Drizzle", False),
    "GR": ("Hail", True),
    "GS": ("Small Hail", True),
    "PL": ("Ice Pellets", True),
    "SG": ("Snow Grains", False),
    "IC": ("Ice Crystals", False),
    "UP": ("Unknown Precip", False),
    # Obscuration
    "FG": ("Fog", True),
    "BR": ("Mist", False),
    "HZ": ("Haze", False),
    "FU": ("Smoke", True),
    "VA": ("Volcanic Ash", True),
    "DU": ("Dust", False),
    "SA": ("Sand", False),
    "PY": ("Spray", False),
    # Other
    "SQ": ("Squall", True),
    "FC": ("Funnel Cloud", True),
    "SS": ("Sandstorm", True),
    "DS": ("Duststorm", True),
    "PO": ("Dust Whirls", False),
}

WEATHER_DESCRIPTORS = {
    "MI": "Shallow",
    "PR": "Partial",
    "BC": "Patches",
    "DR": "Drifting",
    "BL": "Blowing",
    "SH": "Showers",
    "TS": "Thunderstorm",
    "FZ": "Freezing",
}

WEATHER_INTENSITY = {
    "-": "Light",
    "+": "Heavy",
    "VC": "Vicinity",
}


def get_airport_size_priority(airport_info: Dict[str, Any]) -> int:
    """Get airport size priority for sorting (lower = more significant).

    Uses FAR 139 certification as primary indicator, falls back to tower type.
    FAR 139 Class I airports (major hubs) sort first.

    Args:
        airport_info: Airport data dict with 'far139' and 'tower_type' keys

    Returns:
        Priority value (lower = more significant airport)
    """
    far139 = airport_info.get("far139", "")
    # Check FAR 139 first (most accurate for major airports)
    if far139 in FAR139_PRIORITY:
        return FAR139_PRIORITY[far139]
    # Fall back to tower type
    tower_type = airport_info.get("tower_type", "")
    return TOWER_TYPE_PRIORITY.get(tower_type, 9)


def parse_visibility_sm(metar: str) -> Optional[float]:
    """
    Parse visibility in statute miles from METAR.

    Args:
        metar: Raw METAR string

    Returns:
        Visibility in statute miles, or None if not found
    """
    if not metar:
        return None

    # Handle visibility patterns in order of specificity:
    # 1. Fractional visibility: "1/2SM", "1 1/2SM", "M1/4SM" (M = less than)
    # 2. Whole number visibility: "10SM", "3SM"
    # 3. Meters visibility (convert to SM): "9999" means 10+ km, "0800" = 800m

    # Pattern for mixed fraction like "1 1/2SM" or "2 1/4SM"
    mixed_frac_match = re.search(r"\b(\d+)\s+(\d+)/(\d+)SM\b", metar)
    if mixed_frac_match:
        whole = int(mixed_frac_match.group(1))
        num = int(mixed_frac_match.group(2))
        den = int(mixed_frac_match.group(3))
        return whole + (num / den)

    # Pattern for "M1/4SM" (less than 1/4 mile)
    less_than_frac_match = re.search(r"\bM(\d+)/(\d+)SM\b", metar)
    if less_than_frac_match:
        num = int(less_than_frac_match.group(1))
        den = int(less_than_frac_match.group(2))
        # Return slightly less than the fraction for "less than" indicator
        return (num / den) - 0.01

    # Pattern for simple fraction like "1/2SM", "3/4SM"
    frac_match = re.search(r"\b(\d+)/(\d+)SM\b", metar)
    if frac_match:
        num = int(frac_match.group(1))
        den = int(frac_match.group(2))
        return num / den

    # Pattern for whole number like "10SM", "3SM", "P6SM" (P = plus/more than 6)
    whole_match = re.search(r"\b[PM]?(\d+)SM\b", metar)
    if whole_match:
        vis = int(whole_match.group(1))
        # P6SM means greater than 6 miles - treat as good visibility
        if "P" in metar and f"P{vis}SM" in metar:
            return vis + 1  # Slightly more than indicated
        return float(vis)

    # Pattern for meters (4-digit): "9999" = 10km+, "0800" = 800m
    # This appears after the wind in METAR, before clouds
    meters_match = re.search(r"\b(\d{4})\b(?!\d)", metar)
    if meters_match:
        # Make sure it's not a time or other number
        meters_str = meters_match.group(1)
        meters = int(meters_str)
        # Visibility in meters is typically 0000-9999
        # 9999 means visibility >= 10km
        if meters == 9999:
            return 7.0  # Greater than 6 SM
        # Convert meters to statute miles (1 SM = 1609.34 meters)
        return meters / 1609.34

    return None


def parse_ceiling_feet(metar: str) -> Optional[int]:
    """
    Parse ceiling (lowest BKN or OVC layer) from METAR.

    Args:
        metar: Raw METAR string

    Returns:
        Ceiling in feet AGL, or None if clear/no ceiling
    """
    if not metar:
        return None

    # Cloud layer patterns: FEW015, SCT025, BKN035, OVC050, VV003 (vertical visibility)
    # Heights are in hundreds of feet AGL
    # Ceiling is the lowest BKN (broken), OVC (overcast), or VV (vertical visibility/obscured)

    cloud_pattern = r"\b(BKN|OVC|VV)(\d{3})\b"
    matches = re.findall(cloud_pattern, metar)

    if not matches:
        return None  # No ceiling (clear, few, or scattered only)

    # Find the lowest ceiling layer
    lowest_ceiling = None
    for _layer_type, height_str in matches:
        height_feet = int(height_str) * 100
        if lowest_ceiling is None or height_feet < lowest_ceiling:
            lowest_ceiling = height_feet

    return lowest_ceiling


def parse_ceiling_layer(metar: str) -> Optional[str]:
    """
    Parse ceiling layer from METAR and return in METAR format.

    Args:
        metar: Raw METAR string

    Returns:
        Ceiling layer string (e.g., "BKN004", "OVC010", "VV002") or None if no ceiling
    """
    if not metar:
        return None

    # Cloud layer patterns: BKN035, OVC050, VV003 (vertical visibility)
    cloud_pattern = r"\b(BKN|OVC|VV)(\d{3})\b"
    matches = re.findall(cloud_pattern, metar)

    if not matches:
        return None

    # Find the lowest ceiling layer
    lowest_layer = None
    lowest_height = None
    for layer_type, height_str in matches:
        height_feet = int(height_str) * 100
        if lowest_height is None or height_feet < lowest_height:
            lowest_height = height_feet
            lowest_layer = f"{layer_type}{height_str}"

    return lowest_layer


def extract_visibility_str(metar: str) -> Optional[str]:
    """
    Extract the visibility string verbatim from METAR.

    Args:
        metar: Raw METAR string

    Returns:
        Visibility string as it appears in METAR (e.g., "2SM", "1/2SM", "M1/4SM", "P6SM")
        or None if not found
    """
    if not metar:
        return None

    # Match visibility patterns in order of specificity:
    # Mixed fraction: "1 1/2SM", "2 1/4SM"
    mixed_match = re.search(r"\b(\d+\s+\d+/\d+SM)\b", metar)
    if mixed_match:
        return mixed_match.group(1)

    # Less than fraction: "M1/4SM"
    less_than_match = re.search(r"\b(M\d+/\d+SM)\b", metar)
    if less_than_match:
        return less_than_match.group(1)

    # Simple fraction: "1/2SM", "3/4SM"
    frac_match = re.search(r"\b(\d+/\d+SM)\b", metar)
    if frac_match:
        return frac_match.group(1)

    # Whole number with optional P prefix: "10SM", "P6SM"
    whole_match = re.search(r"\b(P?\d+SM)\b", metar)
    if whole_match:
        return whole_match.group(1)

    return None


def extract_flight_rules_weather(metar: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract visibility and ceiling strings relevant to flight rules from METAR.

    Args:
        metar: Raw METAR string

    Returns:
        Tuple of (visibility_str, ceiling_str) as they appear in METAR,
        e.g., ("2SM", "BKN004"). Either may be None if not present.
    """
    return (extract_visibility_str(metar), parse_ceiling_layer(metar))


def parse_wind_from_metar(metar: str) -> Optional[str]:
    """
    Extract wind string from METAR.

    Args:
        metar: Raw METAR string

    Returns:
        Wind string (e.g., "28012G18KT", "VRB05KT", "00000KT") or None
    """
    if not metar:
        return None

    wind_pattern = r"\b(\d{3}|VRB)(\d{2,3})(G\d{2,3})?(KT|MPS|KMH)\b"
    match = re.search(wind_pattern, metar)
    if match:
        return match.group(0)
    return None


def parse_metar_obs_time(metar: str) -> Optional[str]:
    """
    Extract observation time from METAR in DDHHMM format.

    METAR format: ICAO DDHHMMZ ... (e.g., "KSFO 061756Z ...")
    Returns the time portion without Z suffix.

    Args:
        metar: Raw METAR string

    Returns:
        Observation time string like "061756" (day 06, 17:56Z) or None
    """
    if not metar:
        return None

    # Match 6-digit Zulu time after the ICAO code
    time_pattern = r"\b(\d{6})Z\b"
    match = re.search(time_pattern, metar)
    if match:
        return match.group(1)
    return None


def is_speci_metar(metar: str) -> bool:
    """
    Check if a METAR is a SPECI (special) report.

    SPECI METARs are issued when significant weather changes occur between
    regular hourly observations. They indicate important changes like:
    - Visibility dropping below or rising above minima
    - Ceiling changes
    - Onset or cessation of precipitation
    - Thunderstorms or other significant weather

    Args:
        metar: Raw METAR string

    Returns:
        True if this is a SPECI report, False otherwise
    """
    if not metar:
        return False

    # SPECI appears at the start of the report, possibly after whitespace
    # Common formats:
    # - "SPECI KSFO 031756Z..."
    # - "KSFO 031756Z... SPECI" (less common but possible)
    metar_upper = metar.strip().upper()
    return metar_upper.startswith("SPECI") or " SPECI " in metar_upper


def format_obs_time_display(obs_time: str) -> str:
    """
    Format observation time for display.

    Args:
        obs_time: Time string like "061756" (DDHHMM format)

    Returns:
        Formatted string like "1756Z" (just HHMM)
    """
    if not obs_time or len(obs_time) != 6:
        return obs_time or ""

    # Return just HHMM portion with Z suffix
    return f"{obs_time[2:]}Z"


def _parse_single_weather(code: str) -> List[str]:
    """
    Parse a single weather code into human-readable format.

    Args:
        code: Weather code like "-RA", "+TSRA", "FZFG", "VCSH"

    Returns:
        List of weather descriptions (usually 1, but can be more for combined phenomena)
    """
    if not code:
        return []

    results = []
    original = code
    intensity = ""
    descriptor = ""

    # Check for intensity prefix
    if code.startswith("-"):
        intensity = "Light "
        code = code[1:]
    elif code.startswith("+"):
        intensity = "Heavy "
        code = code[1:]
    elif code.startswith("VC"):
        intensity = "Vicinity "
        code = code[2:]

    # Check for descriptor (2-letter codes that modify phenomena)
    for desc_code, desc_name in WEATHER_DESCRIPTORS.items():
        if code.startswith(desc_code):
            descriptor = desc_name + " "
            code = code[len(desc_code) :]
            break

    # Handle thunderstorm specially - it's both descriptor and phenomenon
    if "TS" in original and descriptor == "Thunderstorm ":
        results.append(f"{intensity}Thunderstorm")
        intensity = ""  # Don't double-apply intensity
        descriptor = ""

    # Parse remaining phenomena (2-letter codes, can be multiple like RAGR)
    while len(code) >= 2:
        phenomenon_code = code[:2]
        if phenomenon_code in WEATHER_PHENOMENA:
            name, _ = WEATHER_PHENOMENA[phenomenon_code]
            full_name = f"{intensity}{descriptor}{name}".strip()
            if full_name and full_name not in results:
                results.append(full_name)
            # Reset intensity/descriptor after first use
            intensity = ""
            descriptor = ""
            code = code[2:]
        else:
            # Unknown code, skip
            break

    return results


def parse_weather_phenomena(metar: str) -> List[str]:
    """
    Parse weather phenomena from METAR string.

    Args:
        metar: Raw METAR string

    Returns:
        List of human-readable weather phenomena (e.g., ["Light Rain", "Mist", "Thunderstorm"])
    """
    if not metar:
        return []

    phenomena = []

    # Weather phenomena appear after wind and visibility, before clouds
    # Pattern: optional intensity (-/+/VC) + optional descriptor + one or more weather types
    # Examples: -RA, +TSRA, VCSH, FZFG, -SHRA, +TSRAGR

    # Split METAR into parts
    parts = metar.split()

    for part in parts:
        # Skip parts that are clearly not weather (timestamps, wind, visibility, clouds, temps)
        if re.match(r"^\d{6}Z$", part):  # Timestamp
            continue
        if re.match(r"^\d{5}(G\d+)?KT$", part):  # Wind
            continue
        if re.match(r"^(VRB)?\d{3}V\d{3}$", part):  # Variable wind
            continue
        if re.match(r"^[PM]?\d+SM$", part):  # Visibility
            continue
        if re.match(r"^\d+/\d+SM$", part):  # Fractional visibility
            continue
        if re.match(r"^(SKC|CLR|FEW|SCT|BKN|OVC|VV)\d{3}", part):  # Clouds
            continue
        if re.match(r"^[AM]?\d{2}/[AM]?\d{2}$", part):  # Temp/dewpoint
            continue
        if re.match(r"^A\d{4}$", part):  # Altimeter
            continue
        if re.match(r"^Q\d{4}$", part):  # QNH
            continue
        if re.match(r"^RMK", part):  # Start of remarks - stop processing
            break

        # Try to parse as weather phenomenon
        parsed = _parse_single_weather(part)
        if parsed:
            phenomena.extend(parsed)

    return phenomena


def get_flight_category(metar: str) -> str:
    """
    Determine flight category from METAR conditions.

    FAA Flight Categories:
    - VFR: Ceiling > 3000 ft AND visibility > 5 SM
    - MVFR: Ceiling 1000-3000 ft AND/OR visibility 3-5 SM
    - IFR: Ceiling 500-999 ft AND/OR visibility 1-<3 SM
    - LIFR: Ceiling < 500 ft AND/OR visibility < 1 SM

    Args:
        metar: Raw METAR string

    Returns:
        Category string: VFR, MVFR, IFR, LIFR, or UNK
    """
    if not metar:
        return "UNK"

    visibility = parse_visibility_sm(metar)
    ceiling = parse_ceiling_feet(metar)

    # Determine category based on most restrictive condition
    # Start with VFR and downgrade based on conditions

    vis_category = "VFR"
    if visibility is not None:
        if visibility < 1:
            vis_category = "LIFR"
        elif visibility < 3:
            vis_category = "IFR"
        elif visibility <= 5:
            vis_category = "MVFR"
        else:
            vis_category = "VFR"

    ceil_category = "VFR"
    if ceiling is not None:
        if ceiling < 500:
            ceil_category = "LIFR"
        elif ceiling < 1000:
            ceil_category = "IFR"
        elif ceiling <= 3000:
            ceil_category = "MVFR"
        else:
            ceil_category = "VFR"

    # Use the most restrictive category
    if CATEGORY_PRIORITY.get(vis_category, 3) < CATEGORY_PRIORITY.get(ceil_category, 3):
        return vis_category
    else:
        return ceil_category


# Backward-compatible aliases with underscore prefix (deprecated)
_parse_visibility_sm = parse_visibility_sm
_parse_ceiling_feet = parse_ceiling_feet
_parse_ceiling_layer = parse_ceiling_layer
_extract_visibility_str = extract_visibility_str
_extract_flight_rules_weather = extract_flight_rules_weather
