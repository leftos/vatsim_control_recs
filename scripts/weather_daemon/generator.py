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
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

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
        file_age = datetime.now(UTC).timestamp() - lock_file.stat().st_mtime
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
    def acquire_lock(lock_file: Path, timeout: int = 0) -> Generator[bool]:
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
            # The handle must outlive this block: it holds the lock until the
            # caller releases it, so a context manager would free it too early
            lock_fd = open(lock_file, "w")  # noqa: SIM115
            try:
                msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
                # Write PID and timestamp to lock file for debugging
                lock_fd.write(f"{os.getpid()}\n")
                lock_fd.write(f"{datetime.now(UTC).isoformat()}\n")
                lock_fd.flush()
                logger.debug(f"Acquired lock: {lock_file}")
            except OSError:
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
                    except OSError:
                        pass
                lock_fd.close()
else:
    import fcntl

    @contextmanager
    def acquire_lock(lock_file: Path, timeout: int = 0) -> Generator[bool]:
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
            # The handle must outlive this block: it holds the lock until the
            # caller releases it, so a context manager would free it too early
            lock_fd = open(lock_file, "w")  # noqa: SIM115
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                # Write PID and timestamp to lock file for debugging
                lock_fd.write(f"{os.getpid()}\n")
                lock_fd.write(f"{datetime.now(UTC).isoformat()}\n")
                lock_fd.flush()
                logger.debug(f"Acquired lock: {lock_file}")
            except OSError:
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
                    except OSError:
                        pass
                lock_fd.close()


def compute_weather_hash(metars: dict[str, str], tafs: dict[str, str]) -> str:
    """
    Compute a hash of weather data to detect changes.

    Only hashes METARs and TAFs, not ATIS.

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


def load_weather_hash(cache_dir: Path) -> str | None:
    """Load the skip key the last completed tile stage saved (see compute_skip_key)."""
    hash_file = cache_dir / "weather_hash.txt"
    if hash_file.exists():
        try:
            return hash_file.read_text().strip()
        except Exception:
            return None
    return None


def save_weather_hash(cache_dir: Path, skip_key: str) -> None:
    """Save the skip key of a completed tile stage (see compute_skip_key)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    hash_file = cache_dir / "weather_hash.txt"
    try:
        hash_file.write_text(skip_key)
    except Exception as e:
        logger.warning(f"Failed to save the tile skip key: {e}")


