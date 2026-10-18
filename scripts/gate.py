#!/usr/bin/env python
"""The local gate: run every suite a deploy depends on, then record the commit.

Nothing checks a commit between merge and production (issue #576). Both GitHub
workflows are off for billing, Jest runs nowhere at all, and the commit hook's
Playwright step is routinely skipped with ``SKIP=playwright-e2e``, so "green"
means whatever the committer happened to run. ``./run gate`` runs the whole set
on one machine in one pass and, when every step passes, records the commit the
suites ran on as a git note under ``refs/notes/gate``, pushed to origin.

That note is the contract with ``deploy.sh``: it fetches the same ref on
datahorde and refuses to deploy an ``origin/main`` commit that has no note
unless it is given ``--force``. A note on a commit therefore has to mean that
every step below passed on that exact tree, which is why the gate refuses to
record a dirty working tree, and refuses again if HEAD moved or the tree was
touched while the suites ran.

The suites run in two modes (issue #920). The first is the checkout as it
stands: whatever canon it holds, or the one the caller's ``TARMAR_CANON_DIR``
names, passed through unchanged so an external canon can be gated. The second
always sets ``TARMAR_CANON_DIR`` to ``reference/default_canon``, the stand-in
canon the public repository ships, so a commit is recorded only if the
configuration a checkout without the private canon runs in passes too. Ruff,
pyright and the migration check do not read the canon and run once. So does the
guide check, in the first mode: ``build/`` holds only the player-facing guides,
the same under any canon, and under the Tarmar canon it also checks the Game
Master's guides in that canon's ``guides/`` (issue #921). Since #921 the
checkout holds no canon, so the first mode gates the Tarmar canon only when the
caller's ``TARMAR_CANON_DIR`` names it. pytest, Jest and Playwright run again
as "pytest (default canon)" and so on, on a second e2e port. There is no way
to skip the second mode: a note must not mean less on one machine than on
another.

The note names the canon the first mode ran against and, when that canon is a
git checkout of its own (the Tarmar canon, cloned from ``smarks/tarmar-canon``),
the commit it was at (``canon-commit:``); a canon checkout with uncommitted
changes is refused like a dirty tree. ``--check`` accepts only a note whose
first mode ran the Tarmar canon, since production serves that one, and
``deploy.sh`` compares ``canon-commit:`` with the canon it pulls.

Why a git note rather than a file or a tag: ``deploy.sh`` must not trust
anything in the working tree on the host (a stale or hand-edited file would
wave a deploy through), a note is keyed by commit so it cannot drift onto the
wrong SHA, and one ref holds every record instead of leaving a tag per commit
in every clone at roughly fifty merges a day.

Run it as ``./run gate`` (the wrapper sets the Homebrew dylib path weasyprint
needs on macOS); running this file directly builds the same environment.
"""

import argparse
import codecs
import hashlib
import importlib.machinery
import importlib.util
import os
import select
import socket
import subprocess
import sys
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Run from anywhere: the repo root has to go on sys.path before this file can
# import from it, the same bootstrap scripts/build_guides.py does.
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from reference.paths import (  # noqa: E402
    CANON_DIR_ENV,
    DEFAULT_CANON_DIR,
    TARMAR_CANON_NAME,
    resolve_canon,
)

RUN_SCRIPT = REPOSITORY_ROOT / "run"

# refs/notes/gate holds the records; `git notes --ref=gate` names the same ref.
NOTES_REF_NAME = "gate"
GATE_NOTES_REF = f"refs/notes/{NOTES_REF_NAME}"
DEFAULT_REMOTE = "origin"
# The workflow a passing gate starts, and the branch it deploys (Spencer,
# 2026-10-10). Deploy no longer starts on every push to main: it refused each
# one, because the merge commit had no record yet, and showed red on every
# merge. Now the gate that writes the record starts it.
# By file, which a rename of the workflow's display name does not break.
DEPLOY_WORKFLOW = "deploy.yml"
DEPLOY_BRANCH = "main"

# Playwright defaults to port 8000, which is also the dev server's. The gate
# takes a port of its own so a dev server left running does not answer the
# suite's requests, and so a gate run and a hand-run suite do not collide.
# Since #966 this is where the search for a free port begins (E2E_PORT_SPAN).
DEFAULT_E2E_PORT = "8390"

# The second mode's browser suite gets a port of its own as well. Playwright
# starts a fake Ollama at the app's port + 1000, so a port is only free when
# neither it nor its +1000 is in use by the other mode.
DEFAULT_CANON_E2E_PORT = "8391"
FAKE_OLLAMA_PORT_OFFSET = 1000

# Two sessions' gates on the same fixed ports tested each other's code
# (issue #966): Playwright reused the other worktree's dev server. A port the
# caller does not name is picked free instead, from this many ports upward of
# DEFAULT_E2E_PORT. The search starts at an offset derived from the checkout's
# path, so two worktrees starting at once do not reach for the same pair, and
# moves upward past any port, or +1000 port, that something is listening on.
E2E_PORT_SPAN = 200

# The stand-in canon is ``reference.paths.DEFAULT_CANON_DIR``, an absolute path
# (the suites start servers and child processes in other working directories).
DEFAULT_CANON_SUFFIX = " (default canon)"

# ``canon-commit:`` for a canon that is not a git checkout of its own.
CANON_COMMIT_NONE = "none"

