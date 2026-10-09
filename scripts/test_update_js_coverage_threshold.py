"""Unit tests for the JS e2e coverage hook's pass/fail decisions.

The hook used to swallow e2e failures: a non-zero `npx playwright test` was
reported as "no data collected" and the hook exited 0. These tests pin down
which outcomes fail the hook and which are tolerated.
"""

import json

import pytest
import update_js_coverage_threshold as hook

PASSING_OUTPUT = "Running 6 tests using 1 worker\n\n  6 passed (4.2s)\n"


def make_run(returncode: int = 0, output: str = PASSING_OUTPUT):
    return hook.PlaywrightRun(returncode=returncode, output=output)


def stub_hook(monkeypatch, *, run, coverage=None, threshold=45):
    monkeypatch.setattr(hook, "run_e2e_tests", lambda: run)
    monkeypatch.setattr(hook, "get_reported_coverage", lambda: coverage)
    monkeypatch.setattr(hook, "get_current_threshold", lambda _path: threshold)


def test_test_count_parses_playwright_header():
    assert make_run().test_count == 6
    assert make_run(output="Running 1 test using 1 worker\n").test_count == 1


def test_test_count_is_none_when_playwright_never_started():
    assert make_run(output="Error: no tests found\n").test_count is None


def test_failing_suite_fails_the_hook_and_prints_output(monkeypatch, capsys):
    failure = "Running 6 tests using 1 worker\n\n  1 failed\n  5 passed\n"
    stub_hook(monkeypatch, run=make_run(returncode=1, output=failure))

    assert hook.main() == 1

    printed = capsys.readouterr().out
    assert "1 failed" in printed  # the captured suite output, not swallowed
    assert "exited 1" in printed


def test_suite_that_never_ran_is_tolerated_but_loud(monkeypatch, capsys):
    stub_hook(monkeypatch, run=make_run(output="Error: no tests found\n"))

    assert hook.main() == 0

    printed = capsys.readouterr().out
    assert "WARNING" in printed
    assert "no tests running" in printed


def test_missing_coverage_after_a_real_run_fails_the_hook(monkeypatch, capsys):
    stub_hook(monkeypatch, run=make_run(), coverage=None)

    assert hook.main() == 1
    assert "instrumentation is broken" in capsys.readouterr().out


def test_coverage_drop_fails_the_hook(monkeypatch, capsys):
    stub_hook(monkeypatch, run=make_run(), coverage=30, threshold=45)

    assert hook.main() == 1
    assert "JS coverage dropped: 30% < 45%" in capsys.readouterr().out


def test_unchanged_coverage_passes(monkeypatch, capsys):
    stub_hook(monkeypatch, run=make_run(), coverage=45, threshold=45)

    assert hook.main() == 0

    printed = capsys.readouterr().out
    assert "6 test(s) ran" in printed
    assert "JS coverage unchanged at 45%" in printed


def test_coverage_increase_is_reported_without_writing_files_in_ci(monkeypatch, capsys):
    monkeypatch.setattr(hook, "IS_CI", True)
    stub_hook(monkeypatch, run=make_run(), coverage=60, threshold=45)

    assert hook.main() == 0
    assert "JS coverage increased: 45% -> 60%" in capsys.readouterr().out


def test_check_mode_reports_a_rise_without_writing_outside_ci(monkeypatch, capsys):
    monkeypatch.setattr(hook, "IS_CI", False)
    stub_hook(monkeypatch, run=make_run(), coverage=60, threshold=45)
    written = []
    monkeypatch.setattr(
        hook, "update_threshold", lambda *arguments: written.append(arguments)
    )

    assert hook.main(check_only=True) == 0
    assert written == []
    assert "JS coverage increased: 45% -> 60%" in capsys.readouterr().out


# --- Hysteresis: the ratchet tolerates +/-1pt measurement jitter ---
#
# The e2e JS coverage metric jitters +/-1pt run to run (which code paths the
# suite happens to exercise shifts slightly). A high-water-mark gate on a
# jittery metric produced routine spurious failures (issue #278): a run that
# measured one point under the threshold failed CI even though nothing
# regressed. These tests pin down the fix:
#   - a 1pt dip is tolerated (jitter passes)
#   - a 2+pt drop still fails (a real regression still fails)
#   - the auto-raise stores (measured - 1), never the measured value itself,
#     so a single lucky run can never set an unreachable bar
#   - the threshold never lowers


