"""File logging starts only when an entry point calls setup_file_logging().

The logger's state is process-wide, so every scenario that starts file logging
runs in a fresh subprocess with LOCALAPPDATA, XDG_DATA_HOME and HOME pointed at
a pytest tmp_path.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

REPORT_PREFIX = "#REPORT#"

_PREAMBLE = """
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, {repo_root!r})

from common import logger
from common.paths import get_user_logs_dir

logs_dir = get_user_logs_dir()


def report(**values):
    print({prefix!r} + json.dumps(values))


def vatsim_debug_file_handlers():
    return [
        handler
        for handler in logging.getLogger("vatsim_debug").handlers
        if isinstance(handler, logging.FileHandler)
    ]
"""


def _run(code: str, tmp_path: Path) -> dict:
    """Run code in a subprocess with the user dirs under tmp_path.

    Args:
        code: Python source appended to the preamble, ending in report(...).
        tmp_path: Directory the subprocess resolves the user dirs to.

    Returns:
        The JSON object the subprocess passed to report().

    Raises:
        AssertionError: If the subprocess failed or reported more than once.
    """
    env = dict(os.environ)
    for variable in ("LOCALAPPDATA", "XDG_DATA_HOME", "HOME"):
        env[variable] = str(tmp_path)
    env["STATSIM_API_KEY"] = ""

    source = _PREAMBLE.format(repo_root=str(REPO_ROOT), prefix=REPORT_PREFIX) + code
    result = subprocess.run(
        [sys.executable, "-I", "-c", source],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"subprocess failed:\n{result.stderr}"
    reports = [
        line for line in result.stdout.splitlines() if line.startswith(REPORT_PREFIX)
    ]
    assert len(reports) == 1, f"expected one report line:\n{result.stdout}"
    return json.loads(reports[0][len(REPORT_PREFIX) :])


def test_importing_ui_starts_no_file_logging(tmp_path: Path) -> None:
    report = _run(
        """
import ui.modals.historical_stats
import backend

report(
    logs_dir_exists=logs_dir.exists(),
    file_handlers=len(vatsim_debug_file_handlers()),
)
""",
        tmp_path,
    )
    assert report["logs_dir_exists"] is False, "importing ui created the logs directory"
    assert report["file_handlers"] == 0, "importing ui attached a FileHandler"


def test_setup_file_logging_writes_buffered_records_and_prunes_old_logs(
    tmp_path: Path,
) -> None:
    report = _run(
        """
logs_dir.mkdir(parents=True)

old = logs_dir / "debug_20000101.log"
old.write_text("old", encoding="utf-8")
eleven_days = time.time() - 11 * 24 * 3600
os.utime(old, (eleven_days, eleven_days))

recent = logs_dir / "debug_20000102.log"
recent.write_text("recent", encoding="utf-8")
one_day = time.time() - 24 * 3600
os.utime(recent, (one_day, one_day))

logger.info("before-setup")
logger.setup_file_logging()
logger.info("after-setup")

content = Path(logger.get_log_file_path()).read_text(encoding="utf-8")
report(
    old_exists=old.exists(),
    recent_exists=recent.exists(),
    before_index=content.find("before-setup"),
    after_index=content.find("after-setup"),
)
""",
        tmp_path,
    )
    assert report["old_exists"] is False, "an 11-day-old log survived cleanup"
    assert report["recent_exists"] is True, "a 1-day-old log was pruned"
    assert report["before_index"] >= 0, "the buffered record was never written"
    assert report["before_index"] < report["after_index"], (
        "buffered records were not written before later ones"
    )


def test_buffer_cap_drops_oldest_and_says_so(tmp_path: Path) -> None:
    report = _run(
        """
for number in range(1, 1006):
    logger.info(f"record-{number}")
logger.setup_file_logging()

content = Path(logger.get_log_file_path()).read_text(encoding="utf-8")
report(
    numbers=[int(match) for match in re.findall(r"record-(\\d+)", content)],
    dropped_line=(
        "5 log records from before file logging started were dropped" in content
    ),
)
""",
        tmp_path,
    )
    assert report["numbers"] == list(range(6, 1006)), "the buffer is not capped at 1000"
    assert report["dropped_line"] is True, "the drop count was not recorded in the log"


def test_log_calls_before_setup_do_not_raise() -> None:
    from common import logger

    logger.debug("debug before setup")
    logger.info("info before setup")
    logger.warning("warning before setup")
    logger.error("error before setup")


def test_error_traceback_reaches_the_file(tmp_path: Path) -> None:
    report = _run(
        """
try:
    raise ValueError("boom-value")
except ValueError:
    logger.error("boom", exc_info=True)
logger.setup_file_logging()

content = Path(logger.get_log_file_path()).read_text(encoding="utf-8")
report(
    has_message="boom" in content,
    has_header="Traceback (most recent call last)" in content,
    has_exception="ValueError: boom-value" in content,
)
""",
        tmp_path,
    )
    assert report["has_message"] is True, "the error message was lost"
    assert report["has_header"] is True, "the traceback header was lost"
    assert report["has_exception"] is True, "the exception line was lost"


def test_buffered_records_freeze_message_and_traceback() -> None:
    from common import logger

    logging.getLogger("vatsim_debug").info("value=%s", 42)
    try:
        raise ValueError("in-process boom")
    except ValueError:
        logger.error("boom", exc_info=True)

    value_record, error_record = list(logger._buffer_handler.records)[-2:]
    assert value_record.msg == "value=42", "the message was not frozen"
    assert value_record.args is None, "the record kept its arguments alive"
    assert error_record.exc_info is None, "the record kept its traceback alive"
    assert error_record.exc_text is not None, "the traceback text was not rendered"
    assert "ValueError: in-process boom" in error_record.exc_text


def _run_main(args: list[str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Run main.py with args, the user dirs under tmp_path and no isolated mode."""
    env = dict(os.environ)
    for variable in ("LOCALAPPDATA", "XDG_DATA_HOME", "HOME"):
        env[variable] = str(tmp_path)
    env["STATSIM_API_KEY"] = ""
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "main.py"), *args],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_help_creates_no_logs_dir(tmp_path: Path) -> None:
    result = _run_main(["--help"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.rglob("logs")), "python main.py --help created a logs dir"


def test_invalid_argument_creates_no_log_file(tmp_path: Path) -> None:
    result = _run_main(["--definitely-not-an-option"], tmp_path)
    assert result.returncode != 0, "an unknown option should be rejected"
    assert not list(tmp_path.rglob("debug_*.log")), (
        "an invalid argument created a log file"
    )