# How much of a failing step's output its failure report repeats (issue #36).
# A step's whole output still goes to the terminal or log as it runs; the
# report repeats the end of it beside the exit code, because that is where a
# check says what failed. A canon loader's refusal under makemigrations is
# five lines (SystemCheckError, a blank, ERRORS:, the error naming the file
# and the key, its HINT); forty leaves room for pytest's short summary or
# Playwright's list of failures below a traceback.
FAILED_STEP_TAIL_LINES = 40

# Every step writes into the gate's pipe (issue #36), and Python block-buffers
# stdout to a pipe while stderr stays line-buffered, so a Python step's output
# would arrive late and out of order. Unbuffered, it arrives as written.
UNBUFFERED_PYTHON = {"PYTHONUNBUFFERED": "1"}

# A step's output is read until every process holding the pipe has closed it,
# so that what its children print while they shut down (after Ctrl-C, say)
# still comes through. A process the step left running in the background
# holds the pipe too, and would keep the gate waiting for as long as it runs.
# So once the step itself has exited, the gate stops reading when the pipe has
# been quiet this long, whoever still holds it.
STRAY_OUTPUT_GRACE_SECONDS = 2.0
# A leftover process that keeps printing never lets the pipe go quiet, so
# reading also stops this long after the step exited, whatever still arrives.
STRAY_OUTPUT_CAP_SECONDS = 30.0
# While the step runs and prints nothing, how often the gate looks up from
# the pipe to see whether it has exited. It waits in select() between looks.
STEP_EXIT_CHECK_SECONDS = 0.5
# The most one read takes from the pipe.
OUTPUT_READ_BYTES = 65536


class GateError(Exception):
    """A gate run could not be completed or recorded."""


@dataclass(frozen=True)
class GateStep:
    """One check in the gate, and the environment it needs."""

    name: str
    command: list[str]
    environment: dict[str, str] = field(default_factory=dict)
    # The canon directory ``TARMAR_CANON_DIR`` names for this step, or None to
    # leave the variable unset. Kept apart from ``environment`` so the base
    # environment stays built in one place, by ``build_step_environment``.
    canon_dir: Path | None = None


@dataclass(frozen=True)
class StepResult:
    """How one gate step ended."""

    step: GateStep
    returncode: int
    seconds: float
    # The last FAILED_STEP_TAIL_LINES lines the step printed, stdout and
    # stderr together, without their line endings.
    output_tail: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """Whether the step exited zero."""
        return self.returncode == 0


def claimed_ports(e2e_port: str) -> tuple[int, int]:
    """The ports one Playwright run listens on: the app's and the fake Ollama's."""
    return int(e2e_port), int(e2e_port) + FAKE_OLLAMA_PORT_OFFSET


