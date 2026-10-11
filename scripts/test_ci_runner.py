"""The CI runner's install files agree with each other and with ci.yml (#576).

Since #964 ci.yml no longer selects the Mac Studio runner: the repository
variable CI_RUNNER picks GitHub's machines or datahorde's. These tests hold
that choice, and the Mac files that remain until datahorde's runner replaces
them.

The runner lives under its own macOS account on a machine this suite can only
partly see, and the installer needs root, so nothing here registers anything.
What it can do is hold the four files together: the installer, the daemon it
installs, the workflow that selects the runner, and the branch-protection
script that names the workflow's job. launchd expands nothing and GitHub
matches labels by exact string, so any of those drifting fails silently as a
job that never starts.
"""

import json
import plistlib
import re
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

CI_RUNNER_DIR = Path(__file__).resolve().parent / "ci-runner"
INSTALLER_PATH = CI_RUNNER_DIR / "install-runner.sh"
PROTECT_PATH = CI_RUNNER_DIR / "protect-main.sh"
REPOSITORY_PATH = CI_RUNNER_DIR / "repository.sh"
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


def test_the_sourced_repository_file_parses():
    # Sourced by both scripts, never run, so it need not be executable.
    subprocess.run(["bash", "-n", str(REPOSITORY_PATH)], check=True)


