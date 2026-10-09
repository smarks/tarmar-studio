"""The coverage ratchet both pre-commit coverage hooks apply (issue #337).

``update_coverage_threshold.py`` (pytest, threshold in pyproject.toml) and
``update_js_coverage_threshold.py`` (Playwright, threshold in package.json)
measure differently but judge the result the same way: fail on a drop, report
a rise in CI, and otherwise raise the stored threshold and stage the file. The
JS metric jitters, so it passes a one-point hysteresis; pytest passes none.

Measurements arrive as fractional percentages and the stored threshold is
always floored (issue #552), so storing N means the run that stored it really
did measure at least N. Rounding a measurement up -- which is what reading
pytest-cov's printed "Total coverage: 99.00%" for a 98.995% suite amounted to
-- stores a bar no run can clear, and the hook restores it on every commit.
"""

import math
import os
import subprocess
from collections.abc import Callable
from pathlib import Path


def format_percent(value: float) -> str:
    """A percentage as it should read in a message, without trailing zeroes."""
    return f"{value:g}"


def running_in_ci() -> bool:
    """Whether this process runs under CI, where files must not be rewritten."""
    return os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"


def apply_ratchet(
    *,
    label: str,
    current_threshold: int,
    actual_coverage: float,
    in_ci: bool,
    write_threshold: Callable[[int], object],
    threshold_file: Path,
    hysteresis_points: int = 0,
) -> int:
    """Judge measured coverage against the stored threshold and ratchet it up.

    A dip of up to ``hysteresis_points`` is tolerated. A raised threshold is
    the measurement floored to a whole percent, less ``hysteresis_points``, so
    the run that stored it cleared it and one lucky run cannot set a bar the
    next ordinary run fails. Flooring is what keeps a 98.995% suite from
    storing 99 (issue #552).

    Args:
        label: The metric's name in messages, e.g. ``"coverage"``.
        current_threshold: The stored threshold, in whole percent.
        actual_coverage: The measured coverage, in percent, not rounded up.
        in_ci: Report a rise without writing or staging anything.
        write_threshold: Stores a new threshold in ``threshold_file``.
        threshold_file: The file holding the threshold, staged after a raise.
        hysteresis_points: Tolerated jitter, in percentage points.

    Returns:
        The hook's exit code: 1 when coverage dropped, else 0.
    """
    sentence_label = label[:1].upper() + label[1:]
    measured = format_percent(actual_coverage)

    if actual_coverage < current_threshold - hysteresis_points:
        print(f"{sentence_label} dropped: {measured}% < {current_threshold}%")
        return 1

    raised_threshold = math.floor(actual_coverage) - hysteresis_points

    if raised_threshold > current_threshold:
        if in_ci:
            print(f"{sentence_label} increased: {current_threshold}% -> {measured}%")
            return 0
        write_threshold(raised_threshold)
        if hysteresis_points:
            retained = (
                f" (measured {measured}%, {hysteresis_points}pt hysteresis retained)"
            )
        elif math.floor(actual_coverage) != actual_coverage:
            retained = f" (measured {measured}%, floored)"
        else:
            retained = ""
        print(
            f"Updated {label} threshold: {current_threshold}% -> "
            f"{raised_threshold}%{retained}"
        )
        subprocess.run(["git", "add", str(threshold_file)], check=True)
        return 0

    if math.floor(actual_coverage) == current_threshold:
        print(f"{sentence_label} unchanged at {measured}%")
    else:
        band = (
            f" beyond the {hysteresis_points}pt hysteresis band"
            if hysteresis_points
            else ""
        )
        print(
            f"{sentence_label} {measured}% does not clear threshold "
            f"{current_threshold}% by a whole point{band}; threshold unchanged"
        )
    return 0
