"""Unit tests for the local gate's steps and its record of a passing commit.

Issue #576: nothing checks a commit between merge and production, so ``./run
gate`` runs the suites and records the commit they passed on. ``deploy.sh``
reads that record, which is why these tests care most about what a record can
and cannot say: it names one exact commit, it is not written for a dirty tree,
and it reaches the remote a deploy host fetches from.

The git-backed tests build a real repository with a bare remote in a temporary
directory, so they exercise the git plumbing the gate actually runs rather than
a stand-in for it.
"""

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import gate
import pytest
import yaml

PORTS = {"e2e_port": "8399", "default_canon_e2e_port": "8398"}
# What gate_steps takes: the ports and the canon the first mode serves.
STEP_CHOICES = {**PORTS, "canon_name": gate.TARMAR_CANON_NAME}


@pytest.fixture(autouse=True)
def caller_has_no_canon_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test with no ``TARMAR_CANON_DIR`` in the environment.

    The gate passes the caller's variable through to its first mode, and the
    suite's conftest deliberately preserves it for test workers, so a test that
    reads it implicitly would pass or fail with the shell the suite was started
    from (the gate's own second pass runs this file with it set). A test that
    needs the variable sets it itself.
    """
    monkeypatch.delenv(gate.CANON_DIR_ENV, raising=False)


@pytest.fixture(autouse=True)
def no_port_is_listening(monkeypatch: pytest.MonkeyPatch) -> set[int]:
    """Start every test with every port free, whatever this machine is running.

    The gate refuses a port something listens on (issue #966), so a test that
    asked the real network would pass or fail with whichever other gate or
    dev server happened to be up. A test that needs a port taken adds it to
    the returned set.
    """
    listening: set[int] = set()
    monkeypatch.setattr(gate, "port_listening", lambda port: port in listening)
    return listening


def git(repository: Path, *arguments: str) -> str:
    """Run git in ``repository`` and return its stripped stdout."""
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def make_repository_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Build a working repository with one commit and a bare origin.

    Args:
        tmp_path: The temporary directory to build in.

    Returns:
        The working checkout and the bare remote.
    """
    remote = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote)],
        capture_output=True,
        check=True,
    )
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    git(checkout, "init", "--initial-branch=main")
    git(checkout, "config", "user.email", "gate@example.com")
    git(checkout, "config", "user.name", "Gate Test")
    git(checkout, "remote", "add", "origin", str(remote))
    (checkout / "tracked.txt").write_text("first\n", encoding="utf-8")
    git(checkout, "add", "tracked.txt")
    git(checkout, "commit", "-m", "first")
    git(checkout, "push", "--quiet", "origin", "main")
    return checkout, remote


def note_on(repository: Path, commit_sha: str) -> str | None:
    """The gate note a repository holds for a commit, if any."""
    return gate.read_gate_record(repository, commit_sha)


def test_the_steps_run_cheapest_first_then_the_suites_twice() -> None:
    steps = gate.gate_steps(**STEP_CHOICES)
    assert [step.name for step in steps] == [
        "ruff",
        "ruff format",
        "pyright",
        "makemigrations",
        "build_guides",
        "pytest (Tarmar canon)",
        "jest (Tarmar canon)",
        "playwright (Tarmar canon)",
        "pytest (default canon)",
        "jest (default canon)",
        "playwright (default canon)",
    ]
    commands = {step.name: step.command for step in steps}
    assert commands["pytest (Tarmar canon)"] == ["uv", "run", "pytest", "-q"]
    assert commands["jest (Tarmar canon)"] == ["npx", "jest"]
    playwright = commands["playwright (Tarmar canon)"]
    assert "scripts/update_js_coverage_threshold.py" in playwright
    assert "--check" in playwright
    for name in ("pytest", "jest", "playwright"):
        assert commands[f"{name} (default canon)"] == commands[f"{name} (Tarmar canon)"]


def test_each_pass_names_the_canon_it_serves_in_its_steps_and_its_port() -> None:
    # Issue #10: the opening line called the Tarmar pass's port the default
    # canon's, while the steps on it carried no canon at all. Both now come
    # from the label each step of a pass carries.
    steps = gate.gate_steps(**STEP_CHOICES)
    browser_steps = [step for step in steps if gate.E2E_PORT_ENV in step.environment]
    assert [(step.name, step.environment) for step in browser_steps] == [
        ("playwright (Tarmar canon)", {"E2E_PORT": "8399"}),
        ("playwright (default canon)", {"E2E_PORT": "8398"}),
    ]
    assert gate.e2e_ports_header(steps) == (
        "e2e ports 8399 for the Tarmar canon, 8398 for the default canon"
    )
    for step in steps:
        if step.canon_label is not None:
            assert step.name.endswith(f" ({step.canon_label})")


def test_a_first_mode_on_the_default_canon_is_named_for_it_too() -> None:
    # Without TARMAR_CANON_DIR both modes serve the default canon (#921), and
    # the gate says so rather than calling the first one anything else.
    steps = gate.gate_steps(**PORTS, canon_name=gate.DEFAULT_CANON_NAME)
    assert gate.e2e_ports_header(steps) == (
        "e2e ports 8399 for the default canon, 8398 for the default canon"
    )
    assert [step.name for step in steps].count("playwright (default canon)") == 2