def port_listening(port: int) -> bool:
    """Whether something accepts connections on ``port`` at 127.0.0.1.

    That is what Playwright asks before it reuses a server, so it is the
    question that decides whether a run would test another checkout's code.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def busy_ports(e2e_ports: Sequence[str]) -> list[int]:
    """Every port these Playwright runs would claim that is already taken.

    Args:
        e2e_ports: The app ports; each also claims its fake Ollama's.

    Returns:
        The taken ports, app and fake Ollama alike, in order.
    """
    return [
        port
        for e2e_port in e2e_ports
        for port in claimed_ports(e2e_port)
        if port_listening(port)
    ]


def first_e2e_port_for(repository_root: Path) -> int:
    """Where this checkout's search for free ports starts (issue #966).

    The same checkout starts at the same port each run, and two checkouts
    usually start at different ones, so two gates begun together seldom
    probe the same pair.
    """
    digest = hashlib.sha256(str(repository_root.resolve()).encode()).digest()
    return int(DEFAULT_E2E_PORT) + int.from_bytes(digest[:4], "big") % E2E_PORT_SPAN


def choose_e2e_ports(
    repository_root: Path,
    *,
    e2e_port: str | None,
    default_canon_e2e_port: str | None,
) -> tuple[str, str]:
    """The two modes' browser ports: as the caller named them, or free ones.

    A port the caller names is kept as given; :func:`run_gate` refuses it if
    it is taken. A port the caller leaves out is the first in the span,
    upward of :func:`first_e2e_port_for` and wrapping round, whose app and
    fake Ollama ports are both free and claimed by neither the other mode
    nor each other.

    Args:
        repository_root: The checkout being gated.
        e2e_port: The first mode's port, or None to pick one.
        default_canon_e2e_port: The second mode's port, or None to pick one.

    Returns:
        ``(e2e_port, default_canon_e2e_port)``.

    Raises:
        GateError: No free port was left in the span.
    """
    taken = {
        port
        for named in (e2e_port, default_canon_e2e_port)
        if named is not None
        for port in claimed_ports(named)
    }
    base = int(DEFAULT_E2E_PORT)
    start = first_e2e_port_for(repository_root) - base
    candidates = (
        base + (start + step) % E2E_PORT_SPAN for step in range(E2E_PORT_SPAN)
    )

    def free_port() -> str:
        """The next candidate no other run listens on and neither mode claims."""
        for candidate in candidates:
            ports = claimed_ports(str(candidate))
            if taken.isdisjoint(ports) and not any(map(port_listening, ports)):
                taken.update(ports)
                return str(candidate)
        raise GateError(
            f"no free e2e port from {base} to {base + E2E_PORT_SPAN - 1} (with "
            f"its + {FAKE_OLLAMA_PORT_OFFSET} free too); name one with "
            "--e2e-port / --default-canon-e2e-port"
        )

    first = e2e_port if e2e_port is not None else free_port()
    second = (
        default_canon_e2e_port if default_canon_e2e_port is not None else free_port()
    )
    return first, second


def gate_steps(*, e2e_port: str, default_canon_e2e_port: str) -> list[GateStep]:
    """Every check a deploy depends on, in the order the gate runs them.

    The order is the cheapest first: a formatting slip should not cost the
    twenty minutes the browser suite takes before it is reported. The suites
    that read the canon (pytest, Jest, Playwright) come around a second time,
    last, against the default canon.

    Args:
        e2e_port: Port the Playwright suite serves the app on.
        default_canon_e2e_port: Port for the default-canon Playwright run.

    Returns:
        The steps, in running order.

    Raises:
        GateError: The two ports, or their fake Ollama ports, would collide.
    """
    claimed_ports = [
        int(port) + offset
        for port in (e2e_port, default_canon_e2e_port)
        for offset in (0, FAKE_OLLAMA_PORT_OFFSET)
    ]
    if len(set(claimed_ports)) != len(claimed_ports):
        raise GateError(
            f"e2e ports {e2e_port} and {default_canon_e2e_port} collide (each "
            f"also claims its port + {FAKE_OLLAMA_PORT_OFFSET} for the fake Ollama)"
        )
    steps_that_run_once = [
        GateStep("ruff", ["uv", "run", "ruff", "check", "."]),
        GateStep("ruff format", ["uv", "run", "ruff", "format", "--check", "."]),
        GateStep("pyright", ["uv", "run", "pyright"]),
        GateStep(
            "makemigrations",
            [
                "uv",
                "run",
                "python",
                "manage.py",
                "makemigrations",
                "--check",
                "--dry-run",
            ],
            # DEBUG=true lets settings boot without a production .env, exactly
            # as the commit hook and deploy.sh run this check.
            {"DEBUG": "true"},
        ),
        # Once, in the first mode: build/ is the same under any canon, and a
        # Tarmar canon there brings its own guides/ into the check (#921).
        GateStep(
            "build_guides",
            ["uv", "run", "python", "scripts/build_guides.py", "--check"],
        ),
    ]
    return [
        *steps_that_run_once,
        *suite_steps(e2e_port=e2e_port, canon_dir=None),
        *suite_steps(
            e2e_port=default_canon_e2e_port,
            canon_dir=DEFAULT_CANON_DIR,
            name_suffix=DEFAULT_CANON_SUFFIX,
        ),
    ]


def suite_steps(
    *, e2e_port: str, canon_dir: Path | None, name_suffix: str = ""
) -> list[GateStep]:
    """The three suites that read the canon, for one canon.

    Args:
        e2e_port: Port the Playwright suite serves the app on.
        canon_dir: The directory ``TARMAR_CANON_DIR`` names, or None for none.
        name_suffix: Appended to each step name to tell the modes apart.

    Returns:
        The pytest, Jest and Playwright steps.
    """
    return [
        GateStep(
            f"pytest{name_suffix}", ["uv", "run", "pytest", "-q"], canon_dir=canon_dir
        ),
        GateStep(f"jest{name_suffix}", ["npx", "jest"], canon_dir=canon_dir),
        GateStep(
            f"playwright{name_suffix}",
            # --check judges JS coverage against package.json's stored
            # threshold and never rewrites or stages it (issue #576).
            [
                "uv",
                "run",
                "python",
                "scripts/update_js_coverage_threshold.py",
                "--check",
            ],
            {"E2E_PORT": e2e_port},
            canon_dir=canon_dir,
        ),
    ]


def build_step_environment(
    *, debug: bool = False, canon_dir: Path | None = None
) -> dict[str, str]:
    """The environment ``./run`` builds, loaded from the wrapper itself.

    ``run`` injects Homebrew's lib directory into ``DYLD_FALLBACK_LIBRARY_PATH``
    on macOS or weasyprint cannot dlopen libpango. Loading that function rather
    than copying it keeps one definition of the environment the suites get,
    whether they are started by ``./run gate`` or by this file directly. The
    wrapper has no ``.py`` suffix, hence the explicit loader.

    ``TARMAR_CANON_DIR`` is set to ``canon_dir`` when one is given. Otherwise
    the caller's own value, if any, is left alone, so a canon that lives
    outside the checkout can be gated.

    Args:
        debug: Passed through to the wrapper's ``build_env``.
        canon_dir: The canon directory the steps should run against, or None
            to leave ``TARMAR_CANON_DIR`` as the caller has it.

    Returns:
        The environment to hand each step.
    """
    loader = importlib.machinery.SourceFileLoader("tarmar_run_wrapper", str(RUN_SCRIPT))
    specification = importlib.util.spec_from_loader(loader.name, loader)
    if specification is None:
        raise GateError(f"could not load {RUN_SCRIPT}")
    wrapper = importlib.util.module_from_spec(specification)
    loader.exec_module(wrapper)
    environment: dict[str, str] = wrapper.build_env(debug=debug)
    if canon_dir is not None:
        environment[CANON_DIR_ENV] = str(canon_dir)
    return environment


def git_output(repository_root: Path, *arguments: str) -> str:
    """Run git in ``repository_root`` and return its stdout, stripped.

    Args:
        repository_root: The checkout to run in.
        *arguments: Arguments after ``git``.

    Returns:
        The command's standard output with surrounding whitespace removed.

    Raises:
        subprocess.CalledProcessError: git exited non-zero.
    """
    completed = subprocess.run(
        ["git", "-C", str(repository_root), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def head_commit_sha(repository_root: Path) -> str:
    """The full SHA of HEAD in ``repository_root``."""
    return git_output(repository_root, "rev-parse", "HEAD")


def uncommitted_changes(repository_root: Path) -> list[str]:
    """Porcelain lines for every changed, staged or untracked path.

    Ignored files are left out, so build artefacts the suites write do not
    count as a dirty tree.

    Args:
        repository_root: The checkout to inspect.

    Returns:
        One porcelain line per path, empty when the tree is clean.
    """
    porcelain = git_output(repository_root, "status", "--porcelain")
    return [line for line in porcelain.splitlines() if line.strip()]


def canon_checkout_root(canon_dir: Path) -> Path | None:
    """``canon_dir`` when it is the top of a git checkout of its own, else None.

    The default canon sits inside this repository, so it is not one: its
    commit is this checkout's, already in the note as ``commit:``.
    """
    completed = subprocess.run(
        ["git", "-C", str(canon_dir), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    top_level = Path(completed.stdout.strip()).resolve()
    return top_level if top_level == canon_dir.resolve() else None


def canon_commit_sha(canon_dir: Path) -> str:
    """The commit a canon checkout is at, or ``none`` when it is not one."""
    root = canon_checkout_root(canon_dir)
    if root is None:
        return CANON_COMMIT_NONE
    return git_output(root, "rev-parse", "HEAD")


def canon_uncommitted_changes(canon_dir: Path) -> list[str]:
    """``git status --porcelain`` lines of a canon checkout; none if not one."""
    root = canon_checkout_root(canon_dir)
    if root is None:
        return []
    return uncommitted_changes(root)


def parse_gate_record(record: str) -> dict[str, str]:
    """A note's ``key: value`` lines as a mapping."""
    fields: dict[str, str] = {}
    for line in record.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip()
    return fields


