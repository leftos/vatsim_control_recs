"""Recapture the goldens of ``test_weather_generator_characterization.py``.

Runs every scenario and case of the characterization against the current
``scripts.weather_daemon.generator`` and rewrites the golden assignments at the
end of the test file. Run it from the repo root after an intentional change to
what the generator writes, logs or hands its stages, then review the diff:

    uv run --group dev python tests/capture_weather_generator_goldens.py
"""

import importlib
import pprint
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEST_FILE = ROOT / "tests" / "test_weather_generator_characterization.py"
MARKER = "GENERATE_GOLDEN: dict[str, Any] = "


def _with_clock(t: Any, gen: Any, fn: Callable[[], Any]) -> Any:
    with pytest.MonkeyPatch.context() as mp:
        t.freeze_clock(mp, gen)
        return fn()


def _run(t: Any, gen: Any, scenario: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp, pytest.MonkeyPatch.context() as mp:
        return t.run_generate(gen, Path(tmp), mp, scenario)


def capture() -> list[tuple[str, dict[str, Any]]]:
    """Every golden table, in the order the test file assigns them."""
    sys.path.insert(0, str(ROOT))
    t = importlib.import_module("tests.test_weather_generator_characterization")
    gen = t.generator
    scenarios = sorted(t.GENERATE_SCENARIOS.items())
    matrix = sorted(t.STAGE_MATRIX.items())
    return [
        ("GENERATE_GOLDEN", {name: _run(t, gen, s) for name, s in scenarios}),
        (
            "STAGE_MATRIX_GOLDEN",
            {name: t.snapshot_digest(_run(t, gen, s)) for name, s in matrix},
        ),
        (
            "CARD_GOLDEN",
            {
                name: _with_clock(t, gen, lambda n=name: t.build_card(gen, n))
                for name in sorted(t.CARD_CASES)
            },
        ),
        (
            "HTML_GOLDEN",
            {
                name: _with_clock(
                    t,
                    gen,
                    lambda n=name: t._sha12(t.HTML_CASES[n](gen).generate_html()),
                )
                for name in sorted(t.HTML_CASES)
            },
        ),
        (
            "POINTS_GOLDEN",
            {
                name: _with_clock(
                    t,
                    gen,
                    lambda n=name: t.POINTS_CASES[n](gen).get_airport_weather_points(),
                )
                for name in sorted(t.POINTS_CASES)
            },
        ),
    ]


def main() -> None:
    tables = capture()
    text = TEST_FILE.read_text(encoding="utf-8")
    head = text[: text.index(MARKER)]
    body = "\n\n".join(
        f"{name}: dict[str, Any] = {pprint.pformat(value, width=88, sort_dicts=False)}"
        for name, value in tables
    )
    with open(TEST_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + body + "\n")
    print(f"Rewrote {len(tables)} golden tables in {TEST_FILE.relative_to(ROOT)}")
    print(
        "Format it with: uv run --group dev ruff format "
        + TEST_FILE.relative_to(ROOT).as_posix()
    )


if __name__ == "__main__":
    main()