def test_a_browser_step_that_names_no_canon_is_refused() -> None:
    # The opening line says which canon each port serves; a port with no
    # canon would bring back "for the None" (issue #10).
    with pytest.raises(ValueError, match="names no canon"):
        gate.GateStep("playwright", ["true"], {gate.E2E_PORT_ENV: "8399"})


def test_a_run_with_no_browser_step_names_no_ports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert gate.e2e_ports_header([passing_step()]) == ""
    checkout, _ = make_repository_with_remote(tmp_path)
    monkeypatch.setattr(gate, "gate_steps", steps_stub(passing_step()))

    status = gate.run_gate(
        repository_root=checkout, e2e_port="8399", push=False, deploy=False
    )

    assert status == 0
    commit_sha = gate.head_commit_sha(checkout)
    assert f"[gate] gating {commit_sha} with 1 steps\n" in capsys.readouterr().out


def test_only_the_suite_steps_of_the_second_mode_set_a_canon_directory() -> None:
    canon_by_step = {
        step.name: step.canon_dir for step in gate.gate_steps(**STEP_CHOICES)
    }
    default_canon_steps = {
        name for name, canon_dir in canon_by_step.items() if canon_dir is not None
    }
    assert default_canon_steps == {
        "pytest (default canon)",
        "jest (default canon)",
        "playwright (default canon)",
    }
    for name in default_canon_steps:
        assert canon_by_step[name] == gate.DEFAULT_CANON_DIR


def test_the_default_canon_path_is_absolute() -> None:
    assert gate.DEFAULT_CANON_DIR.is_absolute()


def test_each_mode_gets_its_own_browser_port_and_never_the_default() -> None:
    steps = {step.name: step for step in gate.gate_steps(**STEP_CHOICES)}
    assert steps["playwright (Tarmar canon)"].environment == {"E2E_PORT": "8399"}
    assert steps["playwright (default canon)"].environment == {"E2E_PORT": "8398"}
    assert gate.DEFAULT_E2E_PORT != "8000"  # 8000 is the dev server's
    assert gate.DEFAULT_CANON_E2E_PORT != gate.DEFAULT_E2E_PORT


@pytest.mark.parametrize(
    ("first_port", "second_port"),
    [("8399", "8399"), ("8390", "9390"), ("9390", "8390")],
)
def test_ports_that_collide_with_each_other_or_a_fake_ollama_are_refused(
    first_port: str, second_port: str
) -> None:
    with pytest.raises(gate.GateError, match="collide"):
        gate.gate_steps(
            e2e_port=first_port,
            default_canon_e2e_port=second_port,
            canon_name=gate.TARMAR_CANON_NAME,
        )


def test_the_migration_check_runs_with_debug_so_it_needs_no_production_env() -> None:
    steps = {step.name: step for step in gate.gate_steps(**STEP_CHOICES)}
    assert steps["makemigrations"].environment == {"DEBUG": "true"}


def test_the_note_names_the_commit_the_port_and_every_step() -> None:
    body = gate.gate_note_body(
        commit_sha="0123456789abcdef0123456789abcdef01234567",
        recorded_at=datetime(2026, 9, 21, 17, 4, 5, tzinfo=UTC),
        host="quantumfoam",
        canon_name="tarmar",
        canon_dir=Path("/srv/canon"),
        canon_commit="fedcba9876543210fedcba9876543210fedcba98",
        e2e_port="8390",
        default_canon_e2e_port="8391",
        step_names=["ruff", "pytest"],
    )
    assert body.splitlines() == [
        "gate: pass",
        "commit: 0123456789abcdef0123456789abcdef01234567",
        "recorded: 2026-09-21T17:04:05Z",
        "host: quantumfoam",
        "canon: tarmar",
        "canon-dir: /srv/canon",
        "canon-commit: fedcba9876543210fedcba9876543210fedcba98",
        "e2e-port: 8390",
        "default-canon-e2e-port: 8391",
        "steps: ruff, pytest",
    ]


def test_a_record_lands_on_the_remote_against_that_one_commit(tmp_path: Path) -> None:
    checkout, remote = make_repository_with_remote(tmp_path)
    commit_sha = gate.head_commit_sha(checkout)

    gate.record_passing_commit(checkout, commit_sha, "gate: pass\n")

    assert note_on(checkout, commit_sha) == "gate: pass"
    # The remote is what deploy.sh fetches from, so the record has to be there.
    assert git(remote, "rev-parse", "--verify", gate.GATE_NOTES_REF)
    reader = tmp_path / "reader"
    subprocess.run(
        ["git", "clone", "--quiet", str(remote), str(reader)],
        capture_output=True,
        check=True,
    )
    assert gate.fetch_gate_notes(reader) is True
    assert note_on(reader, commit_sha) == "gate: pass"


def test_a_record_does_not_follow_the_next_commit(tmp_path: Path) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    recorded = gate.head_commit_sha(checkout)
    gate.record_passing_commit(checkout, recorded, "gate: pass\n", push=False)

    (checkout / "tracked.txt").write_text("second\n", encoding="utf-8")
    git(checkout, "commit", "--all", "-m", "second")

    assert note_on(checkout, recorded) == "gate: pass"
    assert note_on(checkout, gate.head_commit_sha(checkout)) is None


