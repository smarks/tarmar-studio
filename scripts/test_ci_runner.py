"""The CI runner's install files agree with each other and with ci.yml (#576).

The runner lives under its own macOS account on a machine this suite can only
partly see, and the installer needs root, so nothing here registers anything.
What it can do is hold the four files together: the installer, the daemon it
installs, the workflow that selects the runner, and the branch-protection
script that names the workflow's job. launchd expands nothing and GitHub
matches labels by exact string, so any of those drifting fails silently as a
job that never starts.
"""

import plistlib
import re
import subprocess
from pathlib import Path

import pytest
import yaml

CI_RUNNER_DIR = Path(__file__).resolve().parent / "ci-runner"
INSTALLER_PATH = CI_RUNNER_DIR / "install-runner.sh"
PROTECT_PATH = CI_RUNNER_DIR / "protect-main.sh"
PLIST_PATH = CI_RUNNER_DIR / "com.origamisoftware.tarmar-ci-runner.plist"
README_PATH = CI_RUNNER_DIR / "README.md"
WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
)


def shell_constant(source: str, name: str) -> str:
    """The value of a double-quoted ``NAME="..."`` assignment in ``source``.

    ``${OTHER}`` references to earlier constants are expanded, so a path built
    from the runner's home compares against the daemon's literal one.
    """
    match = re.search(rf'^{name}="([^"]*)"', source, re.MULTILINE)
    assert match, f"{name} is not assigned in the installer"
    return re.sub(
        r"\$\{(\w+)\}",
        lambda reference: shell_constant(source, reference.group(1)),
        match.group(1),
    )


@pytest.fixture(scope="module")
def installer_source() -> str:
    return INSTALLER_PATH.read_text()


@pytest.fixture(scope="module")
def daemon() -> dict:
    return plistlib.loads(PLIST_PATH.read_bytes())


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


