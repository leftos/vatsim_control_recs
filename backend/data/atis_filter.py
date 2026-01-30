"""
ATIS text filtering and parsing.

- Removes METAR-duplicated weather info from ATIS text
- Extracts active runway assignments from ATIS
"""

import re
from typing import Any, Dict, Set, Tuple

from backend.data.weather_parsing import (
    VISIBILITY_REMOVAL_PATTERNS,
    WIND_REMOVAL_PATTERNS,
    CLOUD_REMOVAL_PATTERNS,
    TEMP_DEWPOINT_REMOVAL_PATTERNS,
    ALTIMETER_REMOVAL_PATTERNS,
)

# =============================================================================
# Shared pattern components for runway/approach parsing and colorization
# =============================================================================

# Runway number pattern with spoken direction forms
# Matches: "17R", "17L", "17 R", "17R AND LEFT", "17R AND 17L", "17R, 17L AND CENTER", "10L OR 10R"
RWY_NUM_PATTERN = (
    r"\d{1,2}\s*[LRC]?(?:\s*(?:AND|OR|&|,|/)\s*(?:\d{1,2}\s*[LRC]?|LEFT|RIGHT|CENTER))*"
)

# Approach type keywords
APPROACH_TYPES = r"ILS|RNAV|VISUAL|VIS|VA|LOC|VOR|NDB|GPS|LDA|SDF|RNP"

# Runway/RWY prefix
RWY_PREFIX = r"RWYS?|RUNWAYS?"

# Approach suffix keywords (APCH, APPROACH, etc.)
APPROACH_SUFFIX = r"APCHS?|APPROACH(?:ES)?|APPS?"

# Landing operation keywords
LANDING_KW = r"LANDING|LDG|LNDG|ARRIVING|ARR"

# Departing operation keywords
DEPARTING_KW = r"DEPARTING|DEPARTURES?|DEPTG?|DEPG|DEP"

# Character class for runway specs (used in parse_runway_assignments for loose matching)
# Includes digits, L/R/C, LEFT/RIGHT/CENTER letters, and common separators
RWY_CHARS = r"[\d\sLRCAND,/EFIGHTCENTER]+"

# =============================================================================
# Patterns to remove METAR-duplicated info from ATIS text
# These detect and remove raw METAR-format data embedded in ATIS
# Note: Core patterns are imported from weather_parsing.py for consistency
METAR_REMOVAL_PATTERNS = [
    # Weather intro with time: "WEATHER AT 0100Z.", "WEATHER AT 0130ZUTC."
    r"\bWEATHER\s+AT\s+\d{4}Z?(UTC)?\.?\s*",
    # Observed at time
    r"\bOBSERVED\s+AT\s+\d{4}\s*(UTC)*(UTC)*\.?\s*",
    # METAR reference
    r"\bMETAR\s+\d+\s*",
    # Wind patterns (from shared constants)
    *WIND_REMOVAL_PATTERNS,
    # Visibility patterns (from shared constants)
    *VISIBILITY_REMOVAL_PATTERNS,
    # Cloud patterns (from shared constants)
    *CLOUD_REMOVAL_PATTERNS,
    # Temperature/dewpoint patterns (from shared constants)
    *TEMP_DEWPOINT_REMOVAL_PATTERNS,
    # Altimeter patterns (from shared constants)
    *ALTIMETER_REMOVAL_PATTERNS,
    # Weather phenomena: -DZ, +RA, BR, FG, FZFG, TSRA, etc. - must be standalone words
    # Includes compound codes like FZFG, FZRA, FZDZ, TSRA, TSSN, VCSH, etc.
    r"(?<!\w)[-+]?(VC|MI|PR|BC|DR|BL|SH|TS|FZ)?(DZ|RA|SN|SG|IC|PL|GR|GS|BR|FG|FU|VA|DU|SA|HZ|PY|UP)(?!\w)\s*",
    # TDZ (touchdown zone) wind readings - remove "WIND RWY XX TDZ" or standalone "TDZ"
    r"\bWIND\s+RWY\s+\d+\s+TDZ\b\s*",
    r"\bTDZ\s+\d{5}(G\d{2,3})?KT\b(\s+\d{3}V\d{3})?\s*",
    r"\bTDZ\s*$",  # TDZ at end
    # Standalone hyphen or dash leftover
    r"\s+-\s+",
    r"\s+-$",
    # Trend: TEMPO, BECMG with conditions
    r"\bTREND\s+\w+(\s+\w+)*\s*",
    # Orphaned "UTC." leftover
    r"\bUTC\.\s*",
    # METAR remarks section: RMK followed by codes like AO2, SLP###, T########, P####, VIRGA, etc.
    # This removes the entire RMK section up to but not including the next period
    r"\bRMK\s+(?:AO[12]|SLP\d{3}|T\d{8}|P\d{4}|VIRGA[\w\s-]*|[A-Z0-9]{2,6})(?:\s+(?:AO[12]|SLP\d{3}|T\d{8}|P\d{4}|VIRGA[\w\s-]*|[A-Z0-9]{2,6}))*\.?\s*",
]


