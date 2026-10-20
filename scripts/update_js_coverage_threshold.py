#!/usr/bin/env python
"""Update JS e2e coverage threshold in package.json based on current coverage."""

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from coverage_ratchet import apply_ratchet, running_in_ci

IS_CI = running_in_ci()


@dataclass(frozen=True)
class PlaywrightRun:
    """Outcome of one `npx playwright test` invocation."""

    returncode: int
    output: str

    @property
    def test_count(self) -> int | None:
        """Tests Playwright said it was running, or None if it never said."""
        match = re.search(r"Running (\d+) tests?", self.output)
        return int(match.group(1)) if match else None


def get_current_threshold(package_path: Path) -> int:
    """Extract current JS coverage threshold from package.json."""
    content = json.loads(package_path.read_text())
    return content.get("jsCoverageThreshold", 0)


def run_e2e_tests() -> PlaywrightRun:
    """Run the Playwright suite, capturing its combined output."""
    # Clean previous coverage data
    subprocess.run(["npx", "rimraf", ".nyc_output"], capture_output=True)

    result = subprocess.run(
        ["npx", "playwright", "test"],
        capture_output=True,
        text=True,
    )
    return PlaywrightRun(
        returncode=result.returncode,
        output=result.stdout + result.stderr,
    )


def coverage_from_summary(summary_path: Path) -> float | None:
    """Exact statement coverage from nyc's json-summary report, if written.

    The counts are computed from, rather than read out of, ``pct``: istanbul
    stores that already truncated to two places, and the ratchet should judge
    the figure the run actually achieved (issue #552).
    """
    if not summary_path.is_file():
        return None
    statements = (
        json.loads(summary_path.read_text()).get("total", {}).get("statements", {})
    )
    covered, total = statements.get("covered"), statements.get("total")
    if covered is None or not total:
        return None
    return 100.0 * covered / total


def coverage_from_summary_text(output: str) -> float | None:
    """Statement coverage read back from nyc's text summary, never rounded up.

    The printed percentage is rounded for display, so "45.19%" guarantees only
    45.185%; the bottom of that interval is what the ratchet may rely on.
    """
    # Match "Statements   : 45.19% ( 94/208 )". The decimals sit inside the
    # optional group so a long run of digits is not read twice (issue #44).
    match = re.search(r"Statements\s+:\s+(\d+(?:\.\d*)?)%", output)
    if match is None:
        return None
    printed = match.group(1)
    _, _, decimals = printed.partition(".")
    return float(printed) - 0.5 * 10 ** -len(decimals)


def get_reported_coverage() -> float | None:
    """Statement coverage percentage from nyc, or None if nyc collected nothing."""
    with tempfile.TemporaryDirectory() as report_dir:
        summary_path = Path(report_dir) / "coverage-summary.json"
        report_result = subprocess.run(
            [
                "npx",
                "nyc",
                "report",
                "--reporter=text-summary",
                "--reporter=json-summary",
                f"--report-dir={report_dir}",
            ],
            capture_output=True,
            text=True,
        )

        output = report_result.stdout + report_result.stderr
        print(output)

        coverage = coverage_from_summary(summary_path)

    if coverage is None:
        coverage = coverage_from_summary_text(output)
    return coverage


def update_threshold(package_path: Path, new_threshold: int) -> bool:
    """Update the JS coverage threshold in package.json."""
    content = json.loads(package_path.read_text())
    if content.get("jsCoverageThreshold") != new_threshold:
        content["jsCoverageThreshold"] = new_threshold
        package_path.write_text(json.dumps(content, indent=2) + "\n")
        return True
    return False


def main(check_only: bool = False) -> int:
    """Run the e2e suite and judge its JS coverage against package.json.

    Args:
        check_only: Judge only; never rewrite or stage package.json. The
            commit hook and CI pass ``--check`` (issue #576), so the threshold
            rises only when someone runs this script by hand.
    """
    package_path = Path(__file__).parent.parent / "package.json"

    current_threshold = get_current_threshold(package_path)

    run = run_e2e_tests()
    # Always echo the suite output. The hook is marked `verbose: true` in
    # .pre-commit-config.yaml so this reaches the CI log even when the hook
    # passes -- that test count is the only proof the browser suite ran.
    print(run.output)

    if run.returncode != 0:
        print(f"E2E tests failed (npx playwright test exited {run.returncode})")
        return 1

    # A run that never started a test tells us nothing about coverage. Tolerate
    # it -- a skipped suite is not a regression -- but say so unmistakably.
    if not run.test_count:
        print(
            "JS coverage: WARNING - Playwright reported no tests running, so no "
            "coverage was collected and nothing was verified"
        )
        return 0

    print(f"E2E tests passed: {run.test_count} test(s) ran")

    actual_coverage = get_reported_coverage()

    if actual_coverage is None:
        # Tests ran, so instrumentation should have produced data. Missing data
        # here means the coverage pipeline is broken, not that work was skipped.
        print(
            f"JS coverage: no data collected despite {run.test_count} test(s) "
            "running -- coverage instrumentation is broken"
        )
        return 1

    # Hysteresis (issue #278): the e2e JS coverage metric jitters +/-1pt run
    # to run (which code paths the browser suite happens to exercise shifts
    # slightly). A plain high-water-mark gate on a jittery metric fails
    # routinely on no-op runs. So the ratchet tolerates a 1pt dip -- only a
    # genuine (2+pt) drop fails -- and never stores the measured value itself;
    # it stores (floor(measured) - 1pt), so a single lucky run can never set a
    # bar the very next average run can't clear. The floor matters for the same
    # reason it does in the pytest hook (issue #552): a measurement is
    # fractional, and rounding one up stores a bar the run itself missed.
    return apply_ratchet(
        label="JS coverage",
        current_threshold=current_threshold,
        actual_coverage=actual_coverage,
        in_ci=IS_CI or check_only,
        write_threshold=lambda threshold: update_threshold(package_path, threshold),
        threshold_file=package_path,
        hysteresis_points=1,
    )


if __name__ == "__main__":
    sys.exit(main(check_only="--check" in sys.argv[1:]))