@pytest.fixture(scope="module")
def dry_run_output() -> str:
    """The installer's whole plan, printed rather than done, as an ordinary user.

    ``--fresh`` plans as if nothing were installed, so the same lines print on
    a machine where the runner already exists; without it every "already
    there" step is skipped and the plan depends on the host.
    """
    completed = subprocess.run(
        ["bash", str(INSTALLER_PATH), "--dry-run", "--fresh"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


@pytest.mark.parametrize("script_path", [INSTALLER_PATH, PROTECT_PATH])
def test_scripts_parse_and_are_executable(script_path: Path):
    subprocess.run(["bash", "-n", str(script_path)], check=True)
    assert script_path.stat().st_mode & 0o111


def test_installer_refuses_to_run_without_root():
    # Not root, no --dry-run: it must stop before touching anything, and say
    # how it wants to be run.
    completed = subprocess.run(
        ["bash", str(INSTALLER_PATH)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 1
    assert "sudo" in completed.stderr
    assert "would run" not in completed.stdout


def test_dry_run_names_the_pinned_release_and_its_checksum(
    installer_source: str, dry_run_output: str
):
    version = shell_constant(installer_source, "RUNNER_VERSION")
    checksum = shell_constant(installer_source, "RUNNER_SHA256")
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert re.fullmatch(r"[0-9a-f]{64}", checksum)
    assert f"actions-runner-osx-arm64-{version}.tar.gz" in dry_run_output
    assert checksum in dry_run_output


def test_dry_run_changes_nothing_but_shows_every_step(
    dry_run_output: str, installer_source: str
):
    repository = shell_constant(installer_source, "REPOSITORY")
    runner_name = shell_constant(installer_source, "RUNNER_NAME")
    runner_group = shell_constant(installer_source, "RUNNER_GROUP")
    for step in (
        f"dseditgroup -o create -r Tarmar CI {runner_group}",
        "sysadminctl -addUser",
        "cannot read",
        "https://github.com/actions/runner/releases/download/",
        f"--url 'https://github.com/{repository}'",
        f"--name '{runner_name}'",
        "config.sh --unattended",
        "cp -R",
        "launchctl bootstrap system",
        "enable the CI workflow",
    ):
        assert step in dry_run_output, step
    assert "kickstart" not in dry_run_output


def plan_with_pg_hba(tmp_path: Path, hba_text: str) -> str:
    """The dry-run plan with the installer pointed at a given pg_hba.conf."""
    hba = tmp_path / "pg_hba.conf"
    hba.write_text(hba_text)
    patched = tmp_path / "install-runner.sh"
    patched.write_text(
        INSTALLER_PATH.read_text().replace(
            'POSTGRES_HBA="/opt/homebrew/var/postgresql@16/pg_hba.conf"',
            f'POSTGRES_HBA="{hba}"',
        )
    )
    completed = subprocess.run(
        ["bash", str(patched), "--dry-run", "--fresh"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_installer_sees_a_trusting_postgres(tmp_path: Path):
    # The hole the separate account exists to close: a local Postgres that
    # trusts every local connection lets a job act as its superuser. The dry
    # run reports where the real run stops; the message names the fix.
    plan = plan_with_pg_hba(
        tmp_path, "local   all   all   trust\nhost all all 127.0.0.1/32 trust\n"
    )
    assert "would refuse" in plan
    assert "trusts local connections" in plan
    assert "scram-sha-256" in plan


def test_installer_accepts_a_postgres_that_asks_for_a_password(tmp_path: Path):
    plan = plan_with_pg_hba(
        tmp_path,
        "local   all   all   peer\nhost all all 127.0.0.1/32 scram-sha-256\n"
        "# host all all ::1/128 trust\n",
    )
    assert "would refuse" not in plan
    assert "does not trust local connections" in plan


def test_installer_labels_cover_what_the_workflow_asks_for(
    installer_source: str, workflow: dict
):
    labels = set(shell_constant(installer_source, "RUNNER_LABELS").split(","))
    for job_name, job in workflow["jobs"].items():
        runs_on = job["runs-on"]
        wanted = set(runs_on if isinstance(runs_on, list) else [runs_on])
        missing = wanted - labels
        assert not missing, f"job {job_name} wants labels the runner lacks: {missing}"


def test_daemon_label_matches_the_filename_and_the_installer(
    daemon: dict, installer_source: str
):
    assert daemon["Label"] == PLIST_PATH.stem
    assert daemon["Label"] == shell_constant(installer_source, "SERVICE_LABEL")


def test_daemon_runs_as_the_runner_user_in_the_runner_directory(
    daemon: dict, installer_source: str
):
    runner_user = shell_constant(installer_source, "RUNNER_USER")
    runner_home = shell_constant(installer_source, "RUNNER_HOME")
    runner_dir = shell_constant(installer_source, "RUNNER_DIR")
    assert daemon["UserName"] == runner_user
    # Its own group: staff can read into the developer's home.
    assert daemon["GroupName"] == shell_constant(installer_source, "RUNNER_GROUP")
    assert daemon["GroupName"] != "staff"
    assert daemon["WorkingDirectory"] == runner_dir
    # The wrapper at the runner root, where the installer copies it and where
    # the runner's self-updater looks for it.
    assert daemon["ProgramArguments"][-1] == f"{runner_dir}/runsvc.sh"
    assert (
        f"cp {runner_dir}/bin/runsvc.sh {runner_dir}/runsvc.sh"
        in installer_source.replace('"', "").replace("${RUNNER_DIR}", runner_dir)
    )
    assert daemon["EnvironmentVariables"]["HOME"] == runner_home
    for key in ("StandardOutPath", "StandardErrorPath"):
        assert daemon[key].startswith(runner_home + "/")


def test_daemon_path_is_the_installers_path(daemon: dict, installer_source: str):
    # The job finds uv and node through this; two different values would give
    # the listener one PATH and the registration another.
    assert daemon["EnvironmentVariables"]["PATH"] == shell_constant(
        installer_source, "RUNNER_PATH"
    )
    assert daemon["EnvironmentVariables"]["PATH"].startswith("/opt/homebrew/bin:")


def test_daemon_starts_at_boot_and_comes_back(daemon: dict):
    assert daemon["RunAtLoad"] is True
    assert daemon["KeepAlive"] is True


def test_daemon_is_not_throttled_as_a_background_process(daemon: dict):
    # launchd throttles an unclassified daemon's CPU and I/O; the suites need
    # every core and Playwright's timing.
    assert daemon["ProcessType"] == "Interactive"


def test_protection_binds_admins_and_github_actions():
    source = PROTECT_PATH.read_text()
    # Everyone who pushes is the owner, so an admin exemption exempts everyone.
    assert '"enforce_admins": true' in source
    assert '"app_id": ${GITHUB_ACTIONS_APP_ID}' in source
    assert shell_constant(source, "GITHUB_ACTIONS_APP_ID") == "15368"


def test_protection_requires_exactly_the_check_the_workflow_produces(
    workflow: dict,
):
    source = PROTECT_PATH.read_text()
    job = workflow["jobs"]["test"]
    # One job, no matrix (issue #921): GitHub names its status check after
    # the job's `name:` as written.
    assert "strategy" not in job
    assert shell_constant(source, "REQUIRED_CHECK") == job["name"]
    assert source.count('"context": "${REQUIRED_CHECK}"') == 1
    assert source.count('"context":') == 1


def test_ci_runs_the_default_canon_with_the_variable_unset(workflow: dict):
    # The public configuration: no TARMAR_CANON_DIR anywhere, and no canon in
    # the checkout, so reference.paths falls back to reference/default_canon.
    job = workflow["jobs"]["test"]
    assert job["name"] == "test (default canon)"
    assert "TARMAR_CANON_DIR" not in job.get("env", {})
    assert not any("TARMAR_CANON_DIR" in step.get("run", "") for step in job["steps"])


def test_the_guides_check_runs_unconditionally(workflow: dict):
    # build/ holds only the player-facing guides since #921, the same under
    # any canon.
    guides = next(
        step
        for step in workflow["jobs"]["test"]["steps"]
        if "build_guides.py" in step.get("run", "")
    )
    assert "if" not in guides


def test_readme_names_the_same_release(installer_source: str):
    version = shell_constant(installer_source, "RUNNER_VERSION")
    assert version in README_PATH.read_text()