def _find_metar_ops_boundary(atis_text: str) -> int:
    """
    Find the boundary between METAR portion and operational portion of ATIS.

    ATIS structure is typically:
    1. Header: "XXX ATIS INFO [LETTER] [TIME]Z."
    2. METAR: Wind, visibility, weather, clouds, temp/dewpoint, altimeter
    3. Spoken altimeter: "(THREE ZERO ONE THREE)"
    4. Optional RMK section: "RMK AO2 SLP### T########"
    5. Period marking end of METAR
    6. Operational info: Approaches, runways, NOTAMs, etc.

    Returns:
        Index where operational portion begins, or 0 if boundary not found
    """
    # Operational keywords that mark the start of the ops section
    OPS_KEYWORDS = (
        r"APPROACH|APCH|APCHS|"
        r"ARR\b|ARRIVAL|"
        r"DEP\b|DEPS\b|DEPG|DEPARTURE|"
        r"SIMUL|SIMULTANEOUS|"
        r"ILS\b|RNAV|VISUAL|VIS\b|"
        r"LDG\b|LNDG|LANDING|"
        r"RWY|RY\b|RWYS|RUNWAY|"
        r"INST\b|"
        r"NOTAM|TWY|TAXIWAY|"
        r"ATTN|CAUTION|"
        r"FLOW\b|LOW\b|"
        r"CTC\b|CONTACT"
    )

    # Look for altimeter + spoken form pattern
    # Example: "A3013 (THREE ZERO ONE THREE)"
    altimeter_pattern = r"[AQ]\d{4}\s*\([A-Z\s]+\)"
    alt_match = re.search(altimeter_pattern, atis_text, re.IGNORECASE)

    if alt_match:
        search_start = alt_match.end()

        # Check if there's an RMK section after the altimeter
        # RMK section ends at a period followed by operational keyword
        rmk_match = re.search(r"\bRMK\b", atis_text[search_start:], re.IGNORECASE)

        if rmk_match:
            # Found RMK - look for period + ops keyword after it
            rmk_start = search_start + rmk_match.start()
            ops_pattern = rf"\.\s*(?={OPS_KEYWORDS})"
            ops_match = re.search(ops_pattern, atis_text[rmk_start:], re.IGNORECASE)
            if ops_match:
                return rmk_start + ops_match.end()
        else:
            # No RMK section - look for period + ops keyword after altimeter
            ops_pattern = rf"\.\s*(?={OPS_KEYWORDS})"
            ops_match = re.search(ops_pattern, atis_text[search_start:], re.IGNORECASE)
            if ops_match:
                return search_start + ops_match.end()

    # Fallback: look for period + operational keyword pattern anywhere
    # This handles cases where altimeter format is non-standard
    ops_pattern = rf"\.\s*(?={OPS_KEYWORDS})"
    fallback_match = re.search(ops_pattern, atis_text, re.IGNORECASE)
    if fallback_match:
        return fallback_match.end()

    # No boundary found - return 0 to filter entire text (old behavior)
    return 0