def test_one_point_dip_is_tolerated_as_jitter(monkeypatch, capsys):
    stub_hook(monkeypatch, run=make_run(), coverage=44, threshold=45)

    assert hook.main() == 0

    printed = capsys.readouterr().out
    assert "dropped" not in printed


def test_two_point_drop_still_fails_the_hook(monkeypatch, capsys):
    stub_hook(monkeypatch, run=make_run(), coverage=43, threshold=45)

    assert hook.main() == 1
    assert "JS coverage dropped: 43% < 45%" in capsys.readouterr().out


def test_ratchet_raises_to_measured_minus_one_not_the_measured_value(
    monkeypatch, capsys
):
    monkeypatch.setattr(hook, "IS_CI", False)
    recorded = {}
    monkeypatch.setattr(
        hook,
        "update_threshold",
        lambda path, value: recorded.setdefault("value", value) or True,
    )
    monkeypatch.setattr(hook.subprocess, "run", lambda *args, **kwargs: None)
    stub_hook(monkeypatch, run=make_run(), coverage=50, threshold=45)

    assert hook.main() == 0
    assert recorded["value"] == 49  # measured (50) - 1pt hysteresis, not 50
    assert "45% -> 49%" in capsys.readouterr().out


def test_ratchet_does_not_raise_when_measured_minus_one_would_not_exceed_current(
    monkeypatch, capsys
):
    """A run that's only 1pt above the current threshold can't clear the
    hysteresis band -- raising would set threshold == measured, recreating
    the unreachable-bar bug. The threshold must stay put."""
    monkeypatch.setattr(hook, "IS_CI", False)
    monkeypatch.setattr(
        hook,
        "update_threshold",
        lambda path, value: (_ for _ in ()).throw(
            AssertionError("update_threshold should not be called")
        ),
    )
    stub_hook(monkeypatch, run=make_run(), coverage=46, threshold=45)

    assert hook.main() == 0
    assert "45%" in capsys.readouterr().out


# --- Flooring: the stored bar is one the measuring run cleared (issue #552) ---


def test_coverage_comes_from_the_json_summary_counts(tmp_path):
    summary = tmp_path / "coverage-summary.json"
    summary.write_text(
        json.dumps(
            {"total": {"statements": {"covered": 199, "total": 200, "pct": 99.5}}}
        )
    )
    assert hook.coverage_from_summary(summary) == 99.5
    assert hook.coverage_from_summary(tmp_path / "absent.json") is None


def test_text_summary_fallback_is_read_as_the_lowest_figure_it_can_mean():
    printed = "Statements   : 99.00% ( 198/200 )\n"
    assert hook.coverage_from_summary_text(printed) == pytest.approx(98.995)
    assert hook.coverage_from_summary_text("Statements : 45% ( 94/208 )") == 44.5
    assert hook.coverage_from_summary_text("nothing here") is None


def test_a_fractional_measurement_stores_the_floor_minus_hysteresis(
    monkeypatch, capsys
):
    """98.995% measured stores 97, not 98 and never 99."""
    monkeypatch.setattr(hook, "IS_CI", False)
    recorded = {}
    monkeypatch.setattr(
        hook,
        "update_threshold",
        lambda path, value: recorded.setdefault("value", value) or True,
    )
    monkeypatch.setattr(hook.subprocess, "run", lambda *args, **kwargs: None)
    stub_hook(monkeypatch, run=make_run(), coverage=98.995, threshold=45)

    assert hook.main() == 0
    assert recorded["value"] == 97
    assert "45% -> 97%" in capsys.readouterr().out


def test_ratchet_never_lowers_the_threshold(monkeypatch, capsys):
    """Coverage below the threshold, even within the jitter band, must never
    write a new (lower) value to package.json."""
    monkeypatch.setattr(hook, "IS_CI", False)
    monkeypatch.setattr(
        hook,
        "update_threshold",
        lambda path, value: (_ for _ in ()).throw(
            AssertionError("update_threshold should not be called")
        ),
    )
    stub_hook(monkeypatch, run=make_run(), coverage=44, threshold=45)

    assert hook.main() == 0
