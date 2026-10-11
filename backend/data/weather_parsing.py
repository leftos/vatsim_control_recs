"""
Shared weather parsing constants and utilities.

Used by both the UI weather briefing modal and the weather daemon generator.
"""

import re
from typing import Any

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

# =============================================================================
# METAR component patterns - shared between weather parsing and ATIS filtering
# =============================================================================

# Patterns for matching tokens (anchored, for token matching in parse_weather_phenomena)
# Patterns for removal from text (word boundary, for ATIS filter)

# --- Visibility patterns ---
VISIBILITY_TOKEN_PATTERNS = [
    r"^[PM]?\d+SM$",  # Whole number: 10SM, P6SM, M10SM
    r"^M?\d+/\d+SM$",  # Fractional: 3/4SM, M1/4SM
    r"^\d+$",  # Standalone number from mixed fraction like "1" in "1 1/2SM"
]

VISIBILITY_REMOVAL_PATTERNS = [
    r"\bVIS\s+\d+(\s+\d+)?/\d+\s*(KM)?\s*",  # VIS 3/4, VIS 1 1/2 (fractional)
    r"\bVIS\s+\d+\s*(KM)?\s*",  # VIS 10, VIS 10KM (whole number)
    r"\b\d+\s+\d+/\d+SM\b\s*",  # 1 1/2SM (mixed fraction)
    r"\bM?\d+/\d+SM\b\s*",  # 3/4SM, M1/4SM (simple/less-than fraction)
    r"\b\d+SM\b\s*",  # 10SM (whole number)
    r"\b9999\b\s*",  # 9999 (meters, 10km+)
]

# --- Wind patterns ---
WIND_TOKEN_PATTERNS = [
    r"^(\d{3}|VRB)\d{2,3}(G\d{2,3})?KT$",  # Wind: 27005KT, VRB05KT, 27015G25KT
    r"^\d{3}V\d{3}$",  # Variable wind direction: 240V300
]

WIND_REMOVAL_PATTERNS = [
    r"\b(\d{3}|VRB)\d{2,3}(G\d{2,3})?KT\b(\s+\d{3}V\d{3})?\s*",  # Wind with optional variable
]

# --- Cloud patterns ---
CLOUD_TOKEN_PATTERNS = [
    r"^(SKC|CLR|FEW|SCT|BKN|OVC|VV)\d{0,3}(CB|TCU)?$",  # Cloud layers with optional CB/TCU
]

CLOUD_REMOVAL_PATTERNS = [
    r"\b(FEW|SCT|BKN|OVC|VV)\d{3}(///)?(\s+(FEW|SCT|BKN|OVC|VV)\d{3}(///)?)*\s*",
    r"\bSKY\s+CLEAR\b\s*",
    r"\bCLR\b\s*",
    r"\bCAVOK\b\s*",
]

# --- Temperature/Dewpoint patterns ---
TEMP_DEWPOINT_TOKEN_PATTERNS = [
    r"^[AM]?\d{2}/[AM]?\d{2}$",  # Standard: 08/08, M03/M07
]

TEMP_DEWPOINT_REMOVAL_PATTERNS = [
    r"\bM?\d{2}/M?\d{2}\b\s*",  # Standard: 08/08, M03/M07
    r"\bT\d{2}\s+DPM?\d{2}\b\s*",  # Spoken: T02 DP00, T02 DPM02
    r"\b\d{2}\s+\d{2}\b(?=\s+[AQ]\d{4}|\s*\.|\s*$)\s*",  # "26 22" before altimeter
]

# --- Altimeter patterns ---
ALTIMETER_TOKEN_PATTERNS = [
    r"^[AQ]\d{4}$",  # Altimeter: A2992, Q1013
]

ALTIMETER_REMOVAL_PATTERNS = [
    r"\b[AQ]\d{4}\b\s*(\([A-Z\s]+\))?(-?HPA)?\.?\s*",  # With optional spoken
    r"\bQNH\d{3,4}\b\s*",  # QNH format
]


def _matches_any_pattern(token: str, patterns: list) -> bool:
    """Check if a token matches any pattern in the list."""
    return any(re.match(pattern, token) for pattern in patterns)


def is_visibility_token(token: str) -> bool:
    """
    Check if a token is a visibility value.

    Used by parse_weather_phenomena to skip visibility parts when parsing weather.

    Args:
        token: A whitespace-separated token from a METAR string

    Returns:
        True if the token is a visibility value, False otherwise
    """
    return _matches_any_pattern(token, VISIBILITY_TOKEN_PATTERNS)


