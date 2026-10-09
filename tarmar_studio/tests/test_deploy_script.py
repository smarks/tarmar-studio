"""Guards on ``deploy.sh``'s handling of ``.env`` and its deploy lock.

Issue #574: the script hard-coded the production database URL, credentials and
all, and sourced ``.env`` with bash while the units read it through systemd's
``EnvironmentFile``. These tests source the real script and run its loader, so
they exercise the shell code that runs on the host rather than a copy of it.

Issue #576: the only deploy lock lived in the Actions workflow, which a
hand-run deploy never passes through, and nothing checked that a commit's
suites had ever been run. The tests at the bottom run the real script against a
throwaway checkout with a bare origin and stand-ins for the commands that would
reach the host, so the lock and the gate check are exercised rather than
described.
"""

import fcntl
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEPLOY_SCRIPT = REPO_ROOT / "deploy.sh"
SETUP_SCRIPT = REPO_ROOT / "setup-blue-green.sh"


def load_env(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    """Source deploy.sh, run load_env_file over ``body``, print the result."""
    env_file = tmp_path / ".env"
    env_file.write_text(body, encoding="utf-8")
    return subprocess.run(
        [
            "bash",
            "-c",
            'set -e; source "$1"; load_env_file "$2"; '
            'printf "%s|%s|%s\\n" "${FIRST-unset}" "${SECOND-unset}" "${THIRD-unset}"',
            "load_env",
            str(DEPLOY_SCRIPT),
            str(env_file),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "TARMAR_APP_DIR": str(tmp_path)},
        check=False,
    )


def test_plain_values_are_exported_literally(tmp_path: Path) -> None:
    result = load_env(
        tmp_path,
        "# a comment\n"
        "\n"
        "   \n"
        "  ; another comment\n"
        "FIRST=postgres://user:pa#ss@localhost:5432/db?sslmode=require\n"
        "SECOND=a,b,c=d\n"
        "THIRD=no-trailing-newline",
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip() == (
        "postgres://user:pa#ss@localhost:5432/db?sslmode=require|a,b,c=d|"
        "no-trailing-newline"
    )


def test_an_empty_value_is_exported_as_empty(tmp_path: Path) -> None:
    result = load_env(tmp_path, "FIRST=\n")
    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip() == "|unset|unset"


def test_quoted_value_is_refused_without_printing_it(tmp_path: Path) -> None:
    result = load_env(tmp_path, "FIRST=ok\nSECOND='hunter2'\n")
    assert result.returncode == 1
    assert "line 2 (SECOND)" in result.stdout
    assert "hunter2" not in result.stdout + result.stderr


def test_values_are_never_expanded(tmp_path: Path) -> None:
    for body in ("FIRST=$HOME\n", "FIRST=`id`\n", "FIRST=a\\b\n", "FIRST=two words\n"):
        result = load_env(tmp_path, body)
        assert result.returncode == 1, body
        assert "line 1 (FIRST)" in result.stdout, body


def test_a_line_without_a_key_is_refused_without_printing_it(tmp_path: Path) -> None:
    result = load_env(tmp_path, "FIRST=ok\nsecret-without-a-key\n")
    assert result.returncode == 1
    assert "line 2 is not a KEY=value line" in result.stdout
    assert "secret-without-a-key" not in result.stdout + result.stderr


def test_database_url_is_not_committed() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "PROD_DATABASE_URL" not in script
    assert not re.search(r"postgres(ql)?://", script)


def test_env_file_is_never_sourced_by_bash() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(source|\.)\s+\S*\.env", script, re.MULTILINE)
    assert 'load_env_file "$ENV_FILE"' in script


def test_every_mutating_command_takes_the_lock_but_status_does_not() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'exec 9>"$LOCK_FILE"' in script
    assert "flock -n 9 || error" in script
    dispatch = script[script.index('case "${1:-}" in') :]
    rollback_branch = dispatch[dispatch.index("rollback)") : dispatch.index(";;")]
    assert "take_deploy_lock" in rollback_branch
    status_branch = dispatch[dispatch.index("status)") :]
    assert "take_deploy_lock" not in status_branch[: status_branch.index(";;")]
    # backup was the one mutating command left outside the lock (issue #667):
    # a nightly dump overlapping a deploy's migration stalls the live site.
    backup_branch = dispatch[dispatch.index("backup)") : dispatch.index("*)")]
    assert backup_branch.index("take_deploy_lock") < backup_branch.index(
        "backup_database nightly"
    )
    default_branch = dispatch[dispatch.index("*)") :]
    assert default_branch.index("take_deploy_lock") < default_branch.index("main ")
    assert "take_deploy_lock" in SETUP_SCRIPT.read_text(encoding="utf-8")


def test_a_backup_is_refused_while_a_deploy_holds_the_lock(tmp_path: Path) -> None:
    """The overlap the lock exists to prevent, driven through the real script."""
    host = make_deploy_host(tmp_path)
    (host.app_dir / ".env").write_text(
        "DATABASE_URL=postgres://example/db\n", encoding="utf-8"
    )
    lock_file = host.app_dir / ".deploy.lock"
    with lock_file.open("w") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)

        result = run_deploy(host, "backup")

    assert result.returncode == 1
    assert "deploy in progress" in result.stdout
    # It stopped at the lock, before reading .env or reaching pg_dump.
    assert "Backing up the database" not in result.stdout


FAKE_PG_DUMP = """#!/bin/bash
for argument in "$@"; do
    case "$argument" in
        --file=*) echo dump > "${argument#--file=}" ;;
    esac
done
exit "${FAKE_PG_DUMP_EXIT:-0}"
"""

# Stands in for both uses of pg_restore: reading a finished dump's table of
# contents (--list, inside backup_database) and restoring one into the scratch
# database (--dbname, inside verify_latest_backup). It records the restore so a
# test can see which file was read, and each use has its own exit status.
FAKE_PG_RESTORE = """#!/bin/bash
for argument in "$@"; do
    case "$argument" in
        --list) exit "${FAKE_PG_RESTORE_LIST_EXIT:-0}" ;;
    esac
done
echo "$*" >> "${FAKE_RESTORE_LOG:-/dev/null}"
exit "${FAKE_PG_RESTORE_EXIT:-0}"
"""

# createdb and dropdb, recording the database each was asked for so a test can
# see that the scratch database was cleaned up however the check ended.
RECORDING_COMMAND = """#!/bin/bash
echo "$(basename "$0") $*" >> "${FAKE_RESTORE_LOG:-/dev/null}"
exit "${FAKE_DB_COMMAND_EXIT:-0}"
"""

# psql, answering the row count the test is asking about.
FAKE_PSQL = """#!/bin/bash
echo "$*" >> "${FAKE_RESTORE_LOG:-/dev/null}"
echo "${FAKE_MIGRATION_ROWS-3}"
exit "${FAKE_PSQL_EXIT:-0}"
"""


def backup_stand_ins(tmp_path: Path) -> Path:
    """A bin directory holding every PostgreSQL command deploy.sh reaches for."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    write_command(fake_bin / "pg_dump", FAKE_PG_DUMP)
    write_command(fake_bin / "pg_restore", FAKE_PG_RESTORE)
    write_command(fake_bin / "createdb", RECORDING_COMMAND)
    write_command(fake_bin / "dropdb", RECORDING_COMMAND)
    write_command(fake_bin / "psql", FAKE_PSQL)
    return fake_bin


def run_backup(
    tmp_path: Path, kind: str, pg_dump_exit: int = 0, **overrides: str
) -> subprocess.CompletedProcess[str]:
    """Source deploy.sh and run backup_database with stand-in commands."""
    fake_bin = backup_stand_ins(tmp_path)
    return subprocess.run(
        [
            "bash",
            "-c",
            'set -e; source "$1"; PATH="$2:$PATH"; backup_database "$3"',
            "backup",
            str(DEPLOY_SCRIPT),
            str(fake_bin),
            kind,
        ],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "TARMAR_APP_DIR": str(tmp_path),
            "DATABASE_URL": "postgres://example/db",
            "FAKE_PG_DUMP_EXIT": str(pg_dump_exit),
            **overrides,
        },
        check=False,
    )


def run_backup_verification(
    tmp_path: Path, **overrides: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run verify_latest_backup over a directory holding one dump.

    Args:
        tmp_path: The throwaway app directory.
        **overrides: Environment for this run, steering the stand-ins.

    Returns:
        The finished process and every command line the stand-ins recorded.
    """
    fake_bin = backup_stand_ins(tmp_path)
    backups = tmp_path / "var" / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    (backups / "nightly-20260925T033000Z.dump").write_text("dump", encoding="utf-8")
    restore_log = tmp_path / "restore.log"
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -e; source "$1"; PATH="$2:$PATH"; verify_latest_backup',
            "verify-backup",
            str(DEPLOY_SCRIPT),
            str(fake_bin),
        ],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "TARMAR_APP_DIR": str(tmp_path),
            "DATABASE_URL": "postgres://user:pw@example:5432/tarmar?sslmode=require",
            "FAKE_RESTORE_LOG": str(restore_log),
            **overrides,
        },
        check=False,
    )
    recorded = (
        restore_log.read_text(encoding="utf-8").splitlines()
        if restore_log.exists()
        else []
    )
    return result, recorded


