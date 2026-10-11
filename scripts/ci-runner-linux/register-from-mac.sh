#!/bin/bash
# Install or update the one-job container runner on datahorde, from a Mac
# logged in to gh (issue #964). Run from a checkout of the repository:
#
#     scripts/ci-runner-linux/register-from-mac.sh
#
# It checks the repository is private, then asks for the fine-grained token
# (this repository only, Administration: read and write). Press Return without
# one to keep the token datahorde already holds. It copies this directory and
# repository.sh to a fresh directory on datahorde and runs install.sh there
# with sudo. The token is typed at a hidden prompt, piped over ssh into a file
# only its owner can read, and moved by install.sh to a root-only file; it is
# never on a command line.
#
# The remote side runs under bash explicitly: sam's login shell on datahorde
# is zsh, where `status` is read-only.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../ci-runner/repository.sh
source "${SCRIPT_DIR}/../ci-runner/repository.sh"

DEPLOY_HOST="sam@192.168.4.163"

require_private_repository gh

read -rsp "Fine-grained token for ${REPOSITORY} (Return to keep the current one): " token
echo

remote_dir="$(ssh "$DEPLOY_HOST" "bash -c 'mktemp -d /tmp/tarmar-ci-runner-install.XXXXXX'")"
scp -q "$SCRIPT_DIR"/* "${SCRIPT_DIR}/../ci-runner/repository.sh" "${DEPLOY_HOST}:${remote_dir}/"
token_argument=""
if [ -n "$token" ]; then
    printf '%s\n' "$token" | ssh "$DEPLOY_HOST" "bash -c 'umask 077 && cat > \"${remote_dir}/token\"'"
    token_argument="--token-file ${remote_dir}/token"
fi
unset token
ssh -t "$DEPLOY_HOST" "bash -c 'sudo \"${remote_dir}/install.sh\" ${token_argument}; outcome=\$?; rm -rf \"${remote_dir}\"; exit \$outcome'"