def _save_weather_cache(
    cache_dir: Path, metars: dict, tafs: dict, atis_data: dict
) -> None:
    """Save weather data to cache for later use with --use-cached."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "weather_cache.json"

    cache_data = {
        "timestamp": datetime.now(UTC).isoformat(),
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
    cache_dir: Path, max_age_seconds: int | None = None
) -> tuple[dict, dict, dict, str] | None:
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
        with open(cache_file, encoding="utf-8") as f:
            cache_data = json.load(f)

        timestamp_str = cache_data.get("timestamp", "")

        # Check TTL if specified
        if max_age_seconds is not None and timestamp_str:
            try:
                cache_time = datetime.fromisoformat(
                    timestamp_str.replace("Z", "+00:00")
                )
                age = (datetime.now(UTC) - cache_time).total_seconds()
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
        self.start_time = datetime.now(UTC)

    def update(self, completed: int | None = None, increment: int = 1) -> None:
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
        elapsed = (datetime.now(UTC) - self.start_time).total_seconds()

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

from airport_disambiguator import AirportDisambiguator  # noqa: E402
from backend import (  # noqa: E402
    fetch_weather_bbox,
    get_metar_batch,
    get_rate_limit_status,
    get_taf_batch,
    load_all_groupings,
    load_unified_airport_data,
)
from backend.briefing import (  # noqa: E402
    AreaClusterer,
    build_area_summary,
    count_area_categories,
    format_taf_relative_time,
    parse_taf_changes,
    parse_wind_from_metar,
)
from backend.core.groupings import (  # noqa: E402
    load_custom_groupings,
    resolve_grouping_recursively,
)
from backend.data.atis_filter import colorize_atis_text, filter_atis_text  # noqa: E402
from backend.data.datis_api import get_datis_for_airports  # noqa: E402
from backend.data.vatsim_api import (  # noqa: E402
    download_vatsim_data,
    get_atis_for_airports,
)

# Import METAR parsing functions
from backend.data.weather_parsing import (  # noqa: E402
    extract_visibility_str,
    format_obs_time_display,
    get_flight_category,
    is_speci_metar,
    parse_ceiling_feet,
    parse_ceiling_layer,
    parse_metar_obs_time,
    parse_visibility_sm,
    parse_weather_phenomena,
)
from common.logger import setup_file_logging  # noqa: E402

from .artcc_boundaries import get_artcc_boundaries  # noqa: E402
from .config import ARTCC_NAMES, CATEGORY_COLORS, DaemonConfig  # noqa: E402
from .tile_generator import generate_weather_tiles, write_tile_version  # noqa: E402


def compute_code_version(daemon_dir: Path | None = None) -> str:
    """
    Hash the daemon's own code, so a deploy regenerates with unchanged weather.

    Covered are every ``*.py`` file under ``daemon_dir``, recursive and sorted by
    POSIX relative path, hashing each relative path and then its bytes. The git
    sha is no use here: rsync deploy targets have no ``.git`` and LocalTest runs
    uncommitted edits.

    Args:
        daemon_dir: Directory to hash; the daemon package when omitted

    Returns:
        First 16 hex characters of the SHA256 digest
    """
    root = daemon_dir if daemon_dir is not None else Path(__file__).parent
    files = sorted(
        root.rglob("*.py"), key=lambda path: path.relative_to(root).as_posix()
    )
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def compute_skip_key(metars: dict[str, str], tafs: dict[str, str]) -> str:
    """
    The key the last completed tile stage wrote to ``weather_hash.txt``.

    The weather hash and the code version joined by ``:``; any difference from
    the stored key counts as changed.

    Args:
        metars: Dict of ICAO -> METAR string
        tafs: Dict of ICAO -> TAF string

    Returns:
        ``"<weather hash>:<code version>"``
    """
    return f"{compute_weather_hash(metars, tafs)}:{compute_code_version()}"


def should_skip_tile_stage(config: DaemonConfig, skip_key: str | None) -> bool:
    """
    Whether this run may skip the tile stage because nothing it draws has changed.

    Only a run that fetched fresh weather considers the skip, and only while
    ``skip_if_unchanged`` holds (``--force`` clears it): a run from cached weather
    is an explicit regeneration. The stored key comes from the last completed tile
    stage, so a run killed inside one regenerates on the next tick.

    Args:
        config: Daemon configuration
        skip_key: This run's key, or None when there is no weather to hash

    Returns:
        True when the stored key matches this run's
    """
    if not (config.fetch_fresh_weather and config.skip_if_unchanged and skip_key):
        return False
    return load_weather_hash(config.weather_cache_dir) == skip_key


def get_artcc_bboxes(
    artcc_codes: set[str], cache_dir: Path, padding_degrees: float = 0.5
) -> dict[str, tuple[float, float, float, float]]:
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
        airports: list[str],
        unified_airport_data: dict[str, dict],
        disambiguator: AirportDisambiguator,
    ):
        self.grouping_name = grouping_name
        self.airports = airports
        self.unified_airport_data = unified_airport_data
        self.disambiguator = disambiguator
        self.weather_data: dict[str, dict[str, Any]] = {}

    def fetch_weather_data(
        self,
        metars: dict[str, str],
        tafs: dict[str, str],
        atis_data: dict[str, dict],
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

    def _get_airport_coords(self, icao: str) -> tuple[float, float] | None:
        """Get airport coordinates."""
        airport_info = self.unified_airport_data.get(icao, {})
        lat = airport_info.get("latitude")
        lon = airport_info.get("longitude")
        if lat is not None and lon is not None:
            return (lat, lon)
        return None

    def _create_area_groups(self) -> list[dict[str, Any]]:
        """Create area-based groupings using shared AreaClusterer."""
        clusterer = AreaClusterer(
            weather_data=self.weather_data,
            unified_airport_data=self.unified_airport_data,
            disambiguator=self.disambiguator,
        )
        return clusterer.create_area_groups()

    def _get_common_obs_time(self) -> tuple[str | None, set[str]]:
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
        airport_times: dict[str, str] = {}

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

    def _get_latest_obs_time(self) -> str | None:
        """
        Find the latest (most recent) METAR observation time across all airports.

        Returns:
            The latest observation time as HHMM string, or None if no times found.
        """
        from datetime import datetime

        now = datetime.now(UTC)
        current_day = now.day
        current_hour = now.hour
        current_minute = now.minute

        latest_time: str | None = None
        latest_minutes_ago: int | None = None

        for data in self.weather_data.values():
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
                    day_diff = 1

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
        self, icao: str, data: dict[str, Any], show_obs_time: bool = False
    ) -> str:
        """Build a Rich markup card for one airport.

        Args:
            icao: Airport ICAO code
            data: Weather data dict for this airport
            show_obs_time: If True, show observation time in header (for airports with different times)
        """
        lines = [
            self._card_header(icao, data, show_obs_time),
            _card_conditions_line(data),
        ]

        phenomena = data.get("phenomena", [])
        if phenomena:
            lines.append(f"  Weather: {', '.join(phenomena)}")

        lines.extend(_taf_change_lines(data.get("taf_changes", [])))
        # ATIS info - supports dual ATIS (departure/arrival)
        # Shows source label (RW) for real-world D-ATIS when no VATSIM ATIS available
        lines.extend(_atis_lines(data.get("atis") or []))

        return "\n".join(lines)

    def _card_header(self, icao: str, data: dict[str, Any], show_obs_time: bool) -> str:
        """The card's first line: name, optional observation time, category, SPECI.

        Args:
            icao: Airport ICAO code
            data: Weather data dict for this airport
            show_obs_time: Whether to show the observation time

        Returns:
            The header line in Rich markup
        """
        category = data.get("category", "UNK")
        color = CATEGORY_COLORS.get(category, "white")
        pretty_name = (
            self.disambiguator.get_full_name(icao) if self.disambiguator else icao
        )

        obs_time_str = ""
        if show_obs_time:
            obs_time = data.get("obs_time")
            if obs_time:
                formatted_time = format_obs_time_display(obs_time)
                obs_time_str = f" [#999999]({formatted_time})[/#999999]"

        speci_str = ""
        if data.get("is_speci"):
            speci_str = " [#ff9900 bold]SPECI[/#ff9900 bold]"

        return f"{icao} - {pretty_name}{obs_time_str} // [{color} bold]{category}[/{color} bold]{speci_str}"

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
        console.print(_briefing_time_line(latest_obs_hhmm or common_obs_hhmm))

        summary = _category_summary_markup(self.get_category_summary())
        if summary:
            console.print(summary)
        console.print()

        self._print_area_cards(console, different_airports)
        self._print_missing_airports(console)

        return _finish_briefing_html(console.export_html(inline_styles=True))

    def _print_area_cards(self, console: Console, different_airports: set[str]) -> None:
        """Print each area's heading, category summary and airport cards.

        Args:
            console: The recording console
            different_airports: Airports whose observation time differs from the common one
        """
        for area in self._create_area_groups():
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

    def _print_missing_airports(self, console: Console) -> None:
        """Print the section listing the grouping's airports without a METAR."""
        missing_airports = [
            icao
            for icao in self.airports
            if not self.weather_data.get(icao, {}).get("metar")
        ]
        if missing_airports:
            console.print(
                "[bold #cc6666]━━━ AIRPORTS WITHOUT METAR DATA ━━━[/bold #cc6666]"
            )
            console.print(
                f"[#aaaaaa]The following {len(missing_airports)} airport(s) are in this grouping but have no METAR available from aviationweather.gov:[/#aaaaaa]"
            )
            # Sort and format as a comma-separated list
            missing_str = ", ".join(sorted(missing_airports))
            console.print(f"[#888888]{missing_str}[/#888888]")
            console.print()

    def get_category_summary(self) -> dict[str, int]:
        """Get category counts for this briefing."""
        counts = {"LIFR": 0, "IFR": 0, "MVFR": 0, "VFR": 0, "UNK": 0}
        for data in self.weather_data.values():
            cat = data.get("category", "UNK")
            counts[cat] = counts.get(cat, 0) + 1
        return counts

    def get_airport_weather_points(self) -> list[dict[str, Any]]:
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
                significant_changes = _significant_taf_points(
                    data.get("taf_changes", [])
                )

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


