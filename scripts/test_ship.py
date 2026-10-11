"""Unit tests for ``./run ship``, the one command that deploys main.

The git-backed tests build real repositories with bare origins, so the fetch
and worktree plumbing are the real thing. Provisioning, the gate and gh are
stood in for, by stand-in scripts or functions, since they need the suites,
npm and GitHub.
"""

import os
import subprocess
from pathlib import Path
from typing import Any

import gate
import pytest
import ship

SHA = "a" * 40


def git(repository: Path, *arguments: str) -> str:
    """Run git in ``repository`` and return its stripped stdout."""
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def commit(repository: Path, content: str) -> None:
    (repository / "file.txt").write_text(f"{content}\n", encoding="utf-8")
    git(repository, "add", "file.txt")
    git(repository, "commit", "--quiet", "-m", content)


def make_repository_with_origin(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """A checkout, and a bare origin that a second clone has moved past it.

    The checkout has never fetched the newer commit, so a ship that skipped
    its fetch would gate the wrong one.
    """
    origin = tmp_path / f"{name}-origin.git"
    subprocess.run(
        ["git", "init", "--quiet", "--bare", "--initial-branch=main", str(origin)],
        check=True,
    )
    checkout = tmp_path / name
    for clone in (checkout, tmp_path / f"{name}-elsewhere"):
        clone.mkdir()
        git(clone, "init", "--quiet", "--initial-branch=main")
        git(clone, "config", "user.email", "ship@example.com")
        git(clone, "config", "user.name", "Ship Test")
        git(clone, "remote", "add", "origin", str(origin))
    commit(checkout, "first")
    git(checkout, "push", "--quiet", "origin", "main")
    elsewhere = tmp_path / f"{name}-elsewhere"
    git(elsewhere, "pull", "--quiet", "origin", "main")
    commit(elsewhere, "merged elsewhere")
    git(elsewhere, "push", "--quiet", "origin", "main")
    return checkout, origin


@pytest.fixture()
def repositories(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    app, app_origin = make_repository_with_origin(tmp_path, "app")
    canon, canon_origin = make_repository_with_origin(tmp_path, "canon")
    workspaces = tmp_path / "temporary"
    workspaces.mkdir()
    return app, canon, workspaces, app_origin, canon_origin


def stand_in(
    monkeypatch: pytest.MonkeyPatch,
    *,
    gate_raises: BaseException | None = None,
    deploy_outcome: str = ship.DEPLOYED,
) -> dict[str, Any]:
    """Replace gh, provisioning, the gate and the Deploy follow-up."""
    seen: dict[str, Any] = {"steps": []}

    def record_step(command, *, cwd, environment):
        seen["steps"].append(tuple(command))

    def record_gate(app: Path, environment: dict[str, str], *, deploy: bool) -> str:
        canon_dir = Path(environment[ship.CANON_DIR_ENV])
        seen.update(
            gated=git(app, "rev-parse", "HEAD"),
            canon=git(canon_dir, "rev-parse", "HEAD"),
            tmpdir=environment["TMPDIR"],
            virtual_env="VIRTUAL_ENV" in environment,
            deploy=deploy,
        )
        if gate_raises is not None:
            raise gate_raises
        return "4242" if deploy else ""

    def record_follow(run_id: str, app: Path) -> str:
        seen["followed"] = run_id
        if isinstance(deploy_outcome, BaseException):
            raise deploy_outcome
        return deploy_outcome

    real_run_quietly = ship.run_quietly

    def gh_is_logged_in(command, *, cwd=None):
        if command[:3] == ["gh", "auth", "status"]:
            return ""
        return real_run_quietly(command, cwd=cwd)

    monkeypatch.setattr(ship, "run_quietly", gh_is_logged_in)
    monkeypatch.setattr(ship, "run_step", record_step)
    monkeypatch.setattr(ship, "run_gate", record_gate)
    monkeypatch.setattr(ship, "follow_deploy", record_follow)
    return seen


def assert_nothing_left(app: Path, canon: Path, workspaces: Path) -> None:
    assert list(workspaces.iterdir()) == []
    for repository in (app, canon):
        assert len(git(repository, "worktree", "list").splitlines()) == 1


def test_ship_gates_origins_heads_after_fetching_them(
    repositories, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, canon, workspaces, app_origin, canon_origin = repositories
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/else")
    seen = stand_in(monkeypatch)

    status = ship.ship(
        repository_root=app, canon_checkout=canon, temporary_parent=workspaces
    )

    assert status == ship.EXIT_LIVE
    assert seen["gated"] == git(app_origin, "rev-parse", "main")
    assert seen["canon"] == git(canon_origin, "rev-parse", "main")
    assert seen["gated"] != git(app, "rev-parse", "HEAD")
    assert seen["followed"] == "4242"
    assert seen["steps"][0][:2] == ("uv", "sync") and seen["steps"][1][:2] == (
        "npm",
        "ci",
    )
    # The suites' temporary files go in the workspace, not the system's (#987).
    assert seen["tmpdir"].startswith(str(workspaces))
    assert seen["virtual_env"] is False
    assert_nothing_left(app, canon, workspaces)


@pytest.mark.parametrize(
    ("gate_raises", "deploy_outcome", "expected"),
    [
        (None, ship.ALREADY_LIVE, ship.EXIT_LIVE),
        (ship.ShipError("the gate failed"), ship.DEPLOYED, ship.EXIT_NOT_DEPLOYED),
        (None, ship.ShipError("Deploy stood down"), ship.EXIT_NOT_DEPLOYED),
        (KeyboardInterrupt(), ship.DEPLOYED, ship.EXIT_INTERRUPTED),
        (ship.Interrupted("SIGHUP"), ship.DEPLOYED, ship.EXIT_INTERRUPTED),
    ],
)
def test_every_ending_reports_its_status_and_leaves_nothing_behind(
    repositories, monkeypatch: pytest.MonkeyPatch, gate_raises, deploy_outcome, expected
) -> None:
    app, canon, workspaces, *_ = repositories
    stand_in(monkeypatch, gate_raises=gate_raises, deploy_outcome=deploy_outcome)

    status = ship.ship(
        repository_root=app, canon_checkout=canon, temporary_parent=workspaces
    )

    assert status == expected
    assert_nothing_left(app, canon, workspaces)


def test_a_failed_fetch_stops_the_ship_and_leaves_nothing_behind(
    repositories, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, canon, workspaces, *_ = repositories
    stand_in(monkeypatch)
    git(canon, "remote", "set-url", "origin", "/nowhere/at/all.git")

    status = ship.ship(
        repository_root=app, canon_checkout=canon, temporary_parent=workspaces
    )

    assert status == ship.EXIT_NOT_DEPLOYED
    assert_nothing_left(app, canon, workspaces)


def test_a_cleanup_failure_is_reported_without_hiding_the_deploy(
    repositories, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app, canon, workspaces, *_ = repositories
    stand_in(monkeypatch)
    real_remove = ship.remove_worktree

    def app_worktree_sticks(repository: Path, path: Path) -> None:
        if path.name == "app":
            raise ship.ShipError(f"could not remove {path}")
        real_remove(repository, path)

    monkeypatch.setattr(ship, "remove_worktree", app_worktree_sticks)

    status = ship.ship(
        repository_root=app, canon_checkout=canon, temporary_parent=workspaces
    )

    out = capsys.readouterr().out
    assert status == ship.EXIT_LEFT_BEHIND
    assert "is live" in out
    assert "left behind: app worktree" in out
    # The other steps still ran.
    assert len(git(canon, "worktree", "list").splitlines()) == 1


def test_a_killed_ships_worktrees_are_cleared_by_the_next(
    repositories, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, canon, workspaces, *_ = repositories
    stale = workspaces / f"{ship.WORKSPACE_PREFIX}killed" / "app"
    stale.parent.mkdir()
    git(app, "worktree", "add", "--quiet", "--detach", str(stale), "HEAD")
    stand_in(monkeypatch)

    ship.ship(repository_root=app, canon_checkout=canon, temporary_parent=workspaces)

    assert_nothing_left(app, canon, workspaces)


def test_a_rehearsal_gates_without_starting_deploy(
    repositories, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, canon, workspaces, *_ = repositories
    seen = stand_in(monkeypatch)

    status = ship.ship(
        repository_root=app,
        canon_checkout=canon,
        deploy=False,
        temporary_parent=workspaces,
    )

    assert status == ship.EXIT_LIVE
    assert seen["deploy"] is False
    assert "followed" not in seen
    assert_nothing_left(app, canon, workspaces)


def test_the_run_is_read_from_the_gates_own_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The real gate's wording, not a copy of it: a rewording there fails here.
    url = "https://github.com/smarks/tarmar-studio-private/actions/runs/38093998487"
    monkeypatch.setattr(gate, "remote_branch_head", lambda root, remote: SHA)
    monkeypatch.setattr(gate, "dispatch_deploy_workflow", lambda root, sha: url)
    with_url = gate.start_deploy_if_head(Path("."), SHA)
    monkeypatch.setattr(gate, "dispatch_deploy_workflow", lambda root, sha: "")
    without_url = gate.start_deploy_if_head(Path("."), SHA)

    found_with = ship.DEPLOY_STARTED_PATTERN.search(with_url)
    assert found_with is not None
    assert found_with.groups() == (SHA, "38093998487")
    found_without = ship.DEPLOY_STARTED_PATTERN.search(without_url)
    assert found_without is not None
    assert found_without.groups() == (SHA, None)


def write_script(directory: Path, name: str, body: str) -> Path:
    script = directory / name
    script.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def test_run_gate_returns_the_run_the_gate_started(tmp_path: Path) -> None:
    write_script(
        tmp_path,
        "run",
        f"echo '[gate] started {gate.DEPLOY_WORKFLOW} for {SHA}: https://x/actions/runs/77'",
    )
    assert ship.run_gate(tmp_path, dict(os.environ), deploy=True) == "77"


def test_run_gate_looks_the_run_up_when_gh_printed_no_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_script(
        tmp_path, "run", f"echo '[gate] started {gate.DEPLOY_WORKFLOW} for {SHA}'"
    )
    monkeypatch.setattr(
        ship, "find_deploy_run", lambda sha, app: f"found-for-{sha[:4]}"
    )
    assert ship.run_gate(tmp_path, dict(os.environ), deploy=True) == "found-for-aaaa"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("echo '[gate] pytest failed'; exit 1", "the gate failed"),
        ("echo '[gate] not deploying: main moved'", "started no Deploy"),
    ],
)
def test_run_gate_stops_the_ship_when_nothing_was_started(
    tmp_path: Path, body: str, message: str
) -> None:
    write_script(tmp_path, "run", body)
    with pytest.raises(ship.ShipError, match=message):
        ship.run_gate(tmp_path, dict(os.environ), deploy=True)


def test_run_step_reports_a_failing_or_missing_program(tmp_path: Path) -> None:
    with pytest.raises(ship.ShipError, match="exit 3"):
        ship.run_step(
            ["bash", "-c", "exit 3"], cwd=tmp_path, environment=dict(os.environ)
        )
    with pytest.raises(ship.ShipError, match="not installed"):
        ship.run_step(
            ["no-such-program-anywhere"], cwd=tmp_path, environment=dict(os.environ)
        )


@pytest.mark.parametrize(
    ("watch_exit", "log", "expected"),
    [
        (0, "[DEPLOY] Deployment complete!", ship.DEPLOYED),
        (0, "[DEPLOY] Already up to date. Use --force", ship.ALREADY_LIVE),
        (
            0,
            "main moved past a to b, which has no record. Nothing to do.",
            "stood down",
        ),
        (1, "", "did not finish green"),
    ],
)
def test_follow_deploy_says_what_the_run_did(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    watch_exit: int,
    log: str,
    expected: str,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_script(
        bin_dir,
        "gh",
        f'if [ "$2" = watch ]; then exit {watch_exit}; fi\necho "{log}"',
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    if expected in (ship.DEPLOYED, ship.ALREADY_LIVE):
        assert ship.follow_deploy("9", tmp_path) == expected
    else:
        with pytest.raises(ship.ShipError, match=expected):
            ship.follow_deploy("9", tmp_path)


def test_ship_takes_only_rehearse_and_needs_a_canon_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert ship.main(["--force"]) == 2
    monkeypatch.setenv(ship.CANON_DIR_ENV, str(tmp_path / "missing"))
    assert ship.main([]) == 2
    assert ship.main(["--rehearse"]) == 2


@pytest.mark.parametrize(("printed", "found"), [("123", "123"), ("null", None)])
def test_find_deploy_run_asks_gh_for_the_commits_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, printed: str, found: str | None
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_script(bin_dir, "gh", f'echo "$*" > "{tmp_path}/asked"; echo {printed}')
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    if found:
        assert ship.find_deploy_run(SHA, tmp_path) == found
        asked = (tmp_path / "asked").read_text()
        assert (
            f"--commit {SHA}" in asked and f"--workflow {gate.DEPLOY_WORKFLOW}" in asked
        )
    else:
        with pytest.raises(ship.ShipError, match="cannot be found"):
            ship.find_deploy_run(SHA, tmp_path)


def test_an_interrupted_gate_is_stopped_with_everything_it_started(
    tmp_path: Path,
) -> None:
    # The gate's own children (Playwright, a dev server) must not outlive it.
    process = subprocess.Popen(
        ["bash", "-c", "sleep 60 & wait"], start_new_session=True, text=True
    )
    group = os.getpgid(process.pid)

    ship.stop_process_group(process)

    assert process.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.killpg(group, 0)


def test_the_outcomes_ship_reads_are_what_deploy_sh_prints() -> None:
    # Copies of deploy.sh's own words; a rewording there must fail here.
    deploy_script = (ship.REPOSITORY_ROOT / "deploy.sh").read_text()
    for outcome in (ship.DEPLOYED, ship.ALREADY_LIVE, ship.STOOD_DOWN):
        assert outcome in deploy_script, outcome


def test_follow_deploy_waits_for_a_log_that_is_not_ready_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    counter = tmp_path / "asked"
    write_script(
        bin_dir,
        "gh",
        'if [ "$2" = watch ]; then exit 0; fi\n'
        f'echo x >> "{counter}"\n'
        f'if [ "$(wc -l < "{counter}")" -lt 3 ]; then exit 1; fi\n'
        'echo "[DEPLOY] Deployment complete!"',
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(ship.time, "sleep", lambda seconds: None)

    assert ship.follow_deploy("9", tmp_path) == ship.DEPLOYED
    assert len(counter.read_text().splitlines()) == 3
