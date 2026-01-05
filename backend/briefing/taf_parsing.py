"""
TAF parsing utilities for weather briefings.

This module provides shared TAF/forecast parsing functionality used by both
the UI weather briefing modal and the weather daemon HTML generator.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from backend.data.weather_parsing import (
    CATEGORY_PRIORITY,
    get_flight_category,
    parse_visibility_sm,
    parse_ceiling_feet,
    parse_ceiling_layer,
    parse_weather_phenomena,
    parse_wind_from_metar,
)


def parse_taf_forecast_details(conditions: str) -> Dict[str, Any]:
    """
    Parse detailed forecast conditions from a TAF segment.

    Args:
        conditions: TAF segment conditions string (after FM/TEMPO/BECMG time code)

    Returns:
        Dict with visibility_sm, ceiling_ft, ceiling_layer, wind, phenomena, category
    """
    category = get_flight_category(conditions)
    visibility_sm = parse_visibility_sm(conditions)
    ceiling_ft = parse_ceiling_feet(conditions)
    ceiling_layer = parse_ceiling_layer(conditions)
    wind = parse_wind_from_metar(conditions)
    phenomena = parse_weather_phenomena(conditions)

    return {
        "category": category,
        "visibility_sm": visibility_sm,
        "ceiling_ft": ceiling_ft,
        "ceiling_layer": ceiling_layer,
        "wind": wind,
        "phenomena": phenomena,
    }


def calculate_trend(
    current_vis: Optional[float],
    current_ceil: Optional[int],
    current_cat: str,
    forecast_vis: Optional[float],
    forecast_ceil: Optional[int],
    forecast_cat: str,
) -> str:
    """
    Calculate weather trend between current and forecast conditions.

    Uses smarter logic: only flags as worsening/improving when:
    - Category actually changes, OR
    - Conditions are approaching a worse category boundary

    Args:
        current_vis: Current visibility in SM
        current_ceil: Current ceiling in feet
        current_cat: Current flight category
        forecast_vis: Forecast visibility in SM
        forecast_ceil: Forecast ceiling in feet
        forecast_cat: Forecast flight category

    Returns:
        "improving", "worsening", or "stable"
    """
    current_priority = CATEGORY_PRIORITY.get(current_cat, 3)
    forecast_priority = CATEGORY_PRIORITY.get(forecast_cat, 3)

    # If category changes, that's definitive
    if forecast_priority < current_priority:
        return "worsening"
    if forecast_priority > current_priority:
        return "improving"

    # Same category - only flag as worsening if approaching the next worse boundary
    boundary_thresholds = {
        "VFR": {"ceil": 4000, "vis": 6.0},
        "MVFR": {"ceil": 1500, "vis": 3.5},
        "IFR": {"ceil": 700, "vis": 1.5},
        "LIFR": {"ceil": 200, "vis": 0.5},
    }

    thresholds = boundary_thresholds.get(forecast_cat, {"ceil": 1000, "vis": 3.0})

    vis_near_boundary = forecast_vis is not None and forecast_vis <= thresholds["vis"]
    ceil_near_boundary = (
        forecast_ceil is not None and forecast_ceil <= thresholds["ceil"]
    )

    if vis_near_boundary or ceil_near_boundary:
        vis_decreasing = (
            current_vis is not None
            and forecast_vis is not None
            and forecast_vis < current_vis - 0.5
        )
        ceil_decreasing = (
            current_ceil is not None
            and forecast_ceil is not None
            and forecast_ceil < current_ceil - 200
        )

        if (vis_near_boundary and vis_decreasing) or (
            ceil_near_boundary and ceil_decreasing
        ):
            return "worsening"

    if current_vis is not None and forecast_vis is not None:
        if forecast_vis > current_vis + 2 and forecast_vis > thresholds["vis"]:
            return "improving"

    if current_ceil is not None and forecast_ceil is not None:
        if forecast_ceil > current_ceil + 1000 and forecast_ceil > thresholds["ceil"]:
            return "improving"

    return "stable"


def parse_taf_changes(
    taf: str,
    current_category: str,
    current_vis: Optional[float] = None,
    current_ceil: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Parse TAF to find forecast weather changes with detailed conditions.

    Args:
        taf: Raw TAF string
        current_category: Current flight category (VFR/MVFR/IFR/LIFR)
        current_vis: Current visibility in SM (for trend comparison)
        current_ceil: Current ceiling in feet (for trend comparison)

    Returns:
        List of changes with type, time, predicted category, detailed conditions, and trend.
        Trend is calculated relative to the previous forecast period (or current METAR for first).
    """
    changes = []
    if not taf:
        return changes

    # Collect all forecast groups with their positions for document-order processing
    all_groups = []

    # Parse FM groups: FM251800 ...conditions...
    fm_pattern = r"FM(\d{6})\s+([^\n]+?)(?=\s+FM|\s+TEMPO|\s+BECMG|\s+PROB|$)"
    for match in re.finditer(fm_pattern, taf, re.DOTALL):
        all_groups.append(
            {
                "pos": match.start(),
                "type": "FM",
                "time_str": match.group(1),
                "conditions": match.group(2),
                "updates_baseline": True,
            }
        )

    # Parse TEMPO/BECMG groups
    tempo_becmg_pattern = r"(TEMPO|BECMG)\s+(\d{4})/(\d{4})\s+([^\n]+?)(?=\s+TEMPO|\s+BECMG|\s+FM|\s+PROB|$)"
    for match in re.finditer(tempo_becmg_pattern, taf, re.DOTALL):
        all_groups.append(
            {
                "pos": match.start(),
                "type": match.group(1),
                "time_str": f"{match.group(2)}/{match.group(3)}",
                "conditions": match.group(4),
                "updates_baseline": False,
            }
        )

    # Sort by position in document (chronological order in TAF)
    all_groups.sort(key=lambda x: x["pos"])

    # Track the "baseline" conditions for trend comparison
    baseline_vis = current_vis
    baseline_ceil = current_ceil
    baseline_cat = current_category

    # Process groups in document order
    for group in all_groups:
        details = parse_taf_forecast_details(group["conditions"])
        predicted_cat = details["category"]

        trend = calculate_trend(
            baseline_vis,
            baseline_ceil,
            baseline_cat,
            details["visibility_sm"],
            details["ceiling_ft"],
            predicted_cat,
        )

        changes.append(
            {
                "type": group["type"],
                "time_str": group["time_str"],
                "category": predicted_cat,
                "visibility_sm": details["visibility_sm"],
                "ceiling_ft": details["ceiling_ft"],
                "ceiling_layer": details["ceiling_layer"],
                "wind": details["wind"],
                "phenomena": details["phenomena"],
                "trend": trend,
                "is_improvement": trend == "improving",
                "is_deterioration": trend == "worsening",
            }
        )

        # FM groups update the baseline for subsequent comparisons
        if group["updates_baseline"]:
            baseline_vis = details["visibility_sm"]
            baseline_ceil = details["ceiling_ft"]
            baseline_cat = predicted_cat

    return changes


