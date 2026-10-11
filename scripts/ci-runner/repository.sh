# shellcheck shell=bash
# The repository the runner serves and main's protection guards, named once
# for install-runner.sh and protect-main.sh, which source this file.
#
# It must stay private. On 2026-10-09 the code repository was renamed to
# tarmar-studio-private and its old name went to a public snapshot; scripts
# that still named it would have protected the wrong repository and could have
# registered this Mac's runner on a public one, where any fork's pull request
# runs here (issue #961). So both scripts ask GitHub before acting.
#
# Bash 3.2 (macOS ships no newer one).

REPOSITORY="smarks/tarmar-studio-private"

# Stop unless GitHub reports REPOSITORY as private. The arguments are the gh
# command to ask with, so the installer can run it as the account that holds
# the gh login.
require_private_repository() {
    local visibility
    visibility="$("$@" repo view "$REPOSITORY" --json visibility --jq .visibility)" \
        || { echo "could not read ${REPOSITORY} from GitHub; is gh logged in?" >&2; exit 1; }
    if [ "$visibility" != "PRIVATE" ]; then
        echo "${REPOSITORY} is ${visibility:-unknown}, not PRIVATE; refusing: a self-hosted runner or its protection rule belongs on the private code repository only" >&2
        exit 1
    fi
}