def is_wind_token(token: str) -> bool:
    """Check if a token is a wind value (e.g., 27005KT, VRB05KT, 240V300)."""
    return _matches_any_pattern(token, WIND_TOKEN_PATTERNS)


def is_cloud_token(token: str) -> bool:
    """Check if a token is a cloud layer (e.g., FEW015, BKN030, OVC050, VV003)."""
    return _matches_any_pattern(token, CLOUD_TOKEN_PATTERNS)


def is_temp_dewpoint_token(token: str) -> bool:
    """Check if a token is temperature/dewpoint (e.g., 08/08, M03/M07)."""
    return _matches_any_pattern(token, TEMP_DEWPOINT_TOKEN_PATTERNS)


def is_altimeter_token(token: str) -> bool:
    """Check if a token is an altimeter setting (e.g., A2992, Q1013)."""
    return _matches_any_pattern(token, ALTIMETER_TOKEN_PATTERNS)


def is_non_weather_token(token: str) -> bool:
    """
    Check if a token is a non-weather METAR component.

    Used by parse_weather_phenomena to skip non-weather parts.

    Args:
        token: A whitespace-separated token from a METAR string

    Returns:
        True if the token is NOT a weather phenomenon, False otherwise
    """
    # Timestamp (e.g., 240153Z)
    if re.match(r"^\d{6}Z$", token):
        return True
    # Wind and variable wind
    if is_wind_token(token):
        return True
    # Visibility
    if is_visibility_token(token):
        return True
    # Cloud layers
    if is_cloud_token(token):
        return True
    # Temperature/dewpoint
    if is_temp_dewpoint_token(token):
        return True
    # Altimeter
    return bool(is_altimeter_token(token))


def get_airport_size_priority(airport_info: dict[str, Any]) -> int:
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


def parse_visibility_sm(metar: str) -> float | None:
    """
    Parse visibility in statute miles from METAR.

    Args:
        metar: Raw METAR string

    Returns:
        Visibility in statute miles, or None if not found
    """
    if not metar:
        return None

    # Handle visibility forms in order of specificity:
    # 1. Fractional visibility: "1/2SM", "1 1/2SM", "M1/4SM" (M = less than)
    # 2. Whole number visibility: "10SM", "3SM"
    # 3. Meters visibility (convert to SM): "9999" means 10+ km, "0800" = 800m
    visibility = _parse_fraction_visibility(metar)
    if visibility is not None:
        return visibility

    visibility = _parse_whole_mile_visibility(metar)
    if visibility is not None:
        return visibility

    return _parse_meters_visibility(metar)


def _parse_fraction_visibility(metar: str) -> float | None:
    """Fractional visibility: mixed ("1 1/2SM"), less-than ("M1/4SM") and simple ("3/4SM")."""
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

    return None


def _parse_whole_mile_visibility(metar: str) -> float | None:
    """Whole-number visibility ("10SM", "3SM", "P6SM"), or None if absent."""
    # Pattern for whole number like "10SM", "3SM", "P6SM" (P = plus/more than 6)
    whole_match = re.search(r"\b[PM]?(\d+)SM\b", metar)
    if not whole_match:
        return None

    vis = int(whole_match.group(1))
    # P6SM means greater than 6 miles - treat as good visibility
    if "P" in metar and f"P{vis}SM" in metar:
        return vis + 1  # Slightly more than indicated
    return float(vis)


def _parse_meters_visibility(metar: str) -> float | None:
    """4-digit meters visibility ("9999" = 10km+, "0800" = 800m) as statute miles."""
    # This appears after the wind in METAR, before clouds
    meters_match = re.search(r"\b(\d{4})\b(?!\d)", metar)
    if not meters_match:
        return None

    # Make sure it's not a time or other number
    meters = int(meters_match.group(1))
    # Visibility in meters is typically 0000-9999
    # 9999 means visibility >= 10km
    if meters == 9999:
        return 7.0  # Greater than 6 SM
    # Convert meters to statute miles (1 SM = 1609.34 meters)
    return meters / 1609.34


def parse_ceiling_feet(metar: str) -> int | None:
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


def parse_ceiling_layer(metar: str) -> str | None:
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


def extract_visibility_str(metar: str) -> str | None:
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