def gate_note_body(
    *,
    commit_sha: str,
    recorded_at: datetime,
    host: str,
    canon_name: str,
    canon_dir: Path,
    canon_commit: str,
    e2e_port: str,
    default_canon_e2e_port: str,
    step_names: Sequence[str],
) -> str:
    """The text of the note recorded against a passing commit.

    Args:
        commit_sha: The commit every step ran on.
        recorded_at: When the run finished.
        host: The machine that ran the suites.
        canon_name: Which canon the first mode ran against ("tarmar" or
            "default"), as ``reference.paths`` names it.
        canon_dir: That canon's directory.
        canon_commit: The commit that canon's own checkout was at, or
            ``none`` when it is not a checkout of its own.
        e2e_port: The port the browser suite used.
        default_canon_e2e_port: The port the default-canon browser run used.
        step_names: The steps that passed, in running order.

    Returns:
        A plain ``key: value`` block, one field per line.
    """
    return "\n".join(
        [
            "gate: pass",
            f"commit: {commit_sha}",
            f"recorded: {recorded_at.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"host: {host}",
            f"canon: {canon_name}",
            f"canon-dir: {canon_dir}",
            f"canon-commit: {canon_commit}",
            f"e2e-port: {e2e_port}",
            f"default-canon-e2e-port: {default_canon_e2e_port}",
            f"steps: {', '.join(step_names)}",
        ]
    )


def fetch_gate_notes(repository_root: Path, remote: str = DEFAULT_REMOTE) -> bool:
    """Bring the remote's gate notes into the local ref, if it has any.

    A plain ``git fetch`` does not carry ``refs/notes/*``, so both this and
    ``deploy.sh`` ask for the ref by name. A remote with no gate notes yet is
    not an error.

    Args:
        repository_root: The checkout to fetch into.
        remote: The remote to fetch from.

    Returns:
        Whether the ref was fetched.
    """
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repository_root),
            "fetch",
            "--quiet",
            remote,
            f"+{GATE_NOTES_REF}:{GATE_NOTES_REF}",
        ],
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def write_gate_note(repository_root: Path, commit_sha: str, note_body: str) -> None:
    """Attach ``note_body`` to ``commit_sha``, replacing any earlier note.

    Args:
        repository_root: The checkout holding the commit.
        commit_sha: The commit the suites ran on.
        note_body: The record, as ``gate_note_body`` writes it.

    Raises:
        subprocess.CalledProcessError: git could not write the note.
    """
    git_output(
        repository_root,
        "notes",
        f"--ref={NOTES_REF_NAME}",
        "add",
        "--force",
        "--message",
        note_body,
        commit_sha,
    )