def test_a_local_only_record_never_reaches_the_remote(tmp_path: Path) -> None:
    checkout, remote = make_repository_with_remote(tmp_path)
    commit_sha = gate.head_commit_sha(checkout)

    gate.record_passing_commit(checkout, commit_sha, "gate: pass\n", push=False)

    assert note_on(checkout, commit_sha) == "gate: pass"
    listed = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", "--verify", gate.GATE_NOTES_REF],
        capture_output=True,
        text=True,
    )
    assert listed.returncode != 0


def test_a_push_rejected_by_another_run_keeps_both_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout, remote = make_repository_with_remote(tmp_path)
    (checkout / "tracked.txt").write_text("second\n", encoding="utf-8")
    git(checkout, "commit", "--all", "-m", "second")
    git(checkout, "push", "--quiet", "origin", "main")
    ours = gate.head_commit_sha(checkout)
    theirs = git(checkout, "rev-parse", "HEAD~1")

    # Another machine's gate run records the older commit and pushes it while
    # this run is between its own fetch and its own push.
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", "--quiet", str(remote), str(other)],
        capture_output=True,
        check=True,
    )
    git(other, "config", "user.email", "other@example.com")
    git(other, "config", "user.name", "Other Gate")
    gate.write_gate_note(other, theirs, "gate: pass (theirs)")
    assert gate.push_gate_notes(other).returncode == 0

    real_fetch = gate.fetch_gate_notes
    fetches: list[int] = []

    def fetch_that_misses_the_first_time(
        repository: Path, remote_name: str = "origin"
    ) -> bool:
        """Stand in for a fetch that ran before the other machine pushed."""
        fetches.append(1)
        if len(fetches) == 1:
            return False
        return real_fetch(repository, remote_name)

    monkeypatch.setattr(gate, "fetch_gate_notes", fetch_that_misses_the_first_time)

    gate.record_passing_commit(checkout, ours, "gate: pass (ours)")

    assert len(fetches) == 2  # the first push was rejected and retried
    reader = tmp_path / "reader"
    subprocess.run(
        ["git", "clone", "--quiet", str(remote), str(reader)],
        capture_output=True,
        check=True,
    )
    gate.fetch_gate_notes(reader)
    assert note_on(reader, ours) == "gate: pass (ours)"
    assert note_on(reader, theirs) == "gate: pass (theirs)"


def test_a_push_that_keeps_failing_is_reported_as_unrecorded(tmp_path: Path) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    git(checkout, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    commit_sha = gate.head_commit_sha(checkout)

    with pytest.raises(gate.GateError) as failure:
        gate.record_passing_commit(checkout, commit_sha, "gate: pass\n")

    assert commit_sha in str(failure.value)
    assert "will not see it" in str(failure.value)


def passing_step(name: str = "stub") -> gate.GateStep:
    """A step that does nothing and succeeds."""
    return gate.GateStep(name, ["bash", "-c", "true"])


def steps_stub(*steps: gate.GateStep) -> Callable[..., list[gate.GateStep]]:
    """Stand in for gate.gate_steps: ``steps``, whatever ports or canon it is
    asked for."""
    return lambda **_choices: list(steps)


def test_a_dirty_tree_is_refused_before_a_single_step_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    (checkout / "tracked.txt").write_text("edited\n", encoding="utf-8")
    ran: list[str] = []

    def record_that_it_ran(step: gate.GateStep, **kwargs: object) -> gate.StepResult:
        """Stand in for run_step, noting any step the gate tries to run."""
        ran.append(step.name)
        return gate.StepResult(step=step, returncode=0, seconds=0.0)

    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(passing_step()),
    )
    monkeypatch.setattr(gate, "run_step", record_that_it_ran)

    status = gate.run_gate(
        repository_root=checkout, e2e_port="8399", push=False, deploy=False
    )

    assert status == 1
    assert ran == []
    assert "tracked.txt" in capsys.readouterr().out
    assert note_on(checkout, gate.head_commit_sha(checkout)) is None


def test_every_step_passing_records_the_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    commit_sha = gate.head_commit_sha(checkout)
    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(passing_step("one"), passing_step("two")),
    )

    status = gate.run_gate(
        repository_root=checkout, e2e_port="8399", push=False, deploy=False
    )

    assert status == 0
    record = note_on(checkout, commit_sha)
    assert record is not None
    assert f"commit: {commit_sha}" in record
    assert "e2e-port: 8399" in record


def test_the_first_failing_step_stops_the_run_and_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(
            gate.GateStep("failing", ["bash", "-c", "exit 3"]),
            gate.GateStep("later", ["bash", "-c", "touch ran-later"]),
        ),
    )

    status = gate.run_gate(
        repository_root=checkout, e2e_port="8399", push=False, deploy=False
    )

    assert status == 3
    assert not (checkout / "ran-later").exists()
    assert note_on(checkout, gate.head_commit_sha(checkout)) is None


