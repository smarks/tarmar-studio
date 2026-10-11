# shellcheck shell=bash
# The repository the runner serves and main's protection guards, named once
# for install-runner.sh, protect-main.sh and register-from-mac.sh, which
# source this file. Only register-from-mac.sh runs the opt-in check below.
#
# Development moved on 2026-10-10 from tarmar-studio-private (now an archive)
# to this public repository. On a public repository a self-hosted runner lets
# any fork's pull request run code on the host (issue #961), so CI runs on
# GitHub's machines unless the repository opts in: ci.yml uses the self-hosted
# runner only when the repository variable CI_RUNNER is the value below. The
# datahorde registration (register-from-mac.sh) asks GitHub before acting and
# stops unless that opt-in is set and the repository holds fork workflows for
# approval.
#
# Bash 3.2 (macOS ships no newer one).

REPOSITORY="smarks/tarmar-studio"

# The value of the CI_RUNNER repository variable that sends ci.yml's job to
# the self-hosted runner; scripts/test_ci_runner.py checks it against ci.yml.
SELF_HOSTED_CI_RUNNER="datahorde"

# The fork-pull-request approval policy that holds every outside
# collaborator's workflow runs until a maintainer approves them (the
# repository setting "Require approval for all outside collaborators").
REQUIRED_FORK_APPROVAL_POLICY="all_external_contributors"

# Stop unless this repository opts in to a self-hosted runner: CI_RUNNER is
# SELF_HOSTED_CI_RUNNER, and fork workflows wait for approval. The arguments
# are the gh command to ask with (register-from-mac.sh passes plain gh).
require_self_hosted_runner_opt_in() {
    local ci_runner approval_policy
    ci_runner="$("$@" api --paginate "repos/${REPOSITORY}/actions/variables?per_page=100" \
        --jq '.variables[] | select(.name == "CI_RUNNER") | .value')" \
        || { echo "could not read the variables of ${REPOSITORY} from GitHub; is gh logged in?" >&2; exit 1; }
    if [ "$ci_runner" != "$SELF_HOSTED_CI_RUNNER" ]; then
        echo "CI_RUNNER on ${REPOSITORY} is '${ci_runner}', not '${SELF_HOSTED_CI_RUNNER}'; refusing: a self-hosted runner is opt-in, set it with: gh variable set CI_RUNNER --body ${SELF_HOSTED_CI_RUNNER} --repo ${REPOSITORY}" >&2
        exit 1
    fi
    approval_policy="$("$@" api "repos/${REPOSITORY}/actions/permissions/fork-pr-contributor-approval" \
        --jq .approval_policy)" \
        || { echo "could not read the fork pull request approval policy of ${REPOSITORY} from GitHub; is gh logged in?" >&2; exit 1; }
    if [ "$approval_policy" != "$REQUIRED_FORK_APPROVAL_POLICY" ]; then
        echo "${REPOSITORY} approves fork workflows under '${approval_policy:-unknown}', not '${REQUIRED_FORK_APPROVAL_POLICY}'; refusing: without \"Require approval for all outside collaborators\" (Settings > Actions > General) a fork's pull request runs on the self-hosted runner unreviewed" >&2
        exit 1
    fi
}