_SUMMARY_CATEGORY_COLORS = (
    ("LIFR", "#ffaaff"),
    ("IFR", "#ff9999"),
    ("MVFR", "#77bbff"),
    ("VFR", "#66ff66"),
)

_TAF_DETAIL_COLUMNS = ("vis", "ceil", "wind", "wx")


def _format_visibility_sm(vis_sm: float | None) -> str | None:
    """Format a TAF visibility in statute miles: ``P6SM``, ``3SM`` or ``1.5SM``.

    Args:
        vis_sm: Visibility in statute miles, or None when the change gives none

    Returns:
        The formatted visibility, or None when there is none
    """
    if vis_sm is None:
        return None
    if vis_sm >= 6:
        return "P6SM"
    if vis_sm == int(vis_sm):
        return f"{int(vis_sm)}SM"
    return f"{vis_sm:.1f}SM"


def _strip_markup(s: str) -> str:
    return re.sub(r"\[/?[^\]]+\]", "", s)


def _card_conditions_line(data: dict[str, Any]) -> str:
    """The card's ceiling, visibility and wind line."""
    ceiling = data.get("ceiling")
    conditions_parts = [f"Ceiling: {ceiling}" if ceiling else "Ceiling: CLR"]

    visibility = data.get("visibility")
    if visibility:
        conditions_parts.append(f"Vis: {visibility}")

    wind = data.get("wind")
    if wind:
        conditions_parts.append(f"Wind: {wind}")

    return "  " + " | ".join(conditions_parts)


def _taf_change_lines(taf_changes: list[dict[str, Any]]) -> list[str]:
    """The card's lines for the first two significant TAF changes, in aligned columns.

    Args:
        taf_changes: The airport's parsed TAF changes

    Returns:
        One Rich markup line per shown change
    """
    significant_changes = [
        c for c in taf_changes if c.get("is_improvement") or c.get("is_deterioration")
    ]
    if not significant_changes:
        return []

    taf_entries = [_taf_entry(change) for change in significant_changes[:2]]
    max_widths = _taf_column_widths(taf_entries)
    return [_taf_line(entry, max_widths) for entry in taf_entries]


def _taf_entry(change: dict[str, Any]) -> dict[str, Any]:
    """The display fields of one significant TAF change, before column alignment.

    Args:
        change: One parsed TAF change

    Returns:
        Its indicator, prefix fields, predicted category and colour, and detail parts
    """
    change_type = change.get("type", "")
    time_str = change.get("time_str", "")
    pred_category = change.get("category", "UNK")
    trend = change.get("trend", "stable")

    if trend == "worsening":
        indicator = "[#ff9999 bold]▼[/#ff9999 bold]"
    else:
        indicator = "[#66ff66 bold]▲[/#66ff66 bold]"

    raw_relative = format_taf_relative_time(time_str)
    relative_time = f" [#999999]{raw_relative}[/#999999]" if raw_relative else ""
    pred_color = CATEGORY_COLORS.get(pred_category, "white")

    taf_phenomena = change.get("phenomena", [])
    parts = {
        "vis": _format_visibility_sm(change.get("visibility_sm")) or "",
        "ceil": change.get("ceiling_layer") or "",
        "wind": change.get("wind") or "",
        "wx": ", ".join(taf_phenomena) if taf_phenomena else "",
    }

    return {
        "indicator": indicator,
        "change_type": change_type,
        "time_str": time_str,
        "relative_time": relative_time,
        "pred_category": pred_category,
        "pred_color": pred_color,
        "parts": parts,
    }


def _taf_prefix_plain(entry: dict[str, Any]) -> str:
    return f"TAF {entry['change_type']} {entry['time_str']}{_strip_markup(entry['relative_time'])}"


def _taf_column_widths(taf_entries: list[dict[str, Any]]) -> dict[str, int]:
    """The widest prefix, category and detail part across the shown TAF changes."""
    widths = {
        "prefix": max((len(_taf_prefix_plain(e)) for e in taf_entries), default=0),
        "cat": max((len(e["pred_category"]) for e in taf_entries), default=0),
    }
    for column in _TAF_DETAIL_COLUMNS:
        widths[column] = max((len(e["parts"][column]) for e in taf_entries), default=0)
    return widths


def _taf_line(entry: dict[str, Any], max_widths: dict[str, int]) -> str:
    """One TAF change's card line, padded to the shown changes' column widths.

    Args:
        entry: The change's display fields from ``_taf_entry``
        max_widths: The column widths from ``_taf_column_widths``

    Returns:
        The change's line in Rich markup
    """
    parts = entry["parts"]
    pred_color = entry["pred_color"]

    prefix_plain = _taf_prefix_plain(entry)
    prefix_padding = " " * (max_widths["prefix"] - len(prefix_plain))
    prefix_with_markup = f"TAF {entry['change_type']} {entry['time_str']}{entry['relative_time']}{prefix_padding}"

    cat_padded = entry["pred_category"].rjust(max_widths["cat"])

    padded_parts = []
    if max_widths["vis"] > 0:
        padded_parts.append(parts["vis"].rjust(max_widths["vis"]))
    padded_parts.extend(
        parts[column].ljust(max_widths[column])
        for column in ("ceil", "wind", "wx")
        if max_widths[column] > 0
    )

    details_str = " | ".join(padded_parts).rstrip() if padded_parts else ""
    line = f"  {entry['indicator']} {prefix_with_markup}: [{pred_color} bold]{cat_padded}[/{pred_color} bold]"
    if details_str:
        return f"{line} - {details_str}"
    return line


