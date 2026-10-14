"""Unit tests for ``./run ship``, the one command that deploys main.

The git-backed tests build real repositories with bare origins, so the fetch
and worktree plumbing are the real thing. Provisioning, the gate and gh are
stood in for, by stand-in scripts or functions, since they need the suites,
npm and GitHub.
"""

import contextlib
import fcntl
import json
import os
import shlex
import signal
import subprocess
import sys
import termios
import threading
import time
from collections.abc import Sequence
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
        (
            None,
            ship.LostTrackOfDeploy("lost track of Deploy run 4242"),
            ship.EXIT_DEPLOY_UNKNOWN,
        ),
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


# What gh printed for the failed request in issue #33.
BAD_GATEWAY = "failed to get jobs: HTTP 502: Server Error"


def fake_gh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    watch_exits: Sequence[int],
    states: Sequence[str] = (),
    log: str = "",
) -> list[float]:
    """Put a gh on PATH that answers ``run watch``, the run's state and its log.

    Each ``gh run watch`` exits with the next of ``watch_exits``, printing
    BAD_GATEWAY when that is non-zero; each state query prints the next of
    ``states``, where "502" fails the request as GitHub did. ``--log`` prints
    ``log``. Waits are recorded rather than slept.

    Returns:
        The seconds follow_deploy asked to wait, in order.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "watch_exits").write_text("".join(f"{code}\n" for code in watch_exits))
    (tmp_path / "states").write_text("".join(f"{state}\n" for state in states))
    write_script(
        bin_dir,
        "gh",
        f"""take() {{
  count=$(( $(cat "{tmp_path}/$1.count" 2>/dev/null || echo 0) + 1 ))
  echo "$count" > "{tmp_path}/$1.count"
  sed -n "${{count}}p" "{tmp_path}/$1"
}}
if [ "$2" = watch ]; then
  code=$(take watch_exits)
  if [ "$code" != 0 ]; then echo "{BAD_GATEWAY}" >&2; fi
  exit "$code"
fi
if [ "$4" = --json ]; then
  state=$(take states)
  if [ "$state" = 502 ]; then echo "HTTP 502: Server Error" >&2; exit 1; fi
  echo "$state"
  exit 0
fi
echo "{log}"
""",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    waits: list[float] = []
    monkeypatch.setattr(ship.time, "sleep", waits.append)
    return waits


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
    fake_gh(
        tmp_path,
        monkeypatch,
        watch_exits=[watch_exit],
        states=["completed failure"],
        log=log,
    )
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


@pytest.mark.parametrize(
    "states",
    [
        # gh run watch failed a request; the run was still going.
        ["in_progress "],
        # ...and so did the question of where the run stood.
        ["502", "in_progress "],
    ],
)
def test_follow_deploy_rides_out_a_failed_request_to_github(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, states: list[str]
) -> None:
    # Issue #33: an HTTP 502 while following the run is not a failed deploy.
    waits = fake_gh(
        tmp_path,
        monkeypatch,
        watch_exits=[1] * len(states) + [0],
        states=states,
        log="[DEPLOY] Deployment complete!",
    )

    assert ship.follow_deploy("9", tmp_path) == ship.DEPLOYED
    assert waits == [
        ship.POLL_RETRY_SECONDS * 2**attempt for attempt in range(len(states))
    ]


def test_follow_deploy_gives_up_on_a_github_that_keeps_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = ship.POLL_FAILURE_LIMIT + 1
    waits = fake_gh(
        tmp_path, monkeypatch, watch_exits=[1] * attempts, states=["502"] * attempts
    )

    with pytest.raises(ship.LostTrackOfDeploy) as lost:
        ship.follow_deploy("9", tmp_path)

    message = str(lost.value)
    assert "lost track of Deploy run 9" in message
    assert "HTTP 502" in message
    assert "not a failed deploy" in message
    assert "did not finish green" not in message
    assert len(waits) == ship.POLL_FAILURE_LIMIT


def test_follow_deploy_believes_the_run_over_a_failed_watch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The watch's last request failed, but the run had already ended green.
    waits = fake_gh(
        tmp_path,
        monkeypatch,
        watch_exits=[1],
        states=["completed success"],
        log="[DEPLOY] Deployment complete!",
    )

    assert ship.follow_deploy("9", tmp_path) == ship.DEPLOYED
    assert waits == []


def start_detached(command: Sequence[str], *, cwd: Path) -> subprocess.Popen[bytes]:
    """Start ``command`` as Playwright starts a webServer: in a session of its own."""
    return subprocess.Popen(list(command), cwd=cwd, start_new_session=True)


def make_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A ship workspace with an app folder in it, and a folder outside it."""
    workspace = tmp_path / f"{ship.WORKSPACE_PREFIX}test"
    app = workspace / "app"
    app.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    return workspace, app, elsewhere


