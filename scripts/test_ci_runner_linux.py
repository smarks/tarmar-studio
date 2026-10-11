"""The datahorde container runner's files agree with each other and ci.yml (#964).

Datahorde is the production host, so most of what is held here is what the
runner's container is denied: the Docker socket, host paths, the host's
network, IPv6, capabilities, and anything that outlives one job. Nothing here
builds the image or runs a container; no machine this suite runs on has
Docker. The Runner image workflow builds the image on GitHub, and install.sh
proves the walls on datahorde before it starts the runner.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

SCRIPTS_DIR = Path(__file__).resolve().parent
RUNNER_DIR = SCRIPTS_DIR / "ci-runner-linux"
DOCKERFILE_PATH = RUNNER_DIR / "Dockerfile"
ENTRYPOINT_PATH = RUNNER_DIR / "entrypoint.sh"
FIREWALL_PATH = RUNNER_DIR / "firewall.sh"
INSTALL_PATH = RUNNER_DIR / "install.sh"
JIT_CONFIG_PATH = RUNNER_DIR / "jit-config.sh"
REGISTER_PATH = RUNNER_DIR / "register-from-mac.sh"
SERVICE_PATH = RUNNER_DIR / "tarmar-ci-runner.service"
MAC_INSTALLER_PATH = SCRIPTS_DIR / "ci-runner" / "install-runner.sh"
REPOSITORY_PATH = SCRIPTS_DIR / "ci-runner" / "repository.sh"
REPOSITORY_ROOT = SCRIPTS_DIR.parent
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
PACKAGE_LOCK_PATH = REPOSITORY_ROOT / "package-lock.json"

# Every word the unit's `docker run` may carry, in order. Anything added, a
# mount, --network host, --cap-add, --privileged, a second --security-opt,
# --user, --device, changes this list and fails the test that holds it.
DOCKER_RUN = [
    "/usr/bin/docker", "run", "--rm", "--name", "tarmar-ci-runner",
    "--network", "tarmar-ci",
    "--sysctl", "net.ipv6.conf.all.disable_ipv6=1",
    "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges",
    "--pids-limit", "4096",
    "--cpus", "4",
    "--memory", "16g",
    "--memory-swap", "16g",
    "--blkio-weight", "100",
    "--env-file", "/run/tarmar-ci-runner/jit.env",
    "tarmar-ci-runner:latest",
]  # fmt: skip


def shell_constant(source: str, name: str) -> str:
    """The value of a double-quoted ``NAME="..."`` assignment in ``source``."""
    match = re.search(rf'^{name}="([^"]*)"', source, re.MULTILINE)
    assert match, f"{name} is not assigned"
    return match.group(1)


def dockerfile_argument(name: str) -> str:
    """The default of ``ARG NAME=...`` in the Dockerfile."""
    match = re.search(rf"^ARG {name}=(\S+)$", DOCKERFILE_PATH.read_text(), re.MULTILINE)
    assert match, f"ARG {name} has no default"
    return match.group(1)


def unit_lines(key: str) -> list[str]:
    """Every value of ``key=`` in the unit, continuation lines joined."""
    service = SERVICE_PATH.read_text().replace("\\\n", " ")
    return re.findall(rf"^{key}=(.*)$", service, re.MULTILINE)


@pytest.fixture(scope="module")
def workflow_labels() -> set[str]:
    """The labels ci.yml asks for when CI_RUNNER is datahorde."""
    runs_on = yaml.safe_load(WORKFLOW_PATH.read_text())["jobs"]["test"]["runs-on"]
    match = re.search(r"fromJSON\('(\[.*?\])'\)", runs_on)
    assert match, runs_on
    return set(json.loads(match.group(1)))


@pytest.mark.parametrize(
    "script_path",
    [ENTRYPOINT_PATH, FIREWALL_PATH, INSTALL_PATH, JIT_CONFIG_PATH, REGISTER_PATH],
)
def test_scripts_parse_and_are_executable(script_path: Path):
    subprocess.run(["bash", "-n", str(script_path)], check=True)
    assert script_path.stat().st_mode & 0o111


def test_the_container_runs_with_exactly_the_allowed_arguments():
    (exec_start,) = unit_lines("ExecStart")
    assert exec_start.split() == DOCKER_RUN


def test_each_container_is_registered_for_one_job_by_root_before_it_starts():
    assert unit_lines("ExecStartPre")[:2] == [
        "/usr/local/lib/tarmar-ci-runner/firewall.sh",
        "/usr/local/lib/tarmar-ci-runner/jit-config.sh",
    ]
    jit = JIT_CONFIG_PATH.read_text()
    assert "generate-jitconfig" in jit
    assert 'ENV_PATH="/run/tarmar-ci-runner/jit.env"' in jit
    assert "--env-file /run/tarmar-ci-runner/jit.env" in " ".join(DOCKER_RUN)
    entrypoint = ENTRYPOINT_PATH.read_text()
    # As ACTIONS_RUNNER_INPUT_JITCONFIG, which the runner reads and removes,
    # never on run.sh's command line, which every host account can see.
    assert "exec ./run.sh\n" in entrypoint
    assert "--jitconfig" not in entrypoint
    assert "ACTIONS_RUNNER_INPUT_JITCONFIG=" in jit
    # It never registers a long-lived runner itself.
    assert "./config.sh" not in entrypoint


def test_the_one_job_runner_answers_exactly_what_the_workflow_asks_for(
    workflow_labels: set[str],
):
    match = re.search(r'"labels": (\[[^\]]*\])', JIT_CONFIG_PATH.read_text())
    assert match
    assert workflow_labels <= set(json.loads(match.group(1)))
    # And the Mac runner, still registered, cannot answer it.
    mac = set(
        shell_constant(MAC_INSTALLER_PATH.read_text(), "RUNNER_LABELS").split(",")
    )
    assert not workflow_labels <= mac


def test_the_image_runs_as_an_unused_non_root_uid():
    dockerfile = DOCKERFILE_PATH.read_text()
    users = re.findall(r"^USER (\S+)$", dockerfile, re.MULTILINE)
    assert users and users[-1] == "runner"
    # 1001 is `guest` on datahorde; an escaped process should be nobody there.
    assert int(dockerfile_argument("RUNNER_UID")) == 64001
    assert '--uid "${RUNNER_UID}"' in dockerfile


def test_the_network_the_installer_makes_is_the_one_the_firewall_walls_off():
    install = INSTALL_PATH.read_text()
    firewall = FIREWALL_PATH.read_text()
    assert shell_constant(install, "CI_BRIDGE") == shell_constant(firewall, "CI_BRIDGE")
    assert (
        shell_constant(install, "NETWORK")
        == DOCKER_RUN[DOCKER_RUN.index("--network") + 1]
    )
    # An existing network is checked, not trusted.
    assert '[ "$actual" = "${CI_BRIDGE} ${CI_SUBNET} ${CI_GATEWAY}" ]' in install


def test_the_firewall_is_its_own_nftables_table_covering_ipv6():
    firewall = FIREWALL_PATH.read_text()
    assert "table inet tarmar_ci {" in firewall
    assert 'iifname "${CI_BRIDGE}" drop' in firewall
    assert 'iifname "${CI_BRIDGE}" meta nfproto ipv6 drop' in firewall
    assert 'iifname "${CI_BRIDGE}" ip daddr { ${PRIVATE_NETWORKS} } drop' in firewall
    assert set(shell_constant(firewall, "PRIVATE_NETWORKS").split(", ")) == {
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
    }
    commands = [
        line for line in firewall.splitlines() if not line.lstrip().startswith("#")
    ]
    assert not any("iptables" in line for line in commands)


def test_the_unit_runs_root_owned_copies_not_the_checkout():
    # The unit runs these as root; a file an ordinary account can edit would
    # be a way to root.
    install = INSTALL_PATH.read_text()
    for script in ("firewall.sh", "jit-config.sh"):
        assert f'install -o root -g root -m 755 "${{SCRIPT_DIR}}/{script}"' in install
    assert 'install -o root -g root -m 644 "${SCRIPT_DIR}/repository.sh"' in install
    assert 'install -o root -g root -m 600 "$TOKEN_FILE" "$TOKEN_PATH"' in install


def test_the_installer_proves_the_walls_before_starting_the_runner():
    install = INSTALL_PATH.read_text()
    start = install.index('systemctl restart "$SERVICE"')
    for probe in (
        'must_time_out "${HOST_ADDRESS}:22"',
        'must_time_out "${HOST_ADDRESS}:5432"',
        'must_time_out "${CI_GATEWAY}:22"',
        'must_time_out "$LAN_PROBE"',
        "has IPv6 addresses",
    ):
        assert install.index(probe) < start, probe
    # A timeout, not any failure: a refusal means the packet arrived.
    assert '[ "$outcome" -eq 124 ]' in install
    assert shell_constant(install, "HOST_ADDRESS") == "192.168.4.163"


def test_docker_keeps_everything_on_its_own_capped_store():
    install = INSTALL_PATH.read_text()
    data_root = shell_constant(install, "DOCKER_DATA_ROOT")
    assert '"data-root": "${DOCKER_DATA_ROOT}"' in install
    assert '"containerd-snapshotter": false' in install
    assert "RequiresMountsFor=${DOCKER_DATA_ROOT}" in install
    assert shell_constant(install, "DOCKER_STORE_SIZE") == "30G"
    assert data_root != "/var/lib/docker"
    # The store is set up before Docker is installed or restarted.
    assert install.index("daemon.json") < install.index("--force-confold docker.io")


def test_the_image_pins_the_runner_the_mac_pins_and_gh():
    mac = MAC_INSTALLER_PATH.read_text()
    assert dockerfile_argument("RUNNER_VERSION") == shell_constant(
        mac, "RUNNER_VERSION"
    )
    for checksum in ("RUNNER_SHA256", "GH_SHA256"):
        assert re.fullmatch(r"[0-9a-f]{64}", dockerfile_argument(checksum))


def test_the_image_installs_the_libraries_for_the_locked_playwright():
    lock = json.loads(PACKAGE_LOCK_PATH.read_text())
    locked = lock["packages"]["node_modules/@playwright/test"]["version"]
    assert dockerfile_argument("PLAYWRIGHT_VERSION") == locked


def test_the_browser_lands_where_ci_caches_it():
    # ci.yml caches ~/.cache/ms-playwright, Playwright's default on Linux.
    dockerfile = DOCKERFILE_PATH.read_text()
    assert not re.search(r"^ENV\s+PLAYWRIGHT_BROWSERS_PATH", dockerfile, re.MULTILINE)
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
    cache = next(
        step
        for step in workflow["jobs"]["test"]["steps"]
        if step.get("name") == "Cache Playwright browser"
    )
    assert cache["with"]["path"] == "~/.cache/ms-playwright"


def test_registration_checks_the_runner_opt_in_before_asking_for_the_token():
    assert 'source "$REPOSITORY_PATH"' in JIT_CONFIG_PATH.read_text()
    register = REGISTER_PATH.read_text()
    assert register.index("require_self_hosted_runner_opt_in gh") < register.index(
        "read -rsp"
    )
    assert shell_constant(REPOSITORY_PATH.read_text(), "REPOSITORY") == (
        "smarks/tarmar-studio"
    )


@pytest.mark.parametrize("script_path", [REGISTER_PATH, INSTALL_PATH, JIT_CONFIG_PATH])
def test_the_token_never_reaches_a_command_line(script_path: Path):
    source = script_path.read_text()
    # No token variable expanded into a command's arguments.
    assert not re.search(r"(ssh|docker|curl|gh)[^\n]*\$\{?token\b", source)
    assert "--env RUNNER_TOKEN" not in source


def test_the_token_is_typed_hidden_and_moved_to_a_root_only_file():
    register = REGISTER_PATH.read_text()
    assert "read -rsp" in register
    assert "umask 077 && cat >" in register
    # The remote side runs under bash: sam's zsh has a read-only `status`.
    assert re.search(r'ssh -t "\$DEPLOY_HOST" "bash -c ', register)
    assert "status=" not in register
    jit = JIT_CONFIG_PATH.read_text()
    assert 'TOKEN_PATH="/etc/tarmar-ci-runner/github-token"' in jit
    assert "Authorization" in jit


def test_a_failing_registration_waits_instead_of_hammering_github():
    jit = JIT_CONFIG_PATH.read_text()
    assert jit.count("sleep 60") == 2
    (timeout,) = unit_lines("TimeoutStartSec")
    assert int(timeout) > 60
    # The installer tries the token before it starts the service.
    install = INSTALL_PATH.read_text()
    assert install.index('"${LIB_DIR}/jit-config.sh" || fail') < install.index(
        'systemctl restart "$SERVICE"'
    )


def test_no_job_starts_without_the_wall():
    jit = JIT_CONFIG_PATH.read_text()
    assert jit.index("nft list table inet tarmar_ci") < jit.index("generate-jitconfig")


def test_the_firewall_is_replaced_in_one_transaction():
    firewall = FIREWALL_PATH.read_text()
    assert "nft delete table" not in firewall
    assert (
        "table inet tarmar_ci\ndelete table inet tarmar_ci\ntable inet tarmar_ci {"
        in firewall
    )


def test_docker_uses_overlay2_on_the_store_and_apt_never_prompts():
    install = INSTALL_PATH.read_text()
    assert '= "overlay2" ]' in install
    assert "DEBIAN_FRONTEND=noninteractive apt-get install" in install
    assert "--force-confold" in install


def test_the_wall_is_reapplied_every_minute_and_a_tight_loop_stops():
    timer = (RUNNER_DIR / "tarmar-ci-firewall.timer").read_text()
    assert "OnUnitActiveSec=60" in timer
    oneshot = (RUNNER_DIR / "tarmar-ci-firewall.service").read_text()
    assert "ExecStart=/usr/local/lib/tarmar-ci-runner/firewall.sh" in oneshot
    install = INSTALL_PATH.read_text()
    assert "systemctl enable --now tarmar-ci-firewall.timer" in install
    assert install.index('systemctl reset-failed "$SERVICE"') < install.index(
        'systemctl restart "$SERVICE"'
    )
    (burst,) = unit_lines("StartLimitBurst")
    (interval,) = unit_lines("StartLimitIntervalSec")
    assert int(burst) >= 10 and int(interval) >= 60