def test_backup_keeps_the_newest_ten_of_its_own_kind(tmp_path: Path) -> None:
    backups = tmp_path / "var" / "backups"
    backups.mkdir(parents=True)
    for day in range(1, 13):
        old = backups / f"deploy-202601{day:02d}T000000Z.dump"
        old.write_text("old", encoding="utf-8")
        os.utime(old, (1_700_000_000 + day, 1_700_000_000 + day))
    nightly = backups / "nightly-20250101T000000Z.dump"
    nightly.write_text("old", encoding="utf-8")
    os.utime(nightly, (1_600_000_000, 1_600_000_000))

    result = run_backup(tmp_path, "deploy")

    assert result.returncode == 0, result.stdout + result.stderr
    deploy_dumps = sorted(item.name for item in backups.glob("deploy-*.dump"))
    assert len(deploy_dumps) == 10
    # The three oldest went; the new dump is among the kept.
    for day in (1, 2, 3):
        assert f"deploy-202601{day:02d}T000000Z.dump" not in deploy_dumps
    assert any(not name.startswith("deploy-202601") for name in deploy_dumps)
    assert nightly.exists()
    assert not list(backups.glob("*.partial"))


def test_a_failed_dump_stops_and_leaves_no_file(tmp_path: Path) -> None:
    result = run_backup(tmp_path, "deploy", pg_dump_exit=1)
    assert result.returncode == 1
    assert "pg_dump failed" in result.stdout
    assert list((tmp_path / "var" / "backups").iterdir()) == []


def test_deploy_backs_up_after_checks_and_right_before_migrating() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    main_body = script[script.index("main() {") : script.index("rollback() {")]
    makemigrations = main_body.index("makemigrations --check --dry-run")
    load_env = main_body.index('load_env_file "$ENV_FILE"')
    backup = main_body.index("backup_database deploy")
    migrate = main_body.index("manage.py migrate")
    assert makemigrations < load_env < backup < migrate
    assert "manage.py" not in main_body[backup:migrate].replace("manage.py migrate", "")