def test_a_failing_step_s_report_repeats_the_end_of_its_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Issue #36: the report names what failed, not only the exit code.

    The stub fails as makemigrations did on an unknown canon block: more
    output than the report keeps, and the refusal itself last, on stderr.
    """
    checkout, _ = make_repository_with_remote(tmp_path)
    commit_sha = gate.head_commit_sha(checkout)
    early_lines = gate.FAILED_STEP_TAIL_LINES
    refusal = "magic-rules.json: shieldwall is not a field this file takes"
    script = (
        f'for number in $(seq 1 {early_lines}); do echo "progress $number"; done; '
        f'echo "{refusal}" >&2; exit 1'
    )
    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(gate.GateStep("makemigrations", ["bash", "-c", script])),
    )

    status = gate.run_gate(
        repository_root=checkout, e2e_port="8399", push=False, deploy=False
    )

    assert status == 1
    printed = capsys.readouterr().out
    # The whole output still goes to the log as the step runs ...
    assert "progress 1\n" in printed
    # ... and the report after the exit code repeats its end: the refusal and
    # the lines before it, as many as the report keeps, and no more.
    assert "[gate] makemigrations failed (exit 1) after " in printed
    summary_end = f"; nothing recorded for {commit_sha}. The end of its output:\n"
    assert summary_end in printed
    report = printed.split(summary_end, 1)[1].splitlines()
    assert report == [
        *(f"  progress {number}" for number in range(2, early_lines + 1)),
        f"  {refusal}",
    ]
    assert note_on(checkout, commit_sha) is None


def test_a_failing_step_that_printed_nothing_says_so(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(gate.GateStep("silent", ["bash", "-c", "exit 2"])),
    )

    status = gate.run_gate(
        repository_root=checkout, e2e_port="8399", push=False, deploy=False
    )

    assert status == 2
    assert capsys.readouterr().out.rstrip().endswith("; it printed nothing")


def test_a_python_step_s_output_arrives_in_the_order_it_was_written(
    tmp_path: Path,
) -> None:
    # Through a pipe Python block-buffers stdout but not stderr, so without
    # the gate's PYTHONUNBUFFERED the stderr line would come out first.
    environment = {
        name: value for name, value in os.environ.items() if name != "PYTHONUNBUFFERED"
    }
    script = "import sys; print('first'); print('second', file=sys.stderr)"
    step = gate.GateStep("python", [sys.executable, "-c", script])

    result = gate.run_step(step, repository_root=tmp_path, environment=environment)

    assert result.output_tail == ("first", "second")


def test_an_interrupted_step_s_remaining_output_is_still_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ctrl-C reaches the step too; what it prints while it stops must still be
    # read and shown, not left in a pipe nobody reads.
    echoed: list[str] = []

    def interrupted_at_the_first_line(line: str, **kwargs: object) -> None:
        """Stand in for print; Ctrl-C arrives as the first line is shown."""
        echoed.append(line)
        if len(echoed) == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(gate, "print", interrupted_at_the_first_line, raising=False)
    step = gate.GateStep(
        "interrupted", ["bash", "-c", "echo running; echo stopping; echo stopped"]
    )

    with pytest.raises(KeyboardInterrupt):
        gate.run_step(step, repository_root=tmp_path, environment=dict(os.environ))

    assert echoed == ["running", "stopping", "stopped"]


def test_a_background_process_holding_the_output_does_not_hold_the_gate(
    tmp_path: Path,
) -> None:
    # The step exits, but a process it left running shares its output and
    # would keep the pipe open for thirty seconds.
    step = gate.GateStep(
        "leaves a sleeper",
        ["bash", "-c", "echo started; sleep 30 & echo $!; exit 0"],
    )
    started = time.monotonic()

    result = gate.run_step(step, repository_root=tmp_path, environment=dict(os.environ))

    elapsed = time.monotonic() - started
    started_line, sleeper = result.output_tail
    os.kill(int(sleeper), signal.SIGTERM)
    assert started_line == "started"
    assert result.passed
    # Well short of the sleeper's thirty seconds: the grace, a check
    # interval, and room for a loaded machine.
    assert elapsed < gate.STRAY_OUTPUT_GRACE_SECONDS + gate.STEP_EXIT_CHECK_SECONDS + 5


def test_a_background_process_that_keeps_printing_is_cut_off_at_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A leftover that prints more often than the grace never lets the pipe go
    # quiet; the cap, counted from the step's exit, stops the reading anyway.
    monkeypatch.setattr(gate, "STRAY_OUTPUT_CAP_SECONDS", 1.0)
    pid_file = tmp_path / "leftover.pid"
    script = (
        "echo started; (while true; do echo tick; sleep 0.2; done) & "
        f"echo $! > {pid_file}; exit 0"
    )
    step = gate.GateStep("leaves a chatty loop", ["bash", "-c", script])
    started = time.monotonic()

    result = gate.run_step(step, repository_root=tmp_path, environment=dict(os.environ))

    elapsed = time.monotonic() - started
    os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGTERM)
    assert result.passed
    assert result.output_tail[0] == "started"
    assert "tick" in result.output_tail
    # The loop would print until killed; the cap, a check interval and room
    # for a loaded machine bound the wait instead.
    assert elapsed < 1.0 + gate.STEP_EXIT_CHECK_SECONDS + 5


def test_a_tree_changed_by_the_suites_is_not_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    commit_sha = gate.head_commit_sha(checkout)
    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(
            gate.GateStep(
                "rewrites the tree", ["bash", "-c", "echo edited > tracked.txt"]
            )
        ),
    )

    status = gate.run_gate(
        repository_root=checkout, e2e_port="8399", push=False, deploy=False
    )

    assert status == 1
    assert "tracked.txt" in capsys.readouterr().out
    assert note_on(checkout, commit_sha) is None


def test_a_commit_made_while_the_suites_ran_is_not_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    started_at = gate.head_commit_sha(checkout)
    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(
            gate.GateStep(
                "commits underneath",
                [
                    "bash",
                    "-c",
                    "echo second > tracked.txt && git commit --all -m second",
                ],
            )
        ),
    )

    status = gate.run_gate(
        repository_root=checkout, e2e_port="8399", push=False, deploy=False
    )

    assert status == 1
    assert "HEAD moved" in capsys.readouterr().out
    assert note_on(checkout, started_at) is None
    assert note_on(checkout, gate.head_commit_sha(checkout)) is None


def test_check_reports_a_missing_record_and_then_a_present_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    commit_sha = gate.head_commit_sha(checkout)

    missing = gate.check_gate_record(
        repository_root=checkout, commit="HEAD", fetch=False
    )
    assert missing == 1
    assert commit_sha in capsys.readouterr().out

    gate.record_passing_commit(
        checkout, commit_sha, "gate: pass\ncanon: tarmar\n", push=False
    )
    present = gate.check_gate_record(
        repository_root=checkout, commit="HEAD", fetch=False
    )
    assert present == 0
    assert "gate: pass" in capsys.readouterr().out


def test_check_refuses_a_record_whose_first_mode_ran_the_default_canon(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Issue #921: without TARMAR_CANON_DIR both modes run the default canon,
    # and production serves the Tarmar one.
    checkout, _ = make_repository_with_remote(tmp_path)
    commit_sha = gate.head_commit_sha(checkout)
    for body in ("gate: pass\ncanon: default\n", "gate: pass\n"):
        gate.record_passing_commit(checkout, commit_sha, body, push=False)
        status = gate.check_gate_record(
            repository_root=checkout, commit="HEAD", fetch=False
        )
        assert status == 1
        output = capsys.readouterr().out
        assert "not the Tarmar canon" in output
        assert gate.CANON_DIR_ENV in output


def make_canon_checkout(tmp_path: Path) -> Path:
    """A canon that is a git checkout of its own, as smarks/tarmar-canon is."""
    canon_dir = tmp_path / "tarmar-canon"
    canon_dir.mkdir()
    git(canon_dir, "init", "--quiet", "--initial-branch=main")
    git(canon_dir, "config", "user.email", "canon@example.com")
    git(canon_dir, "config", "user.name", "Canon Test")
    (canon_dir / "canon.json").write_text('{"name": "tarmar"}\n', encoding="utf-8")
    git(canon_dir, "add", "canon.json")
    git(canon_dir, "commit", "--quiet", "-m", "canon")
    return canon_dir


def test_the_note_records_the_external_canons_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    canon_dir = make_canon_checkout(tmp_path)
    monkeypatch.setenv(gate.CANON_DIR_ENV, str(canon_dir))
    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(passing_step()),
    )

    status = gate.run_gate(
        repository_root=checkout, e2e_port="8399", push=False, deploy=False
    )

    assert status == 0
    record = note_on(checkout, gate.head_commit_sha(checkout))
    assert record is not None
    fields = gate.parse_gate_record(record)
    assert fields["canon"] == "tarmar"
    assert fields["canon-commit"] == git(canon_dir, "rev-parse", "HEAD")


def test_a_canon_checkout_with_changes_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    canon_dir = make_canon_checkout(tmp_path)
    (canon_dir / "the-world.md").write_text("# Unsaved\n", encoding="utf-8")
    monkeypatch.setenv(gate.CANON_DIR_ENV, str(canon_dir))
    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(passing_step()),
    )

    status = gate.run_gate(
        repository_root=checkout, e2e_port="8399", push=False, deploy=False
    )

    assert status == 1
    assert "canon checkout" in capsys.readouterr().out
    assert note_on(checkout, gate.head_commit_sha(checkout)) is None


def test_the_command_line_defaults_to_running_and_pushing() -> None:
    default = gate.parse_arguments([])
    assert default.check is None
    assert default.no_push is False
    # No fixed default: a free port is picked (issue #966). This asserted
    # DEFAULT_E2E_PORT, the fixed port two sessions' gates collided on.
    assert default.e2e_port is None

    checking = gate.parse_arguments(["--check"])
    assert checking.check == "HEAD"
    assert gate.parse_arguments(["--check", "abc123"]).check == "abc123"
    assert gate.parse_arguments(["--no-push"]).no_push is True
    assert gate.parse_arguments(["--e2e-port", "8123"]).e2e_port == "8123"
    assert default.default_canon_e2e_port is None
    assert (
        gate.parse_arguments(
            ["--default-canon-e2e-port", "8124"]
        ).default_canon_e2e_port
        == "8124"
    )


def test_there_is_no_flag_that_skips_the_default_canon_run() -> None:
    option_names = set(vars(gate.parse_arguments([])))
    # --no-deploy skips starting the Deploy workflow, never a step.
    assert option_names == {
        "check",
        "no_push",
        "no_deploy",
        "e2e_port",
        "default_canon_e2e_port",
    }


def test_the_steps_run_with_the_environment_the_run_wrapper_builds() -> None:
    environment = gate.build_step_environment()
    assert environment["PATH"]
    assert gate.build_step_environment(debug=True)["DEBUG"] == "true"


def test_the_caller_s_canon_passes_through_and_the_default_canon_overrides_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    external_canon = make_canon_directory(tmp_path)
    monkeypatch.setenv(gate.CANON_DIR_ENV, str(external_canon))
    first_mode = gate.build_step_environment()
    second_mode = gate.build_step_environment(canon_dir=gate.DEFAULT_CANON_DIR)

    # An external canon can be gated: the first mode sees it unchanged.
    assert first_mode[gate.CANON_DIR_ENV] == str(external_canon)
    assert second_mode[gate.CANON_DIR_ENV] == str(gate.DEFAULT_CANON_DIR)
    assert Path(second_mode[gate.CANON_DIR_ENV]).is_absolute()
    # Same base environment otherwise: the wrapper's PATH work reaches both.
    assert second_mode["PATH"] == first_mode["PATH"]


def test_the_variable_is_absent_from_the_first_mode_when_the_caller_set_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert gate.CANON_DIR_ENV not in gate.build_step_environment()
    second_mode = gate.build_step_environment(canon_dir=gate.DEFAULT_CANON_DIR)
    assert second_mode[gate.CANON_DIR_ENV] == str(gate.DEFAULT_CANON_DIR)


def make_canon_directory(tmp_path: Path) -> Path:
    """A stand-in canon directory outside the checkout, so the tree stays clean."""
    canon_dir = tmp_path / "canon"
    canon_dir.mkdir()
    return canon_dir


def test_each_step_runs_with_its_own_modes_canon_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    canon_dir = make_canon_directory(tmp_path)
    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(
            passing_step("own canon"),
            gate.GateStep("default canon", ["bash", "-c", "true"], canon_dir=canon_dir),
        ),
    )
    seen: dict[str, str | None] = {}

    def capture_environment(
        step: gate.GateStep, *, repository_root: Path, environment: dict[str, str]
    ) -> gate.StepResult:
        """Stand in for run_step, noting the canon variable each step receives."""
        seen[step.name] = environment.get(gate.CANON_DIR_ENV)
        return gate.StepResult(step=step, returncode=0, seconds=0.0)

    monkeypatch.setattr(gate, "run_step", capture_environment)

    status = gate.run_gate(
        repository_root=checkout, e2e_port="8399", push=False, deploy=False
    )

    assert status == 0
    assert seen == {"own canon": None, "default canon": str(canon_dir)}


def test_a_failing_default_canon_step_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    canon_dir = make_canon_directory(tmp_path)
    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(
            passing_step("pytest"),
            gate.GateStep(
                "pytest (default canon)", ["bash", "-c", "exit 5"], canon_dir=canon_dir
            ),
        ),
    )

    status = gate.run_gate(
        repository_root=checkout, e2e_port="8399", push=False, deploy=False
    )

    assert status == 5
    assert note_on(checkout, gate.head_commit_sha(checkout)) is None


def test_the_note_lists_both_modes_and_both_ports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    canon_dir = make_canon_directory(tmp_path)
    external_canon = tmp_path / "external-canon"
    external_canon.mkdir()
    monkeypatch.setenv(gate.CANON_DIR_ENV, str(external_canon))
    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(
            passing_step("pytest"),
            gate.GateStep(
                "pytest (default canon)", ["bash", "-c", "true"], canon_dir=canon_dir
            ),
        ),
    )

    status = gate.run_gate(
        repository_root=checkout,
        e2e_port="8399",
        default_canon_e2e_port="8398",
        push=False,
        deploy=False,
    )

    assert status == 0
    record = note_on(checkout, gate.head_commit_sha(checkout))
    assert record is not None
    assert "default-canon-e2e-port: 8398" in record
    assert "canon: tarmar" in record
    assert f"canon-dir: {external_canon}" in record
    assert "steps: pytest, pytest (default canon)" in record


def test_a_missing_default_canon_is_refused_before_any_step_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    absent = tmp_path / "no-such-canon"
    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(
            gate.GateStep("touches", ["bash", "-c", "touch ran"]),
            gate.GateStep("default canon", ["bash", "-c", "true"], canon_dir=absent),
        ),
    )

    status = gate.run_gate(
        repository_root=checkout, e2e_port="8399", push=False, deploy=False
    )

    assert status == 1
    assert str(absent) in capsys.readouterr().out
    assert not (checkout / "ran").exists()
    assert note_on(checkout, gate.head_commit_sha(checkout)) is None


def test_colliding_ports_are_refused_before_any_step_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)

    status = gate.run_gate(
        repository_root=checkout,
        e2e_port="8399",
        default_canon_e2e_port="8399",
        push=False,
        deploy=False,
    )

    assert status == 1
    output = capsys.readouterr().out
    assert "refusing to run" in output
    assert "collide" in output
    assert note_on(checkout, gate.head_commit_sha(checkout)) is None


def test_a_checkout_records_its_own_canon_not_the_gate_script_s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    in_tree_canon = checkout / "reference" / "content" / "dm-rules"
    in_tree_canon.mkdir(parents=True)
    (in_tree_canon / "index.md").write_text("# Canon\n", encoding="utf-8")
    git(checkout, "add", "reference/content/dm-rules/index.md")
    git(checkout, "commit", "-m", "add a canon")
    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(passing_step()),
    )

    status = gate.run_gate(
        repository_root=checkout, e2e_port="8399", push=False, deploy=False
    )

    assert status == 0
    record = note_on(checkout, gate.head_commit_sha(checkout))
    assert record is not None
    assert "canon: tarmar" in record
    assert f"canon-dir: {in_tree_canon}" in record
    # Inside this checkout, the canon has no commit of its own.
    assert "canon-commit: none" in record


# Issue #966: two sessions' gates collided on the fixed ports 8390/8391, and
# Playwright's reuseExistingServer had one pass test the other worktree's code.


def candidate(checkout: Path, step: int) -> int:
    """The port ``step`` places after where ``checkout``'s search starts."""
    base = int(gate.DEFAULT_E2E_PORT)
    start = gate.first_e2e_port_for(checkout) - base
    return base + (start + step) % gate.E2E_PORT_SPAN