def extract_flight_rules_weather(metar: str) -> tuple[str | None, str | None]:
    """
    Extract visibility and ceiling strings relevant to flight rules from METAR.

    Args:
        metar: Raw METAR string

    Returns:
        Tuple of (visibility_str, ceiling_str) as they appear in METAR,
        e.g., ("2SM", "BKN004"). Either may be None if not present.
    """
    return (extract_visibility_str(metar), parse_ceiling_layer(metar))


def parse_wind_from_metar(metar: str) -> str | None:
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


def parse_metar_obs_time(metar: str) -> str | None:
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


def _split_weather_intensity(code: str) -> tuple[str, str]:
    """Split a leading intensity prefix ("-", "+" or "VC") off a weather code."""
    for prefix, label in WEATHER_INTENSITY.items():
        if code.startswith(prefix):
            return label + " ", code[len(prefix) :]
    return "", code


def _split_weather_descriptor(code: str) -> tuple[str, str]:
    """Split a leading descriptor (e.g. "FZ", "TS", "SH") off a weather code."""
    for desc_code, desc_name in WEATHER_DESCRIPTORS.items():
        if code.startswith(desc_code):
            return desc_name + " ", code[len(desc_code) :]
    return "", code


def _parse_weather_codes(code: str, intensity: str, descriptor: str) -> list[str]:
    """Parse the leading 2-letter phenomena, applying intensity and descriptor once."""
    results = []
    while len(code) >= 2:
        phenomenon = WEATHER_PHENOMENA.get(code[:2])
        if phenomenon is None:
            # Unknown code, skip
            break
        name, _ = phenomenon
        full_name = f"{intensity}{descriptor}{name}".strip()
        if full_name and full_name not in results:
            results.append(full_name)
        # Reset intensity/descriptor after first use
        intensity = ""
        descriptor = ""
        code = code[2:]
    return results


def _parse_single_weather(code: str) -> list[str]:
    """
    Parse a single weather code into human-readable format.

    Args:
        code: Weather code like "-RA", "+TSRA", "FZFG", "VCSH"

    Returns:
        List of weather descriptions (usually 1, but can be more for combined phenomena)
    """
    if not code:
        return []

    intensity, remainder = _split_weather_intensity(code)
    descriptor, remainder = _split_weather_descriptor(remainder)

    results = []
    # Handle thunderstorm specially - it's both descriptor and phenomenon
    if "TS" in code and descriptor == "Thunderstorm ":
        results.append(f"{intensity}Thunderstorm")
        intensity = ""  # Don't double-apply intensity
        descriptor = ""

    results.extend(_parse_weather_codes(remainder, intensity, descriptor))
    return results


def parse_weather_phenomena(metar: str) -> list[str]:
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
        # Stop at remarks section
        if re.match(r"^RMK", part):
            break

        # Skip non-weather METAR components (timestamp, wind, visibility, clouds, etc.)
        if is_non_weather_token(part):
            continue

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

    # Determine category based on most restrictive condition
    vis_category = _visibility_category(parse_visibility_sm(metar))
    ceil_category = _ceiling_category(parse_ceiling_feet(metar))

    # Use the most restrictive category
    if CATEGORY_PRIORITY.get(vis_category, 3) < CATEGORY_PRIORITY.get(ceil_category, 3):
        return vis_category
    return ceil_category


def _visibility_category(visibility: float | None) -> str:
    """Flight category for a visibility in statute miles; an unknown one is VFR."""
    if visibility is None:
        return "VFR"
    if visibility < 1:
        return "LIFR"
    if visibility < 3:
        return "IFR"
    if visibility <= 5:
        return "MVFR"
    return "VFR"


def _ceiling_category(ceiling: int | None) -> str:
    """Flight category for a ceiling in feet AGL; no ceiling is VFR."""
    if ceiling is None:
        return "VFR"
    if ceiling < 500:
        return "LIFR"
    if ceiling < 1000:
        return "IFR"
    if ceiling <= 3000:
        return "MVFR"
    return "VFR"


# Backward-compatible aliases with underscore prefix (deprecated)
_parse_visibility_sm = parse_visibility_sm
_parse_ceiling_feet = parse_ceiling_feet
_parse_ceiling_layer = parse_ceiling_layer
_extract_visibility_str = extract_visibility_str
_extract_flight_rules_weather = extract_flight_rules_weather