def test_installer_refuses_outright_because_the_mac_runner_path_is_retired():
    # No --dry-run: it must stop before touching anything, whatever the
    # repository's settings, and say where the self-hosted runner went.
    completed = subprocess.run(
        ["bash", str(INSTALLER_PATH)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 1
    assert "retired on 2026-10-10" in completed.stderr
    assert "datahorde" in completed.stderr
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
    repository = shell_constant(REPOSITORY_PATH.read_text(), "REPOSITORY")
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
    # The installer sources repository.sh from its own directory.
    (tmp_path / "repository.sh").write_text(REPOSITORY_PATH.read_text())
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


def plan_with_home_mode(tmp_path: Path, mode: int) -> str:
    """The dry-run plan with the developer's home a directory of ``mode``."""
    home = tmp_path / "developer"
    home.mkdir()
    home.chmod(mode)
    (tmp_path / "repository.sh").write_text(REPOSITORY_PATH.read_text())
    patched = tmp_path / "install-runner.sh"
    source = INSTALLER_PATH.read_text()
    assert 'INVOKING_HOME="/Users/${INVOKING_USER}"' in source
    patched.write_text(
        source.replace(
            'INVOKING_HOME="/Users/${INVOKING_USER}"', f'INVOKING_HOME="{home}"'
        )
    )
    completed = subprocess.run(
        ["bash", str(patched), "--dry-run", "--fresh"],
        capture_output=True,
        text=True,
        check=False,
    )
    home.chmod(0o700)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


@pytest.mark.skipif(
    subprocess.run(["stat", "-f", "%Lp", "/"], capture_output=True).returncode != 0,
    reason="BSD stat (macOS), as the installer runs on",
)
@pytest.mark.parametrize("mode", [0o750, 0o711, 0o705])
def test_a_home_staff_or_anyone_can_enter_is_refused_before_the_account(
    tmp_path: Path, mode: int
):
    # macOS puts every local account in staff, the runner's included, so a
    # primary group of its own keeps nothing out (issue #946): the home's
    # mode must, and the plan says so before it creates the account.
    plan = plan_with_home_mode(tmp_path, mode)
    assert "would refuse" in plan
    assert "chmod 700" in plan
    assert "~/Public" in plan
    assert plan.index("chmod 700") < plan.index("sysadminctl -addUser")


@pytest.mark.skipif(
    subprocess.run(["stat", "-f", "%Lp", "/"], capture_output=True).returncode != 0,
    reason="BSD stat (macOS), as the installer runs on",
)
def test_a_home_only_its_owner_can_enter_passes_the_preflight(tmp_path: Path):
    plan = plan_with_home_mode(tmp_path, 0o700)
    assert "is drwx------" in plan
    assert "would refuse" not in plan


def test_the_home_check_comes_before_the_account_is_created(installer_source: str):
    # A real run fails on it before step 1 creates anything (issue #946).
    assert installer_source.index('fail "$HOME_MODE_MESSAGE"') < installer_source.index(
        "# ---------------------------------------------------------------- 1. the user"
    )


def test_every_postgres_attempt_refuses_to_prompt_for_a_password(
    installer_source: str,
):
    # Without -w psql asks on /dev/tty, which no redirection stops, and a
    # developer who answers turns the check into a connection with a
    # password (issue #947).
    # Every place psql runs: the binary followed by its options, across a
    # line continuation if there is one.
    invocations = re.findall(r'"\$psql_binary"(?:\s|\\)+(-[^\n]*)', installer_source)
    assert invocations, "the installer no longer runs psql"
    for options in invocations:
        assert options.startswith("-w "), options
    # Both attempts, TCP and the socket, go through that one invocation.
    assert installer_source.count('postgres_attempt_connects "$psql_binary"') == 2
    # Its stderr is printed, not thrown away.
    assert "2>&1 >/dev/null" in installer_source
    assert (
        "/dev/null 2>&1; then"
        not in installer_source.split("postgres_attempt_connects()")[1].split("\n}\n")[
            0
        ]
    )


def test_nothing_runs_as_the_runner_from_a_directory_it_cannot_enter(
    installer_source: str,
):
    # sudo -u keeps the working directory (issue #947).
    first_sudo = installer_source.index('sudo -u "$')
    assert installer_source.index("\ncd /\n") < first_sudo


# What ci.yml's runs-on resolves to for each value of CI_RUNNER (issue #964).
GITHUB_HOSTED = "ubuntu-24.04"
DATAHORDE_LABELS = ["self-hosted", "Linux", "tarmar-datahorde"]
RUNS_ON = (
    f"${{{{ vars.CI_RUNNER == 'datahorde' && "
    f"fromJSON('{json.dumps(DATAHORDE_LABELS)}') || '{GITHUB_HOSTED}' }}}}"
)


def test_every_job_runs_where_ci_runner_says(workflow: dict):
    # Unset: GitHub's machines. "datahorde": the container runner there. No
    # value selects the Mac Studio runner, whose labels are not datahorde's.
    for job_name, job in workflow["jobs"].items():
        assert job["runs-on"] == RUNS_ON, job_name


def test_the_datahorde_labels_are_not_the_mac_runners(installer_source: str):
    mac_labels = set(shell_constant(installer_source, "RUNNER_LABELS").split(","))
    assert "tarmar-datahorde" not in mac_labels
    assert set(DATAHORDE_LABELS) - mac_labels == {"Linux", "tarmar-datahorde"}


def test_the_job_sets_up_its_own_tools_in_either_mode(workflow: dict):
    # Neither machine is trusted to hold uv, Node or the browser already.
    steps = workflow["jobs"]["test"]["steps"]
    uses = [step.get("uses", "") for step in steps]
    assert any(action.startswith("astral-sh/setup-uv@") for action in uses)
    assert any(action.startswith("actions/setup-node@") for action in uses)
    setup_uv = next(
        step for step in steps if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    # Not the newest CPython uv can find: 3.15.0 had no Pillow wheel.
    assert setup_uv["with"]["python-version"] == "3.14"
    install = next(
        step for step in steps if step.get("name") == "Install Playwright browser"
    )
    # apt (--with-deps) only where the job has root: GitHub's own machines.
    assert "runner.environment == 'github-hosted' && '--with-deps'" in install["run"]


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
    # Its own primary group, so its files are not group staff. That does not
    # keep the account out of staff (macOS adds every local account), so the
    # developer's home is shut by its own mode (issue #946).
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


VARIABLES_QUESTION = (
    "api --paginate repos/smarks/tarmar-studio/actions/variables?per_page=100"
    ' --jq .variables[] | select(.name == "CI_RUNNER") | .value'
)
APPROVAL_QUESTION = (
    "api repos/smarks/tarmar-studio/actions/permissions/fork-pr-contributor-approval"
    " --jq .approval_policy"
)


def opt_in_check_with_stand_in_gh(tmp_path: Path, stand_in_body: str):
    """Run require_self_hosted_runner_opt_in as register-from-mac.sh does, under
    its shell options and through a shell function, against a stand-in gh, and
    return the finished process."""
    stand_in = tmp_path / "gh"
    stand_in.write_text(f"#!/bin/bash\n{stand_in_body}\n")
    stand_in.chmod(0o755)
    return subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail; "
            f'source "{REPOSITORY_PATH}"; '
            f'wrapped_gh() {{ "{stand_in}" "$@"; }}; '
            "require_self_hosted_runner_opt_in wrapped_gh; "
            'echo "went on"',
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def answers_settings(ci_runner: str, approval_policy: str) -> str:
    """A stand-in gh body that answers only the two questions the scripts must
    ask: the CI_RUNNER variable's value, and the fork approval policy."""
    return (
        f'case "$*" in\n'
        f"  {shlex.quote(VARIABLES_QUESTION)}) echo {shlex.quote(ci_runner)} ;;\n"
        f"  {shlex.quote(APPROVAL_QUESTION)}) echo {shlex.quote(approval_policy)} ;;\n"
        f"  *) exit 1 ;;\n"
        f"esac"
    )


def test_an_opted_in_repository_with_approval_required_lets_the_scripts_go_on(
    tmp_path: Path,
):
    completed = opt_in_check_with_stand_in_gh(
        tmp_path, answers_settings("datahorde", "all_external_contributors")
    )
    assert completed.returncode == 0, completed.stderr
    assert "went on" in completed.stdout


@pytest.mark.parametrize("ci_runner", ["", "github", "Datahorde"])
def test_a_repository_that_has_not_opted_in_stops_the_scripts(
    tmp_path: Path, ci_runner: str
):
    # Issue #961: a runner on a public repository runs any fork's pull request
    # on the host. CI_RUNNER unset (the default) means GitHub's machines.
    completed = opt_in_check_with_stand_in_gh(
        tmp_path, answers_settings(ci_runner, "all_external_contributors")
    )
    assert completed.returncode != 0
    assert "went on" not in completed.stdout
    assert "refusing" in completed.stderr
    assert "CI_RUNNER" in completed.stderr


@pytest.mark.parametrize(
    "approval_policy",
    ["first_time_contributors", "first_time_contributors_new_to_github", ""],
)
def test_a_repository_that_runs_fork_workflows_unapproved_stops_the_scripts(
    tmp_path: Path, approval_policy: str
):
    completed = opt_in_check_with_stand_in_gh(
        tmp_path, answers_settings("datahorde", approval_policy)
    )
    assert completed.returncode != 0
    assert "went on" not in completed.stdout
    assert "refusing" in completed.stderr
    assert "outside collaborators" in completed.stderr


def test_a_gh_that_cannot_answer_stops_the_scripts(tmp_path: Path):
    completed = opt_in_check_with_stand_in_gh(tmp_path, "exit 1")
    assert completed.returncode != 0
    assert "went on" not in completed.stdout
    assert "could not read" in completed.stderr


def test_a_gh_that_cannot_read_the_approval_policy_stops_the_scripts(tmp_path: Path):
    completed = opt_in_check_with_stand_in_gh(
        tmp_path,
        f'[ "$*" = {shlex.quote(VARIABLES_QUESTION)} ] || exit 1\necho datahorde',
    )
    assert completed.returncode != 0
    assert "went on" not in completed.stdout
    assert "could not read the fork pull request approval policy" in completed.stderr


def test_the_opt_in_value_is_the_one_ci_yml_switches_on():
    workflow = WORKFLOW_PATH.read_text()
    value = shell_constant(REPOSITORY_PATH.read_text(), "SELF_HOSTED_CI_RUNNER")
    assert f"vars.CI_RUNNER == '{value}'" in workflow


def first_line_matching(source: str, pattern: str) -> int:
    """Where the first line matching ``pattern`` starts in ``source``."""
    match = re.search(pattern, source, re.MULTILINE)
    assert match, pattern
    return match.start()


def test_the_repository_is_named_once_and_checked_before_either_script_acts(
    installer_source: str,
):
    protect_source = PROTECT_PATH.read_text()
    assert shell_constant(REPOSITORY_PATH.read_text(), "REPOSITORY") == (
        "smarks/tarmar-studio"
    )
    for source, first_action in (
        (installer_source, r"^\s*act dseditgroup -o create"),
        (protect_source, r"^gh api -X PUT"),
    ):
        assert not re.search(r"^REPOSITORY=", source, re.MULTILINE)
        assert re.search(r'^source ".*repository\.sh"', source, re.MULTILINE)
    sourced = first_line_matching(installer_source, r'^source ".*repository\.sh"')
    refusal = first_line_matching(installer_source, r'^\s*fail "\$RETIRED_MESSAGE"')
    assert (
        sourced
        < refusal
        < first_line_matching(installer_source, r"^\s*act dseditgroup -o create")
    )
    # The opt-in rule guards the datahorde registration; neither Mac script
    # runs it. Branch protection suits a public repository.
    assert "require_" not in installer_source
    assert "require_" not in protect_source


def test_the_dry_run_says_the_path_is_retired_before_it_plans_any_change(
    dry_run_output: str,
):
    assert dry_run_output.index("retired") < dry_run_output.index(
        "dseditgroup -o create"
    )


def test_readme_names_the_same_repository():
    repository = shell_constant(REPOSITORY_PATH.read_text(), "REPOSITORY")
    named = set(re.findall(r"repos/([\w.-]+/[\w.-]+)/", README_PATH.read_text()))
    assert named == {repository}
