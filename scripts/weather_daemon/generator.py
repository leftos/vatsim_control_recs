"""
Headless Weather Briefing Generator

Generates HTML weather briefings without requiring the Textual UI.
Reuses core logic from ui/modals/weather_briefing.py.
"""

import hashlib
import json
import logging
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Set, Tuple

from rich.console import Console

# Set up logging
logger = logging.getLogger("weather_daemon")

# Maximum age for a lock file before considering it stale (seconds)
# If a lock file is older than this AND the PID is not running, it's stale
LOCK_STALE_TIMEOUT = 600  # 10 minutes


def _is_process_running(pid: int) -> bool:
    """Check if a process with given PID is running."""
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x100000
            process = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if process:
                kernel32.CloseHandle(process)
                return True
            return False
        else:
            # Unix: send signal 0 to check if process exists
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError):
        return False
    except Exception:
        # If we can't determine, assume it's running to be safe
        return True


def _check_stale_lock(lock_file: Path) -> bool:
    """
    Check if a lock file is stale (process crashed without releasing).

    Returns True if lock is stale and was cleaned up, False otherwise.
    """
    if not lock_file.exists():
        return False

    try:
        # Read PID from lock file
        content = lock_file.read_text().strip()
        if not content:
            return False

        pid = int(content.split("\n")[0])

        # Check if process is still running
        if _is_process_running(pid):
            return False  # Process is alive, lock is valid

        # Process is not running - check file age as safety measure
        file_age = datetime.now().timestamp() - lock_file.stat().st_mtime
        if file_age < LOCK_STALE_TIMEOUT:
            # File is recent, maybe process just started - be conservative
            logger.debug(
                f"Lock file PID {pid} not running but file is recent ({file_age:.0f}s old)"
            )
            return False

        # Lock is stale - remove it
        logger.warning(
            f"Removing stale lock file (PID {pid} not running, file {file_age:.0f}s old)"
        )
        lock_file.unlink()
        return True

    except (ValueError, OSError) as e:
        logger.warning(f"Error checking stale lock: {e}")
        return False


# Platform-specific file locking
if sys.platform == "win32":
    import msvcrt

    @contextmanager
    def acquire_lock(lock_file: Path, timeout: int = 0) -> Generator[bool, None, None]:
        """
        Context manager for acquiring an exclusive lock file (Windows version).

        Args:
            lock_file: Path to the lock file
            timeout: Not used (kept for API compatibility), lock is non-blocking

        Yields:
            True if lock was acquired, False if another process holds it
        """
        lock_file.parent.mkdir(parents=True, exist_ok=True)

        # Check for stale lock before attempting to acquire
        _check_stale_lock(lock_file)

        lock_fd = None
        acquired = False

        try:
            lock_fd = open(lock_file, "w")
            try:
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
                # Write PID and timestamp to lock file for debugging
                lock_fd.write(f"{os.getpid()}\n")
                lock_fd.write(f"{datetime.now(timezone.utc).isoformat()}\n")
                lock_fd.flush()
                logger.debug(f"Acquired lock: {lock_file}")
            except (IOError, OSError):
                # Lock is held by another process
                logger.info(f"Lock already held by another process: {lock_file}")
                acquired = False

            yield acquired

        finally:
            if lock_fd:
                if acquired:
                    try:
                        lock_fd.seek(0)
                        msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
                        logger.debug(f"Released lock: {lock_file}")
                    except (IOError, OSError):
                        pass
                lock_fd.close()
else:
    import fcntl

    @contextmanager
    def acquire_lock(lock_file: Path, timeout: int = 0) -> Generator[bool, None, None]:
        """
        Context manager for acquiring an exclusive lock file (Unix version).

        Includes stale lock detection: if the PID in the lock file is no longer
        running and the file is older than LOCK_STALE_TIMEOUT, the lock is
        considered stale and removed.

        Args:
            lock_file: Path to the lock file
            timeout: Not used (kept for API compatibility), lock is non-blocking

        Yields:
            True if lock was acquired, False if another process holds it

        Example:
            with acquire_lock(Path("/tmp/weather.lock")) as acquired:
                if not acquired:
                    print("Another instance is running")
                    return
                # Do work...
        """
        lock_file.parent.mkdir(parents=True, exist_ok=True)

        # Check for stale lock before attempting to acquire
        _check_stale_lock(lock_file)

        lock_fd = None
        acquired = False

        try:
            lock_fd = open(lock_file, "w")
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                # Write PID and timestamp to lock file for debugging
                lock_fd.write(f"{os.getpid()}\n")
                lock_fd.write(f"{datetime.now(timezone.utc).isoformat()}\n")
                lock_fd.flush()
                logger.debug(f"Acquired lock: {lock_file}")
            except (IOError, OSError):
                # Lock is held by another process
                logger.info(f"Lock already held by another process: {lock_file}")
                acquired = False

            yield acquired

        finally:
            if lock_fd:
                if acquired:
                    try:
                        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                        logger.debug(f"Released lock: {lock_file}")
                    except (IOError, OSError):
                        pass
                lock_fd.close()


def compute_weather_hash(metars: Dict[str, str], tafs: Dict[str, str]) -> str:
    """
    Compute a hash of weather data to detect changes.

    Only hashes METARs and TAFs (not ATIS, which changes frequently
    and doesn't affect the visual presentation much).

    Args:
        metars: Dict of ICAO -> METAR string
        tafs: Dict of ICAO -> TAF string

    Returns:
        SHA256 hash of the weather data
    """
    # Sort keys for consistent ordering
    metar_str = json.dumps(metars, sort_keys=True)
    taf_str = json.dumps(tafs, sort_keys=True)
    combined = f"{metar_str}\n{taf_str}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


def load_weather_hash(cache_dir: Path) -> Optional[str]:
    """Load the previously saved weather hash."""
    hash_file = cache_dir / "weather_hash.txt"
    if hash_file.exists():
        try:
            return hash_file.read_text().strip()
        except Exception:
            return None
    return None


def save_weather_hash(cache_dir: Path, weather_hash: str) -> None:
    """Save the current weather hash."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    hash_file = cache_dir / "weather_hash.txt"
    try:
        hash_file.write_text(weather_hash)
    except Exception as e:
        logger.warning(f"Failed to save weather hash: {e}")


def _save_weather_cache(
    cache_dir: Path, metars: Dict, tafs: Dict, atis_data: Dict
) -> None:
    """Save weather data to cache for later use with --use-cached."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "weather_cache.json"

    cache_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metars": metars,
        "tafs": tafs,
        "atis": atis_data,
    }

    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f)
        logger.debug(f"Saved weather cache to {cache_file}")
    except Exception as e:
        logger.warning(f"Failed to save weather cache: {e}")