def test_free_ports_are_picked_past_any_port_or_fake_ollama_port_in_use(
    tmp_path: Path, no_port_is_listening: set[int]
) -> None:
    # The first candidate's app port is taken, the second's fake Ollama.
    no_port_is_listening.update(
        {candidate(tmp_path, 0), candidate(tmp_path, 1) + gate.FAKE_OLLAMA_PORT_OFFSET}
    )

    first, second = gate.choose_e2e_ports(
        tmp_path, e2e_port=None, default_canon_e2e_port=None
    )

    assert (int(first), int(second)) == (candidate(tmp_path, 2), candidate(tmp_path, 3))
    gate.gate_steps(  # no collision
        e2e_port=first,
        default_canon_e2e_port=second,
        canon_name=gate.TARMAR_CANON_NAME,
    )


def test_the_search_starts_where_the_checkout_says_and_stays_in_the_span(
    tmp_path: Path,
) -> None:
    one = gate.first_e2e_port_for(tmp_path / "one")
    assert one == gate.first_e2e_port_for(tmp_path / "one")
    base = int(gate.DEFAULT_E2E_PORT)
    starts = {gate.first_e2e_port_for(tmp_path / str(number)) for number in range(20)}
    assert all(base <= start < base + gate.E2E_PORT_SPAN for start in starts)
    assert len(starts) > 1  # different checkouts, different starting points


