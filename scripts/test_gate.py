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

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import gate
import pytest

PORTS = {"e2e_port": "8399", "default_canon_e2e_port": "8398"}


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
    steps = gate.gate_steps(**PORTS)
    assert [step.name for step in steps] == [
        "ruff",
        "ruff format",
        "pyright",
        "makemigrations",
        "build_guides",
        "pytest",
        "jest",
        "playwright",
        "pytest (default canon)",
        "jest (default canon)",
        "playwright (default canon)",
    ]
    commands = {step.name: step.command for step in steps}
    assert commands["pytest"] == ["uv", "run", "pytest", "-q"]
    assert commands["jest"] == ["npx", "jest"]
    assert "scripts/update_js_coverage_threshold.py" in commands["playwright"]
    assert "--check" in commands["playwright"]
    for name in ("pytest", "jest", "playwright"):
        assert commands[f"{name} (default canon)"] == commands[name]


def test_only_the_suite_steps_of_the_second_mode_name_a_canon() -> None:
    canon_by_step = {step.name: step.canon_dir for step in gate.gate_steps(**PORTS)}
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
    steps = {step.name: step for step in gate.gate_steps(**PORTS)}
    assert steps["playwright"].environment == {"E2E_PORT": "8399"}
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
        gate.gate_steps(e2e_port=first_port, default_canon_e2e_port=second_port)


def test_the_migration_check_runs_with_debug_so_it_needs_no_production_env() -> None:
    steps = {step.name: step for step in gate.gate_steps(**PORTS)}
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
        gate, "gate_steps", lambda *, e2e_port, default_canon_e2e_port: [passing_step()]
    )
    monkeypatch.setattr(gate, "run_step", record_that_it_ran)

    status = gate.run_gate(repository_root=checkout, e2e_port="8399", push=False)

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
        lambda *, e2e_port, default_canon_e2e_port: [
            passing_step("one"),
            passing_step("two"),
        ],
    )

    status = gate.run_gate(repository_root=checkout, e2e_port="8399", push=False)

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
        lambda *, e2e_port, default_canon_e2e_port: [
            gate.GateStep("failing", ["bash", "-c", "exit 3"]),
            gate.GateStep("later", ["bash", "-c", "touch ran-later"]),
        ],
    )

    status = gate.run_gate(repository_root=checkout, e2e_port="8399", push=False)

    assert status == 3
    assert not (checkout / "ran-later").exists()
    assert note_on(checkout, gate.head_commit_sha(checkout)) is None


def test_a_tree_changed_by_the_suites_is_not_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout, _ = make_repository_with_remote(tmp_path)
    commit_sha = gate.head_commit_sha(checkout)
    monkeypatch.setattr(
        gate,
        "gate_steps",
        lambda *, e2e_port, default_canon_e2e_port: [
            gate.GateStep(
                "rewrites the tree", ["bash", "-c", "echo edited > tracked.txt"]
            )
        ],
    )

    status = gate.run_gate(repository_root=checkout, e2e_port="8399", push=False)

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
        lambda *, e2e_port, default_canon_e2e_port: [
            gate.GateStep(
                "commits underneath",
                [
                    "bash",
                    "-c",
                    "echo second > tracked.txt && git commit --all -m second",
                ],
            )
        ],
    )

    status = gate.run_gate(repository_root=checkout, e2e_port="8399", push=False)

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
        lambda *, e2e_port, default_canon_e2e_port: [passing_step()],
    )

    status = gate.run_gate(repository_root=checkout, e2e_port="8399", push=False)

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
        lambda *, e2e_port, default_canon_e2e_port: [passing_step()],
    )

    status = gate.run_gate(repository_root=checkout, e2e_port="8399", push=False)

    assert status == 1
    assert "canon checkout" in capsys.readouterr().out
    assert note_on(checkout, gate.head_commit_sha(checkout)) is None


def test_the_command_line_defaults_to_running_and_pushing() -> None:
    default = gate.parse_arguments([])
    assert default.check is None
    assert default.no_push is False
    assert default.e2e_port == gate.DEFAULT_E2E_PORT

    checking = gate.parse_arguments(["--check"])
    assert checking.check == "HEAD"
    assert gate.parse_arguments(["--check", "abc123"]).check == "abc123"
    assert gate.parse_arguments(["--no-push"]).no_push is True
    assert gate.parse_arguments(["--e2e-port", "8123"]).e2e_port == "8123"
    assert default.default_canon_e2e_port == gate.DEFAULT_CANON_E2E_PORT
    assert (
        gate.parse_arguments(
            ["--default-canon-e2e-port", "8124"]
        ).default_canon_e2e_port
        == "8124"
    )


def test_there_is_no_flag_that_skips_the_default_canon_run() -> None:
    option_names = set(vars(gate.parse_arguments([])))
    assert option_names == {"check", "no_push", "e2e_port", "default_canon_e2e_port"}


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
        lambda *, e2e_port, default_canon_e2e_port: [
            passing_step("own canon"),
            gate.GateStep("default canon", ["bash", "-c", "true"], canon_dir=canon_dir),
        ],
    )
    seen: dict[str, str | None] = {}

    def capture_environment(
        step: gate.GateStep, *, repository_root: Path, environment: dict[str, str]
    ) -> gate.StepResult:
        """Stand in for run_step, noting the canon variable each step receives."""
        seen[step.name] = environment.get(gate.CANON_DIR_ENV)
        return gate.StepResult(step=step, returncode=0, seconds=0.0)

    monkeypatch.setattr(gate, "run_step", capture_environment)

    status = gate.run_gate(repository_root=checkout, e2e_port="8399", push=False)

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
        lambda *, e2e_port, default_canon_e2e_port: [
            passing_step("pytest"),
            gate.GateStep(
                "pytest (default canon)", ["bash", "-c", "exit 5"], canon_dir=canon_dir
            ),
        ],
    )

    status = gate.run_gate(repository_root=checkout, e2e_port="8399", push=False)

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
        lambda *, e2e_port, default_canon_e2e_port: [
            passing_step("pytest"),
            gate.GateStep(
                "pytest (default canon)", ["bash", "-c", "true"], canon_dir=canon_dir
            ),
        ],
    )

    status = gate.run_gate(
        repository_root=checkout,
        e2e_port="8399",
        default_canon_e2e_port="8398",
        push=False,
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
        lambda *, e2e_port, default_canon_e2e_port: [
            gate.GateStep("touches", ["bash", "-c", "touch ran"]),
            gate.GateStep("default canon", ["bash", "-c", "true"], canon_dir=absent),
        ],
    )

    status = gate.run_gate(repository_root=checkout, e2e_port="8399", push=False)

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
        lambda *, e2e_port, default_canon_e2e_port: [passing_step()],
    )

    status = gate.run_gate(repository_root=checkout, e2e_port="8399", push=False)

    assert status == 0
    record = note_on(checkout, gate.head_commit_sha(checkout))
    assert record is not None
    assert "canon: tarmar" in record
    assert f"canon-dir: {in_tree_canon}" in record
    # Inside this checkout, the canon has no commit of its own.
    assert "canon-commit: none" in record