def _load_weather_cache(
    cache_dir: Path, max_age_seconds: Optional[int] = None
) -> Optional[Tuple[Dict, Dict, Dict, str]]:
    """Load weather data from cache.

    Args:
        cache_dir: Directory containing cache file
        max_age_seconds: If set, only return cache if fresher than this many seconds

    Returns:
        (metars, tafs, atis, timestamp) or None if cache missing/expired
    """
    cache_file = cache_dir / "weather_cache.json"

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            cache_data = json.load(f)

        timestamp_str = cache_data.get("timestamp", "")

        # Check TTL if specified
        if max_age_seconds is not None and timestamp_str:
            try:
                cache_time = datetime.fromisoformat(
                    timestamp_str.replace("Z", "+00:00")
                )
                age = (datetime.now(timezone.utc) - cache_time).total_seconds()
                if age > max_age_seconds:
                    logger.debug(
                        f"Cache expired ({age:.0f}s old, TTL={max_age_seconds}s)"
                    )
                    return None
            except (ValueError, TypeError):
                pass  # Can't parse timestamp, ignore TTL check

        return (
            cache_data.get("metars", {}),
            cache_data.get("tafs", {}),
            cache_data.get("atis", {}),
            timestamp_str or "unknown",
        )
    except Exception as e:
        logger.warning(f"Failed to load weather cache: {e}")
        return None


class ProgressTracker:
    """Tracks and reports progress for long-running operations."""

    def __init__(self, operation: str, total: int, log_interval_pct: int = 10):
        """
        Initialize progress tracker.

        Args:
            operation: Name of the operation (e.g., "Fetching METAR")
            total: Total number of items to process
            log_interval_pct: How often to log progress (percentage)
        """
        self.operation = operation
        self.total = total
        self.completed = 0
        self.log_interval_pct = log_interval_pct
        self.last_logged_pct = -log_interval_pct  # Ensure first update logs
        self.start_time = datetime.now(timezone.utc)

    def update(self, completed: Optional[int] = None, increment: int = 1) -> None:
        """Update progress and print/log if at interval."""
        if completed is not None:
            self.completed = completed
        else:
            self.completed += increment

        if self.total == 0:
            return

        pct = int((self.completed / self.total) * 100)

        # Log at intervals
        if (
            pct >= self.last_logged_pct + self.log_interval_pct
            or self.completed == self.total
        ):
            self._report(pct)
            self.last_logged_pct = pct

    def _report(self, pct: int) -> None:
        """Report progress to console and log."""
        elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds()

        if self.completed == self.total:
            msg = f"    {self.operation}: {self.completed}/{self.total} (100%) - {elapsed:.1f}s"
            print(msg)
            logger.info(
                f"{self.operation}: completed {self.total} items in {elapsed:.1f}s"
            )
        else:
            # Estimate remaining time
            if self.completed > 0:
                rate = self.completed / elapsed
                remaining = (self.total - self.completed) / rate if rate > 0 else 0
                msg = f"    {self.operation}: {self.completed}/{self.total} ({pct}%) - ETA {remaining:.0f}s"
            else:
                msg = f"    {self.operation}: {self.completed}/{self.total} ({pct}%)"
            print(msg)
            logger.debug(f"{self.operation}: {pct}% ({self.completed}/{self.total})")

    def callback(self, completed: int, total: int) -> None:
        """Callback compatible with batch fetch functions."""
        self.update(completed=completed)


# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend import (  # noqa: E402
    get_metar_batch,
    get_taf_batch,
    get_rate_limit_status,
    fetch_weather_bbox,
    load_all_groupings,
    load_unified_airport_data,
)
from backend.briefing import (  # noqa: E402
    AreaClusterer,
    count_area_categories,
    build_area_summary,
    parse_wind_from_metar,
    parse_taf_changes,
    format_taf_relative_time,
)
from .artcc_boundaries import get_artcc_boundaries  # noqa: E402
from .tile_generator import generate_weather_tiles  # noqa: E402
from backend.core.groupings import (  # noqa: E402
    load_custom_groupings,
    resolve_grouping_recursively,
)
from backend.data.vatsim_api import download_vatsim_data, get_atis_for_airports  # noqa: E402
from backend.data.atis_filter import filter_atis_text, colorize_atis_text  # noqa: E402
from airport_disambiguator import AirportDisambiguator  # noqa: E402

from .config import DaemonConfig, CATEGORY_COLORS, ARTCC_NAMES  # noqa: E402

# Import METAR parsing functions
from backend.data.weather_parsing import (  # noqa: E402
    get_flight_category,
    extract_visibility_str,
    parse_ceiling_layer,
    parse_visibility_sm,
    parse_ceiling_feet,
    parse_weather_phenomena,
    parse_metar_obs_time,
    format_obs_time_display,
    is_speci_metar,
)


def get_artcc_bboxes(
    artcc_codes: Set[str], cache_dir: Path, padding_degrees: float = 0.5
) -> Dict[str, Tuple[float, float, float, float]]:
    """
    Calculate bounding boxes for ARTCCs from their polygon boundaries.

    Adds padding to each bounding box to ensure airports at the edges
    of ARTCC boundaries are captured (since the polygon-to-bbox conversion
    may exclude some airports that are technically within the ARTCC).

    Args:
        artcc_codes: Set of ARTCC codes to get bboxes for
        cache_dir: Cache directory for ARTCC boundary data
        padding_degrees: Extra degrees to add around the bbox (default: 0.5, ~30nm)

    Returns:
        Dict mapping ARTCC codes to (min_lat, min_lon, max_lat, max_lon)
    """
    boundaries = get_artcc_boundaries(cache_dir)
    bboxes = {}

    for artcc in artcc_codes:
        if artcc not in boundaries or artcc == "custom":
            continue

        polygons = boundaries[artcc]
        all_points = []
        for polygon in polygons:
            all_points.extend(polygon)

        if not all_points:
            continue

        min_lat = min(p[0] for p in all_points) - padding_degrees
        max_lat = max(p[0] for p in all_points) + padding_degrees
        min_lon = min(p[1] for p in all_points) - padding_degrees
        max_lon = max(p[1] for p in all_points) + padding_degrees

        # Clamp to valid ranges
        min_lat = max(-90.0, min_lat)
        max_lat = min(90.0, max_lat)
        min_lon = max(-180.0, min_lon)
        max_lon = min(180.0, max_lon)

        bboxes[artcc] = (min_lat, min_lon, max_lat, max_lon)

    return bboxes