def test_cleanup_stops_what_the_workspace_left_running(tmp_path: Path) -> None:
    # Issue #31: Playwright's dev server runs in a process group of its own,
    # out of reach of the signal that stops the gate's group.
    workspace, app, elsewhere = make_workspace(tmp_path)
    server = app / "serve.py"
    server.write_text("import time\ntime.sleep(300)\n", encoding="utf-8")
    running_in_workspace = start_detached(["sleep", "300"], cwd=app)
    running_from_workspace = start_detached(
        [sys.executable, str(server)], cwd=elsewhere
    )
    running_elsewhere = start_detached(["sleep", "300"], cwd=elsewhere)
    try:
        ship.stop_processes_in(workspace)

        assert running_in_workspace.wait(timeout=5) == -signal.SIGTERM
        assert running_from_workspace.wait(timeout=5) == -signal.SIGTERM
        assert running_elsewhere.poll() is None
    finally:
        for process in (
            running_in_workspace,
            running_from_workspace,
            running_elsewhere,
        ):
            process.kill()
            process.wait()


def test_cleanup_kills_what_will_not_stop_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, app, _ = make_workspace(tmp_path)
    monkeypatch.setattr(ship, "STOP_GRACE_SECONDS", 0.5)
    stubborn = start_detached(["bash", "-c", "trap '' TERM; sleep 300 & wait"], cwd=app)
    try:
        # The sleep starts after the trap is set, and inherits it.
        deadline = time.monotonic() + 10
        while not ship.processes_in(workspace).keys() - {stubborn.pid}:
            assert time.monotonic() < deadline, "the sleep never started"
        ship.stop_processes_in(workspace)

        assert stubborn.wait(timeout=5) == -signal.SIGKILL
        assert ship.processes_in(workspace) == {}
    finally:
        # The whole group, so a failure here leaves no sleep behind either.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(stubborn.pid, signal.SIGKILL)
        stubborn.wait()