def format_taf_relative_time(time_str: str) -> str:
    """
    Format a TAF time string with relative duration.

    Args:
        time_str: TAF time string, either "DDHHMM" (FM format) or "HHMM/HHMM" (TEMPO/BECMG)

    Returns:
        Relative time string like "(in 2h)" or "(1h ago)" or empty string if can't parse
    """
    now = datetime.now(timezone.utc)

    try:
        # FM format: DDHHMM (e.g., "251800" = 25th day at 18:00Z)
        if len(time_str) == 6 and time_str.isdigit():
            day = int(time_str[:2])
            hour = int(time_str[2:4])
            minute = int(time_str[4:6])

            target = now.replace(
                day=day, hour=hour, minute=minute, second=0, microsecond=0
            )

            # Handle month rollover
            if target < now - timedelta(days=15):
                if now.month == 12:
                    target = target.replace(year=now.year + 1, month=1)
                else:
                    target = target.replace(month=now.month + 1)
            elif target > now + timedelta(days=15):
                if now.month == 1:
                    target = target.replace(year=now.year - 1, month=12)
                else:
                    target = target.replace(month=now.month - 1)

        # TEMPO/BECMG format: DDHH/DDHH (e.g., "2515/2518")
        elif "/" in time_str:
            start_str = time_str.split("/")[0]
            if len(start_str) == 4 and start_str.isdigit():
                day = int(start_str[:2])
                hour = int(start_str[2:4])

                target = now.replace(
                    day=day, hour=hour, minute=0, second=0, microsecond=0
                )

                if target < now - timedelta(days=15):
                    if now.month == 12:
                        target = target.replace(year=now.year + 1, month=1)
                    else:
                        target = target.replace(month=now.month + 1)
                elif target > now + timedelta(days=15):
                    if now.month == 1:
                        target = target.replace(year=now.year - 1, month=12)
                    else:
                        target = target.replace(month=now.month - 1)
            else:
                return ""
        else:
            return ""

        # Calculate relative time
        diff_seconds = (target - now).total_seconds()
        abs_hours = abs(diff_seconds) / 3600

        if abs_hours < 1:
            mins = int(abs(diff_seconds) / 60)
            time_label = f"{mins:2d}m"
        elif abs_hours < 24:
            hours = int(abs_hours)
            time_label = f"{hours:2d}h"
        else:
            days = int(abs_hours / 24)
            time_label = f"{days:2d}d"

        if diff_seconds > 0:
            return f"(in {time_label})"
        else:
            return f"({time_label} ago)"

    except (ValueError, AttributeError):
        return ""