class WeatherBriefingGenerator:
    """Generates weather briefings for a grouping without UI dependencies."""

    def __init__(
        self,
        grouping_name: str,
        airports: List[str],
        unified_airport_data: Dict[str, Dict],
        disambiguator: AirportDisambiguator,
    ):
        self.grouping_name = grouping_name
        self.airports = airports
        self.unified_airport_data = unified_airport_data
        self.disambiguator = disambiguator
        self.weather_data: Dict[str, Dict[str, Any]] = {}

    def fetch_weather_data(
        self,
        metars: Dict[str, str],
        tafs: Dict[str, str],
        atis_data: Dict[str, Dict],
    ) -> None:
        """Build weather data structure from pre-fetched data."""
        for icao in self.airports:
            metar = metars.get(icao, "")
            taf = tafs.get(icao, "")
            category = get_flight_category(metar) if metar else "UNK"
            color = CATEGORY_COLORS.get(category, "white")
            visibility_sm = parse_visibility_sm(metar) if metar else None
            ceiling_ft = parse_ceiling_feet(metar) if metar else None
            obs_time = parse_metar_obs_time(metar) if metar else None

            self.weather_data[icao] = {
                "metar": metar,
                "taf": taf,
                "category": category,
                "color": color,
                "visibility": extract_visibility_str(metar) if metar else None,
                "visibility_sm": visibility_sm,
                "ceiling": parse_ceiling_layer(metar) if metar else None,
                "ceiling_ft": ceiling_ft,
                "wind": parse_wind_from_metar(metar) if metar else None,
                "atis": atis_data.get(icao),
                "phenomena": parse_weather_phenomena(metar) if metar else [],
                "taf_changes": parse_taf_changes(
                    taf, category, visibility_sm, ceiling_ft
                )
                if taf
                else [],
                "obs_time": obs_time,
                "is_speci": is_speci_metar(metar) if metar else False,
            }

    def _get_airport_coords(self, icao: str) -> Optional[Tuple[float, float]]:
        """Get airport coordinates."""
        airport_info = self.unified_airport_data.get(icao, {})
        lat = airport_info.get("latitude")
        lon = airport_info.get("longitude")
        if lat is not None and lon is not None:
            return (lat, lon)
        return None

    def _create_area_groups(self) -> List[Dict[str, Any]]:
        """Create area-based groupings using shared AreaClusterer."""
        clusterer = AreaClusterer(
            weather_data=self.weather_data,
            unified_airport_data=self.unified_airport_data,
            disambiguator=self.disambiguator,
        )
        return clusterer.create_area_groups()

    def _get_common_obs_time(self) -> Tuple[Optional[str], Set[str]]:
        """
        Find the most common METAR observation time and airports with different times.

        Biases towards XX53 times since that's the standard METAR issuance time
        (METARs are typically issued at 53 minutes past the hour).

        Returns:
            Tuple of (most_common_time, set of airports with different times)
        """
        from collections import Counter

        # Count observation times (just HHMM, ignoring day)
        time_counts: Counter = Counter()
        airport_times: Dict[str, str] = {}

        for icao, data in self.weather_data.items():
            obs_time = data.get("obs_time")
            if obs_time and len(obs_time) == 6:
                # Use just HHMM for comparison (ignore day)
                hhmm = obs_time[2:]
                time_counts[hhmm] += 1
                airport_times[icao] = hhmm

        if not time_counts:
            return (None, set())

        # Find most common time
        most_common_hhmm, most_common_count = time_counts.most_common(1)[0]
        total_count = sum(time_counts.values())

        # Bias towards XX53 times (standard METAR issuance time)
        # Find any XX53 time and prefer it if it has reasonable representation
        xx53_times = [
            (hhmm, count) for hhmm, count in time_counts.items() if hhmm[2:] == "53"
        ]
        if xx53_times:
            # Get the most common XX53 time (could be different hours)
            best_xx53, xx53_count = max(xx53_times, key=lambda x: x[1])

            # Use XX53 if it has at least 25% of total OR at least 60% of the most common count
            if (
                xx53_count >= total_count * 0.25
                or xx53_count >= most_common_count * 0.6
            ):
                most_common_hhmm = best_xx53

        # Find airports with different times
        different_airports = {
            icao for icao, hhmm in airport_times.items() if hhmm != most_common_hhmm
        }

        return (most_common_hhmm, different_airports)

    def _get_latest_obs_time(self) -> Optional[str]:
        """
        Find the latest (most recent) METAR observation time across all airports.

        Returns:
            The latest observation time as HHMM string, or None if no times found.
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        current_day = now.day
        current_hour = now.hour
        current_minute = now.minute

        latest_time: Optional[str] = None
        latest_minutes_ago: Optional[int] = None

        for icao, data in self.weather_data.items():
            obs_time = data.get("obs_time")
            if obs_time and len(obs_time) == 6:
                # Parse DDHHMM format
                obs_day = int(obs_time[0:2])
                obs_hour = int(obs_time[2:4])
                obs_minute = int(obs_time[4:6])

                # Calculate minutes ago (handle day rollover)
                # Assume observations are within the last 24 hours
                day_diff = current_day - obs_day
                if day_diff < 0:
                    # Month rollover (e.g., current day 1, obs day 31)
                    day_diff = (
                        1
                        if day_diff == -30 or day_diff == -29 or day_diff == -28
                        else 1
                    )

                minutes_ago = (
                    day_diff * 24 * 60
                    + (current_hour - obs_hour) * 60
                    + (current_minute - obs_minute)
                )

                # Handle negative (future) times - likely means yesterday's obs
                if minutes_ago < -60:
                    minutes_ago += 24 * 60

                if latest_minutes_ago is None or minutes_ago < latest_minutes_ago:
                    latest_minutes_ago = minutes_ago
                    latest_time = obs_time[2:]  # Just HHMM

        return latest_time

    def _build_airport_card(
        self, icao: str, data: Dict[str, Any], show_obs_time: bool = False
    ) -> str:
        """Build a Rich markup card for one airport.

        Args:
            icao: Airport ICAO code
            data: Weather data dict for this airport
            show_obs_time: If True, show observation time in header (for airports with different times)
        """
        lines = []

        category = data.get("category", "UNK")
        color = CATEGORY_COLORS.get(category, "white")
        pretty_name = (
            self.disambiguator.get_full_name(icao) if self.disambiguator else icao
        )

        # Build header with optional observation time and SPECI indicator
        obs_time_str = ""
        if show_obs_time:
            obs_time = data.get("obs_time")
            if obs_time:
                formatted_time = format_obs_time_display(obs_time)
                obs_time_str = f" [#999999]({formatted_time})[/#999999]"

        # Add SPECI indicator if this is a special weather report
        speci_str = ""
        if data.get("is_speci"):
            speci_str = " [#ff9900 bold]SPECI[/#ff9900 bold]"

        header = f"{icao} - {pretty_name}{obs_time_str} // [{color} bold]{category}[/{color} bold]{speci_str}"
        lines.append(header)

        conditions_parts = []
        ceiling = data.get("ceiling")
        if ceiling:
            conditions_parts.append(f"Ceiling: {ceiling}")
        else:
            conditions_parts.append("Ceiling: CLR")

        visibility = data.get("visibility")
        if visibility:
            conditions_parts.append(f"Vis: {visibility}")

        wind = data.get("wind")
        if wind:
            conditions_parts.append(f"Wind: {wind}")

        if conditions_parts:
            lines.append("  " + " | ".join(conditions_parts))

        phenomena = data.get("phenomena", [])
        if phenomena:
            lines.append(f"  Weather: {', '.join(phenomena)}")

        taf_changes = data.get("taf_changes", [])
        significant_changes = [
            c
            for c in taf_changes
            if c.get("is_improvement") or c.get("is_deterioration")
        ]

        if significant_changes:
            taf_entries = []
            for change in significant_changes[:2]:
                change_type = change.get("type", "")
                time_str = change.get("time_str", "")
                pred_category = change.get("category", "UNK")
                trend = change.get("trend", "stable")

                if trend == "worsening":
                    indicator = "[#ff9999 bold]▼[/#ff9999 bold]"
                else:
                    indicator = "[#66ff66 bold]▲[/#66ff66 bold]"

                raw_relative = format_taf_relative_time(time_str)
                relative_time = (
                    f" [#999999]{raw_relative}[/#999999]" if raw_relative else ""
                )
                pred_color = CATEGORY_COLORS.get(pred_category, "white")

                parts = {"vis": "", "ceil": "", "wind": "", "wx": ""}

                vis_sm = change.get("visibility_sm")
                if vis_sm is not None:
                    if vis_sm >= 6:
                        parts["vis"] = "P6SM"
                    elif vis_sm == int(vis_sm):
                        parts["vis"] = f"{int(vis_sm)}SM"
                    else:
                        parts["vis"] = f"{vis_sm:.1f}SM"

                ceiling_layer = change.get("ceiling_layer")
                if ceiling_layer:
                    parts["ceil"] = ceiling_layer

                taf_wind = change.get("wind")
                if taf_wind:
                    parts["wind"] = taf_wind

                taf_phenomena = change.get("phenomena", [])
                if taf_phenomena:
                    parts["wx"] = ", ".join(taf_phenomena)

                taf_entries.append(
                    {
                        "indicator": indicator,
                        "change_type": change_type,
                        "time_str": time_str,
                        "relative_time": relative_time,
                        "pred_category": pred_category,
                        "pred_color": pred_color,
                        "parts": parts,
                    }
                )

            def strip_markup(s: str) -> str:
                return re.sub(r"\[/?[^\]]+\]", "", s)

            max_widths = {
                "prefix": max(
                    (
                        len(
                            f"TAF {e['change_type']} {e['time_str']}{strip_markup(e['relative_time'])}"
                        )
                        for e in taf_entries
                    ),
                    default=0,
                ),
                "cat": max((len(e["pred_category"]) for e in taf_entries), default=0),
                "vis": max((len(e["parts"]["vis"]) for e in taf_entries), default=0),
                "ceil": max((len(e["parts"]["ceil"]) for e in taf_entries), default=0),
                "wind": max((len(e["parts"]["wind"]) for e in taf_entries), default=0),
                "wx": max((len(e["parts"]["wx"]) for e in taf_entries), default=0),
            }

            for entry in taf_entries:
                parts = entry["parts"]
                pred_color = entry["pred_color"]
                pred_category = entry["pred_category"]

                prefix_plain = f"TAF {entry['change_type']} {entry['time_str']}{strip_markup(entry['relative_time'])}"
                prefix_padding = " " * (max_widths["prefix"] - len(prefix_plain))
                prefix_with_markup = f"TAF {entry['change_type']} {entry['time_str']}{entry['relative_time']}{prefix_padding}"

                cat_padded = pred_category.rjust(max_widths["cat"])

                padded_parts = []
                if max_widths["vis"] > 0:
                    padded_parts.append(parts["vis"].rjust(max_widths["vis"]))
                if max_widths["ceil"] > 0:
                    padded_parts.append(parts["ceil"].ljust(max_widths["ceil"]))
                if max_widths["wind"] > 0:
                    padded_parts.append(parts["wind"].ljust(max_widths["wind"]))
                if max_widths["wx"] > 0:
                    padded_parts.append(parts["wx"].ljust(max_widths["wx"]))

                details_str = " | ".join(padded_parts).rstrip() if padded_parts else ""
                if details_str:
                    lines.append(
                        f"  {entry['indicator']} {prefix_with_markup}: [{pred_color} bold]{cat_padded}[/{pred_color} bold] - {details_str}"
                    )
                else:
                    lines.append(
                        f"  {entry['indicator']} {prefix_with_markup}: [{pred_color} bold]{cat_padded}[/{pred_color} bold]"
                    )

        atis = data.get("atis")
        if atis:
            atis_code = atis.get("atis_code", "")
            raw_text = atis.get("text_atis", "")
            filtered_text = filter_atis_text(raw_text)
            if filtered_text:
                display_text = colorize_atis_text(filtered_text, atis_code)
                lines.append(f"  [#aaaaaa]{display_text}[/#aaaaaa]")

        return "\n".join(lines)

    def generate_html(self) -> str:
        """Generate HTML content for the weather briefing."""
        if not self.weather_data:
            return "<html><body><p>No weather data available</p></body></html>"

        # Use StringIO to prevent console output during recording
        console = Console(record=True, force_terminal=True, width=500, file=StringIO())

        # Get the most common observation time and airports with different times
        common_obs_hhmm, different_airports = self._get_common_obs_time()
        latest_obs_hhmm = self._get_latest_obs_time()

        console.print(f"[bold]Weather Briefing: {self.grouping_name}[/bold]")

        # Show latest METAR observation time
        display_time = latest_obs_hhmm or common_obs_hhmm
        if display_time:
            now = datetime.now(timezone.utc)
            date_str = now.strftime("%Y-%m-%d")
            console.print(
                f"METARs current as of: [#aaaaff]{date_str} {display_time}Z[/#aaaaff]\n"
            )
        else:
            # Fallback if no observation times found
            now = datetime.now(timezone.utc)
            zulu_str = now.strftime("%Y-%m-%d %H%MZ")
            console.print(f"Generated: [#aaaaff]{zulu_str}[/#aaaaff]\n")

        category_counts = {"LIFR": 0, "IFR": 0, "MVFR": 0, "VFR": 0, "UNK": 0}
        for data in self.weather_data.values():
            cat = data.get("category", "UNK")
            category_counts[cat] = category_counts.get(cat, 0) + 1

        summary_parts = []
        if category_counts["LIFR"] > 0:
            summary_parts.append(f"[#ffaaff]{category_counts['LIFR']} LIFR[/#ffaaff]")
        if category_counts["IFR"] > 0:
            summary_parts.append(f"[#ff9999]{category_counts['IFR']} IFR[/#ff9999]")
        if category_counts["MVFR"] > 0:
            summary_parts.append(f"[#77bbff]{category_counts['MVFR']} MVFR[/#77bbff]")
        if category_counts["VFR"] > 0:
            summary_parts.append(f"[#66ff66]{category_counts['VFR']} VFR[/#66ff66]")

        if summary_parts:
            console.print(" | ".join(summary_parts))
        console.print()

        area_groups = self._create_area_groups()

        for area in area_groups:
            if not area["airports"]:
                continue

            # Count categories and build summary using shared functions
            area_cats = count_area_categories(area["airports"])
            area_summary = build_area_summary(area_cats, color_scheme="html")

            console.print(
                f"[bold #66cccc]━━━ {area['name'].upper()} ━━━[/bold #66cccc]"
            )
            if area_summary:
                console.print(area_summary)

            for icao, data, _size in area["airports"]:
                show_time = icao in different_airports
                card = self._build_airport_card(icao, data, show_obs_time=show_time)
                console.print(card)
            console.print()

        html_content = console.export_html(inline_styles=True)

        # Add cache control meta tags to prevent browser caching
        html_content = html_content.replace(
            "<head>",
            """<head>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">""",
        )

        html_content = html_content.replace(
            "</style>",
            """pre { margin: 0; padding: 0; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word; }
body { margin: 20px; background: #1a1a1a; color: #e0e0e0; max-width: 100%; }
</style>""",
        )
        html_content = html_content.replace("<body>\n    <pre", "<body>\n<pre")

        return html_content

    def get_category_summary(self) -> Dict[str, int]:
        """Get category counts for this briefing."""
        counts = {"LIFR": 0, "IFR": 0, "MVFR": 0, "VFR": 0, "UNK": 0}
        for data in self.weather_data.values():
            cat = data.get("category", "UNK")
            counts[cat] = counts.get(cat, 0) + 1
        return counts

    def get_airport_weather_points(self) -> List[Dict[str, Any]]:
        """
        Get per-airport weather data with coordinates for map visualization.

        Returns:
            List of dicts with {icao, lat, lon, category, ...weather details} for each airport
        """
        points = []
        for icao, data in self.weather_data.items():
            coords = self._get_airport_coords(icao)
            if coords:
                # Extract significant TAF changes for tooltip display
                taf_changes = data.get("taf_changes", [])
                significant_changes = []
                for c in taf_changes:
                    if c.get("is_improvement") or c.get("is_deterioration"):
                        # Format visibility
                        vis_str = None
                        vis_sm = c.get("visibility_sm")
                        if vis_sm is not None:
                            if vis_sm >= 6:
                                vis_str = "P6SM"
                            elif vis_sm == int(vis_sm):
                                vis_str = f"{int(vis_sm)}SM"
                            else:
                                vis_str = f"{vis_sm:.1f}SM"

                        significant_changes.append(
                            {
                                "type": c.get("type", ""),
                                "time_str": c.get("time_str", ""),
                                "category": c.get("category", "UNK"),
                                "trend": c.get("trend", "stable"),
                                "visibility": vis_str,
                                "ceiling": c.get("ceiling_layer"),
                                "wind": c.get("wind"),
                                "phenomena": c.get("phenomena", []),
                            }
                        )
                        if len(significant_changes) >= 2:
                            break

                points.append(
                    {
                        "icao": icao,
                        "lat": coords[0],
                        "lon": coords[1],
                        "category": data.get("category", "UNK"),
                        "visibility": data.get("visibility"),
                        "ceiling": data.get("ceiling"),
                        "wind": data.get("wind"),
                        "phenomena": data.get("phenomena", []),
                        "taf_changes": significant_changes,
                    }
                )
        return points


