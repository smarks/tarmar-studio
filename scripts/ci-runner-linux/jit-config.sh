#!/bin/bash
# Ask GitHub for a one-job registration for the next runner container
# (issue #964). Run as root by tarmar-ci-runner.service before each start.
#
# Reads the fine-grained token (this repository only, Administration: read and
# write) from TOKEN_PATH, which only root can read, and writes the registration
# to ENV_PATH for `docker run --env-file`, also root-only. Before that it
# deletes this host's runners that GitHub shows offline: a container stopped
# before its job (a reboot, a restart) leaves one behind.
#
# The token never appears on a command line: python reads it from the file and
# sends it as a header.
#
# When GitHub refuses (an expired or revoked token) or cannot be reached, this
# waits a minute before failing, so the service's restarts ask about once a
# minute rather than every few seconds. TimeoutStartSec in the unit covers it.

set -euo pipefail

TOKEN_PATH="/etc/tarmar-ci-runner/github-token"
ENV_PATH="/run/tarmar-ci-runner/jit.env"
REPOSITORY_PATH="/usr/local/lib/tarmar-ci-runner/repository.sh"

# shellcheck source=../ci-runner/repository.sh
source "$REPOSITORY_PATH"

install -d -o root -g root -m 700 "$(dirname "$ENV_PATH")"
umask 077

# Never start a job without the wall: something on the host (`nft -f
# /etc/nftables.conf`, which begins with `flush ruleset`) can remove it.
if ! nft list table inet tarmar_ci >/dev/null 2>&1; then
    echo "the firewall table inet tarmar_ci is missing; not registering a runner" >&2
    sleep 60
    exit 1
fi

if ! REPOSITORY="$REPOSITORY" TOKEN_PATH="$TOKEN_PATH" ENV_PATH="$ENV_PATH" \
RUNNER_NAME="datahorde-$(date +%Y%m%d%H%M%S)-$$" \
python3 - <<'PYTHON'
"""Delete this host's offline runners, then mint one just-in-time registration."""

import json
import os
import urllib.request

API = f"https://api.github.com/repos/{os.environ['REPOSITORY']}/actions/runners"
with open(os.environ["TOKEN_PATH"]) as token_file:
    TOKEN = token_file.read().strip()
HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
}


def call(method: str, url: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read()
    return json.loads(payload) if payload else {}


for runner in call("GET", f"{API}?per_page=100")["runners"]:
    if runner["name"].startswith("datahorde-") and runner["status"] == "offline":
        call("DELETE", f"{API}/{runner['id']}")

registration = call(
    "POST",
    f"{API}/generate-jitconfig",
    {
        "name": os.environ["RUNNER_NAME"],
        "runner_group_id": 1,
        # ci.yml asks for self-hosted, Linux, tarmar-datahorde.
        "labels": ["self-hosted", "Linux", "X64", "tarmar-datahorde"],
        "work_folder": "_work",
    },
)
with open(os.environ["ENV_PATH"], "w") as env_file:
    env_file.write(f"ACTIONS_RUNNER_INPUT_JITCONFIG={registration['encoded_jit_config']}\n")
PYTHON
then
    echo "GitHub refused or could not be reached; waiting a minute before failing" >&2
    sleep 60
    exit 1
fi
