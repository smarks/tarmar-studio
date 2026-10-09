"""Tests for the shared coverage ratchet and the pytest hook's app list."""

from pathlib import Path

import coverage_ratchet
import update_coverage_threshold


def run_ratchet(monkeypatch, *, current, actual, in_ci=False, hysteresis=0):
    written = []
    staged = []
    monkeypatch.setattr(
        coverage_ratchet.subprocess,
        "run",
        lambda command, check: staged.append(command),
    )
    exit_code = coverage_ratchet.apply_ratchet(
        label="coverage",
        current_threshold=current,
        actual_coverage=actual,
        in_ci=in_ci,
        write_threshold=written.append,
        threshold_file=Path("pyproject.toml"),
        hysteresis_points=hysteresis,
    )
    return exit_code, written, staged


def test_a_drop_fails(monkeypatch, capsys):
    assert run_ratchet(monkeypatch, current=90, actual=89)[0] == 1
    assert "Coverage dropped: 89% < 90%" in capsys.readouterr().out


def test_a_rise_writes_and_stages_the_new_threshold(monkeypatch, capsys):
    exit_code, written, staged = run_ratchet(monkeypatch, current=90, actual=92)
    assert (exit_code, written) == (0, [92])
    assert staged == [["git", "add", "pyproject.toml"]]
    assert "Updated coverage threshold: 90% -> 92%" in capsys.readouterr().out


def test_a_rise_in_ci_is_only_reported(monkeypatch, capsys):
    exit_code, written, staged = run_ratchet(
        monkeypatch, current=90, actual=92, in_ci=True
    )
    assert (exit_code, written, staged) == (0, [], [])
    assert "Coverage increased: 90% -> 92%" in capsys.readouterr().out


def test_hysteresis_tolerates_a_small_dip_and_banks_one_below(monkeypatch):
    assert run_ratchet(monkeypatch, current=45, actual=44, hysteresis=1)[0] == 0
    assert run_ratchet(monkeypatch, current=45, actual=43, hysteresis=1)[0] == 1
    assert run_ratchet(monkeypatch, current=45, actual=50, hysteresis=1)[1] == [49]


# --- Flooring: a stored threshold must be one the measuring run cleared ---
#
# Issue #552: the pytest hook took its figure from pytest-cov's printed
# "Total coverage: 99.00%", which is 98.995% rounded for display, and stored
# 99. Every later run then failed a bar nothing could clear, and the next
# commit's hook stored it again. The ratchet now floors.


def test_a_fractional_rise_stores_the_whole_percent_below_it(monkeypatch, capsys):
    exit_code, written, staged = run_ratchet(monkeypatch, current=90, actual=98.995)
    assert (exit_code, written) == (0, [98])
    assert staged == [["git", "add", "pyproject.toml"]]
    printed = capsys.readouterr().out
    assert "90% -> 98%" in printed
    assert "measured 98.995%" in printed


def test_a_measurement_that_floors_to_the_threshold_changes_nothing(
    monkeypatch, capsys
):
    exit_code, written, staged = run_ratchet(monkeypatch, current=98, actual=98.995)
    assert (exit_code, written, staged) == (0, [], [])
    assert "unchanged at 98.995%" in capsys.readouterr().out


def test_a_measurement_a_whole_point_clear_raises_the_threshold(monkeypatch):
    assert run_ratchet(monkeypatch, current=98, actual=99.4)[1] == [99]


def test_flooring_and_hysteresis_compose(monkeypatch):
    assert run_ratchet(monkeypatch, current=90, actual=98.995, hysteresis=1)[1] == [97]


def test_a_fractional_dip_below_the_threshold_still_fails(monkeypatch, capsys):
    assert run_ratchet(monkeypatch, current=99, actual=98.995)[0] == 1
    assert "Coverage dropped: 98.995% < 99%" in capsys.readouterr().out


def test_running_in_ci_reads_the_environment(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert coverage_ratchet.running_in_ci() is False
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert coverage_ratchet.running_in_ci() is True


def test_the_pytest_hook_measures_every_app_pyproject_names(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.pytest.ini_options]\naddopts = "-q --cov=alpha --cov=beta -x"\n'
    )
    assert update_coverage_threshold.coverage_apps(pyproject) == ["alpha", "beta"]


def test_the_real_pyproject_lists_the_apps():
    apps = update_coverage_threshold.coverage_apps(
        update_coverage_threshold.PYPROJECT_PATH
    )
    assert {"accounts", "battle", "gm", "ruins"} <= set(apps)