def test_a_named_port_is_kept_and_the_other_avoids_its_claims(
    tmp_path: Path,
) -> None:
    named = candidate(tmp_path, 0)

    first, second = gate.choose_e2e_ports(
        tmp_path, e2e_port=str(named), default_canon_e2e_port=None
    )

    assert first == str(named)
    assert second == str(candidate(tmp_path, 1))


def test_no_free_port_in_the_span_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate, "port_listening", lambda port: True)

    with pytest.raises(gate.GateError, match="no free e2e port"):
        gate.choose_e2e_ports(tmp_path, e2e_port=None, default_canon_e2e_port=None)


@pytest.mark.parametrize("taken_offset", [0, gate.FAKE_OLLAMA_PORT_OFFSET])
def test_a_named_port_in_use_is_refused_before_any_step_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_port_is_listening: set[int],
    taken_offset: int,
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    no_port_is_listening.add(8398 + taken_offset)
    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(gate.GateStep("touches", ["bash", "-c", "touch ran"])),
    )

    status = gate.run_gate(
        repository_root=checkout,
        e2e_port="8399",
        default_canon_e2e_port="8398",
        push=False,
        deploy=False,
    )

    assert status == 1
    output = capsys.readouterr().out
    assert "refusing to run" in output
    assert f"port {8398 + taken_offset} is already listening" in output
    assert not (checkout / "ran").exists()
    assert note_on(checkout, gate.head_commit_sha(checkout)) is None