def filter_atis_text(atis_text: str) -> str:
    """
    Filter METAR-duplicated information from ATIS text.

    Only filters the METAR portion of the ATIS, preserving the operational
    information (approaches, runways, NOTAMs, etc.) that follows.

    For US-style ATIS with altimeter + spoken form pattern (e.g., "A3013 (THREE ZERO...)"),
    filters weather data before the operational section.

    For non-standard or international ATIS formats, returns the text with minimal
    cleanup to avoid removing important operational information.

    Args:
        atis_text: Full ATIS text as single string

    Returns:
        String with METAR-duplicated info removed, keeping operational info
        like runway assignments, approaches, NOTAMs, etc.
    """
    if not atis_text:
        return ""

    # Check if this is a US-style ATIS with altimeter + spoken form
    # Only apply aggressive filtering if we find this pattern
    altimeter_spoken_pattern = r"[AQ]\d{4}\s*\([A-Z\s]+\)"
    has_us_format = re.search(altimeter_spoken_pattern, atis_text, re.IGNORECASE)

    if has_us_format:
        # Find boundary between METAR and operational portions
        boundary = _find_metar_ops_boundary(atis_text)

        if boundary > 0:
            # Split into METAR and operational portions
            metar_portion = atis_text[:boundary]
            ops_portion = atis_text[boundary:]

            # Only filter the METAR portion
            filtered_metar = metar_portion
            for pattern in METAR_REMOVAL_PATTERNS:
                filtered_metar = re.sub(pattern, " ", filtered_metar, flags=re.IGNORECASE)

            # Clean up the filtered METAR portion
            filtered_metar = re.sub(r"\s+", " ", filtered_metar).strip()
            filtered_metar = re.sub(r"\(\s*\)", "", filtered_metar)

            # Combine: filtered METAR + unchanged operational portion
            # Remove leading/trailing periods and spaces from the join point
            filtered_metar = filtered_metar.rstrip(". ")
            ops_portion = ops_portion.lstrip(". ")

            if filtered_metar and ops_portion:
                filtered = f"{filtered_metar}. {ops_portion}"
            elif ops_portion:
                filtered = ops_portion
            else:
                filtered = filtered_metar
        else:
            # Has US format but no clear boundary - filter entire text carefully
            filtered = atis_text
            for pattern in METAR_REMOVAL_PATTERNS:
                filtered = re.sub(pattern, " ", filtered, flags=re.IGNORECASE)
    else:
        # Non-US format - don't filter weather, just clean up formatting
        filtered = atis_text

    # Final cleanup (applies to all formats)
    filtered = re.sub(r"\s+", " ", filtered).strip()
    filtered = re.sub(r"\(\s*\)", "", filtered)
    filtered = re.sub(r"\s+\.", ".", filtered)
    filtered = re.sub(r"\s+,", ",", filtered)
    # Clean up multiple dots - normalize to single dot or ellipsis
    filtered = re.sub(r"\.{4,}", "...", filtered)  # 4+ dots -> ellipsis
    filtered = re.sub(r"\.\.\.\.", "...", filtered)  # 4 dots -> ellipsis
    filtered = re.sub(r"(?<!\.)\.\.(?!\.)", ".", filtered)  # exactly 2 dots -> 1 dot
    # Clean double periods
    filtered = re.sub(r"\.\s*\.", ".", filtered)
    # Clean up space before punctuation one more time
    filtered = re.sub(r"\s+\.", ".", filtered)

    return filtered


def _extract_runway_numbers(text: str) -> Set[str]:
    """
    Extract runway numbers from a text fragment.

    Args:
        text: Text containing runway numbers (e.g., "16L, 17R AND 18", "17R AND LEFT", "17 RIGHT AND LEFT")

    Returns:
        Set of runway designators (e.g., {"16L", "17R", "18"})
    """
    runways = set()
    last_runway_num = None
    suffix_map = {"LEFT": "L", "RIGHT": "R", "CENTER": "C"}

    # Pattern 1: Match runway numbers with optional space before direction
    # e.g., "17R", "17L", "17 RIGHT", "17 LEFT", "17RIGHT"
    pattern = r"\b(\d{1,2})\s*([LRC]|LEFT|RIGHT|CENTER)?\b"
    for match in re.finditer(pattern, text, re.IGNORECASE):
        num = match.group(1)
        suffix = match.group(2) or ""
        # Convert spoken forms to single letter
        suffix = suffix_map.get(suffix.upper(), suffix.upper())
        runways.add(f"{num}{suffix}")
        last_runway_num = num

    # Pattern 2: Handle standalone LEFT/RIGHT/CENTER that refer to the previous runway number
    # e.g., "17R AND LEFT" means 17R and 17L
    if last_runway_num:
        standalone_pattern = r"\b(AND|&|,)\s+(LEFT|RIGHT|CENTER)\b"
        for match in re.finditer(standalone_pattern, text, re.IGNORECASE):
            direction = match.group(2).upper()
            suffix = suffix_map.get(direction, "")
            if suffix:
                runways.add(f"{last_runway_num}{suffix}")

    return runways


