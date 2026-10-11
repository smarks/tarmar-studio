#!/bin/bash
# The runner container's entrypoint (issue #964): take one job and exit.
#
# tarmar-ci-runner.service asks GitHub for a just-in-time registration before
# each start (jit-config.sh) and hands it in as ACTIONS_RUNNER_INPUT_JITCONFIG,
# which the runner reads and removes from its environment (CommandSettings.cs),
# so it never sits on a command line other accounts on the host can see. The
# registration is good for one job; GitHub removes the runner when the job
# ends, the container exits, --rm deletes it, and systemd starts a fresh one.
# So no credential and no file outlives the job that could have touched it.

set -euo pipefail

if [ -z "${ACTIONS_RUNNER_INPUT_JITCONFIG:-}" ]; then
    echo "not registered: no ACTIONS_RUNNER_INPUT_JITCONFIG; the service supplies one (jit-config.sh)" >&2
    exit 1
fi

cd /home/runner/actions-runner
exec ./run.sh