def test_a_process_ship_cannot_stop_is_reported_as_left_behind(
    repositories, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    app, canon, workspaces, *_ = repositories
    stand_in(monkeypatch)

    def refuses(workspace: Path) -> None:
        raise ship.ShipError("still running: 4321 ./run dev 127.0.0.1:8547")

    monkeypatch.setattr(ship, "stop_processes_in", refuses)

    status = ship.ship(
        repository_root=app, canon_checkout=canon, temporary_parent=workspaces
    )

    assert status == ship.EXIT_LEFT_BEHIND
    assert "left behind: processes: still running: 4321" in capsys.readouterr().out
    assert_nothing_left(app, canon, workspaces)


def test_an_interrupted_ship_leaves_no_dev_server_running(
    repositories, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The incident in issue #31: the ship stopped during Playwright, and the
    # dev server Playwright had started detached outlived it.
    app, canon, workspaces, *_ = repositories
    stand_in(monkeypatch)
    started: list[subprocess.Popen[bytes]] = []

    def gate_starts_a_server_then_is_stopped(
        app_worktree: Path, environment: dict[str, str], *, deploy: bool
    ) -> str:
        started.append(start_detached(["sleep", "300"], cwd=app_worktree))
        raise ship.Interrupted("SIGTERM")

    monkeypatch.setattr(ship, "run_gate", gate_starts_a_server_then_is_stopped)
    try:
        status = ship.ship(
            repository_root=app, canon_checkout=canon, temporary_parent=workspaces
        )

        assert status == ship.EXIT_INTERRUPTED
        assert started[0].wait(timeout=5) == -signal.SIGTERM
        assert_nothing_left(app, canon, workspaces)
    finally:
        for process in started:
            process.kill()
            process.wait()


def test_processes_are_read_from_proc_where_there_is_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Linux's way (CI's containers may have no ps), checked on any machine.
    workspace, app, elsewhere = make_workspace(tmp_path)
    proc = tmp_path / "proc"

    def fake_process(
        name: str,
        state: str,
        cwd: Path,
        *arguments: str,
        parent: int = 1,
        terminal: int = 0,
        session: int = 1,
    ) -> None:
        entry = proc / name
        entry.mkdir(parents=True)
        (entry / "stat").write_text(
            f"{name} (a (strange) name) {state} {parent} 1 {session} {terminal} 0\n"
        )
        (entry / "cmdline").write_bytes(
            b"".join(f"{argument}\0".encode() for argument in arguments)
        )
        (entry / "cwd").symlink_to(cwd)

    # Above Linux's largest process id, so no real process is one of these;
    # except ship's parent and grandparent, whose command lines name the
    # workspace and which must not be found.
    fake_process("self", "R", elsewhere, "python")
    fake_process("5000100", "S", app, "sleep", "300")
    fake_process("5000101", "Z", app, "sleep", "300")
    fake_process("5000102", "S", elsewhere, "python", str(app / "serve.py"))
    fake_process("5000103", "S", elsewhere, "sleep", "300")
    fake_process("5000104", "S", app, "-zsh", terminal=34816, session=5000104)
    fake_process(str(os.getppid()), "S", app, "bash", str(app), parent=5000200)
    fake_process("5000200", "S", app, "bash", str(app))
    monkeypatch.setattr(ship, "PROC", proc)

    found = ship.processes_in(workspace)

    assert found == {
        5000100: ship.RunningProcess("sleep 300", str(app), 1, False, session_id=1),
        5000102: ship.RunningProcess(
            f"python {app / 'serve.py'}", str(elsewhere), 1, False, session_id=1
        ),
        5000104: ship.RunningProcess("-zsh", str(app), 1, True, session_id=5000104),
    }


def test_a_killed_ships_processes_are_stopped_by_the_next(
    repositories, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A ship killed outright runs no cleanup; the next one finds what it left.
    app, canon, workspaces, *_ = repositories
    stale = workspaces / f"{ship.WORKSPACE_PREFIX}killed" / "app"
    stale.parent.mkdir()
    git(app, "worktree", "add", "--quiet", "--detach", str(stale), "HEAD")
    left_running = start_detached(["sleep", "300"], cwd=stale)
    stand_in(monkeypatch)
    try:
        ship.ship(
            repository_root=app, canon_checkout=canon, temporary_parent=workspaces
        )

        assert left_running.wait(timeout=5) == -signal.SIGTERM
        assert_nothing_left(app, canon, workspaces)
    finally:
        left_running.kill()
        left_running.wait()


def test_a_workspace_is_named_only_by_a_whole_path(tmp_path: Path) -> None:
    workspace, *_ = make_workspace(tmp_path)
    pattern = ship.workspace_pattern(workspace)

    for named in (
        f"{workspace}",
        f"python {workspace}/app/serve.py",
        f"server --root={workspace}/app",
        f"bash -c '{workspace}/app/run dev'",
        f'"{workspace}"',
    ):
        assert pattern.search(named), named
    for not_named in (
        f"tail /other{workspace}/app/log",
        f"ls {workspace}-other/app",
        f"ls {workspace}x",
    ):
        assert not pattern.search(not_named), not_named


def test_a_workspace_named_through_a_link_is_found_where_it_resolves(
    tmp_path: Path,
) -> None:
    # On macOS ship names /tmp/tarmar-ship-..., and the kernel reports the
    # working directory of what runs there as /private/tmp/tarmar-ship-...
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    workspace, app, _ = make_workspace(real_parent)
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent)
    running = start_detached(["sleep", "300"], cwd=app)
    try:
        found = ship.processes_in(linked_parent / workspace.name)

        assert list(found) == [running.pid]
        assert found[running.pid].working_directory == str(app)
    finally:
        running.kill()
        running.wait()


def test_cleanup_never_stops_what_started_ship(tmp_path: Path) -> None:
    # A shell whose command names the workspace (ls it, then ./run ship).
    workspace, *_ = make_workspace(tmp_path)
    ship_code = (
        f"import sys; sys.path.insert(0, {str(Path(ship.__file__).parent)!r}); "
        "import ship; from pathlib import Path; "
        f"ship.stop_processes_in(Path({str(workspace)!r})); print('survived')"
    )
    shell_command = (
        f": {shlex.quote(str(workspace))}; "
        f"{shlex.quote(sys.executable)} -c {shlex.quote(ship_code)}; exit $?"
    )

    finished = subprocess.run(
        ["bash", "-c", shell_command], capture_output=True, text=True, timeout=60
    )

    assert finished.returncode == 0, finished.stderr
    assert "survived" in finished.stdout


def test_cleanup_reports_a_process_with_a_terminal_and_leaves_it(
    tmp_path: Path,
) -> None:
    # Someone's shell, cd'd into the workspace: stopping it would close their
    # terminal.
    workspace, app, _ = make_workspace(tmp_path)
    controller, terminal = os.openpty()

    def take_the_terminal() -> None:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    shell = subprocess.Popen(
        ["sleep", "300"],
        cwd=app,
        stdin=terminal,
        stdout=terminal,
        stderr=terminal,
        start_new_session=True,
        preexec_fn=take_the_terminal,
    )
    try:
        assert ship.processes_in(workspace)[shell.pid].has_terminal

        with pytest.raises(ship.ShipError, match=rf"{shell.pid} .*it has a terminal"):
            ship.stop_processes_in(workspace)

        assert shell.poll() is None
    finally:
        shell.kill()
        shell.wait()
        os.close(controller)
        os.close(terminal)


def test_an_interrupted_gate_leaves_its_detached_server_to_the_cleanup(
    tmp_path: Path,
) -> None:
    # The real run_gate, interrupted while its stand-in gate runs a server the
    # way Playwright does. Stopping the gate's group misses the server (the
    # cause of issue #31); the workspace cleanup stops it.
    workspace, app, _ = make_workspace(tmp_path)
    server_id_file = app / "server.pid"
    (app / "start_server.py").write_text(
        "import pathlib, subprocess\n"
        "server = subprocess.Popen(['sleep', '300'], start_new_session=True)\n"
        f"pathlib.Path({str(server_id_file)!r}).write_text(str(server.pid))\n",
        encoding="utf-8",
    )
    write_script(
        app, "run", f"{shlex.quote(sys.executable)} start_server.py\nexec sleep 300"
    )

    def both_sleeps_run() -> bool:
        # The server has recorded itself and start_server.py has exited, so
        # all that runs is the server and the gate's own sleep.
        found = ship.processes_in(workspace)
        return server_id_file.exists() and [
            process.command for process in found.values()
        ] == ["sleep 300", "sleep 300"]

    gate_returned = threading.Event()

    def interrupt_once_both_run() -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not both_sleeps_run():
            if gate_returned.wait(0.1):
                return
        if not gate_returned.is_set():
            os.kill(os.getpid(), signal.SIGUSR1)

    previous = signal.signal(signal.SIGUSR1, ship.raise_interrupted)
    interrupter = threading.Thread(target=interrupt_once_both_run, daemon=True)
    try:
        interrupter.start()
        with pytest.raises(ship.Interrupted):
            try:
                ship.run_gate(app, dict(os.environ), deploy=True)
            finally:
                gate_returned.set()
        server_id = int(server_id_file.read_text())

        assert list(ship.processes_in(workspace)) == [server_id]
        ship.stop_processes_in(workspace)
        assert ship.processes_in(workspace) == {}
    finally:
        # The thread has signalled or never will before the handler goes, so
        # a late SIGUSR1 cannot reach the default handler and end the worker.
        gate_returned.set()
        interrupter.join()
        signal.signal(signal.SIGUSR1, previous)
        if server_id_file.exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(server_id_file.read_text()), signal.SIGKILL)