def parse_approach_info(atis_text: str) -> Dict[str, Any]:
    """
    Parse ATIS text to extract active runway assignments AND approach types.

    Args:
        atis_text: Full ATIS text

    Returns:
        Dict with keys:
            - 'landing': Set of runway designators for landing
            - 'departing': Set of runway designators for departing
            - 'approaches': Dict mapping runway -> set of approach types
                           (e.g., {"16L": {"ILS", "RNAV"}, "16R": {"VISUAL"}})
    """
    if not atis_text:
        return {"landing": set(), "departing": set(), "approaches": {}}

    landing: Set[str] = set()
    departing: Set[str] = set()
    approaches: Dict[str, Set[str]] = {}  # runway -> set of approach types
    text = atis_text.upper()

    # Helper to add approach type for runways
    def add_approaches(runways: Set[str], approach_type: str):
        for rwy in runways:
            if rwy not in approaches:
                approaches[rwy] = set()
            approaches[rwy].add(approach_type)

    # Pattern 1: Compound LDG/DEPTG or LDG AND DEPTG format (both ops on same runways)
    # e.g., "LDG/DEPTG 4/8", "LDG AND DEPTG RWY 27", "ARR/DEP RWY 36"
    compound_pattern = (
        rf"\b(?:{LANDING_KW})\s*(?:/|AND)\s*"
        rf"(?:{DEPARTING_KW})\s*"
        rf"(?:{RWY_PREFIX})?\s*"
        rf"({RWY_CHARS})"
    )
    for match in re.finditer(compound_pattern, text):
        rwys = _extract_runway_numbers(match.group(1))
        landing.update(rwys)
        departing.update(rwys)

    # Pattern 2: Landing/Arriving runways
    # e.g., "LDG RWY 16L", "LANDING RUNWAY 27", "ARR RWY 35", "LNDG RWYS 17R AND LEFT"
    landing_pattern = (
        rf"\b(?:{LANDING_KW})\s+"
        rf"(?:{RWY_PREFIX})?\s*"
        rf"({RWY_CHARS})"
    )
    for match in re.finditer(landing_pattern, text):
        # Skip if this is part of a compound pattern (already handled)
        start = match.start()
        prefix_check = text[max(0, start - 5) : start]
        if "/" in prefix_check or "AND" in prefix_check:
            continue
        rwys = _extract_runway_numbers(match.group(1))
        landing.update(rwys)

    # Pattern 3: Departing runways
    # e.g., "DEP RWY 16R", "DEPARTING RWYS 26L, 27R", "DEPTG RWY 18"
    # Handles malformed double prefix: "DEPG RWYS RWY 10L"
    departing_pattern = (
        rf"\b(?:{DEPARTING_KW})\s+"
        rf"(?:{RWY_PREFIX})?\s*(?:{RWY_PREFIX})?\s*"
        rf"({RWY_CHARS})"
    )
    for match in re.finditer(departing_pattern, text):
        # Skip if this is part of a compound pattern
        start = match.start()
        prefix_check = text[max(0, start - 5) : start]
        if "/" in prefix_check or "AND" in prefix_check:
            continue
        rwys = _extract_runway_numbers(match.group(1))
        departing.update(rwys)

    # Pattern 4: Approach types imply landing runway (CAPTURE APPROACH TYPE)
    # e.g., "ILS RWY 22R", "RNAV-Y RWY 35", "VISUAL APPROACH RWY 26"
    # Also handles spoken forms: "ILS RWYS 17R AND LEFT" means 17R and 17L
    approach_pattern = (
        rf"\b({APPROACH_TYPES})[-\s]?[XYZWUK]?\s*"
        rf"(?:{APPROACH_SUFFIX})?\s*"
        r"(?:TO\s+)?"
        rf"(?:{RWY_PREFIX})?\s*"
        rf"({RWY_NUM_PATTERN})"
    )
    for match in re.finditer(approach_pattern, text):
        approach_type = match.group(1)
        rwys = _extract_runway_numbers(match.group(2))
        landing.update(rwys)
        add_approaches(rwys, approach_type)

    # Pattern 5: EXPECT approach type (CAPTURE APPROACH TYPE)
    # e.g., "EXPECT ILS RWY 35L", "EXP VIS APCH RWY 27", "ARRIVALS EXPECT ILS APCH RWY 10L"
    # Also handles spoken forms: "EXPECT ILS RWYS 17R AND LEFT"
    # Handles malformed double prefix: "ARRIVALS EXPECT ILS RWYS RWY 10L"
    expect_pattern = (
        r"\b(?:ARRIVALS?\s+)?(?:EXPECT|EXPT?|EXPECTED)\s+"
        r"(?:PROC\s+)?"
        rf"({APPROACH_TYPES})[-\s]?[XYZWUK]?\s*"
        rf"(?:{APPROACH_SUFFIX})?\s*"
        rf"(?:{RWY_PREFIX})?\s*(?:{RWY_PREFIX})?\s*"
        rf"({RWY_NUM_PATTERN})"
    )
    for match in re.finditer(expect_pattern, text):
        approach_type = match.group(1)
        rwys = _extract_runway_numbers(match.group(2))
        landing.update(rwys)
        add_approaches(rwys, approach_type)

    # Pattern 6: RWY XX FOR ARR/DEP (Australian/international style)
    # e.g., "RWY 03 FOR ARR", "RWY 06 FOR DEP"
    for_pattern = (
        rf"\b(?:{RWY_PREFIX})\s*"
        r"(\d{1,2}[LRC]?)\s+"
        r"FOR\s+(?:ALL\s+(?:OTHER\s+)?)?"
        r"(ARR(?:IVALS?)?|DEP(?:ARTURES?)?)"
    )
    for match in re.finditer(for_pattern, text):
        rwy = match.group(1)
        op_type = match.group(2).upper()
        if op_type.startswith("ARR"):
            landing.add(rwy)
        else:
            departing.add(rwy)

    # Pattern 7: SIMUL/INSTR operations
    # e.g., "SIMUL DEPARTURES RWYS 24 AND 25"
    simul_pattern = (
        r"\b(?:SIMUL?(?:TANEOUS)?|INSTR?)\s+"
        r"(DEPARTURES?|ARRIVALS?|DEPS?|ARRS?)\s+"
        r"(?:IN\s+(?:PROG(?:RESS)?|USE|EFFECT)\s+)?"
        rf"(?:{RWY_PREFIX})?\s*"
        rf"({RWY_NUM_PATTERN})"
    )
    for match in re.finditer(simul_pattern, text):
        op_type = match.group(1).upper()
        rwys = _extract_runway_numbers(match.group(2))
        if op_type.startswith("ARR"):
            landing.update(rwys)
        else:
            departing.update(rwys)

    return {"landing": landing, "departing": departing, "approaches": approaches}