def _atis_lines(atis_list: list[dict[str, Any]]) -> list[str]:
    """The card's ATIS lines, one per ATIS with text left after filtering."""
    lines = []
    for atis in atis_list:
        line = _atis_line(atis)
        if line is not None:
            lines.append(line)
    return lines


def _atis_line(atis: dict[str, Any]) -> str | None:
    """One ATIS as a card line with its type and source labels.

    Args:
        atis: One ATIS record (code, type, source, text)

    Returns:
        The line in Rich markup, or None when filtering leaves no text
    """
    atis_code = atis.get("atis_code", "")
    atis_type = atis.get("type", "combined")
    atis_source = atis.get("source", "vatsim")
    raw_text = atis.get("text_atis", "")
    filtered_text = filter_atis_text(raw_text)
    if not filtered_text:
        return None

    type_label = ""
    if atis_type == "departure":
        type_label = "[cyan]DEP:[/cyan] "
    elif atis_type == "arrival":
        type_label = "[cyan]ARR:[/cyan] "
    source_label = "[#ffaa00](RW)[/#ffaa00] " if atis_source == "rw" else ""
    display_text = colorize_atis_text(filtered_text, atis_code)
    return f"  [#aaaaaa]{source_label}{type_label}{display_text}[/#aaaaaa]"


def _briefing_time_line(display_time: str | None) -> str:
    """The briefing's time line: the latest METAR time, else the generation time."""
    now = datetime.now(UTC)
    if display_time:
        date_str = now.strftime("%Y-%m-%d")
        return f"METARs current as of: [#aaaaff]{date_str} {display_time}Z[/#aaaaff]\n"
    zulu_str = now.strftime("%Y-%m-%d %H%MZ")
    return f"Generated: [#aaaaff]{zulu_str}[/#aaaaff]\n"


def _category_summary_markup(category_counts: dict[str, int]) -> str:
    """The briefing's category counts line, or an empty string when none count."""
    summary_parts = [
        f"[{color}]{category_counts[category]} {category}[/{color}]"
        for category, color in _SUMMARY_CATEGORY_COLORS
        if category_counts[category] > 0
    ]
    return " | ".join(summary_parts)


def _finish_briefing_html(html_content: str) -> str:
    """Add the no-cache meta tags and the page styles to Rich's exported HTML."""
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
    return html_content.replace("<body>\n    <pre", "<body>\n<pre")


def _significant_taf_points(taf_changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The first two significant TAF changes, in the map tooltip's form.

    Args:
        taf_changes: The airport's parsed TAF changes

    Returns:
        Up to two dicts of type, time, category, trend, visibility, ceiling, wind
        and phenomena
    """
    significant_changes: list[dict[str, Any]] = []
    for c in taf_changes:
        if not (c.get("is_improvement") or c.get("is_deterioration")):
            continue
        vis_str = _format_visibility_sm(c.get("visibility_sm"))
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
    return significant_changes


def _write_briefing_html(
    generator: WeatherBriefingGenerator,
    output_path: Path,
    grouping_name: str,
    artcc: str,
    generated_files: dict[str, str],
) -> bool:
    """Generate and write a briefing, logging and skipping it when it raises.

    One bad briefing must not stop the run. A briefing that raises is logged with
    its traceback, its ARTCC and its grouping name, and left out of the run's
    output, so the remaining groupings, the ARTCC briefings, the index and the
    tiles still run.

    Returns:
        True when the briefing was written, False when it raised.
    """
    try:
        html_content = generator.generate_html()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)
    except Exception:
        logger.exception(
            "Skipping briefing for grouping %r in ARTCC %s", grouping_name, artcc
        )
        return False

    generated_files[str(output_path)] = grouping_name
    return True


def _write_artcc_briefing(
    config: DaemonConfig,
    generator: WeatherBriefingGenerator,
    grouping_name: str,
    artcc: str,
    generated_files: dict[str, str],
) -> None:
    """Generate and write one ARTCC-wide briefing, skipping it when it raises."""
    output_path = config.output_dir / artcc / "_all.html"
    if _write_briefing_html(
        generator, output_path, grouping_name, artcc, generated_files
    ):
        logger.debug(f"Generated ARTCC briefing: {output_path}")


@dataclass(frozen=True)
class _BriefingSources:
    """What a run's briefings are built from: the airport data and the weather snapshot."""

    unified_airport_data: dict[str, Any]
    disambiguator: Any
    metars: dict[str, str]
    tafs: dict[str, str]
    atis_data: dict[str, Any]


def _generate_artcc_wide_briefings(
    config: DaemonConfig,
    groupings_to_process: dict[str, tuple[list[str], str]],
    sources: _BriefingSources,
    generated_files: dict[str, str],
) -> None:
    """
    Generate ARTCC-wide briefings containing all airports for each ARTCC.

    The briefing stage of ``generate`` calls it after the grouping briefings when
    briefings are enabled; custom groupings have no ARTCC and are left out.
    """

    # Collect all unique airports per ARTCC
    artcc_all_airports: dict[str, set[str]] = {}
    for airports, artcc in groupings_to_process.values():
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
            unified_airport_data=sources.unified_airport_data,
            disambiguator=sources.disambiguator,
        )

        generator.fetch_weather_data(sources.metars, sources.tafs, sources.atis_data)

        # A briefing that raises is logged with its traceback and skipped, so the
        # remaining ARTCC briefings, the index and the tiles still run.
        _write_artcc_briefing(config, generator, grouping_name, artcc, generated_files)


def _load_airport_inputs(
    config: DaemonConfig,
) -> tuple[dict[str, Any], AirportDisambiguator, dict[str, list[str]]]:
    """Load the unified airport data, the disambiguator and every grouping.

    Args:
        config: Daemon configuration

    Returns:
        (unified airport data, disambiguator, all groupings)
    """
    print("  Loading airport data...")
    unified_airport_data = load_unified_airport_data(
        str(config.data_dir / "raw" / "APT_BASE.csv"),
        str(config.data_dir / "raw" / "airports.json"),
        str(config.data_dir / "raw" / "iata-icao.csv"),
    )

    print("  Initializing airport disambiguator...")
    airports_json_path = str(config.data_dir / "raw" / "airports.json")
    disambiguator = AirportDisambiguator(
        airports_json_path,
        lazy_load=True,
        unified_data=unified_airport_data,
        names_csv_path=str(config.data_dir / "airport_names.csv"),
    )

    print("  Loading groupings...")
    all_groupings = load_all_groupings(
        str(config.custom_groupings_path), unified_airport_data
    )
    return unified_airport_data, disambiguator, all_groupings


