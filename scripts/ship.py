#!/usr/bin/env python
"""Deploy the head of main, completely, in one command: ``./run ship``.

A deploy needs the suites run on the exact commit at the head of main, with
the Tarmar canon at the head of its own main, and a record of the pass pushed
for deploy.sh (issue #576); the gate then starts the Deploy workflow (Spencer,
2026-10-10). Doing that by hand meant a clean checkout of main, a provisioned
environment, the canon checkout brought up to date, the right
TARMAR_CANON_DIR, and watching the run the gate started. This does all of it:

1. checks gh is logged in, and clears away anything an earlier, killed ship
   left behind;
2. fetches main here and in the canon checkout;
3. makes a detached worktree of each at their heads, in a temporary
   directory, so no checkout anyone is working in is touched;
4. provisions the app worktree (uv sync, npm ci);
5. runs ``./run gate`` there against the canon worktree, which records the
   pass and starts Deploy;
6. follows that Deploy run to the end, then reads its log for what it did:
   deployed, already up to date, or stood down because main moved on;
7. removes both worktrees and the temporary directory, whatever happened,
   including Ctrl-C or a closed terminal.

Exit status: 0 when the commit is live (deployed now, or already); 1 when
nothing was deployed (a check failed, the gate failed, Deploy failed or stood
down); 3 when the deploy result stands but something could not be removed;
130 when interrupted. ``--rehearse`` does everything but start Deploy: the
full gate runs and its pass is recorded, and production is not touched.
"""

import fcntl
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import gate

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CANON_DIR_ENV = "TARMAR_CANON_DIR"
# Where the canon checkout lives on the Mac unless TARMAR_CANON_DIR says.
DEFAULT_CANON_CHECKOUT = Path.home() / "dev" / "tarmar-canon"
BRANCH = gate.DEPLOY_BRANCH
WORKSPACE_PREFIX = "tarmar-ship-"
# Held for the whole of a ship; a workspace whose lock can be taken belongs to
# a ship that is no longer running.
LOCK_NAME = ".ship.lock"
# Short, so the sockets Chromium makes under TMPDIR stay within macOS's
# 104-character limit on socket paths.
DEFAULT_TEMPORARY_PARENT = Path("/tmp")
# The run's log can lag the end of the run: how often, and how far apart, to
# ask for it again before giving up.
LOG_ATTEMPTS = 6
LOG_RETRY_SECONDS = 5
# What the gate announces when it has started Deploy (gate.start_deploy_if_head);
# the run's address follows when gh printed one.
DEPLOY_STARTED_PATTERN = re.compile(
    rf"started {re.escape(gate.DEPLOY_WORKFLOW)} for ([0-9a-f]{{40}})"
    r"(?:: \S*/actions/runs/(\d+))?"
)
# What deploy.sh logs for each way a Deploy run can end green.
DEPLOYED = "Deployment complete!"
ALREADY_LIVE = "Already up to date."
STOOD_DOWN = "Nothing to do."

EXIT_LIVE = 0
EXIT_NOT_DEPLOYED = 1
EXIT_LEFT_BEHIND = 3
EXIT_INTERRUPTED = 130


class ShipError(Exception):
    """A step of the ship could not be completed; nothing more is deployed."""


class Interrupted(Exception):
    """A signal asked the ship to stop."""


def announce(message: str) -> None:
    """Print a progress line, flushed so it interleaves with the steps."""
    print(f"[ship] {message}", flush=True)


def run_quietly(command: Sequence[str], *, cwd: Path | None = None) -> str:
    """Run ``command`` and return its stdout, stripped.

    Raises:
        ShipError: the command failed or is not installed.
    """
    try:
        completed = subprocess.run(
            list(command), cwd=cwd, capture_output=True, text=True, check=True
        )
    except FileNotFoundError as failure:
        raise ShipError(f"{command[0]} is not installed") from failure
    except subprocess.CalledProcessError as failure:
        raise ShipError(
            f"{' '.join(command)} failed: {(failure.stderr or failure.stdout).strip()}"
        ) from failure
    return completed.stdout.strip()


def git(repository: Path, *arguments: str) -> str:
    """Run git in ``repository`` and return its stdout, stripped."""
    return run_quietly(["git", "-C", str(repository), *arguments])


