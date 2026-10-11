#!/bin/bash
# Install the one-job container runner on datahorde (issue #964). Run as root,
# from a copy of this directory that also holds repository.sh;
# register-from-mac.sh makes that copy and runs this:
#
#     sudo ./install.sh [--token-file <file holding the fine-grained token>]
#
# The token (this repository only, Administration: read and write) is needed
# the first time and when it is replaced; it is moved to a root-only file and
# the given file deleted.
#
# Idempotent. Rerun it after a Playwright, gh or runner upgrade to rebuild the
# image; that restarts the service, which stops a job in progress.
#
# Before it starts the runner it proves the walls from inside a container on
# the CI network, and refuses if any of them is open.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# REPOSITORY (issue #961). The private check needs gh, which runs on the Mac
# before this (register-from-mac.sh).
# shellcheck source=../ci-runner/repository.sh
source "${SCRIPT_DIR}/repository.sh"

IMAGE="tarmar-ci-runner:latest"
NETWORK="tarmar-ci"
# Must match firewall.sh.
CI_BRIDGE="br-tarmar-ci"
CI_SUBNET="172.30.99.0/24"
CI_GATEWAY="172.30.99.1"
SERVICE="tarmar-ci-runner"
LIB_DIR="/usr/local/lib/tarmar-ci-runner"
ETC_DIR="/etc/tarmar-ci-runner"
TOKEN_PATH="${ETC_DIR}/github-token"
UNIT_PATH="/etc/systemd/system/${SERVICE}.service"
# Docker keeps everything, images, containers and their logs, in a
# fixed-size file of its own, so a job cannot fill the disk production's
# database lives on (64 GB free on / when this was written).
DOCKER_STORE_FILE="/var/lib/tarmar-ci-docker.img"
DOCKER_STORE_SIZE="30G"
DOCKER_DATA_ROOT="/var/lib/tarmar-ci-docker"
# The host's own LAN address and the Mac Studio's Ollama: what a job must not
# reach. Production Postgres listens on the LAN address too.
HOST_ADDRESS="192.168.4.163"
LAN_PROBE="192.168.4.158:11434"

log() { printf '[ci-runner-linux] %s\n' "$*"; }
fail() { printf '[ci-runner-linux] ERROR: %s\n' "$*" >&2; exit 1; }

TOKEN_FILE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --token-file) TOKEN_FILE="${2:?--token-file needs a path}"; shift 2 ;;
        *) fail "unknown argument: $1" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || fail "run with sudo: it installs Docker, a systemd unit and firewall rules"
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] || fail "written for Ubuntu; this is ${PRETTY_NAME:-unknown}"

# ------------------------------------------------------ 1. Docker's storage
if [ ! -f "$DOCKER_STORE_FILE" ]; then
    log "creating ${DOCKER_STORE_SIZE} store for Docker at ${DOCKER_STORE_FILE}"
    truncate --size "$DOCKER_STORE_SIZE" "$DOCKER_STORE_FILE"
    chmod 600 "$DOCKER_STORE_FILE"
    mkfs.ext4 -q -F "$DOCKER_STORE_FILE"
fi
install -d -o root -g root -m 711 "$DOCKER_DATA_ROOT"
if ! grep -q "^${DOCKER_STORE_FILE} " /etc/fstab; then
    echo "${DOCKER_STORE_FILE} ${DOCKER_DATA_ROOT} ext4 loop,nofail 0 2" >> /etc/fstab
fi
mountpoint -q "$DOCKER_DATA_ROOT" || mount "$DOCKER_DATA_ROOT"
mountpoint -q "$DOCKER_DATA_ROOT" || fail "${DOCKER_DATA_ROOT} did not mount"

# Docker reads this when it starts, so it is in place before Docker is
# installed: the data root on the capped store, the classic storage driver so
# nothing lands in /var/lib/containerd instead, capped logs, no IPv6.
install -d -o root -g root -m 755 /etc/docker
cat > /etc/docker/daemon.json <<JSON
{
  "data-root": "${DOCKER_DATA_ROOT}",
  "features": {"containerd-snapshotter": false},
  "log-driver": "json-file",
  "log-opts": {"max-size": "10m", "max-file": "3"},
  "ipv6": false
}
JSON
# Docker must not start before the store is mounted, or it would write to /.
install -d -o root -g root -m 755 /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/tarmar-ci-store.conf <<UNIT
[Unit]
RequiresMountsFor=${DOCKER_DATA_ROOT}
UNIT
systemctl daemon-reload

# ------------------------------------------------------------- 2. Docker
if command -v docker >/dev/null 2>&1; then
    log "docker present"
    systemctl restart docker
else
    log "installing docker.io from Ubuntu's archive"
    DEBIAN_FRONTEND=noninteractive apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold docker.io
fi
systemctl enable --now docker
[ "$(docker info --format '{{.DockerRootDir}}')" = "$DOCKER_DATA_ROOT" ] \
    || fail "Docker's data root is not ${DOCKER_DATA_ROOT}"
# overlay2, not the containerd image store, which would keep images and
# container layers in /var/lib/containerd on / and around the cap.
[ "$(docker info --format '{{.Driver}}')" = "overlay2" ] \
    || fail "Docker's storage driver is $(docker info --format '{{.Driver}}'), not overlay2"

# ------------------------------------------------------------ 3. the image
log "building ${IMAGE}"
docker build --pull --tag "$IMAGE" "$SCRIPT_DIR"
docker image prune --force >/dev/null