def test_rollback_checks_migrations_before_switching() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    body = script[script.index("rollback() {") :]
    assert body.index("check_rollback_migrations") < body.index("switch_to")


def test_a_dump_pg_restore_cannot_read_is_not_left_looking_like_a_backup(
    tmp_path: Path,
) -> None:
    """Written is not the same as restorable (issue #666)."""
    result = run_backup(tmp_path, "deploy", FAKE_PG_RESTORE_LIST_EXIT="1")

    assert result.returncode == 1
    assert "pg_restore cannot read it" in result.stdout
    backups = tmp_path / "var" / "backups"
    assert list(backups.glob("*.dump")) == []
    assert len(list(backups.glob("*.dump.unreadable"))) == 1


def test_the_newest_backup_is_restored_into_a_scratch_database(
    tmp_path: Path,
) -> None:
    """The check that had never been run: restore one and read a table back."""
    result, recorded = run_backup_verification(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "django_migrations holds 3 rows" in result.stdout
    # It built the scratch database beside the real one, restored the newest
    # dump into it, counted a table, and took it away again.
    assert any(line.startswith("createdb ") for line in recorded)
    assert any("nightly-20260925T033000Z.dump" in line for line in recorded)
    assert any("django_migrations" in line for line in recorded)
    assert any(line.startswith("dropdb ") for line in recorded)
    # Never the database the site runs on.
    assert all("/tarmar?" not in line for line in recorded)


def test_a_backup_that_will_not_restore_fails_and_cleans_up(tmp_path: Path) -> None:
    """A dump that cannot be restored is worth a failed nightly run."""
    result, recorded = run_backup_verification(tmp_path, FAKE_PG_RESTORE_EXIT="1")

    assert result.returncode == 1
    assert "cannot be restored" in result.stdout
    assert any(line.startswith("dropdb ") for line in recorded)


def test_a_backup_that_restores_an_empty_schema_fails(tmp_path: Path) -> None:
    """pg_restore can succeed over a dump holding nothing."""
    result, _ = run_backup_verification(tmp_path, FAKE_MIGRATION_ROWS="0")

    assert result.returncode == 1
    assert "empty schema" in result.stdout


def test_the_scratch_url_keeps_the_connection_and_changes_the_database(
    tmp_path: Path,
) -> None:
    """The throwaway database is on the same server, under another name."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -e; source "$1"; scratch_database_url "$2" restore_check',
            "scratch-url",
            str(DEPLOY_SCRIPT),
            "postgres://user:pw@example:5432/tarmar?sslmode=require",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "TARMAR_APP_DIR": str(tmp_path)},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (
        result.stdout.strip()
        == "postgres://user:pw@example:5432/restore_check?sslmode=require"
    )


def test_backup_timer_is_installed_and_runs_the_backup_command() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "install_backup_timer" in script
    assert 'enable --now "$BACKUP_TIMER"' in script
    service = (REPO_ROOT / "tarmar-backup.service.template").read_text(encoding="utf-8")
    # --verify: the nightly run reads its own dump back (issue #666).
    assert "ExecStart=/bin/bash __APP_DIR__/deploy.sh backup --verify" in service
    assert "User=sam" in service
    timer = (REPO_ROOT / "tarmar-backup.timer").read_text(encoding="utf-8")
    assert "OnCalendar=" in timer
    assert "Persistent=true" in timer
    dispatch = script[script.index('case "${1:-}" in') :]
    backup_branch = dispatch[dispatch.index("backup)") : dispatch.index("*)")]
    assert "backup_database nightly" in backup_branch
    assert "verify_latest_backup" in backup_branch


# macOS ships no flock(1), so the deploy tests bring one along. It locks the
# descriptor deploy.sh opened, exactly as util-linux's does: the lock belongs to
# the open file description the shell holds, so it outlives this process and is
# released only when the shell's descriptor 9 closes.
FLOCK_STAND_IN = """#!/bin/bash
exec python3 -c '
import fcntl
import sys

try:
    fcntl.flock(int(sys.argv[1]), fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(1)
' "${@: -1}"
"""

# uv would sync a real dependency set; the deploy tests only need it to fail
# where a deploy on the host would go on to build a release. It names itself so
# a test can tell that this, and not the machine's real uv, is what ran.
FAILING_UV = """#!/bin/bash
echo "stub uv: $*" >&2
exit 1
"""

# Stands in for the real uv the deploy host keeps ahead of everything else on
# PATH. deploy.sh puts $TARMAR_LOCAL_BIN at the front, so this one must never
# be reached; on datahorde the real uv in ~/.local/bin got there first and a
# test that expected the deploy to stop early instead watched it carry on.
DECOY_UV = """#!/bin/bash
echo "decoy uv: $*" >&2
exit 0
"""


@dataclass(frozen=True)
class DeployHost:
    """A throwaway stand-in for the main checkout on the deploy host."""

    app_dir: Path
    origin: Path
    seed: Path
    stub_bin: Path
    decoy_bin: Path
    # A bare stand-in for smarks/tarmar-canon, and where deploy.sh clones it.
    canon_origin: Path
    canon_checkout: Path


def git_in(repository: Path, *arguments: str) -> str:
    """Run git in ``repository`` and return its stripped stdout."""
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def identify(repository: Path) -> None:
    """Give a throwaway repository a committer, so commits and notes work."""
    git_in(repository, "config", "user.email", "deploy@example.com")
    git_in(repository, "config", "user.name", "Deploy Test")


def make_deploy_host(tmp_path: Path) -> DeployHost:
    """Build a main checkout holding the real deploy.sh, with a bare origin.

    Args:
        tmp_path: The temporary directory to build in.

    Returns:
        The pieces the deploy tests drive.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(origin)],
        capture_output=True,
        check=True,
    )
    seed = tmp_path / "seed"
    seed.mkdir()
    git_in(seed, "init", "--initial-branch=main")
    identify(seed)
    git_in(seed, "remote", "add", "origin", str(origin))
    shutil.copy(DEPLOY_SCRIPT, seed / "deploy.sh")
    (seed / "tarmar.service.template").write_text("stub\n", encoding="utf-8")
    git_in(seed, "add", "deploy.sh", "tarmar.service.template")
    git_in(seed, "commit", "-m", "first")
    git_in(seed, "push", "--quiet", "origin", "main")

    app_dir = tmp_path / "app"
    subprocess.run(
        ["git", "clone", "--quiet", str(origin), str(app_dir)],
        capture_output=True,
        check=True,
    )
    identify(app_dir)

    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    if shutil.which("flock") is None:
        stand_in = stub_bin / "flock"
        stand_in.write_text(FLOCK_STAND_IN, encoding="utf-8")
        stand_in.chmod(0o755)
    write_command(stub_bin / "uv", FAILING_UV)
    decoy_bin = tmp_path / "decoy-bin"
    decoy_bin.mkdir()
    write_command(decoy_bin / "uv", DECOY_UV)
    canon_origin = make_canon_origin(tmp_path)
    # Untracked, as on the host: what the units read, naming the canon
    # checkout the deploy keeps current.
    (app_dir / ".env").write_text(
        f"TARMAR_CANON_DIR={tmp_path / 'tarmar-canon'}\n", encoding="utf-8"
    )
    return DeployHost(
        app_dir=app_dir,
        origin=origin,
        seed=seed,
        stub_bin=stub_bin,
        decoy_bin=decoy_bin,
        canon_origin=canon_origin,
        canon_checkout=tmp_path / "tarmar-canon",
    )