def test_cleanup_stops_its_own_sessions_terminal_holders_but_not_others(
    tmp_path: Path,
) -> None:
    # Ship run from a terminal: what uv sync or npm ci started shares that
    # terminal and is ship's to stop. A shell in another terminal is not.
    workspace, app, elsewhere = make_workspace(tmp_path)
    ships_controller, ships_terminal = os.openpty()
    others_controller, others_terminal = os.openpty()

    def take_the_terminal() -> None:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    others_shell = subprocess.Popen(
        ["sleep", "300"],
        cwd=app,
        stdin=others_terminal,
        start_new_session=True,
        preexec_fn=take_the_terminal,
    )
    ship_code = f"""
import json, subprocess, sys
sys.path.insert(0, {str(Path(ship.__file__).parent)!r})
import ship
from pathlib import Path
workspace = Path({str(workspace)!r})
install_script = subprocess.Popen(["sleep", "300"], cwd=workspace / "app")
seen = ship.processes_in(workspace)[install_script.pid]
try:
    ship.stop_processes_in(workspace)
    reported = ""
except ship.ShipError as failure:
    reported = str(failure)
print(json.dumps({{
    "install_script": install_script.pid,
    "install_script_has_terminal": seen.has_terminal,
    "install_script_exit": install_script.wait(timeout=5),
    "reported": reported,
}}))
"""
    # Ship in a session of its own, with its own terminal.
    shipping = subprocess.run(
        [sys.executable, "-c", ship_code],
        cwd=elsewhere,
        stdin=ships_terminal,
        capture_output=True,
        text=True,
        start_new_session=True,
        preexec_fn=take_the_terminal,
        timeout=60,
    )
    try:
        assert shipping.returncode == 0, shipping.stderr
        outcome = json.loads(shipping.stdout.splitlines()[-1])
        assert outcome["install_script_has_terminal"]
        assert outcome["install_script_exit"] == -signal.SIGTERM
        assert (
            f"{others_shell.pid} sleep 300 (it has a terminal)" in outcome["reported"]
        )
        assert str(outcome["install_script"]) not in outcome["reported"]
        assert others_shell.poll() is None
    finally:
        others_shell.kill()
        others_shell.wait()
        for descriptor in (
            ships_controller,
            ships_terminal,
            others_controller,
            others_terminal,
        ):
            os.close(descriptor)