def read_gate_record(repository_root: Path, commit_sha: str) -> str | None:
    """The gate note on ``commit_sha``, or None when it has none.

    Args:
        repository_root: The checkout to read from.
        commit_sha: The commit to look up.

    Returns:
        The note's text, or None.
    """
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repository_root),
            "notes",
            f"--ref={NOTES_REF_NAME}",
            "show",
            commit_sha,
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def push_gate_notes(
    repository_root: Path, remote: str = DEFAULT_REMOTE
) -> subprocess.CompletedProcess[str]:
    """Push the gate notes ref to ``remote``, without forcing.

    Args:
        repository_root: The checkout to push from.
        remote: The remote to push to.

    Returns:
        The completed push, for the caller to judge.
    """
    return subprocess.run(
        ["git", "-C", str(repository_root), "push", remote, GATE_NOTES_REF],
        capture_output=True,
        text=True,
    )


def record_passing_commit(
    repository_root: Path,
    commit_sha: str,
    note_body: str,
    *,
    push: bool = True,
    remote: str = DEFAULT_REMOTE,
) -> None:
    """Record ``commit_sha`` as having passed, and put the note on the remote.

    Args:
        repository_root: The checkout the suites ran in.
        commit_sha: The commit they ran on.
        note_body: The record, as ``gate_note_body`` writes it.
        push: Whether to push the ref. A local-only record is invisible to
            ``deploy.sh`` on the deploy host.
        remote: The remote to fetch from and push to.

    Raises:
        GateError: The note was written locally but could not be pushed.
    """
    if push:
        fetch_gate_notes(repository_root, remote)
    write_gate_note(repository_root, commit_sha, note_body)
    if not push:
        return
    pushed = push_gate_notes(repository_root, remote)
    if pushed.returncode != 0:
        # Another gate run moved the ref between the fetch and the push. Take
        # its version of the ref and put this note back on top: notes are keyed
        # by commit, so re-adding one loses none of the others.
        fetch_gate_notes(repository_root, remote)
        write_gate_note(repository_root, commit_sha, note_body)
        pushed = push_gate_notes(repository_root, remote)
    if pushed.returncode != 0:
        raise GateError(
            f"{commit_sha} is recorded locally but {GATE_NOTES_REF} would not "
            f"push to {remote}, so a deploy will not see it:\n"
            f"{pushed.stderr.strip()}"
        )


def remote_branch_head(
    repository_root: Path, remote: str = DEFAULT_REMOTE, branch: str = DEPLOY_BRANCH
) -> str | None:
    """The commit ``branch`` points at on ``remote`` now, or None if absent.

    Args:
        repository_root: The checkout to ask from.
        remote: The remote to ask.
        branch: The branch to look up.

    Returns:
        The full sha, or None when the remote has no such branch.

    Raises:
        subprocess.CalledProcessError: git could not reach the remote.
    """
    listed = git_output(repository_root, "ls-remote", remote, f"refs/heads/{branch}")
    return listed.split()[0] if listed else None