def make_canon_origin(tmp_path: Path) -> Path:
    """A bare origin standing in for the private canon repository (#921).

    Its working copy stays at ``tmp_path / "canon-seed"`` for tests that push
    a newer canon.

    Args:
        tmp_path: The temporary directory to build in.

    Returns:
        The bare repository deploy.sh clones from.
    """
    canon_origin = tmp_path / "canon-origin.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(canon_origin)],
        capture_output=True,
        check=True,
    )
    canon_seed = tmp_path / "canon-seed"
    canon_seed.mkdir()
    git_in(canon_seed, "init", "--initial-branch=main")
    identify(canon_seed)
    git_in(canon_seed, "remote", "add", "origin", str(canon_origin))
    (canon_seed / "canon.json").write_text('{"name": "tarmar"}\n', encoding="utf-8")
    git_in(canon_seed, "add", "canon.json")
    git_in(canon_seed, "commit", "-m", "first canon")
    git_in(canon_seed, "push", "--quiet", "origin", "main")
    return canon_origin


def push_canon_change(tmp_path: Path, message: str) -> str:
    """Commit a change to the canon's seed, push it, and return its sha."""
    canon_seed = tmp_path / "canon-seed"
    (canon_seed / "the-world.md").write_text(f"# {message}\n", encoding="utf-8")
    git_in(canon_seed, "add", "the-world.md")
    git_in(canon_seed, "commit", "-m", message)
    git_in(canon_seed, "push", "--quiet", "origin", "main")
    return git_in(canon_seed, "rev-parse", "HEAD")


def write_command(path: Path, body: str) -> None:
    """Write an executable stand-in for a command."""
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def deploy_environment(host: DeployHost, **overrides: str) -> dict[str, str]:
    """The environment a throwaway deploy runs in.

    The decoy uv sits ahead of the real PATH, standing in for the deploy host's
    own uv, and TARMAR_LOCAL_BIN points deploy.sh's own prepend at the
    stand-ins, which is what puts them in front of both.

    Args:
        host: The throwaway checkout being driven.
        **overrides: Extra variables for this run.

    Returns:
        The environment to hand bash.
    """
    return {
        **os.environ,
        "TARMAR_APP_DIR": str(host.app_dir),
        "TARMAR_LOCAL_BIN": str(host.stub_bin),
        "PATH": f"{host.decoy_bin}:{os.environ['PATH']}",
        # Never the real canon: on datahorde this suite runs inside a deploy,
        # beside the production canon checkout.
        "TARMAR_CANON_REMOTE": str(host.canon_origin),
        "TARMAR_CANON_CHECKOUT": str(host.canon_checkout),
        **overrides,
    }


def run_deploy(
    host: DeployHost, *arguments: str, **overrides: str
) -> subprocess.CompletedProcess[str]:
    """Run the throwaway checkout's deploy.sh with the stand-ins on PATH."""
    return subprocess.run(
        ["bash", str(host.app_dir / "deploy.sh"), *arguments],
        capture_output=True,
        text=True,
        cwd=str(host.app_dir),
        env=deploy_environment(host, **overrides),
        check=False,
    )


