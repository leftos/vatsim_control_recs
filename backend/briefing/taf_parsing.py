"""
TAF parsing utilities for weather briefings.

This module provides shared TAF/forecast parsing functionality used by both
the UI weather briefing modal and the weather daemon HTML generator.
"""

import calendar
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.data.weather_parsing import (
    CATEGORY_PRIORITY,
    get_flight_category,
    parse_ceiling_feet,
    parse_ceiling_layer,
    parse_visibility_sm,
    parse_weather_phenomena,
    parse_wind_from_metar,
)


def parse_taf_forecast_details(conditions: str) -> dict[str, Any]:
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
    current_vis: float | None,
    current_ceil: int | None,
    current_cat: str,
    forecast_vis: float | None,
    forecast_ceil: int | None,
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

    if (
        current_vis is not None
        and forecast_vis is not None
        and forecast_vis > current_vis + 2
        and forecast_vis > thresholds["vis"]
    ):
        return "improving"

    if (
        current_ceil is not None
        and forecast_ceil is not None
        and forecast_ceil > current_ceil + 1000
        and forecast_ceil > thresholds["ceil"]
    ):
        return "improving"

    return "stable"


def parse_taf_changes(
    taf: str,
    current_category: str,
    current_vis: float | None = None,
    current_ceil: int | None = None,
) -> list[dict[str, Any]]:
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
    all_groups.extend(
        {
            "pos": match.start(),
            "type": "FM",
            "time_str": match.group(1),
            "conditions": match.group(2),
            "updates_baseline": True,
        }
        for match in re.finditer(fm_pattern, taf, re.DOTALL)
    )

    # Parse TEMPO/BECMG groups
    tempo_becmg_pattern = r"(TEMPO|BECMG)\s+(\d{4})/(\d{4})\s+([^\n]+?)(?=\s+TEMPO|\s+BECMG|\s+FM|\s+PROB|$)"
    all_groups.extend(
        {
            "pos": match.start(),
            "type": match.group(1),
            "time_str": f"{match.group(2)}/{match.group(3)}",
            "conditions": match.group(4),
            "updates_baseline": False,
        }
        for match in re.finditer(tempo_becmg_pattern, taf, re.DOTALL)
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


def _month_length(year: int, month: int) -> int:
    """Return how many days a month has."""
    return calendar.monthrange(year, month)[1]


def _shifted_month(target: datetime, delta: int) -> tuple[int, int]:
    """Return the ``(year, month)`` pair ``delta`` months from ``target``."""
    index = target.year * 12 + (target.month - 1) + delta
    return index // 12, index % 12 + 1


def _reading_in_month(
    code: tuple[int, int, int], now: datetime, year: int, month: int
) -> datetime | None:
    """
    Read a TAF code as a time inside one month.

    Args:
        code: TAF ``(day, hour, minute)`` code
        now: Reference time, whose remaining date parts the reading keeps
        year: The month's year
        month: The month's number

    Returns:
        The parsed time, or None when that month has no such day
    """
    day, hour, minute = code
    if not 1 <= day <= _month_length(year, month):
        return None
    return now.replace(
        year=year,
        month=month,
        day=day,
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )


def _shift_month_by_one(target: datetime, now: datetime) -> datetime:
    """
    Move a parsed TAF time one month forward or back when it is over 15 days away.

    A TAF day-of-month code carries no month, so a target more than 15 days in the
    past belongs to the next month and one more than 15 days ahead to the previous
    month. A month lacking the target's day keeps the current reading.

    Args:
        target: Parsed time read as a day of ``now``'s month
        now: Reference time

    Returns:
        The parsed time, moved one month when it falls outside the 15-day window
    """
    if target < now - timedelta(days=15):
        delta = 1
    elif target > now + timedelta(days=15):
        delta = -1
    else:
        return target

    year, month = _shifted_month(target, delta)
    if target.day > _month_length(year, month):
        return target
    return target.replace(year=year, month=month)


def _parse_day_time(day: int, hour: int, minute: int, now: datetime) -> datetime:
    """
    Read a TAF day, hour and minute as a time near ``now``.

    The code carries no month, so it is read in ``now``'s month when that month has
    the day, moved one month toward ``now`` when the reading falls outside the
    15-day window. When ``now``'s month has no such day, the reading comes from the
    nearer of the previous and the next month that have it, the previous on a tie.

    Args:
        day: TAF day-of-month code
        hour: Hour of the day
        minute: Minute of the hour
        now: Reference time

    Returns:
        The parsed time

    Raises:
        ValueError: When no month near ``now`` has the day, or the hour is out of range
    """
    code = (day, hour, minute)
    current = _reading_in_month(code, now, now.year, now.month)
    if current is not None:
        return _shift_month_by_one(current, now)

    previous_year, previous_month = _shifted_month(now, -1)
    next_year, next_month = _shifted_month(now, 1)
    previous = _reading_in_month(code, now, previous_year, previous_month)
    following = _reading_in_month(code, now, next_year, next_month)
    if previous is None and following is None:
        raise ValueError(f"no month near {now:%Y-%m} has a day {day}")
    if following is None:
        return previous
    if previous is None:
        return following
    return previous if now - previous <= following - now else following


def _parse_fm_time(time_str: str, now: datetime) -> datetime:
    """Parse an FM group's ``DDHHMM`` code into a time near ``now``."""
    return _parse_day_time(
        int(time_str[:2]), int(time_str[2:4]), int(time_str[4:6]), now
    )


def _parse_period_start(time_str: str, now: datetime) -> datetime | None:
    """Parse the start of a TEMPO/BECMG ``DDHH/DDHH`` code, or None if malformed."""
    start_str = time_str.split("/")[0]
    if len(start_str) != 4 or not start_str.isdigit():
        return None
    return _parse_day_time(int(start_str[:2]), int(start_str[2:4]), 0, now)


def _format_time_label(diff_seconds: float) -> str:
    """Format a signed second offset as ``(in ...)`` or ``(... ago)``."""
    abs_hours = abs(diff_seconds) / 3600

    if abs_hours < 1:
        time_label = f"{int(abs(diff_seconds) / 60):2d}m"
    elif abs_hours < 24:
        time_label = f"{int(abs_hours):2d}h"
    else:
        time_label = f"{int(abs_hours / 24):2d}d"

    if diff_seconds > 0:
        return f"(in {time_label})"
    return f"({time_label} ago)"


def format_taf_relative_time(time_str: str) -> str:
    """
    Format a TAF time string with relative duration.

    Args:
        time_str: TAF time string, either "DDHHMM" (FM format) or "DDHH/DDHH" (TEMPO/BECMG)

    Returns:
        Relative time string like "(in 2h)" or "(1h ago)" or empty string if can't parse
    """
    now = datetime.now(UTC)

    try:
        if len(time_str) == 6 and time_str.isdigit():
            target = _parse_fm_time(time_str, now)
        elif "/" in time_str:
            target = _parse_period_start(time_str, now)
            if target is None:
                return ""
        else:
            return ""

        return _format_time_label((target - now).total_seconds())

    except (ValueError, AttributeError):
        return ""
