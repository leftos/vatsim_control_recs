"""
ATIS text filtering - removes METAR-duplicated weather info from ATIS text.
"""

import re

# Patterns to remove METAR-duplicated info from ATIS text
# These detect and remove raw METAR-format data embedded in ATIS
METAR_REMOVAL_PATTERNS = [
    # Weather intro with time: "WEATHER AT 0100Z.", "WEATHER AT 0130ZUTC."
    r'\bWEATHER\s+AT\s+\d{4}Z?(UTC)?\.?\s*',
    # Observed at time
    r'\bOBSERVED\s+AT\s+\d{4}\s*(UTC)*(UTC)*\.?\s*',
    # METAR reference
    r'\bMETAR\s+\d+\s*',
    # Raw wind format: 07005KT, VRB02KT, 24005G15KT, with optional variable
    r'\b(\d{3}|VRB)\d{2,3}(G\d{2,3})?KT\b(\s+\d{3}V\d{3})?\s*',
    # Visibility: VIS 10KM, 15SM, 9SM, 9999
    r'\bVIS\s+\d+\s*(KM)?\s*',
    r'\b\d+SM\b\s*',
    r'\b9999\b\s*',
    # CAVOK (ceiling and visibility OK)
    r'\bCAVOK\b\s*',
    # Cloud layers: FEW005, SCT100, BKN010, OVC060, VV003 (with optional ///)
    r'\b(FEW|SCT|BKN|OVC|VV)\d{3}(///)?(\s+(FEW|SCT|BKN|OVC|VV)\d{3}(///)?)*\s*',
    # Sky clear
    r'\bSKY\s+CLEAR\b\s*',
    r'\bCLR\b\s*',
    # Temp/Dewpoint various formats: 11/00, M03/M07, T02 DP00, T02 DPM02, "26 22" (space-separated)
    r'\bM?\d{2}/M?\d{2}\b\s*',
    r'\bT\d{2}\s+DPM?\d{2}\b\s*',
    r'\b\d{2}\s+\d{2}\b(?=\s+[AQ]\d{4}|\s*\.|\s*$)\s*',  # "26 22" before altimeter or end
    # Altimeter with spoken: A2997 (TWO NINER SEVEN SEVEN)
    r'\b[AQ]\d{4}\b\s*(\([A-Z\s]+\))?(-?HPA)?\.?\s*',
    r'\bQNH\d{3,4}\b\s*',
    # Weather phenomena: -DZ, +RA, BR, FG, etc. - must be standalone words
    r'(?<!\w)[-+]?(DZ|RA|SN|SG|IC|PL|GR|GS|BR|FG|FU|VA|DU|SA|HZ|PY)(?!\w)\s*',
    # TDZ (touchdown zone) wind readings - remove "WIND RWY XX TDZ" or standalone "TDZ"
    r'\bWIND\s+RWY\s+\d+\s+TDZ\b\s*',
    r'\bTDZ\s+\d{5}(G\d{2,3})?KT\b(\s+\d{3}V\d{3})?\s*',
    r'\bTDZ\s*$',  # TDZ at end
    # Standalone hyphen or dash leftover
    r'\s+-\s+',
    r'\s+-$',
    # Trend: TEMPO, BECMG with conditions
    r'\bTREND\s+\w+(\s+\w+)*\s*',
    # Orphaned "UTC." leftover
    r'\bUTC\.\s*',
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
        filtered = re.sub(pattern, ' ', filtered, flags=re.IGNORECASE)

    # Clean up multiple spaces and trim
    filtered = re.sub(r'\s+', ' ', filtered).strip()

    # Remove orphaned parentheses and punctuation
    filtered = re.sub(r'\(\s*\)', '', filtered)
    filtered = re.sub(r'\s+\.', '.', filtered)
    filtered = re.sub(r'\s+,', ',', filtered)
    # Clean up multiple dots - normalize to single dot or ellipsis
    filtered = re.sub(r'\.{4,}', '...', filtered)  # 4+ dots -> ellipsis
    filtered = re.sub(r'\.\.\.\.', '...', filtered)  # 4 dots -> ellipsis
    filtered = re.sub(r'(?<!\.)\.\.(?!\.)', '.', filtered)  # exactly 2 dots -> 1 dot
    # Clean up space before punctuation one more time
    filtered = re.sub(r'\s+\.', '.', filtered)

    return filtered