def parse_runway_assignments(atis_text: str) -> Dict[str, Set[str]]:
    """
    Parse ATIS text to extract active runway assignments.

    Args:
        atis_text: Full ATIS text

    Returns:
        Dict with keys 'landing' and 'departing', each containing a set of runway designators.
        Runways are normalized (e.g., "16L", "27", "35R").
    """
    # Reuse the new parse_approach_info function and discard approach data
    result = parse_approach_info(atis_text)
    return {"landing": result["landing"], "departing": result["departing"]}


def format_runway_summary(assignments: Dict[str, Set[str]]) -> str:
    """
    Format runway assignments as a compact summary string.

    Args:
        assignments: Dict from parse_runway_assignments()

    Returns:
        Formatted string like "L:16L,17R D:18" or "L/D:27" for same runways
    """
    landing = assignments.get("landing", set())
    departing = assignments.get("departing", set())

    if not landing and not departing:
        return ""

    # Sort runways for consistent display (handle malformed runway strings gracefully)
    def runway_sort_key(x: str) -> Tuple[int, str]:
        match = re.match(r"\d+", x)
        return (int(match.group()) if match else 99, x)

    landing_sorted = sorted(landing, key=runway_sort_key)
    departing_sorted = sorted(departing, key=runway_sort_key)

    # Check if landing and departing are the same
    if landing == departing and landing:
        return f"L/D:{','.join(landing_sorted)}"

    parts = []
    if landing:
        parts.append(f"L:{','.join(landing_sorted)}")
    if departing:
        parts.append(f"D:{','.join(departing_sorted)}")

    return " ".join(parts)