def record_gate_note(
    host: DeployHost,
    revision: str = "origin/main",
    *,
    canon: str = "tarmar",
    canon_commit: str = "",
) -> str:
    """Record a passing gate note on origin, the way ./run gate pushes one."""
    commit_sha = git_in(host.seed, "rev-parse", revision)
    canon_commit = canon_commit or git_in(
        host.canon_origin.parent / "canon-seed", "rev-parse", "HEAD"
    )
    git_in(
        host.seed,
        "notes",
        "--ref=gate",
        "add",
        "--force",
        "--message",
        f"gate: pass\ncommit: {commit_sha}\ncanon: {canon}\n"
        f"canon-commit: {canon_commit}\n",
        commit_sha,
    )
    git_in(host.seed, "push", "--quiet", "origin", "refs/notes/gate")
    return commit_sha


def test_a_second_deploy_is_refused_while_another_holds_the_lock(
    tmp_path: Path,
) -> None:
    host = make_deploy_host(tmp_path)
    lock_file = host.app_dir / ".deploy.lock"
    with lock_file.open("w") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)

        result = run_deploy(host, "--force")

    assert result.returncode == 1
    assert "deploy in progress" in result.stdout
    # It stopped at the lock, before touching git or the release checkouts.
    assert "Fetching from origin" not in result.stdout


def test_a_deploy_takes_the_lock_and_gives_it_back(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)

    run_deploy(host, "--force")

    lock_file = host.app_dir / ".deploy.lock"
    assert lock_file.exists()
    with lock_file.open("w") as holder:
        # The finished deploy left nothing holding it.
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_the_lock_survives_the_re_exec_after_the_pull(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)
    (host.seed / "note.txt").write_text("second\n", encoding="utf-8")
    git_in(host.seed, "add", "note.txt")
    git_in(host.seed, "commit", "-m", "second")
    git_in(host.seed, "push", "--quiet", "origin", "main")

    result = run_deploy(host, "--force")

    # The pull happened and the pulled copy took over...
    assert "Re-running the freshly pulled deploy.sh" in result.stdout
    # ...and did not find its own lock in the way.
    assert "deploy in progress" not in result.stdout
    # It carried on into the build, where the stub uv stops it.
    assert "Syncing dependencies" in result.stdout


def test_a_commit_with_no_gate_record_is_refused(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)
    commit_sha = git_in(host.app_dir, "rev-parse", "origin/main")

    result = run_deploy(host)

    assert result.returncode == 1
    assert commit_sha in result.stdout
    assert "no passing gate record" in result.stdout
    assert "./run gate" in result.stdout
    assert "--force" in result.stdout
    # Nothing was built: the refusal comes before any release checkout.
    assert "Syncing dependencies" not in result.stdout
    assert not (tmp_path / "app-blue").exists()


def mark_canon_deployed(host: DeployHost, tmp_path: Path) -> str:
    """Record the canon origin's HEAD as the one the live site runs."""
    canon_head = git_in(tmp_path / "canon-seed", "rev-parse", "HEAD")
    (host.app_dir / ".deploy-canon").write_text(f"{canon_head}\n", encoding="utf-8")
    return canon_head


def test_a_gate_record_on_origin_lets_the_deploy_through(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)
    commit_sha = record_gate_note(host)
    mark_canon_deployed(host, tmp_path)

    result = run_deploy(host)

    # The record is only on origin, so the deploy found it by fetching the ref.
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Gate record for {commit_sha}" in result.stdout
    assert "gate: pass" in result.stdout
    # Nothing to deploy, so it stops there rather than building a release. It
    # once did build one on datahorde, having inherited TARMAR_DEPLOY_PULLED
    # from the deploy that was running this very suite.
    assert "Already up to date" in result.stdout
    assert "Syncing dependencies" not in result.stdout
    assert "uv:" not in result.stderr


def test_force_deploys_a_commit_that_has_no_record(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)

    result = run_deploy(host, "--force")

    assert "not checking whether" in result.stdout
    assert "no passing gate record" not in result.stdout
    assert "Syncing dependencies" in result.stdout
    # deploy.sh's own prepend put the stand-in ahead of the uv this machine
    # would otherwise have found, wherever that one lives.
    assert "stub uv" in result.stderr
    assert "decoy uv" not in result.stderr


def test_status_needs_neither_the_lock_nor_a_gate_record(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)
    lock_file = host.app_dir / ".deploy.lock"
    with lock_file.open("w") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)

        result = run_deploy(host, "status")

    assert "Blue-Green Deployment Status" in result.stdout
    assert "deploy in progress" not in result.stdout
    assert "no passing gate record" not in result.stdout


# What bash does for the pulled copy on the host: descriptor 9 is open and
# holding the lock, and TARMAR_DEPLOY_LOCKED says so. The backgrounded sleep
# keeps that same open file description alive behind the deploy, so a copy that
# re-opened the file rather than keeping the descriptor it was handed would
# find the lock still held and refuse itself.
RE_EXEC_WRAPPER = """
set -e
# What deploy.sh itself did before it reached the pull, so the stand-in flock
# and uv are on PATH here too.
export PATH="$TARMAR_LOCAL_BIN:$PATH"
exec 9>"$1"
flock -n 9 || exit 7
sleep 10 >/dev/null 2>&1 &
exec env TARMAR_DEPLOY_LOCKED=1 TARMAR_DEPLOY_PULLED=1 bash "$2" --force
"""


def test_the_pulled_copy_keeps_the_descriptor_it_was_handed(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)

    result = subprocess.run(
        [
            "bash",
            "-c",
            RE_EXEC_WRAPPER,
            "re-exec",
            str(host.app_dir / ".deploy.lock"),
            str(host.app_dir / "deploy.sh"),
        ],
        capture_output=True,
        text=True,
        cwd=str(host.app_dir),
        env=deploy_environment(host),
        check=False,
    )

    assert result.returncode != 7, "the wrapper could not take the lock at all"
    assert "deploy in progress" not in result.stdout
    assert "Syncing dependencies" in result.stdout


