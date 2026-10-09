#!/usr/bin/env python
"""Update coverage threshold in pyproject.toml based on current coverage."""

import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from coverage_ratchet import apply_ratchet, running_in_ci

IS_CI = running_in_ci()

PYPROJECT_PATH = Path(__file__).parent.parent / "pyproject.toml"

# weasyprint's cffi dlopen() can't find Homebrew libgobject/libpango on macOS
# unless /opt/homebrew/lib is in DYLD_FALLBACK_LIBRARY_PATH at process start.
# Inject it here so the pytest subprocess inherits it.
if platform.system() == "Darwin":
    HOMEBREW_LIB = "/opt/homebrew/lib"
    existing_dyld = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    if HOMEBREW_LIB not in existing_dyld.split(":"):
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
            f"{HOMEBREW_LIB}:{existing_dyld}" if existing_dyld else HOMEBREW_LIB
        )


def get_current_threshold(pyproject_path: Path) -> int:
    """Extract current coverage threshold from pyproject.toml."""
    content = pyproject_path.read_text()
    match = re.search(r"--cov-fail-under=(\d+)", content)
    if match:
        return int(match.group(1))
    return 0


def coverage_apps(pyproject_path: Path) -> list[str]:
    """The apps pytest measures, read from the ``--cov`` flags in addopts.

    pyproject.toml's addopts is the one list of covered apps; this hook used
    to keep a shorter copy of its own (issue #337).
    """
    config = tomllib.loads(pyproject_path.read_text())
    addopts = config["tool"]["pytest"]["ini_options"]["addopts"]
    return re.findall(r"--cov=(\S+)", addopts)


def lower_bound_of_printed(printed: str) -> float:
    """The lowest coverage a *rounded* printed percentage can stand for.

    pytest-cov and coverage.py print a rounded figure, so "99.00%" can mean
    anything from 98.995% up, and "99%" anything from 98.5% up (issue #552).
    Taking the bottom of that interval keeps a stored threshold honest when
    the exact figure is unavailable.
    """
    _, _, decimals = printed.partition(".")
    return float(printed) - 0.5 * 10 ** -len(decimals)


def coverage_from_report(report_path: Path) -> float | None:
    """Exact total coverage from coverage.py's JSON report, if it was written.

    ``percent_covered`` is the unrounded ratio -- the only figure in the run's
    output that has not already been rounded for display.
    """
    if not report_path.is_file():
        return None
    totals = json.loads(report_path.read_text()).get("totals", {})
    percent = totals.get("percent_covered")
    return float(percent) if percent is not None else None


def coverage_from_output(output: str) -> float | None:
    """Total coverage read back from the printed summary, never rounded up."""
    # Match "Total coverage: 92.86%"
    match = re.search(r"Total coverage: (\d+\.?\d*)%", output)
    if match:
        return lower_bound_of_printed(match.group(1))

    # Match "TOTAL ... 93%"
    match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+\.?\d*)%", output)
    if match:
        return lower_bound_of_printed(match.group(1))

    return None


def get_actual_coverage() -> tuple[int, float | None]:
    """Run pytest and extract actual coverage percentage.

    Returns (returncode, coverage_percent). coverage_percent is None if it
    could not be determined from the run. It is the exact measured figure
    whenever coverage.py's JSON report is available, and otherwise a lower
    bound on what the printed summary can mean -- never a rounded-up value,
    which is how the ratchet used to store a threshold no run could clear.
    """
    with tempfile.TemporaryDirectory() as report_dir:
        report_path = Path(report_dir) / "coverage.json"
        result = subprocess.run(
            [
                "uv",
                "run",
                "pytest",
                *(f"--cov={app}" for app in coverage_apps(PYPROJECT_PATH)),
                "--cov-report=term-missing",
                f"--cov-report=json:{report_path}",
                # Measure first, judge after. addopts carries the stored
                # --cov-fail-under, and applying it here made the run exit
                # non-zero whenever coverage had drifted below it -- so the
                # ratchet gave up before it could lower the number, and the
                # threshold wedged every local commit (issue #558).
                "--cov-fail-under=0",
                "-q",
            ],
            capture_output=True,
            text=True,
        )

        output = result.stdout + result.stderr

        # Always surface the test output, pass or fail. A hook that prints
        # nothing on success leaves no way to tell a green suite from one that
        # collected nothing, and no way to see how long it took -- which is how
        # a pytest run that had quietly grown to eleven minutes went unnoticed.
        # The e2e hook prints its summary for the same reason.
        print(output)

        coverage = coverage_from_report(report_path)

    if coverage is None:
        coverage = coverage_from_output(output)

    return result.returncode, coverage


def update_threshold(pyproject_path: Path, new_threshold: int) -> bool:
    """Update the coverage threshold in pyproject.toml."""
    content = pyproject_path.read_text()
    new_content = re.sub(
        r"--cov-fail-under=\d+",
        f"--cov-fail-under={new_threshold}",
        content,
    )
    if new_content != content:
        pyproject_path.write_text(new_content)
        return True
    return False


def main() -> int:
    pyproject_path = PYPROJECT_PATH

    current_threshold = get_current_threshold(pyproject_path)
    returncode, actual_coverage = get_actual_coverage()

    if returncode != 0:
        print(f"pytest failed with exit code {returncode}")
        return 1

    if actual_coverage is None:
        print("Could not determine actual coverage")
        return 1

    return apply_ratchet(
        label="coverage",
        current_threshold=current_threshold,
        actual_coverage=actual_coverage,
        in_ci=IS_CI,
        write_threshold=lambda threshold: update_threshold(pyproject_path, threshold),
        threshold_file=pyproject_path,
        # A point of slack, as the JS ratchet has always had (issue #561).
        # Two runs of this suite do not measure the same: the WeasyPrint
        # export tests run only where their library is on the path, so a
        # machine that has it measured 99.01% while a plain run measured
        # 98.93%. Without slack the luckier run stores a bar the ordinary
        # run fails, and every commit after it is blocked.
        hysteresis_points=1,
    )


if __name__ == "__main__":
    sys.exit(main())