def test_a_port_taken_before_its_browser_step_records_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_port_is_listening: set[int],
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)

    def run_and_take(
        step: gate.GateStep, *, repository_root: Path, environment: dict[str, str]
    ) -> gate.StepResult:
        """Stand in for run_step; the first step leaves 8399 taken."""
        no_port_is_listening.add(8399)
        return gate.StepResult(step=step, returncode=0, seconds=0.0)

    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(
            passing_step("pytest"),
            gate.GateStep(
                "playwright (Tarmar canon)",
                ["bash", "-c", "true"],
                {"E2E_PORT": "8399"},
                canon_label="Tarmar canon",
            ),
        ),
    )
    monkeypatch.setattr(gate, "run_step", run_and_take)

    status = gate.run_gate(
        repository_root=checkout, e2e_port="8399", push=False, deploy=False
    )

    assert status == 1
    assert (
        "playwright (Tarmar canon): port 8399 is already listening"
        in capsys.readouterr().out
    )
    assert note_on(checkout, gate.head_commit_sha(checkout)) is None


def test_the_note_records_the_ports_the_gate_picked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    expected = gate.choose_e2e_ports(
        checkout, e2e_port=None, default_canon_e2e_port=None
    )
    choices_given: list[tuple[str, str, str]] = []

    def steps_for(
        *, e2e_port: str, default_canon_e2e_port: str, canon_name: str
    ) -> list[gate.GateStep]:
        """Stand in for gate_steps, noting the ports and canon the gate chose."""
        choices_given.append((e2e_port, default_canon_e2e_port, canon_name))
        return [passing_step()]

    monkeypatch.setattr(gate, "gate_steps", steps_for)

    status = gate.run_gate(repository_root=checkout, push=False, deploy=False)

    assert status == 0
    [(first_port, second_port, canon_name)] = choices_given
    assert (first_port, second_port) == expected
    record = note_on(checkout, gate.head_commit_sha(checkout))
    assert record is not None
    assert f"e2e-port: {expected[0]}" in record.splitlines()
    assert f"default-canon-e2e-port: {expected[1]}" in record.splitlines()
    # The first mode's steps are named for the canon the note records (#10).
    assert f"canon: {canon_name}" in record.splitlines()