def _keeps_preset_grouping(grouping_data: Any) -> bool:
    """Whether a preset grouping is briefed.

    Single-airport groupings are left out unless they have a facility_id, which
    keeps legitimate single-airport TRACONs like ITO Hilo.
    """
    if isinstance(grouping_data, dict):
        airports = grouping_data.get("airports", [])
        facility_id = grouping_data.get("facility_id")
    elif isinstance(grouping_data, list):
        airports = grouping_data
        facility_id = None
    else:
        return False
    return not (len(airports) <= 1 and not facility_id)


def _add_preset_file(
    json_file: Path,
    artcc: str,
    all_groupings: dict[str, list[str]],
    groupings_to_process: dict[str, tuple[list[str], str]],
) -> None:
    """Add one ARTCC's preset groupings, printing a warning when its file fails.

    Args:
        json_file: The ARTCC's preset groupings file
        artcc: The ARTCC the file belongs to
        all_groupings: Every grouping, for resolving nested ones
        groupings_to_process: Grouping name -> (airports, ARTCC), added to in place
    """
    try:
        with open(json_file) as f:
            artcc_groupings = json.load(f)

        for grouping_name, grouping_data in artcc_groupings.items():
            if not _keeps_preset_grouping(grouping_data):
                continue

            # Resolve nested groupings (airports resolved from all_groupings)
            resolved = resolve_grouping_recursively(grouping_name, all_groupings)
            if resolved:
                groupings_to_process[grouping_name] = (list(resolved), artcc)
    except Exception as e:
        print(f"  Warning: Error loading {json_file}: {e}")


def _add_custom_groupings(
    custom_groupings: dict[str, list[str]],
    all_groupings: dict[str, list[str]],
    groupings_to_process: dict[str, tuple[list[str], str]],
) -> None:
    """Add the custom groupings no preset already named, under ``custom``."""
    for grouping_name in custom_groupings:
        if grouping_name not in groupings_to_process:
            resolved = resolve_grouping_recursively(grouping_name, all_groupings)
            if resolved:
                groupings_to_process[grouping_name] = (list(resolved), "custom")


def _collect_groupings(
    config: DaemonConfig,
    all_groupings: dict[str, list[str]],
    custom_groupings: dict[str, list[str]] | None,
) -> dict[str, tuple[list[str], str]]:
    """Choose the groupings this run briefs.

    Args:
        config: Daemon configuration
        all_groupings: Every grouping, for resolving nested ones
        custom_groupings: The custom groupings, empty or None when there are none

    Returns:
        Grouping name -> (airports, ARTCC, or ``custom`` for custom groupings)
    """
    groupings_to_process: dict[str, tuple[list[str], str]] = {}

    if config.include_presets:
        for json_file in config.preset_groupings_dir.glob("*.json"):
            artcc = json_file.stem  # e.g., "ZOA" from "ZOA.json"
            if config.artcc_filter and artcc not in config.artcc_filter:
                continue
            _add_preset_file(json_file, artcc, all_groupings, groupings_to_process)

    if config.include_custom and custom_groupings:
        _add_custom_groupings(custom_groupings, all_groupings, groupings_to_process)

    return groupings_to_process


def _airports_and_artccs(
    groupings_to_process: dict[str, tuple[list[str], str]],
) -> tuple[set[str], set[str]]:
    """Every airport the groupings hold, and the ARTCCs of the preset ones."""
    all_airports: set[str] = set()
    artccs_involved: set[str] = set()
    for airports, artcc in groupings_to_process.values():
        all_airports.update(airports)
        if artcc != "custom":
            artccs_involved.add(artcc)
    return all_airports, artccs_involved


def _load_cached_weather(config: DaemonConfig) -> tuple[dict, dict, dict, str] | None:
    """Load the weather cache this run may use.

    A run that fetches fresh weather uses a cache younger than its TTL; a run from
    cached weather uses one of any age and warns when there is none.

    Returns:
        (metars, tafs, atis, timestamp), or None when no usable cache exists
    """
    if config.fetch_fresh_weather:
        return _load_weather_cache(
            config.weather_cache_dir, max_age_seconds=config.weather_cache_ttl
        )

    cache_result = _load_weather_cache(
        config.weather_cache_dir, max_age_seconds=86400 * 365
    )  # 1 year
    if cache_result is None:
        print("  WARNING: No cached weather data found, continuing with empty weather")
        logger.warning("No cached weather data found, continuing with empty weather")
    return cache_result