def _generate_artcc_wide_briefings(
    config: DaemonConfig,
    groupings_to_process: Dict[str, Tuple[List[str], str]],
    metars: Dict[str, str],
    tafs: Dict[str, str],
    atis_data: Dict[str, Any],
    unified_airport_data: Dict[str, Any],
    disambiguator: Any,
    generated_files: Dict[str, str],
) -> None:
    """
    Generate ARTCC-wide briefings containing all airports for each ARTCC.

    This is a shared helper used by both generate_all_briefings and
    generate_with_cached_weather to avoid code duplication.
    """

    # Collect all unique airports per ARTCC
    artcc_all_airports: Dict[str, Set[str]] = {}
    for grouping_name, (airports, artcc) in groupings_to_process.items():
        if artcc != "custom":
            if artcc not in artcc_all_airports:
                artcc_all_airports[artcc] = set()
            artcc_all_airports[artcc].update(airports)

    if not artcc_all_airports:
        return

    print(f"  Generating {len(artcc_all_airports)} ARTCC-wide briefings...")
    logger.info(f"Generating {len(artcc_all_airports)} ARTCC-wide briefings")

    for artcc, airports in artcc_all_airports.items():
        display_name = ARTCC_NAMES.get(artcc, artcc)
        grouping_name = f"All {display_name} Airports"

        generator = WeatherBriefingGenerator(
            grouping_name=grouping_name,
            airports=list(airports),
            unified_airport_data=unified_airport_data,
            disambiguator=disambiguator,
        )

        generator.fetch_weather_data(metars, tafs, atis_data)
        html_content = generator.generate_html()

        # Write to ARTCC directory as _all.html
        artcc_dir = config.output_dir / artcc
        artcc_dir.mkdir(parents=True, exist_ok=True)
        output_path = artcc_dir / "_all.html"

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        generated_files[str(output_path)] = grouping_name
        logger.debug(f"Generated ARTCC briefing: {output_path}")