def test_a_pulled_copy_with_no_descriptor_takes_the_lock_afresh(
    tmp_path: Path,
) -> None:
    host = make_deploy_host(tmp_path)

    with (host.app_dir / ".deploy.lock").open("w") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            ["bash", str(host.app_dir / "deploy.sh"), "--force"],
            capture_output=True,
            text=True,
            cwd=str(host.app_dir),
            env=deploy_environment(
                host, TARMAR_DEPLOY_LOCKED="1", TARMAR_DEPLOY_PULLED="1"
            ),
            check=False,
        )

    # Told the lock is held but handed no descriptor, it locks again rather
    # than deploying unlocked, and finds this test still holding it.
    assert result.returncode == 1
    assert "deploy in progress" in result.stdout


# --- The Tarmar canon checkout beside the main one (issue #921) ---


def test_a_deploy_clones_the_canon_before_it_builds_a_release(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)

    result = run_deploy(host, "--force")

    assert "Cloning the Tarmar canon" in result.stdout, result.stdout + result.stderr
    canon_head = git_in(host.canon_checkout, "rev-parse", "HEAD")
    assert canon_head == git_in(tmp_path / "canon-seed", "rev-parse", "HEAD")
    assert result.stdout.index("Tarmar canon at") < result.stdout.index(
        "Syncing dependencies"
    )


def test_a_later_deploy_fast_forwards_the_canon(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)
    run_deploy(host, "--force")
    newer = push_canon_change(tmp_path, "newer canon")

    result = run_deploy(host, "--force")

    assert "Updating the Tarmar canon" in result.stdout, result.stdout + result.stderr
    assert git_in(host.canon_checkout, "rev-parse", "HEAD") == newer
    assert "Syncing dependencies" in result.stdout


def test_a_canon_that_cannot_be_cloned_stops_the_deploy(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)

    result = run_deploy(
        host, "--force", TARMAR_CANON_REMOTE=str(tmp_path / "no-such-canon.git")
    )

    assert result.returncode == 1
    assert "could not clone the Tarmar canon" in result.stdout
    # Stopped before any release was checked out or built.
    assert "Syncing dependencies" not in result.stdout
    assert not (tmp_path / "app-blue").exists()
    assert not (tmp_path / "app-green").exists()


def test_a_canon_that_cannot_fast_forward_stops_the_deploy(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)
    run_deploy(host, "--force")
    # A commit made on the host that origin does not have...
    identify(host.canon_checkout)
    (host.canon_checkout / "local.md").write_text("# local\n", encoding="utf-8")
    git_in(host.canon_checkout, "add", "local.md")
    git_in(host.canon_checkout, "commit", "-m", "edited on the host")
    # ...and a different one origin does.
    push_canon_change(tmp_path, "edited upstream")

    result = run_deploy(host, "--force")

    assert result.returncode == 1
    assert "cannot fast-forward" in result.stdout
    assert "Syncing dependencies" not in result.stdout


def test_a_canon_path_that_is_not_a_checkout_stops_the_deploy(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)
    host.canon_checkout.mkdir()

    result = run_deploy(host, "--force")

    assert result.returncode == 1
    assert "is not a git checkout of the Tarmar canon" in result.stdout
    assert "Syncing dependencies" not in result.stdout