def dispatch_deploy_workflow(repository_root: Path, commit_sha: str) -> str:
    """Start the Deploy workflow on ``DEPLOY_BRANCH`` with gh, for ``commit_sha``.

    deploy.sh deploys whatever the branch points at when the run starts, which
    can be later than now: the run may wait behind another deploy. So it is
    told the commit this record vouches for (``--expect``), and stands down,
    green, if the branch has moved to a commit with no record of its own. gh
    picks the repository from the checkout's remote.

    Args:
        repository_root: The checkout to run gh in.
        commit_sha: The commit the record is for.

    Returns:
        What gh printed, which names the run it started.

    Raises:
        subprocess.CalledProcessError: gh refused or failed.
        FileNotFoundError: gh is not installed.
    """
    started = subprocess.run(
        [
            "gh",
            "workflow",
            "run",
            DEPLOY_WORKFLOW,
            "--ref",
            DEPLOY_BRANCH,
            "-f",
            f"command=--expect {commit_sha}",
        ],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return f"{started.stdout}{started.stderr}".strip()


def start_deploy_if_head(
    repository_root: Path, commit_sha: str, remote: str = DEFAULT_REMOTE
) -> str:
    """Start Deploy when ``commit_sha`` is still the head of the deploy branch.

    deploy.sh deploys whatever the branch points at, and refuses a commit with
    no record. So a pass on an older commit starts nothing: the newer commit's
    own gate run deploys it.

    Args:
        repository_root: The checkout the gate ran in.
        commit_sha: The commit just recorded.
        remote: The remote holding the branch.

    Returns:
        What happened, for the gate to announce.

    Raises:
        subprocess.CalledProcessError: git or gh failed.
        FileNotFoundError: gh is not installed.
    """
    head = remote_branch_head(repository_root, remote)
    if head != commit_sha:
        return (
            f"not deploying: {DEPLOY_BRANCH} is at {head or 'nothing'}, not "
            f"{commit_sha}; a gate run on that commit deploys it"
        )
    printed = dispatch_deploy_workflow(repository_root, commit_sha)
    started = f"started {DEPLOY_WORKFLOW} for {commit_sha}"
    return f"{started}: {printed}" if printed else started


def announce(message: str) -> None:
    """Print a gate progress line, flushed so it interleaves with the suites."""
    print(f"[gate] {message}", flush=True)


@dataclass
class StepOutput:
    """A step's output as read so far, echoed a line at a time.

    The state lives here rather than in :func:`echo_output`'s locals so a
    second call, after Ctrl-C interrupted the first, carries on where it
    stopped: a line half read, or lines read but not yet printed.
    """

    # The last FAILED_STEP_TAIL_LINES lines, without their endings.
    tail: deque[str] = field(
        default_factory=lambda: deque(maxlen=FAILED_STEP_TAIL_LINES)
    )
    # Whole lines read but not yet printed.
    waiting: deque[str] = field(default_factory=deque)
    # The start of a line whose end has not arrived yet.
    unfinished: str = ""
    decoder: codecs.IncrementalDecoder = field(
        default_factory=lambda: codecs.getincrementaldecoder("utf-8")(errors="replace")
    )

    def take(self, chunk: bytes) -> None:
        """Add what one read returned, printing each line it completes."""
        lines = (self.unfinished + self.decoder.decode(chunk)).split("\n")
        self.unfinished = lines.pop()
        self.waiting.extend(lines)
        self.print_waiting()

    def finish(self) -> None:
        """The pipe is done with: print whatever is left, ended or not."""
        last = self.unfinished + self.decoder.decode(b"", final=True)
        self.unfinished = ""
        if last:
            self.waiting.append(last)
        self.print_waiting()

    def print_waiting(self) -> None:
        """Print each line read and not yet printed, keeping it for the tail."""
        while self.waiting:
            line = self.waiting.popleft()
            self.tail.append(line.rstrip("\r"))
            print(line, flush=True)


def echo_output(process: subprocess.Popen[bytes], output: StepOutput) -> None:
    """Echo a step's output until the pipe closes, or the step is long gone.

    The trade-off, stated plainly: once the step itself has exited, what a
    process it left behind writes is read only while it keeps coming within
    :data:`STRAY_OUTPUT_GRACE_SECONDS` of the last output, and never past
    :data:`STRAY_OUTPUT_CAP_SECONDS` after the exit. Later output from such a
    process is not read or shown, and a process that keeps printing is cut
    off at the cap; in exchange, nothing the step leaves running can keep the
    gate waiting.

    Args:
        process: The step, with its stdout and stderr on one pipe.
        output: Where the output read so far is kept.
    """
    output.print_waiting()
    # stdout=PIPE always opens one; the assert narrows the type, as in
    # ship.run_gate.
    assert process.stdout is not None
    descriptor = process.stdout.fileno()
    exited_at: float | None = None
    while True:
        if exited_at is None and process.poll() is not None:
            exited_at = time.monotonic()
        if exited_at is None:
            timeout = STEP_EXIT_CHECK_SECONDS
        else:
            cap_left = STRAY_OUTPUT_CAP_SECONDS - (time.monotonic() - exited_at)
            if cap_left <= 0:
                # Something the step left running is still printing.
                break
            timeout = min(STRAY_OUTPUT_GRACE_SECONDS, cap_left)
        readable, _writable, _failed = select.select([descriptor], [], [], timeout)
        if not readable:
            if exited_at is not None:
                # Something the step left running still holds the pipe.
                break
            continue
        chunk = os.read(descriptor, OUTPUT_READ_BYTES)
        if not chunk:
            break
        output.take(chunk)
    output.finish()


def run_step(
    step: GateStep, *, repository_root: Path, environment: dict[str, str]
) -> StepResult:
    """Run one step to completion, echoing its output as it arrives.

    The step's stdout and stderr are read together, a line at a time, and
    every line is printed here, so the terminal or log still holds the whole
    of it; the last :data:`FAILED_STEP_TAIL_LINES` are kept as well, for the
    failure report to repeat (issue #36).

    Ctrl-C reaches the step's process group as well as the gate, so on an
    interruption the gate goes on reading until the step and everything
    sharing its output have closed it: their shutdown messages still reach
    the terminal, and none of them writes into a pipe nobody reads. A second
    interruption, or any other failure here, kills the step.

    The cost of not waiting on processes the step leaves running: after the
    step exits, their output is read only within
    :data:`STRAY_OUTPUT_GRACE_SECONDS` of quiet and at most
    :data:`STRAY_OUTPUT_CAP_SECONDS` in all (see :func:`echo_output`), so a
    leftover that prints later is not shown and one that keeps printing is
    cut off.

    Args:
        step: The step to run.
        repository_root: The checkout to run it in.
        environment: The base environment; the step's own entries win.

    Returns:
        How it ended, with the end of its output.
    """
    started = time.monotonic()
    output = StepOutput()
    with subprocess.Popen(
        step.command,
        cwd=repository_root,
        env={**environment, **UNBUFFERED_PYTHON, **step.environment},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ) as process:
        try:
            try:
                echo_output(process, output)
            except KeyboardInterrupt:
                echo_output(process, output)
                raise
            process.wait()
        except BaseException:
            process.kill()
            raise
    return StepResult(
        step=step,
        returncode=process.returncode,
        seconds=time.monotonic() - started,
        output_tail=tuple(output.tail),
    )


def failure_report(result: StepResult, commit_sha: str) -> list[str]:
    """What the gate says about a failed step: the exit and the output's end.

    Args:
        result: The failed step's result.
        commit_sha: The commit the gate was running on.

    Returns:
        The announced line, then the step's last output lines, indented.
    """
    summary = (
        f"{result.step.name} failed (exit {result.returncode}) after "
        f"{result.seconds:.0f}s; nothing recorded for {commit_sha}"
    )
    if not result.output_tail:
        return [f"{summary}; it printed nothing"]
    return [
        f"{summary}. The end of its output:",
        *(f"  {line}" for line in result.output_tail),
    ]


def refuse_busy_ports(e2e_ports: Sequence[str]) -> str:
    """Why these browser ports may not be used, or ``""`` when they may.

    A port something already listens on would have Playwright test that
    server's code instead of this checkout's (issue #966).
    """
    busy = busy_ports(e2e_ports)
    if not busy:
        return ""
    return (
        f"port {', '.join(str(port) for port in busy)} is already listening (another "
        "gate, suite or dev server holds it), and Playwright would test that "
        "server's code; stop it, or name free ports with --e2e-port / "
        "--default-canon-e2e-port, or leave them out to have free ones picked"
    )


def run_gate(
    *,
    repository_root: Path,
    push: bool,
    deploy: bool,
    e2e_port: str | None = None,
    default_canon_e2e_port: str | None = None,
    remote: str = DEFAULT_REMOTE,
) -> int:
    """Run every step and record the commit when they all pass.

    Args:
        repository_root: The checkout to gate.
        push: Whether to push the record to ``remote``.
        deploy: Whether to start the Deploy workflow once the record is
            pushed, when the commit is still the head of the deploy branch.
        e2e_port: Port for the Playwright suite, or None to pick a free one.
        default_canon_e2e_port: Port for the default-canon Playwright run, or
            None to pick a free one.
        remote: The remote holding the notes ref.

    Returns:
        A process exit status: 0 when the gate passed and was recorded.
    """
    commit_sha = head_commit_sha(repository_root)
    dirty_before = uncommitted_changes(repository_root)
    if dirty_before:
        announce(
            "refusing to run: the working tree has changes, so a passing run "
            "would say nothing about any commit. Commit or stash them first:"
        )
        for line in dirty_before:
            print(f"  {line}")
        return 1

    try:
        e2e_port, default_canon_e2e_port = choose_e2e_ports(
            repository_root,
            e2e_port=e2e_port,
            default_canon_e2e_port=default_canon_e2e_port,
        )
        steps = gate_steps(
            e2e_port=e2e_port, default_canon_e2e_port=default_canon_e2e_port
        )
    except GateError as failure:
        announce(f"refusing to run: {failure}")
        return 1
    busy_refusal = refuse_busy_ports((e2e_port, default_canon_e2e_port))
    if busy_refusal:
        announce(f"refusing to run: {busy_refusal}")
        return 1
    missing_canons = sorted(
        {
            step.canon_dir
            for step in steps
            if step.canon_dir is not None and not step.canon_dir.is_dir()
        }
    )
    if missing_canons:
        # Without this the suites would start and fail on the missing
        # directory only after the cheap steps and a long pytest run.
        announce(
            "refusing to run: the canon directory the second mode needs does "
            f"not exist: {', '.join(str(path) for path in missing_canons)}"
        )
        return 1
    # One environment per canon, each built by build_step_environment.
    environments = {
        canon_dir: build_step_environment(canon_dir=canon_dir)
        for canon_dir in {step.canon_dir for step in steps}
    }
    canon_dir, canon_name = resolve_canon(
        environ=environments[None],
        content_dir=repository_root / "reference" / "content",
    )
    # The note records the canon's commit, so that commit has to be what ran.
    canon_dirty_before = canon_uncommitted_changes(canon_dir)
    if canon_dirty_before:
        announce(
            f"refusing to run: the canon checkout {canon_dir} has changes, so a "
            "passing run would say nothing about any canon commit. Commit them "
            "there first:"
        )
        for line in canon_dirty_before:
            print(f"  {line}")
        return 1
    canon_commit = canon_commit_sha(canon_dir)
    announce(
        f"gating {commit_sha} with {len(steps)} steps (e2e ports {e2e_port}, "
        f"{default_canon_e2e_port} for the default canon)"
    )
    results: list[StepResult] = []
    for number, step in enumerate(steps, start=1):
        # A browser step starts long after the check above, time enough for
        # another run to take its port; ask again just before it starts.
        step_port = step.environment.get("E2E_PORT")
        busy_refusal = refuse_busy_ports((step_port,)) if step_port else ""
        if busy_refusal:
            announce(f"{step.name}: {busy_refusal}; nothing recorded for {commit_sha}")
            return 1
        announce(f"{number}/{len(steps)} {step.name}: {' '.join(step.command)}")
        result = run_step(
            step,
            repository_root=repository_root,
            environment=environments[step.canon_dir],
        )
        results.append(result)
        if not result.passed:
            summary, *output_tail = failure_report(result, commit_sha)
            announce(summary)
            for line in output_tail:
                print(line, flush=True)
            return result.returncode

    for result in results:
        announce(f"passed: {result.step.name} ({result.seconds:.0f}s)")

    # The suites are the evidence for this tree and no other, and they take
    # long enough for a commit or a stray edit to land while they run.
    if head_commit_sha(repository_root) != commit_sha:
        announce(
            f"HEAD moved while the suites ran; nothing recorded for {commit_sha}. "
            "Run the gate again on the commit you mean to deploy."
        )
        return 1
    dirty_after = uncommitted_changes(repository_root)
    if dirty_after:
        announce(
            f"the working tree changed while the suites ran; {commit_sha} not recorded:"
        )
        for line in dirty_after:
            print(f"  {line}")
        return 1

    if canon_commit_sha(canon_dir) != canon_commit or canon_uncommitted_changes(
        canon_dir
    ):
        announce(
            f"the canon checkout {canon_dir} changed while the suites ran; "
            f"{commit_sha} not recorded. Run the gate again."
        )
        return 1

    note_body = gate_note_body(
        commit_sha=commit_sha,
        recorded_at=datetime.now(UTC),
        host=socket.gethostname(),
        canon_name=canon_name,
        canon_dir=canon_dir,
        canon_commit=canon_commit,
        e2e_port=e2e_port,
        default_canon_e2e_port=default_canon_e2e_port,
        step_names=[step.name for step in steps],
    )
    try:
        record_passing_commit(
            repository_root, commit_sha, note_body, push=push, remote=remote
        )
    except (GateError, subprocess.CalledProcessError) as failure:
        announce(f"every step passed but the record failed: {failure}")
        return 1

    where = (
        f"{GATE_NOTES_REF} on {remote}" if push else f"{GATE_NOTES_REF} (local only)"
    )
    announce(f"all {len(steps)} steps passed; recorded {commit_sha} in {where}")
    if not (push and deploy):
        return 0
    # deploy.sh refuses a record whose first pass did not run the Tarmar canon,
    # so starting Deploy from one would only make a red run.
    if canon_name != TARMAR_CANON_NAME:
        announce(
            f"not deploying: this record ran the {canon_name} canon, and "
            "production needs a Tarmar-canon pass; run the gate with "
            "TARMAR_CANON_DIR set to a clone of smarks/tarmar-canon"
        )
        return 0
    try:
        announce(start_deploy_if_head(repository_root, commit_sha, remote))
    except subprocess.CalledProcessError as failure:
        announce(
            f"recorded, but the {DEPLOY_WORKFLOW} workflow did not start: "
            f"{(failure.stderr or '').strip() or failure}"
        )
        return 1
    except FileNotFoundError as failure:
        announce(
            f"recorded, but the {DEPLOY_WORKFLOW} workflow did not start: {failure}"
        )
        return 1
    return 0


def check_gate_record(
    *, repository_root: Path, commit: str, fetch: bool, remote: str = DEFAULT_REMOTE
) -> int:
    """Report whether a commit carries a gate record, the way a deploy asks.

    Args:
        repository_root: The checkout to read from.
        commit: A commit-ish to resolve and look up.
        fetch: Whether to refresh the notes ref from the remote first.
        remote: The remote holding the notes ref.

    Returns:
        A process exit status: 0 when a record exists and its first mode ran
        the Tarmar canon, the one production serves (issue #921). A record
        made without ``TARMAR_CANON_DIR`` ran the default canon twice.
    """
    commit_sha = git_output(repository_root, "rev-parse", commit)
    if fetch:
        fetch_gate_notes(repository_root, remote)
    record = read_gate_record(repository_root, commit_sha)
    if record is None:
        announce(f"no passing gate record for {commit_sha}. Run ./run gate on it.")
        return 1
    announce(f"gate record for {commit_sha}:")
    print(record)
    canon_name = parse_gate_record(record).get("canon")
    if canon_name != TARMAR_CANON_NAME:
        announce(
            f"the record for {commit_sha} ran against the {canon_name or 'unnamed'} "
            f"canon, not the Tarmar canon production serves. Run ./run gate again "
            f"with {CANON_DIR_ENV} set to a clone of smarks/tarmar-canon."
        )
        return 1
    return 0


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    """Parse the gate's command line.

    Args:
        argv: Arguments after the program name.

    Returns:
        The parsed options.
    """
    parser = argparse.ArgumentParser(
        prog="./run gate",
        description=(
            "Run ruff, pyright, the migration and guide checks, then pytest, "
            "Jest and Playwright twice (as the checkout stands, and against "
            "the default canon), and record the passing commit on "
            f"{GATE_NOTES_REF} for deploy.sh to read."
        ),
    )
    parser.add_argument(
        "--check",
        metavar="COMMIT",
        nargs="?",
        const="HEAD",
        help="Report whether COMMIT (default HEAD) has a record; run nothing.",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Record locally without pushing; a deploy host will not see it.",
    )
    parser.add_argument(
        "--no-deploy",
        action="store_true",
        help=(
            f"Record and push, but do not start the {DEPLOY_WORKFLOW} workflow "
            f"(by default it starts when the commit is the head of {DEPLOY_BRANCH})."
        ),
    )
    parser.add_argument(
        "--e2e-port",
        default=None,
        help=(
            "Port for the Playwright suite (default: a free one from "
            f"{DEFAULT_E2E_PORT} up, recorded in the note). Refused if taken."
        ),
    )
    parser.add_argument(
        "--default-canon-e2e-port",
        default=None,
        help=(
            "Port for the default-canon Playwright run (default: a free one, "
            "recorded in the note). Refused if taken."
        ),
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str]) -> int:
    """Entry point.

    Args:
        argv: Arguments after the program name.

    Returns:
        A process exit status.
    """
    options = parse_arguments(argv)
    if options.check is not None:
        return check_gate_record(
            repository_root=REPOSITORY_ROOT,
            commit=options.check,
            fetch=not options.no_push,
        )
    return run_gate(
        repository_root=REPOSITORY_ROOT,
        e2e_port=options.e2e_port,
        default_canon_e2e_port=options.default_canon_e2e_port,
        push=not options.no_push,
        deploy=not options.no_deploy,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