def generate(config: DaemonConfig) -> Dict[str, str]:
    """
    Generate weather briefings based on config settings.

    Stages controlled by config:
    - fetch_fresh_weather: Fetch new weather data (False = use cached only)
    - generate_briefings: Generate HTML briefing pages
    - generate_tiles: Generate weather overlay map tiles
    - generate_index: Generate the index.html page

    Returns:
        Dict mapping output file paths to grouping names
    """
    start_time = datetime.now(timezone.utc)
    timestamp = start_time.strftime("%H:%M:%SZ")
    print(f"[{timestamp}] Starting weather briefing generation...")
    logger.info("Starting weather briefing generation")

    # Load airport data
    print("  Loading airport data...")
    unified_airport_data = load_unified_airport_data(
        str(config.data_dir / "APT_BASE.csv"),
        str(config.data_dir / "airports.json"),
        str(config.data_dir / "iata-icao.csv"),
    )

    # Initialize disambiguator
    print("  Initializing airport disambiguator...")
    airports_json_path = str(config.data_dir / "airports.json")
    disambiguator = AirportDisambiguator(
        airports_json_path, lazy_load=True, unified_data=unified_airport_data
    )

    # Load all groupings
    print("  Loading groupings...")
    all_groupings = load_all_groupings(
        str(config.custom_groupings_path), unified_airport_data
    )

    # Load custom groupings
    custom_groupings = (
        load_custom_groupings(str(config.custom_groupings_path))
        if config.include_custom
        else {}
    )

    # Determine which groupings to process
    groupings_to_process: Dict[
        str, Tuple[List[str], str]
    ] = {}  # name -> (airports, artcc)

    # Map preset groupings to their ARTCC
    if config.include_presets:
        for json_file in config.preset_groupings_dir.glob("*.json"):
            artcc = json_file.stem  # e.g., "ZOA" from "ZOA.json"
            if config.artcc_filter and artcc not in config.artcc_filter:
                continue

            try:
                with open(json_file, "r") as f:
                    artcc_groupings = json.load(f)

                for grouping_name, grouping_data in artcc_groupings.items():
                    # Filter out single-airport groupings
                    if isinstance(grouping_data, dict):
                        airports = grouping_data.get("airports", [])
                    elif isinstance(grouping_data, list):
                        airports = grouping_data
                    else:
                        continue
                    if len(airports) <= 1:
                        continue

                    # Resolve nested groupings (airports resolved from all_groupings)
                    resolved = resolve_grouping_recursively(
                        grouping_name, all_groupings
                    )
                    if resolved:
                        groupings_to_process[grouping_name] = (list(resolved), artcc)
            except Exception as e:
                print(f"  Warning: Error loading {json_file}: {e}")

    # Add custom groupings (placed in "custom" directory)
    if config.include_custom and custom_groupings:
        for grouping_name in custom_groupings:
            if grouping_name not in groupings_to_process:
                resolved = resolve_grouping_recursively(grouping_name, all_groupings)
                if resolved:
                    groupings_to_process[grouping_name] = (list(resolved), "custom")

    print(f"  Found {len(groupings_to_process)} groupings to process")

    # Collect all unique airports and determine which ARTCCs are involved
    all_airports: Set[str] = set()
    artccs_involved: Set[str] = set()
    custom_airports: Set[str] = set()  # Airports in custom groupings (no ARTCC bbox)

    for airports, artcc in groupings_to_process.values():
        all_airports.update(airports)
        if artcc != "custom":
            artccs_involved.add(artcc)
        else:
            custom_airports.update(airports)

    num_airports = len(all_airports)
    airports_list = list(all_airports)

    # Determine whether to fetch fresh weather or use cached
    metars: Dict[str, str] = {}
    tafs: Dict[str, str] = {}
    atis_data: Dict[str, Any] = {}

    if config.fetch_fresh_weather:
        # Check if we have fresh cached weather data (within TTL)
        cache_result = _load_weather_cache(
            config.weather_cache_dir, max_age_seconds=config.weather_cache_ttl
        )
        use_cache = cache_result is not None
    else:
        # Use cached regardless of age (but must exist)
        cache_result = _load_weather_cache(
            config.weather_cache_dir, max_age_seconds=86400 * 365
        )  # 1 year
        use_cache = cache_result is not None
        if not use_cache:
            print(
                "  WARNING: No cached weather data found, continuing with empty weather"
            )
            logger.warning(
                "No cached weather data found, continuing with empty weather"
            )

    if use_cache and cache_result is not None:
        metars, tafs, atis_data, cache_timestamp = cache_result
        print(f"  Using cached weather data (from {cache_timestamp})")
        logger.info(f"Using cached weather data from {cache_timestamp}")
        atis_count = len([a for a in atis_data.values() if a]) if atis_data else 0
    elif config.fetch_fresh_weather:
        # Fetch fresh weather data using bounding box approach for efficiency
        # This uses ~1 API call per ARTCC instead of ~1 per airport

        if artccs_involved:
            # Get bounding boxes for all ARTCCs
            artcc_bboxes = get_artcc_bboxes(artccs_involved, config.artcc_cache_dir)

            if artcc_bboxes:
                print(
                    f"  Fetching weather via bbox for {len(artcc_bboxes)} ARTCCs ({num_airports} airports)..."
                )
                logger.info(f"Fetching weather via bbox for {len(artcc_bboxes)} ARTCCs")

                bbox_progress = ProgressTracker(
                    "Weather fetch (bbox)", len(artcc_bboxes), log_interval_pct=25
                )

                # Fetch weather for each ARTCC bbox in parallel
                from concurrent.futures import ThreadPoolExecutor, as_completed

                with ThreadPoolExecutor(
                    max_workers=min(5, len(artcc_bboxes))
                ) as executor:
                    future_to_artcc = {
                        executor.submit(fetch_weather_bbox, bbox, True): artcc
                        for artcc, bbox in artcc_bboxes.items()
                    }

                    for future in as_completed(future_to_artcc):
                        artcc = future_to_artcc[future]
                        try:
                            bbox_metars, bbox_tafs = future.result()
                            metars.update(bbox_metars)
                            tafs.update(bbox_tafs)
                        except Exception as e:
                            logger.warning(f"Failed to fetch weather for {artcc}: {e}")

                        bbox_progress.update()

                print(
                    f"    Retrieved {len(metars)} METARs, {len(tafs)} TAFs from bbox queries"
                )
                logger.info(
                    f"Retrieved {len(metars)} METARs, {len(tafs)} TAFs from bbox queries"
                )

        # Fallback: fetch any missing airports individually (e.g., custom groupings or bbox misses)
        # Filter to only airports likely to have METAR reporting:
        # - 4-letter ICAO starting with 'K' (US airports with METAR stations)
        # - OR has an actual control tower (not NON-ATCT)
        # - OR has FAR 139 certification (scheduled passenger service)
        # - OR has an IATA code (commercial airports)
        # This avoids hundreds of wasted requests for small private strips like "68AR", "7CO8"
        def likely_has_metar(icao: str) -> bool:
            if len(icao) == 4 and icao.startswith("K"):
                return True
            airport_info = unified_airport_data.get(icao, {})
            tower_type = airport_info.get("tower_type", "")
            # Only count actual towers, not NON-ATCT
            if tower_type and tower_type != "NON-ATCT":
                return True
            if airport_info.get("far139"):
                return True
            if airport_info.get("iata"):
                return True
            return False

        missing_airports = [a for a in airports_list if a not in metars]
        fetchable_airports = [a for a in missing_airports if likely_has_metar(a)]
        skipped_count = len(missing_airports) - len(fetchable_airports)

        if fetchable_airports:
            print(
                f"  Fetching {len(fetchable_airports)} missing airports individually (skipping {skipped_count} unlikely to have METAR)..."
            )
            logger.info(
                f"Fetching {len(fetchable_airports)} airports individually, skipping {skipped_count} without likely METAR"
            )

            fallback_metars = get_metar_batch(
                fetchable_airports, max_workers=config.max_workers
            )
            fallback_tafs = get_taf_batch(
                fetchable_airports, max_workers=config.max_workers
            )

            metars.update(fallback_metars)
            tafs.update(fallback_tafs)
        elif skipped_count > 0:
            print(
                f"  Skipping {skipped_count} airports unlikely to have METAR stations"
            )
            logger.info(
                f"Skipping {skipped_count} airports unlikely to have METAR stations"
            )

        # Log rate limit status after fetches
        rate_status = get_rate_limit_status()
        if rate_status["is_rate_limited"]:
            logger.warning(
                f"Rate limiting active: backoff={rate_status['backoff_seconds']:.1f}s, errors={rate_status['error_count']}"
            )
        elif rate_status["error_count"] > 0:
            logger.info(
                f"Rate limit recovery: {rate_status['error_count']} errors, recovered"
            )

        # Fetch VATSIM data for ATIS
        print("  Fetching VATSIM ATIS data...")
        logger.info("Fetching VATSIM ATIS data")
        vatsim_data = download_vatsim_data()
        atis_data = (
            get_atis_for_airports(vatsim_data, airports_list) if vatsim_data else {}
        )
        atis_count = len([a for a in atis_data.values() if a]) if atis_data else 0
        print(f"    Found {atis_count} airports with ATIS")
        logger.info(f"Found {atis_count} airports with active ATIS")

        # Cache weather data for next run
        _save_weather_cache(config.weather_cache_dir, metars, tafs, atis_data)

    # Check if weather has changed since last run
    weather_changed = True
    if config.skip_if_unchanged and metars:
        new_hash = compute_weather_hash(metars, tafs)
        old_hash = load_weather_hash(config.weather_cache_dir)

        if old_hash == new_hash:
            weather_changed = False
            print(
                f"  Weather unchanged (hash: {new_hash[:8]}...), skipping regeneration"
            )
            logger.info(f"Weather unchanged (hash: {new_hash}), skipping regeneration")
        else:
            print(
                f"  Weather changed (old: {old_hash[:8] if old_hash else 'none'}... -> new: {new_hash[:8]}...)"
            )
            logger.info(f"Weather changed from {old_hash} to {new_hash}")
            save_weather_hash(config.weather_cache_dir, new_hash)

    # If weather hasn't changed, skip briefings and tiles but still update index timestamp
    if not weather_changed:
        # Only regenerate index if enabled (to update timestamp)
        if config.generate_index:
            # We need to load existing data for the index
            # For now, just update the timestamp in the index by regenerating it
            pass
        else:
            # Nothing to do
            total_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            end_timestamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
            print(f"[{end_timestamp}] Skipped (no changes) in {total_time:.1f}s")
            logger.info(f"Skipped (no changes) in {total_time:.1f}s")
            return {}

    num_groupings = len(groupings_to_process)
    print(f"  Generating {num_groupings} briefings...")
    logger.info(f"Generating {num_groupings} briefings")

    # Create output directories
    config.output_dir.mkdir(parents=True, exist_ok=True)

    generated_files: Dict[str, str] = {}
    artcc_groupings_map: Dict[str, List[Dict]] = {}  # artcc -> list of grouping info

    # Helper to get airport's ARTCC from unified data
    def get_airport_artcc(icao: str) -> Optional[str]:
        airport_info = unified_airport_data.get(icao, {})
        # Field is 'artcc' in unified airport data
        artcc_code = airport_info.get("artcc", "")
        if artcc_code and len(artcc_code) == 3:
            return artcc_code
        return None

    # Helper to get airport coordinates
    def get_airport_coords(icao: str) -> Optional[Tuple[float, float]]:
        airport_info = unified_airport_data.get(icao, {})
        lat = airport_info.get("latitude")
        lon = airport_info.get("longitude")
        if lat is not None and lon is not None:
            return (float(lat), float(lon))
        return None

    # Helper to infer ARTCCs for a grouping from its airports
    def infer_artccs_for_grouping(airports: List[str]) -> Set[str]:
        artccs = set()
        for icao in airports:
            artcc_code = get_airport_artcc(icao)
            if artcc_code:
                artccs.add(artcc_code)
        return artccs

    if config.generate_briefings:
        briefing_progress = ProgressTracker(
            "Briefing generation", num_groupings, log_interval_pct=10
        )
    else:
        print("  Skipping briefing generation (not in stages)")

    for grouping_name, (airports, artcc) in groupings_to_process.items():
        # Create generator (needed for weather data even if skipping briefings)
        generator = WeatherBriefingGenerator(
            grouping_name=grouping_name,
            airports=airports,
            unified_airport_data=unified_airport_data,
            disambiguator=disambiguator,
        )

        # Populate weather data from batch
        generator.fetch_weather_data(metars, tafs, atis_data)

        # Generate and write HTML briefing (if enabled)
        if config.generate_briefings:
            html_content = generator.generate_html()

            # Create ARTCC subdirectory
            artcc_dir = config.output_dir / artcc
            artcc_dir.mkdir(parents=True, exist_ok=True)

            # Sanitize filename
            safe_name = re.sub(r"[^\w\-_]", "_", grouping_name)
            output_path = artcc_dir / f"{safe_name}.html"

            # Write file
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(html_content)

            generated_files[str(output_path)] = grouping_name
        else:
            safe_name = re.sub(r"[^\w\-_]", "_", grouping_name)

        # Track for index generation (always needed for tiles/index)
        category_summary = generator.get_category_summary()
        airport_weather_points = generator.get_airport_weather_points()

        # Collect airport coordinates for hover polygon
        airport_coords = []
        for icao in airports:
            coords = get_airport_coords(icao)
            if coords:
                airport_coords.append(coords)

        grouping_info = {
            "name": grouping_name,
            "filename": f"{safe_name}.html",
            "airport_count": len(airports),
            "categories": category_summary,
            "airport_coords": airport_coords,  # For hover polygon
            "airport_weather_points": airport_weather_points,  # For localized map coloring
        }

        # For custom groupings, infer ARTCCs from airport data
        if artcc == "custom":
            inferred_artccs = infer_artccs_for_grouping(airports)
            if inferred_artccs:
                # Add to each inferred ARTCC
                for inferred_artcc in inferred_artccs:
                    if inferred_artcc not in artcc_groupings_map:
                        artcc_groupings_map[inferred_artcc] = []
                    # Create a copy with the correct path for this ARTCC
                    artcc_info = grouping_info.copy()
                    artcc_info["is_custom"] = True  # Mark as originally custom
                    artcc_info["path_prefix"] = (
                        "custom"  # Files are still in custom dir
                    )
                    artcc_groupings_map[inferred_artcc].append(artcc_info)
            else:
                # No ARTCC could be inferred - keep in custom
                if "custom" not in artcc_groupings_map:
                    artcc_groupings_map["custom"] = []
                artcc_groupings_map["custom"].append(grouping_info)
        else:
            if artcc not in artcc_groupings_map:
                artcc_groupings_map[artcc] = []
            artcc_groupings_map[artcc].append(grouping_info)

        if config.generate_briefings:
            briefing_progress.update()

    # Generate ARTCC-wide briefings (all airports in each ARTCC)
    if config.generate_briefings:
        _generate_artcc_wide_briefings(
            config=config,
            groupings_to_process=groupings_to_process,
            metars=metars,
            tafs=tafs,
            atis_data=atis_data,
            unified_airport_data=unified_airport_data,
            disambiguator=disambiguator,
            generated_files=generated_files,
        )

    # Generate weather overlay tiles (if enabled)
    if config.generate_tiles:
        print("  Generating weather overlay tiles...")
        logger.info("Generating weather overlay tiles")

        # Collect weather data for ALL airports with METARs (not just grouping airports)
        # This uses all weather fetched via bbox, giving fuller tile coverage
        all_airport_weather: Dict[str, Dict] = {}
        for icao, metar in metars.items():
            if not metar:
                continue
            # Get coordinates from unified airport data
            airport_info = unified_airport_data.get(icao, {})
            lat = airport_info.get("latitude")
            lon = airport_info.get("longitude")
            if lat is None or lon is None:
                continue
            # Parse weather category
            category = get_flight_category(metar)
            all_airport_weather[icao] = {
                "icao": icao,
                "lat": lat,
                "lon": lon,
                "category": category,
            }

        valid_weather_count = sum(
            1
            for ap in all_airport_weather.values()
            if ap.get("category") in {"VFR", "MVFR", "IFR", "LIFR"}
        )
        print(
            f"    Collected {valid_weather_count} airports with valid weather (of {len(all_airport_weather)} total)"
        )
        logger.info(
            f"Collected {valid_weather_count} airports with valid weather (of {len(all_airport_weather)} total)"
        )

        # Get ARTCC boundaries for tile generation
        from .index_generator import CONUS_ARTCCS

        artcc_boundaries = get_artcc_boundaries(config.artcc_cache_dir)

        print(
            f"    Found {len(artcc_boundaries)} ARTCC boundaries, {len(CONUS_ARTCCS)} in CONUS"
        )
        logger.info(
            f"Found {len(artcc_boundaries)} ARTCC boundaries, {len(CONUS_ARTCCS)} in CONUS"
        )

        if not all_airport_weather:
            print(
                "    WARNING: No airport weather data collected - skipping tile generation"
            )
            logger.warning(
                "No airport weather data collected - skipping tile generation"
            )
        else:
            # Generate tiles (zoom 4-7: continental to regional view)
            tiles_dir = config.output_dir / "tiles"
            tile_results = generate_weather_tiles(
                artcc_boundaries=artcc_boundaries,
                airport_weather=all_airport_weather,
                output_dir=tiles_dir,
                conus_artccs=CONUS_ARTCCS,
                zoom_levels=(4, 5, 6, 7),
                max_workers=config.tile_max_workers,
            )

            total_tiles = sum(tile_results.values())
            print(f"    Generated {total_tiles} weather tiles")
            logger.info(
                f"Generated {total_tiles} weather tiles across {len(tile_results)} zoom levels"
            )
    else:
        print("  Skipping tile generation (--no-tiles)")
        logger.info("Skipping tile generation (--no-tiles)")

    # Generate index if enabled
    if config.generate_index:
        from .index_generator import generate_index_page

        index_path = generate_index_page(
            config, artcc_groupings_map, unified_airport_data
        )
        if index_path:
            generated_files[str(index_path)] = "Index"

    # Final summary
    total_time = (datetime.now(timezone.utc) - start_time).total_seconds()
    end_timestamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    print(
        f"[{end_timestamp}] Generation complete! {len(generated_files)} files in {total_time:.1f}s"
    )
    logger.info(
        f"Generation complete: {len(generated_files)} files in {total_time:.1f}s"
    )
    return generated_files


# Legacy aliases for backwards compatibility
def generate_all_briefings(config: DaemonConfig) -> Dict[str, str]:
    """Legacy alias for generate(). Use generate() instead."""
    return generate(config)


def generate_index_only(config: DaemonConfig) -> Dict[str, str]:
    """Legacy: Generate only the index page. Use generate() with config.generate_index=True instead."""
    config.fetch_fresh_weather = False
    config.generate_briefings = False
    config.generate_tiles = False
    config.generate_index = True
    return generate(config)


def generate_with_cached_weather(config: DaemonConfig) -> Dict[str, str]:
    """Legacy: Use cached weather. Use generate() with config.fetch_fresh_weather=False instead."""
    config.fetch_fresh_weather = False
    return generate(config)


# Delete old implementations - keeping only legacy wrappers above
_REMOVED_OLD_IMPLEMENTATIONS = """
The following functions have been consolidated into generate():
- generate_index_only()
- generate_with_cached_weather()

Use generate() with appropriate config flags instead.
"""


if __name__ == "__main__":
    # Simple test run
    config = DaemonConfig(output_dir=Path("./test_output"))
    generate(config)