def _load_run_weather(
    config: DaemonConfig,
    airports_list: list[str],
    artccs_involved: set[str],
    unified_airport_data: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    """The run's METARs, TAFs and ATIS: from the cache when usable, else fetched.

    Args:
        config: Daemon configuration
        airports_list: Every airport the run briefs
        artccs_involved: The ARTCCs of the preset groupings
        unified_airport_data: Unified airport data

    Returns:
        (metars, tafs, atis), all empty for a cached-weather run with no cache
    """
    cache_result = _load_cached_weather(config)
    if cache_result is not None:
        metars, tafs, atis_data, cache_timestamp = cache_result
        print(f"  Using cached weather data (from {cache_timestamp})")
        logger.info(f"Using cached weather data from {cache_timestamp}")
        return metars, tafs, atis_data
    if config.fetch_fresh_weather:
        return _fetch_fresh_weather(
            config, airports_list, artccs_involved, unified_airport_data
        )
    return {}, {}, {}


def _fetch_fresh_weather(
    config: DaemonConfig,
    airports_list: list[str],
    artccs_involved: set[str],
    unified_airport_data: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    """Fetch METARs, TAFs and ATIS, then cache them for the next run.

    METARs and TAFs come by ARTCC bounding box (about one API call per ARTCC
    instead of one per airport), then one by one for the airports the boxes
    missed.

    Returns:
        (metars, tafs, atis)
    """
    metars: dict[str, str] = {}
    tafs: dict[str, str] = {}

    if artccs_involved:
        _fetch_bbox_weather(config, artccs_involved, len(airports_list), metars, tafs)

    _fetch_missing_airports(config, airports_list, unified_airport_data, metars, tafs)
    _log_rate_limit_status()
    atis_data = _fetch_atis(airports_list)

    # Cache weather data for next run
    _save_weather_cache(config.weather_cache_dir, metars, tafs, atis_data)
    return metars, tafs, atis_data


def _fetch_bbox_weather(
    config: DaemonConfig,
    artccs_involved: set[str],
    num_airports: int,
    metars: dict[str, str],
    tafs: dict[str, str],
) -> None:
    """Fetch METARs and TAFs by ARTCC bounding box, in parallel, into the dicts given.

    Args:
        config: Daemon configuration
        artccs_involved: The ARTCCs whose boxes to fetch
        num_airports: How many airports the run briefs, for the progress line
        metars: ICAO -> METAR, added to in place
        tafs: ICAO -> TAF, added to in place
    """
    artcc_bboxes = get_artcc_bboxes(artccs_involved, config.artcc_cache_dir)
    if not artcc_bboxes:
        return

    print(
        f"  Fetching weather via bbox for {len(artcc_bboxes)} ARTCCs ({num_airports} airports)..."
    )
    logger.info(f"Fetching weather via bbox for {len(artcc_bboxes)} ARTCCs")

    bbox_progress = ProgressTracker(
        "Weather fetch (bbox)", len(artcc_bboxes), log_interval_pct=25
    )

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=min(5, len(artcc_bboxes))) as executor:
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

    print(f"    Retrieved {len(metars)} METARs, {len(tafs)} TAFs from bbox queries")
    logger.info(f"Retrieved {len(metars)} METARs, {len(tafs)} TAFs from bbox queries")


def _likely_has_metar(icao: str, unified_airport_data: dict[str, Any]) -> bool:
    """Whether an airport likely reports METARs, so fetching it one by one is worth it.

    True for a 4-letter ICAO starting with K (US METAR stations), an actual
    control tower (not NON-ATCT), FAR 139 certification (scheduled passenger
    service) or an IATA code (commercial airports). This avoids hundreds of wasted
    requests for small private strips like "68AR" or "7CO8".
    """
    if len(icao) == 4 and icao.startswith("K"):
        return True
    airport_info = unified_airport_data.get(icao, {})
    tower_type = airport_info.get("tower_type", "")
    if tower_type and tower_type != "NON-ATCT":
        return True
    if airport_info.get("far139"):
        return True
    return bool(airport_info.get("iata"))


def _fetch_missing_airports(
    config: DaemonConfig,
    airports_list: list[str],
    unified_airport_data: dict[str, Any],
    metars: dict[str, str],
    tafs: dict[str, str],
) -> None:
    """Fetch, one by one, the airports without a METAR that likely report one.

    Args:
        config: Daemon configuration
        airports_list: Every airport the run briefs
        unified_airport_data: Unified airport data
        metars: ICAO -> METAR, added to in place
        tafs: ICAO -> TAF, added to in place
    """
    missing_airports = [a for a in airports_list if a not in metars]
    fetchable_airports = [
        a for a in missing_airports if _likely_has_metar(a, unified_airport_data)
    ]
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
        print(f"  Skipping {skipped_count} airports unlikely to have METAR stations")
        logger.info(
            f"Skipping {skipped_count} airports unlikely to have METAR stations"
        )


def _log_rate_limit_status() -> None:
    """Log the weather API's rate limiting after the fetches."""
    rate_status = get_rate_limit_status()
    if rate_status["is_rate_limited"]:
        logger.warning(
            f"Rate limiting active: backoff={rate_status['backoff_seconds']:.1f}s, errors={rate_status['error_count']}"
        )
    elif rate_status["error_count"] > 0:
        logger.info(
            f"Rate limit recovery: {rate_status['error_count']} errors, recovered"
        )


def _fetch_atis(airports_list: list[str]) -> dict[str, Any]:
    """Fetch VATSIM ATIS, then real-world D-ATIS for the airports without one.

    Args:
        airports_list: Every airport the run briefs

    Returns:
        ICAO -> ATIS records
    """
    print("  Fetching VATSIM ATIS data...")
    logger.info("Fetching VATSIM ATIS data")
    vatsim_data = download_vatsim_data()
    atis_data = get_atis_for_airports(vatsim_data, airports_list) if vatsim_data else {}
    vatsim_atis_count = len([a for a in atis_data.values() if a]) if atis_data else 0
    print(f"    Found {vatsim_atis_count} airports with VATSIM ATIS")
    logger.info(f"Found {vatsim_atis_count} airports with active VATSIM ATIS")

    airports_without_vatsim_atis = [
        icao for icao in airports_list if icao not in atis_data
    ]
    if airports_without_vatsim_atis:
        print("  Fetching RW D-ATIS for airports without VATSIM ATIS...")
        logger.info(
            f"Fetching RW D-ATIS for {len(airports_without_vatsim_atis)} airports"
        )
        rw_atis_data = get_datis_for_airports(airports_without_vatsim_atis)
        rw_atis_count = len([a for a in rw_atis_data.values() if a])
        atis_data.update(rw_atis_data)
        print(f"    Found {rw_atis_count} airports with RW D-ATIS")
        logger.info(f"Found {rw_atis_count} airports with RW D-ATIS")

    atis_count = len([a for a in atis_data.values() if a]) if atis_data else 0
    print(f"    Total ATIS: {atis_count} airports")
    logger.info(f"Total ATIS: {atis_count} airports")
    return atis_data


def _tile_skip_decision(
    config: DaemonConfig, metars: dict[str, str], tafs: dict[str, str]
) -> tuple[str | None, bool]:
    """This run's skip key and whether the tile stage may skip.

    The key compares this run's weather and code against the key the last
    completed tile stage wrote. Briefings and the index always run.

    Returns:
        (skip key, or None when there are no METARs; whether to skip the tile stage)
    """
    skip_key = compute_skip_key(metars, tafs) if metars else None
    return skip_key, should_skip_tile_stage(config, skip_key)


def _airport_artcc(unified_airport_data: dict[str, Any], icao: str) -> str | None:
    """An airport's three-letter ARTCC from the unified data, or None."""
    airport_info = unified_airport_data.get(icao, {})
    # Field is 'artcc' in unified airport data
    artcc_code = airport_info.get("artcc", "")
    if artcc_code and len(artcc_code) == 3:
        return artcc_code
    return None


def _airport_coords(
    unified_airport_data: dict[str, Any], icao: str
) -> tuple[float, float] | None:
    """An airport's (lat, lon) as floats from the unified data, or None."""
    airport_info = unified_airport_data.get(icao, {})
    lat = airport_info.get("latitude")
    lon = airport_info.get("longitude")
    if lat is not None and lon is not None:
        return (float(lat), float(lon))
    return None


def _infer_artccs(
    unified_airport_data: dict[str, Any], airports: list[str]
) -> set[str]:
    """The ARTCCs a custom grouping's airports belong to."""
    artccs = set()
    for icao in airports:
        artcc_code = _airport_artcc(unified_airport_data, icao)
        if artcc_code:
            artccs.add(artcc_code)
    return artccs


def _grouping_index_info(
    generator: WeatherBriefingGenerator,
    grouping_name: str,
    safe_name: str,
    airports: list[str],
    unified_airport_data: dict[str, Any],
) -> dict[str, Any]:
    """A grouping's entry for the index page and its map.

    Args:
        generator: The grouping's briefing generator, its weather already loaded
        grouping_name: The grouping's name
        safe_name: The grouping's file-safe name
        airports: The grouping's airports
        unified_airport_data: Unified airport data

    Returns:
        Its name, file name, airport count, categories, hover polygon coordinates
        and per-airport weather points
    """
    category_summary = generator.get_category_summary()
    airport_weather_points = generator.get_airport_weather_points()

    airport_coords = []
    for icao in airports:
        coords = _airport_coords(unified_airport_data, icao)
        if coords:
            airport_coords.append(coords)

    return {
        "name": grouping_name,
        "filename": f"{safe_name}.html",
        "airport_count": len(airports),
        "categories": category_summary,
        "airport_coords": airport_coords,  # For hover polygon
        "airport_weather_points": airport_weather_points,  # For localized map coloring
    }


def _add_to_artcc_map(
    artcc_groupings_map: dict[str, list[dict]],
    grouping_info: dict[str, Any],
    artcc: str,
    airports: list[str],
    unified_airport_data: dict[str, Any],
) -> None:
    """File a grouping's index entry under its ARTCC.

    A custom grouping goes under every ARTCC its airports belong to, marked as
    custom and still served from the custom directory; one with no ARTCC stays
    under ``custom``.

    Args:
        artcc_groupings_map: ARTCC -> grouping entries, added to in place
        grouping_info: The grouping's index entry
        artcc: The grouping's ARTCC, or ``custom``
        airports: The grouping's airports
        unified_airport_data: Unified airport data
    """
    if artcc != "custom":
        artcc_groupings_map.setdefault(artcc, []).append(grouping_info)
        return

    inferred_artccs = _infer_artccs(unified_airport_data, airports)
    if not inferred_artccs:
        artcc_groupings_map.setdefault("custom", []).append(grouping_info)
        return

    for inferred_artcc in inferred_artccs:
        artcc_info = grouping_info.copy()
        artcc_info["is_custom"] = True
        artcc_info["path_prefix"] = "custom"
        artcc_groupings_map.setdefault(inferred_artcc, []).append(artcc_info)


def _run_briefing_stage(
    config: DaemonConfig,
    groupings_to_process: dict[str, tuple[list[str], str]],
    *,
    unified_airport_data: dict[str, Any],
    disambiguator: Any,
    metars: dict[str, str],
    tafs: dict[str, str],
    atis_data: dict[str, Any],
) -> tuple[dict[str, str], dict[str, list[dict]]]:
    """Write each grouping's briefing (when enabled) and gather the index entries.

    A grouping whose briefing raises is logged with its traceback and skipped, so
    the run goes on to the remaining groupings, the ARTCC briefings, the index and
    the tiles. The index entries are gathered even when briefings are off.

    Returns:
        (written file path -> grouping name, ARTCC -> grouping index entries)
    """
    num_groupings = len(groupings_to_process)
    print(f"  Generating {num_groupings} briefings...")
    logger.info(f"Generating {num_groupings} briefings")

    config.output_dir.mkdir(parents=True, exist_ok=True)

    generated_files: dict[str, str] = {}
    artcc_groupings_map: dict[str, list[dict]] = {}

    briefing_progress = None
    if config.generate_briefings:
        briefing_progress = ProgressTracker(
            "Briefing generation", num_groupings, log_interval_pct=10
        )
    else:
        print("  Skipping briefing generation (not in stages)")

    for grouping_name, (airports, artcc) in groupings_to_process.items():
        generator = WeatherBriefingGenerator(
            grouping_name=grouping_name,
            airports=airports,
            unified_airport_data=unified_airport_data,
            disambiguator=disambiguator,
        )
        generator.fetch_weather_data(metars, tafs, atis_data)

        safe_name = re.sub(r"[^\w\-_]", "_", grouping_name)
        if config.generate_briefings and not _write_briefing_html(
            generator,
            config.output_dir / artcc / f"{safe_name}.html",
            grouping_name,
            artcc,
            generated_files,
        ):
            continue

        grouping_info = _grouping_index_info(
            generator, grouping_name, safe_name, airports, unified_airport_data
        )
        _add_to_artcc_map(
            artcc_groupings_map, grouping_info, artcc, airports, unified_airport_data
        )

        if briefing_progress is not None:
            briefing_progress.update()

    if config.generate_briefings:
        _generate_artcc_wide_briefings(
            config=config,
            groupings_to_process=groupings_to_process,
            sources=_BriefingSources(
                unified_airport_data=unified_airport_data,
                disambiguator=disambiguator,
                metars=metars,
                tafs=tafs,
                atis_data=atis_data,
            ),
            generated_files=generated_files,
        )

    return generated_files, artcc_groupings_map


def _run_index_stage(
    config: DaemonConfig,
    artcc_groupings_map: dict[str, list[dict]],
    unified_airport_data: dict[str, Any],
    run_tile_version: int,
    generated_files: dict[str, str],
) -> None:
    """Write the index page when enabled, recording it in ``generated_files``.

    It runs before the slow tile stage, so a run killed there still publishes a
    fresh index, pointing at the last complete tile set.
    """
    if not config.generate_index:
        return

    from .index_generator import generate_index_page

    index_path = generate_index_page(
        config,
        artcc_groupings_map,
        unified_airport_data,
        fallback_tile_version=run_tile_version,
    )
    if index_path:
        generated_files[str(index_path)] = "Index"


def _collect_tile_weather(
    metars: dict[str, str], unified_airport_data: dict[str, Any]
) -> dict[str, dict]:
    """Every airport with a METAR and coordinates, with its flight category.

    It covers all the weather fetched, not just the groupings' airports, for
    fuller tile coverage.
    """
    all_airport_weather: dict[str, dict] = {}
    for icao, metar in metars.items():
        if not metar:
            continue
        airport_info = unified_airport_data.get(icao, {})
        lat = airport_info.get("latitude")
        lon = airport_info.get("longitude")
        if lat is None or lon is None:
            continue
        all_airport_weather[icao] = {
            "icao": icao,
            "lat": lat,
            "lon": lon,
            "category": get_flight_category(metar),
        }
    return all_airport_weather


def _generate_tiles(
    config: DaemonConfig,
    metars: dict[str, str],
    unified_airport_data: dict[str, Any],
    skip_key: str | None,
    run_tile_version: int,
) -> None:
    """Draw the weather overlay tiles, then record their version and the skip key.

    Args:
        config: Daemon configuration
        metars: ICAO -> METAR for every airport fetched
        unified_airport_data: Unified airport data
        skip_key: This run's skip key, or None when there are no METARs
        run_tile_version: This run's tile version, its start time
    """
    print("  Generating weather overlay tiles...")
    logger.info("Generating weather overlay tiles")

    all_airport_weather = _collect_tile_weather(metars, unified_airport_data)

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
        logger.warning("No airport weather data collected - skipping tile generation")
        return

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
    write_tile_version(tiles_dir, run_tile_version)
    # Saved only now: a run killed in this stage leaves the old key, so the
    # next run regenerates the tiles it never finished.
    if skip_key is not None:
        save_weather_hash(config.weather_cache_dir, skip_key)

    total_tiles = sum(tile_results.values())
    print(f"    Generated {total_tiles} weather tiles")
    logger.info(
        f"Generated {total_tiles} weather tiles across {len(tile_results)} zoom levels"
    )


def _run_tile_stage(
    config: DaemonConfig,
    metars: dict[str, str],
    unified_airport_data: dict[str, Any],
    *,
    skip_key: str | None,
    skip_tiles: bool,
    run_tile_version: int,
) -> None:
    """Run the tile stage, or say why it does not run.

    A run whose weather and code are unchanged since the last completed tile
    stage leaves the tiles and version.txt alone, so the page's ?v= does not
    churn.
    """
    if config.generate_tiles and not skip_tiles:
        _generate_tiles(
            config, metars, unified_airport_data, skip_key, run_tile_version
        )
    elif config.generate_tiles:
        print("  Tile stage skipped: weather and code unchanged")
        logger.info("Tile stage skipped: weather and code unchanged")
    else:
        print("  Skipping tile generation (--no-tiles)")
        logger.info("Skipping tile generation (--no-tiles)")


def generate(config: DaemonConfig) -> dict[str, str]:
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
    start_time = datetime.now(UTC)
    timestamp = start_time.strftime("%H:%M:%SZ")
    print(f"[{timestamp}] Starting weather briefing generation...")
    logger.info("Starting weather briefing generation")

    unified_airport_data, disambiguator, all_groupings = _load_airport_inputs(config)
    custom_groupings = (
        load_custom_groupings(str(config.custom_groupings_path))
        if config.include_custom
        else {}
    )
    groupings_to_process = _collect_groupings(config, all_groupings, custom_groupings)
    print(f"  Found {len(groupings_to_process)} groupings to process")

    all_airports, artccs_involved = _airports_and_artccs(groupings_to_process)
    metars, tafs, atis_data = _load_run_weather(
        config, list(all_airports), artccs_involved, unified_airport_data
    )

    skip_key, skip_tiles = _tile_skip_decision(config, metars, tafs)

    generated_files, artcc_groupings_map = _run_briefing_stage(
        config,
        groupings_to_process,
        unified_airport_data=unified_airport_data,
        disambiguator=disambiguator,
        metars=metars,
        tafs=tafs,
        atis_data=atis_data,
    )

    # Version of this run's tile set, recorded once all its tiles are written
    run_tile_version = int(start_time.timestamp())

    _run_index_stage(
        config,
        artcc_groupings_map,
        unified_airport_data,
        run_tile_version,
        generated_files,
    )
    _run_tile_stage(
        config,
        metars,
        unified_airport_data,
        skip_key=skip_key,
        skip_tiles=skip_tiles,
        run_tile_version=run_tile_version,
    )

    total_time = (datetime.now(UTC) - start_time).total_seconds()
    end_timestamp = datetime.now(UTC).strftime("%H:%M:%SZ")
    print(
        f"[{end_timestamp}] Generation complete! {len(generated_files)} files in {total_time:.1f}s"
    )
    logger.info(
        f"Generation complete: {len(generated_files)} files in {total_time:.1f}s"
    )
    return generated_files


# Legacy aliases for backwards compatibility
def generate_all_briefings(config: DaemonConfig) -> dict[str, str]:
    """Legacy alias for generate(). Use generate() instead."""
    return generate(config)


if __name__ == "__main__":
    # Simple test run
    setup_file_logging()
    config = DaemonConfig(output_dir=Path("./test_output"))
    generate(config)