# ---------------------------------------------------------- 4. the network
if docker network inspect "$NETWORK" >/dev/null 2>&1; then
    actual="$(docker network inspect "$NETWORK" --format \
        '{{index .Options "com.docker.network.bridge.name"}} {{(index .IPAM.Config 0).Subnet}} {{(index .IPAM.Config 0).Gateway}}')"
    [ "$actual" = "${CI_BRIDGE} ${CI_SUBNET} ${CI_GATEWAY}" ] \
        || fail "network ${NETWORK} exists as '${actual}', not '${CI_BRIDGE} ${CI_SUBNET} ${CI_GATEWAY}'; remove it and rerun"
    log "network ${NETWORK} exists as expected"
else
    log "creating network ${NETWORK} (${CI_SUBNET} on ${CI_BRIDGE})"
    docker network create --driver bridge --subnet "$CI_SUBNET" --gateway "$CI_GATEWAY" \
        --opt "com.docker.network.bridge.name=${CI_BRIDGE}" "$NETWORK"
fi

# ------------------------------------- 5. root-owned copies and the token
# The unit runs these as root, so they must not be files an ordinary account
# can edit.
install -d -o root -g root -m 755 "$LIB_DIR"
install -o root -g root -m 755 "${SCRIPT_DIR}/firewall.sh" "${LIB_DIR}/firewall.sh"
install -o root -g root -m 755 "${SCRIPT_DIR}/jit-config.sh" "${LIB_DIR}/jit-config.sh"
install -o root -g root -m 644 "${SCRIPT_DIR}/repository.sh" "${LIB_DIR}/repository.sh"
install -o root -g root -m 644 "${SCRIPT_DIR}/${SERVICE}.service" "$UNIT_PATH"
for unit in tarmar-ci-firewall.service tarmar-ci-firewall.timer; do
    install -o root -g root -m 644 "${SCRIPT_DIR}/${unit}" "/etc/systemd/system/${unit}"
done
install -d -o root -g root -m 700 "$ETC_DIR"
if [ -n "$TOKEN_FILE" ]; then
    [ -s "$TOKEN_FILE" ] || fail "${TOKEN_FILE} is empty"
    install -o root -g root -m 600 "$TOKEN_FILE" "$TOKEN_PATH"
    rm -f "$TOKEN_FILE"
    log "token stored at ${TOKEN_PATH} (root only)"
fi
[ -s "$TOKEN_PATH" ] || fail "no token at ${TOKEN_PATH}: pass --token-file (register-from-mac.sh does)"
systemctl daemon-reload
"${LIB_DIR}/firewall.sh"

# ------------------------------------------------- 6. what a job can reach
# Tried as a job would, from a container on the CI network. A dropped packet
# shows as a timeout (exit 124), which is what each of these must give: a
# refusal would mean the packet arrived.
in_ci_container() {
    docker run --rm --network "$NETWORK" --sysctl net.ipv6.conf.all.disable_ipv6=1 \
        --entrypoint bash "$IMAGE" -c "$1"
}
must_time_out() {
    local target="$1" outcome=0
    in_ci_container "timeout 3 bash -c '</dev/tcp/${target%:*}/${target#*:}'" >/dev/null 2>&1 || outcome=$?
    [ "$outcome" -eq 124 ] \
        || fail "from the CI network, ${target} gave exit ${outcome}, not a timeout: the wall is open or the probe broke. Not starting the runner."
    log "a job cannot reach ${target}"
}
must_time_out "${HOST_ADDRESS}:22"
must_time_out "${HOST_ADDRESS}:5432"
must_time_out "${CI_GATEWAY}:22"
# The Mac Studio only proves the LAN is walled off if the host can reach it.
if timeout 3 bash -c "</dev/tcp/${LAN_PROBE%:*}/${LAN_PROBE#*:}" 2>/dev/null; then
    must_time_out "$LAN_PROBE"
else
    log "skipped the LAN check: the host cannot reach ${LAN_PROBE} either (asleep?)"
fi
ipv6_addresses="$(in_ci_container 'cat /proc/net/if_inet6 2>/dev/null; true')" \
    || fail "could not start a container on ${NETWORK} to check IPv6"
[ -z "$ipv6_addresses" ] || fail "a container on ${NETWORK} has IPv6 addresses"
log "a job has no IPv6"
in_ci_container "curl -fsS --max-time 10 -o /dev/null https://api.github.com" >/dev/null 2>&1 \
    || fail "a container on ${NETWORK} cannot reach api.github.com; the runner could not work"
log "a job can reach GitHub"

# ---------------------------------------------------------- 7. the service
# The token works: a mistyped or under-scoped one fails here, plainly, rather
# than as a service that never takes a job.
"${LIB_DIR}/jit-config.sh" || fail "could not mint a registration with the stored token; check its repository and Administration permission"
rm -f /run/tarmar-ci-runner/jit.env
log "the token can register runners"

systemctl enable --now tarmar-ci-firewall.timer
systemctl enable "$SERVICE"
# A unit that hit its start limit refuses even a manual restart until reset;
# a rerun of this script after such a failure is how it gets fixed.
systemctl reset-failed "$SERVICE" 2>/dev/null || true
systemctl restart "$SERVICE"
log "done. ${SERVICE} is waiting for a job; switch CI to it with"
log "  gh variable set CI_RUNNER --body datahorde --repo ${REPOSITORY}"