def head_of_main(repository: Path) -> str:
    """Fetch ``repository``'s origin and return the sha of origin's main."""
    git(repository, "fetch", "--quiet", "origin", BRANCH)
    return git(repository, "rev-parse", f"origin/{BRANCH}")


def add_worktree(repository: Path, path: Path, commit_sha: str) -> None:
    """Check ``commit_sha`` out, detached, as a new worktree at ``path``."""
    git(repository, "worktree", "add", "--quiet", "--detach", str(path), commit_sha)


def remove_worktree(repository: Path, path: Path) -> None:
    """Remove the worktree at ``path`` from ``repository``, however it stands.

    ``git worktree remove`` first; if that fails, delete the directory and let
    ``git worktree prune`` forget it.

    Raises:
        ShipError: the worktree is still there.
    """
    if path.exists():
        try:
            git(repository, "worktree", "remove", "--force", str(path))
        except ShipError:
            shutil.rmtree(path, ignore_errors=True)
    git(repository, "worktree", "prune")
    if path.exists():
        raise ShipError(f"could not remove {path}")


def workspace_is_in_use(workspace: Path) -> bool:
    """Whether a running ship holds ``workspace``'s lock."""
    lock_path = workspace / LOCK_NAME
    if not lock_path.exists():
        return False
    with lock_path.open() as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock_file, fcntl.LOCK_UN)
    return False


def remove_stale_worktrees(repository: Path) -> None:
    """Remove worktrees an earlier ship left in ``repository`` when it was killed.

    A workspace another ship is still using (it holds the lock) is left alone.
    """
    listing = git(repository, "worktree", "list", "--porcelain")
    for line in listing.splitlines():
        if not line.startswith("worktree "):
            continue
        path = Path(line.removeprefix("worktree "))
        workspace = path.parent
        if not workspace.name.startswith(WORKSPACE_PREFIX):
            continue
        if workspace_is_in_use(workspace):
            announce(f"leaving {path}: another ship is using it")
            continue
        announce(f"removing {path}, left by an earlier ship")
        remove_worktree(repository, path)
        shutil.rmtree(workspace, ignore_errors=True)


def run_step(command: Sequence[str], *, cwd: Path, environment: dict[str, str]) -> None:
    """Run one provisioning step, its output shown as it goes.

    Raises:
        ShipError: the step failed or its program is not installed.
    """
    announce(" ".join(command))
    try:
        completed = subprocess.run(list(command), cwd=cwd, env=environment, check=False)
    except FileNotFoundError as failure:
        raise ShipError(f"{command[0]} is not installed") from failure
    if completed.returncode != 0:
        raise ShipError(f"{' '.join(command)} failed (exit {completed.returncode})")


def find_deploy_run(commit_sha: str, app: Path) -> str:
    """The newest Deploy run started by hand for ``commit_sha``, when gh printed none.

    Raises:
        ShipError: there is none.
    """
    found = run_quietly(
        [
            "gh",
            "run",
            "list",
            "--workflow",
            gate.DEPLOY_WORKFLOW,
            "--event",
            "workflow_dispatch",
            "--commit",
            commit_sha,
            "--limit",
            "1",
            "--json",
            "databaseId",
            "--jq",
            ".[0].databaseId",
        ],
        cwd=app,
    )
    if not found or found == "null":
        raise ShipError(
            f"the gate started Deploy for {commit_sha} but its run cannot be found"
        )
    return found