def require_canon_dir_with(
    tmp_path: Path, env_body: str
) -> subprocess.CompletedProcess[str]:
    """Source deploy.sh and run require_canon_dir against a .env of ``env_body``."""
    app_dir = tmp_path / "app"
    app_dir.mkdir(exist_ok=True)
    (app_dir / ".env").write_text(env_body, encoding="utf-8")
    environment = {
        **os.environ,
        "TARMAR_APP_DIR": str(app_dir),
        "TARMAR_CANON_CHECKOUT": str(tmp_path / "tarmar-canon"),
    }
    environment.pop("TARMAR_CANON_DIR", None)
    return subprocess.run(
        [
            "bash",
            "-c",
            'set -e; source "$1"; require_canon_dir; '
            'echo "canon-ok ${TARMAR_CANON_DIR-unexported}"',
            "require-canon",
            str(DEPLOY_SCRIPT),
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def test_env_must_name_the_canon_checkout_the_deploy_updates(tmp_path: Path) -> None:
    canon = tmp_path / "tarmar-canon"
    unset = require_canon_dir_with(tmp_path, "OTHER=1\n")
    assert unset.returncode == 1
    assert "TARMAR_CANON_DIR is not set" in unset.stdout

    elsewhere = require_canon_dir_with(tmp_path, "TARMAR_CANON_DIR=/srv/other\n")
    assert elsewhere.returncode == 1
    assert "but this deploy updates" in elsewhere.stdout

    for written in (str(canon), f"{canon}/", f"{canon}//"):
        with_slashes = require_canon_dir_with(tmp_path, f"TARMAR_CANON_DIR={written}\n")
        assert with_slashes.returncode == 0, with_slashes.stdout + with_slashes.stderr
        # Read in a subshell: nothing from .env reaches the deploy's own shell
        # before the tests run.
        assert "canon-ok unexported" in with_slashes.stdout


def test_a_bad_env_stops_the_deploy_before_a_release_is_built(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)
    (host.app_dir / ".env").write_text(
        "TARMAR_CANON_DIR=/srv/other\n", encoding="utf-8"
    )

    result = run_deploy(host, "--force")

    assert result.returncode == 1
    assert "but this deploy updates" in result.stdout
    assert "Syncing dependencies" not in result.stdout
    assert not (tmp_path / "app-blue").exists()
    assert not (tmp_path / "app-green").exists()


def test_a_moved_canon_is_deployed_without_force(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)
    record_gate_note(host)
    mark_canon_deployed(host, tmp_path)
    newer = push_canon_change(tmp_path, "newer canon")

    result = run_deploy(host)

    assert "Already up to date" not in result.stdout, result.stdout + result.stderr
    assert f"Tarmar canon at {newer}" in result.stdout
    assert "deployed with Tarmar canon" in result.stdout
    assert "Syncing dependencies" in result.stdout


def test_a_canon_pulled_by_a_failed_deploy_is_still_deployed_next_time(
    tmp_path: Path,
) -> None:
    host = make_deploy_host(tmp_path)
    record_gate_note(host)
    mark_canon_deployed(host, tmp_path)
    push_canon_change(tmp_path, "newer canon")
    # This one pulls the canon, then fails in the build (the stub uv).
    run_deploy(host)

    result = run_deploy(host)

    assert "Already up to date" not in result.stdout
    assert "Syncing dependencies" in result.stdout


def test_an_unmoved_canon_with_no_new_commit_is_up_to_date(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)
    record_gate_note(host)
    mark_canon_deployed(host, tmp_path)

    result = run_deploy(host)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Already up to date" in result.stdout
    assert "Tarmar canon at" in result.stdout


def test_a_gate_record_from_the_default_canon_is_refused(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)
    record_gate_note(host, canon="default")

    result = run_deploy(host)

    assert result.returncode == 1
    assert "not the Tarmar canon" in result.stdout
    assert "Tarmar canon at" not in result.stdout


def test_a_canon_the_gate_did_not_run_is_warned_about(tmp_path: Path) -> None:
    host = make_deploy_host(tmp_path)
    gated = git_in(tmp_path / "canon-seed", "rev-parse", "HEAD")
    record_gate_note(host, canon_commit=gated)
    newer = push_canon_change(tmp_path, "ungated canon")

    result = run_deploy(host)

    assert f"the gate ran against Tarmar canon {gated}" in result.stdout
    assert f"this deploy serves {newer}" in result.stdout


def test_the_canon_is_synced_before_the_build_and_checked_after_env_loads() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    main_body = script[script.index("main() {") : script.index("rollback() {")]
    sync = main_body.index("    sync_canon\n")
    # After the pull and re-exec, so the pulled script syncs it, and before
    # the up-to-date check, which a moved canon alone can pass.
    assert main_body.index("git pull --ff-only") < sync
    assert sync < main_body.index("Already up to date")
    check = main_body.index("    require_canon_dir\n")
    assert main_body.index("Already up to date") < check
    assert check < main_body.index('prepare_release "$inactive"')
    assert check < main_body.index("uv sync --group prod")
    # The live canon is recorded only once the deploy has gone through.
    record = main_body.index('> "$CANON_STATE_FILE"')
    assert main_body.index("scripts/verify_deployment.py") < record


def test_the_deployed_canon_record_is_ignored_by_git() -> None:
    ignored = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "--quiet", ".deploy-canon"],
        capture_output=True,
        check=False,
    )
    assert ignored.returncode == 0, "git does not ignore .deploy-canon"