def colorize_atis_text(text: str, atis_code: str = "") -> str:
    """
    Colorize ATIS text with highlighted approach types, runways, and ATIS letter.

    Args:
        text: Filtered ATIS text
        atis_code: ATIS letter (e.g., "K", "L")

    Returns:
        Rich markup string with colorized elements
    """
    result = text

    # Colorize ATIS letter references (e.g., "INFORMATION KILO", "INFO K", "ATIS K")
    if atis_code:
        # NATO phonetic alphabet mapping
        phonetic = {
            "A": "ALFA",
            "B": "BRAVO",
            "C": "CHARLIE",
            "D": "DELTA",
            "E": "ECHO",
            "F": "FOXTROT",
            "G": "GOLF",
            "H": "HOTEL",
            "I": "INDIA",
            "J": "JULIET",
            "K": "KILO",
            "L": "LIMA",
            "M": "MIKE",
            "N": "NOVEMBER",
            "O": "OSCAR",
            "P": "PAPA",
            "Q": "QUEBEC",
            "R": "ROMEO",
            "S": "SIERRA",
            "T": "TANGO",
            "U": "UNIFORM",
            "V": "VICTOR",
            "W": "WHISKEY",
            "X": "XRAY",
            "Y": "YANKEE",
            "Z": "ZULU",
        }
        letter = atis_code.upper()
        phonetic_word = phonetic.get(letter, "")

        # Replace phonetic word (e.g., "KILO" -> cyan)
        if phonetic_word:
            result = re.sub(
                rf"\b({phonetic_word})\b",
                r"[cyan bold]\1[/cyan bold]",
                result,
                flags=re.IGNORECASE,
            )
        # Also highlight standalone letter after INFO/INFORMATION/ATIS
        result = re.sub(
            rf"\b(INFORMATION|INFO|ATIS)\s+({letter})\b",
            r"\1 [cyan bold]\2[/cyan bold]",
            result,
            flags=re.IGNORECASE,
        )

    # Pattern 0a: SIMUL with VISUAL approaches and runways ending with APP IN USE
    # e.g., "SIMUL CHARTED VISUAL FMS BRIDGE RY 28R AND TIPP TOE RY 28L APP IN USE"
    # Captures from SIMUL through APP IN USE when VISUAL is present
    result = re.sub(
        r"\bSIMUL(?:TANEOUS)?\s+(?:[A-Z]+\s+)*VISUAL\s+[^.]*?"
        rf"(?:RY|{RWY_PREFIX})\s*{RWY_NUM_PATTERN}"
        r"(?:\s+AND\s+[^.]*?(?:RY|RWY)\s*\d+[LRC]?)*"
        r"\s+APP(?:S|ROACH(?:ES)?)?\s+IN\s+USE\b",
        r"[yellow]\g<0>[/yellow]",
        result,
        flags=re.IGNORECASE,
    )

    # Pattern 0b: Comma-separated approach list ending with APCHS IN USE
    # e.g., "ILS, RNAV Y, RNAV Z, FAIRGROUNDS VISUAL, RWY 30L APCHS IN USE"
    # Matches approach types/names separated by commas, then RWY and APCHS
    result = re.sub(
        rf"\b({APPROACH_TYPES})[-\s]?[XYZWUK]?"
        r"(?:,\s*(?:[A-Z]+\s+)*(?:" + APPROACH_TYPES + r"|VISUAL)[-\s]?[XYZWUK]?)*"
        rf",?\s*(?:{RWY_PREFIX})\s*{RWY_NUM_PATTERN}\s+"
        rf"({APPROACH_SUFFIX})\s+IN\s+USE\b",
        r"[yellow]\g<0>[/yellow]",
        result,
        flags=re.IGNORECASE,
    )

    # Pattern 1: Compound approach types with AND separator (must come before single approach pattern)
    # e.g., "ILS, AND VA, RWYS 30 AND 28R", "ILS AND RNAV RWYS 28L, 28R"
    # Handles multiple approach types listed together before runway info
    result = re.sub(
        rf"\b({APPROACH_TYPES})[-\s]?([XYZWUK])?"
        rf"(?:,?\s+AND\s+(?:{APPROACH_TYPES})[-\s]?[XYZWUK]?)+"
        rf",?\s*({RWY_PREFIX})\s*"
        rf"({RWY_NUM_PATTERN})\b",
        r"[yellow]\g<0>[/yellow]",
        result,
        flags=re.IGNORECASE,
    )

    # Pattern 2: Approach type + optional variant + optional comma + RWY + runway numbers
    # e.g., "ILS Z RWY 22R", "RNAV-Y RWY 35", "ILS RWYS 16R AND 16L", "ILS, RWY 12"
    # Also handles spoken forms: "ILS RWYS 17R AND LEFT" means 17R and 17L
    # Uses negative lookbehind to avoid matching after "AND " (already handled by compound Pattern 1)
    result = re.sub(
        rf"(?<!AND )(?<!AND)\b({APPROACH_TYPES})[-\s]?([XYZWUK])?,?\s*"
        rf"({RWY_PREFIX})?\s*"
        rf"({RWY_NUM_PATTERN})\b",
        r"[yellow]\g<0>[/yellow]",
        result,
        flags=re.IGNORECASE,
    )

    # Pattern 3: Approach type + APCH/APPROACH + RWY + runway numbers
    # e.g., "ILS APCH RWY 35L", "VISUAL APPROACH RWY 26R", "VIS APCH RWYS 17L, 17R"
    # Also handles spoken forms: "ILS APCH RWYS 17R AND LEFT"
    result = re.sub(
        rf"\b({APPROACH_TYPES}|INSTR?)[-\s]?([XYZWUK])?\s*"
        rf"({APPROACH_SUFFIX})\s*"
        rf"((?:TO\s+)?(?:{RWY_PREFIX})?\s*{RWY_NUM_PATTERN})?",
        r"[yellow]\g<0>[/yellow]",
        result,
        flags=re.IGNORECASE,
    )

    # Pattern 3: APCH + approach type (Canadian/other style)
    # e.g., "APCH ILS OR RNAV RWY 27"
    # Also handles spoken forms: "APCH ILS RWYS 17R AND LEFT"
    result = re.sub(
        rf"\b(APCH)\s+((?:{APPROACH_TYPES})(?:\s+OR\s+(?:{APPROACH_TYPES}))*)"
        rf"(\s+(?:{RWY_PREFIX})\s*{RWY_NUM_PATTERN})?",
        r"[yellow]\g<0>[/yellow]",
        result,
        flags=re.IGNORECASE,
    )

    # Pattern 4: Standalone approach mentions
    # e.g., "ILS APPROACHES", "VISUAL APCHS IN USE", "INST APCHS"
    result = re.sub(
        rf"\b({APPROACH_TYPES}|INSTR?)\s+"
        rf"({APPROACH_SUFFIX})\b",
        r"[yellow]\1 \2[/yellow]",
        result,
        flags=re.IGNORECASE,
    )

    # Pattern 5: EXPECT/EXP + approach type (also handles "ARRIVALS EXPECT...")
    # e.g., "EXPECT ILS APPROACH", "EXP VIS APCH", "EXPT PROC ILS", "ARRIVALS EXPECT ILS APCH RWY 10L"
    result = re.sub(
        r"\b((?:ARRIVALS?\s+)?(?:EXPECT|EXPT?|EXPECTED))\s+(?:PROC\s+)?"
        rf"({APPROACH_TYPES}|INSTR?)[-\s]?([XYZWUK])?\s*"
        rf"(?:{APPROACH_SUFFIX})?\s*"
        rf"(?:(?:{RWY_PREFIX})\s*(?:{RWY_PREFIX})?\s*{RWY_NUM_PATTERN})?",
        r"[yellow]\g<0>[/yellow]",
        result,
        flags=re.IGNORECASE,
    )

    # Pattern 6: Compound LDG/DEPTG or LDG AND DEPTG format - must come before Pattern 7
    # e.g., "LDG/DEPTG 4/8", "LNDG AND DEPG RWY 17R, 17L", "LDG AND DEPTG RWY 27"
    # Also handles spoken forms: "LDG/DEPTG 17R AND LEFT"
    result = re.sub(
        rf"\b((?:{LANDING_KW})\s*(?:/|AND)\s*(?:{DEPARTING_KW}))\s*"
        rf"((?:{RWY_PREFIX})?\s*)?"
        rf"({RWY_NUM_PATTERN})\b",
        r"[yellow]\g<0>[/yellow]",
        result,
        flags=re.IGNORECASE,
    )

    # Pattern 6b: ARR/DEP compound format
    # e.g., "ARR/DEP RWY 36", "ARR AND DEP RWY 30"
    # Also handles spoken forms: "ARR/DEP 17R AND LEFT"
    result = re.sub(
        r"\b(ARR(?:IVING)?\s*(?:/|AND)\s*DEP(?:ARTING)?)\s*"
        rf"((?:{RWY_PREFIX})?\s*)?"
        rf"({RWY_NUM_PATTERN})\b",
        r"[yellow]\g<0>[/yellow]",
        result,
        flags=re.IGNORECASE,
    )

    # Pattern 7: Runway assignments (LDG/ARR/DEP + RWY + numbers)
    # e.g., "LDG RWY 16L", "DEPTG RWYS 26L, 27R", "LANDING RUNWAY 27", "DEPARTING RWY 18"
    # Also handles DEPG (common variant), DEPARTURE
    # Handles spoken forms: "17R AND LEFT", "28L AND RIGHT"
    # Handles malformed double prefix: "DEPG RWYS RWY 10L" (some controllers do this)
    # Uses negative lookbehind to avoid matching after "/" or "AND " (already handled by Pattern 6)
    result = re.sub(
        rf"(?<!/)(?<!AND )\b({LANDING_KW}|{DEPARTING_KW})\s+"
        rf"((?:{RWY_PREFIX})\s*(?:{RWY_PREFIX})?\s*)?"
        rf"({RWY_NUM_PATTERN})\b",
        r"[yellow]\g<0>[/yellow]",
        result,
        flags=re.IGNORECASE,
    )

    # Pattern 8: INSTR DEPARTURES IN PROG + RWYS (LAX style)
    # e.g., "INSTR DEPARTURES IN PROG RWYS 24 AND 25"
    # Also handles spoken forms: "SIMUL ARRIVALS RWYS 17R AND LEFT"
    result = re.sub(
        r"\b(INSTR?|SIMUL?(?:TANEOUS)?)\s+"
        r"(DEPARTURES?|ARRIVALS?|DEPS?|ARRS?)\s+"
        r"(?:IN\s+(?:PROG(?:RESS)?|USE|EFFECT)\s+)?"
        rf"((?:{RWY_PREFIX})?\s*)?"
        rf"({RWY_NUM_PATTERN})\b",
        r"[yellow]\g<0>[/yellow]",
        result,
        flags=re.IGNORECASE,
    )

    # Pattern 9: RWY XX FOR ARR/DEP (Australian/international style)
    # e.g., "RWY 03 FOR ARR", "RWY 06 FOR DEP", "RWY 03 FOR ALL DEP"
    result = re.sub(
        rf"\b({RWY_PREFIX})\s*"
        r"(\d{1,2}[LRC]?)\s+"
        r"FOR\s+(?:ALL\s+(?:OTHER\s+)?)?"
        r"(ARR(?:IVALS?)?|DEP(?:ARTURES?)?)\b",
        r"[yellow]\g<0>[/yellow]",
        result,
        flags=re.IGNORECASE,
    )

    # Pattern 10: Parallel operations
    # e.g., "PARL OPS ARE BEING CNTD", "PARALLEL OPS IN USE", "PARL OPERATIONS"
    result = re.sub(
        r"\b(PAR(?:A)?L(?:LEL)?)\s+"
        r"(OPS?|OPERATIONS?)"
        r"(?:\s+(?:ARE\s+)?(?:BEING\s+)?(?:CNTD|CONDUCTED|IN\s+(?:USE|EFFECT|PROG(?:RESS)?)))?\b",
        r"[yellow]\g<0>[/yellow]",
        result,
        flags=re.IGNORECASE,
    )

    # Clean up nested yellow tags - keep only the outermost ones
    # Repeatedly remove inner [yellow] and [/yellow] tags until stable
    prev_result = None
    while prev_result != result:
        prev_result = result
        # Remove [yellow] that appears after another [yellow] without a closing tag in between
        result = re.sub(
            r"(\[yellow\])([^\[]*)\[yellow\]",
            r"\1\2",
            result,
        )
        # Remove [/yellow] that appears before another [/yellow] without an opening tag in between
        result = re.sub(
            r"\[/yellow\]([^\[]*)\[/yellow\]",
            r"\1[/yellow]",
            result,
        )

    return result