# Spencer, 2026-10-10: Deploy started on every push to main and refused each
# merge, which had no record yet, so every merge showed a red run. Deploy now
# starts only when a gate run has recorded the head of main.


def test_a_pass_on_the_head_of_main_starts_deploy_for_that_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    commit_sha = gate.head_commit_sha(checkout)
    started: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        gate,
        "dispatch_deploy_workflow",
        lambda root, sha: started.append((root, sha)) or "",
    )

    outcome = gate.start_deploy_if_head(checkout, commit_sha)

    assert started == [(checkout, commit_sha)]
    assert outcome.startswith(f"started deploy.yml for {commit_sha}")


def test_a_pass_on_an_older_commit_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    older = gate.head_commit_sha(checkout)
    push_a_newer_main(checkout)
    started: list[str] = []
    monkeypatch.setattr(
        gate, "dispatch_deploy_workflow", lambda root, sha: started.append(sha) or ""
    )

    outcome = gate.start_deploy_if_head(checkout, older)

    # deploy.sh deploys the head; the head's own gate run starts that deploy.
    assert started == []
    assert outcome.startswith("not deploying")


def push_a_newer_main(checkout: Path) -> None:
    """Move origin's main one commit past the checkout's HEAD."""
    (checkout / "tracked.txt").write_text("second\n", encoding="utf-8")
    git(checkout, "commit", "--quiet", "-am", "second")
    git(checkout, "push", "--quiet", "origin", "main")
    git(checkout, "reset", "--quiet", "--hard", "HEAD~1")


def run_passing_gate(
    tmp_path: Path,
    checkout: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    push: bool,
    deploy: bool,
    tarmar_canon: bool = True,
) -> tuple[int, list[str]]:
    """Run a gate whose one step passes, recording any Deploy it starts."""
    if tarmar_canon:
        monkeypatch.setenv(gate.CANON_DIR_ENV, str(make_canon_checkout(tmp_path)))
    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(passing_step()),
    )
    started: list[str] = []
    monkeypatch.setattr(
        gate, "dispatch_deploy_workflow", lambda root, sha: started.append(sha) or ""
    )
    status = gate.run_gate(repository_root=checkout, push=push, deploy=deploy, **PORTS)
    return status, started


def test_a_pushed_tarmar_record_of_the_head_starts_deploy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    status, started = run_passing_gate(
        tmp_path, checkout, monkeypatch, push=True, deploy=True
    )
    assert status == 0
    assert started == [gate.head_commit_sha(checkout)]


def test_a_default_canon_record_starts_no_deploy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # deploy.sh refuses it, so starting Deploy would only make a red run.
    checkout, _ = make_repository_with_remote(tmp_path)
    status, started = run_passing_gate(
        tmp_path, checkout, monkeypatch, push=True, deploy=True, tarmar_canon=False
    )
    assert status == 0
    assert started == []
    assert "not deploying: this record ran the default canon" in capsys.readouterr().out


def test_a_gate_run_on_a_commit_main_has_moved_past_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    push_a_newer_main(checkout)
    status, started = run_passing_gate(
        tmp_path, checkout, monkeypatch, push=True, deploy=True
    )
    assert status == 0
    assert started == []


@pytest.mark.parametrize(("push", "deploy"), [(False, True), (True, False)])
def test_no_deploy_without_a_pushed_record_or_when_told_not_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, push: bool, deploy: bool
) -> None:
    # A local-only record is invisible to deploy.sh, so a deploy would refuse.
    checkout, _ = make_repository_with_remote(tmp_path)
    status, started = run_passing_gate(
        tmp_path, checkout, monkeypatch, push=push, deploy=deploy
    )
    assert status == 0
    assert started == []


def test_a_deploy_that_will_not_start_fails_the_run_but_keeps_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    commit_sha = gate.head_commit_sha(checkout)
    monkeypatch.setenv(gate.CANON_DIR_ENV, str(make_canon_checkout(tmp_path)))
    monkeypatch.setattr(
        gate,
        "gate_steps",
        steps_stub(passing_step()),
    )

    def gh_refuses(root: Path, sha: str) -> str:
        raise subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 403")

    monkeypatch.setattr(gate, "dispatch_deploy_workflow", gh_refuses)

    status = gate.run_gate(repository_root=checkout, push=True, deploy=True, **PORTS)

    assert status == 1
    assert note_on(checkout, commit_sha) is not None
    assert "did not start: HTTP 403" in capsys.readouterr().out


def test_the_command_line_starts_deploy_unless_told_not_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(
        gate, "run_gate", lambda **options: calls.append(options["deploy"]) or 0
    )
    gate.main([])
    gate.main(["--no-deploy"])
    assert calls == [True, False]


def test_deploy_starts_only_on_request_not_on_push() -> None:
    workflow = yaml.safe_load(
        (
            gate.REPOSITORY_ROOT / ".github" / "workflows" / gate.DEPLOY_WORKFLOW
        ).read_text()
    )
    # PyYAML reads the bare key `on` as True.
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"workflow_dispatch"}


def test_deploy_is_started_with_exactly_this_gh_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def record_call(
        command: list[str], **options: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(gate.subprocess, "run", record_call)
    gate.dispatch_deploy_workflow(tmp_path, "abc123")

    assert calls == [
        [
            "gh",
            "workflow",
            "run",
            "deploy.yml",
            "--ref",
            "main",
            "-f",
            "command=--expect abc123",
        ]
    ]
