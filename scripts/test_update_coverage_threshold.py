"""Unit tests for the pytest coverage-threshold wrapper's measurement.

Two regressions are guarded here. Issue #121: the wrapper used to ignore
pytest's returncode entirely, so a failing test still exited 0 as long as
coverage held. Issue #552: it read the measurement off pytest-cov's printed
line, which is already rounded, so a 98.995% suite stored a threshold of 99
that no run could clear. Both use a fake subprocess.run, so no real pytest
run is needed.
"""

import json
import subprocess
import time
import tomllib
from pathlib import Path
from unittest.mock import patch

import coverage_ratchet
import pytest
import update_coverage_threshold


def _completed_process(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _fake_pytest(
    stdout: str, returncode: int = 0, percent_covered: float | None = None
):
    """A stand-in for the pytest subprocess, optionally writing a JSON report.

    The report lands wherever the command's ``--cov-report=json:`` flag points,
    exactly as coverage.py would write it.
    """

    def run(command, **kwargs):
        if percent_covered is not None:
            flag = next(
                argument
                for argument in command
                if argument.startswith("--cov-report=json:")
            )
            report_path = Path(flag.split(":", 1)[1])
            report_path.write_text(
                json.dumps({"totals": {"percent_covered": percent_covered}})
            )
        return _completed_process(returncode, stdout=stdout)

    return run


def test_the_measured_apps_and_the_gated_apps_are_the_same_list():
    """One denominator (issue #561).

    The gate applies to addopts' ``--cov`` flags; the ratchet reads its
    figure from the JSON report, which follows ``[tool.coverage.run]
    source``. When source omitted encounters and ruins, the ratchet measured
    a higher number than the gate judged and stored a threshold no gated run
    could clear.
    """
    config = tomllib.loads(update_coverage_threshold.PYPROJECT_PATH.read_text())
    measured = set(config["tool"]["coverage"]["run"]["source"])
    gated = set(
        update_coverage_threshold.coverage_apps(
            update_coverage_threshold.PYPROJECT_PATH
        )
    )

    assert measured == gated


def test_the_measuring_run_does_not_apply_the_stored_threshold():
    """Measure first, judge after (issue #558).

    addopts carries the stored ``--cov-fail-under``. Applying it to the
    measuring run made pytest exit non-zero whenever coverage had drifted
    below it, so the ratchet returned before it could lower the number and
    every local commit was blocked by a threshold nothing could clear.
    """
    commands = []

    def record(command, **kwargs):
        commands.append(command)
        return _completed_process(0, stdout="TOTAL 100 4 96%\n")

    with patch.object(update_coverage_threshold.subprocess, "run", record):
        update_coverage_threshold.get_actual_coverage()

    assert "--cov-fail-under=0" in commands[0]


def test_get_actual_coverage_propagates_nonzero_returncode():
    with patch.object(
        update_coverage_threshold.subprocess,
        "run",
        _fake_pytest("FAILED tests/test_x.py::test_y\nTOTAL 100 5 95%\n", returncode=1),
    ):
        returncode, coverage = update_coverage_threshold.get_actual_coverage()
    assert returncode == 1
    # Coverage is still parsed when present, but callers must check
    # returncode first rather than trusting a parsed percentage.
    # A whole-percent "95%" row guarantees only 94.5%.
    assert coverage == 94.5


def test_get_actual_coverage_returns_zero_on_success():
    with patch.object(
        update_coverage_threshold.subprocess, "run", _fake_pytest("TOTAL 100 4 96%\n")
    ):
        returncode, coverage = update_coverage_threshold.get_actual_coverage()
    assert returncode == 0
    assert coverage == 95.5


def test_coverage_comes_from_the_json_report_not_the_rounded_line():
    """The printed line says 99.00%; the report says 98.995%, and it wins."""
    with patch.object(
        update_coverage_threshold.subprocess,
        "run",
        _fake_pytest("Total coverage: 99.00%\n", percent_covered=98.995),
    ):
        _, coverage = update_coverage_threshold.get_actual_coverage()
    assert coverage == 98.995


def test_printed_total_falls_back_to_the_lowest_figure_it_can_mean():
    """With no JSON report, "99.00%" is read as 98.995% -- never as 99%."""
    with patch.object(
        update_coverage_threshold.subprocess,
        "run",
        _fake_pytest("Total coverage: 99.00%\n"),
    ):
        _, coverage = update_coverage_threshold.get_actual_coverage()
    assert coverage == pytest.approx(98.995)
    assert update_coverage_threshold.lower_bound_of_printed("99") == pytest.approx(98.5)
    assert update_coverage_threshold.lower_bound_of_printed("99.4") == pytest.approx(
        99.35
    )


def test_coverage_is_none_when_the_run_reported_no_total():
    with patch.object(
        update_coverage_threshold.subprocess,
        "run",
        _fake_pytest("no tests ran\n"),
    ):
        _, coverage = update_coverage_threshold.get_actual_coverage()
    assert coverage is None


def test_main_fails_when_pytest_returncode_nonzero_even_if_coverage_holds():
    with (
        patch.object(
            update_coverage_threshold, "get_current_threshold", return_value=90
        ),
        patch.object(
            update_coverage_threshold,
            "get_actual_coverage",
            return_value=(1, 95),
        ),
    ):
        result = update_coverage_threshold.main()

    assert result == 1


def test_main_succeeds_when_pytest_returncode_zero_and_coverage_holds():
    with (
        patch.object(
            update_coverage_threshold, "get_current_threshold", return_value=90
        ),
        patch.object(
            update_coverage_threshold, "get_actual_coverage", return_value=(0, 90)
        ),
    ):
        result = update_coverage_threshold.main()

    assert result == 0


def _main_with_measurement(monkeypatch, *, measured, current):
    """Run main() against a stubbed measurement, returning what it stored."""
    stored = []
    # Outside CI, where the ratchet writes. IS_CI is read from the environment
    # at import, so under CI=true these tests saw no write at all.
    monkeypatch.setattr(update_coverage_threshold, "IS_CI", False)
    monkeypatch.setattr(
        update_coverage_threshold, "get_current_threshold", lambda _path: current
    )
    monkeypatch.setattr(
        update_coverage_threshold, "get_actual_coverage", lambda: (0, measured)
    )
    monkeypatch.setattr(
        update_coverage_threshold,
        "update_threshold",
        lambda _path, value: bool(stored.append(value)) or True,
    )
    monkeypatch.setattr(coverage_ratchet.subprocess, "run", lambda command, check: None)
    assert update_coverage_threshold.main() == 0
    return stored


def test_main_stores_the_floored_measurement_never_a_rounded_one(monkeypatch):
    """98.995% never stores 99: the run that stores a bar has to clear it.

    The floor is 98 (#552), less the ratchet's point of slack (#561), so 97
    is stored. What matters here is that a rounded 99 is never written.
    """
    assert _main_with_measurement(monkeypatch, measured=98.995, current=90) == [97]


def test_main_leaves_the_threshold_alone_when_the_floor_matches_it(monkeypatch):
    assert _main_with_measurement(monkeypatch, measured=98.995, current=98) == []


def test_main_stores_the_whole_percent_a_higher_run_reached(monkeypatch):
    """A rise has to beat the stored bar by more than the slack.

    99.4% floors to 99, less the point of slack is 98 — exactly what a
    stored 98 already demands, so nothing is written. From a stored 90 the
    same measurement raises the bar to 98.
    """
    assert _main_with_measurement(monkeypatch, measured=99.4, current=98) == []
    assert _main_with_measurement(monkeypatch, measured=99.4, current=90) == [98]


def test_a_luckier_run_keeps_a_point_of_slack(monkeypatch):
    """Issue #561: runs of this suite differ by more than a rounding error.

    The WeasyPrint export tests run only where their library is on the path,
    so one machine measured 99.0142% and a plain run measured 98.93%. Storing
    99 from the luckier run blocked every commit afterwards, so the stored
    bar is the floor less one point, as the JS ratchet has always done.
    """
    assert _main_with_measurement(monkeypatch, measured=99.0142, current=90) == [98]
    assert _main_with_measurement(monkeypatch, measured=99.0142, current=98) == []


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("Total coverage: 92.86%", 92.855),
        ("Total coverage: 93%", 92.5),
        ("Total coverage: 93.%", 92.5),
        ("TOTAL      33510   1000    97.5%", 97.45),
        ("TOTAL 10 2 98%", 97.5),
        ("Total coverage: 92.86", None),
        ("Total coverage: %", None),
        ("TOTAL 10 98%", None),
    ],
)
def test_the_printed_percentage_is_read_as_it_always_was(output, expected):
    reading = update_coverage_threshold.coverage_from_output(output)
    if expected is None:
        assert reading is None
    else:
        assert reading == pytest.approx(expected)


@pytest.mark.parametrize(
    "output",
    [
        "Total coverage: " + "1" * 60_000,
        "TOTAL 1 1 " + "1" * 66_000,
        "Total coverage: " + "1" * 74_000 + ".",
        "TOTAL 1 1 " + "1" * 96_000 + ".",
    ],
    ids=["total line", "table row", "total line, point", "table row, point"],
)
def test_the_percentage_patterns_stay_fast_on_a_long_run_of_digits(output):
    # The old patterns split a run of digits between "\d+" and "\d*": 10 to 26 s
    # on each of these (measured on one machine, 2026-10-18), so code that
    # backtracks again fails here.
    started = time.perf_counter()
    assert update_coverage_threshold.coverage_from_output(output) is None
    assert time.perf_counter() - started < 1.0