def test_the_canon_checkout_defaults_beside_the_main_checkout(tmp_path: Path) -> None:
    environment = {**os.environ, "TARMAR_APP_DIR": "/home/sam/dev/tarmar-studio"}
    environment.pop("TARMAR_CANON_CHECKOUT", None)
    environment.pop("TARMAR_CANON_REMOTE", None)
    result = subprocess.run(
        [
            "bash",
            "-c",
            'cd() { :; }; source "$1"; echo "$CANON_CHECKOUT|$CANON_REMOTE"',
            "canon-defaults",
            str(DEPLOY_SCRIPT),
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert result.stdout.strip() == (
        "/home/sam/dev/tarmar-canon|git@github.com:smarks/tarmar-canon.git"
    ), result.stderr


# --- The switch, and what a failure on either side of it does (issue #652) ---

# `sudo` on the deploy host, minus the privilege: run the rest of the line.
PASS_THROUGH_SUDO = """#!/bin/bash
exec "$@"
"""

# `nginx -t` and `systemctl reload nginx`, which switch_to runs through sudo.
ACCEPTING_COMMAND = """#!/bin/bash
exit 0
"""

# Stands in for `uv run python - < scripts/rollback_migrations.py`: swallows
# the script on stdin and answers with the exit status the test is asking
# about. 1 is the real script's "this colour is behind the database".
MIGRATION_COMPARISON_UV = """#!/bin/bash
cat > /dev/null
if [ "${FAKE_MIGRATION_EXIT:-0}" -ne 0 ]; then
    echo "characters.0042_add_a_column"
fi
exit "${FAKE_MIGRATION_EXIT:-0}"
"""


@dataclass(frozen=True)
class SwitchableHost:
    """A throwaway main checkout with both colours' release dirs in place."""

    app_dir: Path
    upstream_conf: Path
    state_file: Path
    stub_bin: Path


def make_switchable_host(tmp_path: Path) -> SwitchableHost:
    """Build the pieces switch_to and the recovery path touch.

    Args:
        tmp_path: The temporary directory to build in.

    Returns:
        The host the switch tests drive.
    """
    app_dir = tmp_path / "app"
    (app_dir / "scripts").mkdir(parents=True)
    # release_knows_every_migration feeds this file to the standby's python.
    (app_dir / "scripts" / "rollback_migrations.py").write_text(
        "print('stub')\n", encoding="utf-8"
    )
    (app_dir / ".env").write_text(
        "DATABASE_URL=postgres://example/db\nALLOWED_HOSTS=tarmar.example,other.example\n",
        encoding="utf-8",
    )
    state_file = app_dir / ".deploy-state"
    state_file.write_text("green\n", encoding="utf-8")
    for colour in ("blue", "green"):
        (tmp_path / f"app-{colour}" / ".git").mkdir(parents=True)

    stub_bin = tmp_path / "switch-bin"
    stub_bin.mkdir()
    write_command(stub_bin / "sudo", PASS_THROUGH_SUDO)
    write_command(stub_bin / "nginx", ACCEPTING_COMMAND)
    write_command(stub_bin / "systemctl", ACCEPTING_COMMAND)
    write_command(stub_bin / "uv", MIGRATION_COMPARISON_UV)
    return SwitchableHost(
        app_dir=app_dir,
        upstream_conf=tmp_path / "tarmar-upstream.conf",
        state_file=state_file,
        stub_bin=stub_bin,
    )


def run_shell_function(
    host: SwitchableHost, script: str, **overrides: str
) -> subprocess.CompletedProcess[str]:
    """Source the real deploy.sh in a throwaway host and run ``script``."""
    return subprocess.run(
        [
            "bash",
            "-c",
            f'set -e; source "$1"; {script}',
            "deploy-function",
            str(DEPLOY_SCRIPT),
        ],
        capture_output=True,
        text=True,
        cwd=str(host.app_dir),
        env={
            **os.environ,
            "TARMAR_APP_DIR": str(host.app_dir),
            "TARMAR_LOCAL_BIN": str(host.stub_bin),
            "TARMAR_UPSTREAM_CONF": str(host.upstream_conf),
            **overrides,
        },
        check=False,
    )


def test_the_release_is_verified_before_the_switch_not_after(tmp_path: Path) -> None:
    """The ordering the issue is about: render the pages, then move traffic."""
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    main_body = script[script.index("main() {") : script.index("rollback() {")]
    health_check = main_body.index('verify_environment "$inactive"')
    real_check = main_body.index('verify_release "$inactive"')
    switch = main_body.index('switch_to "$inactive"')
    assert health_check < real_check < switch


def test_a_failed_verification_after_the_switch_is_acted_on() -> None:
    """It used to be a bare command under `set -e`: no error, no rollback."""
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    main_body = script[script.index("main() {") : script.index("rollback() {")]
    after_switch = main_body[main_body.index('switch_to "$inactive"') :]
    assert "if ! uv run python scripts/verify_deployment.py; then" in after_switch
    assert 'recover_from_failed_switch "$inactive" "$active"' in after_switch


def test_public_host_is_the_first_of_allowed_hosts(tmp_path: Path) -> None:
    """A colour reached by its own port is presented under the site's name."""
    host = make_switchable_host(tmp_path)

    result = run_shell_function(host, 'load_env_file "$ENV_FILE"; public_host')

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("tarmar.example")


def test_a_failed_switch_puts_the_previous_colour_back(tmp_path: Path) -> None:
    """Blue can take the database as it stands, so traffic goes back to it."""
    host = make_switchable_host(tmp_path)

    result = run_shell_function(
        host, "recover_from_failed_switch green blue", FAKE_MIGRATION_EXIT="0"
    )

    assert result.returncode == 1
    assert "rolled back" in result.stdout
    assert "server 127.0.0.1:9066;" in host.upstream_conf.read_text(encoding="utf-8")
    assert host.state_file.read_text(encoding="utf-8").strip() == "blue"


def test_a_failed_switch_stays_put_when_the_old_colour_is_behind(
    tmp_path: Path,
) -> None:
    """Old code on a new schema is worse than a colour failing verification."""
    host = make_switchable_host(tmp_path)

    result = run_shell_function(
        host, "recover_from_failed_switch green blue", FAKE_MIGRATION_EXIT="1"
    )

    assert result.returncode == 1
    assert "Traffic stays on green" in result.stdout
    assert "characters.0042_add_a_column" in result.stdout
    # Nothing was switched: nginx's upstream was never rewritten.
    assert not host.upstream_conf.exists()
    assert host.state_file.read_text(encoding="utf-8").strip() == "green"


def test_rollback_still_refuses_a_colour_that_is_behind(tmp_path: Path) -> None:
    """The same comparison, from the command that has always refused on it."""
    host = make_switchable_host(tmp_path)

    result = run_shell_function(
        host, "check_rollback_migrations blue", FAKE_MIGRATION_EXIT="1"
    )

    assert result.returncode == 1
    assert "refusing to roll back to blue" in result.stdout
    assert "characters.0042_add_a_column" in result.stdout


def test_the_lock_file_the_deploy_leaves_behind_is_ignored() -> None:
    """Issue #668: gate.py refuses on any untracked path, so the file the
    deploy itself creates must not be one. `git status --porcelain` lists
    untracked paths by default, and run_gate stops on a non-empty result --
    so on the deploy host, the machine DEPLOY.md points at when a commit has
    no gate record, the gate could not be run at all after the first deploy.
    """
    lock_name = ".deploy.lock"
    assert lock_name in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    ignored = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "--quiet", lock_name],
        capture_output=True,
        check=False,
    )
    assert ignored.returncode == 0, "git does not ignore .deploy.lock"
    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", lock_name],
        capture_output=True,
        check=False,
    )
    assert tracked.returncode != 0, ".deploy.lock is tracked; it is runtime state"
