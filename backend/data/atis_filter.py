"""
ATIS text filtering and parsing.

- Removes METAR-duplicated weather info from ATIS text
- Extracts active runway assignments from ATIS
"""

import re
from typing import Any, Dict, Set, Tuple

# =============================================================================
# Shared pattern components for runway/approach parsing and colorization
# =============================================================================

# Runway number pattern with spoken direction forms
# Matches: "17R", "17L", "17 R", "17R AND LEFT", "17R AND 17L", "17R, 17L AND CENTER", "10L OR 10R"
RWY_NUM_PATTERN = (
    r"\d{1,2}\s*[LRC]?(?:\s*(?:AND|OR|&|,|/)\s*(?:\d{1,2}\s*[LRC]?|LEFT|RIGHT|CENTER))*"
)

# Approach type keywords
APPROACH_TYPES = r"ILS|RNAV|VISUAL|VIS|LOC|VOR|NDB|GPS|LDA|SDF|RNP"

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
METAR_REMOVAL_PATTERNS = [
    # Weather intro with time: "WEATHER AT 0100Z.", "WEATHER AT 0130ZUTC."
    r"\bWEATHER\s+AT\s+\d{4}Z?(UTC)?\.?\s*",
    # Observed at time
    r"\bOBSERVED\s+AT\s+\d{4}\s*(UTC)*(UTC)*\.?\s*",
    # METAR reference
    r"\bMETAR\s+\d+\s*",
    # Raw wind format: 07005KT, VRB02KT, 24005G15KT, with optional variable
    r"\b(\d{3}|VRB)\d{2,3}(G\d{2,3})?KT\b(\s+\d{3}V\d{3})?\s*",
    # Visibility: VIS 10KM, 15SM, 9SM, 9999
    r"\bVIS\s+\d+\s*(KM)?\s*",
    r"\b\d+SM\b\s*",
    r"\b9999\b\s*",
    # CAVOK (ceiling and visibility OK)
    r"\bCAVOK\b\s*",
    # Cloud layers: FEW005, SCT100, BKN010, OVC060, VV003 (with optional ///)
    r"\b(FEW|SCT|BKN|OVC|VV)\d{3}(///)?(\s+(FEW|SCT|BKN|OVC|VV)\d{3}(///)?)*\s*",
    # Sky clear
    r"\bSKY\s+CLEAR\b\s*",
    r"\bCLR\b\s*",
    # Temp/Dewpoint various formats: 11/00, M03/M07, T02 DP00, T02 DPM02, "26 22" (space-separated)
    r"\bM?\d{2}/M?\d{2}\b\s*",
    r"\bT\d{2}\s+DPM?\d{2}\b\s*",
    r"\b\d{2}\s+\d{2}\b(?=\s+[AQ]\d{4}|\s*\.|\s*$)\s*",  # "26 22" before altimeter or end
    # Altimeter with spoken: A2997 (TWO NINER SEVEN SEVEN)
    r"\b[AQ]\d{4}\b\s*(\([A-Z\s]+\))?(-?HPA)?\.?\s*",
    r"\bQNH\d{3,4}\b\s*",
    # Weather phenomena: -DZ, +RA, BR, FG, etc. - must be standalone words
    r"(?<!\w)[-+]?(DZ|RA|SN|SG|IC|PL|GR|GS|BR|FG|FU|VA|DU|SA|HZ|PY)(?!\w)\s*",
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
]


def filter_atis_text(atis_text: str) -> str:
    """
    Filter METAR-duplicated information from ATIS text.

    Args:
        atis_text: Full ATIS text as single string

    Returns:
        String with METAR-duplicated info removed, keeping operational info
        like runway assignments, approaches, NOTAMs, etc.
    """
    if not atis_text:
        return ""

    # Remove METAR patterns
    filtered = atis_text
    for pattern in METAR_REMOVAL_PATTERNS:
        filtered = re.sub(pattern, " ", filtered, flags=re.IGNORECASE)

    # Clean up multiple spaces and trim
    filtered = re.sub(r"\s+", " ", filtered).strip()

    # Remove orphaned parentheses and punctuation
    filtered = re.sub(r"\(\s*\)", "", filtered)
    filtered = re.sub(r"\s+\.", ".", filtered)
    filtered = re.sub(r"\s+,", ",", filtered)
    # Clean up multiple dots - normalize to single dot or ellipsis
    filtered = re.sub(r"\.{4,}", "...", filtered)  # 4+ dots -> ellipsis
    filtered = re.sub(r"\.\.\.\.", "...", filtered)  # 4 dots -> ellipsis
    filtered = re.sub(r"(?<!\.)\.\.(?!\.)", ".", filtered)  # exactly 2 dots -> 1 dot
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

    # Pattern 1: Approach type + optional variant + optional comma + RWY + runway numbers
    # e.g., "ILS Z RWY 22R", "RNAV-Y RWY 35", "ILS RWYS 16R AND 16L", "ILS, RWY 12"
    # Also handles spoken forms: "ILS RWYS 17R AND LEFT" means 17R and 17L
    result = re.sub(
        rf"\b({APPROACH_TYPES})[-\s]?([XYZWUK])?,?\s*"
        rf"({RWY_PREFIX})?\s*"
        rf"({RWY_NUM_PATTERN})\b",
        r"[yellow]\g<0>[/yellow]",
        result,
        flags=re.IGNORECASE,
    )

    # Pattern 2: Approach type + APCH/APPROACH + RWY + runway numbers
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

    return result