def test_a_session_ship_may_not_read_is_not_taken_for_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # getsid can refuse; the process is then reported, not signalled, and the
    # refusal does not escape the cleanup (or remove_stale_worktrees).
    workspace, app, _ = make_workspace(tmp_path)
    listings = {
        "lsof": f"p5000100\nfcwd\nn{app}\np5000101\nfcwd\nn{app}\n",
        "ps": "5000100 1 Ss ttys009 -zsh\n5000101 1 S ?? sleep 300\n",
    }
    monkeypatch.setattr(
        ship, "run_quietly", lambda command, *, cwd=None: listings[command[0]]
    )
    real_getsid = os.getsid

    def getsid(process_id: int) -> int:
        if process_id == 5000100:
            raise PermissionError(1, "Operation not permitted")
        return 7 if process_id == 5000101 else real_getsid(process_id)

    monkeypatch.setattr(ship.os, "getsid", getsid)
    monkeypatch.setattr(ship, "PROC", tmp_path / "no-proc")

    assert ship.processes_from_ps() == {
        5000100: ship.RunningProcess("-zsh", str(app), 1, True, session_id=None),
        5000101: ship.RunningProcess("sleep 300", str(app), 1, False, session_id=7),
    }
    listings["ps"] = "5000100 1 Ss ttys009 -zsh\n"
    with pytest.raises(ship.ShipError, match=r"5000100 -zsh \(it has a terminal\)"):
        ship.stop_processes_in(workspace)