def run_gate(app: Path, environment: dict[str, str], *, deploy: bool) -> str:
    """Run the gate in ``app``, echoing its output; return the Deploy run id.

    Returns an empty string when ``deploy`` is False. If interrupted, the gate
    and everything it started are stopped before the interruption goes on.

    Raises:
        ShipError: the gate failed, or passed without starting Deploy.
    """
    command = ["./run", "gate", *([] if deploy else ["--no-deploy"])]
    announce(" ".join(command))
    process = subprocess.Popen(
        command,
        cwd=app,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    started_for = ""
    run_id = ""
    try:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            match = DEPLOY_STARTED_PATTERN.search(line)
            if match:
                started_for, run_id = match.group(1), match.group(2) or ""
        process.wait()
    except BaseException:
        # Ctrl-C reaches the gate's process group and is forwarded again by
        # uv, so a second one arrives here; it must not cut the shutdown short.
        held = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            stop_process_group(process)
        finally:
            signal.signal(signal.SIGINT, held)
        raise
    if process.returncode != 0:
        raise ShipError(
            f"the gate failed (exit {process.returncode}); nothing deployed"
        )
    if not deploy:
        return ""
    if not started_for:
        raise ShipError(
            "the gate passed but started no Deploy (see its last line: main may "
            "have moved while it ran); run ./run ship again"
        )
    return run_id or find_deploy_run(started_for, app)


def group_is_gone(group: int) -> bool:
    """Whether no process is left in process group ``group``."""
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def stop_process_group(process: subprocess.Popen[str]) -> None:
    """Stop ``process`` and everything it started, giving it a chance to tidy up.

    Waits for the whole group, not just ``process``: the gate's children
    (Playwright, a dev server) can still be finishing after it exits, and the
    cleanup that follows must not delete files under them.
    """
    group = os.getpgid(process.pid)
    if process.poll() is None:
        os.killpg(group, signal.SIGTERM)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(group, signal.SIGKILL)
            process.wait()
    # Ten seconds to finish, then SIGKILL; past thirty, report and go on (an
    # exited process not yet reaped cannot be killed, so this must not spin).
    started = time.monotonic()
    killed = False
    while not group_is_gone(group):
        waited = time.monotonic() - started
        if waited > 30:
            announce(f"process group {group} is still there after 30 s; going on")
            return
        if waited > 10 and not killed:
            os.killpg(group, signal.SIGKILL)
            killed = True
        time.sleep(0.1)


def follow_deploy(run_id: str, app: Path) -> str:
    """Follow the Deploy run to its end and say what it did.

    Returns:
        DEPLOYED or ALREADY_LIVE.

    Raises:
        ShipError: the run failed, or stood down because main moved on.
    """
    announce(f"following Deploy run {run_id}")
    completed = subprocess.run(
        ["gh", "run", "watch", run_id, "--exit-status", "--interval", "30"],
        cwd=app,
        check=False,
    )
    if completed.returncode != 0:
        raise ShipError(f"Deploy run {run_id} did not finish green")
    for attempt in range(LOG_ATTEMPTS):
        try:
            log = run_quietly(["gh", "run", "view", run_id, "--log"], cwd=app)
        except ShipError:
            log = ""
        for outcome in (DEPLOYED, ALREADY_LIVE):
            if outcome in log:
                return outcome
        if STOOD_DOWN in log:
            moved = re.search(r"main moved past \S+ to \S+[^\n]*", log)
            raise ShipError(
                f"Deploy stood down: {moved.group(0) if moved else 'main moved on'}; "
                "run ./run ship again to deploy the new head"
            )
        if attempt < LOG_ATTEMPTS - 1:
            time.sleep(LOG_RETRY_SECONDS)
    raise ShipError(
        f"Deploy run {run_id} finished green, but its log says neither what it "
        "deployed nor why not"
    )


def raise_interrupted(signal_number: int, frame: object) -> None:
    """Signal handler: turn SIGTERM or SIGHUP into Interrupted, so cleanup runs."""
    raise Interrupted(signal.Signals(signal_number).name)


def clean_up(steps: Sequence[tuple[str, Callable[[], None]]]) -> list[str]:
    """Run every cleanup step, each on its own, with interrupts held off.

    Returns:
        What could not be removed, empty when everything was.
    """
    held = {
        number: signal.signal(number, signal.SIG_IGN)
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    }
    left_behind: list[str] = []
    try:
        for description, step in steps:
            try:
                step()
            except (ShipError, OSError) as failure:
                left_behind.append(f"{description}: {failure}")
    finally:
        for number, handler in held.items():
            signal.signal(number, handler)
    return left_behind


def ship(
    *,
    repository_root: Path,
    canon_checkout: Path,
    deploy: bool = True,
    temporary_parent: Path = DEFAULT_TEMPORARY_PARENT,
) -> int:
    """Gate and deploy the head of main; see the module docstring.

    Args:
        repository_root: A checkout of this repository (any branch, any state).
        canon_checkout: A clone of smarks/tarmar-canon (any branch, any state).
        deploy: False to stop after the gate, which then records but does not
            start Deploy.
        temporary_parent: Where to make the temporary directory.

    Returns:
        A process exit status (see the module docstring).
    """
    workspace = Path(tempfile.mkdtemp(prefix=WORKSPACE_PREFIX, dir=temporary_parent))
    lock_file = (workspace / LOCK_NAME).open("w")
    fcntl.flock(lock_file, fcntl.LOCK_EX)
    app = workspace / "app"
    canon = workspace / "canon"
    run_id = ""
    status = EXIT_NOT_DEPLOYED
    previous = {
        number: signal.signal(number, raise_interrupted)
        for number in (signal.SIGTERM, signal.SIGHUP)
    }
    try:
        for repository in (repository_root, canon_checkout):
            remove_stale_worktrees(repository)
        if deploy:
            run_quietly(["gh", "auth", "status"])
        commit_sha = head_of_main(repository_root)
        canon_sha = head_of_main(canon_checkout)
        announce(
            f"{'shipping' if deploy else 'gating'} {commit_sha} with canon {canon_sha}"
        )
        add_worktree(repository_root, app, commit_sha)
        add_worktree(canon_checkout, canon, canon_sha)
        scratch = workspace / "tmp"
        scratch.mkdir()
        environment = {
            name: value for name, value in os.environ.items() if name != "VIRTUAL_ENV"
        }
        # The canon at its head, and the suites' temporary files inside the
        # workspace, so they go when it does (issue #987).
        environment.update({CANON_DIR_ENV: str(canon), "TMPDIR": str(scratch)})
        run_step(["uv", "sync", "--locked"], cwd=app, environment=environment)
        run_step(
            ["npm", "ci", "--prefer-offline", "--no-audit", "--no-fund"],
            cwd=app,
            environment=environment,
        )
        run_id = run_gate(app, environment, deploy=deploy)
        if not deploy:
            announce(f"rehearsal passed: gate recorded {commit_sha}; nothing deployed")
            status = EXIT_LIVE
        else:
            outcome = follow_deploy(run_id, app)
            announce(
                f"{commit_sha} is live"
                if outcome == DEPLOYED
                else f"{commit_sha} was already live; Deploy run {run_id} had nothing to change"
            )
            status = EXIT_LIVE
    except ShipError as failure:
        announce(f"stopped: {failure}")
        status = EXIT_NOT_DEPLOYED
    except (KeyboardInterrupt, Interrupted) as interruption:
        announce(f"interrupted ({interruption or 'Ctrl-C'})")
        if run_id:
            announce(
                f"Deploy run {run_id} was already started and carries on without this"
            )
        status = EXIT_INTERRUPTED
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        left_behind = clean_up(
            [
                ("app worktree", lambda: remove_worktree(repository_root, app)),
                ("canon worktree", lambda: remove_worktree(canon_checkout, canon)),
                ("lock", lock_file.close),
                ("workspace", lambda: shutil.rmtree(workspace)),
            ]
        )
    if left_behind:
        for leftover in left_behind:
            announce(f"left behind: {leftover}")
        return EXIT_LEFT_BEHIND if status == EXIT_LIVE else status
    announce(f"removed {workspace}")
    return status


def main(argv: Sequence[str]) -> int:
    """Entry point.

    Args:
        argv: Arguments after the program name: nothing, or ``--rehearse``.

    Returns:
        A process exit status.
    """
    if argv not in ([], ["--rehearse"]):
        print(
            "usage: ./run ship [--rehearse]  (TARMAR_CANON_DIR names another canon checkout)"
        )
        return 2
    canon_checkout = Path(os.environ.get(CANON_DIR_ENV) or DEFAULT_CANON_CHECKOUT)
    if not (canon_checkout / ".git").exists():
        print(
            f"[ship] no canon checkout at {canon_checkout}; clone smarks/tarmar-canon "
            f"there or set {CANON_DIR_ENV}"
        )
        return 2
    return ship(
        repository_root=REPOSITORY_ROOT,
        canon_checkout=canon_checkout,
        deploy=argv != ["--rehearse"],
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
